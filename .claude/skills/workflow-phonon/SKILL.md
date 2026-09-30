---
name: "workflow-phonon"
description: "Run the VASP phonon workflow: structure relaxation → finite-displacement force calculations on a supercell → phonopy force-constant fitting → phonon dispersion and density of states (with NAC correction for LO-TO splitting) → comparison with experiment. Trigger when the user wants phonon dispersion curves, phonon density of states (phonon DOS), phonon frequencies, a phonon spectrum, lattice dynamics, NAC/LO-TO splitting, or asks for phonon band/DOS plots. Uses phonopy (v4+) with the VASP finite-displacement method; ionic crystals additionally require a DFPT calculation of Born effective charges + the dielectric tensor. Always go through run-vasp before running VASP, and through incar-performance first when GPU/KPAR/NCORE are involved."
---

# VASP Phonon Workflow (Phonon Dispersion & DOS)

You are an expert in computational materials science. This skill guides you through automating phonon dispersion and phonon DOS calculations for solids. The core method is the **finite-displacement (frozen-phonon) method**: build a supercell of the equilibrium structure, apply small displacements to symmetry-inequivalent atoms, compute the Hellmann-Feynman forces of the displaced configurations with VASP, then hand them to **phonopy** to fit harmonic force constants, compute the dispersion along a high-symmetry path, and reproduce the LO-TO splitting of ionic crystals near Γ with the **NAC (non-analytical term correction, BORN file)**.

## When to trigger

- The user asks for **phonon dispersion curves / phonon spectrum / phonon dispersion**
- The user asks for **phonon density of states / phonon DOS / phonon energy states**
- The user asks for **phonon frequencies, lattice dynamics, lattice vibrations, or thermodynamic properties** (lattice heat capacity, zero-point energy)
- The user wants a phonon calculation on a known structure, especially an **ionic crystal** (LO-TO splitting needed)
- Benchmark calculations to be compared against experimental (INS / Raman / IR) phonon frequencies

## Required existing skills (load first)

This skill is **not responsible** for generating or running VASP inputs. Before executing, first load:

- **`Skill: run-vasp`** — **must** be loaded before any VASP / mpirun / vasp_gpu / vasp_runner.py run. Follow its probes, STRICT HARDWARE ALIGNMENT (GPU vs CPU, 1 rank↔1 GPU), and the `vasp_runner.py --dirs` batching strategy.
- **`Skill: incar-builder`** — **must** be loaded before writing or editing an INCAR.
- **`Skill: structure-builder`** — **must** be loaded before obtaining/building a POSCAR (this skill never hand-writes a POSCAR from memory).
- **`Skill: incar-performance`** — load before touching GPU / KPAR / NCORE / NPAR.
- **`Skill: incar-smearing-precision`**, **`Skill: incar-validator`** — load when setting and validating precision.
- **`Skill: workflow-relax`** — equilibrium structure relaxation (Stage A).
- **`Skill: workflow-convergence`** — ENCUT / KSPACING convergence tests (fixes the production ENCUT / KSPACING).
- **`Skill: research-literature`** — look up experimental phonon frequencies as a comparison benchmark; `duckduckgo_search` / `semanticscholar_search` / `arxiv_search` can also be used.

## Directory layout

```
workflow-phonon/
├── SKILL.md                ← this file (phonon workflow)
├── scripts/
│   ├── build_conventional_cell.py  ← pymatgen primitive → conventional cell (if the unit cell is primitive / low symmetry)
│   ├── get_born_params.py          ← builds the phonopy nac_params dict (parses BORN manually, avoiding the parse_BORN pitfall)
│   ├── plot_phonon_dos.py          ← dispersion + DOS plots from FORCE_CONSTANTS + BORN (v4 Python API, with NAC)
│   └── extract_phonon_report.py    ← extracts high-symmetry-point frequencies and DOS peaks from band/DOS for comparison with experiment
└── references/
    └── vasp_phonopy_settings.md   ← key INCAR tags for each phonon stage (DFPT/forces/convergence)
```

## Workflow overview

