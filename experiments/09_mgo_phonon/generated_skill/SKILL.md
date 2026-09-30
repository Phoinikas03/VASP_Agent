---
name: "workflow-phonon"
description: "Run the VASP phonon workflow: structural relaxation → supercell finite-displacement force calculations → phonopy force-constant fitting → phonon dispersion and density of states (with NAC correction for the LO-TO splitting) → comparison with experiment. Trigger when the user wants phonon dispersion curves, phonon density of states (phonon DOS), phonon frequencies, phonon spectra, lattice dynamics, NAC/LO-TO splitting, or asks for phonon band/DOS plots. Uses phonopy (v4+) with the VASP finite-displacement method; ionic crystals require a DFPT calculation of Born effective charges + dielectric tensor. run-vasp must be used before running VASP, and incar-performance must be used first when GPU/KPAR/NCORE are involved."
---

# VASP Phonon Workflow (Phonon Dispersion & DOS)

You are an expert in computational materials science. This skill guides you through automating the calculation of phonon dispersion and phonon density of states of solids. The core method is the **finite-displacement (frozen-phonon) method**: build a supercell on the equilibrium structure, apply small displacements to symmetry-inequivalent atoms, compute the Hellmann-Feynman forces of the displaced configurations with VASP, then pass them to **phonopy** to fit harmonic force constants, compute the dispersion along a high-symmetry path, and reproduce the LO-TO splitting of ionic crystals near Γ with the **NAC (non-analytical correction, BORN file)**.

## When to Trigger

- The user asks for **phonon dispersion curves / phonon spectrum / phonon dispersion**
- The user asks for the **phonon density of states / phonon DOS**
- The user asks for **phonon frequencies, lattice dynamics, lattice vibrations, thermodynamic properties** (lattice heat capacity, zero-point energy)
- The user wants a phonon calculation on a known structure, especially an **ionic crystal** (LO-TO splitting required)
- Benchmark calculations to be compared with experimental (INS / Raman / IR) phonon frequencies

## Existing Skills This Depends On (load first)

This skill does **not** handle VASP input generation or execution. Before executing, load:

- **`Skill: run-vasp`**: **must** be loaded before any VASP / mpirun / vasp_gpu / vasp_runner.py run. Follow its probes, STRICT HARDWARE ALIGNMENT (GPU vs CPU, 1 rank↔1 GPU), and the `vasp_runner.py --dirs` batching strategy.
- **`Skill: incar-builder`**: **must** be loaded before writing or editing INCAR.
- **`Skill: structure-builder`**: **must** be loaded before obtaining/constructing POSCAR (this skill never writes POSCAR from memory).
- **`Skill: incar-performance`**: load before anything involving GPU / KPAR / NCORE / NPAR.
- **`Skill: incar-smearing-precision`**, **`Skill: incar-validator`**: load when setting and validating precision.
- **`Skill: workflow-relax`**: equilibrium structure relaxation (Stage A).
- **`Skill: workflow-convergence`**: ENCUT / KSPACING convergence tests (determine production ENCUT / KSPACING).
- **`Skill: research-literature`**: search for experimental phonon frequencies as a comparison benchmark; `duckduckgo_search` / `semanticscholar_search` / `arxiv_search` can also be used.

## Directory Layout

```
workflow-phonon/
├── SKILL.md                ← this file (phonon workflow)
├── scripts/
│   ├── build_conventional_cell.py  ← pymatgen primitive → conventional cell (if the unit cell is primitive / low symmetry)
│   ├── get_born_params.py          ← build the phonopy nac_params dict (parse BORN manually, avoiding the parse_BORN pitfalls)
│   ├── plot_phonon_dos.py          ← dispersion + DOS plot from FORCE_CONSTANTS + BORN (v4 Python API, with NAC)
│   └── extract_phonon_report.py    ← extract high-symmetry-point frequencies and DOS peaks for comparison with experiment
└── references/
    └── vasp_phonopy_settings.md   ← key INCAR tags for each phonon stage (DFPT/forces/convergence)
```

## Workflow Overview

