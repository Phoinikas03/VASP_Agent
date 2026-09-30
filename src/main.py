import os
import re
import sys
import asyncio
import argparse
import inspect
import json
import shutil
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from claude_agent_sdk import (
    create_sdk_mcp_server, ClaudeAgentOptions, ClaudeSDKClient,
    AssistantMessage, ResultMessage, SystemMessage, TextBlock,
    ThinkingBlock, ToolResultBlock, UserMessage,
    ProcessError,
)
from claude_agent_sdk.types import StreamEvent
from src.tool_wrapper import (
    setup_vasp_inputs_tool,
    duckduckgo_search_tool, google_search_tool, visit_webpage_tool, arxiv_search_tool,
    semanticscholar_search_tool,
)
from src.result_message import result_message_indicates_failure
from webui.web_history import (
    is_skill_injection_context_text,
    parse_log_file_to_ui_events,
    todo_write_in_progress_label,
    todo_write_items_for_ui,
    write_user_turn_log,
)
from src.conversation_store import PERSIST_FILENAME, load_persist_context_for_prompt, persist_on_sdk_message
from src.event_log import (
    LOG_FILENAME,
    EventLogWriter,
    last_session_id as _log_last_session_id,
    resolve_log_path,
)
from src.scheduler import (
    AgentScheduler,
    SchedulerStateStore,
    TaskRegistry,
    WorkspaceSessionIndex,
    load_structured_resume_session_id,
)
from src.scheduler.state_store import load_session_state
from src.litellm_proxy import resolve_llm_endpoint
from src.skill_hooks import (
    SKILL_COVERAGE_FILENAME,
    build_consolidation_prompt,
    describe_coverage,
    format_skill_catalog,
    load_skill_catalog,
    write_trajectory_digest,
)

load_dotenv(REPO_ROOT / ".env")

SESSION_LOG_NAME = "log.txt"
CLAUDE_STDERR_LOG = "claude_stderr.log"
WEB_PORT = 18688
RUNS_ROOT = REPO_ROOT / "runs"


def _configure_repo_potcar_dir() -> None:
    """Expose the repository-local POTCAR library to pymatgen and child tools."""
    repo_potcar_dir = REPO_ROOT / "POTCAR_dir"
    current = os.environ.get("PMG_VASP_PSP_DIR")
    if repo_potcar_dir.is_dir() and (not current or not Path(current).exists()):
        os.environ["PMG_VASP_PSP_DIR"] = str(repo_potcar_dir)


_configure_repo_potcar_dir()

# Web: when ResultMessage.result is empty and the last tool result of this turn has is_error, avoid a UI with no explanatory text
EMPTY_RESULT_WITH_TOOL_ERROR_FALLBACK = (
    "[System notice] An error occurred during tool execution this turn, and the model returned no closing summary. "
    "See the tool error above; if it involves a script under `.claude/skills/`, first run it from the repository root "
    "(SKILL & `.claude` PATH RULE in system_prompt: `cd` to the Repository root, then run)."
)


def _parse_last_session_id(log_path: Path) -> str | None:
    """Extract the last Claude session_id (used for --resume).

    log.jsonl is read by field; only a legacy log.txt that has not been migrated falls back to a full-text regex.
    """
    if not log_path.is_file():
        return None
    if log_path.name == LOG_FILENAME:
        return _log_last_session_id(log_path)
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = re.findall(
        r"session_id='([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'",
        text,
    )
    return matches[-1] if matches else None


def _invalid_resume_session_ids(workspace: Path) -> set[str]:
    data = load_session_state(workspace)
    invalid = data.get("invalid_claude_session_ids")
    if not isinstance(invalid, list):
        return set()
    return {str(item) for item in invalid if item}


def _workspace_resume_session_id(workspace: Path, log_path: Path) -> str | None:
    invalid = _invalid_resume_session_ids(workspace)
    structured = load_structured_resume_session_id(workspace)
    if structured and structured not in invalid:
        return structured
    logged = _parse_last_session_id(log_path)
    if logged and logged not in invalid:
        return logged
    return None


def resolve_workspace(run_dir: str | None) -> tuple[Path, str | None]:
    """
    Resolve the workspace path and whether to resume a session.
    - run_dir empty: create runs/<timestamp>, no resume.
    - run_dir is a single-level directory name: legacy behavior, resolved as runs/<run_dir>.
    - run_dir is a relative/absolute path: resolved directly as that path.
    If the final directory has a structured scheduler state, the Claude session_id in it takes precedence;
    otherwise fall back to parsing log.txt for SDK resume.
    """
    base = RUNS_ROOT.resolve()
    raw = (run_dir or "").strip()
    if not raw:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ws = base / ts
        ws.mkdir(parents=True, exist_ok=True)
        return ws, None

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        ws = candidate.resolve()
    elif candidate.parent == Path("."):
        # Legacy behavior: a bare directory name is interpreted as runs/<name>.
        ws = (base / candidate.name).resolve()
    else:
        # Explicit relative paths are supported, e.g. runs/foo, ./runs/foo, ../other/foo.
        ws = candidate.resolve()

    try:
        ws.relative_to(base)
    except ValueError:
        pass
    if not ws.is_dir():
        raise ValueError(f"Directory does not exist: {ws}")
    resume = _workspace_resume_session_id(ws, resolve_log_path(ws))
    return ws, resume


def _print_process_error_help(workspace: str, resume: str | None) -> None:
    """Extra diagnostics when the CLI subprocess exits with 1 (the SDK does not put stderr into the exception object)."""
    p = Path(workspace) / CLAUDE_STDERR_LOG
    api = os.environ.get("ANTHROPIC_BASE_URL", "")
    print("\n[vasp-agent] Claude Code CLI failed to initialize (process exit 1).", file=sys.stderr)
    print(f"  ANTHROPIC_BASE_URL={api or '(not set)'}", file=sys.stderr)
    if p.is_file():
        print(f"  stderr written to: {p}", file=sys.stderr)
        try:
            tail = p.read_text(encoding="utf-8", errors="replace")[-6000:]
            print("  --- tail of claude_stderr.log ---", file=sys.stderr)
            print(tail, file=sys.stderr)
        except OSError as ex:
            print(f"  (failed to read: {ex})", file=sys.stderr)
    print(
        "  Common causes: (1) LiteLLM is not listening at BASE_URL; (2) the session to resume is no longer valid.\n"
        "  Try: curl -sS \"${ANTHROPIC_BASE_URL}/v1/models\" ; or add --no-resume to skip session resume.",
        file=sys.stderr,
    )
    if resume:
        print(f"  Current resume session_id: {resume}", file=sys.stderr)


def _stderr_indicates_missing_conversation(workspace: str, resume: str | None) -> bool:
    if not resume:
        return False
    path = Path(workspace) / CLAUDE_STDERR_LOG
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return f"No conversation found with session ID: {resume}" in text[-6000:]


def _make_agent_options(**kwargs) -> ClaudeAgentOptions:
    """Build options while tolerating older claude-agent-sdk versions."""
    supported = inspect.signature(ClaudeAgentOptions).parameters
    return ClaudeAgentOptions(**{k: v for k, v in kwargs.items() if k in supported})


