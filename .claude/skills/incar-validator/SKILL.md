---
name: incar-validator
description: "Validate VASP inputs before running: check INCAR consistency, stage-to-stage comparability, KSPACING versus KPOINTS conflicts, POSCAR/POTCAR element order, and common tag mistakes. Use before run-vasp or when a workflow edits INCAR files."
---

# INCAR Validator

Use this skill as the final pre-run gate after a `workflow-*` skill and any `incar-*` modifiers have prepared inputs.

## Scope

Validate text inputs and workflow consistency. Do not run VASP. Do not change scientific choices unless the active workflow or user explicitly allows it.

## Checks

- `POSCAR` element order matches `POTCAR` order and any per-element tags such as `MAGMOM`, `LDAUL`, `LDAUU`, and `LDAUJ`.
- `KSPACING` policy is not accidentally overridden by a stale `KPOINTS` file.
- Restart tags are coherent with existing files: `ISTART`, `ICHARG`, `LWAVE`, `LCHARG`, `WAVECAR`, and `CHGCAR`.
- Multi-stage workflows keep critical settings consistent across directories: `ENCUT`, k-point policy, `POTCAR` family, `ISPIN`, `LDAU*`, hybrid/GW tags, and smearing strategy.
- Relax workflows do not accidentally use static-only settings such as `NSW=0` unless intentionally performing static SCF.
- Static, EOS, convergence, DOS, and band workflows do not accidentally relax ions unless intended.
- GPU/CPU performance tags are consistent with `incar-performance` and do not carry stale `NCORE`, `NPAR`, or excessive `KPAR`.

## Output

Report one of:

- `pass`: inputs are internally consistent.
- `warn`: inputs are runnable but have assumptions or possible accuracy issues.
- `fail`: do not call `run-vasp` until the listed conflicts are fixed.

For each `fail`, name the file and exact tag or missing artifact. Prefer minimal fixes over rewriting the whole INCAR.
