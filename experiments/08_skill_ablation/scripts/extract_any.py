#!/usr/bin/env python
"""Recover an equilibrium lattice constant from a workspace of any shape.

Without a workflow skill each run chooses its own directory names (for
example eos/a_4.15, kmesh/k20, conv_encut/e500). This walks every directory that holds a finished OUTCAR,
keeps the ones that share the run's production settings, and fits the resulting
energy-volume curve with the same Birch-Murnaghan form used for the other arms.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import re

import numpy as np
from scipy.optimize import curve_fit

ATOMS_PER_CONVENTIONAL = {"fcc": 4, "dia": 8, "bcc": 2}


def birch_murnaghan(V, E0, V0, B0, B0p):
    eta = (V0 / V) ** (2.0 / 3.0)
    return E0 + 9.0 * V0 * B0 / 16.0 * ((eta - 1) ** 3 * B0p + (eta - 1) ** 2 * (6 - 4 * eta))


def read_text(path):
    if os.path.exists(path):
        return open(path, errors="replace").read()
    if os.path.exists(path + ".gz"):
        return gzip.open(path + ".gz", "rt", errors="replace").read()
    return ""


def scan(root):
    """Every finished single-point/relax directory, with its settings."""
    out = []
    for outcar in glob.glob(os.path.join(root, "**", "OUTCAR"), recursive=True):
        text = read_text(outcar)
        if "Total CPU time" not in text:
            continue  # unfinished
        vols = re.findall(r"volume of cell :\s+([\d.]+)", text)
        enes = re.findall(r"energy\(sigma->0\)\s*=\s*(-?[\d.]+)", text)
        if not (vols and enes):
            continue
        nions = re.search(r"NIONS\s*=\s*(\d+)", text)
        encut = re.search(r"ENCUT\s*=\s*([\d.]+)", text)
        kpts = re.search(r"NKPTS\s*=\s*(\d+)", text)
        ispin = re.search(r"ISPIN\s*=\s*(\d+)", text)
        ismear = re.search(r"ISMEAR\s*=\s*([-\d]+)", text)
        titel = re.search(r"TITEL\s*=\s*(\S+\s+\S+)", text)
        out.append({
            "mtime": os.path.getmtime(outcar),
            "dir": os.path.relpath(os.path.dirname(outcar), root),
            "volume": float(vols[-1]), "energy": float(enes[-1]),
            "natoms": int(nions.group(1)) if nions else None,
            "encut": float(encut.group(1)) if encut else None,
            "nkpts": int(kpts.group(1)) if kpts else None,
            "ispin": int(ispin.group(1)) if ispin else None,
            "ismear": int(ismear.group(1)) if ismear else None,
            "potcar": titel.group(1).split()[-1] if titel else None,
        })
    return out


def _fit_group(byvol):
    V = np.array(sorted(byvol))
    E = np.array([byvol[v]["energy"] for v in V])
    try:
        popt, _ = curve_fit(birch_murnaghan, V, E,
                            p0=[E.min(), V[len(V) // 2], 1.0, 4.0], maxfev=20000)
    except (RuntimeError, ValueError):
        return None
    resid = E - birch_murnaghan(V, *popt)
    ss_tot = float(np.sum((E - E.mean()) ** 2))
    v0 = float(popt[1])
    return {
        "V0": v0, "B0_GPa": float(popt[2] * 160.21766208),
        "R2": 1 - float(np.sum(resid ** 2)) / ss_tot if ss_tot else float("nan"),
        "n_points": len(V), "V": V, "popt": popt,
        "bracketed": bool(V.min() < v0 < V.max()),
    }


def best_curve(points):
    """Pick the run's production energy-volume set.

    Points are grouped by (natoms, POTCAR, ENCUT), not by k-point count: under
    KSPACING the k-point count varies with cell volume. Convergence tests sit at
    a single volume and drop out for having too few distinct volumes. A curve is
    usable only if the fitted minimum falls inside the sampled range and the fit
    is good (R^2 >= 0.99). Among usable curves the one with the most volumes
    wins, then the higher cutoff.
    """
    groups = {}
    for p in points:
        groups.setdefault((p["natoms"], p["potcar"], p["encut"]), []).append(p)

    scored = []
    for key, grp in groups.items():
        byvol = {round(p["volume"], 4): p for p in grp}
        if len(byvol) < 5:
            continue
        f = _fit_group(byvol)
        if f is None:
            continue
        usable = f["bracketed"] and f["R2"] >= 0.99
        scored.append((usable, f["n_points"], key[2] or 0, key, byvol, f))
    if not scored:
        return None
    scored.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    best = scored[0]
    return best[3], best[4], best[5]


def fit(root, structure_type):
    points = scan(root)
    chosen = best_curve(points)
    result = {"n_outcars": len(points)}
    if chosen is None:
        result["error"] = "no group with >=5 distinct volumes"
        return result
    (natoms, potcar, encut), byvol, f = chosen
    V = f["V"]
    sample = byvol[V[len(V) // 2]]
    result.update(
        V0=f["V0"], B0_GPa=f["B0_GPa"], R2=f["R2"], n_points=f["n_points"],
        bracketed=f["bracketed"], v_min=float(V.min()), v_max=float(V.max()),
        natoms=natoms, encut=encut, nkpts=sample["nkpts"],
        ispin=sample["ispin"], ismear=sample["ismear"], potcar=potcar,
        a_conv_A=(f["V0"] * ATOMS_PER_CONVENTIONAL[structure_type] / natoms) ** (1 / 3),
    )
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows = []
    for root in args.roots:
        name = os.path.basename(root.rstrip("/"))
        st = name.split("_")[-1]
        r = fit(root, st) if st in ATOMS_PER_CONVENTIONAL else {"error": f"unknown type {st}"}
        r["system"] = name
        rows.append(r)
    if args.json:
        print(json.dumps(rows, indent=2)); return
    hdr = f"{'system':<9} {'a_conv':>8} {'B0':>7} {'R2':>9} {'pts':>4} {'ENCUT':>6} {'nk':>5} {'ISPIN':>6} {'ISMEAR':>7} {'POTCAR':>8} {'nOUT':>5}"
    print(hdr); print("-" * len(hdr))
    for r in rows:
        if "a_conv_A" not in r:
            print(f"{r['system']:<9} {'-':>8}  {r.get('error','')[:50]}"); continue
        print(f"{r['system']:<9} {r['a_conv_A']:8.4f} {r['B0_GPa']:7.1f} {r['R2']:9.6f} {r['n_points']:>4} "
              f"{str(r['encut']):>6} {str(r['nkpts']):>5} {str(r['ispin']):>6} {str(r['ismear']):>7} "
              f"{str(r['potcar']):>8} {r['n_outcars']:>5}  {'' if r.get('bracketed') else 'UNBRACKETED'}")


if __name__ == "__main__":
    main()
