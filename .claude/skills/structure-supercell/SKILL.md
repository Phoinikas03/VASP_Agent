---
name: structure-supercell
description: Performs linear supercell expansion of crystal structures (Supercell Generation). Triggered when the user asks to "enlarge the POSCAR", "build a supercell", or specifies expansion factors (e.g. 2x2x2). Uses pymatgen, and confirms the change in atom count with the user before execution.
---

# Supercell Generation Skill

This Skill expands an existing `POSCAR` file linearly by the specified multiples.

## Trigger Conditions
- The user mentions "supercell expansion", "build a supercell", or "enlarge the structure".
- The user gives specific expansion factors, e.g. "3x3x1", "2*2*2", "double the a and b directions", etc.

## Execution Logic

1. **Parse the request**: extract the expansion factors $n_1, n_2, n_3$ along the three directions from the user's instruction. If the user only says "enlarge by 2x", default to $2\times2\times2$.
2. **Check the environment**: make sure a `POSCAR` exists in the current directory.
3. **Pre-compute and confirm**:
   - Use `scripts/make_supercell.py --info` to get the information before and after expansion (change in total atom count).
   - You **must** report to the user: "About to expand the structure from N atoms to M atoms (factors: $n_1 \times n_2 \times n_3$). Continue?"
4. **Perform the expansion**: after the user confirms, run `scripts/make_supercell.py`.
5. **Handle the result**: generate a `POSCAR_supercell` file and remind the user to check it. Do not overwrite the original `POSCAR` automatically unless the user explicitly asks.

## Notes
- If the expanded cell has too many atoms (e.g. more than 500), specifically warn the user that the computational cost will increase significantly.
- Preserve the integrity of atom labels and the coordinate system.

## Execution Mode (consistent with the system ITERATIVE EXECUTION RULE)

This skill only generates structures via `scripts/make_supercell.py`. If the user needs to run VASP **separately** on **multiple** supercell schemes or output structures, submit them **directory by directory** and check each step; **do not** use `for` or monolithic Bash to submit all related VASP runs at once. All actual calculations go through Skill `run-vasp` (including the environment probe and **STRICT HARDWARE ALIGNMENT** in Steps 2-3), and follow its Step 4: call `vasp_runner.py --dirs` **in batches**, reading OUTCAR/logs in between before continuing; do not queue all directories in a single call without checks.

## Core Principles

- **Do not use supercell expansion as an excuse for batch VASP runs**: structure preparation can be done in one go; once you enter `mpirun` / `vasp_runner`, execution must still be iterative, consistent with skills such as `workflow-relax` and `workflow-eos-lattice-constant`.
