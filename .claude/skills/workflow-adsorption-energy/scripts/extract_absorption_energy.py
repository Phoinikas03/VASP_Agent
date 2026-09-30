#!/usr/bin/env python3
"""
Read E0 from the OSZICAR files of the three geometry-optimization directories and compute the adsorption energy:
  E_ads = E(adsorbed) - E(CO) - E(surface)
Default subdirectory names match the VaspAgent absorptionE example: CO / surface / absorbed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


def get_e0_from_oszicar(oszicar_path: Path) -> Optional[float]:
    if not oszicar_path.is_file():
        return None
    text = oszicar_path.read_text(errors="replace")
    e0_lines = [ln for ln in text.splitlines() if "E0=" in ln]
    if not e0_lines:
        return None
    last = e0_lines[-1]
    m = re.search(r"E0=\s*([-+0-9.Ee]+)", last)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def main() -> int:
    p = argparse.ArgumentParser(description="Compute adsorption energy from three OSZICAR files.")
    p.add_argument(
        "--base",
        type=Path,
        default=None,
        help="Working directory containing CO/surface/absorbed (subdirectories can be overridden with --co-dir, etc.)",
    )
    p.add_argument("--co-dir", type=Path, default=None, help="Gas-phase molecule calculation directory (containing OSZICAR)")
    p.add_argument("--surface-dir", type=Path, default=None, help="Surface calculation directory")
    p.add_argument("--adsorbed-dir", type=Path, default=None, help="Adsorption system calculation directory")
    p.add_argument(
        "--co-name",
        default="CO",
        help="Molecule subdirectory name in --base mode (default CO)",
    )
    p.add_argument(
        "--surface-name",
        default="surface",
        help="Surface subdirectory name in --base mode (default surface)",
    )
    p.add_argument(
        "--adsorbed-name",
        default="absorbed",
        help="Adsorption system subdirectory name in --base mode (default absorbed)",
    )
    args = p.parse_args()

    if args.base:
        base = args.base.resolve()
        co_dir = args.co_dir or (base / args.co_name)
        surf_dir = args.surface_dir or (base / args.surface_name)
        ads_dir = args.adsorbed_dir or (base / args.adsorbed_name)
    else:
        if not all([args.co_dir, args.surface_dir, args.adsorbed_dir]):
            print(
                "You must provide --base, or all of --co-dir --surface-dir --adsorbed-dir",
                file=sys.stderr,
            )
            return 2
        co_dir = args.co_dir.resolve()
        surf_dir = args.surface_dir.resolve()
        ads_dir = args.adsorbed_dir.resolve()

    e1 = get_e0_from_oszicar(co_dir / "OSZICAR")
    e2 = get_e0_from_oszicar(surf_dir / "OSZICAR")
    e3 = get_e0_from_oszicar(ads_dir / "OSZICAR")

    out: dict = {
        "E_CO_eV": e1,
        "E_surface_eV": e2,
        "E_adsorbed_eV": e3,
        "co_dir": str(co_dir),
        "surface_dir": str(surf_dir),
        "adsorbed_dir": str(ads_dir),
    }

    if e1 is None or e2 is None or e3 is None:
        out["absorption_energy_eV"] = None
        out["ok"] = False
        out["error"] = "missing_or_invalid_OSZICAR"
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 1

    e_ads = e3 - e1 - e2
    out["absorption_energy_eV"] = e_ads
    out["ok"] = True
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
