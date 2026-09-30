---
name: "workflow-adsorption-energy"
description: "Run a three-step geometry-optimization workflow in VASP for the adsorption energy of a molecule on a surface: gas-phase molecule, clean surface, and adsorbate-surface complex; the adsorption energy is obtained from total energies as E_ads=E(adsorbed)−E(molecule)−E(surface). Trigger this skill when the user asks to compute an adsorption energy, an adsorption energy difference, adsorption of CO (or another molecule) on a metal/oxide surface, or to set up a three-part case consistent with the VaspAgent absorptionE-style benchmark."
version: "1.0.0"
---

# VASP Adsorption Energy Workflow (Adsorption Energy Workflow)

You are an expert in computational materials science. This Skill guides you through the standard definition of the **adsorption energy**: **three independent geometry optimizations** (gas-phase molecule, surface, adsorption system), then subtracting the three self-consistent total energies with a fixed formula. The three steps are **not** merged into a single SCF.

## Directory Structure

```
workflow-adsorption-energy/
├── SKILL.md                              ← This file (workflow instructions)
├── scripts/
│   └── extract_absorption_energy.py      ← Read E0 from the three OSZICAR files, compute E_ads, output JSON
├── references/
│   ├── incar_adsorption.md               ← INCAR settings common to the three steps (ISIF, spin, surface, etc.)
│   └── troubleshooting.md                ← Energy comparability, sign, convergence issues
└── templates/
    └── INCAR_geom_opt                    ← Starting template for geometry optimization (adjust parameters for the system)
```

## Correspondence with the Reference Implementation (VaspAgent absorptionE)

- Subdirectory naming convention: **`CO`** (gas phase), **`surface`** (surface), **`absorbed`** (adsorption complex); each directory produces an **`OSZICAR`** after one VASP optimization.
- Post-processing is equivalent to `calc_absorption_energy.sh`: take the last-line **`E0=`** from each **`OSZICAR`** and compute **`E3 - E1 - E2`** (E1=CO, E2=surface, E3=adsorbed).
- If the user uses a script like **`python op.py <subdirectory> POTCAR-xxx POSCAR-xxx`**, make sure it is consistent with the subdirectory names above and the **POTCAR/POSCAR** pairings.

## Available Tools