```
Stage A  Structure relaxation     → workflow-relax gives the equilibrium lattice constant
Stage B  Convergence tests        → workflow-convergence fixes ENCUT / KSPACING (1 meV/atom)
Stage C  Primitive vs conventional → decide the supercell scheme and BORN matching
Stage D  DFPT dielectric/Born     → LEPSILON static run → phonopy-vasp-born generates BORN (skip NAC if no BORN)
Stage E  Supercell displacements  → phonopy-init --dim ... -d generates SPOSCAR + POSCAR-###
Stage F  Batch force calculations → vasp_runner.py runs a static force calculation per displacement (IBRION=-1, NSW=0)
Stage G  Extract FORCE_SETS       → phonopy-init -f vasprun.xml...
Stage H  Fit FORCE_CONSTANTS      → phonopy --writefc --fc-spg-symmetry
Stage I  Dispersion + DOS plots   → script reads FORCE_CONSTANTS + BORN → band/DOS plots (v4 API)
Stage J  Comparison + report      → extract key-point frequencies → Phonon_Report.md
```

**Confirmation gates between stages**: Stages A / D / E / F consume compute time; before launching them, present the plan to the user and get approval (see "Execution rules").

---

## Core principles (read before acting)

### PRINCIPLE 1: The finite-displacement method allows no relaxation
VASP force calculations on displaced configurations **must** be static single points: `IBRION=-1`, `NSW=0`, `ISIF=2`. Any ionic relaxation breaks the linear-response assumption of the finite-displacement method and gives wrong force constants. The accuracy of the forces directly determines phonon quality, so electronic convergence must be tight: `EDIFF=1E-6`, `PREC=Accurate`.

**Why**: the finite-displacement method treats the measured forces as "the restoring forces of the lattice under that displacement". If atoms relax again within the measured configuration, you measure the forces of a different, relaxed structure rather than those of the displaced configuration itself, and the second-order force-constant matrix is distorted accordingly.

### PRINCIPLE 2: Displacements must be small and exploit symmetry
phonopy's default displacement amplitude is **0.01 Å** (it does not break the harmonic approximation / linear response), yet large enough that the forces are not swamped by numerical noise. phonopy automatically uses point-group symmetry to reduce the equivalent atoms in the supercell to a **small number of independent displacements** (a 64-atom rock-salt MgO supercell yields only **2 independent displacements**). **Do not add displacements by hand**; that wastes compute time without improving accuracy.

### PRINCIPLE 3: Ionic crystals require NAC (LO-TO splitting)
Polar/ionic crystals (MgO, ZnO, GaN, perovskites, etc.) show **LO-TO splitting** at Γ, which requires a non-analytical term correction using a **BORN file** (Born effective charges + dielectric tensor). Without a BORN file, the optical branches at Γ are wrongly degenerate (splitting = 0). **BORN is obtained from one DFPT (`LEPSILON=.TRUE.`) calculation**; see Stage D.

**Why**: the non-analytical term comes from long-range dipole-dipole interactions, which make the longitudinal optical (LO) branch differ from the transverse optical (TO) branch in the long-wavelength limit. This term is a q→0 limit contribution and is only included correctly when Born charges + the dielectric tensor are read in.

### PRINCIPLE 4: Primitive vs conventional cell — think it through before generating
The standard input of a phonon calculation is a **unit cell + supercell matrix**, but the BORN file must match the **number of atoms in the primitive cell**. Two routes:
- **Route 1 (recommended, MgO etc.)**: use the **conventional cell** (e.g. rock-salt Mg4O4, 8 atoms) as the unit cell, declare a `primitive_matrix` in phonopy (fcc basis transformation) to reduce to the primitive cell, supercell `--dim 2 2 2` = 64 atoms. Use the 8-atom BORN.
- **Route 2**: use the **primitive cell** (2 atoms) as the unit cell with a larger supercell `--dim` (e.g. primitive --dim 3 3 3 = 54 atoms) to ensure force-constant convergence. Use the 2-atom BORN.

Key point: **the number of atoms in BORN must equal the number of atoms in ph.primitive**, and the atom order in POSCAR must match BORN. Choose based on "a supercell large enough to converge the force constants, with BORN matching the primitive cell".

---

