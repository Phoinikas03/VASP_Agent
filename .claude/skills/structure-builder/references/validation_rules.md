# Validation Rules

## Basic checks

- POSCAR can be parsed by pymatgen.
- Composition and atom count match the requested system.
- Lattice vectors are finite and nonzero.
- Minimum interatomic distance is not suspiciously small.

## Slab checks

- Vacuum should usually be at least 10 A, and 15 A is a common starting point.
- For adsorption calculations, clean slab and adsorbed slab must have the same cell and same slab atom count.
- If bottom layers are fixed, POSCAR should contain Selective Dynamics flags.

## Adsorption checks

- The adsorbate should be above the intended surface, not embedded in the slab.
- The adsorbate should not overlap with surface atoms.
- For CO on metal slabs, check adsorbate-slab distances explicitly, e.g. `--slab-elements Pt --adsorbate-elements C O --min-adsorbate-slab-distance 1.4`; values below this often indicate the wrong CO end was anchored or the molecule was tilted into the slab.
- For site comparisons, the same slab, same cell, same adsorbate, and comparable initial heights must be used across configurations.
- If bottom slab layers are fixed, clean slab and adsorbed slab should use the same fixed-layer convention.