```
Stage A  Structural relaxation      → workflow-relax gives the equilibrium lattice constant
Stage B  Convergence tests          → workflow-convergence fixes ENCUT / KSPACING (1 meV/atom)
Stage C  Primitive vs conventional  → decide the supercell scheme and the BORN correspondence
Stage D  DFPT dielectric/Born       → LEPSILON static run → phonopy-vasp-born writes BORN (no BORN → skip NAC)
Stage E  Displaced supercells       → phonopy-init --dim ... -d generates SPOSCAR + POSCAR-###
Stage F  Batch force calculations   → vasp_runner.py runs a static force calculation per displacement (IBRION=-1, NSW=0)
Stage G  Extract FORCE_SETS         → phonopy-init -f vasprun.xml...
Stage H  Fit FORCE_CONSTANTS        → phonopy --writefc --fc-spg-symmetry
Stage I  Dispersion + DOS plot      → script reads FORCE_CONSTANTS + BORN → band/DOS plot (v4 API)
Stage J  Experimental comparison + report → extract key-point frequencies → Phonon_Report.md
```

**Confirmation gates between stages**: Stages A / D / E / F consume machine time; before starting them, report the plan to the user and obtain consent (see "Execution").

---

## Core Principles (read before acting)

### PRINCIPLE 1: The finite-displacement method allows no relaxation
VASP force calculations on displaced configurations **must** be static single points: `IBRION=-1`, `NSW=0`, `ISIF=2`. Any atomic relaxation breaks the linear-response assumption of the finite-displacement method and gives wrong force constants. The accuracy of the force calculation directly determines the phonon quality, so electronic convergence must be tight: `EDIFF=1E-6`, `PREC=Accurate`.

**Why**: the finite-displacement method treats the measured force as "the restoring force of the lattice under this displacement". If the atoms relax again in the measured configuration, the forces belong to a different, relaxed structure rather than the displaced configuration itself, and the second-order force-constant matrix is distorted.

### PRINCIPLE 2: Displacements must be small and exploit symmetry
phonopy's default displacement amplitude is **0.01 Å** (does not break the harmonic approximation / linear response) and large enough that the forces are not drowned by numerical noise. phonopy automatically uses the point-group symmetry to reduce the equivalent atoms in the supercell to a **small number of independent displacements** (the MgO rock-salt 64-atom supercell generates only **2 independent displacements**). **Do not add displacements manually**; that wastes machine time without improving accuracy.

### PRINCIPLE 3: Ionic crystals require NAC (LO-TO splitting)
Polar/ionic crystals (MgO, ZnO, GaN, perovskites, etc.) have an **LO-TO splitting** at Γ, which requires a non-analytical correction using the **BORN file** (Born effective charges + dielectric tensor). Without a BORN file the optical branches at Γ are wrongly degenerate (splitting = 0). **BORN is obtained from one DFPT calculation (`LEPSILON=.TRUE.`)**, see Stage D.

**Why**: the non-analytical term comes from the long-range dipole-dipole interaction, which makes the longitudinal optical branch (LO) differ from the transverse optical branch (TO) in the long-wavelength limit. This term is a q→0 limiting contribution and can only be included correctly by reading in the Born charges + dielectric tensor.

### PRINCIPLE 4: Primitive vs conventional cell: think it through before generating
The standard input of a phonon calculation is **unit cell + supercell matrix**, but the BORN file must match the **number of atoms of the unit cell**. Two routes:
- **Route 1 (recommended, e.g. MgO)**: use the **conventional cell** (e.g. rock-salt Mg4O4, 8 atoms) as the unit cell, declare `primitive_matrix` (fcc basis transformation) in phonopy to recover the primitive cell, supercell `--dim 2 2 2` = 64 atoms. Use the 8-atom BORN.
- **Route 2**: use the **primitive cell** (2 atoms) as the unit cell, with a larger supercell `--dim` (e.g. primitive --dim 3 3 3 = 54 atoms) to converge the force constants. Use the 2-atom BORN.

Key point: **the number of atoms in BORN must equal the number of atoms of ph.primitive**, and the atom order of the POSCAR must match BORN. Choose so that "the supercell is large enough to converge the force constants and BORN matches the unit cell".

---

## Stage A: Structural Relaxation (equilibrium lattice constant)

1. Obtain the initial structure via `structure-builder` (MP mp-id or a user-provided POSCAR).
2. If you obtained a **primitive cell** and will ultimately compute phonons, convert it to the **conventional cell** (with `structure-builder` / pymatgen, see "Conventional cell construction" below).
3. Run a full `ISIF=3` relaxation with **`workflow-relax`** to obtain the equilibrium lattice constant.

