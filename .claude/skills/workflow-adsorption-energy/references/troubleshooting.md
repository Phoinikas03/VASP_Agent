# Adsorption Energy Calculations: Common Issues

## No E0 in OSZICAR or extraction fails

- Confirm the ionic steps have finished: check whether `NSW` was exhausted or `EDIFFG` was satisfied.
- If the calculation crashed: check the end of `OUTCAR` and `stderr`; fix the INCAR and restart from `CONTCAR`→`POSCAR`.

## Energies of the three steps are not comparable

- **ENCUT** and **KSPACING** (or **KPOINTS**) should be kept identical across the three steps, unless the user explicitly adopts a different strategy and documents it.
- **POTCAR** versions and **PBE/functional** must be identical; do not mix pseudopotentials between the gas phase and surface.
- The surface and adsorption systems should use the **same slab thickness and vacuum layer** convention; only the adsorption configuration changes the position of the adsorbed molecule.

## Adsorption energy sign

- Convention `E_ads = E(adsorbed) - E(CO) - E(surface)`: exothermic adsorption is usually **negative** (consistency with the specific literature convention is fine, but it must be stated in the report).

## Relationship to convergence

- If publication-grade accuracy of energy differences is needed, first converge **ENCUT/KSPACING** (static single points, `NSW=0`) for the **same structural convention**, then use the parameters in the three geometry optimizations; the **user's consent must be obtained** before starting the convergence scan (see the standalone skill).
