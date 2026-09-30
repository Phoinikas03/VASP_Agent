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


def _fix_bottom_layers(atoms, slab_element, n_layers):
    if n_layers <= 0:
        return
    from ase.constraints import FixAtoms

    slab_indices = [i for i, atom in enumerate(atoms) if atom.symbol == slab_element]
    z_values = [atoms[i].position[2] for i in slab_indices]
    unique_layers = sorted({round(float(z), 5) for z in z_values})
    fixed_layer_z = set(unique_layers[:n_layers])
    mask = [
        atom.symbol == slab_element and round(float(atom.position[2]), 5) in fixed_layer_z
        for atom in atoms
    ]
    atoms.set_constraint(FixAtoms(mask=mask))


def _resolve_anchor_index(atoms, adsorbate_name, mol_index, anchor_symbol):
    if mol_index is not None and anchor_symbol is not None:
        raise ValueError("Use only one of --mol-index or --anchor-symbol.")
    if mol_index is not None:
        if mol_index < 0 or mol_index >= len(atoms):
            raise ValueError(f"--mol-index {mol_index} is outside adsorbate atom range 0..{len(atoms) - 1}.")
        return mol_index

    symbols = atoms.get_chemical_symbols()
    if anchor_symbol is None and adsorbate_name.upper() == "CO":
        anchor_symbol = "C"
    if anchor_symbol is not None:
        for i, symbol in enumerate(symbols):
            if symbol == anchor_symbol:
                return i
        raise ValueError(f"Anchor symbol '{anchor_symbol}' not found in adsorbate symbols {symbols}.")

    return 0


def _orient_adsorbate(atoms, orientation, anchor_index):
    center = atoms.positions[anchor_index]
    if orientation == "upright":
        return atoms
    if orientation == "tilted_x":
        atoms.rotate(45.0, "y", center=center, rotate_cell=False)
        return atoms
    if orientation == "tilted_y":
        atoms.rotate(45.0, "x", center=center, rotate_cell=False)
        return atoms
    if orientation == "reverse":
        atoms.rotate(180.0, "x", center=center, rotate_cell=False)
        return atoms
    raise ValueError("orientation must be one of: upright, tilted_x, tilted_y, reverse")


def main():
    parser = argparse.ArgumentParser(description="Build a slab + adsorbate POSCAR with ASE.")
    parser.add_argument("--element", required=True, help="Slab element, e.g. Pt")
    parser.add_argument("--surface", required=True, help="Surface builder: fcc111, fcc100, bcc110, hcp0001")
    parser.add_argument("--size", nargs=3, type=int, default=[2, 2, 4], metavar=("A", "B", "LAYERS"))
    parser.add_argument("--vacuum", type=float, default=15.0, help="Vacuum thickness in Angstrom")
    parser.add_argument("--adsorbate", required=True, help="ASE molecule name, e.g. CO")
    parser.add_argument("--site", required=True, help="Adsorption site, e.g. ontop, bridge, fcc, hcp")
    parser.add_argument("--height", type=float, default=1.85, help="Adsorption height in Angstrom")
    parser.add_argument("--orientation", default="upright", help="upright, tilted_x, tilted_y, or reverse")
    parser.add_argument(
        "--mol-index",
        type=int,
        default=None,
        help="Adsorbate atom index placed at the site. Defaults to the C atom for CO, otherwise atom 0.",
    )
    parser.add_argument(
        "--anchor-symbol",
        help="Adsorbate element symbol placed at the site, e.g. C for C-down CO. Mutually exclusive with --mol-index.",
    )
    parser.add_argument("--fix-bottom-layers", type=int, default=0, help="Add Selective Dynamics for bottom N slab layers")
    parser.add_argument("--output", default="POSCAR_adsorbed", help="Output POSCAR path")
    parser.add_argument("--sort", action="store_true", help="Sort atoms in VASP output")
    args = parser.parse_args()

    try:
        from ase.build import add_adsorbate, molecule
        from ase.io import write
    except ImportError:
        print("ERROR: ase is not installed. Install requirements.txt first.", file=sys.stderr)
        return 1

    try:
        slab = _surface_builder(args.surface)(args.element, size=tuple(args.size), vacuum=args.vacuum)
        ads = molecule(args.adsorbate)
        anchor_index = _resolve_anchor_index(ads, args.adsorbate, args.mol_index, args.anchor_symbol)
        ads = _orient_adsorbate(ads, args.orientation, anchor_index)
        add_adsorbate(slab, ads, height=args.height, position=args.site, mol_index=anchor_index)
        _fix_bottom_layers(slab, args.element, args.fix_bottom_layers)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    write(str(output), slab, format="vasp", direct=True, vasp5=True, sort=args.sort)

    print(f"SUCCESS: wrote {output}")
    print(f"FORMULA: {slab.get_chemical_formula()}")
    print(f"ATOMS: {len(slab)}")
    print(f"SITE: {args.site}")
    print(f"ORIENTATION: {args.orientation}")
    print(f"ANCHOR_INDEX: {anchor_index}")
    print(f"ANCHOR_SYMBOL: {ads[anchor_index].symbol}")
    print(f"HEIGHT_A: {args.height:.6f}")
    print(f"FIX_BOTTOM_LAYERS: {args.fix_bottom_layers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
