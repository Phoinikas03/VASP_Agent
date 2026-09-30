# INCAR Essentials for the Three-Step Adsorption Energy Calculation

## Common principles

- **Geometry optimization**: typically `IBRION=2` (or the algorithm specified by the user), a sufficiently large `NSW`, and `EDIFFG` according to the user's accuracy (can be loosened to ~0.05 eV/Å for exploratory runs).
- **Electronic steps**: `EDIFF`, `ALGO`, `NELM` consistent with the metallic/covalent character of the system; pay attention to `ISMEAR`/`SIGMA` for metal surfaces.
- **Spin**: if the user requires **no spin polarization**, use `ISPIN=1`; for magnetic surfaces confirm with the user whether to enable `ISPIN=2` and `MAGMOM`.
- **Cell**: usually **fix the cell and relax only the atoms** → `ISIF=2`; if the user explicitly does not want cell optimization, do not use `ISIF=3`.

## Gas-phase molecule (e.g. CO)

- The box must be large enough to avoid interactions between periodic images (a large vacuum is commonly used).
- If compared with surface calculations, **ENCUT / functional / POTCAR** must be consistent with the subsequent steps.

## Surface (slab)

- The **vacuum layer** along the surface normal must be sufficient to avoid interaction between the top and bottom surfaces.
- To fix bottom-layer atoms, `SELECTIVE DYNAMICS` (in **POSCAR**) is commonly used; in INCAR, `NSW`/`IBRION` settings suffice.

## Adsorption configuration (surf+mol)

- The initial adsorption site and height affect convergence; if not converged, increase `NSW` or improve the initial guess rather than blindly raising the cutoff energy.
