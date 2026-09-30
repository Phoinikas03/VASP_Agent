# VASP Error Recovery Guide

General VASP troubleshooting knowledge base, focused on the most common problems in workflows such as `run-vasp`, `relax`, and `workflow-electronic-structure`.

## Electronic steps not converging

### Symptoms
- `EDDDAV: ... eigenvalues not converged`
- `WARNING: Sub-Space-Matrix is not hermitian`
- `reached required accuracy` still not seen after hitting the `NELM` limit

### Suggestions
1. Increase `NELM`, e.g. from `60` to `100` or `200`
2. Switch to `ALGO = All` or another more robust algorithm
3. Adjust mixing parameters, e.g. `AMIX = 0.2`, `BMIX = 0.0001`
4. For metals, check whether `ISMEAR` / `SIGMA` are reasonable
5. For semiconductors/insulators, avoid an overly large `SIGMA`
6. Check the initial structure for atoms too close together, wrong symmetry, or bad geometry

## Ionic steps not converging

### Symptoms
- `NSW` limit reached while forces are still large
- Structure keeps oscillating and fails to settle

### Suggestions
1. Increase `NSW`
2. When continuing, copy `CONTCAR` to `POSCAR`
3. Try a more robust ionic optimizer, e.g. `IBRION = 1` or `IBRION = 3`
4. Reduce `POTIM`, e.g. to `0.2` or `0.1`
5. If needed, first relax with the lattice fixed, then release the cell

## ZBRENT: fatal error in bracketing

### Symptoms
```text
ZBRENT: fatal error in bracketing
```

### Suggestions
1. First check whether `vasprun.xml` and `OUTCAR` have been fully written
2. If the post-processing files are complete, this error is often a recoverable issue near convergence and may be ignored depending on the case
3. If a rerun is needed, reduce `POTIM`
4. Switch to damped dynamics with `IBRION = 3`
5. Check whether the initial structure is too aggressive

## Out of memory / LAPACK errors

### Symptoms
- `LAPACK: Routine ZPOTRF failed`
- `allocation failed`
- `Killed`
- `segmentation fault`

### Suggestions
1. Reduce concurrency; avoid packing multiple heavy tasks onto the same node
2. Reduce k-point density or `NBANDS`, or tone down overly aggressive parallel settings
3. For higher-level methods such as HSE/GW, prioritize reducing GPU/CPU resource contention
4. If needed, add nodes or move to a machine with more memory

## WAVECAR read failure

### Symptoms
```text
WAVECAR: reading failed
```

### Suggestions
1. Make sure `ENCUT` is consistent between stages
2. Make sure `KSPACING` / `KPOINTS` are consistent
3. If parameters have changed, delete the old `WAVECAR` and set `ISTART` back to `0`

## POTCAR and POSCAR element order mismatch

### Symptoms
- Abnormal structure
- Atoms "explode"
- Results are clearly unreasonable

### Suggestions
1. Check the `VRHFIN` order in `POTCAR`
2. Make sure it exactly matches the element order on line 6 of `POSCAR`
3. If inconsistent, regenerate `POTCAR` via `setup_vasp_inputs`
4. If the user specified a pseudopotential variant, the same `potcar_overrides` (e.g. `{"Cr": "Cr_pv"}`) must be passed when regenerating; if the tool rejects the override, stop and report the error; do not generate, concatenate, or copy `POTCAR` manually with Bash/Python

## Abnormal cell expansion / collapse

### Symptoms
- Cell shape clearly distorted after `ISIF = 3`
- Volume changes unreasonably fast

### Suggestions
1. First relax only the atoms with `ISIF = 2`, then switch to `ISIF = 3`
2. Check the initial structure and experimental lattice parameters
3. Check whether `PSTRESS` was set by mistake
4. If needed, turn off incorrect symmetry, or recheck the structure source

## HSE calculation extremely slow / seemingly no progress

### Symptoms
- No new output for a long time
- Low GPU utilization but the task never finishes

### Suggestions
1. Check whether the k-point mesh is too dense
2. Re-evaluate `KPAR` and `NCORE` for the hardware
3. For large systems, try `ALGO = Damped` with `TIME = 0.4`
4. If the log and `OUTCAR` have not updated for a long time, first determine whether it is stuck, then decide whether to terminate

## Band gap is 0, but the material should be a semiconductor

### Suggestions
1. Check `ISMEAR` and `SIGMA`
2. Check whether the k-point mesh is dense enough
3. Confirm that the HSE / hybrid functional parameters actually took effect
4. Check whether the structure is still not well relaxed

## Stuck / no new output for a long time

### How to judge
1. Look at the last modification times of the main log, `OUTCAR`, and `OSZICAR`
2. Check whether the runner state is still marked `running`
3. Check whether the current directory still has processes or jobs belonging to this run

### Suggestions
1. If output keeps updating, prefer to keep waiting
2. If there has been no update for a long time and it can be proven to be the current run, first ask the user whether to stop it
3. After stopping, modify parameters and rerun via `vasp_runner.py`