**⚠️ pymatgen pitfall**: `Structure.get_conventional_standard_structure()` has been renamed to `get_conventional_standard_structure()` in newer pymatgen and may be unavailable / behave differently. **Do not rely on it.** The reliable approach is to construct the conventional cubic cell by hand with `Structure.from_file` + `Lattice.cubic(a)` (for standard structures such as rock salt/fluorite), or use the related interfaces in `pymatgen.symmetry.analyze`. For complex structures the MP conventional cell can still be used (the `cif`/POSCAR is already conventional).

## Stage B: ENCUT / KSPACING Convergence

Use **`workflow-convergence`** on the **unit cell** for ENCUT and KSPACING convergence (criterion: adjacent-step ΔE ≤ 1 meV/atom, then take the larger value). The converged ENCUT / KSPACING are used for all subsequent stages (DFPT, force calculations) to keep the energy/force reference consistent.

## Stage C: Decide the Supercell Scheme

- Make clear whether the unit cell is **primitive or conventional**; make clear the `primitive_matrix` (if the conventional cell is reduced to the primitive) and the `--dim` supercell matrix.
- Check the supercell size (number of atoms) so that the displaced configurations do not blow up machine time because the supercell is too large; the MgO conventional cell with `--dim 2 2 2` = 64 atoms is a reasonable baseline.
- Record the `POSCAR-unitcell` (unit-cell POSCAR) that will finally be used by phonopy and its correspondence with BORN.

---

## Stage D: DFPT Dielectric Tensor + Born Effective Charges (generate BORN)

Needed only for ionic crystals. If the system is non-polar (e.g. graphite, silicon), **skip this stage** and do no NAC with BORN.

### D1. DFPT static INCAR
Run one DFPT calculation on the **relaxed unit cell**:
```
IBRION = -1   ; NSW = 0     ; ISIF = 2
LEPSILON = .TRUE.          ; # DFPT dielectric tensor + Born effective charges
PREC = Accurate ; EDIFF = 1E-6
ENCUT = <converged value> ; KSPACING = <converged value>   ; # dielectric converges more slowly than energy; use the converged value
ISMEAR = 0 ; SIGMA = 0.05
LWAVE = .FALSE. ; LCHARG = .FALSE.
```
Use `incar-builder` + `incar-performance` to generate the INCAR; use `setup_vasp_inputs` for POTCAR/POSCAR.

**Why KSPACING must be the converged value rather than coarser**: the dielectric tensor and Born charges converge more slowly with the k mesh than the total energy; an energy-converged coarse mesh underestimates the dielectric constant and makes the LO-TO splitting too small.

### D2. Generate the BORN file
```bash
cd <lepsilon dir>          # make sure the cwd contains the completed vasprun.xml
phonopy-vasp-born > BORN   # ⚠️ redirect is mandatory! It only prints to stdout and does not write a file
```
**⚠️ Pitfall**: `phonopy-vasp-born` **does not write a BORN file automatically**; it prints the dielectric tensor and Born charges to stdout. You must redirect with `> BORN`. It produces two kinds of lines (line 1: 9 numbers of the dielectric tensor; then 9 numbers per atom):
```
# epsilon and Z* of atoms ...
   3.233  0.0  0.0  0.0  3.233  0.0  0.0  0.0  3.233
   1.992  0.0  0.0  0.0  1.992  0.0  0.0  0.0  1.992
  -1.992  0.0  0.0  0.0 -1.992  0.0  0.0  0.0 -1.992
```
**Physical check**: for MgO the theoretical Born charges are ≈ ±2 and the dielectric tensor is isotropic ≈ 3.0. If the values are off by an order of magnitude, DFPT is usually unconverged or the tool was run before vasprun.xml was completely written.

### D3. Wait until DFPT has really finished
Do not set the monitor to "match the string BORN": the VASP INCAR may contain `SYSTEM = ... BORN ...` or `LEPSILON`, which leads to a **false completion**. The correct waiting condition is that OUTCAR contains `General timing` / `Total CPU time` and `vasprun.xml` has been fully written.

---

## Stage E: Generate Displaced Supercells

Use `phonopy-init` (**from v4, `--dim`, `-d`, and `--symmetry` moved from the main `phonopy` command to `phonopy-init`**):