## Stage A: Structure relaxation (equilibrium lattice constant)

1. Obtain the initial structure from `structure-builder` (MP mp-id or a user-provided POSCAR).
2. If you get a **primitive cell** and ultimately need phonons, convert it to the **conventional cell** (with `structure-builder` / pymatgen; see "Conventional cell construction" below).
3. Run a full `ISIF=3` relaxation with **`workflow-relax`** to obtain the equilibrium lattice constant.

**⚠️ pymatgen pitfall**: in newer pymatgen, `Structure.get_conventional_standard_structure()` has been renamed to `get_conventional_standard_structure()` and may be unavailable / behave differently. **Do not rely on it**. The reliable approach is to build the conventional cubic cell by hand with `Structure.from_file` + `Lattice.cubic(a)` (for standard structures such as rock salt/fluorite), or use the related `pymatgen.symmetry.analyze` interfaces. For complex structures, the MP conventional cell can still be used (the `cif`/POSCAR is already conventional).

## Stage B: ENCUT / KSPACING convergence

Use **`workflow-convergence`** to converge ENCUT and KSPACING on the **unit cell** (criterion: take the larger value once adjacent steps satisfy ΔE ≤ 1 meV/atom). The converged ENCUT / KSPACING are used for all subsequent stages (DFPT, force calculations) to keep the energy/force reference consistent.

## Stage C: Decide the supercell scheme

- Determine whether the unit cell is **primitive or conventional**; determine the `primitive_matrix` (if reducing a conventional cell to the primitive cell) and the `--dim` supercell matrix.
- Check the supercell size (number of atoms) so the displaced configurations do not blow up compute time because the supercell is too large; the MgO conventional cell with `--dim 2 2 2` = 64 atoms is a reasonable baseline.
- Record the final `POSCAR-unitcell` (unit-cell POSCAR) used by phonopy and how it matches BORN.

---

## Stage D: DFPT dielectric tensor + Born effective charges (generate BORN)

Only needed for ionic crystals. If the system is non-polar (e.g. graphite, silicon), **skip this stage** and do not apply NAC with BORN.

### D1. DFPT static INCAR
Run one DFPT calculation on the **relaxed unit cell**:
```
IBRION = -1   ; NSW = 0     ; ISIF = 2
LEPSILON = .TRUE.          ; # DFPT dielectric tensor + Born effective charges
PREC = Accurate ; EDIFF = 1E-6
ENCUT = <converged value> ; KSPACING = <converged value>   ; # dielectric converges slower than energy; use converged values
ISMEAR = 0 ; SIGMA = 0.05
LWAVE = .FALSE. ; LCHARG = .FALSE.
```
Use `incar-builder` + `incar-performance` when generating the INCAR; use `setup_vasp_inputs` for POTCAR/POSCAR.

**Why KSPACING must use the converged value rather than something coarser**: the dielectric tensor and Born charges converge more slowly with the k mesh than the total energy; a coarse mesh that is converged for energy underestimates the dielectric constant and makes the LO-TO splitting too small.

### D2. Generate the BORN file
```bash
cd <lepsilon dir>          # make sure the cwd has a fully written vasprun.xml
phonopy-vasp-born > BORN   # ⚠️ must redirect! it only prints to stdout and writes no file
```
**⚠️ Pitfall**: `phonopy-vasp-born` **does not write a BORN file automatically**; it prints the dielectric tensor and Born charges to stdout. You must save it with the `> BORN` redirection. It prints two kinds of lines like these (line 1 is the 9-number dielectric tensor, followed by 9 numbers per atom):
```
# epsilon and Z* of atoms ...
   3.233  0.0  0.0  0.0  3.233  0.0  0.0  0.0  3.233
   1.992  0.0  0.0  0.0  1.992  0.0  0.0  0.0  1.992
  -1.992  0.0  0.0  0.0 -1.992  0.0  0.0  0.0 -1.992
```
**Physical check**: for MgO the theoretical Born charges are ≈ ±2 and the dielectric tensor is isotropic, ≈ 3.0. If the values are off by an order of magnitude, DFPT is usually unconverged or the tool was run before vasprun.xml was fully written.

