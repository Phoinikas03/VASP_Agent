---
name: "workflow-eos-lattice-constant"
description: "Run VASP equilibrium lattice constant calculations and equation of state (EOS) fitting. Before the EOS, obtain the user's consent and then converge ENCUT/KSPACING via workflow-convergence; otherwise use user-specified or template parameters and note this. Then apply isotropic scaling, run batched static calculations, do an initial EOS fit, and, based on fit quality, boundary position, and local grid spacing, decide whether to refine sampling around the minimum, finally obtaining the equilibrium lattice constant, volume, and bulk modulus."
---

# VASP Equilibrium Lattice Constant and EOS Workflow (Lattice Constant & EOS Workflow)

You are an expert in computational materials science. This Skill guides you through automated, high-accuracy lookup and calculation of the equilibrium lattice constant of solid materials. Fitting the energy-volume curve with an equation of state (Equation of State, EOS) is the gold standard for finding a material's ground-state stable structure.

## Directory Structure

workflow-eos-lattice-constant/
├── SKILL.md                       ← This file (EOS workflow)
├── scripts/
│   ├── generate_scaled_poscars.py ← Batch-generate strained POSCARs from a list of scale factors or a center/step
│   └── fit_eos.py                 ← Extract batch-calculation energies, fit the EOS, and output a_eq plus refinement suggestions
├── references/
│   ├── convergence_rules.md       ← Placeholder: points to workflow-convergence/references/convergence_rules.md
│   └── troubleshooting.md         ← EOS fitting and calculation issues
└── templates/
    └── INCAR_static               ← High-accuracy static INCAR template (fill in the converged ENCUT/KSPACING)

For the full criteria and procedure of **cutoff energy and k-point convergence**, see the standalone skill **`workflow-convergence`**.

## Available Tools

- `duckduckgo_search` / `Google Search`: search for experimental lattice constants and space group information
- `Skill` (`workflow-convergence`): **ENCUT / KSPACING** convergence tests (1 meV/atom), producing **`Convergence_Report.md`**
- `Skill` (`research-literature`): look up reliable experimental lattice constants and standard EOS fitting literature for a specific material
- `Skill` (`structure-builder`): generate, retrieve, or validate the initial POSCAR for a specific material
- `setup_vasp_inputs`: generate POTCAR; if **INCAR** contains **`KSPACING`**, **KPOINTS** is **not** generated; when the user explicitly specifies a pseudopotential variant, pass a JSON object via `potcar_overrides` (e.g. `{"Cr": "Cr_pv"}`)
- Skill (`run-vasp`): run VASP via Bash / (when needed) **non-blocking** `TaskOutput` / the job scheduler, following that skill and the system orchestration rules (the MCP tool `run-vasp` has been removed; do not call it)
- Skill (`vasp-error-recovery`): unified diagnosis of VASP errors, hangs, and whether the old job should be terminated before rerunning
- `Write` / `Edit`: create and modify workspace files
- `Bash`: file management, running pre- and post-processing scripts
- `Read` / `Grep`: read logs and output files

**VASP launch (mandatory)**: Any step that will execute `mpirun`, directly call `vasp_std` / `vasp_gpu`, or an equivalent command **must first** load the full text of **`Skill: run-vasp`** via a tool call, and determine `--np`, `--exe`, `--gpu-per-task` (and `env_script`) according to its "core execution rules" and the **GPU / CPU** plan the user has confirmed. Production submissions must go **through** `python .claude/skills/run-vasp/scripts/vasp_runner.py`; the assistant is **strictly forbidden** from hand-writing `mpirun -np 16` (or other commands) to bypass the runner. When the user has stated they use the **GPU build of VASP** (the executable may still be named `vasp_std`), follow the rule in `run-vasp`: **GPU: usually 1 rank ↔ 1 GPU; a single-GPU single job uses `np=1` together with device binding**; do not allocate `-np` using pure-CPU logic.

Note: this runs in a terminal environment without a GUI. If you need to ask the user a question, **output the question as plain text and stop generating, waiting for the user to reply in the terminal**.

### Web / IDE: do not freeze the UI during long calculations (workflow unchanged)