def build_options(
    workspace: str,
    resume: str | None = None,
    persist_context: str | None = None,
) -> ClaudeAgentOptions:
    repo_root = str(RUNS_ROOT.parent.resolve())
    # Entry E1: session init only gives the model skill names with no description, so coverage cannot be judged.
    # Scan the frontmatter here and append it to the system prompt, so "check whether a skill covers this" has something to go on.
    skill_catalog = format_skill_catalog(load_skill_catalog(repo_root))
    coverage_file = SKILL_COVERAGE_FILENAME
    persist_block = ""
    if persist_context and persist_context.strip():
        persist_block = f"""

---
## LOCAL PERSISTED DIALOGUE (injected; new Claude Code session)
The following was saved by VASP Agent to `{PERSIST_FILENAME}` under this workspace. It is **local text recovery**, not a Claude server-side session. **Continue the user's task** (materials / VASP) coherently from this history.
---
{persist_context.strip()}
"""
    mcp_name = "vasp_agent"
    mcp_server = create_sdk_mcp_server(
        name=mcp_name,
        tools=[
            setup_vasp_inputs_tool(workspace),
            duckduckgo_search_tool(),
            google_search_tool(),
            visit_webpage_tool(),
            arxiv_search_tool(),
            semanticscholar_search_tool(),
        ],
    )
    _stderr_first = True

    def _cli_stderr(line: str) -> None:
        """Forward the Claude Code CLI stderr to help debug "exit code 1 / Check stderr" errors."""
        nonlocal _stderr_first
        print(f"[claude-code] {line}", file=sys.stderr, flush=True)
        err_path = Path(workspace) / CLAUDE_STDERR_LOG
        try:
            with err_path.open("w" if _stderr_first else "a", encoding="utf-8") as ef:
                ef.write(line + "\n")
            _stderr_first = False
        except OSError:
            pass

    # The model name is determined by resolve_llm_endpoint and written to the environment; upstream variables are not parsed again here.
    model_name = os.environ.get("CLAUDE_CODE_MODEL") or None

    return _make_agent_options(
        cwd=workspace,
        resume=resume,
        setting_sources=["project"],
        permission_mode="bypassPermissions",
        stderr=_cli_stderr,
        model=model_name,
        enable_file_checkpointing=True,
        system_prompt=f"""Your workspace directory is: {workspace}
All VASP input/output files should be read from and written to this directory.

Repository root (skills, `.claude`, and all paths documented in SKILL files): {repo_root}
The shell cwd for Bash is the **session workspace** above, which is **not** the repository root. Paths like `.claude/skills/...` are relative to **{repo_root}**, not to the workspace.

SKILL & `.claude` PATH RULE (mandatory):
- Any Bash that loads or runs skill assets (Python scripts under `.claude/skills/`, `vasp_runner.py`, `probe_env.py`, `quick_test.py`, sourcing templates, etc.) MUST start by changing directory to the repository root: prefix with `cd "{repo_root}" && ...`, **or** use absolute paths beginning with `{repo_root}/`.
- Do **not** assume `.claude` exists under the session workspace.

SKILL LIBRARY: the session's skill list gives names only. These are the project skills and what each one is for — use this table, not the bare names, to decide what applies:
{skill_catalog}

SKILL COVERAGE CHECK (mandatory, once per task, before your first heavy action). Two separate obligations — doing the first does not discharge the second:

**(a) Load** every skill above that applies, by calling the `Skill` tool. Naming a skill, reading its SKILL.md with Bash, or running a script out of its directory is **not** loading it and does not give you its instructions.

**(b) Record** the verdict by writing `{coverage_file}` in the workspace:
`{{"task_type": "<short slug>", "workflow_skill": "<name or null>", "component_skills": ["<name>", ...], "reason": "<one line>"}}`
- `workflow_skill`: the ONE skill that covers this **class of task end to end**, or `null` if no skill does. Judge the task as a whole: a task whose pieces are each covered by a helper skill, with no skill describing the overall procedure, has `workflow_skill: null`. `null` is a meaningful and common answer, not a failure — it is exactly the signal that a new skill may be worth creating.
- `component_skills`: the skills you actually loaded in (a) because they cover a part of the task.
Write it once; update it if you later load a skill you had not listed.

MANDATORY SKILL LOADS (these override your own judgement about whether you need help):
- Before any Bash that runs `mpirun`, `vasp_std`, `vasp_gpu`, `vasp_runner.py`, `quick_test.py`, or submits to Slurm/PBS: you MUST have loaded **`Skill: run-vasp`** in this session.
- Before writing or editing an **INCAR**: **`Skill: incar-builder`**.
- Before fetching or constructing a **POSCAR**: **`Skill: structure-builder`**.
Running a skill's scripts by path is **not** a substitute for loading the skill — the scripts carry no instructions, and the constraints you need (hardware alignment, parameter tables, provenance rules) live only in the SKILL.md.

VASP FILE PROVENANCE (mandatory): You MUST NOT use Write, Edit, or Bash/heredocs to manually author the full contents of **POSCAR**, **POTCAR**, or **KPOINTS**. Obtain and construct crystal structures through **`Skill: structure`** and its scripts (for example `.claude/skills/structure/scripts/fetch_mp_poscar.py`, `build_surface.py`, or `build_adsorption.py`), or through explicitly documented external retrieval procedures—not by typing lattice vectors and coordinates from memory. Generate **POTCAR** (and the POSCAR copy used with them in the workspace) **only** via **`setup_vasp_inputs`**. If the user explicitly requests a POTCAR variant, pass it through `setup_vasp_inputs` using `potcar_overrides` as a JSON object such as `{{"Cr": "Cr_pv"}}`; do not generate, edit, concatenate, or copy POTCAR manually with Bash/Python as a fallback. For workflows that need one directory per case (adsorption stages, EOS volume points, per-configuration scans), pass `work_dir` (a path relative to the workspace, e.g. `configs/ontop_upright`) to `setup_vasp_inputs` and call it once per directory — this replaces any hand-written POTCAR generation. Prefer **KSPACING** (and optionally **KGAMMA**) in **INCAR** so **`setup_vasp_inputs`** does not create a **KPOINTS** file; only when **KSPACING** is absent does the tool write **KPOINTS** from density. You MAY create or adjust **INCAR** by copying skill templates and changing parameters (ENCUT, ISMEAR, KSPACING, etc.).

CRITICAL INTERACTION RULE: You MUST NOT call or attempt to use the `AskUserQuestion` tool. Instead, whenever you finish a major workflow step, encounter an error, or need permission to proceed to a computationally expensive task (like running VASP), you MUST output a plain text block. In this text block, clearly summarize what you have achieved so far, and explicitly ask the user for confirmation to proceed to the next step. NEVER terminate your turn silently without reporting your status.

COMPUTATIONAL WORKFLOW — PLAN BEFORE SETTINGS (CRITICAL):
This applies to **all** computational workflows, not only bandgap or VASP. Before asking the user for calculation settings or execution resources, first present the workflow plan that those settings will configure.

You MUST show the plan before asking about settings such as:
- scientific settings: method level, convergence scope, precision preset, relaxation/static choice, supercell/slab size, k-point policy, smearing, spin, functional, benchmark variants, or whether to run a costly convergence/validation stage;
- execution settings: GPU vs CPU, GPU count, MPI ranks, `KPAR`/`NCORE`/`NPAR`, number of parallel jobs, Slurm/PBS partition or queue, nodes, walltime, environment modules, or local vs scheduler execution.

The plan can be concise, but it must make the next question meaningful. Include the objective, input/source, planned stages, key parameters that define the scientific result, expected expensive stages, confirmation gates, and expected outputs. If a setting changes the scientific workflow itself, present the alternatives as plan variants first, then ask the user to choose. Do not let the first visible question for a computational task be only "which hardware / how many GPUs / which queue / which setting".

END OF TURN & ANTI-SILENCE REQUIREMENT (CRITICAL):
You are STRICTLY FORBIDDEN from ending a conversation turn silently.
1. The LAST THING the user sees in your turn MUST ALWAYS be ordinary human-readable text (Chinese or English prose; Markdown allowed).
2. If your last action was a tool call (especially if the tool returned an ERROR, 'Exit code 1', or empty output), you MUST explicitly generate a text block analyzing the result or explaining the failure before waiting for the user.
3. Never end a turn with only tool calls, empty text, placeholder or control tokens (e.g. strings like "<ctrl46>" or similar), or meaningless repeated characters. If you are stuck, explicitly say (in the user's language when appropriate): "I ran into a problem and need your help..." and describe the roadblock.
4. When a turn delivers a **final result** (not intermediate progress), its closing text MUST end with exactly one line in this form:
   `[Consolidate] Needed — <one-sentence reason>`  or  `[Consolidate] Not needed — <one-sentence reason>`
   Judge it against the SKILL LIBRARY above: needed when this class of task will recur **and** no skill covered it, or when a skill you did load proved incomplete. This is a declaration, not the work itself — do not start editing skills because of it.

MARKDOWN & WEB UI (strikethrough / `~~`):
User-visible replies are rendered as Markdown (GFM). A pair of `~~` starts GitHub-Flavored-Markdown **strikethrough**, which is often triggered by accident in paths or ranges (e.g. `e_300~~e_500`). In normal prose, **do not** type two tildes in a row. For ranges or "A to B", use an en dash (–), a hyphen (-), the word "to", or a **single** `~` if needed—**not** `~~`.

SKILL CONSOLIDATION (self-evolution): Once a task is **fully complete** and produced a valid result — never mid-task — reflect on the execution trajectory and consider whether it should be consolidated into the skill library. Two cases, both handled by `simple-skill-creator`:
- **A SKILL was used and could be improved** (unclear steps, missing edge cases, errors you had to recover from): update that skill via its path B.
- **No existing skill covered the task** and you completed it by combining other skills, consulting literature, or working it out yourself: distil the trajectory into a **new** skill via its path C. This is the case that matters most for self-evolution, and it is the one most easily missed — the absence of a matching skill is precisely the signal that one is needed.
Apply judgement before consolidating: only do this when that class of task will recur. Do not create a skill for a one-off request; that pollutes the library. The full trajectory is in `<workspace>/log.jsonl` — do **not** read it directly, compress it first with `.claude/skills/simple-skill-creator/scripts/extract_trajectory.py`. Always present the result to the user for confirmation; never write into `.claude/skills/` without review.
You do not have to remember to do this at the right moment: when the session ends, the trajectory is compressed automatically and you are handed the digest together with the `{coverage_file}` verdict, which decides path B vs path C. Your obligation during the task is the SKILL COVERAGE CHECK above — record what covered the task (or that nothing did), and the exit flow will pick it up.

BASH ENVIRONMENT PROBES — NO "FAIL-FAST" CHAINS (CRITICAL):
Fragile probes that exit non-zero on the first missing binary cause Bash tools to return ERROR. In parallel tool rounds, that can trigger **Sibling tool call errored** for other tools (e.g. Skill) in the same assistant message—even though Skill content is fine.
Rules:
1. Do **not** use `&&` to chain probes where later commands should still run if an earlier command is missing (unless you intentionally want short-circuit).
2. Do **not** use `which a b c` / `command -v a b c` with multiple names if **any** may be absent: many shells report failure if **any** name is not found.
3. Prefer **separate** Bash invocations, or **one** Bash that uses `;`, `|| true`, or a `for` loop so each check is independent.
4. Avoid launching **Skill** (or other heavy tools) **in parallel** with environment-probing Bash in the **same** turn; probe first, then call Skill when the probe is clean.

Examples (copy the pattern, adapt paths):
- **BAD:** `command -v sbatch && command -v mpirun && command -v vasp_std` → exits at first missing command; rest never runs.
- **BAD:** `which mpirun vasp_std vasp_gpu` → exit 1 if any single binary is missing.
- **BAD:** `nvidia-smi -L && which mpirun vasp_std` → GPUs list OK, but if `which` fails the whole tool returns error.
- **GOOD:** `hostname; nvidia-smi -L 2>/dev/null || true` then separately: `command -v mpirun || echo "mpirun: missing"; command -v vasp_std || echo "vasp_std: missing"`
- **GOOD:** `for c in sbatch qsub mpirun vasp_std; do printf "%s: " "$c"; command -v "$c" || echo missing; done`
- **GOOD (after user gives env script):** `source /path/to/env.sh && command -v vasp_std && command -v mpirun` — here `&&` is OK because the user explicitly provided the environment.

MISSING DEPENDENCY RULE:
If you encounter 'command not found', 'Exit code 1' when probing for software, or 'ModuleNotFoundError':
1. DO NOT attempt to endlessly run alternative Bash commands or 'Read' generic system files to guess the path.
2. IMMEDIATELY STOP using tools.
3. Output a plain text message to the user, reporting exactly which executable or module is missing, and ask them to provide the explicit path or the environment setup commands (e.g. module load, export PATH).
4. DO NOT use generic 'Read' tools on binary files (like PDF) as a substitute for fixing the environment.

VASP ORCHESTRATION — PRE-CHECK TODO LIST (before the first real VASP computation):
Use **TodoWrite** to mirror this checklist and advance items to `completed` / `in_progress` as you go. This is **staged orchestration**, not a one-shot essay; you still complete each stage before starting heavy compute.

1. **Identify intent** — Quick test (e.g. INCAR/convergence sanity) vs production run; align with the user's goal.
2. **Satisfy PLAN BEFORE SETTINGS** — Before asking VASP-specific settings or resources, apply the global rule above. For VASP, the plan should include structure source, planned stages (for example convergence / relaxation / PBE SCF / HSE / DOS / band structure), key input policy (`POTCAR`, `ENCUT`, `KSPACING`, `LWAVE/LCHARG` when relevant), and confirmation gates for expensive stages.
3. **Probe environment** — **Prefer** `run-vasp` skill `scripts/probe_env.py` with Bash, obeying SKILL & `.claude` PATH RULE, e.g. `cd "{repo_root}" && python .claude/skills/run-vasp/scripts/probe_env.py` (CPU/ GPU / `sbatch`/`qsub` in PATH—no `sinfo` required). Optional extra Bash: CPU `lscpu`; GPU `nvidia-smi -L` (ignore if missing); node `hostname`. Treat Slurm as present if `sbatch` exists, PBS if `qsub` exists. Run `sinfo` / `qstat` **only** if `command -v` succeeds for them. **Never** chain `lscpu`, `nvidia-smi`, and optional `sinfo`/`qstat` with `&&` in one command (see BASH ENVIRONMENT PROBES).
4. **If BOTH GPU and CPU are detected** — Do not default to CPU. After the scientific workflow has been presented, ask the user: GPU vs CPU, and GPU count if GPU.
5. **If a workload manager (Slurm/PBS) is detected** — Do not run heavy jobs locally without user input. Ask for partition/queue, nodes, walltime, and other cluster-specific parameters needed for the job script.
6. **Confirm execution strategy** — After user answers, present the final plan as a **runner-facing command**: prefer the exact `python .claude/skills/run-vasp/scripts/vasp_runner.py ...` command with its parameters and log-file/log-prefix choices, or a full sbatch/qsub script. Explain, when useful, that `vasp_runner.py` will generate the corresponding `mpirun` / scheduler run lines in the background. Get **explicit confirmation** before launching expensive work. **Never** fire heavy local VASP work on a login node without this confirmation.

LOCAL COMPUTE — BASH `run_in_background` (mandatory):
- Any **real** workload (VASP, `vasp_runner`, `mpirun`, or anything expected to run **minutes+**) MUST be launched with **`run_in_background: true`** on Bash so the tool returns immediately and does not freeze the session (especially **web** chat). Use `nohup`, `&`, env scripts, and `run-vasp` skill patterns as documented. For formal VASP submission, prefer **`vasp_runner.py` / `quick_test.py`** as the user-facing entrypoints; the assistant should organize and confirm the runner command, and let the runner generate raw `mpirun` / job-script run lines in the background instead of hand-writing them in assistant Bash.
- **Monitoring:** Periodic checking is encouraged. Prefer a coarse cadence for long jobs (for example, every 5 minutes) and only tighten the cadence near completion or when diagnosing anomalies. You may use `grep`/`tail` on `OUTCAR`, `OSZICAR`, logs, and `pgrep`/`ps` to inspect status.
- **Sleep usage:** `sleep 300`-style waiting is allowed and often desirable for long VASP jobs, **provided** it does not freeze the user-facing session. Prefer **background** helper loops/scripts or non-blocking polling; if you use foreground waiting, keep the UI responsiveness tradeoff in mind and explain it briefly when relevant.
- **`TaskOutput` vs frozen UI (critical):** After a long Bash job is started (including when the host auto-backgrounds `vasp_runner`), avoid overly chatty polling. You **must still** fully orchestrate the run yourself (poll until done, then read OUTCAR, run `check_convergence.py`, etc.), but prefer **periodic** checks over rapid-fire checks: use **`TaskOutput` with `block: false`** at a sensible cadence, typically coarse for long jobs, or a background Bash helper that sleeps between checks. Do not use **`TaskOutput` and `block: true`** with a single huge timeout that pins the whole agent turn and **freezes web/IDE chat** until VASP finishes.
- **Slurm / PBS:** Submit → record **Job ID** → poll `squeue`/`qstat` in brief commands; do not keep one foreground Bash open until the job finishes.

PROCESS OWNERSHIP & TERMINATION SAFETY (CRITICAL):
- You MUST treat process termination as a high-risk action. **Never** use broad kill commands such as `pkill`, `killall`, `pkill -f vasp_std`, `killall vasp_std`, or pattern-based `kill $(pgrep ...)` for VASP or MPI workloads.
- When the user asks to stop/pause/cancel a VASP run, DO NOT hand-write any shell kill pipeline. Use the run-vasp termination entrypoint instead:
  - State-backed run: `cd "{repo_root}" && python .claude/skills/run-vasp/scripts/terminate.py --work-dir "<exact_task_dir>" --reason "<reason>"`
  - Legacy hand-launched run without `.vasp_run_state.json`: first inspect with `cd "{repo_root}" && python .claude/skills/run-vasp/scripts/terminate.py --work-dir "<exact_task_dir>" --allow-cwd-scan --dry-run`, then, only if every listed PID has `/proc/<pid>/cwd` exactly equal to the target task directory, run the same command without `--dry-run`.
- `pkill -f` is especially forbidden: it can match the Bash/tool wrapper command line containing the pattern itself and terminate the message reader or agent process.
- You MAY terminate a process **only if all of the following are true**:
  1. You launched that exact workload earlier in the same session/workflow; and
  2. You have concrete ownership evidence tying the running PID/job to the exact workspace/run directory or recorded task/job id you started; and
  3. You have first checked that it is actually stale, wedged, or no longer desired (for example: log file no longer changes, task state is terminal-but-process-remains, or the user explicitly asked to stop it).
- `pgrep`/`ps` name matches alone are **not** ownership evidence. A count like `pgrep -f vasp_std | wc -l` is never sufficient justification to kill anything.
- If ownership cannot be proven, you must **not** terminate the process. Instead, report what you observed, explain the ambiguity, and ask the user before any destructive action.
- If termination is justified, target only the specific PID(s) or scheduler job ID(s) you verified belong to the workload you started. Prefer the narrowest possible command and explain the evidence in user-facing text before or immediately after the action.

RESTART-IN-PLACE SAFETY FOR GPU / MPI FAILURES (CRITICAL):
- If a VASP step fails, wedges, or reports GPU/MPI errors and you plan to retry the **same physical calculation** in the **same work directory** (for example the same `pbe_scf`, `hse_scf`, or convergence-point directory), you MUST first check whether the previous owned `mpirun` / `vasp_*` process tree is still alive.
- You MUST NOT launch a second `mpirun` / VASP process into the same directory while an earlier owned run for that directory may still be alive. In particular, do **not** append `&` to start a replacement run in the background until you have proven the old owned PID tree has exited (or you have explicitly terminated that exact old owned PID tree).
- When changing GPU layout after a failure (for example retrying from 8 GPUs to 7 GPUs), prefer restarting through `vasp_runner.py` / `quick_test.py` with updated arguments, not by hand-writing a fresh ad-hoc `mpirun` command.
- Before any in-place retry, verify all of the following and report them in ordinary text: the old task/job id or PID evidence, whether the old run is still alive, whether it was terminated, and the exact replacement command you will use.
- If you cannot prove the old run is gone, stop and ask the user instead of starting a second run that could race on the same files or log.

ITERATIVE EXECUTION RULE: When performing parameter sweeps or convergence tests, DO NOT write and execute monolithic Python/Bash scripts containing loops to run VASP multiple times. Instead, manage the loop in your reasoning and run **each** heavy step **one at a time** with **`run_in_background: true`** (or the workload manager per `run-vasp`). This preserves intermediate checks and avoids many uncontrolled concurrent processes.

POTCAR SELECTION RULE: Use the pymatgen / Materials Project **recommended** pseudopotential for each element. For transition metals, alkali, alkaline-earth and many heavy elements this is the semi-core variant (e.g., Ti_pv, Fe_pv, Mn_pv, Cr_pv, Ni_pv, Li_sv, Ba_sv, Ca_sv, Na_pv, Sn_d, Pb_d), NOT the bare standard potential. `setup_vasp_inputs` applies this recommended mapping automatically. Do NOT downgrade to the fewest-valence-electron standard version to save computational cost: doing so shifts the total-energy reference (making energies incomparable with reference/benchmark data) and loses accuracy where semi-core states matter. Only deviate from the recommended choice if the user explicitly requests it, and then pass the exact element-to-POTCAR map via `potcar_overrides` (for example `{{"Cr": "Cr_pv"}}`). If `setup_vasp_inputs` rejects the override, stop and report the error instead of bypassing the tool.
{persist_block}""",
        mcp_servers={mcp_name: mcp_server},
        allowed_tools=[
            "Skill",
            f"mcp__{mcp_name}__setup_vasp_inputs",
            f"mcp__{mcp_name}__duckduckgo_search",
            f"mcp__{mcp_name}__google_search",
            f"mcp__{mcp_name}__visit_webpage",
            f"mcp__{mcp_name}__arxiv_search",
            f"mcp__{mcp_name}__semanticscholar_search",
        ],
    )


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

