"""Common equation-of-state evaluation for the Sol27LC experiments.

All lattice constants reported for experiments 06-08 are obtained with this
module, so that every method and run passes through identical fitting code:

* energy-volume points are read from OUTCAR files (last ``volume of cell`` and
  last ``energy(sigma->0)``);
* only normally terminated, electronically converged calculations are used;
* points that share a volume (rounded to 1e-4 A^3) are de-duplicated, keeping
  the last one in directory order;
* a third-order Birch-Murnaghan E(V) is fitted, and the conventional cubic
  lattice constant is obtained from the equilibrium volume.
"""
from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import curve_fit

ATOMS_PER_CONVENTIONAL = {"fcc": 4, "bcc": 2, "dia": 8}
EV_PER_A3_TO_GPA = 160.21766208


def read_text(path: Path) -> str:
    """Read a file, falling back to a gzipped copy."""
    path = Path(path)
    if path.exists():
        return path.read_text(errors="replace")
    gz = Path(str(path) + ".gz")
    if gz.exists():
        return gzip.open(gz, "rt", errors="replace").read()
    return ""


@dataclass
class OutcarPoint:
    volume: float | None
    energy: float | None
    finished: bool
    electronic_converged: bool
    encut: float | None
    elapsed_s: float | None


def outcar_point(outcar: Path) -> OutcarPoint:
    text = read_text(outcar)

    def last(pattern: str) -> float | None:
        found = re.findall(pattern, text)
        return float(found[-1]) if found else None

    return OutcarPoint(
        volume=last(r"volume of cell :\s+([\d.]+)"),
        energy=last(r"energy\(sigma->0\)\s*=\s*(-?[\d.]+)"),
        finished="General timing and accounting" in text,
        electronic_converged="aborting loop because EDIFF is reached" in text,
        encut=last(r"ENCUT\s*=\s*([\d.]+)"),
        elapsed_s=last(r"Elapsed time \(sec\):\s*([\d.]+)"),
    )


def birch_murnaghan(V, E0, V0, B0, B0p):
    eta = (V0 / V) ** (2.0 / 3.0)
    return E0 + 9.0 * V0 * B0 / 16.0 * ((eta - 1) ** 3 * B0p + (eta - 1) ** 2 * (6 - 4 * eta))


def fit_bm(volumes, energies) -> dict | None:
    V = np.asarray(volumes, float)
    E = np.asarray(energies, float)
    if len(V) < 4:
        return None
    order = np.argsort(V)
    V, E = V[order], E[order]
    guess = [E.min(), V[len(V) // 2], 1.0, 4.0]
    try:
        popt, _ = curve_fit(birch_murnaghan, V, E, p0=guess, maxfev=20000)
    except (RuntimeError, ValueError):
        return None
    resid = E - birch_murnaghan(V, *popt)
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((E - E.mean()) ** 2))
    return {
        "E0_eV": float(popt[0]),
        "V0_A3": float(popt[1]),
        "B0_GPa": float(popt[2] * EV_PER_A3_TO_GPA),
        "B0_prime": float(popt[3]),
        "R2": 1 - ss_res / ss_tot if ss_tot else float("nan"),
        "n_points": int(len(V)),
        "minimum_bracketed": bool(V.min() < popt[1] < V.max()),
    }


def conventional_a(V0: float, natoms: int, structure_type: str) -> float:
    """Conventional cubic lattice constant from the volume of an ``natoms`` cell."""
    return (V0 * ATOMS_PER_CONVENTIONAL[structure_type] / natoms) ** (1.0 / 3.0)


def poscar_natoms(poscar: Path) -> int:
    lines = Path(poscar).read_text().splitlines()
    return sum(int(x) for x in lines[6].split())


def select_points(calc_dirs) -> dict[float, OutcarPoint]:
    """Production points entering the fit, keyed by volume rounded to 1e-4 A^3."""
    selected: dict[float, OutcarPoint] = {}
    for d in calc_dirs:
        p = outcar_point(Path(d) / "OUTCAR")
        if p.volume is None or p.energy is None:
            continue
        if p.finished and p.electronic_converged:
            selected[round(p.volume, 4)] = p
    return selected


def agent_calc_dirs(workspace: Path) -> list[Path]:
    """EOS directories of a VASP Agent workspace: every ``scale_*`` directory,
    excluding convergence scans and hidden (archived) subtrees."""
    workspace = Path(workspace)
    return sorted(
        d
        for d in workspace.glob("**/scale_*")
        if d.is_dir()
        and "convergence" not in str(d.relative_to(workspace))
        and not any(part.startswith(".") for part in d.relative_to(workspace).parts)
    )


def atomate2_calc_dirs(workflow_dir: Path) -> list[Path]:
    """Static (NSW = 0) jobs of an atomate2 EOS workflow."""
    dirs = []
    for d in sorted(Path(workflow_dir).glob("job_*")):
        incar = read_text(d / "INCAR")
        m = re.search(r"^\s*NSW\s*=\s*([^\s;#!]+)", incar, re.M)
        if m is None or m.group(1) == "0":
            dirs.append(d)
    return dirs


def fit_lattice_constant(calc_dirs, natoms: int, structure_type: str) -> dict | None:
    points = select_points(calc_dirs)
    volumes = sorted(points)
    fit = fit_bm(volumes, [points[v].energy for v in volumes])
    if fit is None:
        return None
    fit["a_conv_A"] = conventional_a(fit["V0_A3"], natoms, structure_type)
    fit["encuts_eV"] = sorted({points[v].encut for v in volumes if points[v].encut is not None})
    return fit
