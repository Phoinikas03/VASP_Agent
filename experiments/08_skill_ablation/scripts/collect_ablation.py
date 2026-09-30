#!/usr/bin/env python
"""Collect accuracy and workload for the domain-skill ablation (experiment 08).

With domain skills: Run 1 of experiment 06, refitted with experiments/common/eos_fit.py.

Without domain skills: each run chose its own directory layout, so the
production energy-volume series of every system is selected by SELECTION_RULES
(a directory pattern identified from the run's own
report or trajectory, not from the deviation from the reference). Selected
points must have terminated normally, converged electronically, kept the cell
fixed, and share natoms, ENCUT, ISPIN, ISMEAR, SIGMA, POTCAR, and k sampling.
Each series is fitted with the same third-order Birch-Murnaghan form.

Workload: every calculation directory retaining an OUTCAR counts as one task;
normal termination is detected from the timing footer.

Usage:
    python collect_ablation.py \
        --with-skills-runs <archive>/06_sol27lc_deepseek_v4_repeats/runs/rep1 \
        --without-skills-runs <archive>/08_skill_ablation/runs/rep1
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[1]
sys.path.insert(0, str(REPO / "experiments" / "common"))
import eos_fit  # noqa: E402

SETTINGS = ["natoms", "encut", "ispin", "ismear", "sigma", "potcar", "k_sampling"]


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def outcar_record(outcar: Path, workspace: Path) -> dict | None:
    """Energy, volume, status, and production settings of one calculation."""
    text = eos_fit.read_text(outcar)
    header = text.split("Iteration", 1)[0]

    def tag(name: str, pattern: str, default=None):
        # INCAR-type tags are read from the header, anchored at line start
        # (NIONS, NKPTS, and SIGMA appear mid-line).
        anchor = "" if name in ("NIONS", "NKPTS", "SIGMA") else r"^\s*"
        found = re.findall(anchor + name + pattern, header, re.M)
        return float(found[-1]) if found else default

    volumes = [float(x) for x in re.findall(r"volume of cell :\s+([\d.]+)", text)]
    energies = re.findall(r"energy\(sigma->0\)\s*=\s*(-?[\d.]+)", text)
    if not volumes or not energies:
        return None
    directory = outcar.parent
    kpoints = eos_fit.read_text(directory / "KPOINTS")
    if kpoints:
        k_sampling = " | ".join(line.strip() for line in kpoints.splitlines()[1:])
    else:
        k_sampling = "KSPACING=" + str(tag("KSPACING", r"\s*=\s*([\d.]+)"))
    nsw = tag("NSW", r"\s*=\s*(\d+)", 0)
    isif = tag("ISIF", r"\s*=\s*(\d+)", 2)
    fixed_cell = nsw == 0 or (isif in (0, 1, 2) and max(volumes) - min(volumes) <= 0.01001)
    return {
        "directory": str(directory.relative_to(workspace)),
        "volume": volumes[-1],
        "energy": float(energies[-1]),
        "finished": "General timing and accounting" in text,
        "electronic": "aborting loop because EDIFF is reached" in text.rsplit("Iteration", 1)[-1],
        "fixed_cell": fixed_cell,
        "natoms": int(tag("NIONS", r"\s*=\s*(\d+)")),
        "encut": tag("ENCUT", r"\s*=\s*([\d.]+)"),
        "ispin": tag("ISPIN", r"\s*=\s*(\d+)", 1),
        "ismear": tag("ISMEAR", r"\s*=\s*(-?\d+)"),
        "sigma": tag("SIGMA", r"\s*=\s*([\d.]+)"),
        "potcar": ";".join(dict.fromkeys(re.findall(r"TITEL\s*=\s*([^\n]+)", text))),
        "k_sampling": k_sampling,
    }


def workspace_records(workspace: Path) -> list[dict]:
    records = []
    for outcar in sorted(workspace.rglob("OUTCAR*")):
        if outcar.name not in ("OUTCAR", "OUTCAR.gz"):
            continue
        if any(part.startswith(".") for part in outcar.relative_to(workspace).parts):
            continue
        rec = outcar_record(outcar.with_name("OUTCAR"), workspace)
        if rec:
            records.append(rec)
    return records


# Production E-V series per system without skills: directories matching the
# pattern, identified from each run's own report or trajectory.
SELECTION_RULES = {
    "Ag_fcc": r"eos/[^/]+",
    "Al_fcc": r"eosc18/[^/]+",
    "Au_fcc": r"eos/[^/]+",
    "Ba_bcc": r"eos/[^/]+",
    "C_dia":  r"(eos|fine|dense)/[^/]+",
    "Ca_fcc": r"eos/[^/]+",
    "Cu_fcc": r"eos/[^/]+",
    "Fe_bcc": r"calc/eos_tetra015/[^/]+",
    "Ge_dia": r"eos_ged/[^/]+",
    "Ir_fcc": r"eos/[^/]+",
    "K_bcc":  r"eosfine/scan/[^/]+",
    "Li_bcc": r"calc/scale_fix_[^/]+",
    "Mo_bcc": r"03_eos/[^/]+",
    "Na_bcc": r"eos_e700/[^/]+",
    "Nb_bcc": r"eos/[^/]+",
    "Ni_fcc": r"calc/04_eos/[^/]+",
    "Pb_fcc": r"03_eos/[^/]+",
    "Pd_fcc": r"eos/[^/]+",
    "Pt_fcc": r"calc/04_eos/[^/]+",
    "Rb_bcc": r"calc/(eos|fine)/[^/]+",
    "Rh_fcc": r"calc/eos/[^/]+",
    "Si_dia": r"eos/[^/]+",
    "Sn_dia": r"eos/[^/]+",
    "Sr_fcc": r"eos/[^/]+",
    "Ta_bcc": r"eos/[^/]+",
    "V_bcc":  r"stage3_eos/[^/]+",
    "W_bcc":  r"eq/eos/[^/]+",
}


def fit_without_skills(workspace: Path, pattern: str, structure_type: str):
    points = [r for r in workspace_records(workspace)
              if re.fullmatch(pattern, r["directory"])
              and r["finished"] and r["electronic"] and r["fixed_cell"]]
    system = workspace.name
    assert len(points) >= 5, (system, len(points))
    for key in SETTINGS:
        assert len({str(p[key]) for p in points}) == 1, (system, key)
    by_volume = {round(p["volume"], 8): p for p in sorted(points, key=lambda p: p["directory"])}
    volumes = sorted(by_volume)
    fit = eos_fit.fit_bm(volumes, [by_volume[v]["energy"] for v in volumes])
    assert fit and fit["minimum_bracketed"] and fit["B0_GPa"] > 0 and fit["R2"] > 0.99, system
    fit["a_conv_A"] = eos_fit.conventional_a(fit["V0_A3"], points[0]["natoms"], structure_type)
    return fit, points, [by_volume[v] for v in volumes]


def workload(workspace: Path) -> tuple[int, int]:
    """OUTCAR-retaining directories and those that terminated normally."""
    total = finished = 0
    for directory, _, files in os.walk(workspace):
        name = "OUTCAR" if "OUTCAR" in files else ("OUTCAR.gz" if "OUTCAR.gz" in files else None)
        if name is None:
            continue
        total += 1
        path = Path(directory) / name
        if name.endswith(".gz"):
            import gzip
            with gzip.open(path, "rb") as fh:
                tail = fh.read()[-8192:]
        else:
            with open(path, "rb") as fh:
                fh.seek(max(0, path.stat().st_size - 8192))
                tail = fh.read()
        finished += b"Total CPU time used" in tail
    return total, finished


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--with-skills-runs", type=Path,
                        default=REPO / "experiments" / "06_sol27lc_deepseek_v4_repeats" / "runs" / "rep1")
    parser.add_argument("--without-skills-runs", type=Path, default=EXP / "runs" / "rep1")
    parser.add_argument("--out-dir", type=Path, default=EXP / "results")
    args = parser.parse_args()

    with open(REPO / "data" / "sol27lc" / "expert_pbe_reference.csv", newline="") as fh:
        reference = {r["system_id"]: float(r["expert_pbe_lattice_constant_angstrom"]) for r in csv.DictReader(fh)}
    structures = REPO / "data" / "sol27lc" / "structures"

    per_system, selected, work = [], [], []
    for system in sorted(reference):
        stype = system.split("_")[1]
        ref = reference[system]
        natoms = eos_fit.poscar_natoms(structures / system / "POSCAR")
        with_fit = eos_fit.fit_lattice_constant(
            eos_fit.agent_calc_dirs(args.with_skills_runs / system), natoms, stype)
        workspace = args.without_skills_runs / system
        wo_fit, points, used = fit_without_skills(workspace, SELECTION_RULES[system], stype)
        per_system.append({
            "system": system,
            "expert_pbe_A": ref,
            "with_skills_a_A": with_fit["a_conv_A"],
            "without_skills_a_A": wo_fit["a_conv_A"],
            "with_skills_signed_deviation_pct": 100 * (with_fit["a_conv_A"] - ref) / ref,
            "without_skills_signed_deviation_pct": 100 * (wo_fit["a_conv_A"] - ref) / ref,
            "with_skills_n_points": with_fit["n_points"],
            "without_skills_n_points": wo_fit["n_points"],
            "with_skills_encut_eV": ";".join(f"{e:g}" for e in with_fit["encuts_eV"]),
            "without_skills_encut_eV": f"{points[0]['encut']:g}",
            "without_skills_potcar": points[0]["potcar"],
            "without_skills_R2": wo_fit["R2"],
        })
        for p in used:
            selected.append({"system": system, "directory": p["directory"], "volume_A3": p["volume"],
                             "energy_sigma0_eV": p["energy"], "encut_eV": p["encut"], "potcar": p["potcar"],
                             "k_sampling": p["k_sampling"]})
        for condition, root in (("with_skills", args.with_skills_runs), ("without_skills", args.without_skills_runs)):
            total, finished = workload(root / system)
            work.append({"system": system, "condition": condition, "tasks": total, "normally_terminated": finished})

    summary = []
    for condition in ("with_skills", "without_skills"):
        dev = [abs(r[f"{condition}_signed_deviation_pct"]) for r in per_system]
        tasks = [w for w in work if w["condition"] == condition]
        summary.append({
            "condition": condition,
            "completion": f"{len(dev)}/{len(reference)}",
            "mean_abs_relative_deviation_pct": statistics.mean(dev),
            "median_abs_relative_deviation_pct": statistics.median(dev),
            "total_tasks": sum(w["tasks"] for w in tasks),
            "finished_tasks": sum(w["normally_terminated"] for w in tasks),
            "median_tasks_per_system": statistics.median(w["tasks"] for w in tasks),
        })
    ratio = summary[1]["total_tasks"] / summary[0]["total_tasks"]
    diff = statistics.mean(abs(r["with_skills_a_A"] - r["without_skills_a_A"]) for r in per_system)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "per_system.csv", per_system)
    write_csv(args.out_dir / "selected_eos_points_without_skills.csv", selected)
    write_csv(args.out_dir / "workload_by_system.csv", work)
    write_csv(args.out_dir / "summary.csv", summary)
    for s in summary:
        print(s)
    print(f"task ratio without/with skills: {ratio:.3f}; mean |a_with - a_without| = {diff:.4f} A")


if __name__ == "__main__":
    main()
