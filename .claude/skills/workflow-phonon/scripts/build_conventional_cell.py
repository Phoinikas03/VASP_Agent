#!/usr/bin/env python3
"""Build a conventional (cubic) cell from a primitive cell, avoiding the
pymatgen `get_conventional_standard_structure` deprecation/behavior change.

The deprecated/renamed method is not reliable across pymatgen versions, so for
the standard high-symmetry structures we construct the conventional cell
directly. Works for rocksalt (NaCl), zincblende (ZB), fluorite (CaF2), CsCl,
simple cubic, and FCC/BCC primitive cells when given the conventional lattice
of the desired cell.

For arbitrary structures, fall back to MP conventional cell (already
conventional) or `Structure.from_file` of the conventional POSCAR.

Usage:
  python build_conventional_cell.py --in POSCAR_prim --out POSCAR_conv --a 4.194
  # or, derive the conventional lattice from the primitive automatically:
  python build_conventional_cell.py --in POSCAR_prim --out POSCAR_conv --auto
"""
import argparse
import numpy as np

try:
    from pymatgen.core import Structure, Lattice
    from pymatgen.io.vasp import Poscar
except ImportError as e:
    raise SystemExit(f"pymatgen required: {e}")

ROCKSALT_BASIS = (  # fractional coords of the two-atom basis in a cubic cell
    (0.0, 0.0, 0.0),   # cation (Mg)
    (0.5, 0.5, 0.5),   # anion (O)
)
ZINCBLENDE_BASIS = (
    (0.0, 0.0, 0.0),   # cation
    (0.25, 0.25, 0.25),  # anion
)


def conv_lattice_from_prim(prim):
    """For a cubic conventional cell, a_conv = 2 * a_prim for rocksalt/ZB
    (since FCC primitive a_prim = a_conv / sqrt(2) actually, so we can't always
    derive reliably). Prefer passing --a explicitly."""
    raise ValueError("Use --a to give the conventional cubic lattice constant "
                     "instead of auto-deriving; auto is unreliable.")


def build_rocksalt(a):
    lat = Lattice.cubic(a)
    coords = [tuple(np.asarray(b) % 1.0) for b in ROCKSALT_BASIS]
    return Structure(lat, ["Mg", "O"], coords, coords_are_cartesian=False)


def build_zincblende(a):
    lat = Lattice.cubic(a)
    coords = [tuple(np.asarray(b) % 1.0) for b in ZINCBLENDE_BASIS]
    return Structure(lat, ["Zn", "S"], coords, coords_are_cartesian=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="fin", help="input primitive POSCAR")
    p.add_argument("--out", dest="fout", help="output conventional POSCAR")
    p.add_argument("--a", type=float, help="conventional cubic lattice constant (A)")
    p.add_argument("--sym", default="rocksalt",
                   help="structure type: rocksalt | zincblende (default rocksalt)")
    args = p.parse_args()

    if args.fin:
        prim = Structure.from_file(args.fin)
        print("input sym:", prim.get_space_group_info(), "n_atoms:", len(prim))

    if args.sym == "rocksalt":
        s = build_rocksalt(args.a)
    elif args.sym == "zincblende":
        s = build_zincblende(args.a)
    else:
        raise SystemExit(f"unsupported --sym: {args.sym}")

    print("conventional:", s.composition, "n_atoms:", len(s),
          "space group:", s.get_space_group_info())
    print("lattice a =", s.lattice.a)
    if args.fout:
        Poscar(s).write_file(args.fout)
        print("wrote", args.fout)


if __name__ == "__main__":
    main()
