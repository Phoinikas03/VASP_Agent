#!/usr/bin/env python3
"""Extract high-symmetry-point phonon frequencies (with NAC, from band
structure) and total-DOS peaks, for experiment comparison / reporting.

Uses the SAME v4 Phonopy API as plot_phonon_dos.py and the SAME hand-rolled
NAC. Unlike `run_qpoints`, band structure properly applies the non-analytic
correction at q=0, so the reported LO-TO splitting is physical.

Usage (run in the fc dir containing FORCE_CONSTANTS, BORN):
  python extract_phonon_report.py --unitcell ../disp/POSCAR-conv \
       --dim 2 2 2 --primitive "[[0,.5,.5],[.5,0,.5],[.5,.5,0]]" \
       --bands "G,X,W,G,L"
"""
import argparse
import numpy as np
from phonopy import Phonopy
from phonopy.interface.vasp import read_vasp
from phonopy.file_IO import parse_FORCE_CONSTANTS

from get_born_params import build_nac_params

CM_PER_THZ = 33.356
SEGMENTS = {
    "G,X,W,G,L": ([0, 0, 0], [.5, .5, 0], [.5, .25, .75], [0, 0, 0], [.5, .5, .5]),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unitcell", required=True)
    ap.add_argument("--dim", nargs=3, type=int, default=[2, 2, 2])
    ap.add_argument("--primitive", default="[[0,.5,.5],[.5,0,.5],[.5,.5,0]]")
    ap.add_argument("--bands", default="G,X,W,G,L")
    ap.add_argument("--mesh", nargs=3, type=int, default=[31, 31, 31])
    args = ap.parse_args()

    unitcell = read_vasp(args.unitcell)
    ph = Phonopy(unitcell, supercell_matrix=np.diag(args.dim),
                 primitive_matrix=np.array(eval(args.primitive), float))
    ph.force_constants = parse_FORCE_CONSTANTS("FORCE_CONSTANTS")
    nac = build_nac_params(ph, filename="BORN")
    if nac:
        ph.nac_params = nac

    # Band structure frequencies at the labelled points
    pts = [np.array(p, float) for p in SEGMENTS[args.bands]]
    segments = list(zip(pts[:-1], pts[1:]))
    band_paths = [np.linspace(p0, p1, 101) for p0, p1 in segments]
    labels = [lab for lab in args.bands.split(",")]
    bs = ph.run_band_structure(band_paths, with_eigenvectors=False,
                               labels=labels,
                               path_connections=[True] * (len(segments) - 1) + [False])
    freq_arrays = bs.frequencies
    labels = bs.labels

    print("=== High-symmetry point frequencies (cm^-1), NAC applied ===")
    # Each segment's first q is the segment's starting label point.
    for ci, (freq, lab) in enumerate(zip(freq_arrays, labels)):
        g = np.array(freq[0])
        print(f"{lab:>3s} (cm-1):", [round(float(f) * CM_PER_THZ, 1) for f in g])

    # LO-TO at Gamma (first segment, first q)
    g0 = np.array(freq_arrays[0][0])
    if len(g0) >= 6:
        print("Gamma TO:", round(float(g0[3]) * CM_PER_THZ, 1),
              "Gamma LO:", round(float(g0[5]) * CM_PER_THZ, 1),
              "LO-TO split:", round(float(g0[5] - g0[3]) * CM_PER_THZ, 1))

    # Total DOS peaks
    ph.run_mesh(args.mesh, with_eigenvectors=False, is_mesh_symmetry=True)
    dos = ph.run_total_dos(freq_min=-0.3, freq_max=22.0, freq_pitch=0.05,
                           use_tetrahedron_method=True)
    f = dos.frequency_points
    d = dos.dos
    # simple peak detection
    peaks = []
    for i in range(1, len(d) - 1):
        if d[i] > d[i - 1] and d[i] >= d[i + 1] and d[i] > 0.2 * d.max():
            peaks.append((f[i] * CM_PER_THZ, d[i]))
    # dedupe close peaks
    dedup = []
    for pv, dv in peaks:
        if not dedup or abs(pv - dedup[-1][0]) > 8:
            dedup.append((pv, dv))
    print("=== Total DOS peaks (cm^-1) ===")
    for pv, dv in dedup[:8]:
        print(f"  {pv:8.1f} cm^-1  (DOS={dv:.3f})")


if __name__ == "__main__":
    main()
