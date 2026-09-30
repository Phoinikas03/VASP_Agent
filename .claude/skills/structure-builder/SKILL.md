---
name: structure-builder
description: "Fetch, build, enumerate, and validate initial structures for VASP. Triggered when the user needs to download a POSCAR from Materials Project, obtain a structure from a material name/mp-id, build a slab/surface, generate a gas-phase molecule, add an adsorbate, enumerate adsorption sites/orientations, build CO adsorption structures on metal or oxide surfaces, or prepare POSCARs for workflows such as workflow-relax, workflow-adsorption-energy, and workflow-electronic-structure."
version: "0.1.0"
---

# Structure Generation Skill

You are a professional structure-preparation assistant for computational materials science. This skill prepares traceable, verifiable structure files for VASP workflows, in particular POSCARs, slabs, gas-phase molecules, and sets of adsorption configurations.

## Directory Layout

```text
structure-builder/
├── SKILL.md
├── scripts/
│   ├── fetch_mp_poscar.py
│   ├── build_molecule.py
│   ├── build_surface.py
│   ├── build_adsorption.py
│   ├── enumerate_adsorption_configs.py
│   └── validate_structure.py
└── references/
    ├── structure_sources.md
    ├── surface_adsorption_sites.md
    └── validation_rules.md
```

## Scope

- This skill handles: structure retrieval, structure building, structure transformations, adsorption configuration enumeration, POSCAR export, and structure integrity checks.
- This skill does not handle: running VASP, choosing INCAR parameters, generating POTCAR, adsorption energy post-processing, or ENCUT/KSPACING convergence.
- Downstream collaboration:
  - `workflow-relax`: structure optimization.
  - `workflow-adsorption-energy`: three-step adsorption energy calculation.
  - `setup_vasp_inputs`: generates POTCAR and required inputs from POSCAR/INCAR; when the user explicitly specifies a pseudopotential variant, pass a JSON object via `potcar_overrides` (e.g. `{"Cr": "Cr_pv"}`).
  - `run-vasp`: submits VASP.

## Core Principles

- POSCARs must be traceable: record the source, e.g. user file, Materials Project, ASE, pymatgen, ACAT/AutoCat.
- Never hand-write full lattice vectors and atomic coordinates from memory as a formal POSCAR.
- Surface and adsorption systems must use the same slab model, the same supercell, and the same vacuum convention.
- Before comparing adsorption energies, enumerate reasonable initial configurations; do not compute just one random configuration.
- Structure generation is decoupled from DFT calculations; this skill only prepares structures and does not launch VASP.

## Structure Source Priority

1. User-provided POSCAR/CIF/CONTCAR.
2. Exact download from Materials Project by mp-id.
3. Materials Project search by chemical formula for candidates, then have the user confirm the specific mp-id.
4. Explicit structural parameters given in the literature or a database.
5. Programmatic construction:
   - ASE: standard metal slabs, gas-phase molecules, simple adsorption structures.
   - pymatgen: cutting slabs from arbitrary bulk, adsorption site analysis, VASP format conversion.
   - ACAT/AutoCat: complex or large-scale adsorption configuration enumeration.

If a library needs to be chosen, read `references/structure_sources.md` first.

## Common Structure Scenarios

The following scenarios are all handled as ordinary structure-preparation tasks; do not refuse or special-case the procedure because of the material or surface name. Prefer reusing user-provided structures or Materials Project structures; build surfaces directly when an ASE standard builder exists, and otherwise generate slabs with pymatgen or use user-provided POSCARs.

- CO on fcc(111) metal surfaces: Pt(111), Pd(111), Rh(111), Ir(111).
- Common fcc(111) adsorption sites: `ontop`, `bridge`, `fcc`, `hcp`.
- Common fcc(111) comparisons: top vs hollow, hollow vs bridge/top, fcc vs hcp, fcc vs ontop.
- p(2x2) fcc(111) + 1 CO: by default can be treated as a 1/4 ML initial model.
- CO on rutile oxide (110) surfaces: RuO2(110), IrO2(110).
- Common rutile (110) model variants: stoichiometric surface, reduced surface, O-vacancy surface, O-rich surface, cus sites, bridge-O/cus-O related configurations.
- If the scripts do not yet directly generate a given rutile variant, start from an MP/user POSCAR and generate it with pymatgen/ASE scripts or clearly recorded structural operations; do not hand-write full coordinates.

