#!/usr/bin/env python3
"""Recompute the CO/Pt(111) adsorption energies (Table 5, Table S11) from raw outputs.

E_ads = E(adsorbed) - E(surface) - E(CO), using the final ``energy(sigma->0)``
of each geometry optimization from OUTCAR (OSZICAR rounds E0 to fewer digits);
the number of ionic steps is counted in OSZICAR. Relative energies are taken
with respect to the most stable configuration and are computed from unrounded
adsorption energies.

Expected raw layout (``--raw-dir``):
    CO/  surface/  configs/<site>_<orientation>/   (each with OUTCAR and OSZICAR)
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parents[1]
CONFIGS = [
    ("fcc", "upright"),
    ("fcc", "tilted-x"),
    ("fcc", "tilted-y"),
    ("ontop", "upright"),
    ("ontop", "tilted-x"),
    ("ontop", "tilted-y"),
]


def final_e0_and_steps(calc_dir: Path) -> tuple[float, int]:
    """Final energy(sigma->0) from OUTCAR and the number of ionic steps in OSZICAR."""
    energies = re.findall(r"energy\(sigma->0\) =\s*([-+0-9.Ee]+)",
                          (calc_dir / "OUTCAR").read_text(errors="replace"))
    if not energies:
        raise ValueError(f"no energy(sigma->0) in {calc_dir / 'OUTCAR'}")
    ionic = [ln for ln in (calc_dir / "OSZICAR").read_text(errors="replace").splitlines() if " F=" in ln]
    return float(energies[-1]), len(ionic)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", type=Path, required=True, help="raw outputs of this experiment")
    parser.add_argument("--out", type=Path, default=EXP_DIR / "results" / "per_system.csv")
    args = parser.parse_args()
    raw = args.raw_dir

    e_co, n_co = final_e0_and_steps(raw / "CO")
    e_slab, n_slab = final_e0_and_steps(raw / "surface")
    rows = [
        {"system": "CO", "component_type": "gas-phase CO", "site": "", "orientation": "",
         "e0_ev": e_co, "adsorption_energy_ev": "", "ionic_steps": n_co},
        {"system": "surface", "component_type": "clean Pt(111) slab", "site": "", "orientation": "",
         "e0_ev": e_slab, "adsorption_energy_ev": "", "ionic_steps": n_slab},
    ]
    for site, orient in CONFIGS:
        name = f"{site}_{orient.replace('-', '_')}"
        e, n = final_e0_and_steps(raw / "configs" / name)
        rows.append({"system": f"configs/{name}", "component_type": "adsorbed slab", "site": site,
                     "orientation": orient, "e0_ev": e, "adsorption_energy_ev": e - e_slab - e_co,
                     "ionic_steps": n})

    e_min = min(r["adsorption_energy_ev"] for r in rows if r["adsorption_energy_ev"] != "")
    for r in rows:
        r["relative_adsorption_energy_ev"] = (
            "" if r["adsorption_energy_ev"] == "" else r["adsorption_energy_ev"] - e_min
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["system", "component_type", "site", "orientation", "e0_ev", "adsorption_energy_ev",
            "relative_adsorption_energy_ev", "ionic_steps", "included_in_statistics"]
    with open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            r["included_in_statistics"] = "yes"
            writer.writerow({c: (f"{r[c]:.6f}" if isinstance(r[c], float) else r[c]) for c in cols})

    for r in rows:
        ads = "" if r["adsorption_energy_ev"] == "" else (
            f"E_ads={r['adsorption_energy_ev']:.6f}  dE={r['relative_adsorption_energy_ev']:.3f}")
        print(f"{r['system']:24s} E0={r['e0_ev']:.6f}  steps={r['ionic_steps']:3d}  {ads}")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
