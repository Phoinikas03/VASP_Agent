#!/usr/bin/env python
"""VASP Agent driver for one Sol27LC system without domain skills (experiment 08).

Reuses the driver of experiment 06 and changes only what differs:

* Project root. Skills are discovered by walking up from the agent's working
  directory, so the workspaces are placed under a separate project root
  (--agent-root, outside this repository) whose .claude/skills/ contains only
  run-vasp, copied from this repository. The agent code, tools, and model
  configuration are those of this repository.
* Task prompt. Refers to no workflow.
* System prompt. "Project root" instead of "Repository root", and "per-case"
  instead of "per-volume" subdirectories in rule 3.
* Completion check. Runs choose their own directory layout, so completion is
  judged from the energy-volume data (extract_any.py): at least five volumes,
  a fitted minimum inside the sampled range, and R^2 >= 0.99.

Usage:
    python run_agent_noskill.py --system Si_dia --gpu-uuid GPU-xxxx --agent-root /path/outside/repo
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "experiments" / "06_sol27lc_deepseek_v4_repeats" / "scripts"))

import run_agent_lc as base  # noqa: E402

TASK_PROMPT = """\
Please calculate the equilibrium lattice constant of the structure described by
the POSCAR in the working directory.

Working directory: {workspace}
Structure file: {workspace}/POSCAR

Requirements:
- Use the PBE functional.
- Please complete this calculation fully and rigorously; do not skip necessary
  steps to save time.
- Finally, report the equilibrium lattice constant explicitly (in Å).

I will not specify any calculation parameters for you (ENCUT, k points,
smearing, pseudopotential variant, sampling settings, etc.).
"""

AGENT_ROOT: Path | None = None
_SKILL_SYSTEM_PROMPT = base.build_system_prompt


def build_system_prompt(workspace: str) -> str:
    text = _SKILL_SYSTEM_PROMPT(workspace)
    text = text.replace(f"Repository root (skills and everything under `.claude/`): {REPO}",
                        f"Project root (skills and everything under `.claude/`): {AGENT_ROOT}")
    text = text.replace(f'`cd "{REPO}" && ...` or use an absolute path starting with `{REPO}/`.',
                        f'`cd "{AGENT_ROOT}" && ...` or use an absolute path starting with `{AGENT_ROOT}/`.')
    return text.replace("for per-volume subdirectories", "for per-case subdirectories")


def looks_finished(workspace: Path) -> bool:
    from extract_any import fit

    try:
        res = fit(str(workspace), workspace.name.split("_")[-1])
    except Exception:  # noqa: BLE001 - an unreadable workspace is not finished
        return False
    return ("a_conv_A" in res and res.get("n_points", 0) >= 5
            and res.get("bracketed", False) and res.get("R2", 0) >= 0.99)


base.TASK_PROMPT = TASK_PROMPT
base.build_system_prompt = build_system_prompt
base.looks_finished = looks_finished


def prepare_agent_root(agent_root: Path) -> None:
    agent_root = agent_root.resolve()
    if agent_root == REPO or REPO in agent_root.parents:
        raise SystemExit("--agent-root must be outside the repository, or the domain skills remain discoverable")
    skills = agent_root / ".claude" / "skills"
    if not (skills / "run-vasp").exists():
        skills.mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO / ".claude" / "skills" / "run-vasp", skills / "run-vasp")
    present = sorted(p.name for p in skills.iterdir())
    assert present == ["run-vasp"], present


def main() -> None:
    global AGENT_ROOT
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--system", required=True)
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--agent-root", type=Path, required=True,
                        help="project root outside this repository; runs go to <agent-root>/runs/<rep>/<system>")
    parser.add_argument("--rep", default="rep1")
    parser.add_argument("--budget-sec", type=int, default=base.DEFAULT_BUDGET_SEC)
    args = parser.parse_args()

    prepare_agent_root(args.agent_root)
    AGENT_ROOT = args.agent_root.resolve()
    outdir = AGENT_ROOT / "runs" / args.rep / args.system
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "POSCAR").write_text((base.STRUCTURES / args.system / "POSCAR").read_text())

    info = asyncio.run(base.run_one(args.system, args.gpu_uuid, outdir, args.budget_sec))
    print(json.dumps(info, indent=2, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
