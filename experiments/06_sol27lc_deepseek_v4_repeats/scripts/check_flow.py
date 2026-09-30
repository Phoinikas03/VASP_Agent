#!/usr/bin/env python3
"""Flow-conformance check for an agent Sol27LC run.

The reference is the workflow the archived `runs/lc_*` sessions actually
followed: ENCUT scan + KSPACING scan -> Convergence_Report.md -> >=7 isotropic
volume points -> EOS fit -> report. A run that silently drops a stage is a
deviation, not a faster route, so each stage is reported separately.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

STAGES = ("encut_scan", "kspacing_scan", "convergence_report", "eos_points", "eos_fit", "final_report")


def outcar_count(root: str, pattern: str) -> int:
    return sum(
        1
        for d in glob.glob(os.path.join(root, pattern), recursive=True)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "OUTCAR"))
    )


def check(root: str) -> dict:
    res = {"run": os.path.basename(root.rstrip("/")), "stages": {}, "notes": []}

    res["stages"]["encut_scan"] = outcar_count(root, "**/e_*") or outcar_count(root, "**/encut*/*")
    res["stages"]["kspacing_scan"] = outcar_count(root, "**/k_*") or outcar_count(root, "**/kspacing*/*")
    res["stages"]["convergence_report"] = int(bool(glob.glob(os.path.join(root, "**", "Convergence_Report.md"), recursive=True)))
    res["stages"]["eos_points"] = outcar_count(root, "**/scale_*")

    fits = [
        f
        for f in glob.glob(os.path.join(root, "**", "*.json"), recursive=True)
        if re.search(r"eos", os.path.basename(f), re.I)
    ]
    a_eq = None
    for f in fits:
        try:
            data = json.load(open(f))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and ("V_0_Ang3" in data or "a_eq_A" in data):
            a_eq = data.get("a_eq_A", a_eq)
            res["stages"]["eos_fit"] = 1
            res["eos_file"] = os.path.relpath(f, root)
            break
    res["stages"].setdefault("eos_fit", 0)
    res["a_eq_A"] = a_eq

    reports = [
        f for f in glob.glob(os.path.join(root, "**", "*.md"), recursive=True)
        if re.search(r"lattice|eos", os.path.basename(f), re.I)
        and not re.search(r"convergence", os.path.basename(f), re.I)
    ]
    res["stages"]["final_report"] = int(bool(reports))

    skipped = glob.glob(os.path.join(root, "**", "CONVERGENCE_SKIPPED.md"), recursive=True)
    if skipped:
        res["notes"].append("convergence declared skipped (CONVERGENCE_SKIPPED.md present)")

    if not res["stages"]["encut_scan"] and not res["stages"]["kspacing_scan"] and not skipped:
        res["notes"].append("DEVIATION: no convergence scan and no skip record")
    if 0 < res["stages"]["eos_points"] < 5:
        res["notes"].append(f"DEVIATION: only {res['stages']['eos_points']} volume points with OUTCAR")

    done = res["stages"]["eos_fit"] and res["stages"]["eos_points"] >= 5
    conformant = done and (res["stages"]["encut_scan"] or skipped)
    res["verdict"] = "conformant" if conformant else ("done_with_deviation" if done else "in_progress")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = [check(r) for r in args.roots if os.path.isdir(r)]
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    hdr = f"{'run':<10} {'encut':>5} {'kspc':>5} {'rep':>3} {'eos':>4} {'fit':>3} {'rpt':>3}  {'a_eq':>8}  verdict"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        st = r["stages"]
        a = f"{r['a_eq_A']:.4f}" if isinstance(r.get("a_eq_A"), (int, float)) else "-"
        print(f"{r['run']:<10} {st['encut_scan']:>5} {st['kspacing_scan']:>5} "
              f"{st['convergence_report']:>3} {st['eos_points']:>4} {st['eos_fit']:>3} "
              f"{st['final_report']:>3}  {a:>8}  {r['verdict']}")
        for n in r["notes"]:
            print(f"    ! {n}")


if __name__ == "__main__":
    main()
