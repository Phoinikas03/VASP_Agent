---
name: incar-magnetism-soc
description: "Apply VASP INCAR settings for collinear magnetism, initial MAGMOM choices, antiferromagnetic configurations, noncollinear magnetism, spin-orbit coupling, and magnetic anisotropy. Use when materials contain magnetic elements, the user requests spin/SOC, or a workflow needs magnetic consistency across stages."
---

# INCAR Magnetism and SOC

Use this skill as an INCAR modifier. It does not choose the main workflow and does not run VASP.

## INCAR Contract

### Adds

- Collinear spin: `ISPIN = 2` and explicit `MAGMOM`.
- Projection for magnetic analysis when needed: `LORBIT`.
- Noncollinear/SOC when explicitly required: `LNONCOLLINEAR`, `LSORBIT`, `SAXIS`, and vector `MAGMOM`.

### Overrides

- If enabling SOC after a scalar-relativistic pre-run, preserve the same structure, `POTCAR`, `ENCUT`, and k-point policy unless the workflow says otherwise.
- If switching between FM and AFM trials, only change `MAGMOM`/magnetic ordering and keep all other physics settings fixed.

### Forbids

- Do not add SOC automatically just because heavy elements are present. Recommend it when relevant, but require a clear workflow reason or user agreement.
- Do not leave placeholder `MAGMOM = ...` in a final INCAR.
- Do not mix scalar `MAGMOM` with noncollinear vector settings.

## Workflow Notes

- For unknown magnetic order, prepare clearly named trial directories such as `fm`, `afm_a`, `afm_g`, or user-specified orderings.
- Keep `ISPIN` and magnetic order consistent through convergence, relaxation, static, HSE, EOS, and adsorption comparisons unless performing an explicit magnetic comparison.
- For SOC magnetic anisotropy, compare calculations that differ only in `SAXIS` or magnetic orientation.
