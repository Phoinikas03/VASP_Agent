#!/usr/bin/env python
"""VASP Agent driver for one HSE06 band-gap task (experiment 07).

Reuses the Sol27LC driver of experiment 06 and changes only what differs:

* the task prompt and the task line of the system prompt (HSE06 band gap);
* completion: a converged hybrid calculation with readable eigenvalues exists
  (judged from vasprun.xml by gap_tools.best_hybrid_gap);
* the progress fingerprint also counts vasprun.xml files;
* the hardware reply: the material has exclusive use of seven GPUs;
* the budget: 24 h wall time per material.

Materials were run one at a time. Structures are prepared by
extract_bandgap_structures.py.

Usage:
    python run_agent_bg.py --system Si --gpu-uuid <uuid1,uuid2,...> --structures-dir <dir>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "experiments" / "06_sol27lc_deepseek_v4_repeats" / "scripts"))

import run_agent_lc as base  # noqa: E402

# The functional is part of the task specification: atomate2's band-structure
# default is PBE, and comparing a PBE gap with an HSE gap would measure the
# functional, not the workflow. Everything else is left to the agent.
TASK_PROMPT = """\
Please calculate the band gap of the material described by the POSCAR in the
working directory.

Working directory: {workspace}
Structure file: {workspace}/POSCAR

Requirements:
- Use HSE06 hybrid-functional accuracy.
- Run the bandgap workflow **in full**; do not omit any of its steps.
- Finally, report the band gap explicitly (in eV) and its type (direct/indirect).

I will not specify any calculation parameters for you (ENCUT, k points,
smearing, pseudopotential variant, AEXX/HFSCREEN, ALGO, etc.). These parameters
should be determined by the workflow itself, not by directly picking a safe
value from experience.
"""

SYSTEM_TASK_LINE = (
    "Task type: HSE06 band-gap calculation. Apart from the functional, which is "
    "specified as HSE06, all calculation parameters are for you to decide."
)


_SOL27LC_SYSTEM_PROMPT = base.build_system_prompt


def build_system_prompt(workspace: str) -> str:
    # Identical to the Sol27LC system prompt except for the final task line.
    # Rule 4 (one GPU) was kept unchanged in these runs, although the hardware
    # reply in answer_script_bg.py offers seven GPUs.
    lines = _SOL27LC_SYSTEM_PROMPT(workspace).splitlines()
    lines[-1] = SYSTEM_TASK_LINE
    return "\n".join(lines)


def looks_finished(workspace: Path) -> bool:
    from gap_tools import best_hybrid_gap

    return best_hybrid_gap(str(workspace)) is not None


def progress_fingerprint(workspace: Path) -> tuple:
    total = 0
    count = 0
    for path in list(workspace.rglob("OUTCAR")) + list(workspace.rglob("vasprun.xml")):
        try:
            total += path.stat().st_size
        except OSError:
            continue
        count += 1
    return count, total


def get_classifier():
    from answer_script_bg import classify

    return classify


base.TASK_PROMPT = TASK_PROMPT
base.build_system_prompt = build_system_prompt
base.looks_finished = looks_finished
base.progress_fingerprint = progress_fingerprint
base.get_classifier = get_classifier


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--system", required=True, help="Si, GaAs, GaP, ZnO, or Cu2O")
    parser.add_argument("--gpu-uuid", required=True, help="comma-separated GPU UUIDs")
    parser.add_argument("--structures-dir", type=Path, default=EXP / "work" / "bandgap_structures")
    parser.add_argument("--rep", default="rep1")
    parser.add_argument("--runs-dir", type=Path, default=EXP / "runs_agent_bandgap")
    parser.add_argument("--budget-sec", type=int, default=24 * 3600)
    args = parser.parse_args()

    outdir = args.runs_dir / args.rep / args.system
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "POSCAR").write_text((args.structures_dir / args.system / "POSCAR").read_text())

    info = asyncio.run(base.run_one(args.system, args.gpu_uuid, outdir, args.budget_sec))
    print(json.dumps(info, indent=2, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
