"""Entry / exit hooks: make skill-library lookup ("can be found") and consolidation ("can be saved") independent of the model's self-discipline.

In practice the self-evolution loop broke at both ends, and neither is a wording problem; both are structural:

- **Entry**: session initialization gives the model only the skill **names** (`skills: ['run-vasp', ...]`),
  with no description. The model cannot tell which skill covers the current task; worse, the rule
  "anything using mpirun / vasp_gpu / vasp_runner.py must first load run-vasp" lives in
  **run-vasp's own description** -- the rule that requires loading it is hidden inside it, a circular dependency.
  In a measured session that ran a complete phonon workflow end to end, the number of `Skill` calls was 0.
- **Exit**: the trigger for consolidation is "the task is completely finished", a fuzzy state the model
  must judge itself, and the wording is `consider whether`. In the same system prompt, the ANTI-SILENCE
  rule, worded `STRICTLY FORBIDDEN` and attached to an unconditional moment, was obeyed 17/17, while the consolidation rule was obeyed 0/1.

So the entry hook splices the description table into the system prompt and requires a coverage verdict to be written,
and the exit hook compresses the trajectory unconditionally, then uses that verdict to choose path B (improve) or path C (create new).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SKILL_COVERAGE_FILENAME = ".skill_coverage.json"
DIGEST_DIRNAME = ".distill_candidates"

# Truncation length for each description in the system prompt. The table has about 20 entries, kept on the order of a few KB.
_DESC_LIMIT = 240


# ---------------------------------------------------------------------------
# Entry: skill directory scan
# ---------------------------------------------------------------------------

def _parse_frontmatter(path: Path) -> dict[str, Any]:
    """Read the YAML frontmatter of SKILL.md. Return an empty dict if it cannot be parsed, so one bad file does not abort startup."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        import yaml

        data = yaml.safe_load(text[3:end])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_skill_catalog(repo_root: Path | str) -> list[tuple[str, str]]:
    """Scan `.claude/skills/*/SKILL.md` and return [(name, description), ...] sorted by name.

    The name is the frontmatter `name` (falling back to the directory name if missing) -- the two may
    differ, and the model must use the frontmatter name in the `Skill` tool.
    """
    skills_dir = Path(repo_root) / ".claude" / "skills"
    catalog: list[tuple[str, str]] = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        meta = _parse_frontmatter(skill_md)
        name = str(meta.get("name") or skill_md.parent.name).strip()
        desc = " ".join(str(meta.get("description") or "").split())
        if name:
            catalog.append((name, desc))
    return catalog


