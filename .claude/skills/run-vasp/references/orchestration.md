# Agent Scheduling Strategy and Failure-Prevention Guide

## 1. The Login Node Taboo
If `probe_env.py` reports `"is_login_node": true`:
- **Absolutely forbidden**: using `vasp_runner.py --mode local` to launch a local calculation on the login node.
- You must steer the user to submit via Slurm/PBS, or to request an interactive node with `salloc`.

## 2. OOM Prevention
In high-throughput scenarios on fat nodes (e.g. 256 cores), do not simply divide the core count by the number of jobs.
- If the user's system is large (e.g. hundreds of atoms), each job may need 64 GB of memory.
- As the Agent, if you suspect insufficient memory, remind the user: "Since multiple jobs run concurrently, make sure the node's total memory is sufficient; otherwise this will lead to a Segmentation Fault."

## 3. GPU Binding Logic Explained
`--gpu-per-task` in `vasp_runner.py` provides physical isolation at the GPU-memory level. For example, with `--gpu-per-task 1` it automatically assigns `CUDA_VISIBLE_DEVICES=0` to job 0 and `CUDA_VISIBLE_DEVICES=1` to job 1. This is much safer than relying on automatic system assignment.

**Aligned with the main SKILL**: for **GPU builds of VASP**, generally **1 MPI rank <-> 1 GPU**. For the assistant, however, **the canonical entry point should always be `vasp_runner.py`**, rather than showing or hand-writing `mpirun` directly. Based on `--np`, `--gpu-per-task`, `--exe`, `--env-script`, and other arguments, `vasp_runner.py` generates and runs the corresponding `mpirun` command (or job script content) in the background. See `SKILL.md` "Core Execution Rules §1" for the full rules.

**Pre-launch gating and GPU selection (`vasp_runner.py`, local with `--gpu-per-task>0` and without `--fixed-gpu-layout`)**: polls `nvidia-smi`. **Two-tier GPU selection**: (1) **Empty GPUs first** -- simultaneously satisfy `memory.free >= --min-gpu-free-mib`, `utilization.gpu < --max-gpu-util-percent`, and **`memory.used <= --empty-gpu-max-used-mib`** (default about 512 MiB, meaning almost no other job is using GPU memory; pass **`0` to disable empty-GPU priority**, falling back to the next tier only); (2) **otherwise** allocate on GPUs that still satisfy **`min-gpu-free-mib` + `max-gpu-util-percent`** (GPU memory may be shared with other processes as long as thresholds are met). **Contiguous multi-GPU**: when `--gpu-per-task>1`, the smallest available slot with **contiguous physical indices** is taken within the relevant tier. If there is no slot and jobs remain in the queue, the process **sleeps and polls** until a running job releases GPUs or **`--gpu-ready-timeout-sec`** times out. External status checks likewise need not refresh frequently; if a job is expected to be long, extend each wait accordingly, e.g. `sleep 20m` before checking.

## 4. Executable Dependency Probing (mpirun / VASP)
The `dependencies` field output by `probe_env.py` contains:
- `mpirun_found`
- `vasp_std_found`
- `vasp_gpu_found`

**Strategy stage**: if a local run is planned and the relevant item is `false`, this must be stated in the strategy, and the user must confirm the `module load` / `PATH` in the `env_script` (e.g. `template/env_local.sh`); **do not** call `vasp_runner.py --mode local` or `quick_test.py` before this is resolved. What is shown to the user should be the runner command itself, not the underlying `mpirun` snippet.

**Slurm**: on a login node all three items may be `false`; `source` the user-provided env inside the job script, state clearly in the strategy that "modules are loaded on compute nodes", and avoid running `vasp_runner.py --mode local` on the login node. When presenting to the user, prefer showing `vasp_runner.py --mode slurm` or the full job script rather than an isolated `mpirun` snippet.

In local mode, `vasp_runner.py` and `quick_test.py` verify again before execution (after `source` if `--env-script` is passed); on failure they print `ERROR:` and exit with a non-zero code, so that the Agent can act on the global `command not found` rule.

## 5. Mandatory Branching (CPU+GPU coexistence / scheduler)
Consistent with **STRICT HARDWARE ALIGNMENT** in the project **system_prompt**:
- **Both GPU and CPU available**: do not default to a CPU-only multi-core plan; first ask the user whether they want GPU or CPU only, and how many GPUs. The GPU entry point may be `vasp_gpu` or the GPU build of `vasp_std` (see main SKILL §1).
- **Slurm/PBS detected**: do not replace job submission with local `vasp_runner.py --mode local` or direct `mpirun`; first ask for partition/queue, number of nodes, walltime, etc., then write the script.

## 6. Conventions for Showing Commands to the User

- **Formal commands shown to the user / upstream skills** should preferably be:
  - `python .claude/skills/run-vasp/scripts/vasp_runner.py ...`
  - or `python .claude/skills/run-vasp/scripts/quick_test.py ...`
- **Do not** treat the underlying `mpirun` snippet as the assistant's preferred presentation interface; it is an internal implementation detail of the runner.
- When the user needs to know "how it actually runs underneath", you may add:
  - `vasp_runner.py` generates the corresponding `mpirun` command in the background based on its arguments
  - or, in the Slurm/PBS case, generates the run snippet in the job script
- Therefore, the assistant's canonical procedure is:
  1. First assemble the `vasp_runner.py` / `quick_test.py` arguments
  2. Confirm the runner command with the user
  3. Then let the runner generate and execute the corresponding `mpirun` in the background
