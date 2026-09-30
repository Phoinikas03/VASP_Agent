# Key INCAR settings for each VASP + phonopy phonon stage

> These are the **minimal key tags** for each stage. Generate the complete INCAR by combining the workflow baseline via `incar-builder`, with parallel/performance parameters via `incar-performance`. Generate POTCAR/POSCAR for every INCAR with `setup_vasp_inputs`, and prefer KSPACING (avoiding a KPOINTS file).

## Stage B convergence tests (unit cell)
| tag | value | notes |
|---|---|---|
| IBRION | -1 | static |
| NSW | 0 | single point |
| ISMEAR | 0 / SIGMA 0.05 | 0 for insulators (ionic crystals); 1 + SIGMA 0.2 for metals |
| PREC | Accurate | |
| ENCUT / KSPACING | scan | adjacent ΔE ≤ 1 meV/atom |

## Stage A relaxation (workflow-relax)
| tag | value | notes |
|---|---|---|
| IBRION | 2 | conjugate gradient |
| ISIF | 3 | optimize cell + ions together (gives the equilibrium lattice constant) |
| EDIFFG | negative | force convergence criterion, e.g. -0.01 |

## Stage D DFPT (dielectric + Born charges, generates BORN)
| tag | value | notes |
|---|---|---|
| LEPSILON | .TRUE. | DFPT dielectric tensor + Born effective charges |
| IBRION | -1 | no relaxation |
| NSW | 0 | static |
| ISIF | 2 | |
| PREC | Accurate | |
| EDIFF | 1E-6 | dielectric/Born need tighter electronic convergence |
| ENCUT / KSPACING | converged values | dielectric converges slower than energy; do not use a coarse mesh |

## Stage F supercell force calculations (finite-displacement method)
| tag | value | notes |
|---|---|---|
| IBRION | -1 | **no relaxation whatsoever** |
| NSW | 0 | static single point |
| ISIF | 2 | fixed cell |
| PREC | Accurate | forces must be accurate |
| EDIFF | 1E-6 | high-precision electronic convergence |
| ENCUT / KSPACING | converged values | consistent with the energy reference |

### Why force calculations must use `IBRION=-1` / `NSW=0`
The core assumption of the finite-displacement method is: measured force = restoring force of the lattice under that displacement. If atoms relax again during the calculation (`NSW>0`), you measure the forces of a different, relaxed structure; the second-order force-constant matrix is distorted accordingly, and both the phonon frequencies and the DOS come out wrong.

### Why EDIFF must be 1E-6
Force accuracy is governed by the convergence of the electron density. A loose `EDIFF` (e.g. 1E-4) introduces noticeable noise into the force constants, especially in the high-frequency optical branches. For phonon calculations, `EDIFF=1E-6` and `PREC=Accurate` are recommended.

## Common phonopy commands (v4+)
```bash
# generate supercell displacements (setup operations moved from phonopy to phonopy-init)
phonopy-init --dim 2 2 2 -c POSCAR-unitcell -d

# extract forces from the displaced configurations' vasprun.xml → FORCE_SETS (requires phonopy_disp.yaml + SPOSCAR in the same directory)
phonopy-init -f fc_001/vasprun.xml fc_002/vasprun.xml

# fit force constants (--nac removed; --dim no longer used by the main command; --fc-symmetry renamed --fc-spg-symmetry)
phonopy --writefc --fc-spg-symmetry

# generate BORN (only prints to stdout, must be redirected)
phonopy-vasp-born > BORN
```

## Units
- phonopy uses **THz** internally for frequency/energy; multiply by `33.356` to convert to cm⁻¹.
- Phonon frequencies can also be given in meV (1 THz = 4.136 meV).

## Judging ionicity (whether NAC is needed)
- Composition with a large electronegativity difference (e.g. Mg-O, Zn-O, Ga-N, metal-halogen) and a non-centrosymmetric structure → net Born charges → **NAC needed**.
- Purely non-polar (C, Si, Ge and other diamond/graphite structures, or metals) → no BORN / NAC needed.
- The most reliable criterion: whether the Born charges from DFPT are significantly non-zero; if they are close to 0, the effect of NAC is negligible.
