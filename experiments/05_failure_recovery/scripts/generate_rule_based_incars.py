#!/usr/bin/env python3
"""Generate the MPRelaxSet and MITRelaxSet INCARs for LiFePO4 (Table S6).

Both input sets are generated with pymatgen for data/relax_40/LiFePO4/POSCAR.
Only the INCAR is written; POTCAR generation is disabled so that no
pseudopotential library is required. Input-set defaults can change between
pymatgen releases; the version used is printed and recorded in the output
header.
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import pymatgen
from pymatgen.core import Structure
from pymatgen.io.vasp.sets import MITRelaxSet, MPRelaxSet

REPO = Path(__file__).resolve().parents[3]
EXP_DIR = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poscar", type=Path, default=REPO / "data" / "relax_40" / "LiFePO4" / "POSCAR")
    parser.add_argument("--out-dir", type=Path, default=EXP_DIR / "results" / "lifepo4_incar")
    args = parser.parse_args()

    structure = Structure.from_file(args.poscar)
    version = getattr(pymatgen, "__version__", None)
    if version is None:
        from importlib.metadata import version as _v
        version = _v("pymatgen")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, cls in (("MPRelaxSet", MPRelaxSet), ("MITRelaxSet", MITRelaxSet)):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            incar = cls(structure, user_potcar_functional=None).incar
        text = f"# {name} generated with pymatgen {version} for data/relax_40/LiFePO4/POSCAR\n"
        text += incar.get_str(sort_keys=True)
        out = args.out_dir / f"INCAR.{name}"
        out.write_text(text)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
