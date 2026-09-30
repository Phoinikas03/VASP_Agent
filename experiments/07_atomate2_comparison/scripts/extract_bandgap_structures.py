#!/usr/bin/env python
"""Prepare the five band-gap structures of experiment 07 from data/bandgap_24.

Si, GaAs, GaP, ZnO, and Cu2O are taken from the 24-system band-gap set and
reduced to the pymatgen standard primitive cell (symprec = 1e-3), which is the
cell both workflows received.

Ga2O3 was initially part of this subset; it was replaced by GaP because, at the
cutoff and GPU layout the agent chose, it would not finish within the budget.
GaP, like GaAs, is a III-V semiconductor but has an indirect gap, which also
tests the reported gap type.

Usage:
    python extract_bandgap_structures.py [--out-dir work/bandgap_structures]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

EXP = Path(__file__).resolve().parents[1]
REPO = EXP.parents[1]
MATERIALS = ["Si", "GaAs", "GaP", "ZnO", "Cu2O"]


def standard_primitive(structure: Structure) -> Structure:
    primitive = SpacegroupAnalyzer(structure, symprec=1e-3).get_primitive_standard_structure()
    return primitive if len(primitive) <= len(structure) else structure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=EXP / "work" / "bandgap_structures")
    args = parser.parse_args()

    for material in MATERIALS:
        source = Structure.from_file(REPO / "data" / "bandgap_24" / material / "POSCAR")
        cell = standard_primitive(source)
        dest = args.out_dir / material
        dest.mkdir(parents=True, exist_ok=True)
        cell.to(filename=str(dest / "POSCAR"), fmt="poscar")
        print(f"{material:5s} {cell.composition.reduced_formula:5s} natoms={len(cell)} "
              f"V={cell.volume:.4f} A^3 -> {dest / 'POSCAR'}")


if __name__ == "__main__":
    main()
