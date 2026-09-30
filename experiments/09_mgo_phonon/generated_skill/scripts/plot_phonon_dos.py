#!/usr/bin/env python3
"""Plot phonon dispersion + total DOS from phonopy-generated FORCE_CONSTANTS
and a BORN file (NAC / LO-TO splitting), using the phonopy v4 Python API.

This is a robust, generalized version of the script that worked for the MgO
rocksalt phonon run. It demonstrates the v4-only API and the correct hand-rolled
NAC setup (avoiding removed load_force_constants / set_force_constants and the
unreliable read_BORN / parse_BORN helpers).

Prereqs (in /path/to/fc):
  - FORCE_CONSTANTS   (from `phonopy --writefc --fc-spg-symmetry`)
  - BORN              (from `phonopy-vasp-born > BORN`; optional, skip NAC if absent)
  - ../disp/POSCAR-unitcell / POSCAR-conv  -> pass via --unitcell
  - 2x2x2 supercell passed as --dim 2 2 2  -> build supercell_matrix from it

Usage:
  python plot_phonon_dos.py --unitcell ../disp/POSCAR-conv \
       --fc-dir . --dim 2 2 2 \
       --primitive "[[0,.5,.5],[.5,0,.5],[.5,.5,0]]" \
       --bands "G,X,W,G,L" --out MgO_phonon_dispersion_DOS
"""
import argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from phonopy import Phonopy
from phonopy.interface.vasp import read_vasp
from phonopy.file_IO import parse_FORCE_CONSTANTS

from get_born_params import build_nac_params

CM_PER_THZ = 33.356

# Standard high-symmetry path templates (reduced coordinates). The MgO/rocksalt
# path is Gamma-X-W-Gamma-L. Custom paths can be passed via --bands.
SEGMENTS = {
    "G,X,W,G,L": ([0, 0, 0], [.5, .5, 0], [.5, .25, .75], [0, 0, 0], [.5, .5, .5]),
    "G,X,M,G,R": ([0, 0, 0], [.5, .5, 0], [.5, .5, .5], [0, 0, 0], [.5, .5, .5]),
    "G,L,X,W,K": ([0, 0, 0], [.5, .5, .5], [.5, .5, 0], [.5, .25, .75], [.375, .375, .75]),
}


