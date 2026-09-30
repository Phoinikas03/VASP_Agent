---
name: "workflow-convergence"
description: "Run convergence tests of the cutoff energy ENCUT and the INCAR KSPACING with VASP static single-point energies (NSW=0) on a unit cell with fixed geometry, targeting an energy difference between adjacent steps of ≤1 meV/atom, and output Convergence_Report.md with recommended production parameters. Suitable as the prerequisite step for EOS, band gap scans, general static energy comparisons, or any task that must first converge ENCUT/k-points."
version: "1.1.1"
---

# VASP Cutoff Energy and K-point Convergence (ENCUT & KSPACING Convergence)

You are an expert in computational materials science. This Skill extracts the convergence tests of **ENCUT** and **`KSPACING`** (automatic k-points from INCAR, **no KPOINTS file written**) into a standalone procedure, reusable by `workflow-eos-lattice-constant`, `workflow-electronic-structure`, literature workflows, or any other task that needs an accuracy guarantee for **static single-point energies**.

## Directory Structure

```
workflow-convergence/
├── SKILL.md                          ← This file
├── references/
│   └── convergence_rules.md          ← Scan sequences, 1 meV/atom criterion, caveats
└── templates/
    └── INCAR_static_convergence      ← Static single-point INCAR template (copy, then change parameters)
```

## Applicable Calculation Types

- **Required**: fixed cell and atomic positions (**`NSW = 0`**); only the **total energy** is compared to judge convergence.
- **Typical**: parameter preparation before EOS, static baseline before band gap/band structure, general energy-difference comparisons.
- **Not applicable**: when ions/cell also need to be relaxed, first complete the structure optimization in a skill such as `workflow-relax`, then run this convergence test on the **final structure**.

## Available Tools

- `setup_vasp_inputs`: generate POTCAR and a copy of POSCAR; when **INCAR** contains **`KSPACING`**, **KPOINTS** is **not** generated; when the user explicitly specifies a pseudopotential variant, pass a JSON object via `potcar_overrides` (e.g. `{"Cr": "Cr_pv"}`)
- Skill (`run-vasp`): before running VASP, load its full text and invoke it according to the GPU/CPU rules
- Skill (`vasp-error-recovery`): use when a test point fails, does not converge, appears stuck, or when you need to decide whether to stop the current run first before adjusting parameters and rerunning
- `Write` / `Edit`, `Bash`, `Read` / `Grep`

**VASP launch (mandatory)**: Any step that will execute `mpirun`, directly call `vasp_std` / `vasp_gpu`, or an equivalent command **must first** load the full text of **`Skill: run-vasp`** via a tool call, and determine `--np`, `--exe`, `--gpu-per-task` (and `env_script`) according to its "core execution rules" and the **GPU / CPU** plan the user has confirmed. Production submissions **must go through** `python .claude/skills/run-vasp/scripts/vasp_runner.py`; the assistant is **strictly forbidden** from hand-writing `mpirun -np ...` as the submission command.

Note: this runs in a terminal environment without a GUI. If you need to ask the user a question, **output the question as plain text and stop generating, waiting for the user to reply in the terminal**.

**User confirmation (master gate)**: Without the user's explicit consent, **do not** start any convergence-related VASP calculation in this skill. See §0 below.

---

## Workflow Steps

### 0. Ask the User Whether to Run the Convergence Test (mandatory)

Before reading the rules, preparing subdirectories, or submitting **any** ENCUT/KSPACING test point:

1. Explain to the user in **plain text**: the ENCUT and KSPACING convergence test consists of **multiple** static single-point calculations (`NSW=0`),
   give the approximate number of calculation points and order of magnitude of compute time, and note that downstream accuracy depends on it.
2. **Explicitly ask** the user **whether to run** the full **ENCUT and KSPACING convergence test**.
3. **Stop generating** and wait for the user's reply in the terminal. Proceed to §1 **only** after an **explicit yes** (e.g. "yes", "go ahead", "run it").
4. If the workspace already has a `Convergence_Report.md` that is **trustworthy and applicable to the current system** (same elements, same pseudopotentials,
   same structure type), you may instead ask whether to **reuse** it; if the user agrees, restate its conclusions and reuse it.
