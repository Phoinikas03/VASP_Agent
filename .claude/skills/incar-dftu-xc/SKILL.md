---
name: incar-dftu-xc
description: "Apply VASP INCAR settings for DFT+U, exchange-correlation functional choices, vdW corrections, meta-GGA, and hybrid functional consistency. Use when a workflow needs LDAU tags, U values from literature, IVDW, METAGGA, LHFCALC/HSE, or needs to avoid incompatible functional settings."
---

# INCAR DFT+U and XC

Use this skill as an INCAR modifier for physics-changing functional choices. Do not use it for performance-only tuning.

## INCAR Contract

### Adds

- DFT+U: `LDAU`, `LDAUTYPE`, `LDAUL`, `LDAUU`, `LDAUJ`, and usually `LMAXMIX` for d/f systems.
- vdW correction when justified: `IVDW` or an explicitly requested vdW scheme.
- Hybrid functional settings when requested by the workflow: `LHFCALC`, `AEXX`, `HFSCREEN`, `ALGO`, `TIME`, and related tags.
- Meta-GGA settings when requested: `METAGGA` and any required compatible tags.

### Overrides

- If literature or user-provided U values are used, record the source and keep the same values across all comparable stages.
- If a workflow moves from PBE SCF to HSE, preserve `ENCUT`, k-point policy, `POTCAR`, and structure; make HSE-specific tags explicit rather than inheriting ambiguous old values.

### Forbids

- Do not invent `LDAUU` values for a production calculation without user approval or a cited local/literature source.
- Do not combine incompatible functional choices without a workflow-specific reason.
- Do not change functional settings between adsorption components, EOS scale points, convergence points, or PBE/HSE stages unless doing an explicit comparison.

## Notes

When information is missing, prefer: local workflow references, then `research-literature`, then a concise question to the user.
