# Common Errors and Solutions

## Errors That Can Be Safely Ignored

### ZBRENT: fatal error in bracketing
```
ZBRENT: fatal error in bracketing
please rerun with smaller EDIFF, or copy CONTCAR to POSCAR and rerun
```
**Cause**: The ionic steps oscillate near the convergence criterion; this is a known numerical issue.  
**Fix**: If `vasprun.xml` has been fully written and OUTCAR contains the final energy/band-structure information, treat the calculation as successful, **simply ignore this error**, and continue with post-processing.

---

## Electronic Steps Not Converging

### Symptoms
```
WARNING: Sub-Space-Matrix is not hermitian in DAV
```
or `reached required accuracy` does not appear in OUTCAR, but the `NELM` limit has been reached.

**Solutions (try in order)**:
1. Increase `NELM` (e.g. from 60 to 100)
2. Relax `EDIFF` (e.g. from `1E-6` to `1E-5`)
3. Change `ALGO` from `Damped` to `All` (more robust but slower)
4. Check whether `SIGMA` is too large (0.01~0.05 recommended for semiconductors)

---

## HSE Calculation Out of Memory

### Symptoms
```
LAPACK: Routine ZPOTRF failed!
```
or the program is killed directly by OOM.

**Solutions**:
1. Reduce the k-point density
2. Add `NCORE = 4` (or adjust to the cores per node) to parallelize over orbitals
3. Revert `PRECFOCK = Fast` to the default (remove the line) and observe the memory change
4. Increase the number of compute nodes

---

## WAVECAR Read Failure

### Symptoms
```
WAVECAR: reading failed
```
**Cause**: **KSPACING** (or legacy **KPOINTS**) or **ENCUT** differs between the PBE and HSE stages, so the wavefunction format does not match.  
**Fix**: Ensure the HSE `ENCUT` and k-point mesh are identical to those of the PBE SCF stage; if they differ, delete WAVECAR and set the HSE `ISTART` back to `0` (start from scratch, at the cost of losing the PBE warm-start advantage).

---

## Band Gap Is 0 (Metallic)

### Symptoms
`gap.py` outputs `energy_eV: 0.0`, but the material is known to be a semiconductor/insulator.

**Troubleshooting steps**:
1. Check `ISMEAR`: static calculations should use `0` (Gaussian); avoid `ISMEAR=1` or `2` (Methfessel-Paxton)
2. Check whether `SIGMA` is too large (should be ≤ 0.05 eV)
3. Check whether the k-point mesh is dense enough (the gap may lie between high-symmetry points)
4. Confirm `LHFCALC=.TRUE.` is correctly written in the HSE INCAR

---

## HSE Calculation Extremely Slow or Not Progressing

**Common causes**:
- Too many k-points: HSE cost scales with the square of the number of k-points
- `ALGO=All` is extremely slow for large systems: switch to `ALGO=Damped` + `TIME=0.4`
- `PRECFOCK=Normal`: change to `Fast` to speed up (effect on gap accuracy usually < 0.05 eV)