```bash
cd <disp dir>
# unit cell: conventional or primitive (matching BORN)
cp <relax>/CONTCAR POSCAR-unitcell        # if the relaxed cell is conventional and must match BORN
# generate supercell + displaced configurations + SPOSCAR
phonopy-init --dim 2 2 2 -c POSCAR-unitcell -d
```
Products:
- `SPOSCAR` supercell
- `POSCAR-001`, `POSCAR-002` ... one per independent displacement (MgO 64-atom supercell → 2)
- `phonopy_disp.yaml` records the displacements (needed in Stage G to extract forces)

**Check the number of displacements**: the count of `ls POSCAR-*` should equal the number of independent displacements after symmetry reduction; do not add any manually.
**Check the number of supercell atoms**: read the first lines of `SPOSCAR` to check the total atom count (conventional cell 8 atoms × 8 = 64).

---

## Stage F: Batch Force Calculations

One directory per displaced configuration (e.g. `fc/fc_001`, `fc/fc_002`), containing that configuration's POSCAR + the common force-calculation INCAR + POTCAR. Use `setup_vasp_inputs` (`work_dir`) to generate the VASP inputs of each directory.

Force-calculation INCAR:
```
IBRION = -1 ; NSW = 0 ; ISIF = 2      ; # no relaxation whatsoever
PREC = Accurate ; EDIFF = 1E-6
ENCUT = <converged value> ; KSPACING = <converged value>
ISMEAR = 0 ; SIGMA = 0.05
LWAVE = .FALSE. ; LCHARG = .FALSE.
```

**Run**: load `run-vasp` and submit all displaced-configuration directories at once with `vasp_runner.py`:
```bash
python .claude/skills/run-vasp/scripts/vasp_runner.py \
  --dirs <abs>/fc/fc_001 <abs>/fc/fc_002 ... \
  --mode local --np 1 --exe vasp_gpu --gpu-per-task 1 --fixed-gpu-layout \
  --env-script /abs/template/env_gpu.sh --log-prefix vasp_fc
```
**⚠️ Always give `--dirs` as absolute paths.** `vasp_runner.py` resolves relative paths from the repository root; relative paths run in the wrong directory (reporting "No INCAR found").

**⚠️ Wait for tasks with an `until` loop / Monitor, not a foreground `sleep`** (which the Bash tool blocks). Run long tasks in the background with `run_in_background: true`.

---

## Stage G: Extract FORCE_SETS

Extract forces from the `vasprun.xml` of all displaced configurations to generate `FORCE_SETS`. Put `phonopy_disp.yaml` in the fc directory as well (phonopy needs it to read the displacements):

```bash
cp <disp>/phonopy_disp.yaml <fc>/phonopy_disp.yaml
cp <disp>/SPOSCAR <fc>/SPOSCAR
cd <fc>
phonopy-init -f fc_001/vasprun.xml fc_002/vasprun.xml
```
This generates `FORCE_SETS`. Check its size (about 2 displacements × 64 atoms × 3 force components).

---

## Stage H: Fit FORCE_CONSTANTS

The main `phonopy` command reads `FORCE_SETS` + `SPOSCAR` from the current directory by default; `--writefc` writes `FORCE_CONSTANTS`, and `--fc-spg-symmetry` symmetrizes with the space group:

```bash
cd <fc>
phonopy --writefc --fc-spg-symmetry
```
**⚠️ Key v4 changes** (the biggest pitfall this skill captured):
- `phonopy --dim` → **removed**, only in `phonopy-init`
- `phonopy --nac` → **removed**; NAC is **enabled automatically** once a BORN file is read (no `--nac` needed)
- `phonopy --fc-symmetry` → renamed to **`--fc-spg-symmetry`** (main command), or use `ph.force_constants = fc` directly in the Python API
- **Fitting FORCE_CONSTANTS only requires running `phonopy --writefc` in a directory containing FORCE_SETS + SPOSCAR**; do not add `--dim`

Products: `FORCE_CONSTANTS` + `phonopy.yaml` (if the directory contains BORN, `phonopy.yaml` has a `nac:` section).

---

## Stage I: Dispersion + DOS Plot (v4 Python API)

Producing the plot in one step with phonopy's Python API is the most robust. **Do not use removed methods** (see "API pitfalls"); the correct pattern is:

