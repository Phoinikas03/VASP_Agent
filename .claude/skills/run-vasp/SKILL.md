---
name: run-vasp
description: "Environment- and hardware-aware VASP orchestration: probe mpirun/vasp_std/vasp_gpu, STRICT HARDWARE ALIGNMENT (GPU+CPU coexistence / Slurm branching), GPU mapping (usually 1 rank <-> 1 GPU), ITERATIVE batched calls to vasp_runner, and showing the full command to the user and obtaining consent before execution. This skill MUST be loaded whenever mpirun, vasp_std, vasp_gpu, Slurm/PBS submission, or vasp_runner.py is used in this workspace; if the user also asks to optimize for CPU/GPU resources or rewrite KPAR/NCORE/NPAR in INCAR, the incar-performance skill must also be loaded before the actual launch. Materials skills such as workflow-eos-lattice-constant, workflow-relax, workflow-electronic-structure, workflow-adsorption-energy, research-literature, and structure-supercell must also load this skill before actually launching VASP."
version: "1.0.2"
---

# Skill: Run VASP (Intelligent Orchestrator)

## Core Objective
As an advanced computational science assistant, your job is to orchestrate VASP jobs safely and efficiently. Before running any calculation you must be aware of the environment you are in; never blindly launch expensive calculations or `mpirun` without confirming resources, **executable dependencies**, and compute mapping.

**Submission entry points (mandatory)**: When the user asks to actually launch VASP, the default and preferred entry points are:
- `python .claude/skills/run-vasp/scripts/vasp_runner.py ...`
- `python .claude/skills/run-vasp/scripts/quick_test.py ...` (quick trial runs only)
- `python .claude/skills/run-vasp/scripts/check_convergence.py <workdir>` (unified post-run status check)
- `python .claude/skills/run-vasp/scripts/terminate.py --work-dir <dir>` (only when ownership has been proven and the user has explicitly agreed to stop)

Except **inside scripts generated/driven by `vasp_runner.py`**, the assistant **must not** hand-write `mpirun -np ... vasp_std/vasp_gpu` directly in Bash as the formal submission command. In other words: **the point of the `run-vasp` skill is not just "read the rules first", but to funnel actual execution through the runner scripts.**

**Run-state persistence (new)**: `vasp_runner.py` now writes **`.vasp_run_state.json`** into each job directory, recording `run_id`, `pid/pgid` (local) or `job_id` (Slurm), log path, launch command, and status. Subsequent `terminate.py` and `vasp-error-recovery` should treat this file as the primary source of "ownership evidence".

If the user wants not only to "run" but also to have you **modify `INCAR` according to the hardware and optimize parallelization parameters**, this skill does not decide the specific values of `KPAR/NCORE/NPAR`. In that case, first load the `incar-performance` skill, ask the user about their hardware and update `INCAR`, then return to this skill to execute.

## Core Execution Rules (CRITICAL EXECUTION RULES)

### 1. Hardware Alignment & MPI Mapping

When preparing to launch VASP (whether via `vasp_runner.py` or via commands inside a Slurm/PBS script), follow the mapping below. It is **strictly forbidden** to use an oversized `-np` out of habit without aligning with the hardware (e.g. defaulting to `-np 16` / `-np 64` without asking).

