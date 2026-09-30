"""Fallback replies for the band-gap runs of experiment 07.

The rules are those of experiment 06 (answer_script.py); only the hardware
reply differs, because each material had exclusive use of seven GPUs.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "06_sol27lc_deepseek_v4_repeats" / "scripts"))
import answer_script as _base  # noqa: E402

_VASP_BIN_DIR = os.environ.get("VASP_BIN_DIR", "$VASP_BIN_DIR")
HARDWARE = f"""\
This machine is a single-node workstation without a Slurm/PBS scheduler.
CPU: 64 cores; GPUs: 8 x NVIDIA RTX 3090 (24 GB). GPU 2 has a throttling problem and has been excluded from this task's allocation.
VASP 6.4.2 is built for GPU (OpenACC); vasp_gpu and vasp_std are both in {_VASP_BIN_DIR}.
Environment script: source ~/env_vasp (sets up MKL, CUDA, the NVIDIA HPC SDK MPI, and the VASP PATH).
This task has exclusive use of 7 GPUs (CUDA_VISIBLE_DEVICES is already set), and only this material is being calculated at a time. You may use all 7 GPUs with one rank per GPU (for example, with KPAR aligned to them) or only some of them; it is up to you."""

RULES = [
    (category, predicate, HARDWARE if answer is _base.HARDWARE else answer)
    for category, predicate, answer in _base.RULES
]


def classify(text: str):
    for category, predicate, answer in RULES:
        if predicate(text):
            return category, answer
    return "fallback", _base.FALLBACK