### D3. Wait for DFPT to actually finish
Do not set the monitor to "match the string BORN" — the VASP INCAR may contain `SYSTEM = ... BORN ...` or the word `LEPSILON`, causing a **false completion**. The correct wait condition is that OUTCAR contains `General timing` / `Total CPU time` and `vasprun.xml` is fully written.

---

## Stage E: Generate supercell displacements

Use `phonopy-init` (**since v4, `--dim`, `-d`, and `--symmetry` have all moved from the main `phonopy` command to `phonopy-init`**):

```bash
cd <disp dir>
# unit cell: conventional or primitive (must match BORN)
cp <relax>/CONTCAR POSCAR-unitcell        # if the relaxed cell is conventional and must match BORN
# generate supercell + displaced configurations + SPOSCAR
phonopy-init --dim 2 2 2 -c POSCAR-unitcell -d
```
Outputs:
- `SPOSCAR` supercell
- `POSCAR-001`, `POSCAR-002` ... one per independent displacement (MgO 64-atom supercell → 2)
- `phonopy_disp.yaml` records the displacement information (needed to extract forces in Stage G)

**Check the number of displacements**: the count of `ls POSCAR-*` should equal the number of independent displacements after symmetry reduction; do not add more by hand.
**Check the number of atoms in the supercell**: read the first few lines of `SPOSCAR` to verify the total atom count (conventional cell 8 atoms × 8 = 64).

---

## Stage F: Batch force calculations

One directory per displaced configuration (e.g. `fc/fc_001`, `fc/fc_002`), each containing that configuration's POSCAR + the common force-calculation INCAR + POTCAR. Use `setup_vasp_inputs` (`work_dir`) to generate the VASP inputs for each directory.

Force-calculation INCAR:
```
IBRION = -1 ; NSW = 0 ; ISIF = 2      ; # no relaxation whatsoever
PREC = Accurate ; EDIFF = 1E-6
ENCUT = <converged value> ; KSPACING = <converged value>
ISMEAR = 0 ; SIGMA = 0.05
LWAVE = .FALSE. ; LCHARG = .FALSE.
```

**Running**: load `run-vasp` and submit all displacement directories at once with `vasp_runner.py`:
```bash
python .claude/skills/run-vasp/scripts/vasp_runner.py \
  --dirs <abs>/fc/fc_001 <abs>/fc/fc_002 ... \
  --mode local --np 1 --exe vasp_gpu --gpu-per-task 1 --fixed-gpu-layout \
  --env-script /abs/template/env_gpu.sh --log-prefix vasp_fc
```
**⚠️ Always pass absolute paths** to `--dirs`. `vasp_runner.py` resolves relative paths from the repository root; passing relative paths runs in the wrong directory (error "No INCAR found").

**⚠️ Wait for jobs with an `until` loop / Monitor, not a foreground `sleep`** (it is blocked by the Bash tool). Run long jobs in the background with `run_in_background: true`.

---

## Stage G: Extract FORCE_SETS

Extract the forces from the `vasprun.xml` of every displaced configuration to produce `FORCE_SETS`. Put `phonopy_disp.yaml` in the fc directory as well (phonopy needs it to read the displacement information):

```bash
cp <disp>/phonopy_disp.yaml <fc>/phonopy_disp.yaml
cp <disp>/SPOSCAR <fc>/SPOSCAR
cd <fc>
phonopy-init -f fc_001/vasprun.xml fc_002/vasprun.xml
```
This produces `FORCE_SETS`. Check its size (roughly 2 displacements × 64 atoms × 3 force components).

---

## Stage H: Fit the force constants FORCE_CONSTANTS

The main `phonopy` command reads `FORCE_SETS` + `SPOSCAR` from the current directory by default; use `--writefc` to write `FORCE_CONSTANTS` and `--fc-spg-symmetry` for space-group symmetrization:

