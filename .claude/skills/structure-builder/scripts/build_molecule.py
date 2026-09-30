import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Build a gas-phase molecule POSCAR with ASE.")
    parser.add_argument("--molecule", required=True, help="ASE molecule name, e.g. CO, H2O, O2")
    parser.add_argument("--box", type=float, default=18.0, help="Cubic cell length in Angstrom")
    parser.add_argument("--output", default="POSCAR_molecule", help="Output POSCAR path")
    parser.add_argument("--sort", action="store_true", help="Sort atoms in VASP output")
    args = parser.parse_args()

    try:
        from ase.build import molecule
        from ase.io import write
    except ImportError:
        print("ERROR: ase is not installed. Install requirements.txt first.", file=sys.stderr)
        return 1

    atoms = molecule(args.molecule)
    atoms.set_cell([args.box, args.box, args.box])
    atoms.center()
    atoms.pbc = [True, True, True]

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    write(str(output), atoms, format="vasp", direct=True, vasp5=True, sort=args.sort)

    print(f"SUCCESS: wrote {output}")
    print(f"FORMULA: {atoms.get_chemical_formula()}")
    print(f"ATOMS: {len(atoms)}")
    print(f"BOX_A: {args.box:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
