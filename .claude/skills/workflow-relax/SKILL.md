---
name: "workflow-relax"
description: "Automated workflow for VASP structural relaxation calculations. Triggered when the user asks to perform a structural relaxation, optimize a crystal structure, or prepare VASP input files for a specific material."
version: "2.2.0"
---

# VASP Structure Relaxation Workflow

You are an expert in computational materials science. This Skill guides you through automating the preparation of input files, execution of the calculation, and analysis of results for a VASP structural relaxation.

## Directory Structure

```
relax/
├── SKILL.md                          ← this file (workflow instructions)
├── scripts/
│   ├── check_convergence.py          ← checks ionic/electronic convergence status, outputs JSON
│   └── analyze_result.py             ← extracts final energy, forces, pressure, volume, outputs JSON
├── references/
│   ├── incar_params.md               ← empirical INCAR parameter tables by material type (incl. DFT+U)
│   └── troubleshooting.md            ← common errors and fixes
└── templates/
    ├── INCAR_relax_full              ← ISIF=3 full relaxation template (atoms + cell)
    └── INCAR_relax_ions              ← ISIF=2 template relaxing atomic positions only
```

## Available Tools

- `Skill` (`structure-builder`): fetch a POSCAR from a Materials Project ID, or build/validate the initial structure
- `duckduckgo_search` / `google_search`: search documentation, parameter recommendations, and error solutions
- `visit_webpage`: extract the full text of a web page
- `Skill` (`research-literature`): search the literature for DFT calculation parameters and experimental reference values for a specific material
- `Skill` (`workflow-convergence`): run static single-point (`NSW=0`) **ENCUT** and **`KSPACING`** convergence tests (1 meV/atom) on a **fixed POSCAR**, producing **`Convergence_Report.md`**. Use it when the cutoff energy and k-point mesh must be determined rigorously, or aligned with parameters of subsequent static/EOS/band gap calculations; it does **not** replace the ionic relaxation in this skill
- `Skill` (`vasp-error-recovery`): use when the relaxation fails, does not converge, appears stuck, or when you need to decide whether to stop the current run before changing parameters and rerunning
- `setup_vasp_inputs`: generates POTCAR and a copy of POSCAR; if **INCAR** contains **`KSPACING`**, k-points are defined by INCAR alone and **no** **KPOINTS** is generated; otherwise **KPOINTS** is generated from `kpoints_density`; when the user explicitly specifies a pseudopotential variant, pass it via `potcar_overrides` as a JSON object (e.g. `{"Cr": "Cr_pv"}`)
- `Write` / `Edit`: create and modify workspace files
- `Bash`: file management, running post-processing scripts
- `Read` / `Grep`: read logs and output files

Note: this runs in a terminal environment without a GUI. If you need to ask the user a question, **output the question as plain text and stop generating, waiting for the user to reply in the terminal**.

**Execution mode (consistent with the system ITERATIVE EXECUTION RULE)**: each VASP run (including **restarts**: a new round after `CONTCAR`->`POSCAR`) must be submitted **individually**, and convergence must be checked before continuing; it is **strictly forbidden** to use a Bash/Python `for` loop or a single command to run multiple relaxations / multiple INCAR trials in the background all at once. The only one-shot scripts allowed are the tools listed under this skill's `scripts/` (e.g. `check_convergence.py`, `analyze_result.py`).

---

## Workflow Steps

### 1. Obtain the Initial Structure

Ask the user which of the following applies, and wait for a reply:

- **A. Material name provided**: search for the Materials Project ID, then use `Skill: structure-builder` to download or build the POSCAR
- **B. mp-id provided**: directly use `fetch_mp_poscar.py` from `Skill: structure-builder` to download the POSCAR
- **C. POSCAR already available**: ask the user for the exact file path

---

### 2. Determine the Material Type and INCAR Parameters

Before writing the INCAR, consult the local reference documents first:

1. `Read references/incar_params.md` and, based on the material (metal/semiconductor/insulator/magnetic/2D/strongly correlated), determine:
   - `ISMEAR` / `SIGMA`
   - whether `ISPIN` + `MAGMOM` are needed
   - whether `LDAU` + `LDAUU` (DFT+U) are needed
   - whether `IVDW` (vdW correction) is needed
   - the `ISIF` choice (3 for full optimization, 2 for fixed lattice)

2. Only when the material is unusual (novel perovskites, rare-earth compounds, etc.) and not covered by the reference documents, call `Skill: research-literature` and state clearly:
   - **Search target**: calculation parameters (which parameters are needed, e.g. ENCUT, LDAUU, IVDW)
   - **Material system**: chemical formula or material name (e.g. `"BiFeO3"`)
   - **Write target**: append the returned citation block to `INCAR_explanation.md` in this workspace

3. **(Optional) ENCUT / KSPACING convergence**: if you want **ENCUT** and **KSPACING** that are **strictly comparable** with the literature or with subsequent calculations (EOS, band gap, static energy), then **before** writing the relaxation INCAR you **must first ask the user** whether to run **ENCUT/KSPACING convergence tests** (explain that it involves multiple static calculations, time, and compute hours), and **stop and wait for a reply**. Do **not** assume execution or automatically load the convergence skill without the user's consent. If the user **agrees**, load **`Skill: workflow-convergence`** (that skill includes its own pre-execution user confirmation step), run static single-point convergence on the **current POSCAR**, and fill the parameters from **`Convergence_Report.md`** into the relaxation **INCAR**. If the user **declines** or only cares about the geometry optimization, use the template's default **ENCUT/KSPACING** and note in **`INCAR_explanation.md`** that no systematic convergence test was done.

---

### 3. Generate the INCAR and Explanation Document

