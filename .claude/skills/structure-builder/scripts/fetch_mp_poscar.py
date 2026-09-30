import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Fetch a Materials Project structure and write POSCAR.")
    parser.add_argument("--mp-id", required=True, help="Materials Project id, e.g. mp-126")
    parser.add_argument("--output", help="Output POSCAR path. Defaults to POSCAR_<mp-id>")
    args = parser.parse_args()

    api_key = os.getenv("MP_API")
    if not api_key:
        print("ERROR: MP_API environment variable is missing.", file=sys.stderr)
        return 1

    try:
        from mp_api.client import MPRester
    except ImportError:
        print("ERROR: mp-api is not installed.", file=sys.stderr)
        return 1

    output = Path(args.output or f"POSCAR_{args.mp_id}").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        with MPRester(api_key) as mpr:
            structure = mpr.get_structure_by_material_id(args.mp_id)
        if structure is None:
            print(f"ERROR: Structure not found for {args.mp_id}.", file=sys.stderr)
            return 1
        structure.to(fmt="poscar", filename=str(output))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"SUCCESS: wrote {output}")
    print(f"FORMULA: {structure.composition.reduced_formula}")
    print(f"ATOMS: {len(structure)}")
    print(
        "LATTICE_ABC: "
        f"{structure.lattice.a:.6f} {structure.lattice.b:.6f} {structure.lattice.c:.6f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
