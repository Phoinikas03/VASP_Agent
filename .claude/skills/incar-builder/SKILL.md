---
name: incar-builder
description: "Build or revise VASP INCAR files by composing a workflow base profile with optional INCAR modifier skills. Use when preparing INCAR for relax, static SCF, EOS, adsorption, phonon, NEB, AIMD, response, or GW/BSE workflows, especially when several physical models must be combined without tag conflicts."
---

# INCAR Builder

Use this skill to assemble a coherent INCAR from reusable parts. It does not run VASP and does not choose a scientific workflow by itself.

## Role

Start from the active `workflow-*` skill's base profile, then apply any requested `incar-*` modifiers in a documented order. Keep physics-changing choices separate from performance-only choices.

Recommended order:

1. Base workflow profile: `workflow-relax`, `workflow-electronic-structure`, `workflow-eos-lattice-constant`, `workflow-adsorption-energy`, or `workflow-convergence`.
2. Accuracy and comparability: `ENCUT`, `KSPACING` or explicit `KPOINTS` policy, `EDIFF`, `PREC`, `LREAL`.
3. Electronic occupation: `incar-smearing-precision`.
4. Physical model modifiers: `incar-magnetism-soc`, `incar-dftu-xc`, vdW/dipole settings if required by the workflow.
5. Output requirements: wavefunction, charge, DOS, projection, electrostatic potential, ELF, etc.
6. Performance-only settings: `incar-performance`.
7. Final check: `incar-validator`.

## INCAR Contract

When writing or changing an INCAR, include a short `INCAR_explanation.md` or workflow-specific notes file with:

- Base workflow and template source.
- Applied modifier skills.
- Tags added, overridden, removed, or deliberately left unset.
- Tags that must remain consistent across stages or directories, such as `ENCUT`, `KSPACING`, `POTCAR` choice, `ISPIN`, `LDAU*`, and hybrid/GW settings.

## Conflict Rules

- Do not let a performance request alter physics tags such as `ENCUT`, `KSPACING`, `ISMEAR`, `SIGMA`, `AEXX`, `HFSCREEN`, or `LDAUU` unless the user explicitly asks for a benchmark or quick test.
- Do not silently mix `KSPACING` and an old `KPOINTS` file. Use one k-point policy and remove or regenerate conflicting files through the workflow.
- Preserve multi-stage consistency: PBE to HSE, EOS scale directories, adsorption subdirectories, and convergence scans must use matching physics-critical settings unless the user asks for a controlled comparison.
- If a modifier requires information that is not known, stop before writing final INCAR and ask only for the missing scientific choice.
