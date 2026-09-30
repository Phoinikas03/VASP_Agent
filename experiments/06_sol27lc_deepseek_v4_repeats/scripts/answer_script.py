"""Deterministic fallback replies for the VASP Agent Sol27LC runs.

Every question the agent asks is shown to the operator. If the operator does not reply within the
timeout, the rules below produce the reply: regex matching only, no model in
the loop. Rules are evaluated top to bottom; the first match wins.

A question that would decide the physics ("should I run a convergence test?",
"what ENCUT?") is never answered with a parameter value. The operator never
originates a calculation parameter.
"""
import os
import re

# The hardware reply describes the machines used for Runs 1-3 (single-node
# workstations with 8 x RTX 3090). Only the VASP binary directory and the CPU
# count are filled in at run time.
_VASP_BIN_DIR = os.environ.get("VASP_BIN_DIR", "$VASP_BIN_DIR")
_NCPU = os.cpu_count() or 64
HARDWARE = f"""\
This machine is a single-node workstation without a Slurm/PBS scheduler.
CPU: {_NCPU} cores; GPUs: 8 x NVIDIA RTX 3090 (24 GB).
VASP 6.4.2 is built for GPU (OpenACC); vasp_gpu and vasp_std are both in {_VASP_BIN_DIR}.
Environment script: source ~/env_vasp (sets up MKL, CUDA, the NVIDIA HPC SDK MPI, and the VASP PATH).
Use the GPU build of VASP: after source ~/env_vasp, call vasp_gpu. This task has been allocated 1 GPU (CUDA_VISIBLE_DEVICES is already set); use one rank per GPU, i.e. --np 1 --gpu-per-task 1."""

GO_AHEAD = "Run the convergence tests: carry out the full ENCUT/KSPACING convergence procedure."
CONSENT = "Agreed, please proceed."
# Used when the agent asks for a parameter it has not itself proposed.
FALLBACK = "Please decide yourself, explain your reasoning, and then continue."

# A message quoting a concrete launch command is an execution-approval request,
# even though the command line itself is full of protocol words.
_COMMAND_TOKEN = re.compile(
    r"(vasp_runner\.py|quick_test\.py|mpirun|--dirs|--np\b|--gpu-per-task|"
    r"\.claude/skills/run-vasp)", re.I)
_CONFIRM_PHRASE = re.compile(
    r"(do you agree|do you confirm|please confirm|confirm (the )?execution|"
    r"should i (run|execute|start)|may i (run|execute|start)|shall i (start|begin)|"
    r"please approve|shall\s+i\s+proceed|do\s+you\s+approve)", re.I)
# "Shall I run a convergence test / which path do you want": a go-ahead question.
_CONVERGENCE_ASK = re.compile(
    r"(convergence\s*test|convergence\s*scan|full convergence|"
    r"convergence (procedure|workflow)|option\s*[ab]\b|"
    r"(whether|should i|do you want).{0,20}converge)", re.I)
_HARDWARE_ASK = re.compile(
    r"(hardware|machine configuration|how many\s*gpus?|gpu\s*count|cpu\s*cores?|"
    r"scheduler|slurm|pbs|env_script|environment script|executable|vasp_gpu|"
    r"vasp_std|module\s*load|environment variables?)", re.I)

# (category, predicate, answer): first match wins.
RULES = [
    ("command_confirmation",
     lambda t: bool(_COMMAND_TOKEN.search(t) and _CONFIRM_PHRASE.search(t)), CONSENT),
    ("convergence_go_ahead", lambda t: bool(_CONVERGENCE_ASK.search(t)), GO_AHEAD),
    ("hardware_environment", lambda t: bool(_HARDWARE_ASK.search(t)), HARDWARE),
    ("command_confirmation", lambda t: bool(_CONFIRM_PHRASE.search(t)), CONSENT),
]


def classify(text: str):
    """Return (category, answer) for an agent message that awaits a reply.

    Anything unmatched, notably a bare "what ENCUT should I use?", falls through
    to FALLBACK, because the operator never originates a parameter.
    """
    for category, predicate, answer in RULES:
        if predicate(text):
            return category, answer
    return "fallback", FALLBACK
