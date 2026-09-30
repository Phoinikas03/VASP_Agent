# Common Structural Relaxation Errors and Fixes

## Ionic Steps Not Converged (NSW Limit Reached)

### Symptoms
`check_convergence.py` outputs `nsw_reached: true`, `ionic_converged: false`.

**Fixes (try in order)**:
1. Copy CONTCAR to POSCAR (`cp CONTCAR POSCAR`), increase NSW, and restart
2. Check whether `max_force_eV_A` is close to EDIFFG -- if it is already close (within 2x), loosen EDIFFG (e.g. from -0.01 to -0.02) and restart
3. Check whether the structure is reasonable: interatomic distances that are too short can prevent convergence; consider rebuilding the initial structure

---

## BRIONS: POTIM Warning

### Symptoms
```
BRIONS problems: POTIM should be increased
```
or
```
WARNING: Sub-Space-Matrix is not hermitian in DAV
```

**Fix**: reduce `POTIM` from 0.5 to 0.3 (or even 0.1), or change `IBRION` from 2 to 1 (quasi-Newton method). If the structure changes drastically, you can also pre-relax with a few MD steps (`IBRION=0`) before switching back to CG.

---

## Electronic Steps Not Converged

### Symptoms
`electronic_converged: false` in OUTCAR, and the energy oscillates across electronic steps without decreasing.

**Fixes**:
1. Increase `NELM` (e.g. from 60 to 100)
2. Change `ALGO` from `Fast` to `Normal`, or from `Normal` to `All` (more stable)
3. Check `SIGMA`: too small a SIGMA for metals (e.g. 0.01) gives insufficient smearing at the Fermi surface; change it to 0.2
4. For magnetic systems, check whether the initial `MAGMOM` moments are reasonable

---

## ZBRENT: fatal error in bracketing

### Symptoms
```
ZBRENT: fatal error in bracketing
please rerun with smaller EDIFF, or copy CONTCAR to POSCAR and rerun
```

**Fix**:
- If the calculation finished normally (`ionic_converged: true`): **safe to ignore**; continue using the results
- If not finished: copy CONTCAR to POSCAR, reduce `EDIFF` (e.g. from 1E-5 to 1E-6), and rerun

---

## Negative Frequencies / Saddle-Point Structures

### Symptoms
After relaxation, the phonon spectrum shows imaginary frequencies, or the structure undergoes unreasonable deformation during the calculation.

**Cause**: the structure is trapped in a local minimum or saddle point; the relaxation did not reach the true ground state.

**Fixes**:
1. Apply a small perturbation to CONTCAR along the imaginary-mode displacement direction and relax again
2. Use a better initial structure (e.g. download a known stable phase from Materials Project)
3. Lower `EDIFFG` (stricter convergence criterion)

---

## Out of Memory / LAPACK Errors

### Symptoms
```
LAPACK: Routine ZPOTRF failed!
```
or the program is killed by OOM.

**Fixes**:
1. Add `NCORE = 4` (or adjust to the cores per node) to enable band parallelization
2. Lower `ENCUT` to ENMAX × 1.0 (at a small cost in accuracy)
3. Increase the number of compute nodes or memory

---

## Pseudopotential and POSCAR Element Order Mismatch

### Symptoms
```
POSCAR and POTCAR are inconsistent
```

**Fix**: check that the element order on line 6 of POSCAR exactly matches the order of the pseudopotentials in POTCAR. Regenerating the POTCAR via `setup_vasp_inputs` usually fixes this automatically; if the user specified pseudopotential variants, the same `potcar_overrides` (e.g. `{"Cr": "Cr_pv"}`) must be passed again; do not generate, concatenate, or copy the POTCAR manually.

---

## Abnormal Lattice Parameter Changes (Volume Explosion/Collapse)

### Symptoms
The volume changes by more than 30% during relaxation, or the lattice angles change drastically.

**Cause**: the initial structure differs too much from the true ground state, or `ISIF=3` is unstable for certain systems.

**Fixes**:
1. First run a few dozen steps with `ISIF=2` (atomic positions only), then switch to `ISIF=3` for full optimization
2. For 2D materials, make sure to use `ISIF=2` or `ISIF=4` to avoid optimizing the lattice along z
3. Check that the initial structure is reasonable (bond lengths and angles within physical ranges)