5. If the user **declines, defers, or chooses to skip**:
   - **do not** start the convergence scan or any related VASP calculation in this skill;
   - downstream tasks that need **ENCUT/KSPACING** may only use values **explicitly specified** by the user, or template/literature **starting values**;
   - write "**no systematic convergence test was performed**" and the source of the parameters used **near the conclusions** of the final report (not in a footnote);
   - write `CONVERGENCE_SKIPPED.md` in the workspace, recording the reason (user declined), the adopted ENCUT/KSPACING, and their justification.
6. If the conversation **already** contains the user's explicit consent to run the ENCUT/KSPACING convergence test, restate that choice and its cost
   **in one sentence** and proceed to §1, instead of asking again.

---

### 1. Prepare the Structure and INCAR Template

1. Confirm that the working directory contains a reliable **`POSCAR`** (from `Skill: structure-builder` or a user-provided path).
2. `Read templates/INCAR_static_convergence`, copy it as **`INCAR`** or **`INCAR_template`** in the workspace, and set **`ISMEAR` / `SIGMA`**, **`ISPIN` / `MAGMOM`**, etc. according to the system (consistent with subsequent production calculations).
3. If there is no **POTCAR** yet, first call **`setup_vasp_inputs`** (passing `poscar_path` and `incar_path`) to generate the **POTCAR**; **INCAR must contain `KSPACING`** (the template has a placeholder) so that **KPOINTS** is not written. If the user explicitly requests a specific POTCAR variant, you must pass `potcar_overrides` in that tool call; do not manually generate or concatenate POTCARs.

---

### 2. Read the Convergence Criteria

`Read references/convergence_rules.md` to learn:

- the **ENCUT** scan starting point and example sequence;
- the recommended **`KSPACING`** sequence and the lower-bound warning (do not go below **0.08** to avoid an overly large grid);
- the **1 meV/atom** criterion and the convention of "take the larger ENCUT, take the smaller KSPACING".

---

### 3. Submit ENCUT and KSPACING Tests Point by Point

**Goal**: Obtain **`ENCUT`** and **`KSPACING`** satisfying **≤ 1 meV/atom** (between adjacent test steps).

**Execution mode (parallel jobs vs serial within a single job)**:

- **Allowed**: submitting multiple mutually independent test points in parallel as **multiple independent jobs** (one `run-vasp` call/job per subdirectory).
- **Forbidden**: a **single** Bash/Python script, **single** job, or the **same process** running all test points **serially** with `for`/`while`.
- Judgment: read energies only **after** each point's calculation **finishes**; if the next step depends on the previous result, the Agent **reasons step by step**, which does not contradict "multiple points may run in parallel".

**Web / IDE: do not freeze the UI while waiting for this batch of `vasp_runner` (workflow unchanged)**  
You remain **fully responsible** for submission and, after the whole batch finishes, reading OUTCAR / `run-vasp/scripts/check_convergence.py` / the energy table. Launching `vasp_runner.py` requires **`Bash` + `run_in_background: true`**. Single-point single-directory jobs should pass `--log-file` explicitly (e.g. `vasp_encut.log`, `vasp_kspacing.log`); parallel multi-directory jobs should pass `--log-prefix` explicitly so that logs land reliably in each subdirectory. During the waiting phase, **periodic checks** are encouraged: for long jobs, prefer a coarse interval (e.g. 5 minutes), and only check more often near completion or when diagnosing anomalies. You can use **`TaskOutput` + `block: false`** to periodically poll the same `task_id`, or use a **background** Bash with `sleep` to periodically check each subdirectory's `OUTCAR`/processes, repeating until ready before judging energies; avoid **`TaskOutput` + `block: true` + a very long `timeout`** waiting until completion in one call, because this hangs the Web/IDE for the whole turn.

**Suggested directory layout** (in the current workspace root or under `convergence_test/`):

```text
convergence_test/
├── POSCAR
├── INCAR_template
├── encut_test/e_<ENCUT>/
└── kspacing_test/k_<KSPACING>/
```