```python
import numpy as np, matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from phonopy import Phonopy
from phonopy.interface.vasp import read_vasp
from phonopy.file_IO import parse_FORCE_CONSTANTS

unitcell = read_vasp("../disp/POSCAR-conv")   # unit cell (consistent with BORN/original cell)
ph = Phonopy(unitcell,
             supercell_matrix=np.diag([2,2,2]),
             primitive_matrix=[[0,.5,.5],[.5,0,.5],[.5,.5,0]])  # fcc basis
ph.force_constants = parse_FORCE_CONSTANTS("FORCE_CONSTANTS")   # ← attribute, not method

# NAC: build the nac_params dict manually (most robust)
nac_params = build_nac_params(ph)   # see get_born_params.py
if nac_params: ph.nac_params = nac_params

# dispersion
band_paths = [np.linspace(p0,p1,101) for p0,p1 in seg_pts]  # Γ-X-W-Γ-L
bs = ph.run_band_structure(band_paths, with_eigenvectors=False,
                           labels=['Γ','X','W','L'], path_connections=[...])
# bs.distances / bs.frequencies / bs.path_connections

# DOS
ph.run_mesh([31,31,31], with_eigenvectors=False, is_mesh_symmetry=True)
dos = ph.run_total_dos(freq_min=-0.3, freq_max=22.0, freq_pitch=0.05,
                       use_tetrahedron_method=True)
```

**NAC pitfalls (key)**:
- `from phonopy.file_IO import read_BORN` → **does not exist**, and the signature/fields of `parse_BORN` differ from what one expects (the returned NacParams lacks `factor`).
- **The most robust approach is to parse the BORN file manually** and build a `nac_params` dict containing `born`, `dielectric`, `factor` (phonopy convention **14.399652**), `primitive`, and `q_direction`. See `scripts/get_born_params.py`.

**⚠️ Frequency extraction and LO-TO**: `run_qpoints` **does not apply NAC at the Γ point (0,0,0)**, so LO and TO appear at the same frequency (splitting = 0). To obtain high-symmetry-point frequencies including the NAC splitting, **you must use `run_band_structure` or a treatment with a perturbed q direction**. This means that plotting and extracting frequencies should consistently use the **band-structure data**, not the qpoints data.

**Frequency units**: phonopy uses THz internally (the freq axis of the DOS is also THz). For plots, multiply by `CM_PER_THZ = 33.356` to convert to cm⁻¹. Use `freq * cm` for the y axis and the report.

---

## Stage J: Experimental Comparison + Report

1. Use `scripts/extract_phonon_report.py` to extract the frequencies at each high-symmetry point from the band structure and the peaks from the DOS.
2. Search the literature/experimental benchmarks (`research-literature`, `duckduckgo_search`, `semanticscholar_search`) for experimental phonon frequencies (MgO: Γ TO≈401 cm⁻¹, LO≈720 cm⁻¹, Sangster INS).
3. Write `Phonon_Report.md`: structure, converged ENCUT/KSPACING, DFPT dielectric/Born, paths of the dispersion and DOS plots, table of high-symmetry-point frequencies, comparison with experiment and deviation analysis.

**⚠️ Troubleshooting LO-TO splitting = 0**: if the optical branches at Γ show LO=TO (splitting = 0):
- Check whether `phonopy.yaml` has a `nac:` section (only with BORN). If not → `ph.nac_params` was not set, or there was no BORN in the directory when FORCE_CONSTANTS were fitted.
- Confirm that the `factor`, `dielectric`, and `born` values in `nac_params` are correct.
- Extract frequencies from the band structure, not from qpoints.

---

## Execution (ITERATIVE EXECUTION RULE)

Consistent with the repository system_prompt:
- **Do not** use `for`/`while` loops or monolithic Python/Bash scripts to submit multi-point, multi-stage, multi-directory VASP calculations in one go.
- Submit each heavy step (each convergence point, each displaced configuration) **separately** with `run_in_background: true`, or pass the directories of one batch to the runner with `vasp_runner.py --dirs` for batched scheduling.
- After each step, **first read OUTCAR / OSZICAR / the convergence report** to confirm convergence and correctness, then decide the next step.
- **Before running, show the user the complete `vasp_runner.py` command and obtain consent** (including `--env-script`, `--gpu-per-task`, `--fixed-gpu-layout`).
- When GPU and CPU coexist, **do not default to CPU**; first ask the user GPU vs CPU and the number of GPUs.
- With a scheduler, ask for partition / nodes / walltime.
- Wait with an `until` loop / Monitor / `run_in_background`, **not a foreground `sleep`**.

