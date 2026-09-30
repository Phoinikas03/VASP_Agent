#!/usr/bin/env python3
"""Evaluate the structural-relaxation benchmark (manuscript Table 2, Table S8).

Reads the archived VASP outputs and writes, for the four workflow baselines and
VASP Agent:

- results/per_system_all_methods.csv  one row per method and system; the VASP Agent rows are Table S8
- results/summary.csv                 Table 2

Expected archive layout (``--raw-dir``)::

    pymatgen_reference/<system>/
    workflow_baseline/<model>/<system>/
    vasp_agent/<system>/          (outputs in the directory itself or in one subdirectory)

Definitions (Methods, "Structural Relaxation"):

- Final energy: last ``e_0_energy`` in vasprun.xml, falling back to the last E0
  in OSZICAR.
- Final structure: CONTCAR, falling back to the final structure in vasprun.xml.
- Completion: a parseable final structure. Completed structures are matched to
  the reference with ``StructureMatcher(angle_tol=30)``; a completed case that
  cannot be matched stays in the completion count and is reported as unmatched.
- Energy deviation: (E/N - E_ref/N_ref) in meV/atom.
- RMSD: first element of ``StructureMatcher.get_rms_dist(reference, run)``,
  i.e. the RMS displacement normalized by (V/N)^(1/3).
- The lattice-angle audit reports, for each matched pair, the largest difference
  between corresponding lattice angles after the matcher's lattice mapping.

Requires pymatgen. Example::

    python scripts/evaluate_relax.py --raw-dir /path/to/archive/01_structural_relaxation
"""

from __future__ import annotations

import argparse
import csv
import re
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path
from statistics import mean, median

import numpy as np
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Lattice, Structure
from pymatgen.io.vasp.outputs import Vasprun

EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

# (system_class, model_backbone, archive subdirectory)
METHODS = [
    ("Workflow baseline", "DeepSeek-V3.2", "workflow_baseline/DeepSeek-V3.2"),
    ("Workflow baseline", "Gemini-3.1-Pro", "workflow_baseline/Gemini-3.1-Pro"),
    ("Workflow baseline", "GPT-5.2", "workflow_baseline/GPT-5.2"),
    ("Workflow baseline", "Qwen3.5-397B-A17B", "workflow_baseline/Qwen3.5-397B-A17B"),
    ("VASP Agent", "DeepSeek-V3.2", "vasp_agent"),
]

OSZICAR_E0 = re.compile(r"^\s*\d+\s+F=\s*\S+\s+E0=\s*(\S+)")
OUTPUT_NAMES = ("vasprun.xml", "OSZICAR", "OUTCAR", "CONTCAR")
FINISHED_MARKER = "General timing and accounting"


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return ""


def atom_count(directory: Path) -> int | None:
    for name in ("CONTCAR", "POSCAR"):
        lines = [line.split("#", 1)[0].strip() for line in read_text(directory / name).splitlines()]
        for idx in (6, 5):
            if len(lines) > idx:
                parts = lines[idx].split()
                if parts and all(part.isdigit() for part in parts):
                    return sum(int(part) for part in parts)
    return None


def vasprun_e0(path: Path) -> float | None:
    if not path.exists():
        return None
    last = None
    try:
        for _, elem in ET.iterparse(path, events=("end",)):
            if elem.tag == "calculation":
                energy = elem.find("energy")
                if energy is not None:
                    for child in energy:
                        if child.attrib.get("name") == "e_0_energy":
                            last = float(child.text)
                elem.clear()
    except ET.ParseError:
        return None
    return last


def final_energy(directory: Path) -> tuple[float | None, str]:
    e0 = vasprun_e0(directory / "vasprun.xml")
    if e0 is not None:
        return e0, "vasprun.xml"
    last = None
    for line in read_text(directory / "OSZICAR").splitlines():
        match = OSZICAR_E0.match(line)
        if match:
            last = float(match.group(1))
    return (last, "OSZICAR") if last is not None else (None, "")


def final_structure(directory: Path) -> Structure | None:
    for name in ("CONTCAR", "vasprun.xml"):
        path = directory / name
        if not path.exists() or path.stat().st_size == 0:
            continue
        try:
            if name == "vasprun.xml":
                return Vasprun(path, parse_potcar_file=False, parse_dos=False, parse_eigen=False).final_structure
            return Structure.from_file(path)
        except Exception:  # unreadable or truncated output: try the next source
            continue
    return None


def select_run_dir(system_dir: Path) -> Path | None:
    """Pick the calculation directory inside an archived system directory.

    Candidates are the directory itself and its subdirectories that hold VASP
    outputs, excluding archived ``intermediate_*`` snapshots. The first
    candidate with a normally terminated OUTCAR and a parseable energy is used.
    """
    if not system_dir.is_dir():
        return None
    candidates = [system_dir] + sorted(
        path
        for path in system_dir.iterdir()
        if path.is_dir()
        and not path.name.startswith("intermediate")
        and any((path / name).exists() for name in OUTPUT_NAMES)
    )
    with_energy = [path for path in candidates if final_energy(path)[0] is not None]
    finished = [path for path in with_energy if FINISHED_MARKER in read_text(path / "OUTCAR")]
    if finished:
        return finished[0]
    return with_energy[0] if with_energy else (system_dir if any((system_dir / n).exists() for n in OUTPUT_NAMES) else None)