This skill and the prerequisite **`workflow-convergence`**'s **`vasp_runner.py`** (which can run multiple `--dirs` **in parallel**) are still **fully orchestrated** by you: submission, polling until completion, then reading energies / running `run-vasp/scripts/check_convergence.py` / `fit_eos.py`, etc. — **none of these steps may be skipped**.

To comply with the repository **system_prompt**'s **LOCAL COMPUTE** rule and avoid a stuck session:

1. **Launch**: For **`vasp_runner.py`** (and any VASP command lasting minutes), use **`Bash` with `run_in_background: true`**. For a single point in a single directory, pass `--log-file` explicitly; when running multiple `scale_*` directories in parallel, pass `--log-prefix` explicitly.
2. **Waiting for completion**: **Periodic checks** are encouraged rather than frequent refreshing. For long jobs, prefer a coarse polling interval (e.g. 5 minutes), and only poll more often near completion or when troubleshooting. Avoid using **`TaskOutput` + `block: true` + a very long `timeout`** (e.g. hundreds of thousands of ms) to wait for VASP to finish within a **single tool call**, because this freezes the **Web / IDE** for the whole turn. **Instead** use **`TaskOutput` + `block: false`** to periodically poll the same `task_id` until `completed` / failure; if the environment does not support non-blocking `TaskOutput`, use Bash to periodically check whether each target directory's `OUTCAR` has a final energy line and whether the relevant `vasp`/`mpirun` processes are still alive, until all jobs in the batch are ready, then proceed to reading results and convergence checks.
3. Waiting logic with `sleep` may be used, but it is better placed in a **background** Bash or helper script, balancing periodic checks with UI responsiveness (consistent with system_prompt).

The above only changes **how you wait**; it does not change: the set of parallel directories, per-point checks, the ban on running multiple VASP points serially inside a monolithic `for` loop, and similar rules.

---

## Workflow Steps

### 1. Confirm Inputs and Initial Structure

1. Confirm whether the user has provided the target material's **elemental composition** and **crystal structure type** (e.g. FCC, BCC, diamond structure). If not, ask the user and wait for a reply.
2. Look up experimental reference values: call `Skill: research-literature` to obtain the material's experimental lattice constant.
3. Build the initial structure: use `Skill: structure-builder` or a script to generate the initial `POSCAR` based on the experimental lattice constant.

---

### 2. Cutoff Energy and K-point Convergence (prerequisite: standalone Skill + user confirmation)

**The detailed rules are not expanded in this file.** Before loading **`workflow-convergence`**:

1. **Ask the user** whether to run the **ENCUT/KSPACING convergence test** (multiple static calculations, compute cost; EOS accuracy usually depends on a sensible **ENCUT/k-point** setting), then **stop and wait for a reply**. Automatically starting convergence, or assuming it will be done, without the user's consent is **forbidden**.
2. If the user **agrees**, load and run **`Skill: workflow-convergence`** (which contains its own pre-run user confirmation) to complete:
   - the **ENCUT** and **`KSPACING`** scans with static single points (`NSW=0`);
   - **`Convergence_Report.md`** (with the selected **ENCUT**, **KSPACING**, and energy tables).
3. If the user **declines**, or the workspace already has a trustworthy **`Convergence_Report.md`**: you may **ask** whether to **reuse** the existing report; otherwise use the user-**specified** or template **ENCUT/KSPACING** below, and leave a record as required by `workflow-convergence` §0 item 5 (no systematic convergence performed or redone).

Fill the finally adopted **ENCUT** and **KSPACING** into **`templates/INCAR_static`** and the **INCAR** of every **`scale_*`** subdirectory.

If the user **only** needs converged parameters and not the equilibrium lattice constant, **running `workflow-convergence` alone is sufficient**; there is no need to continue with step 3 onward of this skill.

---

### 3. Generate Volume-Scaled Structures (Volume Scaling)

**Goal**: Generate a series of isotropically scaled cells around the equilibrium volume to map out the energy well.