def format_skill_catalog(catalog: list[tuple[str, str]], limit: int = _DESC_LIMIT) -> str:
    """Render as a compact list for the system prompt."""
    if not catalog:
        return "(no project skills found)"
    lines = []
    for name, desc in catalog:
        if len(desc) > limit:
            desc = desc[:limit].rstrip() + "…"
        lines.append(f"- `{name}`: {desc}" if desc else f"- `{name}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Exit: coverage verdict
# ---------------------------------------------------------------------------

def read_coverage(workspace: Path | str) -> dict[str, Any] | None:
    """Read the coverage verdict written at entry. Return None if missing or corrupt, leaving the caller to fall back."""
    path = Path(workspace) / SKILL_COVERAGE_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def skills_loaded_from_log(workspace: Path | str) -> list[str]:
    """Count the skills actually loaded, from log.jsonl.

    This is the fallback for `read_coverage`: the entry instruction to "write .skill_coverage.json"
    also relies on the model executing it and may come to nothing, whereas a `Skill` tool call is a
    hard fact and is always in the log.
    """
    from src.event_log import resolve_log_path

    try:
        log_path = resolve_log_path(Path(workspace))
    except Exception:
        return []
    names: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if obj.get("__type__") == "ToolUseBlock" and obj.get("name") == "Skill":
                skill = (obj.get("input") or {}).get("skill")
                if skill and skill not in names:
                    names.append(str(skill))
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    try:
        with log_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("type") == "AssistantMessage":
                    walk(record.get("payload", {}))
    except OSError:
        return []
    return names


# ---------------------------------------------------------------------------
# Exit: on-site scan (objective facts, not what the agent happened to see)
# ---------------------------------------------------------------------------
#
# The digest used to be derived from log.jsonl alone, i.e. it held only "what the agent grepped at the
# time". After a calculation finishes, the disk still holds a lot of evidence the agent never looked at:
# the atom count in OUTCAR, the actual MPI layout, the effective NCORE, whether the GPU was initialized,
# and wall-clock time. Without these, silent inefficiency of the "task completed successfully but used
# the wrong resources" kind is completely invisible during distillation -- the failure list only
# collects is_error, and such cases raise no error at all.
#
# A measured example: in one session, LEPSILON on 8 atoms ran for 1282 s, while a force calculation on
# 64 atoms took only 425 s. A small system being 3x slower than a large one is a glaring anomaly, but
# neither number made it into the digest.

#: Fields to look for in the OUTCAR header. A fixed line window cannot be used -- for the same MgO,
#: NIONS of the force calculation is at line 544, while that of LEPSILON is at line 3960 (DFPT prints
#: a lot of k-point information first). So scan until all are found, with a generous cap as a backstop.
_HEADER_KEYS = ("NIONS", "mpi-ranks", "one band on NCORE", "GPUs detected")
_OUTCAR_SCAN_LINE_CAP = 30000
_OUTCAR_WARNING_LINES = 600  # The warning block is at the very front; no need to collect throughout
_OUTCAR_TAIL_BYTES = 8192    # Wall-clock time is at the tail


def _scan_outcar(path: Path) -> tuple[dict[str, str], list[str], list[str]]:
    """Return (first match of each header field, warning lines, tail lines). A whole OUTCAR can be hundreds of MB, so scan only the necessary parts."""
    found: dict[str, str] = {}
    warnings: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for i, raw_line in enumerate(handle):
                if i >= _OUTCAR_SCAN_LINE_CAP:
                    break
                line = raw_line.rstrip("\n")
                for key in _HEADER_KEYS:
                    if key not in found and key in line:
                        found[key] = line.strip()
                if i < _OUTCAR_WARNING_LINES and ("fallback" in line.lower() or "WARNING" in line):
                    cleaned = line.strip(" |").strip()
                    if cleaned:
                        warnings.append(cleaned)
                if len(found) == len(_HEADER_KEYS) and i >= _OUTCAR_WARNING_LINES:
                    break
        size = path.stat().st_size
        with path.open("rb") as raw:
            raw.seek(max(0, size - _OUTCAR_TAIL_BYTES))
            tail = raw.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return found, warnings, []
    return found, warnings, tail


def _first_match(lines: list[str], needle: str) -> str | None:
    for line in lines:
        if needle in line:
            return line.strip()
    return None


def scan_vasp_runs(workspace: Path | str, limit: int = 40) -> list[dict[str, Any]]:
    """Scan the VASP run directories in the workspace and collect objective facts about resource use."""
    workspace = Path(workspace)
    rows: list[dict[str, Any]] = []
    for outcar in sorted(workspace.rglob("OUTCAR"))[:limit]:
        found, warnings, tail = _scan_outcar(outcar)
        if not found and not tail:
            continue
        run_dir = outcar.parent
        row: dict[str, Any] = {"dir": str(run_dir.relative_to(workspace))}

        nions = found.get("NIONS")
        if nions:
            parts = nions.split()
            if parts and parts[-1].isdigit():
                row["atoms"] = int(parts[-1])

        ranks = found.get("mpi-ranks")
        if ranks:
            nums = [int(t) for t in ranks.replace(",", " ").split() if t.isdigit()]
            if len(nums) >= 2:
                row["ranks"], row["threads"] = nums[0], nums[1]

        ncore = found.get("one band on NCORE")
        if ncore:
            nums = [int(t) for t in ncore.replace("=", " ").split() if t.isdigit()]
            if nums:
                row["ncore_effective"] = nums[0]

        row["gpu_initialised"] = "GPUs detected" in found

        elapsed = _first_match(tail, "Elapsed time")
        if elapsed:
            try:
                row["elapsed_s"] = float(elapsed.split(":")[-1])
            except ValueError:
                pass

        # The warning block VASP prints itself -- the agent never looks at it, but VASP has already said its piece.
        row["warnings"] = sorted(set(warnings))

        incar = run_dir / "INCAR"
        if incar.exists():
            try:
                text = incar.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            for tag in ("NCORE", "NPAR", "KPAR"):
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.upper().startswith(tag) and "=" in stripped:
                        value = stripped.split("=", 1)[1].split("#")[0].strip()
                        if value.isdigit():
                            row[f"{tag.lower()}_declared"] = int(value)
                        break

        state = run_dir / ".vasp_run_state.json"
        if state.exists():
            try:
                data = json.loads(state.read_text(encoding="utf-8"))
                for key in ("exe", "np", "gpu_per_task", "mode"):
                    if key in data:
                        row[key] = data[key]
            except (OSError, ValueError):
                pass

        rows.append(row)
    return rows


def resource_anomalies(rows: list[dict[str, Any]]) -> list[str]:
    """Mechanical consistency checks.

    Only report "declared vs actual" and "internally inconsistent" -- no domain conclusions are encoded.
    The goal is to put the clues in front of the distillation step and let the model ask "why" itself,
    rather than answering for it.
    """
    flags: list[str] = []

    # 1. A small system slower than a large one: clearly against the size trend, usually meaning some step did not get the resources it should have.
    sized = [r for r in rows if r.get("atoms") and r.get("elapsed_s")]
    for small in sized:
        for big in sized:
            if small is big:
                continue
            if small["atoms"] < big["atoms"] and small["elapsed_s"] > big["elapsed_s"] * 1.5:
                flags.append(
                    f"`{small['dir']}` ({small['atoms']} atoms, {small['elapsed_s']:.0f} s) "
                    f"is slower than `{big['dir']}` ({big['atoms']} atoms, {big['elapsed_s']:.0f} s): "
                    "a smaller system took longer -- check whether this step used the wrong executable or parallel mode"
                )
                break

    # 2. INCAR sets a parallel parameter that did not take effect (typically NCORE cannot form core groups with a single rank).
    for row in rows:
        declared = row.get("ncore_declared")
        effective = row.get("ncore_effective")
        if declared and effective and declared != effective:
            flags.append(
                f"`{row['dir']}`: INCAR declares NCORE={declared}, but OUTCAR shows effective NCORE={effective} "
                f"(ranks={row.get('ranks', '?')}) -- the declared parallel parameter had no effect"
            )

    # 3. A GPU was requested but there is no GPU initialization record.
    for row in rows:
        exe = str(row.get("exe") or "")
        if "gpu" in exe.lower() and not row.get("gpu_initialised"):
            flags.append(f"`{row['dir']}`: submitted with `{exe}`, but OUTCAR has no GPU initialization record")

    return flags


def format_resource_section(rows: list[dict[str, Any]]) -> str:
    """Render the "resource use and anomalies" section, to be inserted into the digest for distillation."""
    if not rows:
        return ""
    lines = ["## Resource use (on-disk facts, not the agent's view)", ""]
    lines.append("| Directory | Atoms | ranks×threads | NCORE effective/declared | exe | GPU initialised | Wall clock |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in rows:
        ncore = f"{row.get('ncore_effective', '?')}/{row.get('ncore_declared', '—')}"
        ranks = f"{row.get('ranks', '?')}×{row.get('threads', '?')}"
        elapsed = f"{row['elapsed_s']:.0f} s" if row.get("elapsed_s") else "—"
        lines.append(
            f"| `{row['dir']}` | {row.get('atoms', '?')} | {ranks} | {ncore} | "
            f"{row.get('exe', '—')} | {'yes' if row.get('gpu_initialised') else 'no'} | {elapsed} |"
        )

    flags = resource_anomalies(rows)
    if flags:
        lines += ["", "### ⚠ Resource anomalies (worth writing into a skill as preventive instructions)", ""]
        lines += [f"{i}. {flag}" for i, flag in enumerate(flags, 1)]

    warnings = sorted({w for row in rows for w in row.get("warnings", [])})
    if warnings:
        lines += ["", "### VASP's own warnings (deduplicated)", ""]
        lines += [f"- {w}" for w in warnings[:15]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Exit: trajectory capture
# ---------------------------------------------------------------------------

def write_trajectory_digest(workspace: Path | str, repo_root: Path | str) -> tuple[Path, str, int]:
    """Compress this session into a digest in the candidate pool and return (path, one-line summary).

    This step **must** finish before any model-dependent action: consolidation can be postponed, but a lost trajectory is gone.
    It uses the same extract_trajectory as batch_runner, and the output goes into the same candidate pool.
    """
    repo_root = Path(repo_root)
    workspace = Path(workspace)
    scripts_dir = repo_root / ".claude" / "skills" / "simple-skill-creator" / "scripts"
    for path in (repo_root, scripts_dir):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    from extract_trajectory import extract, to_markdown  # type: ignore

    from src.event_log import resolve_log_path

    data = extract(resolve_log_path(workspace))
    digest_dir = repo_root / "runs" / DIGEST_DIRNAME
    digest_dir.mkdir(parents=True, exist_ok=True)

    # The on-site scan result goes before the "Failure list": the two are complementary evidence --
    # the failure list holds what raised errors, the resource table holds what raised no error but may have gone wrong.
    try:
        resource_section = format_resource_section(scan_vasp_runs(workspace))
    except Exception:  # A scan failure should not sink the archive
        resource_section = ""

    def _with_resources(text: str) -> str:
        if not resource_section:
            return text
        marker = "## Failure list"
        if marker in text:
            return text.replace(marker, resource_section + "\n" + marker, 1)
        return text + "\n" + resource_section

    out = digest_dir / f"{workspace.name}.md"
    out.write_text(_with_resources(to_markdown(data)), encoding="utf-8")
    # Also write a trimmed --errors-only version. For a long session the full digest can reach 200 KB /
    # 40k tokens, far beyond a single Read's limit; handing the whole thing to the model would get it
    # truncated or even break the upstream request, while "overview + failure list" is the part
    # consolidation really needs and is usually only a few KB.
    errors_out = digest_dir / f"{workspace.name}.errors.md"
    errors_out.write_text(_with_resources(to_markdown(data, errors_only=True)), encoding="utf-8")

    totals = data.get("totals") if isinstance(data, dict) else None
    totals = totals if isinstance(totals, dict) else {}
    turns = int(totals.get("turns") or 0)
    summary = f"{out.stat().st_size // 1024} KB, {turns} turns, {totals.get('errors', 0)} tool failures"
    return out, summary, turns


# ---------------------------------------------------------------------------
# Exit: consolidation instruction
# ---------------------------------------------------------------------------

def build_consolidation_prompt(
    *,
    digest_path: Path,
    covered_by: list[str],
    coverage: dict[str, Any] | None,
    repo_root: Path | str,
) -> str:
    """Build the consolidation instruction handed to the agent at exit.

    With coverage take path B (improve those skills); without coverage take path C (create new). Both
    paths require **reading the digest rather than relying on session memory** -- the model's recall at
    the end of a long session is unreliable, and the digest already picks out the failure list, which
    is exactly the input consolidation needs most.
    """
    task_type = str((coverage or {}).get("task_type") or "").strip()
    task_label = f" ({task_type})" if task_type else ""
    errors_path = digest_path.with_suffix(".errors.md")
    head = (
        "This task is finished; now perform skill consolidation. Load `Skill: simple-skill-creator` and follow its process.\n\n"
        "**Read this first (a few KB, overview + failure list)**: "
        f"`{errors_path}`\n"
        "The failure list contains every tool return with `is_error` and is the main basis for consolidation; each failure should be turned into "
        "a preventive instruction, with the reason stated.\n\n"
        f"**The full trajectory is at**: `{digest_path}`. It may be hundreds of KB and tens of thousands of tokens, "
        "so **do not Read the whole file** -- it would be truncated or even break the request. When you need details, locate them with `Grep` by keyword, "
        "or page through the section you want with `Read`'s `offset`/`limit`.\n\n"
        "In any case, write based on these two files and not on session memory -- recall at the end of a long session is unreliable.\n\n"
    )
    if covered_by:
        body = (
            f"**Take path B (improve existing skills)**: this task is covered by {', '.join('`%s`' % s for s in covered_by)}.\n"
            "For each one, decide where it fell short in this run -- missing steps, unwritten edge cases, "
            "or errors you had to recover from yourself -- and fill those in. When done, launch diff_skill.py per path B for the user to review item by item.\n"
        )
    else:
        body = (
            f"**Take path C (consolidate a new skill from the trajectory)**: no existing skill covers this task{task_label}; "
            "you completed it by combining scripts, consulting the literature, or working it out yourself.\n"
            "First decide whether this kind of task will recur -- only recurring tasks are worth consolidating; making a skill for a one-off request only pollutes the skill library. "
            "If it is worth it, draft a new skill and launch diff_skill.py per path C (omit `--old`) for the user to review item by item.\n"
        )
    tail = (
        f"\nOn either path, before writing you must run `python {Path(repo_root)}/.claude/skills/"
        "simple-skill-creator/scripts/quick_validate.py <skill directory>` to validate the format, "
        "and you **must not** modify `.claude/skills/` directly before the user's review passes.\n"
    )
    return head + body + tail


def describe_coverage(workspace: Path | str) -> tuple[list[str], dict[str, Any] | None, str]:
    """Summarize the coverage verdict; return (skills to improve on path B, raw verdict, one-line note for the user).

    Path B/C is decided by ``workflow_skill`` alone -- i.e. "is there a skill that describes the overall
    workflow of this **kind** of task". This is separate from ``component_skills``: every component of a
    phonon task (relaxation, supercell, INCAR, running VASP, literature lookup) is covered by a skill,
    but the overall workflow is not, and then the right answer is to create a new skill rather than improve one.
    Early on, conflating the two misjudged path C as path B.
    """
    coverage = read_coverage(workspace)
    if isinstance(coverage, dict):
        workflow = coverage.get("workflow_skill")
        if isinstance(workflow, str) and workflow.strip() and workflow.strip().lower() != "null":
            name = workflow.strip()
            return [name], coverage, f"Entry verdict: this kind of task is fully covered by `{name}` -> suggest improving (path B)"
        if "workflow_skill" in coverage:
            components = coverage.get("component_skills")
            extra = ""
            if isinstance(components, list) and components:
                extra = f" (components are covered by {', '.join(str(s) for s in components)}, but the overall workflow is not)"
            return [], coverage, f"Entry verdict: no skill covers this kind of task{extra} -> suggest creating a new one (path C)"

    # The entry wrote no verdict or used an old format -- fall back to the hard facts in the log: if a
    # skill was actually loaded, treat it as covered. This is only a backstop; better to conservatively
    # propose an improvement than to propose nothing.
    actual = skills_loaded_from_log(workspace)
    if actual:
        return actual, coverage, f"No valid entry verdict; the log shows {', '.join(actual)} was loaded -> suggest improving (path B)"
    return [], coverage, "No entry verdict, and no skill was loaded according to the log -> suggest creating a new one (path C)"
