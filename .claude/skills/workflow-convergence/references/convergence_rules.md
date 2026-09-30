# Cutoff Energy and K-point Convergence Test Criteria

Intended for **unit-cell static single-point energies** (`NSW=0`, volume/structure fixed). Target: total energy difference between adjacent test steps **≤ 1 meV/atom** (normalized by the number of atoms in the unit cell).

## Paths

- Root directory of this Skill: `.claude/skills/workflow-convergence/` (or the absolute path within the project).
- If the current working directory is `runs/<timestamp>/` when running scripts, use **absolute paths to the Skill scripts**, e.g.:
  - `python /.../newvaspagent/.claude/skills/run-vasp/scripts/check_convergence.py .`

## Cutoff Energy ENCUT Test

1. **Starting point**: First read the **ENMAX** of each element in the current system's `POTCAR` and take the largest; for `ENCUT`, **max(ENMAX) × 1.3** is generally used as a reference for the upper bound of the scan range.
2. **Scan sequence (example)**: Fix a **fairly dense** k-point spacing in `INCAR` (e.g. `KSPACING = 0.15`, and **make sure no KPOINTS file is written, or delete it**), and increase ENCUT, e.g. (eV):
   - `250, 300, 350, 400, 450, 500` (the lower bound can be reduced for light elements; raise the upper bound for systems with d/f elements)
3. **Criterion**: For each step compute `ΔE = E(n+1) - E(n)` and convert to **meV/atom**; when **|ΔE| ≤ 1 meV/atom**, take the **larger** ENCUT of the pair as the converged value (err on the high side to avoid Pulay errors).
4. **Record**: Tabulate each step's ENCUT, total energy, and ΔE (meV/atom) in `Convergence_Report.md`.

## KSPACING Test (k-point grid spacing)

1. **Fix**: the **converged ENCUT** obtained in the previous step.
2. **Scan**: Preferably scan **`KSPACING`** in `INCAR` (spacing of k-points in reciprocal space, unit $\text{\AA}^{-1}$), **without writing a KPOINTS file**. Smaller values give denser grids.
   - **Recommended sequence**: `0.30, 0.25, 0.20, 0.15, 0.10`.
   - **Hang-prevention warning**: For most systems (including metals), `0.15` to `0.10` is usually sufficient to reach 1 meV/atom accuracy. **Do not set KSPACING extremely small (e.g. below 0.08)**; otherwise an enormous k-point grid that is impractical to compute will be generated and the process will hang.
3. **Criterion**: Likewise use **1 meV/atom** as the threshold for the energy difference between adjacent steps; after convergence, take the **smaller** KSPACING value for subsequent production calculations (a trade-off between accuracy and cost).
4. **Consistency**: All subsequent calculations whose energies must be comparable (e.g. each EOS `scale_*`, multi-step static calculations for band gaps) must use the **same ENCUT and KSPACING** in **INCAR**, and **make sure no KPOINTS file that would override the settings exists in the working directory** (unless you intentionally use KPOINTS).

## Other Recommendations

- **ISMEAR**: Metals commonly use `ISMEAR=1` or `2` with a small `SIGMA`; insulators commonly use `ISMEAR=0` and `SIGMA=0.05`. Just keep it consistent with the static single-point tests.
- **KGAMMA**: When using `KSPACING`, setting `KGAMMA = .TRUE.` in `INCAR` is recommended to force a Gamma-centered grid.
- **PREC**: `PREC = Accurate` is recommended for static energy comparisons.
- If the system has complex magnetism, **use consistent spin settings** (`ISPIN`, `MAGMOM`, etc.) in the convergence tests and subsequent production runs.
