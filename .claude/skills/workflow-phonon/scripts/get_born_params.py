#!/usr/bin/env python3
"""Build phonopy nac_params dict by manually parsing a BORN file (v4-safe).

Phonopy v4 removed `read_BORN` and `parse_BORN` behaves differently from what
you'd expect (its returned NacParams object is missing the `factor` key).
The most robust way to enable NAC (LO-TO splitting) is to parse the BORN file
yourself and build the nac_params dict phonopy's API expects.

BORN file format (from `phonopy-vasp-born`):
  line 0               : comment ('# epsilon and Z* of atoms ...')
  line 1               : 9 numbers = dielectric tensor (3x3)
  lines 2..(1+natom)   : 9 numbers each = Born charge tensor for each atom (3x3)

Usage:
  from get_born_params import build_nac_params
  nac = build_nac_params(ph)            # ph = Phonopy object (needs ph.primitive)
  if nac: ph.nac_params = nac
"""
import numpy as np

# phonopy unit conversion factor for Born charges / dielectric with THz units
FACTOR = 14.399652


def read_born_file(filename="BORN"):
    """Return (dielectric 3x3, born_array natom x 3x3) parsed from BORN file."""
    lines = []
    with open(filename) as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            lines.append(s)
    if len(lines) < 2:
        raise ValueError(f"BORN file {filename} too short / malformed")
    dielectric = np.array([float(x) for x in lines[0].split()]).reshape(3, 3)
    natom = len(lines) - 1
    born = np.zeros((natom, 3, 3))
    for i in range(natom):
        vals = [float(x) for x in lines[i + 1].split()]
        if len(vals) < 9:
            raise ValueError(f"BORN row {i + 1} has {len(vals)} values, expected 9")
        born[i] = np.array(vals[:9]).reshape(3, 3)
    return dielectric, born


def build_nac_params(ph, filename="BORN", factor=FACTOR):
    """Build a nac_params dict for `ph.nac_params` from a BORN file.

    `ph` must be a phonopy Phonopy object already built with the matching
    primitive cell (ph.primitive has the same atom ordering as BORN).
    Returns a dict or None if the BORN file is missing.
    """
    import os
    if not os.path.exists(filename):
        return None
    dielectric, born = read_born_file(filename)
    return {
        "born": born,
        "dielectric": dielectric,
        "factor": factor,
        "unit_conversion_factor": factor,
        "primitive": ph.primitive,
        "q_direction": None,
    }


if __name__ == "__main__":
    # Self-test: print parsed contents
    import sys
    d, b = read_born_file(sys.argv[1] if len(sys.argv) > 1 else "BORN")
    print("dielectric diag:", np.round(np.diag(d), 4))
    print("born shape:", b.shape)
    print("born Mg:", np.round(b[0][0, 0], 4), "born O:", np.round(b[-1][0, 0], 4))