- **GPU acceleration (OpenACC or other GPU builds)**
  - **Default mental model (keep in mind)**: in a typical GPU environment, **one physical GPU corresponds to one (concurrent) VASP job**; each job exclusively owns its set of visible devices, and **`mpirun` uses `-n 1` or `-np 1` for that job**, matching "one process per GPU". **Hand-written launch pattern**: `CUDA_VISIBLE_DEVICES=i mpirun -n 1 <gpu_exe>` (or `mpirun -np 1`; `i` is the device index, `0`, `1`, ...). For concurrent jobs, launch one such command per distinct `i` (or generate the equivalent via `vasp_runner.py --gpu-per-task 1`). **Do not** use `-np`>1 on a single GPU within one job unless the user explicitly requests multi-GPU parallelism for the same calculation.
  - **Mapping rule**: usually **1 MPI rank <-> 1 physical GPU** (consistent with `vasp_runner.py`'s `--gpu-per-task` setting `CUDA_VISIBLE_DEVICES` per job; details in `references/orchestration.md` §3).
  - **Executable name**: many sites provide a separate name **`vasp_gpu`**; many environments also **still call the GPU build `vasp_std`**, or distinguish it via path/module (e.g. **`vasp_std/gpu`**, `bin/gpu/vasp_std` in the install layout). **Go by the user's modules and `command -v`**; `vasp_runner.py`'s `--exe` should be **the name that will actually appear in `mpirun ... <exe>`, or a name resolvable on PATH**.
  - **Single job, single GPU**: only one job occupying 1 GPU -> **`mpirun -np 1`**, with `<exe>` being the **GPU build** entry point above; `np` must match the number of GPUs the job actually uses.
  - **Single job, multiple GPUs**: the same calculation uses multiple GPUs (e.g. 4) -> **`mpirun -np 4`** (or whatever GPU binding the user/site has agreed on), with `<exe>` still the GPU build entry point.
  - **Concurrent jobs**: when running multiple independent jobs in parallel, each job should use **`np=1` per GPU**, isolated with **mutually exclusive `CUDA_VISIBLE_DEVICES`** (or generated automatically by `vasp_runner.py --gpu-per-task 1`). For example job A: `CUDA_VISIBLE_DEVICES=0 mpirun -np 1 <gpu_exe>`; job B: `CUDA_VISIBLE_DEVICES=1 mpirun -np 1 <gpu_exe>` (`<gpu_exe>` may be `vasp_gpu` or the site's `vasp_std`, etc.). Do not let multiple jobs contend for the same GPU unconstrained.
- **CPU only (CPU-only build)**
  - When using a **CPU-only** VASP executable, the **`-np`** reserved for the job may equal the **number of physical cores** allocated to it (or slightly fewer per site policy to avoid oversubscription); still confirm with the user in Steps 2-3 and do not default to a large core count. **Do not** automatically assume that "named `vasp_std`" means CPU-only -- if that binary is actually a GPU build, still follow the GPU mapping and `--gpu-per-task` above.

**Executable names and toolchain**: `vasp_std_found` / `vasp_gpu_found` in `probe_env.py` only reflect whether the default names are hit on the default PATH; **final validity is determined by whether, after `source env_script`,** `vasp_runner.py` / `verify_local_dependencies` can find **the executable pointed to by `--exe`**. When confirming the command with the user, write out the **real** `<exe>` (whether `vasp_gpu` or the GPU build of `vasp_std`).

### 2. Exact Command Confirmation

Before calling `Bash` or `TaskOutput` to **actually launch** a calculation, you **must** stop and show the user: (1) the **hardware and scheduler summary** obtained in Step 2 (CPU/GPU counts, whether on a login node, scheduler type, etc.); (2) the **complete command you will finally use** (including `source env_script`, `CUDA_VISIBLE_DEVICES` if hand-written, the full `vasp_runner.py` argument line, or the `mpirun` line in the job script). **Only after the user explicitly agrees** (e.g. "yes / agree / confirm") may you execute.

*Example*: "The probe shows 8 GPUs in total. The first step of the current convergence test uses one GPU for a single job. Planned command: `source ./template/env_local.sh && python .claude/skills/run-vasp/scripts/vasp_runner.py --dirs <dir> --mode local --np 1 --exe vasp_gpu --gpu-per-task 1 --env-script ./template/env_local.sh --log-file vasp_pbe.log` (if the site's GPU entry point is `vasp_std`, use `--exe vasp_std`). Do you agree?"

**Relation to iterative workflows**: command confirmation applies to **the step or batch currently about to run**; if an upstream skill requires point-by-point/directory-by-directory checks, you must still follow the **ITERATIVE EXECUTION RULE** in Step 4 below, and **must not** use "one confirmation" to cover a subsequent queue of multiple points without checks. For local concurrent jobs, prefer the GPU allocation and logging built into `vasp_runner.py`; **do not** write your own monolithic `for`+`mpirun` that bypasses the runner and the iteration rules.

### 3. Process Ownership & Safe Termination

- **Global process killing is forbidden by default**: never use commands that terminate VASP or MPI processes in bulk by name/pattern, such as `pkill`, `killall`, `pkill -f vasp_std`, `killall vasp_std`, `kill $(pgrep ...)`.
- **Use the termination entry point**: when the user asks to stop/pause/cancel a VASP job, do not improvise a shell kill pipeline. Prefer:
  - With a state file: `cd "<repo_root>" && python .claude/skills/run-vasp/scripts/terminate.py --work-dir "<task_dir>" --reason "<reason>"`
  - Legacy hand-launched jobs without `.vasp_run_state.json`: first run `cd "<repo_root>" && python .claude/skills/run-vasp/scripts/terminate.py --work-dir "<task_dir>" --allow-cwd-scan --dry-run` to show candidate PIDs; only when every PID in the output clearly shows `/proc/<pid>/cwd` exactly equal to the target directory may you drop `--dry-run` and terminate.
- **`pkill -f` is especially forbidden**: it matches the command line of the shell/tool wrapper process running the command itself, and may kill the message reader or agent process along with it, producing `exit code -15` / `Fatal error in message reader`.
- **Only handle jobs you started yourself**: if a job needs to be stopped, you must first prove that the PID or job ID is the one you personally launched in the current workflow; the evidence should come from:
  - the `task_id`, job ID, log path, or directory you recorded when you just launched it;
  - `ps` / `squeue` / `qstat` / job output that clearly maps to the current workspace or the current batch of directories;
  - log or file timestamps showing that the process truly belongs to the current run, rather than someone else's VASP or one in another directory.
- **A name alone is not evidence**: `pgrep -f vasp_std`, `ps | grep vasp_std`, and process counts can at most show that "some VASP is running on the system"; they **cannot** show that those processes belong to the current job, so you **must never** terminate based on them.
- **Verify first, then decide whether to stop**: before considering termination, check whether stopping is really needed, e.g.:
  - whether the log in the associated directory keeps growing;
  - whether `OUTCAR` / `vasp_run_*.log` still receives new content;
  - whether the corresponding `task_id` / job status has already finished but left child processes behind;
  - whether the user explicitly asked to "stop" rather than "keep waiting".
- **Targeted termination only**: only after completing the checks above and confirming that stopping is truly needed may you terminate the specific PID or job ID **confirmed to belong to the current job**; prefer the narrowest-scope command.
- **Correct behavior when ownership cannot be proven**: stop automatic handling, report to the user that "processes with the same name were found, but they cannot be proven to belong to the current job", and ask for confirmation; do not clear them out and resubmit on your own.

### 4. In-place Retry & GPU Failure Recovery

- **No double launches in the same directory**: if VASP in a working directory (e.g. `pbe_scf`, `hse_scf`, or an `e_400` / `k_0.15` directory) needs a retry because of a GPU/MPI error, you **must not** launch a second `mpirun` / `vasp_*` in the same directory while the old process may still be alive. In particular, never write `... mpirun ... > same.log 2>&1 &` directly as a "remedial rerun".
- **Evidence first, then restart**: before restarting you must obtain ownership evidence for the old run, e.g. the `task_id` from when you launched it, the `vasp_runner.py` command, the command line with working directory in `ps`, and the corresponding log path. Then confirm whether the old run is still alive.
- **If the old run is still alive**: you may only perform targeted termination of the exact PID / job ID / process tree spawned by that job; wait until they have actually disappeared before restarting. Do not open a new process just because it "should have failed" or because you saw an error snippet.
- **Keep using the runner**: even if only the GPU layout changes (e.g. going from 8 GPUs to 7 to avoid a bad card), prefer re-invoking `python .claude/skills/run-vasp/scripts/vasp_runner.py ...` or `quick_test.py` over hand-writing a new formal `mpirun` submission.
- **Log rule for in-place reruns**: only after the old run has been confirmed finished or has been terminated in a targeted way may you continue writing to the same log file; otherwise stop the old run first. Never let two active VASP processes write to the same directory or the same `vasp_*.log` at once.
- **Explanation to the user**: after a GPU error, the recovery plan must clearly tell the user whether the old run is still alive, whether it has been cleaned up in a targeted way, what the replacement GPU layout is, and what the restart command is.

---

## Workflow

### Step 1: Intent Recognition
- Ask the user or analyze the prompt: is this a formal production calculation, or just a quick validation to test whether the input files are valid (Pre-flight / Quick Test)?
- **If it is a quick trial run**: first complete the Step 2 probe; if dependencies are satisfied, call `python .claude/skills/run-vasp/scripts/quick_test.py` (optionally with `--env-script`, `--exe`, `--log-file`).
- **If it is a production calculation**: go to Step 2.

### Step 2: Environment Probing
- Run the probe: `python .claude/skills/run-vasp/scripts/probe_env.py`
- **Supplementary probing aligned with the project system_prompt** (when the probe does not cover something or manual cross-checking is needed):
  - **Prefer** having already run `probe_env.py`: the scheduler is determined by **whether `sbatch` / `qsub` is on PATH** (consistent with the script), and **does not depend on** `sinfo`/`qstat`.
  - If you additionally run `lscpu`, `nvidia-smi -L`, `hostname` via Bash: **do not** chain `sinfo`/`qstat` with those commands using **`&&`** in one line -- bare-metal workstations often lack `sinfo`, which makes the whole command fail (e.g. exit 127) and may also break other tools running **in parallel** in the same round ("Sibling tool call errored").
  - If Slurm/PBS details are needed: only run them (e.g. `sinfo -N`) after `command -v sinfo` / `command -v qstat` succeeds; otherwise skip. A safe one-liner: `lscpu; nvidia-smi -L 2>/dev/null || true; hostname; command -v sinfo >/dev/null && sinfo -N || true`
- Analyze the JSON output, paying attention at least to:
  - **Resources and scheduling**: `cpu_cores_total`, `gpu_info`, `scheduler` (`slurm` / `pbs` / `none`), `is_login_node`
  - **Executable dependencies (proactive hazard clearing)**: the `dependencies` object
    - `mpirun_found`
    - `vasp_std_found`
    - `vasp_gpu_found`

**Dependency red lines (must be handled at the strategy stage; no blind execution):**
- If the plan is **CPU only**, `--exe` is **`vasp_std` (or the default `vasp_std`)**, GPU mapping is **not** enabled (no `--gpu-per-task`, and the user has confirmed the binary is a CPU-only build), and `mpirun_found` or `vasp_std_found` is `false`: you **must not** directly run `vasp_runner.py --mode local` or `quick_test.py`. In Step 3 you must explain the missing items to the user, ask which `module load`, `export PATH`, or other initialization to use, write the agreed setup into **`template/env_local.sh`** (or a dedicated `env_script` in the workspace), execute only after user confirmation, and pass `--env-script` on the command line.
- If the plan is **GPU accelerated**: after `source env_script`, **the GPU-build executable selected by `--exe`** must be resolvable (commonly `vasp_gpu`, or a site-provided GPU build of `vasp_std` / `vasp_std/gpu`, etc.). If the probe only shows `vasp_std_found` while `vasp_gpu_found` is `false`, **do not** conclude from that alone that there is no GPU build -- go by the user's module notes and `command -v`; if it still cannot be resolved, complete the `env_script` in Step 3 before running.
- **Slurm special case**: on a login node `dependencies` may all be `false` while modules are available on compute nodes -- it is allowed to generate a job script that `source`s the user-provided env inside the script; the strategy must state clearly that "dependencies are loaded on compute nodes", and avoid running local `mpirun` on the login node.

**STRICT HARDWARE ALIGNMENT (mandatory branching question, consistent with system_prompt):**
- If **both GPU and CPU are detected** (e.g. `gpu_info.has_gpu` and a clearly usable CPU core count): **do not** default to a CPU-only multi-core `mpirun` without asking. You **must** stop and explicitly ask the user: do they want **GPU acceleration** (the executable may be `vasp_gpu` or the site's GPU build of `vasp_std`, etc.) or **CPU only**? If GPU, how many GPUs and how should they be bound?
- If **Slurm or PBS** is detected: **do not** replace cluster submission with local `mpirun`. You **must** ask the user for the target partition/queue, number of nodes, walltime, etc., and then write them into the `sbatch`/PBS script.

### Step 3: Strategy formulation
Based on the probe results and the number of directories to compute, recommend a strategy to the user and **explicitly check dependencies, the mandatory-branching conclusion, and the login-node rule** (see `references/orchestration.md`). The strategy must state **how MPI process counts map to CPU/GPU** (see "Core Execution Rules §1" above), and prepare the full command draft that will be shown to the user in Step 4 through **§2 Exact Command Confirmation**.
- **Scenario 1 (fat bare-metal node, multiple jobs)**: no Slurm, ample resources, dependencies satisfied in Step 2 (or after `source env_script`), and the user has confirmed a **CPU-only or GPU** plan. Recommend `vasp_runner.py` with `--mode local`, assign a suitable `--np` to each directory; for the GPU plan set **`--gpu-per-task`** and set **`--exe`** to the site's actual GPU entry point (`vasp_gpu` or the GPU build of `vasp_std`, etc.), and pass the confirmed env file via `--env-script`.
- **Scenario 2 (HPC cluster)**: Slurm is present and you are on a login node. **Never** run local `mpirun` on the login node. Recommend `--mode slurm` to generate and submit the job; the script contains `source env_script` and the user-agreed `mpirun` command; partition/walltime etc. must come from explicit user replies.
- **Scenario 3 (GPU workstation)**: GPUs are present. For multiple jobs you may use `--gpu-per-task` with the user-confirmed **`--exe` (`vasp_gpu` or the GPU build of `vasp_std`, etc.)**; this must be consistent with the user's confirmed GPU preference and the Step 2 dependency red lines.

**If `dependencies` shows the required executable is unavailable (and this is not the documented "modules only on compute nodes" case)**: you must proactively ask the user in the recommended strategy which modules or environment variables to load, and write them into the execution strategy's **env_script**; **never** run local VASP before this is resolved.

**Public argument quick reference (preferred when showing commands to the user)**:
- `--dirs <dir1> [dir2 ...]`: working directories to run
- `--mode local|slurm`: run locally or generate and submit a Slurm job
- `--np N`: number of MPI ranks for the VASP job of each directory
- `--exe vasp_std|vasp_gpu|<site_exe>`: actual executable name
- `--gpu-per-task N`: number of GPUs bound per job; recommended to give explicitly for local GPU runs
- `--env-script <path>`: environment script to `source` first
- `--log-file <name>`: explicit log name for single-directory jobs, e.g. `vasp_pbe.log`
- `--log-prefix <prefix>`: log prefix for multi-directory jobs; produces `vasp_run_0.log` etc. by default
- `--slurm-template <path>`: Slurm template
- `--min-gpu-free-mib` / `--max-gpu-util-percent` / `--gpu-ready-poll-sec` / `--gpu-ready-timeout-sec` / `--fixed-gpu-layout` / `--empty-gpu-max-used-mib`: local GPU gating and GPU selection policy

**Log conventions (recommended)**:
- Single step, single directory: pass `--log-file` explicitly, e.g. `vasp_pbe.log`, `vasp_hse.log`, `vasp_relax.log`
- Multi-directory batches: use `--log-prefix`, e.g. `--log-prefix vasp_encut`, producing `vasp_encut_0.log`, `vasp_encut_1.log` ...
- `quick_test.py` writes `vasp_quick_test.log` by default

### Step 4: Execution & Tracking
- After confirming the strategy and **env_script** with the user, first complete **"Core Execution Rules §2"**: show the hardware summary and the **complete planned command** (or the `mpirun` snippet in the job script), **obtain the user's explicit consent**, and then call `python .claude/skills/run-vasp/scripts/vasp_runner.py` via Bash (passing `--dirs`, `--mode`, `--np`, `--exe`, `--env-script`, `--gpu-per-task`, `--log-file/--log-prefix`, etc.) or submit via Slurm/PBS.
- **How to wait once `vasp_runner` has started (consistent with the repository system_prompt):** Bash must use **`run_in_background: true`**. During the waiting phase, prefer **periodic checks** over rapid refreshing: for long jobs, proactively use a coarse check interval, and extend each wait according to the expected job duration, e.g. `sleep 20m` before checking; tighten the interval only temporarily when close to completion or when troubleshooting. You may use **`TaskOutput` + `block: false`** to poll the same `task_id` periodically, or a **background** Bash helper script with `sleep` to periodically check each directory's `OUTCAR`, logs, and process status; after completion, read the logs and do downstream checks. Avoid **`TaskOutput` + `block: true` + a very long `timeout`** that blocks in one call until VASP finishes, because this freezes the Web/IDE.
- Tell the user the log paths; prefer citing the explicit log files in the working directory (produced by `--log-file` or `--log-prefix`) rather than leaving the logs only in the outer Bash task output. In the Slurm case, additionally use `squeue -u $USER` etc. to help track queue status.
- **Align with the project ITERATIVE EXECUTION RULE**: if an upstream skill requires **point-by-point** convergence (multiple ENCUT/k-points) or **directory-by-directory** checks (e.g. multiple `scale_*`), **do not** queue all points or all directories in a single `vasp_runner.py --dirs` call without reading OUTCAR/logs in between; split into multiple calls (or small batches of directories), confirming each step before continuing.

---

## Safety Net for Missing Dependencies (aligned with the project system_prompt)

When `mpirun` or the selected VASP executable is missing from the environment, handle it through the chain below to avoid infinite loops or haphazard retries:

1. **Execution stage**  
   When the Agent runs `scripts/vasp_runner.py` or `quick_test.py` via Bash, the underlying command is something like `mpirun -np ... vasp_std`. If the executable is not on PATH, the shell reports `bash: mpirun: command not found` or `bash: vasp_std: command not found`.

2. **Script-level capture**  
   In the local flow, `vasp_runner.py` / `quick_test.py` first verify dependencies (after `source` if `--env-script` is provided); on failure they print a message starting with **`ERROR:`** to **stderr** and exit with a **non-zero exit code**. This output is returned in full to the Agent with the Bash tool result.

3. **system_prompt mandatory rule**  
   Once **`command not found`** or **`ModuleNotFoundError`** appears in tool output, the global rule must be followed: **do not** use the generic `Read` tool on binary files (e.g. PDFs) to sidestep the problem; **do** report the missing dependency to the user and ask for instructions.

4. **Suspend and explain to the user**  
   End this turn with plain text (per CRITICAL INTERACTION RULE / END OF TURN), and **stop** automatically retrying the VASP chain until the user provides environment information. Example:  
   *"Running VASP failed: the system reports `mpirun` (or `vasp_std`) command not found, which usually means the parallel environment has not been loaded or VASP is not on PATH. Would you like me to modify `template/env_local.sh` (or the workspace env script) for your environment, adding `module load` or `export PATH`?"*

---

## Advanced: From Reactive Errors to Proactive Hazard Clearing
Intercepting problems in **Step 2** based on `dependencies` from `probe_env.py` before moving on to Steps 3-4 significantly reduces failures. See `references/orchestration.md` for details.

## Reference Files
- `references/orchestration.md`: login-node taboos, memory, GPU, and dependency probing
- `template/env_local.sh`: local/interactive environment initialization template (uncomment and fill in `module load`, etc.)
- `template/job_slurm.sh`: Slurm template placeholder notes
- `scripts/terminate.py`: targeted termination entry point based on `.vasp_run_state.json`