```bash
cd <fc>
phonopy --writefc --fc-spg-symmetry
```
**⚠️ Key v4 changes** (the biggest pitfall this skill captures):
- `phonopy --dim` → **removed**, only in `phonopy-init`
- `phonopy --nac` → **removed**; NAC is **enabled automatically** once a BORN file is read (no `--nac` needed)
- `phonopy --fc-symmetry` → renamed to **`--fc-spg-symmetry`** (main command), or use `ph.force_constants = fc` in the Python API directly
- **Fitting FORCE_CONSTANTS only requires running `phonopy --writefc` in the directory containing FORCE_SETS + SPOSCAR**; do not add `--dim`

Outputs: `FORCE_CONSTANTS` + `phonopy.yaml` (if BORN is in the directory, `phonopy.yaml` contains a `nac:` section).

---

## Stage I: Dispersion + DOS plots (v4 Python API)

Producing the plots in one step with the phonopy Python API is the most robust. **Do not use removed methods** (see "API pitfalls"); the correct pattern is:

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
ph.force_constants = parse_FORCE_CONSTANTS("FORCE_CONSTANTS")   # ← attribute, not a method

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

**NAC pitfalls (critical)**:
- `from phonopy.file_IO import read_BORN` → **does not exist**, and the signature/fields of `parse_BORN` differ from what you would expect (the returned NacParams lacks `factor`).
- **The most robust approach is to parse the BORN file manually** and build a `nac_params` dict containing `born`, `dielectric`, `factor` (phonopy convention **14.399652**), `primitive`, and `q_direction`. See `scripts/get_born_params.py`.

**⚠️ Frequency extraction and LO-TO**: `run_qpoints` **does not apply NAC at the Γ point (0,0,0)**, so LO and TO show up at the same frequency (splitting = 0). To get high-symmetry-point frequencies that include the NAC splitting, you **must use `run_band_structure` or a treatment with a q-direction perturbation**. This means plotting and extracting frequencies should consistently use the **band structure data**, not qpoints data.

**Frequency units**: phonopy uses THz internally (the freq on the DOS axis is also THz). For plots, multiply by `CM_PER_THZ = 33.356` to convert to cm⁻¹. Use `freq * cm` for the y axis and the report.

---

## Stage J: Comparison with experiment + report

1. Use `scripts/extract_phonon_report.py` to extract the frequencies at each high-symmetry point from the band structure and the peaks from the DOS.
2. Look up literature/experimental benchmarks (`research-literature`, `duckduckgo_search`, `semanticscholar_search`) to obtain experimental phonon frequencies (MgO: Γ TO≈401 cm⁻¹, LO≈720 cm⁻¹, Sangster INS).
3. Write `Phonon_Report.md`: structure, converged ENCUT/KSPACING, DFPT dielectric/Born, paths to the dispersion and DOS plots, table of high-symmetry-point frequencies, comparison with experiment and deviation analysis.

**⚠️ Troubleshooting LO-TO splitting = 0**: if the optical branches at Γ show LO=TO (splitting = 0):
- Check whether `phonopy.yaml` has a `nac:` section (only present with BORN). If not → `ph.nac_params` was not set, or there was no BORN in the directory when FORCE_CONSTANTS was fitted.
- Confirm that the `factor`, `dielectric`, and `born` values in `nac_params` are correct.
- Extract frequencies from the band structure, not from qpoints.

---

## Execution rules (ITERATIVE EXECUTION RULE)

Consistent with the repository system_prompt:
- **Do not** use `for`/`while` loops or monolithic Python/Bash scripts to submit multi-point, multi-stage, or multi-directory VASP calculations in one go.
- Submit each heavy step (each convergence point, each displaced configuration) **individually** with `run_in_background: true`, or pass directories of the same batch to the runner via `vasp_runner.py --dirs` for batched scheduling.
- After each step finishes, **first read OUTCAR / OSZICAR / the convergence report** to confirm convergence and correctness, then decide the next step.
- **Before launching, show the user the full `vasp_runner.py` command and get approval** (including `--env-script`, `--gpu-per-task`, `--fixed-gpu-layout`).
- When both GPU and CPU are available, **do not default to CPU**; first ask the user GPU vs CPU and the number of GPUs.
- With a scheduler, ask for partition / nodes / walltime.
- Wait with an `until` loop / Monitor / `run_in_background`; **no foreground `sleep`**.

