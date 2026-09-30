# Electronic Structure Troubleshooting

## Errors That Can Be Safely Ignored

### ZBRENT: fatal error in bracketing

If `vasprun.xml` has been fully written and `OUTCAR` already contains the final energy or band information, this can usually be treated as a numerical issue near convergence; there is no need to immediately rerun the whole calculation.

## Electronic Steps Not Converging

### Symptoms
- `EDDDAV`
- `Sub-Space-Matrix is not hermitian`
- `NELM` limit reached without `reached required accuracy`

### Suggestions
1. Increase `NELM`
2. Switch to `ALGO = All`
3. Adjust `AMIX` / `BMIX`
4. Check `ISMEAR` / `SIGMA`
5. Check the structure quality

## HSE Out of Memory

### Symptoms
- `LAPACK: Routine ZPOTRF failed`
- OOM / `Killed`

### Suggestions
1. Reduce the k-point density
2. Re-evaluate `KPAR` and `NCORE`
3. If appropriate, remove `PRECFOCK = Fast` as a comparison
4. Reduce the number of concurrent jobs or add nodes/GPU memory

## WAVECAR Read Failure

### Symptoms
```text
WAVECAR: reading failed
```

### Suggestions
1. Ensure `ENCUT` is identical before and after the restart
2. Ensure `KSPACING` / `KPOINTS` are identical
3. If parameters have changed, delete the old `WAVECAR` and set `ISTART` to `0`

## Band Gap Is 0, but the Material Should Be a Semiconductor

### Suggestions
1. Check whether `ISMEAR` was mistakenly set to a metallic smearing
2. Check whether `SIGMA` is too large
3. Check whether the k-point mesh is dense enough
4. Confirm the HSE / hybrid-functional settings actually took effect

## HSE Extremely Slow or Not Progressing

### Suggestions
1. Re-evaluate the k-point density
2. For large systems, try `ALGO = Damped` + `TIME = 0.4`
3. If the user requests hardware-specific tuning, go through `performance` first
4. If the log has not updated for a long time, first determine whether it is stalled, then decide whether to terminate the current run

## DOS / Band Post-processing Problems

### Symptoms
- `vasprun.xml` incomplete
- `PROCAR` missing
- Line-mode KPOINTS written incorrectly

### Suggestions
1. Confirm the non-self-consistent stage uses the correct `ICHARG`
2. For projections, confirm `LORBIT` is enabled
3. For line-mode bands, check the high-symmetry path format in `KPOINTS`
4. If the electronic steps finished but `vasprun.xml` is incomplete, first determine whether only post-processing needs to be redone rather than rerunning the whole calculation
