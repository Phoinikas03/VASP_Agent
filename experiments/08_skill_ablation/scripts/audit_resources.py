#!/usr/bin/env python
"""Resource usage of the 54 Sol27LC workspaces with and without domain skills (Table S22).

Reads session logs, runner state files, and retained VASP outputs; never
executes anything from the trajectories. Hidden directories are not included.

* LLM responses: unique message IDs in the session log.
* Tokens: the last cumulative usage snapshot per session, plus the per-response
  usage of responses recorded after that snapshot (de-duplicated by message ID).
* VASP launch attempts: union of runner launch events and runner state records,
  matched by directory, start time, and process ID; state records outside the
  system's own workspace are excluded; retained OUTCARs without any runner
  record and demonstrably earlier backup OUTCARs are added. Relaunches in the
  same directory include parameter changes and checks, not only failures. The
  count is a lower bound.
* Elapsed time: sum of the timing footers of distinct retained OUTCARs (byte-
  identical copies removed) plus earlier backup OUTCARs; summed over processes,
  not exclusive GPU hours.

Paths recorded inside logs and runner states are absolute paths of the machine
that ran the experiment. --recorded-root-with / --recorded-root-without give
those roots so that they can be mapped to the local copies.

Usage:
    python audit_resources.py \
        --with-skills-runs <archive>/06_sol27lc_deepseek_v4_repeats/runs/rep1 \
        --without-skills-runs <archive>/08_skill_ablation/runs/rep1 \
        --recorded-root-with <root recorded in the logs> \
        --recorded-root-without <root recorded in the logs>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api_usage import COMMON_RATES, MODEL, TOKEN_KEYS, weighted_cost

EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
DECODER = json.JSONDecoder()
LAUNCH_LINE = re.compile(r"\[(?:local-run|flex-gpu)\] task=(\d+) dir=(\S+) log=(\S+) cmd=([^\n]+)")
VASP_TZ = timezone(timedelta(hours=8))  # OUTCAR start times are local time (UTC+8)


class Condition:
    def __init__(self, name: str, local_root: Path, recorded_root: str | None):
        self.name = name
        self.root = local_root.resolve()
        self.recorded = recorded_root.rstrip("/") if recorded_root else None

    def local(self, path: str) -> Path:
        """Map a path recorded on the original machine to the local copy."""
        if self.recorded and path.startswith(self.recorded):
            path = str(self.root) + path[len(self.recorded):]
        return Path(path).resolve()


def audit_tokens(directory: Path) -> dict:
    latest, trailing = {}, {}
    sessions = set()
    for line in (directory / "log.jsonl").open():
        if '"type": "AssistantMessage"' in line[:250]:
            r = json.loads(line)
            p = r["payload"]
            sid = r.get("session_id") or p.get("session_id")
            sessions.add(sid)
            if p.get("message_id"):
                trailing[(sid, p["message_id"])] = p.get("usage") or {}
        if '"type": "ResultMessage"' not in line[:250]:
            continue
        r = json.loads(line)
        q = r["payload"]
        sid = q.get("session_id") or r.get("session_id")
        assert sid and q.get("model_usage"), (directory, "missing cumulative usage")
        for model, usage in q["model_usage"].items():
            assert model == MODEL, (directory, model)
            prev = latest.get((sid, model), {})
            for original in TOKEN_KEYS.values():
                assert usage[original] >= prev.get(original, 0), (directory, "counter reset")
            latest[(sid, model)] = usage
        trailing.clear()
    snapshot = {f: sum(s[src] for s in latest.values()) for f, src in TOKEN_KEYS.items()}
    tail = {f: sum(u.get(f, 0) for u in trailing.values()) for f in TOKEN_KEYS}
    recovered = {f: snapshot[f] + tail[f] for f in TOKEN_KEYS}
    return {
        "sessions": len(sessions - {None}),
        "responses_after_last_snapshot": len(trailing),
        **{"snapshot_" + f: v for f, v in snapshot.items()},
        **recovered,
        "total_tokens_including_cache": sum(recovered.values()),
        "common_rate_cost_usd": weighted_cost(recovered, COMMON_RATES),
    }


def states_in_text(text: str):
    """Complete runner-state JSON objects inside returned tool output."""
    if '"run_id"' not in text:
        return
    text = re.sub(r"(?m)^\s*\d+\t", "", text)
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = DECODER.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and re.fullmatch(r"local-\d+-\d+-\d+", str(obj.get("run_id", ""))):
            yield obj


def audit_log(directory: Path):
    messages, events, states = {}, {}, {}
    for line in (directory / "log.jsonl").open():
        if '"type": "SystemMessage"' in line[:250]:
            continue  # streaming notifications, not API calls
        r = json.loads(line)
        q = r["payload"]
        kind = r["type"]
        if kind == "AssistantMessage" and q.get("message_id"):
            messages[(r.get("session_id") or q.get("session_id"), q["message_id"])] = q.get("error")
        elif kind == "VaspRunEvent":
            key = (q["dir"], q.get("started_at"), q.get("pid"))
            e = events.setdefault(key, {"directory": q["dir"], "started_at": q.get("started_at"), "pid": q.get("pid")})
            e[q["event"]] = True
        elif kind == "UserMessage":
            for b in q.get("content", []):
                if b.get("__type__") != "ToolResultBlock":
                    continue
                content = b.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(x.get("text", "") for x in content if isinstance(x, dict))
                if isinstance(content, str):
                    for s in states_in_text(content):
                        if s.get("started_at") and s.get("pid"):
                            states[s["run_id"]] = s
    for p in directory.rglob("*vasp*state*.json"):
        try:
            s = json.loads(p.read_text())
        except (ValueError, UnicodeError):
            continue
        if isinstance(s, dict) and s.get("run_id") and s.get("started_at") and s.get("pid"):
            states[s["run_id"]] = s
    return {"llm_responses": len(messages), "llm_error_responses": sum(bool(v) for v in messages.values())}, events, states


def audit_outputs(directory: Path) -> list[dict]:
    rows = []
    for p in sorted(directory.rglob("OUTCAR")):
        with p.open("rb") as handle:
            head = handle.read(2500).decode(errors="replace")
            handle.seek(max(0, p.stat().st_size - 12000))
            tail = handle.read().decode(errors="replace")
        elapsed = re.search(r"Elapsed time \(sec\):\s*([\d.]+)", tail)
        start = re.search(r"executed on\s+\S+ date (\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})", head)
        rows.append({
            "path": p,
            "normally_terminated": "General timing and accounting informations for this job:" in tail,
            "elapsed_seconds": float(elapsed[1]) if elapsed else None,
            "vasp_start_local": start[1] if start else "",
        })
    return rows


def reconcile(cond: Condition, system_dir: Path, events: dict, states: dict, outputs: list[dict]) -> dict:
    identified = {}
    for e in events.values():
        if not e.get("launched"):
            continue
        workdir = cond.local(str(system_dir / e["directory"]))
        if workdir.is_relative_to(system_dir):
            identified[(str(workdir), e["started_at"], e["pid"])] = "launched_event"
    for s in states.values():
        workdir = cond.local(str(s["workdir"]))
        if workdir.is_relative_to(system_dir):
            identified.setdefault((str(workdir), s["started_at"], s["pid"]), "runner_state")
    by_dir = defaultdict(list)
    for key in identified:
        by_dir[key[0]].append(key)

    seen, distinct = {}, []
    for r in outputs:
        digest = hashlib.sha256(r["path"].read_bytes()).hexdigest()
        if digest not in seen:
            seen[digest] = r["path"]
            distinct.append(r)
    for r in distinct:
        workdir = str(r["path"].parent.resolve())
        if workdir not in by_dir and r["vasp_start_local"]:
            started = datetime.strptime(r["vasp_start_local"], "%Y.%m.%d %H:%M:%S").replace(tzinfo=VASP_TZ)
            key = (workdir, started.astimezone(timezone.utc).isoformat(), "")
            identified[key] = "outcar_without_runner_record"
            by_dir[workdir].append(key)
    backup_seconds = 0.0
    for p in sorted(system_dir.rglob("OUTCAR.*")):
        if not p.is_file():
            continue
        data = p.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            continue
        text = data.decode(errors="replace")
        m = re.search(r"executed on\s+\S+ date (\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})", text[:2500])
        if not m:
            continue
        started = datetime.strptime(m[1], "%Y.%m.%d %H:%M:%S").replace(tzinfo=VASP_TZ).astimezone(timezone.utc)
        workdir = str(p.parent.resolve())
        existing = [datetime.fromisoformat(k[1]) for k in by_dir.get(workdir, [])]
        # Count only a demonstrably earlier execution.
        if existing and started < min(existing):
            identified[(workdir, started.isoformat(), "")] = "earlier_backup_outcar"
            by_dir[workdir].append((workdir, started.isoformat(), ""))
            seen[digest] = p
            elapsed = re.search(r"Elapsed time \(sec\):\s*([\d.]+)", text[-12000:])
            backup_seconds += float(elapsed[1]) if elapsed else 0.0
    counts = Counter(k[0] for k in identified)
    return {
        "vasp_launch_attempts": len(identified),
        "launch_directories": len(counts),
        "same_directory_relaunches": sum(n - 1 for n in counts.values()),
        "launch_evidence": dict(Counter(identified.values())),
        "outcar_directories": len(outputs),
        "normally_terminated_outcars": sum(r["normally_terminated"] for r in outputs),
        "identical_outcar_copies_removed": len(outputs) - len(distinct),
        "vasp_elapsed_hours": (sum(r["elapsed_seconds"] or 0 for r in distinct) + backup_seconds) / 3600,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--with-skills-runs", type=Path,
                        default=REPO / "experiments" / "06_sol27lc_deepseek_v4_repeats" / "runs" / "rep1")
    parser.add_argument("--without-skills-runs", type=Path, default=EXP / "runs" / "rep1")
    parser.add_argument("--recorded-root-with", help="with-skills runs root as recorded in the logs")
    parser.add_argument("--recorded-root-without", help="without-skills runs root as recorded in the logs")
    parser.add_argument("--out-dir", type=Path, default=EXP / "results")
    args = parser.parse_args()

    conditions = [Condition("with_skills", args.with_skills_runs, args.recorded_root_with),
                  Condition("without_skills", args.without_skills_runs, args.recorded_root_without)]
    rows, summary = [], {}
    for cond in conditions:
        systems = sorted(p for p in cond.root.iterdir() if p.is_dir() and not p.name.startswith("."))
        assert len(systems) == 27, (cond.name, len(systems))
        for system_dir in systems:
            row = {"condition": cond.name, "system": system_dir.name}
            row.update(audit_tokens(system_dir))
            info, events, states = audit_log(system_dir)
            row.update(info)
            row.update(reconcile(cond, system_dir, events, states, audit_outputs(system_dir)))
            row["launch_evidence"] = json.dumps(row["launch_evidence"], sort_keys=True)
            rows.append(row)
            print(cond.name, system_dir.name, row["llm_responses"], row["vasp_launch_attempts"], flush=True)
        subset = [r for r in rows if r["condition"] == cond.name]
        numeric = [k for k, v in subset[0].items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        summary[cond.name] = {"totals": {k: sum(r[k] for r in subset) for k in numeric}}

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with open(args.out_dir / "resources_by_system.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    table = [
        ("Calculation directories retaining an OUTCAR", "outcar_directories"),
        ("VASP launch attempts", "vasp_launch_attempts"),
        ("Recorded LLM responses (unique message IDs)", "llm_responses"),
        ("Uncached input tokens", "input_tokens"),
        ("Cache-read input tokens", "cache_read_input_tokens"),
        ("Output tokens", "output_tokens"),
        ("Total tokens, including cache reads", "total_tokens_including_cache"),
        ("Estimated API cost at a common rate (USD)", "common_rate_cost_usd"),
        ("Cumulative VASP process elapsed time (h)", "vasp_elapsed_hours"),
    ]
    with open(args.out_dir / "resources_table_S22.csv", "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["metric", "with_domain_skills", "without_domain_skills"])
        for label, key in table:
            writer.writerow([label, summary["with_skills"]["totals"][key], summary["without_skills"]["totals"][key]])
    (args.out_dir / "resources_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    for label, key in table:
        print(f"{label:48s} {summary['with_skills']['totals'][key]:>16,.3f} {summary['without_skills']['totals'][key]:>16,.3f}")


if __name__ == "__main__":
    main()
