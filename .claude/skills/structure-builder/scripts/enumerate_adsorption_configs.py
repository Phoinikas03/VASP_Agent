import argparse
import subprocess
import sys
from pathlib import Path


CONFIGS = [
    ("fcc_upright", "fcc", "upright"),
    ("fcc_tilted_x", "fcc", "tilted_x"),
    ("fcc_tilted_y", "fcc", "tilted_y"),
    ("ontop_upright", "ontop", "upright"),
    ("ontop_tilted_x", "ontop", "tilted_x"),
    ("ontop_tilted_y", "ontop", "tilted_y"),
]


def _run(cmd):
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return False
    sys.stdout.write(result.stdout)
    return True


def main():
    parser = argparse.ArgumentParser(description="Enumerate benchmark adsorption structures.")
    parser.add_argument("--system", default="co-pt111", choices=["co-pt111"])
    parser.add_argument("--output-dir", default="structures/co_pt111")
    parser.add_argument("--height", type=float, default=1.85)
    parser.add_argument("--vacuum", type=float, default=15.0)
    parser.add_argument("--layers", type=int, default=4)
    parser.add_argument("--fix-bottom-layers", type=int, default=2)
    args = parser.parse_args()

    base = Path(args.output_dir).resolve()
    script_dir = Path(__file__).resolve().parent
    base.mkdir(parents=True, exist_ok=True)

    molecule_dir = base / "CO"
    surface_dir = base / "surface"
    configs_dir = base / "configs"
    molecule_dir.mkdir(exist_ok=True)
    surface_dir.mkdir(exist_ok=True)
    configs_dir.mkdir(exist_ok=True)

    py = sys.executable

    ok = _run([
        py,
        str(script_dir / "build_molecule.py"),
        "--molecule",
        "CO",
        "--box",
        "18",
        "--output",
        str(molecule_dir / "POSCAR"),
    ])
    ok = ok and _run([
        py,
        str(script_dir / "build_surface.py"),
        "--element",
        "Pt",
        "--surface",
        "fcc111",
        "--size",
        "2",
        "2",
        str(args.layers),
        "--vacuum",
        str(args.vacuum),
        "--fix-bottom-layers",
        str(args.fix_bottom_layers),
        "--output",
        str(surface_dir / "POSCAR"),
    ])

    for name, site, orientation in CONFIGS:
        out_dir = configs_dir / name
        out_dir.mkdir(exist_ok=True)
        ok = ok and _run([
            py,
            str(script_dir / "build_adsorption.py"),
            "--element",
            "Pt",
            "--surface",
            "fcc111",
            "--size",
            "2",
            "2",
            str(args.layers),
            "--vacuum",
            str(args.vacuum),
            "--adsorbate",
            "CO",
            "--anchor-symbol",
            "C",
            "--site",
            site,
            "--height",
            str(args.height),
            "--orientation",
            orientation,
            "--fix-bottom-layers",
            str(args.fix_bottom_layers),
            "--output",
            str(out_dir / "POSCAR"),
        ])
        if not ok:
            return 1

    print(f"SUCCESS: generated CO/Pt(111) benchmark structures in {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