1. `Read templates/INCAR_relax_full` (or `INCAR_relax_ions`, depending on ISIF) and fill in the material-specific parameters determined in Step 2 (the template already contains **`KSPACING`** / **`KGAMMA`**; adjust `KSPACING` according to the system's convergence needs)
2. `Write INCAR` (to the workspace)
3. `Write INCAR_explanation.md`, recording the rationale and reference source for each key parameter choice

---

### 4. Complete the Input Files

Call `setup_vasp_inputs` with `poscar_path` and `incar_path` to generate the POTCAR automatically (and no **KPOINTS** when the INCAR contains **`KSPACING`**). If the user explicitly requests a specific POTCAR variant, pass `potcar_overrides` in the same tool call, e.g. `{"Cr": "Cr_pv"}`; if the tool rejects the override, stop and report the error to the user; do not fall back to generating, concatenating, or copying the POTCAR manually with Bash/Python.

Only when **`KSPACING` is not set in the `INCAR`** should you rely on `kpoints_density` to generate **KPOINTS**; for metals and other systems needing a denser mesh, **prefer** tightening the **`KSPACING`** value rather than just increasing `kpoints_density`.

---

### 5. Run the VASP Calculation

Following Skill `run-vasp`, submit the calculation **by calling `python .claude/skills/run-vasp/scripts/vasp_runner.py` via Bash**; do **not** hand-write `mpirun ... vasp_std/vasp_gpu` directly. Single-directory relaxation jobs should explicitly pass `--log-file vasp_relax.log`; for a restart, keep using this log name or state the new log file name clearly when reporting to the user.

While waiting for completion, follow the repository's **LOCAL COMPUTE** rules: **periodic checks** are encouraged; for long jobs prefer a coarser check interval (e.g. 5 minutes), tightening it temporarily only when needed; avoid prolonged **`TaskOutput` + `block: true`** calls that freeze the Web/IDE. Prefer periodic polling with **`block: false`** or background Bash checks with `sleep` until the job finishes, then run `check_convergence.py` etc.

---

### 6. Check Convergence Status

After the calculation finishes:

```bash
python scripts/check_convergence.py .
```

Judge based on the JSON output:

| Field | Expected value | Action if not met |
|------|--------|--------------|
| `ionic_converged` | `true` | Copy CONTCAR to POSCAR, increase NSW, and restart; see `references/troubleshooting.md` |
| `nsw_reached` | `false` | If `true` while `ionic_converged` is `false`, restart as above |
| `electronic_converged` | `true` | Check the `errors` field; adjust ALGO/NELM per `troubleshooting.md` |
| `contcar_exists` | `true` | If `false`, the calculation exited abnormally; check `errors` and `last_lines` |

If you encounter errors, non-convergence, or a log that has not updated for a long time, the handling order must be:

1. First `Read references/troubleshooting.md`
2. Then call:

```bash
cd "<Repository root>" && python .claude/skills/vasp-error-recovery/scripts/analyze_error.py --work-dir "<current relaxation directory>"
```

3. Based on the structured output of `vasp-error-recovery`, decide:
   - whether to simply keep waiting
   - whether to modify parameters such as `NELM` / `ALGO` / `NSW` / `POTIM` and restart
   - whether it recommends **stopping the current run first**

4. If `vasp-error-recovery` recommends stopping the old run first, you **must first explain the evidence and the proposed changes to the user, and wait for the user's explicit consent**. Only after the user explicitly agrees may you run:

```bash
cd "<Repository root>" && python .claude/skills/run-vasp/scripts/terminate.py --work-dir "<current relaxation directory>" --reason "<reason for stopping>"
```

5. **Do not** start a new `vasp_runner.py` / `mpirun` in the same directory while the old run may still be alive

---

### 7. Extract Results

After the calculation succeeds:

```bash
python scripts/analyze_result.py .
```

Read the following from the JSON output and report them to the user:
- `final_energy_eV` / `final_energy_per_atom`: final total energy
- `max_force_eV_A`: maximum atomic force (should be smaller than the absolute value of EDIFFG)
- `pressure_kbar`: final pressure (should be close to 0 for an ideal relaxation)
- `volume_A3`: final cell volume
- `contcar_path`: path of the relaxed structure file (input for subsequent calculations)

---

### 8. Report Results

Report to the user:
- whether the relaxation converged, the final energy, and the maximum force
- the location of the final structure file (`CONTCAR`; subsequent calculations should start from it)
- the locations of all key files: `INCAR`, `INCAR_explanation.md`, `CONTCAR`, `OUTCAR`
- whether follow-up calculations are recommended (e.g. electronic structure or high-accuracy band gap calculations, via `Skill: workflow-electronic-structure`)

---

## Core Principles

- **No monolithic loops for batch VASP runs**: do not write Bash/Python with `for`/`while` that submits multiple relaxations or multiple parameter sets at once; every calculation or restart must be submitted individually and checked before moving to the next step.
- **Check local references for parameters first**: consult `references/incar_params.md` and `troubleshooting.md` first; when not covered locally, call `Skill: research-literature` rather than calling `arxiv_search` or search tools directly, so that results are structured and cited.
- **ENCUT/k-point convergence**: when production-level **ENCUT** and **KSPACING** (1 meV/atom) are needed, **obtain the user's consent first** before entering **`workflow-convergence`**; that procedure uses **static single-point** calculations, separate from the ionic relaxation, and its parameters are merged into the relaxation **INCAR** afterward.
- **Physical rigor**: always keep track of the material's electronic structure class (metal/semiconductor, magnetic/non-magnetic) and make sure parameters such as ISMEAR/MAGMOM are set sensibly.
- **Restart rather than recompute**: when the ionic steps have not converged, copy CONTCAR to POSCAR and restart, instead of starting from scratch.
- **On failure/hang, go through `vasp-error-recovery` first**: if the relaxation fails, produces no new output for a long time, or you are about to decide whether to terminate the old run, use `vasp-error-recovery` for evidence-based diagnosis first; `terminate.py` may only be called after the user explicitly agrees.
- **Transparent steps**: after completing each major milestone (obtaining the structure, generating the INCAR, finishing the convergence check), briefly report progress to the user.
- **Persist logs to disk**: production calculation logs should preferably go to an explicit file in the working directory (e.g. `vasp_relax.log`), not only remain in the outer Bash/task system output.
