#!/usr/bin/env python
"""Recorded API token usage and cost estimates for the DeepSeek v4 sessions.

Each ResultMessage.model_usage in a session log is a cumulative snapshot. The
last snapshot per session and model is taken and summed across sessions; the
successive snapshots are not summed. Only logs are read.

Run as a script, this produces the token table of the response letter
(Table R7): Sol27LC Run 1 with domain skills (27 systems) and the five HSE06
band-gap tasks of experiment 07. The functions are also used by
audit_resources.py.

Usage:
    python api_usage.py \
        --sol27lc-runs <archive>/06_sol27lc_deepseek_v4_repeats/runs/rep1 \
        --hse06-runs <archive>/07_atomate2_comparison/runs_agent_bandgap/rep1
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean, median

EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
MODEL = "deepseek-v4-flash-vision-exp"
TOKEN_KEYS = {
    "input_tokens": "inputTokens",
    "cache_read_input_tokens": "cacheReadInputTokens",
    "cache_creation_input_tokens": "cacheCreationInputTokens",
    "output_tokens": "outputTokens",
}
# Common rate used in the manuscript (USD per million tokens): DeepSeek
# off-peak pricing accessed on 9 September 2026, applied uniformly to all
# recorded tokens regardless of execution time.
COMMON_RATES = {"input_tokens": 0.22, "cache_read_input_tokens": 0.007, "output_tokens": 0.66}
# Au_fcc of Sol27LC Run 1 wrote its final EOS report after 1.6 h; the driver then
# kept prompting the idle agent until the 6 h wall-time limit, so run_meta.json
# records "budget_exceeded". The task itself completed normally.
STATUS_OVERRIDES = {("Sol27LC", "Au_fcc"): "completed"}
RATE_SOURCES = {
    "pricing": "https://api-docs.deepseek.com/quick_start/pricing/",
    "vision_same_rate": "https://api-docs.deepseek.com/news/news260821/",
}


def weighted_cost(tokens, rates):
    assert tokens["cache_creation_input_tokens"] == 0
    return sum(tokens[key] * price for key, price in rates.items()) / 1_000_000


def audit_task(group, directory):
    path = directory / "log.jsonl"
    latest = {}
    result_count = 0
    trailing_assistants = 0
    with path.open() as handle:
        for line_number, line in enumerate(handle, 1):
            if '"type": "AssistantMessage"' in line[:250]:
                trailing_assistants += 1
            if '"type": "ResultMessage"' not in line[:250]:
                continue
            record = json.loads(line)
            payload = record["payload"]
            session = payload.get("session_id") or record.get("session_id")
            assert session, (path, line_number, "missing session ID")
            model_usage = payload.get("model_usage")
            assert model_usage, (path, line_number, "missing cumulative usage")
            for model, usage in model_usage.items():
                assert model == MODEL, (path, model)
                previous = latest.get((session, model))
                for field in TOKEN_KEYS.values():
                    assert isinstance(usage[field], int) and usage[field] >= 0
                    if previous:
                        assert usage[field] >= previous[field], (path, line_number, field, "counter reset")
                latest[(session, model)] = usage
            result_count += 1
            trailing_assistants = 0
    assert latest, (path, "no result snapshot")
    assert trailing_assistants == 0, (path, "assistant messages after last usage snapshot")
    meta = json.loads((directory / "run_meta.json").read_text())
    totals = {field: sum(u[source] for u in latest.values()) for field, source in TOKEN_KEYS.items()}
    return {
        "task_group": group,
        "system": directory.name,
        "driver_status": STATUS_OVERRIDES.get((group, directory.name), meta["status"]),
        "session_count": len({session for session, _ in latest}),
        "result_message_count": result_count,
        **totals,
        "total_tokens_including_cache": sum(totals.values()),
        "common_rate_cost_usd": weighted_cost(totals, COMMON_RATES),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sol27lc-runs", type=Path,
                        default=REPO / "experiments" / "06_sol27lc_deepseek_v4_repeats" / "runs" / "rep1")
    parser.add_argument("--hse06-runs", type=Path,
                        default=REPO / "experiments" / "07_atomate2_comparison" / "runs_agent_bandgap" / "rep1")
    parser.add_argument("--out-dir", type=Path, default=EXP / "results")
    args = parser.parse_args()

    rows, summary = [], {}
    for group, root, expected in (("Sol27LC", args.sol27lc_runs, 27), ("HSE06", args.hse06_runs, 5)):
        directories = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        assert len(directories) == expected, (group, len(directories))
        subset = [audit_task(group, d) for d in directories]
        rows.extend(subset)
        keys = [k for k in subset[0] if isinstance(subset[0][k], (int, float)) and k not in ("session_count", "result_message_count")]
        summary[group] = {
            "n_tasks": len(subset),
            "totals": {k: sum(r[k] for r in subset) for k in keys},
            "means_per_task": {k: mean(r[k] for r in subset) for k in keys},
            "medians_per_task": {k: median(r[k] for r in subset) for k in keys},
        }
        print(group, json.dumps(summary[group]["totals"]))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with open(args.out_dir / "api_usage_by_task.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    report = {
        "scope": "Sol27LC Run 1 with domain skills (27 sessions) and the five HSE06 band-gap sessions of experiment 07.",
        "method": "Last cumulative ResultMessage.model_usage snapshot per session and model, summed over sessions. Full archived sessions, including interaction after the result became available.",
        "common_rates_usd_per_million_tokens": COMMON_RATES,
        "rate_basis": "DeepSeek off-peak pricing accessed on 9 September 2026, applied uniformly to all recorded tokens regardless of execution time.",
        "rate_sources": RATE_SOURCES,
        "limits": "Recorded SDK usage, not reconciled provider bills.",
        "groups": summary,
    }
    (args.out_dir / "api_usage_summary.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
