#!/usr/bin/env python
"""Collect Runs 1-3 into the result tables of experiment 06.

Writes to results/:
  per_system.csv   lattice constant and signed deviation per system and run (Table S24)
  run_metrics.csv  completion and mean/median absolute relative deviation per run,
                   plus mean and sample standard deviation across runs (Tables 4, S23)
  run_status.csv   driver status, wall time, rounds, and operator exchanges per run

Usage:
    python collect_repeats.py --runs-dir <archive>/06_sol27lc_deepseek_v4_repeats/runs
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[1]
sys.path.insert(0, str(REPO / "experiments" / "common"))
import eos_fit  # noqa: E402

REPS = ["rep1", "rep2", "rep3"]
# Au_fcc in Run 1 wrote its final EOS report after 1.6 h; the driver then kept
# prompting the idle agent until the 6 h wall-time limit, so run_meta.json records
# "budget_exceeded". The task itself completed normally.
STATUS_OVERRIDES = {("rep1", "Au_fcc"): "completed"}


def load_reference() -> dict[str, float]:
    with open(REPO / "data" / "sol27lc" / "expert_pbe_reference.csv", newline="") as fh:
        return {r["system_id"]: float(r["expert_pbe_lattice_constant_angstrom"]) for r in csv.DictReader(fh)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs-dir", type=Path, default=EXP / "runs")
    parser.add_argument("--out-dir", type=Path, default=EXP / "results")
    args = parser.parse_args()

    reference = load_reference()
    structures = REPO / "data" / "sol27lc" / "structures"
    per_system, status_rows = [], []
    for system in sorted(reference):
        natoms = eos_fit.poscar_natoms(structures / system / "POSCAR")
        structure_type = system.split("_")[1]
        for rep in REPS:
            workspace = args.runs_dir / rep / system
            fit = eos_fit.fit_lattice_constant(eos_fit.agent_calc_dirs(workspace), natoms, structure_type)
            row = {"system": system, "run": rep[-1], "expert_pbe_A": reference[system]}
            if fit:
                row.update(
                    a_A=round(fit["a_conv_A"], 6),
                    # unrounded: run-level metrics are computed from these values
                    signed_deviation_pct=100 * (fit["a_conv_A"] - reference[system]) / reference[system],
                    n_points=fit["n_points"],
                    R2=round(fit["R2"], 6),
                    minimum_bracketed=fit["minimum_bracketed"],
                    B0_GPa=round(fit["B0_GPa"], 2),
                    encut_eV=";".join(f"{e:g}" for e in fit["encuts_eV"]),
                )
            per_system.append(row)

            meta_path = workspace / "run_meta.json"
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            status_rows.append({
                "system": system,
                "run": rep[-1],
                "driver_status": STATUS_OVERRIDES.get((rep, system), meta.get("status")),
                "model": meta.get("model"),
                "wall_hours": round(meta["wall_seconds"] / 3600, 3) if "wall_seconds" in meta else None,
                "rounds": meta.get("rounds"),
                "agent_turns": meta.get("total_turns"),
                "operator_exchanges": len(meta.get("qa", [])),
            })

    metric_rows = []
    for rep in REPS:
        rows = [r for r in per_system if r["run"] == rep[-1] and "a_A" in r]
        dev = [abs(r["signed_deviation_pct"]) for r in rows]
        metric_rows.append({
            "run": rep[-1],
            "completion": f"{len(rows)}/{len(reference)}",
            "mean_abs_relative_deviation_pct": statistics.mean(dev),
            "median_abs_relative_deviation_pct": statistics.median(dev),
        })
    keys = ("mean_abs_relative_deviation_pct", "median_abs_relative_deviation_pct")
    per_run = list(metric_rows)
    metric_rows.append({"run": "mean", "completion": "",
                        **{k: statistics.mean(m[k] for m in per_run) for k in keys}})
    metric_rows.append({"run": "sample_sd", "completion": "",
                        **{k: statistics.stdev([m[k] for m in per_run]) for k in keys}})

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in [("per_system.csv", per_system), ("run_metrics.csv", metric_rows),
                       ("run_status.csv", status_rows)]:
        fields = list(dict.fromkeys(k for r in rows for k in r))
        with open(args.out_dir / name, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    for m in metric_rows:
        print(m)


if __name__ == "__main__":
    main()
