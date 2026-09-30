import argparse
import sys
from pathlib import Path


def _min_distance(structure):
    min_d = None
    for i, site in enumerate(structure):
        for j in range(i + 1, len(structure)):
            d = float(site.distance(structure[j]))
            if min_d is None or d < min_d:
                min_d = d
    return min_d


def _min_distance_between(structure, group_a, group_b):
    group_a = set(group_a)
    group_b = set(group_b)
    indices_a = [i for i, site in enumerate(structure) if site.specie.symbol in group_a]
    indices_b = [i for i, site in enumerate(structure) if site.specie.symbol in group_b]
    if not indices_a or not indices_b:
        return None

    min_d = None
    for i in indices_a:
        for j in indices_b:
            d = float(structure[i].distance(structure[j]))
            if min_d is None or d < min_d:
                min_d = d
    return min_d


def main():
    parser = argparse.ArgumentParser(description="Validate a POSCAR-like structure.")
    parser.add_argument("--input", default="POSCAR", help="Input structure path")
    parser.add_argument("--min-distance", type=float, default=0.65, help="Minimum allowed distance in Angstrom")
    parser.add_argument("--min-vacuum", type=float, default=0.0, help="Warn if c span vacuum is less than this")
    parser.add_argument("--slab-elements", nargs="+", help="Slab element symbols for adsorbate-slab checks")
    parser.add_argument("--adsorbate-elements", nargs="+", help="Adsorbate element symbols for adsorbate-slab checks")
    parser.add_argument(
        "--min-adsorbate-slab-distance",
        type=float,
        default=1.4,
        help="Minimum allowed distance between slab and adsorbate atoms when both element groups are provided",
    )
    args = parser.parse_args()

    if bool(args.slab_elements) != bool(args.adsorbate_elements):
        parser.error("--slab-elements and --adsorbate-elements must be provided together")

    try:
        from pymatgen.core import Structure
    except ImportError:
        print("ERROR: pymatgen is not installed.", file=sys.stderr)
        return 1

    path = Path(args.input).resolve()
    if not path.exists():
        print(f"ERROR: input not found: {path}", file=sys.stderr)
        return 1

    try:
        structure = Structure.from_file(str(path))
    except Exception as exc:
        print(f"ERROR: failed to parse {path}: {exc}", file=sys.stderr)
        return 1

    if len(structure) < 2:
        min_d = None
    else:
        min_d = _min_distance(structure)
    adsorbate_slab_min_d = None
    if args.slab_elements and args.adsorbate_elements:
        adsorbate_slab_min_d = _min_distance_between(
            structure,
            args.slab_elements,
            args.adsorbate_elements,
        )

    z_coords = [float(site.coords[2]) for site in structure]
    atom_span_z = max(z_coords) - min(z_coords) if z_coords else 0.0
    vacuum_estimate = float(structure.lattice.c) - atom_span_z

    ok = True
    warnings = []
    if min_d is not None and min_d < args.min_distance:
        ok = False
        warnings.append(f"minimum distance {min_d:.4f} A is below threshold {args.min_distance:.4f} A")
    if adsorbate_slab_min_d is not None and adsorbate_slab_min_d < args.min_adsorbate_slab_distance:
        ok = False
        warnings.append(
            "adsorbate-slab minimum distance "
            f"{adsorbate_slab_min_d:.4f} A is below threshold {args.min_adsorbate_slab_distance:.4f} A"
        )
    if args.min_vacuum > 0 and vacuum_estimate < args.min_vacuum:
        warnings.append(f"estimated z vacuum {vacuum_estimate:.4f} A is below requested {args.min_vacuum:.4f} A")

    print(f"INPUT: {path}")
    print(f"FORMULA: {structure.composition.reduced_formula}")
    print(f"ATOMS: {len(structure)}")
    print(
        "LATTICE_ABC: "
        f"{structure.lattice.a:.6f} {structure.lattice.b:.6f} {structure.lattice.c:.6f}"
    )
    if min_d is not None:
        print(f"MIN_DISTANCE_A: {min_d:.6f}")
    if adsorbate_slab_min_d is not None:
        print(f"ADSORBATE_SLAB_MIN_DISTANCE_A: {adsorbate_slab_min_d:.6f}")
    print(f"ESTIMATED_Z_VACUUM_A: {vacuum_estimate:.6f}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(f"STATUS: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