1. Set the first-round list of scale factors, aiming first to bracket the energy minimum. If the experimental/literature lattice constant is reliable, a narrower 7-9 point grid is usually used; if the initial structure is uncertain, use a wider coarse scan, e.g.: `0.94, 0.96, 0.98, 1.00, 1.02, 1.04, 1.06`.
2. `Bash`: `python scripts/generate_scaled_poscars.py --poscar POSCAR --scales 0.94 0.96 0.98 1.00 1.02 1.04 1.06`
3. Check that subfolders were successfully generated in the current directory (e.g. `scale_0.940/`, `scale_0.960/` ...), each containing the corresponding `POSCAR`.

---

### 4. Batched Static Calculations (Production Runs)

**Goal**: Obtain accurate total energies of the system at all volumes/scale factors.

For each `scale_x.xxx` subfolder, prepare inputs and submit the calculation (directories may be submitted **in parallel** as multiple independent jobs; a single script running all scales serially in a `for` loop within one process is **forbidden**):
1. Copy the template and configuration: copy `templates/INCAR_static` into the current subdirectory and fill in the **`ENCUT`** and **`KSPACING`** obtained from **`workflow-convergence`**.
2. Call `setup_vasp_inputs` to prepare a POTCAR consistent with the convergence test; **INCAR** must contain the same **`KSPACING`** as the convergence test so that **KPOINTS** is not generated. If the user specifies a pseudopotential variant, all `scale_*` subdirectories must receive the same `potcar_overrides` mapping; do not manually copy or concatenate POTCARs.
3. Following Skill `run-vasp` and the orchestration rules, **call `python .claude/skills/run-vasp/scripts/vasp_runner.py` via Bash** (`run_in_background: true`) together with the **Web / IDE waiting rules** above to submit and complete one VASP calculation **separately** in that subdirectory (one job per point); prolonged blocking `TaskOutput` that freezes the session is **forbidden**, and you **must not** hand-write `mpirun ... vasp_std/vasp_gpu` directly.
4. After the calculation in that directory finishes, `Bash`: `python .claude/skills/run-vasp/scripts/check_convergence.py .`
   - Confirm `electronic_converged: true`.
   - If not converged, an error occurred, or there has been no new output for a long time, first `Read references/troubleshooting.md`, then call `python .claude/skills/vasp-error-recovery/scripts/analyze_error.py --work-dir .` for unified diagnosis, to decide whether to keep waiting, modify parameters and rerun, or whether the old job must be terminated first.
   - Only after the user explicitly agrees may you call `python .claude/skills/run-vasp/scripts/terminate.py --work-dir . --reason "<reason>"` to terminate the runner-managed old job in the current directory; while the old run may still be alive, starting a second active VASP process in the same `scale_*` directory is strictly forbidden.

---

### 5. Equation of State Fitting (EOS Fitting)

**Goal**: Find the analytic energy minimum from the first-round discrete volume-energy data points, and decide whether refined sampling is needed.

1. Make sure the VASP calculations for all scaled jobs have finished normally.
2. `Bash`: `python scripts/fit_eos.py --dirs scale_* --eos_type birch_murnaghan --reference-poscar POSCAR`
3. Read the results from the JSON output by the script:
   - `V_0_Ang3`: equilibrium volume
   - `E_0_eV`: minimum system energy
   - `B_0_GPa`: bulk modulus (Bulk Modulus)
   - `linear_scale_eq`: equilibrium linear scale factor relative to the reference `POSCAR`
   - `a_eq_A` / `lattice_lengths_eq_A`: calculated equilibrium lattice constant or lattice vector lengths
   - `R_squared`: goodness of fit
   - `refinement_recommended`, `refinement_reasons`, `suggested_new_refinement_scales`: whether additional points should be added around the minimum

---

### 6. Refined Sampling and Final Fit (Refinement)

**Goal**: Avoid an unstable equilibrium lattice constant and bulk modulus caused by an overly wide or coarse first-round grid.

1. Perform refined sampling if any of the following conditions holds:
   - `refinement_recommended: true`;
   - the discrete lowest-energy point lies on the boundary of the first-round scales;
   - the fitted `V_0_Ang3` is close to or outside the sampled volume range;
   - `R_squared < 0.999`, the residuals show a systematic bias, or the bottom of the curve is clearly determined by too few points;
   - the first-round scale spacing is coarse (e.g. `0.02`) and the user needs a high-accuracy lattice constant or bulk modulus.