### STRICT HARDWARE ALIGNMENT
Follow `run-vasp`: 1 rank ↔ 1 GPU; number of GPU cards = number of parallel tasks (e.g. 2 displaced configurations = 2 cards). Under `start_new_session` without a tty, `vasp_gpu` may report "No INCAR found" because of an OpenMPI cwd resolution issue; in that case pass **absolute paths** to `--dirs` of the runner. For local multi-GPU runs use `--gpu-per-task 1` + `--fixed-gpu-layout`.

---

## Provenance of VASP Files

- **POSCAR / POTCAR / KPOINTS**: never written by hand. Obtain the structure via `structure-builder` + `mcp__vasp_agent__setup_vasp_inputs` (a `work_dir` can be passed to generate POTCAR/POSCAR/INCAR separately for each displaced configuration/directory). POTCAR uses the pymatgen/MP **recommended** semicore potentials (Mg_pv, O, etc.); use `potcar_overrides` only when the user explicitly specifies them.
- **INCAR**: copy and modify this skill's templates or the `workflow-relax` templates (ENCUT / ISMEAR / KSPACING / IBRION / NSW, etc.); do not write POSCAR by hand.

---

## Quick Reference of Pitfalls (with reasons)

| Symptom | Cause | Remedy |
|---|---|---|
| `phonopy: error: '--dim' is a setup operation` | v4 moved setup operations to `phonopy-init` | Use `phonopy-init --dim ... -d` |
| `'--nac' was removed in phonopy v4` | NAC enabled automatically | Put BORN in the directory; do not pass `--nac` |
| `'--fc-symmetry'` not accepted | Main command renamed it `--fc-spg-symmetry` | Use `phonopy --writefc --fc-spg-symmetry` |
| `Phonopy(...) unexpected keyword 'factor'` | v4 constructor has no factor | Put factor into `nac_params` |
| `Phonopy has no 'load_force_constants'` | v4 uses an attribute | `ph.force_constants = parse_FORCE_CONSTANTS(...)` |
| `cannot import read_BORN` / `parse_BORN` missing fields | Fields changed | Parse BORN manually to build `nac_params` (get_born_params.py) |
| `phonopy-vasp-born` did not write BORN | Only prints to stdout | `phonopy-vasp-born > BORN` |
| LO-TO splitting = 0 (even in qpoints) | `run_qpoints` does not apply NAC at Γ | Extract with `run_band_structure` |
| VASP reports "No INCAR found" for displaced configurations | Ambiguous resolution of relative `--dirs` paths | Use absolute paths for `--dirs` |
| vasp_gpu start-up reports MPI / missing help files | Misplaced OpenMPI prefix | Set `OPAL_PREFIX` in `--env-script`; `run-vasp` probes |
| Monitor falsely reports DFPT completion | Matched the word "BORN" | Wait for `General timing` + completed vasprun.xml |
| Foreground `sleep` blocked | Bash tool rule | `until` loop / Monitor / `run_in_background` |
| `get_conventional_standard_structure` errors | pymatgen API change | Construct the conventional cell manually (build_conventional_cell.py) |

---

## Appendix: Quick Reference of Available v4 phonopy API Members

- Constructor: `Phonopy(unitcell, supercell_matrix=..., primitive_matrix=...)` (no `factor`)
- Force constants: read with `ph.force_constants = parse_FORCE_CONSTANTS("FORCE_CONSTANTS")`; write with `phonopy --writefc`
- NAC: `ph.nac_params = {born, dielectric, factor, primitive, q_direction}`
- Dispersion: `ph.run_band_structure(band_paths, with_eigenvectors=False, labels=..., path_connections=...)` → the returned object's `.distances` `.frequencies` `.path_connections`
- DOS: `ph.run_mesh([N,N,N], ...)`; `ph.run_total_dos(freq_min, freq_max, freq_pitch, use_tetrahedron_method=True)` → `.frequency_points` `.dos`
- Units: `CM_PER_THZ = 33.356`

Before writing, confirm the phonopy version: `python -c "import phonopy; print(phonopy.__version__)"`. If it is <4, the v4 behavior above does not apply; use the old syntax instead (`--dim`, `--nac`, `--fc-symmetry`).