async def _async_input(prompt: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


def _append_sdk_log_line(log_writer: EventLogWriter, msg: Any, turn_id: str | None = None) -> None:
    """Write every SDK message to log.jsonl immediately (same order as the stream)."""
    log_writer.append_sdk_message(msg, turn_id=turn_id)
    print(repr(msg))


def _dispatch_message_to_cli(msg: Any) -> None:
    """In CLI mode, print a single message to the terminal (excluding the repr already written to the log)."""
    if isinstance(msg, StreamEvent):
        return

    subtype = getattr(msg, "subtype", "") or ""
    data = getattr(msg, "data", None)
    data = data if isinstance(data, dict) else {}
    if isinstance(msg, SystemMessage) or subtype in {"task_notification", "task_started", "task_updated"}:
        if subtype == "task_notification":
            summary = data.get("summary") or ""
            if summary:
                print(f"[Background task] {summary}\n", flush=True)
        elif subtype == "task_started":
            desc = data.get("description") or ""
            if desc:
                print(f"[Background task] started: {desc}\n", flush=True)
        elif subtype == "task_updated":
            task_id = data.get("task_id") or ""
            patch = data.get("patch") if isinstance(data.get("patch"), dict) else {}
            status = patch.get("status") or data.get("status") or "updated"
            print(f"[Background task] {task_id} {status}\n", flush=True)
        return

    if isinstance(msg, AssistantMessage):
        for block in getattr(msg, "content", []):
            block_type = type(block).__name__
            if block_type == "TextBlock" or isinstance(block, TextBlock):
                print(f"Agent> {block.text}\n", flush=True)
            elif block_type == "ToolUseBlock" or getattr(block, "type", None) == "tool_use":
                tool_name = getattr(block, "name", "UnknownTool")
                try:
                    input_str = json.dumps(block.input, indent=2, ensure_ascii=False)
                except Exception:
                    input_str = str(getattr(block, "input", ""))
                print(f" [🛠 Tool call] {tool_name}\n {input_str}\n", flush=True)
        return

    if isinstance(msg, UserMessage):
        for block in getattr(msg, "content", []):
            if type(block).__name__ == "ToolResultBlock":
                content = getattr(block, "content", "")
                is_error = getattr(block, "is_error", False)
                status_icon = "❌" if is_error else "✅"
                content_str = str(content)
                if len(content_str) > 300:
                    content_str = content_str[:300] + "\n ... [content truncated]"
                print(f" [{status_icon} Tool result]\n {content_str}\n", flush=True)
        return

    if isinstance(msg, ResultMessage):
        failed = result_message_indicates_failure(msg)
        status = "✗ Error" if failed else "✓ Done"
        extra = f"  subtype={msg.subtype!r}" if failed else ""
        print(f"\n{status}  Turns: {msg.num_turns}{extra}\n", flush=True)


async def _cli_sdk_receive_loop(
    client: ClaudeSDKClient,
    log_file,
    workspace: str,
    persist_state: dict[str, Any],
    scheduler: AgentScheduler,
) -> None:
    """Continuously consume the SDK message stream (ClaudeSDKClient.receive_messages).

    Unlike receive_response() (which ends at ResultMessage), this still receives
    SystemMessage (e.g. task_notification) while idle waiting for user input, so it is logged and printed promptly.
    """
    async for msg in client.receive_messages():
        _append_sdk_log_line(log_file, msg)
        scheduler.observe_message(msg)
        persist_on_sdk_message(workspace, msg, persist_state)
        _dispatch_message_to_cli(msg)
        if isinstance(msg, ResultMessage):
            failed = result_message_indicates_failure(msg)
            pending = scheduler.complete_result(msg, failed=failed)
            if pending:
                print("[scheduler] Interrupted; handling the new instruction...\n", flush=True)
                write_user_turn_log(log_file, pending)
                await scheduler.submit(pending)


#: Sessions with fewer turns than this do not prompt for consolidation: usually an immediate exit or an abandoned start, with no trajectory worth distilling.
_MIN_TURNS_FOR_CONSOLIDATION = 2


def _capture_trajectory_digest(workspace: str) -> Path | None:
    """Exit X1: unconditionally compress this trajectory into the candidate pool.

    A pure script with no LLM, so it cannot fail because the model "forgot". On failure it only warns and does not raise;
    the exit flow should not be blocked by consolidation.
    """
    try:
        digest_path, summary, turns = write_trajectory_digest(workspace, RUNS_ROOT.parent.resolve())
    except Exception as exc:  # a failed trajectory capture should not block exit
        print(f"[Consolidate] Trajectory compression failed, skipping: {exc}\n", flush=True)
        return None
    print(f"[Consolidate] Trajectory archived: {digest_path} ({summary})", flush=True)
    # An empty session (immediate exit or abandoned start) has nothing to consolidate; do not bother the user with it.
    return digest_path if turns >= _MIN_TURNS_FOR_CONSOLIDATION else None


async def _offer_consolidation(workspace: str, digest_path: Path) -> str | None:
    """Exit X2: read the entry-time coverage verdict and ask the user which path to take.

    Returns the consolidation instruction to send to the agent; returns None if the user skips. The verdict is taken from
    `.skill_coverage.json` first, falling back to the actual `Skill` call records in the log when it is missing --
    the latter is a hard fact and does not depend on whether the model complied.
    """
    covered_by, coverage, explanation = describe_coverage(workspace)
    print(f"[Consolidate] {explanation}", flush=True)
    try:
        answer = await _async_input("[Consolidate] Let the agent consolidate now? (y = yes / Enter = skip and exit) ")
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if answer.strip().lower() not in ("y", "yes", "是"):
        print("[Consolidate] Skipped. The digest stays in the candidate pool and can be consolidated at any time later.\n", flush=True)
        return None
    return build_consolidation_prompt(
        digest_path=digest_path,
        covered_by=covered_by,
        coverage=coverage,
        repo_root=RUNS_ROOT.parent.resolve(),
    )


def _report_consolidation_candidate(workspace: str) -> None:
    """Archive the digest and print the consolidation verdict, but do **not** start consolidation.

    For exit paths where no immediate user confirmation is available (web mode shutdown, Ctrl-C in the CLI). In these cases
    a silent exit would let the value of the whole trajectory vanish with the session, and users often do not know the digest can be used later.
    """
    digest_path = _capture_trajectory_digest(workspace)
    if digest_path is None:
        return
    _, _, explanation = describe_coverage(workspace)
    print(f"[Consolidate] {explanation}", flush=True)
    print(
        "[Consolidate] No consolidation was done this time. Later, use `python main.py --mode cli --dir "
        f"{Path(workspace).name}` to enter this workspace and type quit to run the consolidation flow.\n",
        flush=True,
    )


async def _run_consolidation_session(workspace: str, prompt: str, log_file) -> None:
    """Consolidate in a **fresh session** instead of reusing the one that just ran the task.

    On exit the old session context is already filled by the whole task -- in one measured phonon task, input reached
    415k tokens at the end while the model window is only 200k, so any further substantive work is rejected upstream
    (`400 malformed JSON body`). Consolidation only needs the trajectory digest, not the task history,
    so a separate clean session is opened whose input is just the digest path and the consolidation instruction.
    """
    options = build_options(workspace, resume=None, persist_context=None)
    async with ClaudeSDKClient(options=options) as fresh:
        await fresh.query(prompt)
        async for msg in fresh.receive_response():
            _append_sdk_log_line(log_file, msg)
            _dispatch_message_to_cli(msg)


async def cli_agent_loop(
    client: ClaudeSDKClient,
    log_file,
    workspace: str,
    scheduler: AgentScheduler,
) -> None:
    persist_state: dict[str, Any] = {"persist_assistant_buf": ""}
    drain = asyncio.create_task(
        _cli_sdk_receive_loop(client, log_file, workspace, persist_state, scheduler)
    )
    consolidation_offered = False
    try:
        while True:
            try:
                user_input = await _async_input("You> ")
            except (EOFError, KeyboardInterrupt):
                # Ctrl-C means "let me out now" and should not prompt for confirmation, but it should not be silent either: archive and tell
                # the user where the digest is and how to pick it up later.
                print()
                if not consolidation_offered:
                    _report_consolidation_candidate(workspace)
                break

            command = user_input.strip().lower()
            if command in ("quit!", "exit!", "q!"):
                _report_consolidation_candidate(workspace)
                break
            if command in ("quit", "exit", "q"):
                # X1: capture the trajectory unconditionally first. Consolidation can be postponed; a lost trajectory is gone.
                digest_path = _capture_trajectory_digest(workspace)
                if consolidation_offered or digest_path is None:
                    break
                consolidation_offered = True
                prompt = await _offer_consolidation(workspace, digest_path)
                if prompt is None:
                    break
                print("[Consolidate] Running in a separate clean session (the current session context is full from this task)...\n", flush=True)
                write_user_turn_log(log_file, prompt)
                try:
                    await _run_consolidation_session(workspace, prompt, log_file)
                except Exception as exc:
                    print(f"\n[Consolidate] Session failed: {exc}\nThe candidate digest is still at {digest_path}; you can retry later.", flush=True)
                break
            if not user_input.strip():
                continue

            control = await scheduler.handle_control_command(user_input, allow_interrupt=True)
            if control.handled:
                if control.text:
                    print(f"[scheduler] {control.text}\n", flush=True)
                continue

            if scheduler.busy:
                print("Interrupting the current reply and switching to the new instruction...\n", flush=True)
                await scheduler.interrupt(user_input)
            else:
                print("Thinking...\n", flush=True)
                write_user_turn_log(log_file, user_input)
                await scheduler.submit(user_input)
    finally:
        drain.cancel()
        with suppress(asyncio.CancelledError):
            await drain


async def cli_main(
    workspace: str,
    resume: str | None,
    log_append: bool,
    persist_context: str | None = None,
) -> None:
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    log_path = ws / LOG_FILENAME

    print(f"VASP Agent (CLI mode)  |  type quit or exit to leave")
    print(f"Working directory: {workspace}")
    if resume:
        print(f"Resuming session: {resume} (structured state or log.txt)")
    else:
        print("New session (no resume, or no session_id in the log)")
    if persist_context:
        print(f"Injected local persisted history: {Path(workspace) / PERSIST_FILENAME} (about {len(persist_context)} characters)")
    print(f"Log written to: {log_path} ({'append' if log_append else 'new'})\n")

    log_file = EventLogWriter.open_for_workspace(ws)
    store = SchedulerStateStore(workspace)
    try:
        try:
            async with ClaudeSDKClient(
                options=build_options(workspace, resume=resume, persist_context=persist_context),
            ) as client:
                scheduler = AgentScheduler(client, store, TaskRegistry(store))
                await cli_agent_loop(client, log_file, workspace, scheduler)
        except ProcessError:
            _print_process_error_help(workspace, resume)
            if not _stderr_indicates_missing_conversation(workspace, resume):
                raise
            store.clear_claude_session_id(
                invalid_session_id=resume,
                source="resume_missing_conversation",
            )
            print(
                "[System notice] The original Claude Code session can no longer be resumed, "
                "starting a new session in the same workspace and injecting the local persisted history.\n",
                flush=True,
            )
            fallback_context = load_persist_context_for_prompt(ws)
            try:
                async with ClaudeSDKClient(
                    options=build_options(workspace, resume=None, persist_context=fallback_context),
                ) as client:
                    scheduler = AgentScheduler(client, store, TaskRegistry(store))
                    await cli_agent_loop(client, log_file, workspace, scheduler)
            except ProcessError:
                _print_process_error_help(workspace, None)
                raise
    finally:
        store.close()
        log_file.close()


# ---------------------------------------------------------------------------
# Web mode
# ---------------------------------------------------------------------------

def _format_tool_result_content(content: Any) -> str:
    """Convert ToolResultBlock.content to plain text for web display."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    try:
                        parts.append(json.dumps(item, ensure_ascii=False, indent=2))
                    except Exception:
                        parts.append(str(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


async def _dispatch_message_to_web(
    msg: Any, ui: Any, session_state: dict[str, Any] | None = None,
) -> None:
    """Push a single SDK message to the web page (StreamEvent is ignored by the UI; SystemMessage shows only task-related ones)."""
    if isinstance(msg, StreamEvent):
        return

    subtype = getattr(msg, "subtype", "") or ""
    data = getattr(msg, "data", None)
    data = data if isinstance(data, dict) else {}
    if isinstance(msg, SystemMessage) or subtype in {"task_notification", "task_started", "task_updated"}:
        if subtype == "task_notification":
            summary = data.get("summary") or ""
            if summary:
                await ui.send({"type": "agent_text", "text": f"[Background task] {summary}"})
        elif subtype == "task_started":
            desc = data.get("description") or ""
            if desc:
                await ui.send({"type": "agent_text", "text": f"[Background task] started: {desc}"})
        elif subtype == "task_updated":
            task_id = data.get("task_id") or ""
            patch = data.get("patch") if isinstance(data.get("patch"), dict) else {}
            status = patch.get("status") or data.get("status") or "updated"
            await ui.send({"type": "agent_text", "text": f"[Background task] {task_id} {status}"})
        return

    if isinstance(msg, AssistantMessage):
        for block in getattr(msg, "content", []):
            block_type = type(block).__name__
            if block_type == "TextBlock" or isinstance(block, TextBlock):
                await ui.send({"type": "agent_text", "text": block.text})
            elif block_type == "ThinkingBlock" or isinstance(block, ThinkingBlock):
                await ui.send(
                    {"type": "agent_text", "text": "[Thinking]\n" + getattr(block, "thinking", "")}
                )
            elif block_type == "ToolUseBlock" or getattr(block, "type", None) == "tool_use":
                tname = getattr(block, "name", "?")
                tid = getattr(block, "id", "") or ""
                try:
                    input_str = json.dumps(block.input, indent=2, ensure_ascii=False)
                except Exception:
                    input_str = str(getattr(block, "input", ""))
                await ui.send(
                    {
                        "type": "tool_use",
                        "name": tname,
                        "input_str": input_str,
                        "tool_use_id": tid,
                    }
                )
                if tname == "TodoWrite":
                    tw_in = getattr(block, "input", None) or {}
                    await ui.send(
                        {
                            "type": "todo_update",
                            "todos": todo_write_items_for_ui(tw_in),
                        }
                    )
                    tw_label = todo_write_in_progress_label(tw_in)
                    if tw_label:
                        await ui.send(
                            {
                                "type": "status",
                                "text": f"In progress: {tw_label}",
                                "thinking": True,
                            }
                        )
        return

    if isinstance(msg, UserMessage):
        raw = getattr(msg, "content", None)
        blocks = raw if isinstance(raw, list) else []
        for block in blocks:
            if isinstance(block, ToolResultBlock):
                tid = getattr(block, "tool_use_id", "") or ""
                err = getattr(block, "is_error", None)
                is_err = bool(err) if err is not None else False
                if session_state is not None:
                    session_state["last_tool_error"] = is_err
                body = _format_tool_result_content(getattr(block, "content", None))
                await ui.send(
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "is_error": is_err,
                        "content_str": body,
                    }
                )
            elif isinstance(block, TextBlock):
                text = getattr(block, "text", "")
                if text.strip() == "[Request interrupted by user]":
                    if session_state is not None:
                        session_state["saw_interrupt_context"] = True
                    continue
                if is_skill_injection_context_text(block.text):
                    await ui.send(
                        {
                            "type": "agent_text",
                            "text": block.text,
                            "collapsed": True,
                            "collapsed_label": "Skill body (click to expand)",
                        }
                    )
                else:
                    await ui.send(
                        {"type": "agent_text", "text": "[Context]\n" + block.text}
                    )
        return

    if isinstance(msg, ResultMessage):
        has_pending_interrupt = bool(
            session_state is not None
            and (session_state.get("pending_interrupt_text") or "").strip()
        )
        user_interrupted = bool(
            session_state is not None
            and session_state.get("user_interrupt_requested")
        )
        result_text = (getattr(msg, "result", None) or "").strip()
        if (
            session_state is not None
            and not user_interrupted
            and not result_text
            and session_state.get("last_tool_error")
        ):
            await ui.send({"type": "agent_text", "text": EMPTY_RESULT_WITH_TOOL_ERROR_FALLBACK})
        if session_state is not None:
            session_state["last_tool_error"] = False
        failed = result_message_indicates_failure(msg) and not user_interrupted
        await ui.send({
            "type": "result",
            "turns": getattr(msg, "num_turns", 0),
            "error": failed,
            "interrupted": user_interrupted,
            "pending_interrupt": has_pending_interrupt,
            "subtype": getattr(msg, "subtype", None) or "",
            "summary": getattr(msg, "result", None) or "",
        })
        if session_state is not None:
            session_state["user_interrupt_requested"] = False
            session_state["saw_interrupt_context"] = False
        if has_pending_interrupt:
            await ui.send({"type": "status", "text": "Interrupted, switching to the new instruction...", "thinking": True})
        else:
            await ui.send({"type": "status", "text": "Ready — type below", "thinking": False})
            await ui.send({"type": "done"})


class _SessionSender:
    def __init__(self, ui: Any, agent_session_id: str) -> None:
        self.ui = ui
        self.agent_session_id = agent_session_id

    async def send(self, data: dict[str, Any]) -> None:
        payload = dict(data)
        payload.setdefault("agent_session_id", self.agent_session_id)
        await self.ui.send(payload)


def _with_session_id(events: list[dict[str, Any]], agent_session_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        item = dict(event)
        item.setdefault("agent_session_id", agent_session_id)
        out.append(item)
    return out


VASP_RUN_STATE_NAME = ".vasp_run_state.json"


def _live_vasp_runs(workspace: Path) -> list[tuple[str, int]]:
    """Return the VASP jobs still running under this workspace as ``(relative dir, PID)``.

    Based on the ``.vasp_run_state.json`` written by run-vasp. A job counts only if "the PID exists and its cwd
    really lies inside this workspace" -- PID existence alone is not enough, since PIDs are reused.
    """
    live: list[tuple[str, int]] = []
    for state_path in workspace.rglob(VASP_RUN_STATE_NAME):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("ended_at"):
            continue
        pid = state.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            cwd = Path(f"/proc/{pid}/cwd").resolve()
        except OSError:
            continue  # process has exited
        if cwd == workspace or workspace in cwd.parents:
            try:
                rel = str(state_path.parent.relative_to(workspace)) or "."
            except ValueError:
                rel = str(state_path.parent)
            live.append((rel, pid))
    return live


def _tasks_for_ui(store: SchedulerStateStore) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task in store.list_tasks(include_terminal=True):
        tasks.append(
            {
                "task_id": task.get("task_id"),
                "kind": task.get("kind"),
                "status": task.get("status"),
                "label": task.get("label"),
                "owner": task.get("owner"),
                "updated_at": task.get("updated_at"),
            }
        )
    return tasks


class WorkspaceRuntime:
    def __init__(
        self,
        *,
        record: dict[str, Any],
        ui: Any,
        index: WorkspaceSessionIndex,
    ) -> None:
        self.record = record
        self.agent_session_id = str(record["agent_session_id"])
        self.workspace = Path(record["workspace"])
        self.ui = ui
        self.index = index
        self.sender = _SessionSender(ui, self.agent_session_id)
        self.log_file = None
        self.store: SchedulerStateStore | None = None
        self.client: ClaudeSDKClient | None = None
        self.scheduler: AgentScheduler | None = None
        self.session_state: dict[str, Any] = {
            "busy": False,
            "interrupt_in_flight": False,
            "user_interrupt_requested": False,
            "last_tool_error": False,
            "persist_assistant_buf": "",
        }
        self.receive_task: asyncio.Task | None = None
        self.started = False
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        # The same session may be selected by two browser tabs at once, and two _ws_handler coroutines would enter here
        # concurrently, producing two ClaudeSDKClients, two receive loops, and two handles writing the same log.
        async with self._start_lock:
            if self.started:
                return
            try:
                await self._start_locked()
            except BaseException:
                # A failure midway through start leaves an open log handle and sqlite connection while started
                # is still False -- each user retry would open another copy and the previous one would leak forever.
                await self._cleanup_partial_start()
                raise

    async def _cleanup_partial_start(self) -> None:
        if self.receive_task:
            self.receive_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.receive_task
            self.receive_task = None
        if self.client:
            with suppress(Exception):
                await self.client.disconnect()
            self.client = None
        if self.store:
            with suppress(Exception):
                self.store.close()
            self.store = None
        if self.log_file:
            with suppress(Exception):
                self.log_file.close()
            self.log_file = None
        self.scheduler = None
        self.started = False

    async def _start_locked(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        log_path = self.workspace / LOG_FILENAME
        resume = _workspace_resume_session_id(self.workspace, resolve_log_path(self.workspace))
        persist_context = None if resume else load_persist_context_for_prompt(self.workspace)
        self.log_file = EventLogWriter.open_for_workspace(self.workspace)
        self.store = SchedulerStateStore(self.workspace)
        self.index.update_runtime_state(self.agent_session_id, status="opening", claude_session_id=resume)
        self.client = ClaudeSDKClient(
            options=build_options(str(self.workspace), resume=resume, persist_context=persist_context),
        )
        try:
            await self.client.connect()
        except ProcessError:
            _print_process_error_help(str(self.workspace), resume)
            if not _stderr_indicates_missing_conversation(str(self.workspace), resume):
                self.index.update_runtime_state(self.agent_session_id, status="error")
                raise
            self.store.clear_claude_session_id(
                invalid_session_id=resume,
                source="resume_missing_conversation",
            )
            self.index.clear_claude_session_id(self.agent_session_id)
            await self.sender.send(
                {
                    "type": "agent_text",
                    "text": (
                        "[System notice] The original Claude Code session can no longer be resumed, "
                        "starting a new session in the same workspace and injecting the local persisted history."
                    ),
                }
            )
            persist_context = load_persist_context_for_prompt(self.workspace)
            self.client = ClaudeSDKClient(
                options=build_options(
                    str(self.workspace),
                    resume=None,
                    persist_context=persist_context,
                ),
            )
            try:
                await self.client.connect()
            except ProcessError:
                _print_process_error_help(str(self.workspace), None)
                self.index.update_runtime_state(self.agent_session_id, status="error")
                raise
            resume = None
        self.scheduler = AgentScheduler(self.client, self.store, TaskRegistry(self.store))
        self.receive_task = asyncio.create_task(self._receive_loop())
        self.started = True
        self.index.update_runtime_state(self.agent_session_id, status="idle", claude_session_id=resume)
        await self.sender.send({"type": "log_path", "path": str(log_path)})
        await self.sender.send({"type": "task_snapshot", "tasks": _tasks_for_ui(self.store)})
        await self.send_session_list()

    async def close(self) -> None:
        # Each step must be suppressed independently: previously, if client.disconnect() raised,
        # neither store nor log_file would be closed.
        if self.receive_task:
            self.receive_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.receive_task
            self.receive_task = None
        if self.client:
            with suppress(Exception):
                await self.client.disconnect()
            self.client = None
        if self.store:
            with suppress(Exception):
                self.store.close()
            self.store = None
        if self.log_file:
            with suppress(Exception):
                self.log_file.close()
            self.log_file = None
        self.scheduler = None
        with suppress(Exception):
            self.index.update_runtime_state(self.agent_session_id, status="detached")
        self.started = False

    def history_events(self) -> list[dict[str, Any]]:
        log_path = resolve_log_path(self.workspace)
        events = parse_log_file_to_ui_events(
            log_path,
            format_tool_result=_format_tool_result_content,
            result_failed=result_message_indicates_failure,
        )
        return _with_session_id(events, self.agent_session_id)

    async def send_snapshot(self, ws: Any | None = None) -> None:
        if not self.store:
            return
        payload = {
            "type": "task_snapshot",
            "agent_session_id": self.agent_session_id,
            "tasks": _tasks_for_ui(self.store),
        }
        if ws is not None:
            await self.ui.send_to(ws, payload)
        else:
            await self.ui.send(payload)

    async def send_session_list(self) -> None:
        await self.ui.send(
            {
                "type": "session_list",
                "sessions": self.index.list_sessions(),
            }
        )

    async def handle_user_message(self, text: str) -> None:
        await self.start()
        assert self.scheduler is not None
        assert self.log_file is not None

        control = await self.scheduler.handle_control_command(text, allow_interrupt=True)
        if control.handled:
            if control.text:
                await self.sender.send({"type": "agent_text", "text": f"[scheduler]\n{control.text}"})
            await self.send_snapshot()
            if self.scheduler.busy or control.thinking:
                await self.sender.send({"type": "status", "text": "Still processing the current reply...", "thinking": True})
            else:
                await self.sender.send({"type": "status", "text": "Ready — type below", "thinking": False})
                if control.done:
                    await self.sender.send({"type": "done"})
            return

        if self.scheduler.busy:
            await self._interrupt_current_turn(text)
            return

        await self.sender.send({"type": "user_message", "text": text})
        await self.sender.send({"type": "status", "text": "Thinking...", "thinking": True})
        write_user_turn_log(self.log_file, text)
        self.session_state["last_tool_error"] = False
        await self.scheduler.submit(text)
        self.session_state["busy"] = self.scheduler.busy
        self.index.update_runtime_state(self.agent_session_id, status="running")
        await self.send_session_list()

    async def handle_interrupt(self, text: str) -> None:
        await self.start()
        assert self.scheduler is not None
        if self.scheduler.busy:
            await self._interrupt_current_turn(text)
        elif text.strip():
            await self.handle_user_message(text)
        else:
            await self.sender.send({"type": "status", "text": "Ready — type below", "thinking": False})
            await self.sender.send({"type": "done"})

    async def stop_task(self, task_id: str) -> None:
        await self.start()
        assert self.scheduler is not None
        response = await self.scheduler.handle_control_command(
            f"/stop-claude-task {task_id}",
            allow_interrupt=True,
        )
        if response.text:
            await self.sender.send({"type": "agent_text", "text": f"[scheduler]\n{response.text}"})
        await self.send_snapshot()

    async def _interrupt_current_turn(self, text: str) -> None:
        assert self.scheduler is not None
        pending = text.strip()
        self.session_state["user_interrupt_requested"] = True
        if pending:
            await self.sender.send({"type": "user_message", "text": pending})
            await self.sender.send({"type": "status", "text": "Interrupting and preparing the new instruction...", "thinking": True})
        else:
            await self.sender.send({"type": "status", "text": "Stopping the current reply...", "thinking": True})
        try:
            await self.scheduler.interrupt(pending)
            self.session_state["busy"] = self.scheduler.busy
            self.session_state["interrupt_in_flight"] = self.scheduler.interrupt_in_flight
            if self.scheduler.pending_after_interrupt:
                self.session_state["pending_interrupt_text"] = self.scheduler.pending_after_interrupt
            else:
                self.session_state.pop("pending_interrupt_text", None)
            self.index.update_runtime_state(self.agent_session_id, status="interrupting")
            await self.send_session_list()
        except Exception as e:
            self.session_state["user_interrupt_requested"] = False
            self.scheduler.interrupt_in_flight = False
            self.session_state["interrupt_in_flight"] = False
            await self.sender.send({"type": "agent_text", "text": f"[Error] Interrupt failed: {e}"})
            await self.sender.send({"type": "status", "text": "Interrupt failed, still waiting for the current reply...", "thinking": True})

    async def _receive_loop(self) -> None:
        assert self.client is not None
        assert self.log_file is not None
        assert self.scheduler is not None
        assert self.store is not None
        try:
            async for msg in self.client.receive_messages():
                _append_sdk_log_line(self.log_file, msg)
                self.scheduler.observe_message(msg)
                session_id = getattr(msg, "session_id", None)
                data = getattr(msg, "data", None)
                if not session_id and isinstance(data, dict):
                    session_id = data.get("session_id")
                if session_id:
                    self.index.update_runtime_state(
                        self.agent_session_id,
                        claude_session_id=str(session_id),
                    )
                persist_on_sdk_message(str(self.workspace), msg, self.session_state)
                if self.scheduler.pending_after_interrupt:
                    self.session_state["pending_interrupt_text"] = self.scheduler.pending_after_interrupt
                else:
                    self.session_state.pop("pending_interrupt_text", None)
                user_interrupted = bool(self.session_state.get("user_interrupt_requested"))
                await _dispatch_message_to_web(msg, self.sender, self.session_state)
                subtype = getattr(msg, "subtype", "") or ""
                if subtype in {"task_started", "task_notification", "task_updated"}:
                    await self.send_snapshot()
                if isinstance(msg, ResultMessage):
                    failed = result_message_indicates_failure(msg) and not user_interrupted
                    pending = self.scheduler.complete_result(msg, failed=failed)
                    self.session_state["busy"] = self.scheduler.busy
                    self.session_state["interrupt_in_flight"] = self.scheduler.interrupt_in_flight
                    self.session_state.pop("pending_interrupt_text", None)
                    self.index.update_runtime_state(
                        self.agent_session_id,
                        status="idle" if not failed else "error",
                    )
                    await self.send_session_list()
                    await self.send_snapshot()
                    if pending:
                        await self.sender.send(
                            {"type": "status", "text": "Handling the new instruction after the interrupt...", "thinking": True}
                        )
                        write_user_turn_log(self.log_file, pending)
                        self.session_state["last_tool_error"] = False
                        await self.scheduler.submit(pending)
                        self.session_state["busy"] = self.scheduler.busy
                        self.index.update_runtime_state(self.agent_session_id, status="running")
                        await self.send_session_list()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.index.update_runtime_state(self.agent_session_id, status="error")
            await self.send_session_list()
            await self.sender.send({"type": "agent_text", "text": f"[Error] SDK message stream error: {e}"})
            raise


class WorkspaceRuntimeManager:
    def __init__(self, *, runs_root: Path, ui: Any, initial_workspace: Path) -> None:
        self.runs_root = runs_root.resolve()
        self.ui = ui
        self.index = WorkspaceSessionIndex(self.runs_root)
        self.index.sync_from_runs()
        initial = self.index.ensure_session_for_workspace(initial_workspace, title=initial_workspace.name)
        self.active_session_id = str(initial["agent_session_id"])
        self.runtimes: dict[str, WorkspaceRuntime] = {}

    async def start_initial(self) -> None:
        await self.open_runtime(self.active_session_id)

    async def close(self) -> None:
        # Exit X1: web mode has no stdin, so the "consolidate immediately after confirmation" interaction is impossible, but **archiving must
        # still happen** -- consolidation can be postponed; a lost trajectory is gone. Write one digest for each workspace hosted in this run
        # and print the verdict; the user can later start a CLI session to finish consolidation.
        for runtime in list(self.runtimes.values()):
            _report_consolidation_candidate(str(runtime.workspace))
        for runtime in list(self.runtimes.values()):
            await runtime.close()
        self.index.close()

    async def open_runtime(self, agent_session_id: str) -> WorkspaceRuntime:
        runtime = self.runtimes.get(agent_session_id)
        if runtime:
            await runtime.start()
            return runtime
        record = self.index.get(agent_session_id)
        if not record:
            raise ValueError(f"unknown session: {agent_session_id}")
        runtime = WorkspaceRuntime(record=record, ui=self.ui, index=self.index)
        self.runtimes[agent_session_id] = runtime
        try:
            await runtime.start()
        except BaseException:
            # A runtime that failed to start must not stay in the table: the next fetch would run start() again
            # because started=False, while its internal cleanup has already released the resources once.
            self.runtimes.pop(agent_session_id, None)
            raise
        return runtime

    async def on_connect(self, ui: Any, ws: Any) -> None:
        await self.send_session_list(ws=ws)
        await self.send_session_history(self.active_session_id, ws=ws)

    async def on_event(self, data: dict[str, Any]) -> None:
        event_type = str(data.get("type") or "")
        agent_session_id = str(data.get("agent_session_id") or self.active_session_id)

        if event_type == "create_session":
            title = str(data.get("title") or "").strip() or None
            record = self.index.create_session(title=title)
            self.active_session_id = str(record["agent_session_id"])
            await self.open_runtime(self.active_session_id)
            await self.send_session_list()
            await self.send_session_history(self.active_session_id)
            return

        if event_type == "select_session":
            if not self.index.get(agent_session_id):
                await self.ui.send({"type": "agent_text", "agent_session_id": self.active_session_id, "text": f"[Error] Session not found: {agent_session_id}"})
                return
            self.active_session_id = agent_session_id
            await self.open_runtime(agent_session_id)
            await self.send_session_list()
            await self.send_session_history(agent_session_id)
            return

        if event_type == "rename_session":
            title = str(data.get("title") or "").strip()
            self.index.rename(agent_session_id, title)
            await self.send_session_list()
            return

        if event_type == "star_session":
            self.index.set_starred(agent_session_id, bool(data.get("starred")))
            await self.send_session_list()
            return

        if event_type == "delete_session":
            await self.delete_session(agent_session_id, delete_files=bool(data.get("delete_files")))
            return

        if event_type == "stop_task":
            runtime = await self.open_runtime(agent_session_id)
            await runtime.stop_task(str(data.get("task_id") or ""))
            return

        if event_type in {"user_message", "interrupt"}:
            runtime = await self.open_runtime(agent_session_id)
            text = str(data.get("text") or "")
            if event_type == "interrupt":
                await runtime.handle_interrupt(text)
            else:
                await runtime.handle_user_message(text)
            await self.send_session_list()

    async def handle_api(self, action: str, request: Any) -> Any:
        if action == "list_sessions":
            return {"sessions": self.index.list_sessions(), "active_session_id": self.active_session_id}
        if action == "create_session":
            body = await request.json() if request.can_read_body else {}
            record = self.index.create_session(title=str(body.get("title") or "").strip() or None)
            self.active_session_id = str(record["agent_session_id"])
            await self.open_runtime(self.active_session_id)
            await self.send_session_list()
            return {"session": record, "active_session_id": self.active_session_id}
        if action == "update_session":
            agent_session_id = str(request.match_info["agent_session_id"])
            body = await request.json() if request.can_read_body else {}
            if "title" in body:
                self.index.rename(agent_session_id, str(body.get("title") or ""))
            if "starred" in body:
                self.index.set_starred(agent_session_id, bool(body.get("starred")))
            await self.send_session_list()
            return {"session": self.index.get(agent_session_id)}
        if action == "delete_session":
            agent_session_id = str(request.match_info["agent_session_id"])
            body = await request.json() if request.can_read_body else {}
            deleted = await self.delete_session(agent_session_id, delete_files=bool(body.get("delete_files")))
            return {"deleted": deleted, "active_session_id": self.active_session_id}
        return {"error": f"unknown action: {action}"}

    async def delete_session(self, agent_session_id: str, *, delete_files: bool = False) -> bool:
        runtime = self.runtimes.get(agent_session_id)
        if runtime and runtime.scheduler and runtime.scheduler.busy:
            await self.ui.send(
                {
                    "type": "agent_text",
                    "agent_session_id": agent_session_id,
                    "text": "[System notice] The current session is still running and cannot be deleted. Stop the current reply or wait for it to finish first.",
                }
            )
            return False

        record = self.index.get(agent_session_id)
        if not record:
            await self.send_session_list()
            return False
        workspace = Path(record["workspace"]).resolve()

        # scheduler.busy only says whether the agent is replying; it is unrelated to background VASP.
        # Without this check, vasp_runner could be writing CHGCAR into a directory while it is rmtree'd.
        if delete_files:
            live = _live_vasp_runs(workspace)
            if live:
                detail = "; ".join(f"{d} (PID {p})" for d, p in live[:5])
                await self.ui.send(
                    {
                        "type": "agent_text",
                        "agent_session_id": agent_session_id,
                        "text": (
                            f"[System notice] This session directory still has {len(live)} VASP process(es) running, "
                            f"refusing to delete files: {detail}. Stop them first with terminate.py of run-vasp."
                        ),
                    }
                )
                return False

        if runtime:
            await runtime.close()
            self.runtimes.pop(agent_session_id, None)
        deleted = self.index.delete(agent_session_id)
        if not deleted:
            await self.send_session_list()
            return False

        if delete_files:
            try:
                workspace.relative_to(self.runs_root)
            except ValueError:
                await self.ui.send(
                    {
                        "type": "agent_text",
                        "agent_session_id": self.active_session_id,
                        "text": f"[Error] Refusing to delete a path outside the runs directory: {workspace}",
                    }
                )
                return False
            if workspace == self.runs_root:
                await self.ui.send(
                    {
                        "type": "agent_text",
                        "agent_session_id": self.active_session_id,
                        "text": "[Error] Refusing to delete the runs root directory.",
                    }
                )
                return False
            shutil.rmtree(workspace, ignore_errors=False)

        sessions = self.index.list_sessions()
        if not sessions:
            new_record = self.index.create_session()
            sessions = [new_record]
        if self.active_session_id == agent_session_id:
            self.active_session_id = str(sessions[0]["agent_session_id"])
            await self.open_runtime(self.active_session_id)
            await self.send_session_history(self.active_session_id)
        await self.send_session_list()
        return True

    async def send_session_list(self, ws: Any | None = None) -> None:
        payload = {
            "type": "session_list",
            "sessions": self.index.list_sessions(),
            "active_session_id": self.active_session_id,
        }
        if ws is not None:
            await self.ui.send_to(ws, payload)
        else:
            await self.ui.send(payload)

    async def send_session_history(self, agent_session_id: str, ws: Any | None = None) -> None:
        record = self.index.get(agent_session_id)
        if not record:
            return
        runtime = self.runtimes.get(agent_session_id)
        events = runtime.history_events() if runtime else _with_session_id(
            parse_log_file_to_ui_events(
                resolve_log_path(Path(record["workspace"])),
                format_tool_result=_format_tool_result_content,
                result_failed=result_message_indicates_failure,
            ),
            agent_session_id,
        )
        tasks: list[dict[str, Any]] = []
        if runtime and runtime.store:
            tasks = _tasks_for_ui(runtime.store)
        payload = {
            "type": "session_history",
            "agent_session_id": agent_session_id,
            "events": events,
            "log_path": str(resolve_log_path(Path(record["workspace"]))),
            "tasks": tasks,
        }
        if ws is not None:
            await self.ui.send_to(ws, payload)
        else:
            await self.ui.send(payload)


async def web_main(
    workspace: str,
    resume: str | None,
    log_append: bool,
    port: int = WEB_PORT,
    persist_context: str | None = None,
) -> None:
    from webui.web import WebUI

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    manager_holder: dict[str, WorkspaceRuntimeManager] = {}

    async def _on_event(data: dict[str, Any]) -> None:
        await manager_holder["manager"].on_event(data)

    async def _on_connect(ui_obj: Any, ws_obj: Any) -> None:
        await manager_holder["manager"].on_connect(ui_obj, ws_obj)

    async def _api_handler(action: str, request: Any) -> Any:
        return await manager_holder["manager"].handle_api(action, request)

    ui = WebUI(
        port=port,
        on_event=_on_event,
        on_connect=_on_connect,
        api_handler=_api_handler,
    )
    manager = WorkspaceRuntimeManager(runs_root=RUNS_ROOT, ui=ui, initial_workspace=ws)
    manager_holder["manager"] = manager
    await ui.start()
    if ui.port != port:
        print(f"[web] Port {port} is already in use, switched to {ui.port}", flush=True)
    print(f"Web UI: http://localhost:{ui.port}")
    print(f"Initial working directory: {workspace}")
    if resume:
        print(f"Resuming session: {resume} (structured state or log.txt)")
    else:
        print("New session (no resume, or no session_id in the log)")
    if persist_context:
        print(f"Injected local persisted history: {Path(workspace) / PERSIST_FILENAME} (about {len(persist_context)} characters)")
    print(f"Session index: {RUNS_ROOT / '.scheduler' / 'session_index.sqlite3'}\n")
    try:
        try:
            await manager.start_initial()
            while True:
                await asyncio.sleep(3600)
        except ProcessError:
            _print_process_error_help(workspace, resume)
            raise
    finally:
        await manager.close()
        await ui.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VASP Agent")
    parser.add_argument(
        "--mode",
        choices=["cli", "web"],
        default="cli",
        help="Interaction mode: cli (terminal, default) or web (browser)",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default="",
        metavar="NAME",
        help=(
            "Working directory; if omitted, create runs/<timestamp>. "
            "A single-level directory name is resolved as runs/<name>; "
            "relative or absolute paths are also supported. "
            "If the directory contains scheduler state or log.txt, session resume is attempted"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=WEB_PORT,
        metavar="N",
        help=f"Web port (web mode only, default {WEB_PORT})",
    )
    parser.add_argument(
        "--api-base",
        type=str,
        default=None,
        metavar="URL",
        help="Upstream API base URL; defaults to LLM_API_BASE in .env (UPSTREAM_API_BASE also accepted)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        metavar="KEY",
        help="Upstream API key; defaults to LLM_API_KEY in .env (UPSTREAM_API_KEY also accepted)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        metavar="NAME",
        help="Model name; defaults to LLM_MODEL in .env (UPSTREAM_MODEL also accepted)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Still use the workspace from --dir, but do not resume the session from log.txt (for invalid sessions or debugging)",
    )
    parser.add_argument(
        "--no-inject-persist",
        action="store_true",
        help="Do not inject history from conversation_turns.jsonl into the system prompt (it is still written to that file)",
    )
    parser.add_argument(
        "--force-litellm",
        action="store_true",
        help="Skip the direct-connection probe and force conversion through the in-process protocol bridge (use when the probe misjudges the upstream)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    endpoint = resolve_llm_endpoint(
        api_base=args.api_base,
        api_key=args.api_key,
        model=args.model,
        force_litellm=args.force_litellm,
    )
    print(f"[llm] {endpoint.describe()}", flush=True)
    try:
        ws_path, resume = resolve_workspace(args.dir)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    workspace_str = str(ws_path)
    log_append = bool(args.dir and str(args.dir).strip())
    if args.no_resume:
        resume = None

    persist_context: str | None = None
    if not args.no_inject_persist and resume is None:
        persist_context = load_persist_context_for_prompt(ws_path)

    if args.mode == "web":
        asyncio.run(
            web_main(workspace_str, resume, log_append, args.port, persist_context=persist_context),
        )
    else:
        asyncio.run(
            cli_main(workspace_str, resume, log_append, persist_context=persist_context),
        )


if __name__ == "__main__":
    main()