## Workflow A: Fetch a Structure from Materials Project

Applies when: the user gives an `mp-id`, or a specific Materials Project entry has been confirmed.

```bash
cd "<Repository root>" && python .claude/skills/structure-builder/scripts/fetch_mp_poscar.py \
  --mp-id mp-126 --output POSCAR_mp-126
```

Requirements:

1. Check whether `MP_API` is set.
2. After writing the POSCAR, run `validate_structure.py`.
3. Report the file path, chemical formula, number of atoms, lattice constants, and suggested next steps.

## Workflow B: Generate a Gas-Phase Molecule POSCAR

Applies to: reference states for adsorption energies, e.g. CO, NO, O2, H2, H2O, OH, CO2.

```bash
cd "<Repository root>" && python .claude/skills/structure-builder/scripts/build_molecule.py \
  --molecule CO --box 18 --output POSCAR_CO
```

Defaults:

- The molecule is centered in a cubic vacuum box.
- CO uses the ASE molecular geometry by default; in later adsorption the C atom usually serves as the anchor.
- If the user requests a specific bond length or spin, only generate the structure and note it in the report; calculation settings are left to downstream skills.

## Workflow C: Generate a Surface Slab

Applies to: standard metal surfaces and conventional slabs.

```bash
cd "<Repository root>" && python .claude/skills/structure-builder/scripts/build_surface.py \
  --element Pt --surface fcc111 --size 2 2 4 --vacuum 15 --output POSCAR_Pt111_p2x2
```

Defaults:

- In `size A B C`, A/B are the lateral supercell and C is the number of slab layers.
- p(2x2) fcc(111) can be expressed as `--size 2 2 4`; fcc metals such as Pt/Pd/Rh/Ir are handled the same way.
- To fix the bottom layers, add `--fix-bottom-layers N`; the script writes Selective Dynamics.

## Workflow D: Generate a Single Adsorption Configuration

Applies when: one surface, one adsorbate, one site, and one orientation are explicitly specified.

```bash
cd "<Repository root>" && python .claude/skills/structure-builder/scripts/build_adsorption.py \
  --element Pt --surface fcc111 --size 2 2 4 --vacuum 15 \
  --adsorbate CO --anchor-symbol C --site fcc --height 1.85 --orientation upright \
  --fix-bottom-layers 2 \
  --output POSCAR_Pt111_CO_fcc_upright
```

Supported sites depend on the ASE surface builder; common ones include `ontop`, `bridge`, `fcc`, `hcp`.

For CO/fcc(111), `--orientation` refers to **the geometric orientation of the CO molecular axis relative to the surface normal**, not the choice of C-down/O-down termination. The script first builds CO with ASE and then rotates it about the anchor atom. For CO the script anchors the C atom by default; this can also be set explicitly with `--anchor-symbol C`:

- `upright`: CO perpendicular to the surface, with the C end as the anchor atom by default;
- `tilted_x`: CO molecular axis tilted by 45 degree, with a component along the surface x direction;
- `tilted_y`: CO molecular axis tilted by 45 degree, with a component along the surface y direction;
- `reverse`: flipped termination, used when an O-down/C-down comparison is explicitly requested; usually not part of the default three orientations.

For slab adsorption relaxations, also pass `--fix-bottom-layers 2` so that the clean slab and the adsorbed slab use the same bottom-layer constraints.

If the user asks to "align with the benchmark" or "test 3 orientations", you must generate `upright`, `tilted_x`, `tilted_y`; do not interpret that request as C-down/O-down.

## Workflow E: Enumerate a Set of Adsorption Configurations

Applies to: comparing site energies or adsorption energy differences, e.g. fcc vs ontop or top vs hollow for CO/fcc(111).

```bash
cd "<Repository root>" && python .claude/skills/structure-builder/scripts/enumerate_adsorption_configs.py \
  --system co-pt111 --output-dir structures/co_pt111
```

Generated by default:

