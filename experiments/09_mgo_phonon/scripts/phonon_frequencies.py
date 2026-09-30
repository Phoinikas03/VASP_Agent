#!/usr/bin/env python3
"""Recompute the MgO phonon results of Supplementary Section S5 from the
archived force constants.

Reads the files in ``results/phonon/`` (conventional cell, FORCE_CONSTANTS,
BORN, total_dos.dat), applies the non-analytical correction (NAC), and
writes

* ``results/gamma_frequencies.json``: Gamma-point TO/LO frequencies and the
  LO-TO splitting (manuscript Section 2.5; Supplementary Section S5),
* ``results/band_gxwgl.csv``: frequencies along Gamma-X-W-Gamma-L,
* ``results/figures/fig_S4_mgo_phonon_dispersion_dos.pdf``: Fig. S4.

No VASP or new phonopy force calculation is performed. Requires phonopy >= 4.

    python scripts/phonon_frequencies.py [--phonon-dir results/phonon] [--check]
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import phonopy
from phonopy import Phonopy
from phonopy.file_IO import parse_FORCE_CONSTANTS
from phonopy.interface.vasp import read_vasp

EXP_DIR = Path(__file__).resolve().parents[1]
CM_PER_THZ = 33.356          # conversion used in the original run
NAC_FACTOR = 14.399652       # phonopy unit-conversion factor for VASP BORN data
PRIMITIVE_MATRIX = [[0, .5, .5], [.5, 0, .5], [.5, .5, 0]]   # fcc primitive of the rock-salt cell
SUPERCELL = np.diag([2, 2, 2])                               # 8-atom cell -> 64-atom supercell
PATH = np.array([[0, 0, 0], [.5, .5, 0], [.5, .25, .75], [0, 0, 0], [.5, .5, .5]])
LABELS = ["Γ", "X", "W", "Γ", "L"]
POINTS_PER_SEGMENT = 101
# Values reported in the manuscript (cm^-1, one decimal).
EXPECTED = {"TO": 371.9, "LO": 680.7, "LO_TO_splitting": 308.8}


def read_born(path: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = [line.split() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    values = np.array(rows, dtype=float)
    return values[0].reshape(3, 3), values[1:].reshape(-1, 3, 3)


def build_phonopy(phonon_dir: Path) -> tuple[Phonopy, np.ndarray, np.ndarray]:
    ph = Phonopy(read_vasp(phonon_dir / "POSCAR-conv"),
                 supercell_matrix=SUPERCELL, primitive_matrix=PRIMITIVE_MATRIX)
    ph.force_constants = parse_FORCE_CONSTANTS(str(phonon_dir / "FORCE_CONSTANTS"))
    dielectric, born = read_born(phonon_dir / "BORN")
    ph.nac_params = {"born": born, "dielectric": dielectric, "factor": NAC_FACTOR,
                     "unit_conversion_factor": NAC_FACTOR, "primitive": ph.primitive,
                     "q_direction": None}
    return ph, dielectric, born


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--phonon-dir", type=Path, default=EXP_DIR / "results" / "phonon")
    ap.add_argument("--out-dir", type=Path, default=EXP_DIR / "results")
    ap.add_argument("--check", action="store_true",
                    help="Fail unless the Gamma frequencies match the manuscript values.")
    args = ap.parse_args()

    ph, dielectric, born = build_phonopy(args.phonon_dir)

    # At q = 0 the NAC term depends on the approach direction. Without a
    # direction phonopy returns TO = LO; the band path fixes the direction.
    gamma_no_dir = ph.run_qpoints([[0, 0, 0]]).frequencies[0] * CM_PER_THZ
    gamma_dir = ph.run_qpoints([[0, 0, 0]], nac_q_direction=[.5, .5, 0]).frequencies[0] * CM_PER_THZ

    paths = [np.linspace(a, b, POINTS_PER_SEGMENT) for a, b in zip(PATH[:-1], PATH[1:])]
    bs = ph.run_band_structure(paths, path_connections=[True, True, True, False], labels=LABELS)
    freq = np.array(bs.frequencies) * CM_PER_THZ          # (segment, q, branch)
    gamma_band = freq[0, 0]
    to, lo = float(gamma_band[3]), float(gamma_band[5])

    result = {
        "phonopy_version": phonopy.__version__,
        "relaxed_conventional_a_angstrom": float(ph.unitcell.cell[0, 0]),
        "supercell_atoms": len(ph.supercell),
        "epsilon_infinity_diagonal": np.diag(dielectric).tolist(),
        "born_charge_xx": [float(b[0, 0]) for b in born],
        "gamma_band_cm-1": gamma_band.tolist(),
        "gamma_qpoint_without_direction_cm-1": gamma_no_dir.tolist(),
        "gamma_qpoint_direction_110_cm-1": gamma_dir.tolist(),
        "TO_cm-1": to,
        "LO_cm-1": lo,
        "LO_TO_splitting_cm-1": lo - to,
        "path_minimum_cm-1": float(freq.min()),
        "path_sampled_qpoints": int(freq.shape[0] * freq.shape[1]),
        "note": "Gamma frequencies are taken from the first point of the Gamma-X band segment, "
                "which fixes the NAC approach direction.",
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "gamma_frequencies.json").write_text(json.dumps(result, indent=2) + "\n")

    with (args.out_dir / "band_gxwgl.csv").open("w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["segment", "point", "distance"] + [f"branch_{i}_cm-1" for i in range(1, 7)])
        offset = 0.0
        for si, (dist, f) in enumerate(zip(bs.distances, freq)):
            x = np.asarray(dist) - dist[0] + offset
            for qi, (xx, ff) in enumerate(zip(x, f)):
                writer.writerow([si, qi, f"{xx:.8f}"] + [f"{v:.6f}" for v in ff])
            offset = float(x[-1])

    plot(bs, freq, args.phonon_dir / "total_dos.dat", to, lo, args.out_dir / "figures")

    rounded = {"TO": round(to, 1), "LO": round(lo, 1), "LO_TO_splitting": round(lo - to, 1)}
    print(json.dumps({**rounded, "path_minimum_cm-1": result["path_minimum_cm-1"]}, indent=2))
    if args.check:
        assert rounded == EXPECTED, (rounded, EXPECTED)
        assert result["supercell_atoms"] == 64
        print("check passed: matches manuscript values", EXPECTED)


def plot(bs, freq, dos_file: Path, to: float, lo: float, fig_dir: Path) -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8.5, "pdf.fonttype": 42,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "xtick.direction": "in", "ytick.direction": "in"})
    fig = plt.figure(figsize=(7.2, 4.2))
    grid = fig.add_gridspec(1, 2, width_ratios=[3.35, 1], left=.105, right=.975,
                            top=.935, bottom=.14, wspace=.09)
    ax = fig.add_subplot(grid[0])
    ad = fig.add_subplot(grid[1], sharey=ax)
    ticks, offset = [0.0], 0.0
    for dist, f in zip(bs.distances, freq):
        x = np.asarray(dist) - dist[0] + offset
        ax.plot(x, f, color="#315E7C", lw=.9)
        offset = float(x[-1])
        ticks.append(offset)
    for t in ticks:
        ax.axvline(t, color="#C8C8C8", lw=.75, zorder=0)
    ax.axhline(0, color="#666666", lw=.7, zorder=0)
    ax.set(xticks=ticks, xticklabels=LABELS, xlim=(0, ticks[-1]), ylim=(-12, 745),
           ylabel=r"Frequency (cm$^{-1}$)", xlabel="Wave vector")
    ax.annotate(f"LO  {lo:.1f}", xy=(0, lo), xytext=(.13, 707), fontsize=8,
                arrowprops={"arrowstyle": "-", "lw": .7, "color": "#666666"})
    ax.annotate(f"TO  {to:.1f}", xy=(0, to), xytext=(.13, 345), fontsize=8,
                arrowprops={"arrowstyle": "-", "lw": .7, "color": "#666666"})
    ax.text(-.10, 1.055, "a", transform=ax.transAxes, va="top", fontsize=11, weight="bold")
    # total_dos.dat from the original run: frequency in THz, DOS normalized to its maximum.
    dos = np.loadtxt(dos_file)
    density = dos[:, 1] / dos[:, 1].max()
    ad.fill_betweenx(dos[:, 0] * CM_PER_THZ, 0, density, color="#EEEEEE")
    ad.plot(density, dos[:, 0] * CM_PER_THZ, color="#222222", lw=.8)
    ad.set(xlim=(0, 1.15), xticks=[0, .5, 1], xlabel="Normalized DOS")
    ad.tick_params(axis="y", labelleft=False, left=False)
    ad.spines["left"].set_visible(False)
    ad.text(.01, 1.055, "b", transform=ad.transAxes, va="top", fontsize=11, weight="bold")
    fig_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(fig_dir / "fig_S4_mgo_phonon_dispersion_dos.pdf", dpi=300, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