### STRICT HARDWARE ALIGNMENT
Follow `run-vasp`: 1 rank ↔ 1 GPU; number of GPUs = number of parallel jobs (e.g. 2 displaced configurations = 2 GPUs). Under `start_new_session` without a tty, `vasp_gpu` may report "No INCAR found" due to an OpenMPI cwd resolution issue; passing **absolute paths** to the runner's `--dirs` fixes it. For multiple local GPUs use `--gpu-per-task 1` + `--fixed-gpu-layout`.

---

## Provenance of VASP files

- **POSCAR / POTCAR / KPOINTS**: never hand-written. Obtain the structure via `structure-builder` + `mcp__vasp_agent__setup_vasp_inputs` (pass `work_dir` to generate POTCAR/POSCAR/INCAR separately for each displaced configuration/directory). Use the pymatgen/MP **recommended** semicore potentials for POTCAR (Mg_pv, O, etc.); use `potcar_overrides` only when the user explicitly specifies otherwise.
- **INCAR**: may be copied from this skill's template or the `workflow-relax` template and modified (ENCUT / ISMEAR / KSPACING / IBRION / NSW, etc.); do not hand-write POSCAR.

---

## Quick reference of key pitfalls (and why)

| Symptom | Cause | Workaround |
|---|---|---|
| `phonopy: error: '--dim' is a setup operation` | v4 moved setup operations to `phonopy-init` | Use `phonopy-init --dim ... -d` |
| `'--nac' was removed in phonopy v4` | NAC is enabled automatically | Put BORN in the directory, do not add `--nac` |
| `'--fc-symmetry'` not accepted | Main command renamed it `--fc-spg-symmetry` | Use `phonopy --writefc --fc-spg-symmetry` |
| `Phonopy(...) unexpected keyword 'factor'` | v4 constructor has no factor | Put factor in `nac_params` |
| `Phonopy has no 'load_force_constants'` | v4 uses an attribute | `ph.force_constants = parse_FORCE_CONSTANTS(...)` |
| `cannot import read_BORN` / `parse_BORN` missing fields | Fields changed | Parse BORN manually to build `nac_params` (get_born_params.py) |
| `phonopy-vasp-born` wrote no BORN | Only prints to stdout | `phonopy-vasp-born > BORN` |
| LO-TO splitting = 0 (even in qpoints) | `run_qpoints` does not apply NAC at Γ | Extract with `run_band_structure` |
| VASP reports "No INCAR found" for displaced configurations | Ambiguous resolution of relative `--dirs` paths | Use absolute paths for `--dirs` |
| vasp_gpu startup reports missing MPI / help files | Misplaced OpenMPI prefix | Set `OPAL_PREFIX` in `--env-script`; `run-vasp` probes |
| Monitor falsely reports DFPT finished | Matched the word "BORN" | Wait for `General timing` + fully written vasprun.xml |
| Foreground `sleep` blocked | Bash tool rule | `until` loop / Monitor / `run_in_background` |
| `get_conventional_standard_structure` errors | pymatgen API change | Build the conventional cell manually (build_conventional_cell.py) |

---

## Appendix: quick reference of usable v4 phonopy API members

- Constructor: `Phonopy(unitcell, supercell_matrix=..., primitive_matrix=...)` (no `factor`)
- Force constants: read with `ph.force_constants = parse_FORCE_CONSTANTS("FORCE_CONSTANTS")`; write with `phonopy --writefc`
- NAC: `ph.nac_params = {born, dielectric, factor, primitive, q_direction}`
- Dispersion: `ph.run_band_structure(band_paths, with_eigenvectors=False, labels=..., path_connections=...)` → returned object's `.distances` `.frequencies` `.path_connections`
- DOS: `ph.run_mesh([N,N,N], ...)`; `ph.run_total_dos(freq_min, freq_max, freq_pitch, use_tetrahedron_method=True)` → `.frequency_points` `.dos`
- Units: `CM_PER_THZ = 33.356`

Before writing, confirm the phonopy version: `python -c "import phonopy; print(phonopy.__version__)"`. If <4, the v4 behavior above does not apply; use the old syntax (`--dim`, `--nac`, `--fc-symmetry`).