def max_angle_difference(matcher: StructureMatcher, ref: Structure, run: Structure) -> float:
    s1, s2 = matcher._process_species([ref, run])
    s1, s2, fu, s1_supercell = matcher._preprocess(s1, s2)
    match = matcher._match(s1, s2, fu, s1_supercell, use_rms=True, break_on_match=False)
    mapped = Lattice(np.dot(match[2], s1.lattice.matrix))
    return float(np.max(np.abs(np.array(mapped.angles) - np.array(s2.lattice.angles))))


def load_systems(data_dir: Path) -> list[dict[str, str]]:
    with open(data_dir / "relax_40" / "tasks.csv", newline="") as handle:
        return [{"system": row["system"], "category": row["category"]} for row in csv.DictReader(handle)]


def evaluate(raw_dir: Path, systems: list[dict[str, str]]) -> list[dict[str, object]]:
    matcher = StructureMatcher(angle_tol=30)
    references = {}
    for item in systems:
        ref_dir = raw_dir / "pymatgen_reference" / item["system"]
        energy, _ = final_energy(ref_dir)
        count = atom_count(ref_dir)
        structure = final_structure(ref_dir)
        if energy is None or count is None or structure is None:
            raise RuntimeError(f"incomplete reference for {item['system']}: {ref_dir}")
        references[item["system"]] = (energy / count, structure)

    rows = []
    for system_class, backbone, subdir in METHODS:
        for item in systems:
            system = item["system"]
            ref_epa, ref_structure = references[system]
            run_dir = select_run_dir(raw_dir / subdir / system)
            row = {
                "system_class": system_class,
                "model_backbone": backbone,
                "system": system,
                "category": item["category"],
                "status": "no output",
                "energy_per_atom_ev": None,
                "reference_energy_per_atom_ev": ref_epa,
                "delta_e_mev_atom": None,
                "rmsd": None,
                "max_lattice_angle_difference_deg": None,
                "run_subdir": "",
                "energy_source": "",
            }
            if run_dir is not None:
                row["run_subdir"] = str(run_dir.relative_to(raw_dir / subdir / system)) if run_dir != raw_dir / subdir / system else "."
                structure = final_structure(run_dir)
                energy, source = final_energy(run_dir)
                count = atom_count(run_dir)
                if energy is not None and count:
                    row["energy_per_atom_ev"] = energy / count
                    row["delta_e_mev_atom"] = (energy / count - ref_epa) * 1000.0
                    row["energy_source"] = source
                if structure is None:
                    row["status"] = "no final structure"
                else:
                    rms = matcher.get_rms_dist(ref_structure, structure)
                    if rms is None:
                        row["status"] = "completed, unmatched"
                    else:
                        row["status"] = "completed"
                        row["rmsd"] = float(rms[0])
                        row["max_lattice_angle_difference_deg"] = max_angle_difference(matcher, ref_structure, structure)
            rows.append(row)
    return rows


def included(row: dict[str, object]) -> bool:
    return row["status"] == "completed" and row["delta_e_mev_atom"] is not None


def summarize(rows: list[dict[str, object]], n_systems: int) -> list[dict[str, object]]:
    summary = []
    for system_class, backbone, _ in METHODS:
        subset = [r for r in rows if r["system_class"] == system_class and r["model_backbone"] == backbone]
        completed = [r for r in subset if r["status"].startswith("completed")]
        used = [r for r in subset if included(r)]
        energies = [abs(r["delta_e_mev_atom"]) for r in used]
        rmsds = [r["rmsd"] for r in used]
        summary.append(
            {
                "system_class": system_class,
                "model_backbone": backbone,
                "completion": f"{len(completed)}/{n_systems}",
                "mean_energy_deviation_mev_atom": round(mean(energies), 2),
                "median_energy_deviation_mev_atom": round(median(energies), 2),
                "mean_rmsd": round(mean(rmsds), 4),
                "median_rmsd": round(median(rmsds), 4),
            }
        )
    return summary


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: object, digits: int) -> object:
    return "" if value is None else round(float(value), digits)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", type=Path, required=True, help="archive directory 01_structural_relaxation")
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data", help="repository data directory")
    parser.add_argument("--out-dir", type=Path, default=EXPERIMENT_DIR / "results")
    args = parser.parse_args()
    warnings.filterwarnings("ignore")

    systems = load_systems(args.data_dir)
    rows = evaluate(args.raw_dir, systems)

    write_csv(
        args.out_dir / "per_system_all_methods.csv",
        [
            {
                **row,
                "energy_per_atom_ev": fmt(row["energy_per_atom_ev"], 6),
                "reference_energy_per_atom_ev": fmt(row["reference_energy_per_atom_ev"], 6),
                "delta_e_mev_atom": fmt(row["delta_e_mev_atom"], 2),
                "rmsd": fmt(row["rmsd"], 4),
                "max_lattice_angle_difference_deg": fmt(row["max_lattice_angle_difference_deg"], 2),
                "included_in_statistics": "yes" if included(row) else "no",
            }
            for row in rows
        ],
    )
    write_csv(args.out_dir / "summary.csv", summarize(rows, len(systems)))

    matched = [r for r in rows if r["max_lattice_angle_difference_deg"] is not None]
    worst = max(matched, key=lambda r: r["max_lattice_angle_difference_deg"])
    print(f"matched pairs: {len(matched)}; largest lattice-angle difference "
          f"{worst['max_lattice_angle_difference_deg']:.2f} deg ({worst['model_backbone']} {worst['system_class']}, {worst['system']})")
    for row in summarize(rows, len(systems)):
        print(row)


if __name__ == "__main__":
    main()