In each subdirectory: place the corresponding **POSCAR** and **INCAR** (with the **ENCUT** or **KSPACING** for that point), call **`setup_vasp_inputs`** (ensuring **KSPACING** is in INCAR and there is no extra **KPOINTS**; if the user specified a pseudopotential variant, pass the same `potcar_overrides` as well), then submit via **`python .claude/skills/run-vasp/scripts/vasp_runner.py`**. After the calculation:

```bash
# Run from the repository root (consistent with the SKILL & `.claude` PATH RULE in system_prompt):
cd "<Repository root>" && python .claude/skills/run-vasp/scripts/check_convergence.py "<subdirectory containing OUTCAR>"
```

If `electronic_converged` is false, do not use it for the convergence judgment; first adjust **NELM** / **ALGO**, etc. according to `references/convergence_rules.md` and other troubleshooting documents in the project, then recalculate that point.

If a test point exits with a nonzero status, produces no new output for a long time, or you need to decide whether to terminate the old run first, handle it in the following order:

1. First check that point's INCAR, logs, and `references/convergence_rules.md`
2. Then call:

```bash
cd "<Repository root>" && python .claude/skills/vasp-error-recovery/scripts/analyze_error.py --work-dir "<test point subdirectory>"
```

3. Based on the `vasp-error-recovery` output, decide to:
   - keep waiting
   - adjust `NELM` / `ALGO` / mixing parameters and rerun that point
   - or recommend **stopping the current old run for that point first**

4. If stopping the old run first is recommended, you must first present the evidence, the proposed changes, and the new runner command to the user; only after the user explicitly agrees may you run:

```bash
cd "<Repository root>" && python .claude/skills/run-vasp/scripts/terminate.py --work-dir "<test point subdirectory>" --reason "<reason for stopping>"
```

5. **Forbidden**: starting a second active VASP process in the same subdirectory while the old test-point run may still be alive

---

### 4. Write the Report

`Write Convergence_Report.md` (or `convergence_test/Convergence_Report.md`), containing at least:

- the selected **`ENCUT`** and **`KSPACING`**;
- a table of total energies and **ΔE (meV/atom)** for each test step;
- a note on the source of the **POSCAR** used for testing.

---

### 5. Deliverables

Clearly provide to the user or downstream skill:

- the recommended **`ENCUT`** and **`KSPACING`** (and whether **`KGAMMA`** matches the test);
- the path of **`Convergence_Report.md`**;
- a reminder: all subsequent static calculations whose energies must be comparable must use the **same** **ENCUT / KSPACING / POTCAR type**.

---

## Core Principles

- **User confirmation**: Without the user's explicit consent, **do not** run convergence tests or submit related VASP calculations on the user's behalf; §0 is a mandatory step. If the user declines, leave `CONVERGENCE_SKIPPED.md`.
- **Static single points**: Use **`NSW = 0`** throughout the convergence tests; no ionic steps or cell changes at this stage.
- **K-points via INCAR only**: Rely on **`KSPACING`**; **`setup_vasp_inputs`** does not write **KPOINTS** when **`KSPACING`** is present; if an old **KPOINTS** exists in the directory, delete it so it does not override INCAR.
- **No monolithic multi-point VASP**: Running all ENCUT/KSPACING sets sequentially within a single script or single job is forbidden; multiple independent jobs in parallel are allowed.
- **Single-point failures go through `vasp-error-recovery` first**: Once a single point in the convergence test fails, hangs, or produces no new output for a long time, diagnose first, then decide whether to terminate the old run and rerun only that point.
- **Logging conventions**: Use `--log-file` explicitly for a single directory and `--log-prefix` explicitly for multiple directories; do not let key run logs exist only in the outer Bash job output.

---

## Relationship to Other Skills

- **`workflow-eos-lattice-constant`**: The EOS workflow should complete this skill (or an equivalent step) **before** scaling volumes, then fill the resulting **ENCUT/KSPACING** into **`INCAR_static`**.
- Only when the user wants just the converged parameters and not the equilibrium lattice constant, **running this skill alone is sufficient**.
