---
name: incar-smearing-precision
description: "Choose and validate VASP smearing, precision, and static/relax accuracy tags such as ISMEAR, SIGMA, EDIFF, PREC, LREAL, LASPH, ADDGRID, and related output precision settings. Use when preparing INCAR for metals, semiconductors, insulators, surfaces, DOS/band calculations, or energy comparisons."
---

# INCAR Smearing and Precision

Use this skill as an INCAR modifier whenever occupation and precision choices affect accuracy or convergence.

## INCAR Contract

### Adds or Sets

- `ISMEAR` and `SIGMA` based on material class and workflow stage.
- `EDIFF`, `PREC`, `LREAL`, `LASPH`, and related accuracy tags when the workflow template is incomplete.
- Static/DOS/band choices that differ from relaxation choices, documented in notes.

### Defaults

- Unknown semiconductor/insulator: conservative Gaussian smearing, typically `ISMEAR = 0`, small `SIGMA`.
- Metal relaxation or metallic surface: metallic smearing may be appropriate, but keep the choice consistent across energy differences.
- DOS and band-structure post-SCF steps require careful occupation settings and should not silently inherit a rough relaxation INCAR.

### Forbids

- Do not use a large metallic `SIGMA` for final bandgap extraction unless the workflow explicitly uses it as a controlled approximation.
- Do not compare energies across directories with different smearing settings unless that difference is the study variable.
- Do not lower precision tags just to speed up a production calculation; route performance-only requests through `incar-performance`.

## Handoff

After applying this modifier, call `incar-validator` before `run-vasp` if the calculation will be submitted.
