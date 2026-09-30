#!/usr/bin/env python3
"""Summarize the LiFePO4 recovery case (Fig. S1, Table S6) from raw VASP outputs.

The VASP Agent run directory keeps the abandoned first attempt in
``intermediate_<timestamp>/`` and the accepted, restarted relaxation at the top
level. For each attempt this script lists, per ionic step, the number of
electronic iterations and whether the NELM limit was reached, reports whether
OUTCAR contains "reached required accuracy", and prints the INCAR tags that
changed between the two attempts.

Expected raw layout (``--raw-dir``):
    lifepo4_vasp_agent/{INCAR,OSZICAR,OUTCAR}
    lifepo4_vasp_agent/intermediate_<timestamp>/{INCAR,OSZICAR,OUTCAR}
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parents[1]


def read_incar(path: Path) -> dict[str, str]:
    tags = {}
    for line in path.read_text().splitlines():
        line = line.split("#")[0].split("!")[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            tags[key.strip().upper()] = value.strip()
    return tags


def ionic_steps(oszicar: Path) -> list[tuple[int, int, float]]:
    """(ionic step, electronic iterations, E0) for every ionic step."""
    steps, n_elec = [], 0
    for line in oszicar.read_text(errors="replace").splitlines():
        m = re.match(r"\s*(?:DAV|RMM|CG|SDA):\s+(\d+)\s", line)
        if m:
            n_elec = int(m.group(1))
        elif " F=" in line:
            step = int(line.split()[0])
            e0 = float(re.search(r"E0=\s*([-+0-9.Ee]+)", line).group(1))
            steps.append((step, n_elec, e0))
            n_elec = 0
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", type=Path, required=True,
                        help="the lifepo4_vasp_agent directory of the raw outputs")
    parser.add_argument("--out", type=Path, default=EXP_DIR / "results" / "lifepo4_recovery_trace.csv")
    args = parser.parse_args()

    first = sorted(args.raw_dir.glob("intermediate_*"))
    if len(first) != 1:
        raise SystemExit(f"expected one intermediate_* directory in {args.raw_dir}, found {len(first)}")
    attempts = [("initial", first[0]), ("recovered", args.raw_dir)]

    rows = []
    for label, d in attempts:
        nelm = int(float(read_incar(d / "INCAR").get("NELM", "60")))
        steps = ionic_steps(d / "OSZICAR")
        converged = "reached required accuracy" in (d / "OUTCAR").read_text(errors="replace")
        hits = sum(1 for _, n, _ in steps if n >= nelm)
        print(f"{label:9s} NELM={nelm:3d}  ionic steps={len(steps):3d}  steps at NELM={hits}  "
              f"reached required accuracy={converged}")
        for step, n, e0 in steps:
            rows.append({"attempt": label, "ionic_step": step, "electronic_iterations": n,
                         "nelm": nelm, "hit_nelm": n >= nelm, "e0_ev": e0,
                         "attempt_reached_required_accuracy": converged})

    a, b = read_incar(attempts[0][1] / "INCAR"), read_incar(attempts[1][1] / "INCAR")
    print("INCAR changes (initial -> recovered):")
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            print(f"  {key}: {a.get(key, '(unset)')} -> {b.get(key, '(unset)')}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
