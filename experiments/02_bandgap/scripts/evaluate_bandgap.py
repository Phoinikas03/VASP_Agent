#!/usr/bin/env python3
"""Evaluate the HSE band-gap benchmark (manuscript Table 3, Table S9).

Reads the archived VASP outputs and writes, for the four workflow baselines and
VASP Agent:

- results/per_system_all_methods.csv  one row per method and system; the VASP Agent rows are Table S9
- results/summary.csv                 Table 3

Expected archive layout (``--raw-dir``)::

    pymatgen_reference/<system>/hse06_scf/
    workflow_baseline/<model>/<system>/        (final HSE step in the directory itself)
    vasp_agent/<system>/                       (HSE stage in one subdirectory or in the directory itself)

Definitions (Methods, "Bandgap Calculation"):

- Band gap: ``Vasprun.eigenvalue_band_properties`` of the final HSE vasprun.xml.
- Completion: the final HSE vasprun.xml parses completely and yields a band gap.
  A run whose vasprun.xml is truncated is not completed, even if an EIGENVAL
  file exists.
- For VASP Agent the HSE directory is the unique directory holding vasprun.xml
  outside ``convergence_test/`` and outside directories whose name starts with
  ``pbe``.

Requires pymatgen. Example::

    python scripts/evaluate_bandgap.py --raw-dir /path/to/archive/02_bandgap
"""

from __future__ import annotations

import argparse
import csv
import warnings
from pathlib import Path
from statistics import mean, median

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


def hse_gap(directory: Path) -> tuple[float | None, bool | None, str]:
    path = directory / "vasprun.xml"
    if not path.exists():
        return None, None, "no vasprun.xml"
    try:
        run = Vasprun(path, parse_potcar_file=False, parse_dos=False)
    except Exception as exc:  # truncated or malformed XML
        return None, None, f"vasprun.xml not parseable ({type(exc).__name__})"
    gap, _cbm, _vbm, direct = run.eigenvalue_band_properties
    return float(gap), bool(direct), "completed"


def agent_hse_dir(system_dir: Path) -> Path | None:
    candidates = sorted(
        path.parent
        for path in system_dir.rglob("vasprun.xml")
        if "convergence_test" not in path.relative_to(system_dir).parts
        and not path.parent.name.lower().startswith("pbe")
    )
    if len(candidates) > 1:
        raise RuntimeError(f"ambiguous HSE directory in {system_dir}: {candidates}")
    return candidates[0] if candidates else None


def load_systems(data_dir: Path) -> list[str]:
    with open(data_dir / "bandgap_24" / "tasks.csv", newline="") as handle:
        return [row["system"] for row in csv.DictReader(handle)]


def evaluate(raw_dir: Path, systems: list[str]) -> list[dict[str, object]]:
    references = {}
    for system in systems:
        gap, _, status = hse_gap(raw_dir / "pymatgen_reference" / system / "hse06_scf")
        if gap is None:
            raise RuntimeError(f"reference band gap unavailable for {system}: {status}")
        references[system] = gap

    rows = []
    for system_class, backbone, subdir in METHODS:
        for system in systems:
            system_dir = raw_dir / subdir / system
            run_dir = agent_hse_dir(system_dir) if subdir == "vasp_agent" else system_dir
            gap, direct, status = (None, None, "no output") if run_dir is None or not run_dir.exists() else hse_gap(run_dir)
            rows.append(
                {
                    "system_class": system_class,
                    "model_backbone": backbone,
                    "system": system,
                    "status": status,
                    "reference_bandgap_ev": references[system],
                    "bandgap_ev": gap,
                    "direct": direct,
                    "delta_bandgap_ev": None if gap is None else gap - references[system],
                    "run_subdir": "" if run_dir is None else (str(run_dir.relative_to(system_dir)) if run_dir != system_dir else "."),
                }
            )
    return rows


def summarize(rows: list[dict[str, object]], n_systems: int) -> list[dict[str, object]]:
    summary = []
    for system_class, backbone, _ in METHODS:
        deviations = [
            abs(r["delta_bandgap_ev"])
            for r in rows
            if r["system_class"] == system_class and r["model_backbone"] == backbone and r["status"] == "completed"
        ]
        summary.append(
            {
                "system_class": system_class,
                "model_backbone": backbone,
                "completion": f"{len(deviations)}/{n_systems}",
                "mean_bandgap_deviation_ev": round(mean(deviations), 3),
                "median_bandgap_deviation_ev": round(median(deviations), 3),
            }
        )
    return summary


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: object, digits: int = 4) -> object:
    return "" if value is None else round(float(value), digits)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", type=Path, required=True, help="archive directory 02_bandgap")
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
                "reference_bandgap_ev": fmt(row["reference_bandgap_ev"]),
                "bandgap_ev": fmt(row["bandgap_ev"]),
                "direct": "" if row["direct"] is None else ("direct" if row["direct"] else "indirect"),
                "delta_bandgap_ev": fmt(row["delta_bandgap_ev"]),
                "included_in_statistics": "yes" if row["status"] == "completed" else "no",
            }
            for row in rows
        ],
    )
    write_csv(args.out_dir / "summary.csv", summarize(rows, len(systems)))
    for row in summarize(rows, len(systems)):
        print(row)


if __name__ == "__main__":
    main()