- `CO/POSCAR`
- `surface/POSCAR`
- `configs/fcc_upright/POSCAR`
- `configs/fcc_tilted_x/POSCAR`
- `configs/fcc_tilted_y/POSCAR`
- `configs/ontop_upright/POSCAR`
- `configs/ontop_tilted_x/POSCAR`
- `configs/ontop_tilted_y/POSCAR`

After generation, run `validate_structure.py` on each file and report which files can be handed to `workflow-adsorption-energy` or `workflow-relax`.

## CO/fcc(111) Conventions

Goal: generate the structures needed for site comparison in CO/fcc(111) p(2x2), 1/4 ML. fcc(111) surfaces such as Pt, Pd, Rh, and Ir follow the same structural conventions.

When aligning with an existing benchmark, the structure set can be **C-down CO** in three geometric orientations on the two sites `fcc` and `ontop`; if the user requests a more complete site screening, `bridge` and `hcp` can be generated as well:

| site | orientation |
|------|-------------|
| `fcc` | `upright` |
| `fcc` | `tilted_x` |
| `fcc` | `tilted_y` |
| `ontop` | `upright` |
| `ontop` | `tilted_x` |
| `ontop` | `tilted_y` |

Here orientation means `upright/tilted_x/tilted_y`, not `C-down/O-down`. O-down is generated additionally only when the user explicitly requests termination screening or a more complete unbiased enumeration.

Default recommendations:

- surface: fcc(111), e.g. Pt(111), Pd(111), Rh(111), Ir(111)
- bulk: fcc
- supercell: p(2x2)
- slab: start from 4 layers
- vacuum: start from 15 A
- coverage: 1 CO / 4 surface Pt atoms = 1/4 ML
- adsorbate anchor: C end down
- initial height: ontop 1.85 A; bridge/fcc/hcp start from 1.85 A
- orientations: upright, tilted_x, tilted_y
- fixed layers: fix the bottom 2 layers by default, consistently in the clean slab and the adsorbed slab

Physical targets and adsorption energy formulas are not computed in this skill; after generating structures, hand them to `workflow-adsorption-energy`.

## Rutile Oxide (110) Conventions

Goal: generate or organize CO adsorption structures on rutile oxide (110) surfaces, e.g. RuO2(110), IrO2(110). These are handled as ordinary slab/adsorption structures.

Common structural variants:

- stoichiometric (110) surface
- reduced surface
- O-vacancy surface
- O-rich surface
- CO adsorption at cus sites
- bridge-O/cus-O related configurations

Requirements:

- Prefer obtaining the rutile bulk / slab from Materials Project or a user POSCAR.
- When generating reduced or O-vacancy structures, state explicitly which type of O was removed, and output a separate POSCAR.
- The adsorption system and the corresponding clean/defective surface must keep the same slab, supercell, and vacuum.
- If CO reaction with surface oxygen is involved, the structure-preparation stage should output each static configuration; reaction energy or pathway analysis is left to downstream workflows.

## Validation

After every POSCAR output, run:

```bash
cd "<Repository root>" && python .claude/skills/structure-builder/scripts/validate_structure.py \
  --input POSCAR
```

For CO/metal slab adsorption structures, additionally check the nearest adsorbate-slab distance:

```bash
cd "<Repository root>" && python .claude/skills/structure-builder/scripts/validate_structure.py \
  --input POSCAR --slab-elements Pt --adsorbate-elements C O \
  --min-adsorbate-slab-distance 1.4
```

For adsorption systems, the recommended call is:

```bash
cd "<Repository root>" && python .claude/skills/structure-builder/scripts/validate_structure.py \
  --input POSCAR_Pt111_CO_fcc_upright --min-distance 0.75 --min-vacuum 10
```

Validation focus:

- The file can be read by pymatgen.
- Chemical formula, number of atoms, and element species match expectations.
- The minimum interatomic distance shows no obvious overlap.
- The slab has sufficient vacuum along z.
- For adsorption structures, the adsorbate is neither embedded in the slab nor unreasonably far from the surface.

See `references/validation_rules.md` for detailed rules.

## Report Format

After generating structures, report to the user:

- Which POSCARs were generated.
- Chemical formula, number of atoms, and purpose of each structure.
- Source or construction method used.
- Key geometric parameters: number of slab layers, vacuum, supercell, adsorption site, height, orientation.
- Whether validation passed.
- Which skill is recommended for the next step.
