---
name: potcar-policy
description: "Apply the repository policy for VASP POTCAR selection and generation without storing copyrighted POTCAR data in git. Use when preparing VASP inputs, selecting recommended pseudopotential variants, checking element order, or ensuring POTCAR_dir and generated POTCAR files are not tracked."
---

# POTCAR Policy

Use this skill whenever a workflow prepares VASP inputs or discusses pseudopotential selection.

## Policy

- Never commit or stage vendor POTCAR libraries or generated POTCAR files.
- Keep `POTCAR_dir/`, run-directory `POTCAR` files, and other pseudopotential data ignored.
- Generate task-local POTCAR files only through the project's approved input generation path, such as `setup_vasp_inputs`, with explicit `potcar_overrides` when the user requires a variant.
- Do not manually concatenate, edit, or copy POTCAR content in skill instructions.

## Selection Rules

- Use a consistent POTCAR family and variant across comparable calculations.
- Preserve recommended semicore variants where required by the workflow, especially for elements where prior project rules require `_d`, `_pv`, or `_sv` variants.
- If the user specifies a variant, record it in the workflow notes and pass it through the approved generation tool.
- Validate that POSCAR element order and POTCAR order match before running.

## Git Check

Before finalizing repository changes involving VASP input generation, verify:

```bash
git ls-files POTCAR_dir POTCAR '**/POTCAR'
git check-ignore -v POTCAR_dir/POT_GGA_PAW_PBE/Fe/POTCAR
```

If tracked POTCAR data appears, stop and remove it from the index with `git rm --cached`, preserving local files unless the user explicitly asks to delete them.
