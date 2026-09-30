#!/usr/bin/env python3
"""Extract the MgO case-study evidence of Supplementary Section S5 from the
archived session log.

Input is the experiment directory of the data archive (``--raw-dir``), which
mirrors the original run directory: ``log.jsonl`` at its root and the VASP
calculations under ``conv_test/`` and ``phonon/``. The anchors below are
physical line numbers of ``log.jsonl``.

Outputs (in ``results/``):

* ``timeline.csv``            elapsed minutes of each stage (Table S17)
* ``web_retrieval.csv``       every search and webpage retrieval call (Table S18, Section S5.2)
* ``process_statistics.csv``  tool calls and errors by phase
* ``session_summary.json``    model, CLI version, skills available at start, skills loaded
* ``convergence_scan.csv``    ENCUT/KSPACING scan energies (only if ``conv_test/`` is present)

    python scripts/extract_evidence.py --raw-dir <archive>/09_mgo_phonon [--check]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parents[1]
# The consolidation trigger is the first log line of the skill-generation phase.
CONSOLIDATION_START_LINE = 21144

# (start line, end line, stage) -- physical 1-based log lines; stage text as in Table S17.
TIMELINE = [
    (1, 7264, "Receive the task, search the skill library, and identify the absence of a dedicated phonon workflow skill"),
    (10266, 17474, "Search for methods and experimental references; retrieve Phonopy/VASP documentation and the MgO example"),
    (20424, 20510, "Resolve environment and directory issues, complete parameter scans, and select ENCUT=550 eV and KSPACING=0.15 1/Å"),
    (20539, 20585, "Launch relaxation of the eight-atom conventional cell, followed by dielectric-response/Born-charge calculations; use KSPACING=0.10 1/Å for the response calculation"),
    (20628, 20679, "Save BORN explicitly, construct a 64-atom 2x2x2 supercell, and launch force calculations for two independent displacements in parallel"),
    (20702, 20875, "Generate force constants, resolve post-processing API and NAC data-reading issues, and complete dispersion/DOS post-processing"),
    (20888, 20889, "Deliver curves, data, and a report containing an experimental comparison; receive user acknowledgment"),
    (21144, 25108, "Trigger consolidation; selectively read an error summary and working scripts in a new session, generate workflow-phonon, and launch the review interface"),
]
# Rounded elapsed minutes reported in Table S17.
EXPECTED_TIMELINE = [(0, 1), (2, 5), (143, 147), (153, 156), (177, 180), (187, 192), (193, 193), (194, 201)]
SEARCH_TOOLS = ("mcp__vasp_agent__duckduckgo_search", "mcp__vasp_agent__google_search",
                "mcp__vasp_agent__semanticscholar_search", "mcp__vasp_agent__arxiv_search")
EXPECTED_WEB = {"search_calls": 12, "webfetch_calls": 7}


def load(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def ts(record: dict) -> datetime:
    return datetime.fromisoformat(record["ts"])


def phase(line: int) -> str:
    return "consolidation" if line >= CONSOLIDATION_START_LINE else "calculation"


def text_of(content) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def collect(rows: list[dict]):
    """Deduplicated tool calls, their results, result messages, and session inits."""
    calls, results, seen = {}, {}, set()
    result_messages, inits = [], []
    for n, r in enumerate(rows, 1):
        p = r["payload"]
        if r["type"] == "SystemMessage" and p.get("subtype") == "init":
            inits.append((n, p.get("data", {})))
        elif r["type"] == "ResultMessage":
            result_messages.append((n, bool(p.get("is_error"))))
        elif r["type"] == "AssistantMessage":
            for b in p.get("content", []):
                key = (r.get("session_id"), b.get("id"))
                if b.get("__type__") == "ToolUseBlock" and key not in seen:
                    seen.add(key)
                    calls[b["id"]] = {"line": n, "tool": b["name"], "input": b.get("input", {})}
        elif r["type"] == "UserMessage" and isinstance(p.get("content"), list):
            for b in p["content"]:
                if isinstance(b, dict) and b.get("__type__") == "ToolResultBlock":
                    results.setdefault(b.get("tool_use_id"), (bool(b.get("is_error")), text_of(b.get("content", ""))))
    return calls, results, result_messages, inits


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def timeline(rows: list[dict], out: Path) -> list[tuple[int, int]]:
    t0 = ts(rows[0])
    table, rounded = [], []
    for start, end, stage in TIMELINE:
        a = (ts(rows[start - 1]) - t0).total_seconds() / 60
        b = (ts(rows[end - 1]) - t0).total_seconds() / 60
        rounded.append((round(a), round(b)))
        table.append([f"{round(a)}" if round(a) == round(b) else f"{round(a)}-{round(b)}",
                      f"{a:.2f}", f"{b:.2f}", start, end, stage])
    write_csv(out / "timeline.csv",
              ["elapsed_min", "start_min_exact", "end_min_exact", "log_line_start", "log_line_end", "stage"], table)
    return rounded


def outcome(tool: str, is_error: bool, text: str) -> str:
    if is_error:
        m = re.search(r"\b(\d{3}) Client Error", text)
        return f"error (HTTP {m.group(1)})" if m else "error"
    if "No papers found" in text:
        return "no papers found"
    if "REDIRECT DETECTED" in text:
        return "redirect to another host"
    return "content returned"


def web_retrieval(calls: dict, results: dict, out: Path) -> dict:
    table, counts = [], Counter()
    for cid, c in sorted(calls.items(), key=lambda kv: kv[1]["line"]):
        if c["tool"] not in SEARCH_TOOLS and c["tool"] != "WebFetch":
            continue
        is_error, text = results.get(cid, (False, ""))
        target = c["input"].get("query") or c["input"].get("url", "")
        kind = "retrieval" if c["tool"] == "WebFetch" else "search"
        counts[kind] += 1
        table.append([c["line"], kind, c["tool"].replace("mcp__vasp_agent__", ""), target,
                      outcome(c["tool"], is_error, text)])
    write_csv(out / "web_retrieval.csv", ["log_line", "kind", "tool", "query_or_url", "outcome"], table)
    return {"search_calls": counts["search"], "webfetch_calls": counts["retrieval"]}


def process_statistics(calls: dict, results: dict, result_messages: list, out: Path) -> None:
    def split(items):
        c = Counter(phase(line) for line in items)
        return [c["calculation"], c["consolidation"], c["calculation"] + c["consolidation"]]

    rows = [
        ["Tool calls"] + split([c["line"] for c in calls.values()]),
        ["Explicitly flagged tool errors"] + split([c["line"] for cid, c in calls.items()
                                                    if results.get(cid, (False,))[0]]),
        ["Skill tool calls"] + split([c["line"] for c in calls.values() if c["tool"] == "Skill"]),
        ["ResultMessage with is_error=true"] + split([n for n, err in result_messages if err]),
    ]
    write_csv(out / "process_statistics.csv",
              ["metric", "calculation_and_delivery", "consolidation", "total"], rows)


def session_summary(rows: list[dict], calls: dict, inits: list, out: Path) -> dict:
    first_init = inits[0][1]
    project_skills = sorted(set(first_init.get("skills", [])) & set(first_init.get("slash_commands", [])))
    loaded = []
    for c in sorted(calls.values(), key=lambda c: c["line"]):
        name = c["input"].get("skill") if c["tool"] == "Skill" else None
        if name and phase(c["line"]) == "calculation" and name not in loaded:
            loaded.append(name)
    sessions = {}
    for n, d in inits:  # one init record per turn; keep the first per session
        s = sessions.setdefault(d.get("session_id"), {
            "session_id": d.get("session_id"), "first_init_log_line": n, "init_records": 0,
            "models": set(), "claude_code_versions": set()})
        s["init_records"] += 1
        s["models"].add(d.get("model"))
        s["claude_code_versions"].add(d.get("claude_code_version"))
    summary = {
        "first_event_utc": rows[0]["ts"],
        "last_event_utc": rows[-1]["ts"],
        "sessions": [{**s, "models": sorted(s["models"]), "claude_code_versions": sorted(s["claude_code_versions"])}
                     for s in sessions.values()],
        "skills_listed_at_start": first_init.get("skills", []),
        "workflow_phonon_available_at_start": "workflow-phonon" in first_init.get("skills", []),
        "component_skills_loaded_during_calculation": loaded,
        "consolidation_start_log_line": CONSOLIDATION_START_LINE,
    }
    (out / "session_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def convergence_scan(raw: Path, out: Path) -> None:
    conv = raw / "conv_test"
    if not conv.is_dir():
        return
    rows = []
    for outcar in sorted(conv.glob("*/*/OUTCAR")):
        incar = (outcar.parent / "INCAR").read_text()
        tag = lambda k: re.search(rf"^\s*{k}\s*=\s*([^\s#!;]+)", incar, re.M).group(1)
        text = outcar.read_text(errors="replace")
        toten = float(re.findall(r"free\s+energy\s+TOTEN\s*=\s*([-\d.]+)", text)[-1])
        nions = int(re.search(r"NIONS\s*=\s*(\d+)", text).group(1))
        rows.append([outcar.parent.parent.name, float(tag("ENCUT")), float(tag("KSPACING")), nions, toten])
    rows.sort(key=lambda r: (r[0], r[1], -r[2]))
    table, prev = [], None
    for scan, encut, ksp, nions, e in rows:
        delta = "" if prev is None or prev[0] != scan else f"{(e - prev[1]) / nions * 1000:.4f}"
        table.append([scan, encut, ksp, nions, f"{e:.8f}", delta])
        prev = (scan, e)
    write_csv(out / "convergence_scan.csv",
              ["scan", "ENCUT_eV", "KSPACING_per_A", "atoms", "TOTEN_eV", "delta_vs_previous_meV_per_atom"], table)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--raw-dir", type=Path, required=True,
                    help="Archive directory 09_mgo_phonon/ (contains log.jsonl).")
    ap.add_argument("--out-dir", type=Path, default=EXP_DIR / "results")
    ap.add_argument("--check", action="store_true", help="Compare with the values reported in Section S5.")
    args = ap.parse_args()

    rows = load(args.raw_dir / "log.jsonl")
    calls, results, result_messages, inits = collect(rows)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rounded = timeline(rows, args.out_dir)
    web = web_retrieval(calls, results, args.out_dir)
    process_statistics(calls, results, result_messages, args.out_dir)
    summary = session_summary(rows, calls, inits, args.out_dir)
    convergence_scan(args.raw_dir, args.out_dir)

    print(json.dumps({"timeline_min": rounded, **web,
                      "component_skills": summary["component_skills_loaded_during_calculation"],
                      "workflow_phonon_available_at_start": summary["workflow_phonon_available_at_start"]},
                     indent=2))
    if args.check:
        assert rounded == EXPECTED_TIMELINE, (rounded, EXPECTED_TIMELINE)
        assert web == EXPECTED_WEB, (web, EXPECTED_WEB)
        assert len(summary["component_skills_loaded_during_calculation"]) == 7
        assert not summary["workflow_phonon_available_at_start"]
        print("check passed: timeline, search counts, and component skills match Section S5")


if __name__ == "__main__":
    main()
