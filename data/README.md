# Input Data

This directory contains only the inputs of the experiments reported in the
manuscript: task definitions, initial structures, and reference values used for
evaluation. Calculation results, analysis scripts, and experiment protocols are
in `experiments/`. Raw VASP outputs and agent session logs are available from
the corresponding authors upon reasonable request.

VASP pseudopotentials (`POTCAR`) are not redistributed.

## Input Sets

| Directory | Contents | Used by |
| --- | --- | --- |
| `relax_40/` | 40 initial structures (`<system>/POSCAR`) and `tasks.csv` | `experiments/01_structural_relaxation` |
| `bandgap_24/` | 24 semiconductor structures (`<system>/POSCAR`) and `tasks.csv` | `experiments/02_bandgap`, `experiments/07_atomate2_comparison` (five-system subset) |
| `sol27lc/tasks.csv` | 27 Sol27LC tasks specified by element and crystal type | `experiments/03_sol27lc_dreams` |
| `sol27lc/structures/` | 27 initial structures (`<element>_<type>/POSCAR`) and `structures_manifest.json` | `experiments/06`, `07`, `08` |
| `sol27lc/expert_pbe_reference.csv` | Human-expert PBE lattice constants used as the reference | `experiments/03`, `06`, `07`, `08` |
| `co_pt111/` | Gas-phase CO, clean Pt(111) slab, six adsorbed configurations, and `configurations.csv` | `experiments/04_co_pt111_adsorption` |

## Provenance

**Structural relaxation (`relax_40/`).** Initial structures were obtained from
the Materials Project. Point defects were introduced, and small primitive cells
were expanded into supercells before defect creation (Methods, "Structural
Relaxation").

**Band gap (`bandgap_24/`).** Initial structures were obtained from Springer
Materials. The atomate2 comparison uses Si, GaAs, GaP, ZnO, and Cu2O from this
set after reduction to the standard primitive cell; the extraction script is in
`experiments/07_atomate2_comparison/`.

**Sol27LC tasks (`sol27lc/tasks.csv`).** In the original evaluation
(compared with DREAMS), each task specified only the element and
crystal type; the agent built the structure itself.

**Sol27LC structures (`sol27lc/structures/`).** The revision experiments
(DeepSeek v4 repeated runs, atomate2 comparison, and domain-skill ablation)
supplied an initial structure so that all compared methods started from
identical inputs. Each structure is the standard primitive cell
(pymatgen `get_primitive_standard_structure`, `symprec=1e-3`) of the
`scale_1.000` POSCAR from the corresponding original run (experiment 03).
`structures_manifest.json` records the source run, space group, primitive-cell
volume, and conventional lattice constant of each structure.

**Sol27LC reference (`sol27lc/expert_pbe_reference.csv`).** Human-expert PBE
lattice constants tabulated with the Sol27LC benchmark (Wellendorff et al.,
Phys. Rev. B 85, 235149, 2012) as reported in the DREAMS Supplementary
Information, Section 2, Table 1 (arXiv:2507.14267).

**CO/Pt(111) (`co_pt111/`).** A 4-layer p(2×2) Pt(111) slab with 15 Å vacuum,
gas-phase CO, and six C-down adsorbed configurations (fcc and ontop sites, each
upright, tilted-x, and tilted-y), following the DREAMS adsorption setting.

## Column Definitions

`relax_40/tasks.csv`: `system`, `category` (material category),
`initial_structure_file`, `task_prompt`.

`bandgap_24/tasks.csv`: `system`, `initial_structure_file`, `task_prompt`.

`sol27lc/tasks.csv`: `system`, `element`, `crystal_type` (`bcc`, `fcc`, or
`diamond`), `task_prompt`.

`sol27lc/expert_pbe_reference.csv`: `system_id` (matches the directory names
under `sol27lc/structures/`), `system`, `element`, `crystal_type`,
`expert_pbe_lattice_constant_angstrom` (conventional cubic lattice constant).

`co_pt111/configurations.csv`: `system` (component path), `component_type`
(gas molecule, clean slab, or adsorbed slab), `site`, `orientation`,
`initial_structure_file`, `task_prompt`.