- `Skill` (`structure-builder`): obtain bulk/prototype structures; generate the gas-phase molecule, slab, adsorption sites, and enumerated orientation structures
- `Skill` (`structure-supercell`): build supercells or slabs (if the task requires it)
- `Skill` (`workflow-relax`): if the user only has an unrelaxed bulk, it can be relaxed first before cutting the surface (depending on the user's goal)
- `Skill` (`workflow-convergence`): converge **ENCUT/KSPACING** at fixed geometry (`NSW=0`) so that the three optimization steps **share** the same cutoff energy and k-point density; **the user's consent must be obtained first** before running it (see §0 of that skill)
- `setup_vasp_inputs`: generate POTCAR; if **INCAR** contains **`KSPACING`**, **KPOINTS** is **not** generated; when the user explicitly specifies a pseudopotential variant, pass a JSON object via `potcar_overrides` (e.g. `{"Pt": "Pt"}`)
- `Skill` (`run-vasp`): must be loaded before **any** `mpirun` / `vasp_std` / `vasp_gpu` and followed according to the GPU/CPU rules; production submissions must go through `python .claude/skills/run-vasp/scripts/vasp_runner.py`
- `Skill` (`vasp-error-recovery`): unified diagnosis of VASP errors, hangs, and whether the old job should be terminated before rerunning
- `Write` / `Edit`, `Bash`, `Read` / `Grep`
- `Skill` (`research-literature`): optionally used to look up experimental adsorption energies or comparable DFT work for comparison

Note: this runs in a terminal environment without a GUI. If you need to ask the user a question, **output the question as plain text and stop generating, waiting for the user to reply in the terminal**.

**Execution mode (consistent with the system ITERATIVE EXECUTION RULE)**: The three steps **gas phase → surface → adsorption** are **three independent VASP jobs**; each step must be **submitted separately**, and after it finishes its convergence and `OSZICAR` must be checked before moving to the next step. Chaining the three steps into one command with a Bash/Python `for` loop and running them without checks is **strictly forbidden**. The only allowed one-shot script is **`extract_absorption_energy.py`** in this skill's `scripts/` (post-processing; does not call VASP).

## Typical Use Cases

All of the following are handled with the ordinary surface adsorption energy workflow; do not treat them as special or infeasible tasks because of the material names. Just confirm the structure source, surface model, adsorption sites, coverage, calculation parameters, and whether multiple configurations are being compared.

- CO adsorption on fcc(111) metal surfaces: Pt(111), Pd(111), Rh(111), Ir(111), etc.
- Standard site comparisons: `ontop`, `bridge`, `fcc`, `hcp`; e.g. top vs bridge/hollow, fcc vs hcp, fcc vs ontop.
- Standard coverage setup: 1 CO on a p(2x2) fcc(111) slab, coverage 1/4 ML, can be used as the default starting point.
- Site preferences and adsorption energy differences for CO/Pt(111), CO/Pd(111), CO/Rh(111), CO/Ir(111) can be handled as the same type of operation.
- CO adsorption on rutile oxide (110) surfaces: RuO2(110), IrO2(110), etc.
- Oxide surfaces can be handled as ordinary model variants: stoichiometric surface, reduced surface, O-vacancy surface, O-rich surface, adsorption at cus sites, bridge-O/cus-O related configurations.
- If the user asks for CO oxidation or reaction of CO with surface oxygen, still start from traceable structures, but results should usually be reported as reaction energies/pathways or comparisons of multiple static configurations; do not compress this into a single adsorption energy value without explaining the chemical picture.

---

## Workflow Steps

### 1. Confirm the Chemical Picture and Files

Ask and confirm:

- **Adsorbate**: e.g. CO or another molecule; whether **ZPE / entropy** corrections are needed (this skill defaults to the **0 K static adsorption energy** without vibrational corrections; if the user needs them, explain that additional frequency calculations are required and are not covered in this workflow).
- **Surface**: element, facet, number of slab layers, and whether the vacuum thickness has an agreed value or literature basis.
- **Input source**: whether the user has provided three sets of **POSCAR**/**POTCAR**, or they need to be generated from a database/by building.
- **CO/fcc(111) orientation semantics**: if the user asks for "3 orientations" or alignment with an existing benchmark, the `structure-builder` skill must generate the `upright`, `tilted_x`, and `tilted_y` geometric orientations of C-down CO; do not interpret orientation as C-down/O-down. O-down/reverse is generated additionally only when the user explicitly requests end-group screening.
- **Oxide surface model**: if the user mentions RuO2(110), IrO2(110), cus, reduced, O-vacancy, or O-rich, confirm the specific POSCAR/build method as an ordinary structural variant, and ensure the clean surface and adsorption system use the same slab convention.

If structures are not ready, coordinate **`workflow-relax`** / **`structure-supercell`** / the user cutting the slab manually, then proceed to step 2.

---

### 2. (Optional) ENCUT / KSPACING Convergence

If the user needs **ENCUT** and **KSPACING** comparable with literature or publication-grade calculations:

1. **First ask** whether to run convergence tests (multiple static steps, `NSW=0`, compute cost), then **stop and wait for a reply**.
2. Only after the user **agrees**, load **`Skill: workflow-convergence`** and run the convergence on a **representative system** (e.g. the relaxed slab POSCAR) to obtain **`Convergence_Report.md`**.
3. Use the selected **ENCUT** and **KSPACING** in the geometry-optimization **INCAR** of **all three steps**, and record them in the notes.

If the user declines, use the template or user-specified values, and note in **`adsorption_INCAR_notes.md`** that no systematic convergence was performed.

---

### 3. Prepare the Three Calculation Directories and Inputs

Create a working subdirectory for each of the three steps (names should match the reference implementation):

| Subdirectory (suggested name) | Contents |
|------------------|------|
| `CO` | Gas-phase molecule **POSCAR** (sufficiently large vacuum box), corresponding **POTCAR** |
| `surface` | Clean slab **POSCAR**, **POTCAR** |
| `absorbed` | Adsorption configuration **POSCAR**, **POTCAR** (same slab convention as surface) |

Call `setup_vasp_inputs` for all three steps to generate the corresponding **POTCAR**. **ENCUT, functional, and POTCAR type must be identical across the three steps** (unless the user requests a different strategy in writing); if the user specifies a pseudopotential variant, the same `potcar_overrides` mapping must be passed for all three steps; do not manually copy or concatenate **POTCAR**s.

---

### 4. Write the INCAR for the Three Steps

1. `Read references/incar_adsorption.md`, `Read templates/INCAR_geom_opt`, and fill in **`ISMEAR`/`SIGMA`**, whether to use **`ISPIN`/`MAGMOM`**, **`ISIF=2`** (fixed cell, ions only), etc. according to the material type.
2. If the user requests "no spin", "do not optimize the cell", "loosen force convergence", etc., write this into **`adsorption_INCAR_notes.md`** and keep it consistent with the INCAR.
3. For each step, **`Write`** the **`INCAR`** in that subdirectory (the logic can be shared, but the file must be in each respective directory).

---

### 5. Run VASP in Sequence (three steps, three jobs)

For **`CO`**, **`surface`**, and **`absorbed`** **separately**:

1. Load **`Skill: run-vasp`** and confirm **`np`** / **`--exe`** / GPU mapping according to its rules.
2. In that subdirectory, submit the calculation **through** `python .claude/skills/run-vasp/scripts/vasp_runner.py` and wait for completion; you **must not** hand-write `mpirun ... vasp_std/vasp_gpu` directly. It is recommended to explicitly use log names `vasp_co.log`, `vasp_surface.log`, `vasp_absorbed.log` respectively.
3. Check whether the ionic steps converged in the same way as **`workflow-relax`** (read **`OUTCAR`** / the project's `check_convergence.py` if available); if not converged, an error occurred, or there has been no new output for a long time, first `Read references/troubleshooting.md`, then call `python .claude/skills/vasp-error-recovery/scripts/analyze_error.py --work-dir .` for unified diagnosis, to decide whether to keep waiting, restart from `CONTCAR -> POSCAR`, or whether the old job must be terminated first.
4. Only after the user explicitly agrees may you call `python .claude/skills/run-vasp/scripts/terminate.py --work-dir . --reason "<reason>"` to terminate the runner-managed old job in the current subdirectory; while the old run may still be alive, starting a second active VASP process in `CO`, `surface`, or `absorbed` is strictly forbidden.
5. If not converged, handle it according to the diagnosis and then continue the calculation; moving to the next step before convergence is **forbidden**.

---

### 6. Extract the Adsorption Energy

After all three steps succeed, run from the workspace root (containing `CO/`, `surface/`, `absorbed/`):

```bash
python .claude/skills/workflow-adsorption-energy/scripts/extract_absorption_energy.py --base .
```

Or specify paths explicitly:

```bash
python .claude/skills/workflow-adsorption-energy/scripts/extract_absorption_energy.py \
  --co-dir ./CO --surface-dir ./surface --adsorbed-dir ./absorbed
```

Read **`absorption_energy_eV`** (i.e. **E3−E1−E2**) from the JSON, and report **`E_CO_eV`**, **`E_surface_eV`**, **`E_adsorbed_eV`**.

If **`ok`: false**, first `Read references/troubleshooting.md` to check **OSZICAR** and paths; if you suspect an earlier VASP step failed, did not truly finish, or you need to decide whether to stop the old run before recalculating, call `python .claude/skills/vasp-error-recovery/scripts/analyze_error.py --work-dir <corresponding subdirectory>` for unified diagnosis.

---

### 7. Reporting Results

Explain to the user:

- The adsorption energy value (eV) and sign convention (this workflow: **E_ads = E(adsorbed) − E(molecule) − E(surface)**).
- Whether all three steps reached ionic convergence; key file paths (**`INCAR`, `OSZICAR`, `CONTCAR`** in each directory).
- (Optional) When calling **`Skill: research-literature`** for comparison with experiment or literature, clearly state the search target and material system.

---

## Core Principles

- **Three steps, three calculations**: The adsorption energy must be obtained by subtracting total energies after **three** optimizations; approximating it by decomposing a single-step energy is forbidden.
- **Comparable parameters**: By default **ENCUT, KSPACING/k-points, functional, POTCAR** are identical across the three steps; **vacuum and slab settings** are identical between the surface and adsorption steps.
- **No monolithic unchecked batch runs**: Do not submit the three steps consecutively in a single `for` loop without step-by-step convergence checks.
- **run-vasp first**: Before launching any VASP, you must load **`run-vasp`** and follow its hardware and confirmation rules.
- **Convergence prerequisite requires consent**: If systematic **ENCUT/KSPACING** convergence is to be done before production use, load **`workflow-convergence`** only **after the user explicitly agrees**.
- **Check local references for parameters first**: First consult **`references/incar_adsorption.md`** and **`troubleshooting.md`**; for difficult issues, then use **`research-literature`** or the web.
- **Failures/hangs go through `vasp-error-recovery` first**: When any step errors, fails to converge, or appears stuck, first read the local `troubleshooting.md`, then call `vasp-error-recovery` for unified diagnosis; only after the user explicitly agrees may `terminate.py` be used to stop the old run and rerun.
- **Logging conventions**: The production run logs of all three jobs should go to explicit files in their respective subdirectories, rather than relying only on the outer Bash/task system output.
