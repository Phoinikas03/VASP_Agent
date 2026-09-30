---
name: "workflow-bandgap-legacy-alias"
description: "Compatibility entry for the old bandgap skill. This skill has been upgraded to the more general workflow-electronic-structure, which handles electronic-structure tasks such as static SCF, band structure, DOS, and PBE/HSE band gaps. If the user mentions bandgap, the original PBE→HSE band-gap workflow can be followed, but the primary entry point should be understood as workflow-electronic-structure."
---

# Legacy Alias: bandgap -> workflow-electronic-structure

`bandgap` is now a compatibility entry note for the old workflow; the main capability has been upgraded and moved to:

```text
.claude/skills/workflow-electronic-structure/
```

## How to Interpret This Alias

- If the user says "compute bandgap / HSE band gap", the old two-step approach can still be used:
  1. PBE SCF
  2. PBE convergence check
  3. Prepare HSE
  4. **Separately ask the user whether to continue**
  5. If GPUs are available, **separately ask for the GPU count**
  6. Then submit HSE via `run-vasp`

- If the user's intent has expanded to:
  - band structure
  - DOS / PDOS
  - static electronic structure
  - more general electronic-structure analysis

  then follow the `workflow-electronic-structure` skill directly.

## Old Rules That Still Apply

1. Production runs must go through `run-vasp`
2. Anything involving GPU / `KPAR` / `NCORE` / `NPAR` must first go through `incar-performance`
3. The POTCAR must be generated via `setup_vasp_inputs` and follow the Mandatory POTCAR Rules of `workflow-electronic-structure`; for systems containing `Ga/In/Sn/Pb`, confirm that `Ga_d/In_d/Sn_d/Pb_d` are actually used
4. A separate confirmation round is required before starting HSE
5. When GPUs are available, the number of GPUs must be clarified
6. The PBE pre-calculation must save `WAVECAR/CHGCAR`: `LWAVE = .TRUE.` and `LCHARG = .TRUE.`; if you see `.FALSE.`, fix it before submitting PBE
7. `POTCAR`, `ENCUT`, and k-point settings must be consistent between PBE and HSE
8. If the old HSE run may still be alive, do not start a second active process in the same directory
9. On errors or hangs, first diagnose with `vasp-error-recovery`, then decide whether to terminate + rerun

## Hard Reminder Before Submission

The PBE `INCAR` of a PBE -> HSE band-gap task must keep the warm-start files:

```text
LWAVE  = .TRUE.
LCHARG = .TRUE.
```

If a template or the model produces `LWAVE = .FALSE.` or `LCHARG = .FALSE.`, change it to `.TRUE.` before submitting PBE.

## References

- Main skill: `workflow-electronic-structure`
- Run orchestration: `run-vasp`
- Performance tuning: `incar-performance`
- Error recovery: `vasp-error-recovery`