def parse_primitive_matrix(s):
    return np.array(eval(s), dtype=float)  # noqa: S307 - trusted input


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unitcell", required=True, help="unitcell POSCAR (conventional)")
    ap.add_argument("--fc-dir", default=".", help="dir containing FORCE_CONSTANTS + BORN")
    ap.add_argument("--dim", nargs=3, type=int, default=[2, 2, 2],
                    help="supercell matrix diagonal")
    ap.add_argument("--primitive", default="[[0,.5,.5],[.5,0,.5],[.5,.5,0]]",
                    help="primitive matrix (list-of-lists)")
    ap.add_argument("--bands", default="G,X,W,G,L", help="band path key")
    ap.add_argument("--mesh", nargs=3, type=int, default=[31, 31, 31],
                    help="DOS mesh")
    ap.add_argument("--out", default="phonon_dispersion_DOS", help="output basename")
    ap.add_argument("--freq-max", type=float, default=22.0, help="DOS freq max (THz)")
    ap.add_argument("--cm", action="store_true", help="plot in cm^-1 (default THz)")
    args = ap.parse_args()

    unitcell = read_vasp(args.unitcell)
    pm = parse_primitive_matrix(args.primitive)
    ph = Phonopy(unitcell, supercell_matrix=np.diag(args.dim), primitive_matrix=pm)
    fc = parse_FORCE_CONSTANTS(f"{args.fc_dir}/FORCE_CONSTANTS")
    ph.force_constants = fc

    # NAC: only if a BORN file exists (ionic crystals). Non-polar -> no BORN.
    nac = build_nac_params(ph, filename=f"{args.fc_dir}/BORN")
    if nac:
        ph.nac_params = nac
        print("NAC enabled: born Mg=%s O=%s dielec=%s" % (
            np.round(nac["born"][0][0, 0], 4), np.round(nac["born"][-1][0, 0], 4),
            np.round(np.diag(nac["dielectric"]), 4)))
    else:
        print("No BORN file -> NAC disabled (no LO-TO splitting)")

    # Band structure
    key = args.bands
    if key not in SEGMENTS:
        raise SystemExit(f"unknown --bands key '{key}'; pick from {list(SEGMENTS)}")
    pts = [np.array(p, float) for p in SEGMENTS[key]]
    segments = list(zip(pts[:-1], pts[1:]))
    band_paths = [np.linspace(p0, p1, 101) for p0, p1 in segments]
    labels = [lab for lab in key.split(",")]
    # path_connections: junction between segment i and i+1 is connected when the
    # end of i equals start of i+1 (True). Last segment has no next.
    path_connections = [True] * (len(segments) - 1) + [False]
    bs = ph.run_band_structure(band_paths, with_eigenvectors=False,
                               labels=labels, path_connections=path_connections)
    dist_arrays = bs.distances
    freq_arrays = bs.frequencies

    # Total DOS
    ph.run_mesh(args.mesh, with_eigenvectors=False, is_mesh_symmetry=True)
    dos = ph.run_total_dos(freq_min=-0.3, freq_max=args.freq_max, freq_pitch=0.05,
                           use_tetrahedron_method=True)
    dos_freq = dos.frequency_points
    dos_val = dos.dos

    unit = CM_PER_THZ if args.cm else 1.0
    ylab = "Frequency (cm$^{-1}$)" if args.cm else "Frequency (THz)"

    fig = plt.figure(figsize=(9, 7.5))
    gs = fig.add_gridspec(1, 2, width_ratios=[2.3, 1.0], wspace=0.03)
    ax = fig.add_subplot(gs[0])
    axd = fig.add_subplot(gs[1], sharey=ax)

    xoff, tick_pos, tick_lab = 0.0, [], []
    for ci, (dist, freq) in enumerate(zip(dist_arrays, freq_arrays)):
        x = dist + xoff
        ax.plot(x, freq * unit, "b-", lw=1.4,
                label="DFT" if ci == 0 else None)
        tick_pos.append(xoff)
        tick_lab.append(labels[ci] if ci < len(labels) else "")
        xoff = x[-1]
    tick_pos.append(xoff)
    tick_lab.append(labels[-1])

    ax.set_ylabel(ylab, fontsize=12)
    ax.set_xlabel("Wave vector", fontsize=12)
    ax.set_xlim(-0.05, xoff + 0.05)
    ymax = 1.1 * np.max([np.max(f * unit) for f in freq_arrays if len(f)])
    ax.set_ylim(-0.05 * ymax if not args.cm else -40, ymax)
    for t in tick_pos:
        ax.axvline(t, color="k", lw=0.6)
    ax.set_xticks(tick_pos)
    ax.set_xticklabels(tick_lab, fontsize=12)
    ax.legend(fontsize=10, loc="upper right")
    ax.set_title("Phonon dispersion + DOS", fontsize=13)

    axd.plot(dos_val, dos_freq * unit, "r-", lw=1.5)
    axd.set_xlabel("DOS", fontsize=12)
    axd.set_ylim(-0.05 * ymax if not args.cm else -40, ymax)
    axd.set_xlim(0, None)
    axd.set_xticks([])
    axd.get_yaxis().set_visible(False)
    axd.axhline(0, color="k", lw=0.5)

    fig.savefig(f"{args.out}.png", dpi=200, bbox_inches="tight")
    fig.savefig(f"{args.out}.pdf")
    print("Saved", args.out, ".png/.pdf")

    # Report key frequencies on the first segment (first q = 0)
    g0 = np.array(freq_arrays[0][0])
    print("first-q bands (%s):" % ("cm-1" if args.cm else "THz"),
          [round(float(f) * unit, 1) for f in g0])
    # For rocksalt Gamma: 3 acoustic ~0, then TO,TO,LO (indices 3,4,5)
    if len(g0) >= 6:
        print("TO:", round(float(g0[3]) * unit, 1),
              "LO:", round(float(g0[5]) * unit, 1),
              "LO-TO split:", round(float(g0[5] - g0[3]) * unit, 1))


if __name__ == "__main__":
    main()
