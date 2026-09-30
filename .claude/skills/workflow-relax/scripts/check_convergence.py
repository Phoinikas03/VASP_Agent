"""
Check the convergence status of a VASP structural relaxation, and output a JSON report.
Usage: python scripts/check_convergence.py [work_dir]
Default directory: current directory

Output fields:
  ionic_converged      - whether the ionic steps meet the EDIFFG convergence criterion
  electronic_converged - whether the last electronic step converged (reuses the generic run_vasp criterion)
  nsw_reached          - whether the maximum number of ionic steps was exhausted (sign of non-convergence)
  max_force_eV_A       - maximum atomic force at the last ionic step (eV/Å)
  num_ionic_steps      - number of completed ionic steps
  contcar_exists       - whether CONTCAR exists (indicates relaxation finished)
  errors               - error lines found in OUTCAR
  warnings             - warning lines found in OUTCAR
  last_lines           - last 20 lines of OUTCAR (for manual inspection)
"""
from __future__ import annotations

import contextlib
import io
import importlib.util
import json
import os
import re
import sys
from pathlib import Path


def _load_shared_checker():
    shared_path = (
        Path(__file__).resolve().parents[2] / "run-vasp" / "scripts" / "check_convergence.py"
    )
    spec = importlib.util.spec_from_file_location("run_vasp_check_convergence", shared_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load the shared convergence check script: {shared_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_convergence(work_dir="."):
    shared = _load_shared_checker()
    with contextlib.redirect_stdout(io.StringIO()):
        shared_result = shared.check_convergence(work_dir)

    result = {
        "status": shared_result["status"],
        "ionic_converged": False,
        "electronic_converged": shared_result["electronic_converged"],
        "finished_normally": shared_result["finished_normally"],
        "nsw_reached": False,
        "max_force_eV_A": None,
        "num_ionic_steps": 0,
        "contcar_exists": False,
        "errors": list(shared_result["errors"]),
        "warnings": list(shared_result["warnings"]),
        "last_lines": list(shared_result["last_lines"]),
    }

    outcar_path = os.path.join(work_dir, "OUTCAR")
    contcar_path = os.path.join(work_dir, "CONTCAR")
    result["contcar_exists"] = os.path.exists(contcar_path) and os.path.getsize(contcar_path) > 0

    if not os.path.exists(outcar_path):
        if "OUTCAR file does not exist" not in result["errors"]:
            result["errors"].append("OUTCAR file does not exist")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result

    with open(outcar_path, "r", errors="replace") as f:
        lines = f.readlines()

    nsw = None
    ionic_steps = 0
    max_force = None

    for line in lines:
        line_lower = line.lower()
        if "reached required accuracy - stopping structural energy minimisation" in line_lower:
            result["ionic_converged"] = True

        if "nsw" in line_lower and "=" in line:
            m = re.search(r"NSW\s*=\s*(\d+)", line, re.IGNORECASE)
            if m:
                nsw = int(m.group(1))

        if "- Iteration" in line:
            m = re.search(r"Iteration\s+(\d+)\s*\(", line)
            if m:
                ionic_steps = max(ionic_steps, int(m.group(1)))

        if "FORCES: max atom, RMS" in line:
            m = re.search(r"max atom, RMS\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)", line)
            if m:
                max_force = float(m.group(1))

    result["num_ionic_steps"] = ionic_steps
    result["max_force_eV_A"] = max_force
    if nsw is not None and ionic_steps >= nsw:
        result["nsw_reached"] = True

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    work_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    check_convergence(work_dir)
