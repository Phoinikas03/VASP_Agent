# Common Issues and Fixes (Lattice Constant / EOS Workflow)

## 1. Electronic SCF not converged (`electronic_converged: false`)

**Symptom**: `check_convergence.py` shows that `aborting loop because EDIFF is reached` was not reached, or the end of OUTCAR shows the maximum number of electronic steps was hit.

**Things to try**:

- Increase **`NELM`** in `INCAR` (e.g. 80–120).
- Adjust **`ALGO`**: `Normal` → `Fast` or `All`/`Damped` (choose according to whether the system is metallic or insulating).
- Slightly loosen **`EDIFF`** for pre-convergence (for troubleshooting only; production EOS points should return to strict settings).
- Check whether mixing parameters such as **`AMIN`, `BMIX`** are too aggressive; for metals try `IMIX=4`, etc. (should be consistent with literature/experience).
- Check **`CHGCAR/WAVECAR`**: if the previous charge density is poor, delete them and restart the SCF from scratch.

## 2. `fit_eos.py` errors or poor R²

**Symptom**: The fit diverges, `curve_fit` fails, or `R_squared < 0.99`.

**Checks**:

- Whether the energy/volume in the **OUTCAR** of some `scale_*` points is abnormal (SCF not converged, killed midway).
- Whether the **scale range** covers the energy minimum: if the energy still decreases/increases monotonically, **widen the scale range** or add points.
- If the minimum is covered but the bottom has too few points or `fit_eos.py` outputs `refinement_recommended: true`, do not just widen the range; refine around `linear_scale_eq` with a linear scale step of `0.003-0.005`, then refit using the first-round + new points.
- Whether **ENCUT / KSPACING (k-point grid) / POTCAR** are exactly identical across points (otherwise the energies are not comparable).
- Confirm that all are **truly static calculations** (`NSW=0`, `ISIF=2`, no ionic steps).

## 3. Energy jumps sharply with ENCUT or k-points

- Check whether different **POTCAR versions or functionals** were mixed.
- Check whether **`PREC`, `LREAL`** are consistent across calculations.
- At low **ENCUT**, metallic systems may show sawtooth convergence; **increase ENCUT** until the 1 meV/atom criterion is satisfied.

## 4. Pulay stress / volume and energy inconsistent

- For EOS points, changing cell-optimization settings during the calculation is **forbidden**; keep the geometry fixed and change the volume only through the **scaled POSCAR**.
- **Do not use different ENCUT values at different points**.

## 5. Paths and scripts not found

- When the working directory is under `runs/...`, call Skill scripts with **absolute paths**, e.g.:
  - `python .../run-vasp/scripts/check_convergence.py .`
- `templates/INCAR_static` is located in `workflow-eos-lattice-constant/templates/`; copy it into each `scale_*` directory before changing `ENCUT`.

## 6. Materials Project mp-id does not match the structure

- The mp-id found may not be the target **pure phase/structure** (e.g. an oxide vs the elemental solid). After obtaining the POSCAR with **`Skill: structure`**, check whether the **POSCAR elements and space group** match the user's target; if necessary, switch to another mp-id or database entry.
