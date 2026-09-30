import argparse
import sys
from pathlib import Path


def _surface_builder(surface_name):
    from ase.build import bcc100, bcc110, bcc111, fcc100, fcc110, fcc111, hcp0001

    builders = {
        "fcc100": fcc100,
        "fcc110": fcc110,
        "fcc111": fcc111,
        "bcc100": bcc100,
        "bcc110": bcc110,
        "bcc111": bcc111,
        "hcp0001": hcp0001,
    }
    if surface_name not in builders:
        raise ValueError(f"Unsupported surface '{surface_name}'. Choose one of: {', '.join(builders)}")
    return builders[surface_name]


def _fix_bottom_layers(atoms, n_layers):
    if n_layers <= 0:
        return
    from ase.constraints import FixAtoms

    z_values = atoms.positions[:, 2]
    unique_layers = sorted({round(float(z), 5) for z in z_values})
    fixed_layer_z = set(unique_layers[:n_layers])
    mask = [round(float(atom.position[2]), 5) in fixed_layer_z for atom in atoms]
    atoms.set_constraint(FixAtoms(mask=mask))


def main():
    parser = argparse.ArgumentParser(description="Build a standard metal surface slab with ASE.")
    parser.add_argument("--element", required=True, help="Element symbol, e.g. Pt")
    parser.add_argument("--surface", required=True, help="Surface builder: fcc111, fcc100, bcc110, hcp0001")
    parser.add_argument("--size", nargs=3, type=int, default=[2, 2, 4], metavar=("A", "B", "LAYERS"))
    parser.add_argument("--vacuum", type=float, default=15.0, help="Vacuum thickness in Angstrom")
    parser.add_argument("--output", default="POSCAR_surface", help="Output POSCAR path")
    parser.add_argument("--fix-bottom-layers", type=int, default=0, help="Add Selective Dynamics for bottom N layers")
    parser.add_argument("--sort", action="store_true", help="Sort atoms in VASP output")
    args = parser.parse_args()

    try:
        from ase.io import write
    except ImportError:
        print("ERROR: ase is not installed. Install requirements.txt first.", file=sys.stderr)
        return 1

    try:
        builder = _surface_builder(args.surface)
        atoms = builder(args.element, size=tuple(args.size), vacuum=args.vacuum)
        _fix_bottom_layers(atoms, args.fix_bottom_layers)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    write(str(output), atoms, format="vasp", direct=True, vasp5=True, sort=args.sort)

    print(f"SUCCESS: wrote {output}")
    print(f"FORMULA: {atoms.get_chemical_formula()}")
    print(f"ATOMS: {len(atoms)}")
    print(f"SURFACE: {args.element} {args.surface}")
    print(f"SIZE: {args.size[0]} {args.size[1]} {args.size[2]}")
    print(f"VACUUM_A: {args.vacuum:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