2. Refined sampling is centered on the **fitted `linear_scale_eq`**, not just on the discrete lowest-energy point. Usually add 5-7 points with a linear scale step of `0.003-0.005`; by default you can use:
   - `python scripts/generate_scaled_poscars.py --poscar POSCAR --center-scale <linear_scale_eq> --step 0.005 --points 5`
   - or directly use `suggested_new_refinement_scales` output by `fit_eos.py`, generating only new scales that have not been calculated yet.
3. Repeat step 4 for the new `scale_*` subdirectories: prepare the same `INCAR/POTCAR/KSPACING`, submit static single points directory by directory, and check each point with `check_convergence.py`. Do not write the new points as one script that runs them serially in the same VASP process.
4. After the new points finish, rerun the fit over all valid directories from both the first round and the refinement:
   - `python scripts/fit_eos.py --dirs scale_* --eos_type birch_murnaghan --reference-poscar POSCAR`
5. If `refinement_recommended: false`, the minimum is clearly bracketed, the local scale spacing is dense enough, and the fit residuals are small, refined sampling may be skipped; state "refined sampling not triggered" in the results report.

---

### 7. Reporting and Verification

Report to the user:
- The optimal calculation parameters (ENCUT and k-point grid used).
- The calculated equilibrium lattice constant a_eq and bulk modulus B_0.
- Goodness of fit and refinement status: whether refined sampling was performed; if `R^2 < 0.999` or `fit_eos.py` still recommends refinement, explain the residual risk; if `R^2 < 0.99`, strongly warn that the curve may not contain the energy minimum or may contain outliers.
- Comparison with experiment: compare the calculated result with the experimental lattice constant retrieved in step 1 and compute the percentage error `Error (%) = |a_calc - a_exp| / a_exp * 100%`.

---

## Core Principles

- **Convergence tests require user confirmation**: Before loading **`workflow-convergence`** you **must** ask the user whether to proceed; automatically starting convergence tests is **forbidden**.
- **No serial multi-point VASP within a single job**: Apart from explicitly allowed scripts such as `generate_scaled_poscars.py`, `fit_eos.py`, and `run-vasp/scripts/check_convergence.py`, do not use **one** Bash/Python script or **one** job submission to loop over or sequentially run multiple VASP calculations in the **same process/same job**. It **is allowed** to submit multiple single-point VASP runs **in parallel** as multiple independent jobs; each job is still one point, one directory, one set of inputs, and each is checked separately afterwards with the generic `check_convergence.py`, etc.
- **Strictly identical parameters**: In the batched calculations of step 4, `ENCUT`, the `POTCAR` choice, and the k-point grid scheme must be **exactly identical** across all subjobs. Changing the basis-set cutoff introduces large errors from Pulay stress.
- **Static calculations first**: During EOS fitting, the VASP calculation at each scale must be a **single-point static calculation (ISIF=2 and NSW=0)**; no further cell-volume relaxation may be performed inside it, otherwise the energy-volume correspondence breaks down.
- **Refined sampling is evidence-driven**: The first-round coarse scan is only for bracketing the minimum; if the fitted minimum is near the edge, the local grid is coarse, `R_squared` is insufficient, or the user requires high accuracy, you must add 5-7 static points near `linear_scale_eq` and refit.
- **Outlier removal**: If `fit_eos.py` reports an error or the fitted curve has outliers clearly deviating from the parabolic bottom (e.g. energy distortion from SCF not truly converging), you must recheck that point's `OUTCAR`.
- **Failures/hangs go through `vasp-error-recovery` first**: If a `scale_*` point errors, fails to converge, or appears stuck, first read the local `references/troubleshooting.md`, then call `vasp-error-recovery` for unified diagnosis; only after the user explicitly agrees may `terminate.py` be used to stop that point's old run and rerun.
- **Logging conventions**: When running EOS directories in batch, use `--log-prefix` explicitly so that each `scale_*` subdirectory has a stable, traceable run log.
