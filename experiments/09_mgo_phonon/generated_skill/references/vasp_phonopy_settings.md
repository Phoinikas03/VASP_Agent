# Key INCAR Settings for Each VASP + phonopy Phonon Stage

> These are the **minimal key tags** of each stage. Generate the complete INCAR by composing the workflow baseline via `incar-builder`, with parallel/performance parameters via `incar-performance`. Generate POTCAR/POSCAR for every INCAR with `setup_vasp_inputs`; KSPACING is recommended (avoids a KPOINTS file).

## Stage B Convergence tests (unit cell)
| tag | value | note |
|---|---|---|
| IBRION | -1 | static |
| NSW | 0 | single point |
| ISMEAR | 0 / SIGMA 0.05 | insulators (ionic crystals) use 0; metals use 1 + SIGMA 0.2 |
| PREC | Accurate | |
| ENCUT / KSPACING | scan | adjacent ΔE ≤ 1 meV/atom |

## Stage A Relaxation (workflow-relax)
| tag | value | note |
|---|---|---|
| IBRION | 2 | conjugate gradient |
| ISIF | 3 | optimize cell + ions together (gives the equilibrium lattice constant) |
| EDIFFG | negative | force criterion, e.g. -0.01 |

## Stage D DFPT (dielectric + Born charges, generates BORN)
| tag | value | note |
|---|---|---|
| LEPSILON | .TRUE. | DFPT dielectric tensor + Born effective charges |
| IBRION | -1 | no relaxation |
| NSW | 0 | static |
| ISIF | 2 | |
| PREC | Accurate | |
| EDIFF | 1E-6 | dielectric/Born need tighter electronic convergence |
| ENCUT / KSPACING | converged values | dielectric converges more slowly than energy; do not use a coarse mesh |

## Stage F Supercell force calculations (finite-displacement method)
| tag | value | note |
|---|---|---|
| IBRION | -1 | **no relaxation whatsoever** |
| NSW | 0 | static single point |
| ISIF | 2 | fixed cell |
| PREC | Accurate | forces must be accurate |
| EDIFF | 1E-6 | high-accuracy electronic convergence |
| ENCUT / KSPACING | converged values | consistent with the energy reference |

### Why force calculations must use `IBRION=-1` / `NSW=0`
The core assumption of the finite-displacement method is: measured force = the restoring force of the lattice under this displacement. If the atoms relax again during the calculation (`NSW>0`), the measured forces belong to another, relaxed structure; the second-order force-constant matrix is distorted, and the phonon frequencies and DOS are wrong.

### Why EDIFF should be 1E-6
Force accuracy is governed by the convergence of the electron density. A coarse `EDIFF` (e.g. 1E-4) introduces noticeable noise into the force constants, especially in the high-frequency optical branches. Phonon calculations should use `EDIFF=1E-6`, `PREC=Accurate`.

## Common phonopy commands (v4+)
```bash
# generate supercell displacements (setup operations moved from phonopy to phonopy-init)
phonopy-init --dim 2 2 2 -c POSCAR-unitcell -d

# extract forces from the displaced configurations' vasprun.xml → FORCE_SETS (needs phonopy_disp.yaml + SPOSCAR in the same directory)
phonopy-init -f fc_001/vasprun.xml fc_002/vasprun.xml

# fit force constants (--nac removed; --dim no longer used with the main command; --fc-symmetry renamed --fc-spg-symmetry)
phonopy --writefc --fc-spg-symmetry

# generate BORN (only prints to stdout; redirect is mandatory)
phonopy-vasp-born > BORN
```

## Units
- phonopy uses **THz** internally for frequencies/energies; multiply by `33.356` to convert to cm⁻¹.
- Phonon frequencies can also be given in meV (1 THz = 4.136 meV).

## Ionicity check (is NAC needed?)
- Compositions with a strong electronegativity difference (e.g. Mg-O, Zn-O, Ga-N, metal-halogen) and a non-centrosymmetric structure → net Born charges → **NAC required**.
- Purely non-polar systems (diamond/graphite structures of C, Si, Ge, or metals) → no BORN / NAC needed.
- The most reliable criterion: whether the Born charges from DFPT are significantly non-zero; if close to 0, the NAC effect is negligible.
