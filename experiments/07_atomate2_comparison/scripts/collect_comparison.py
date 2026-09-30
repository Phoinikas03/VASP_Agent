#!/usr/bin/env python
"""Collect both arms of experiment 07 into the result tables.

Lattice constants: both arms are refitted with experiments/common/eos_fit.py
(normally terminated, electronically converged static points; third-order
Birch-Murnaghan). The VASP Agent arm is Run 1 of experiment 06.

Band gaps: for each arm and material, the hybrid-functional calculation on a
uniform k-point mesh (no zero-weight k points, ISPIN = 1) that terminated
normally and converged electronically; the gap is read from the eigenvalues in
vasprun.xml.

Usage:
    python collect_comparison.py \
        --agent-lc-runs <archive>/06_sol27lc_deepseek_v4_repeats/runs/rep1 \
        --atomate2-lc-runs <archive>/07_atomate2_comparison/runs_atomate2_sol27lc \
        --agent-bg-runs <archive>/07_atomate2_comparison/runs_agent_bandgap/rep1 \
        --atomate2-bg-runs <archive>/07_atomate2_comparison/runs_atomate2_bandgap
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[1]
sys.path.insert(0, str(REPO / "experiments" / "common"))
import eos_fit  # noqa: E402

MATERIALS = ["Si", "GaAs", "GaP", "ZnO", "Cu2O"]
ATOMATE2_EOS_DONE = "Finished job - MP GGA EOS Maker postprocessing"


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def lattice_constants(agent_runs: Path, atomate2_runs: Path, out: Path) -> None:
    with open(REPO / "data" / "sol27lc" / "expert_pbe_reference.csv", newline="") as fh:
        reference = {r["system_id"]: float(r["expert_pbe_lattice_constant_angstrom"]) for r in csv.DictReader(fh)}
    structures = REPO / "data" / "sol27lc" / "structures"

    rows = []
    for system in sorted(reference):
        natoms = eos_fit.poscar_natoms(structures / system / "POSCAR")
        stype = system.split("_")[1]
        agent = eos_fit.fit_lattice_constant(eos_fit.agent_calc_dirs(agent_runs / system), natoms, stype)
        a2 = eos_fit.fit_lattice_constant(eos_fit.atomate2_calc_dirs(atomate2_runs / system), natoms, stype)
        # The Si_dia workflow was run first as a pilot with the same settings;
        # its log is <system>.pilot.log.
        logs = [atomate2_runs / f"{system}{suffix}.log" for suffix in ("", ".pilot")]
        a2_complete = any(log.exists() and ATOMATE2_EOS_DONE in log.read_text(errors="replace")
                          for log in logs)
        ref = reference[system]
        rows.append({
            "system": system,
            "expert_pbe_A": ref,
            "agent_a_A": agent["a_conv_A"],
            "atomate2_a_A": a2["a_conv_A"],
            "agent_signed_deviation_pct": 100 * (agent["a_conv_A"] - ref) / ref,
            "atomate2_signed_deviation_pct": 100 * (a2["a_conv_A"] - ref) / ref,
            "agent_n_points": agent["n_points"],
            "atomate2_n_points": a2["n_points"],
            "agent_R2": agent["R2"],
            "atomate2_R2": a2["R2"],
            "agent_encut_eV": ";".join(f"{e:g}" for e in agent["encuts_eV"]),
            "atomate2_encut_eV": ";".join(f"{e:g}" for e in a2["encuts_eV"]),
            "atomate2_workflow_completed": a2_complete,
            "minimum_bracketed": agent["minimum_bracketed"] and a2["minimum_bracketed"],
        })
    write_csv(out / "sol27lc_per_system.csv", rows)

    summary = []
    for arm in ("agent", "atomate2"):
        dev = [abs(r[f"{arm}_signed_deviation_pct"]) for r in rows]
        completed = len(rows) if arm == "agent" else sum(r["atomate2_workflow_completed"] for r in rows)
        summary.append({
            "method": "VASP Agent (DeepSeek v4, Run 1)" if arm == "agent" else "atomate2",
            "workflow_completion": f"{completed}/{len(rows)}",
            "systems_fitted": len(rows),
            "mean_abs_relative_deviation_pct": statistics.mean(dev),
            "median_abs_relative_deviation_pct": statistics.median(dev),
            "max_abs_relative_deviation_pct": max(dev),
            "min_R2": min(r[f"{arm}_R2"] for r in rows),
            "points_min": min(r[f"{arm}_n_points"] for r in rows),
            "points_max": max(r[f"{arm}_n_points"] for r in rows),
        })
    write_csv(out / "sol27lc_summary.csv", summary)
    for s in summary:
        print(s)


def hybrid_stages(workspace: Path) -> list[dict]:
    """Normally terminated, electronically converged hybrid runs under a workspace."""
    from pymatgen.io.vasp.outputs import Vasprun

    # atomate2 gzips its outputs (INCAR.gz, vasprun.xml.gz, OUTCAR.gz).
    job_dirs = sorted({
        p.parent for pattern in ("**/INCAR", "**/INCAR.gz") for p in workspace.glob(pattern)
        if not any(part.startswith(".") for part in p.parent.relative_to(workspace).parts)
    })
    stages = []
    for d in job_dirs:
        incar = eos_fit.read_text(d / "INCAR")
        if not re.search(r"^\s*LHFCALC\s*=\s*\.?T", incar, re.M | re.I):
            continue
        xml = d / "vasprun.xml"
        if not xml.exists():
            xml = Path(str(xml) + ".gz")
        if not xml.exists():
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                run = Vasprun(str(xml), parse_dos=False, parse_projected_eigen=False,
                              parse_potcar_file=False, exception_on_bad_xml=True)
        except Exception:  # noqa: BLE001 - incomplete XML means the stage did not finish
            continue
        if not (run.converged_electronic and eos_fit.outcar_point(d / "OUTCAR").finished):
            continue
        gap, cbm, vbm, direct = run.eigenvalue_band_properties
        stages.append({
            "directory": str(d.relative_to(workspace)),
            "gap_eV": float(gap),
            "direct": bool(direct),
            "nkpoints": len(run.actual_kpoints),
            "zero_weight_kpoints": sum(abs(w) < 1e-12 for w in run.actual_kpoints_weights),
            "ispin": run.parameters.get("ISPIN"),
            "encut_eV": run.incar.get("ENCUT"),
            "kspacing": run.parameters.get("KSPACING"),
            "potcar": ";".join(run.potcar_symbols),
        })
    return stages


def band_gaps(agent_runs: Path, atomate2_runs: Path, out: Path) -> None:
    # pymatgen reference gaps of experiment 02
    with open(REPO / "experiments" / "02_bandgap" / "results" / "per_system_all_methods.csv", newline="") as fh:
        reference = {r["system"]: float(r["reference_bandgap_ev"]) for r in csv.DictReader(fh)
                     if r["system_class"] == "VASP Agent"}

    rows, stage_rows = [], []
    for material in MATERIALS:
        row = {"material": material, "reference_gap_eV": reference[material]}
        for arm, root in (("agent", agent_runs), ("atomate2", atomate2_runs)):
            stages = hybrid_stages(root / material)
            for s in stages:
                stage_rows.append({"material": material, "arm": arm, **s})
            uniform = [s for s in stages if s["zero_weight_kpoints"] == 0 and s["ispin"] == 1]
            assert len(uniform) == 1, (material, arm, [s["directory"] for s in uniform])
            u = uniform[0]
            row.update({
                f"{arm}_gap_eV": u["gap_eV"],
                f"{arm}_direct": u["direct"],
                f"{arm}_error_eV": u["gap_eV"] - reference[material],
                f"{arm}_nkpoints": u["nkpoints"],
                f"{arm}_encut_eV": u["encut_eV"],
                f"{arm}_stage": u["directory"],
            })
        rows.append(row)
    write_csv(out / "bandgap_per_system.csv", rows)
    write_csv(out / "bandgap_hybrid_stages.csv", stage_rows)

    summary = [{
        "method": name,
        "completion": f"{len(rows)}/{len(rows)}",
        "mean_abs_error_eV": statistics.mean(abs(r[f"{arm}_error_eV"]) for r in rows),
    } for arm, name in (("agent", "VASP Agent (DeepSeek v4)"), ("atomate2", "atomate2"))]
    diffs = [abs(r["agent_gap_eV"] - r["atomate2_gap_eV"]) for r in rows]
    summary.append({"method": "|VASP Agent - atomate2|", "completion": "",
                    "mean_abs_difference_eV": statistics.mean(diffs),
                    "max_abs_difference_eV": max(diffs)})
    write_csv(out / "bandgap_summary.csv", summary)
    for s in summary:
        print(s)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent-lc-runs", type=Path,
                        default=REPO / "experiments" / "06_sol27lc_deepseek_v4_repeats" / "runs" / "rep1")
    parser.add_argument("--atomate2-lc-runs", type=Path, default=EXP / "runs_atomate2_sol27lc")
    parser.add_argument("--agent-bg-runs", type=Path, default=EXP / "runs_agent_bandgap" / "rep1")
    parser.add_argument("--atomate2-bg-runs", type=Path, default=EXP / "runs_atomate2_bandgap")
    parser.add_argument("--out-dir", type=Path, default=EXP / "results")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    lattice_constants(args.agent_lc_runs, args.atomate2_lc_runs, args.out_dir)
    band_gaps(args.agent_bg_runs, args.atomate2_bg_runs, args.out_dir)


if __name__ == "__main__":
    main()
