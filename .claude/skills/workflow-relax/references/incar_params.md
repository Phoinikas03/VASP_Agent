# INCAR Parameter Reference for Structural Relaxation

## Contents
1. [General Parameters](#general-parameters)
2. [Key Parameters by Material Type](#key-parameters-by-material-type)
3. [ISMEAR / SIGMA Selection Guide](#ismear--sigma-selection-guide)
4. [ISIF Selection Guide](#isif-selection-guide)
5. [DFT+U Parameter Reference](#dftu-parameter-reference)
6. [Official Reference Links](#official-reference-links)

---

## General Parameters

| Parameter | Typical value | Description |
|------|--------|------|
| `ENCUT` | ENMAX × 1.3 | 1.3 times the largest ENMAX among all elements in POTCAR, to eliminate the Pulay stress arising from cell volume changes ([see the VASP volume relaxation guide](https://vasp.at/wiki/Volume_relaxation)). |
| `EDIFF` | 1E-6 | Electronic convergence criterion (1E-5 is usually sufficient for structural relaxation as well). |
| `EDIFFG` | -0.02 ~ -0.01 | A negative value sets a force convergence threshold (eV/Å); a positive value sets an energy convergence threshold (eV). Negative values are recommended. |
| `NSW` | 100 ~ 300 | Maximum number of ionic steps; can be set to 500 for complex systems. |
| `IBRION` | 2 | **Conjugate gradient (CG)**, the most robust choice, suited to systems whose **initial structure is poor** and far from equilibrium; if the initial structure is already good (small forces), `IBRION = 1` (quasi-Newton) can be used to speed up convergence. |
| `POTIM` | 0.5 | CG step size; if BRIONS warnings cause non-convergence or atoms flying off, reduce it to 0.3 or lower. |
| `ISIF` | 3 | See the ISIF selection guide below. |
| `PREC` | Accurate | Standard precision, usually sufficient for relaxation and effective at avoiding basis-set truncation errors. |
| `ALGO` | Normal | Standard iterative algorithm. `Fast` is faster but may be unstable for some systems containing transition metals or magnetism. |

---

## Key Parameters by Material Type

### Metals
ISMEAR = 1        # First-order Methfessel-Paxton smearing
SIGMA  = 0.2      # Larger smearing, improves k-point convergence
ENCUT  = 400~520  # Depends on the elements, or use ENMAX * 1.3

### Semiconductors / Insulators
ISMEAR = 0        # Gaussian smearing
SIGMA  = 0.05     # Small smearing, avoids artificial smearing contaminating the true electronic states near the VBM/CBM
ENCUT  = 400~520

### Magnetic Materials (Ferromagnetic/Antiferromagnetic)
ISPIN  = 2
MAGMOM = ...      # Initial magnetic moment per atom, e.g. Fe: 5, Ni: 2, O: 0
ISMEAR = 1 or 0   # 1 for metals, 0 for semiconductors/insulators

Common initial magnetic moments:
| Element | Recommended MAGMOM |
|------|------------|
| Fe   | 5.0        |
| Co   | 3.0        |
| Ni   | 2.0        |
| Mn   | 5.0        |
| Cr   | 3.0        |
| Non-magnetic | 0.0        |

### 2D Materials (Monolayer/Few-Layer)
ISMEAR = 0
SIGMA  = 0.05
LDIPOL = .TRUE.   # Dipole correction (critical for polar 2D materials)
IDIPOL = 3        # Dipole correction direction (3 = z, i.e. perpendicular to the 2D plane)
ISIF   = 2 or 4   # Usually do not optimize the z lattice constant, to avoid vacuum collapse

*Note: the vacuum layer usually needs to be >= 15 Å thick to prevent interactions between periodic images.*

### Strongly Correlated Systems (Oxides with d/f Orbitals, Rare Earths, etc.)
LDAU   = .TRUE.
LDAUTYPE = 2      # Dudarev method (most common; only the effective value Ueff = U-J is needed)
LDAUL  = ...      # l quantum number for each element (d=2, f=3, s/p and others=-1)
LDAUU  = ...      # U value for each element (eV)
LDAUJ  = 0 0 ...  # J can be set to 0 in the Dudarev method
LDAUPRINT = 0

*See the DFT+U Parameter Reference at the end of this file for common DFT+U parameters.*

### Van der Waals Interactions (Layered Materials, Molecular Crystals)
IVDW = 11         # DFT-D3 (Grimme) correction, widely applicable
# or
IVDW = 12         # DFT-D3(BJ) damped correction, usually performs better for layered materials and molecular crystals

---

## ISMEAR / SIGMA Selection Guide

Choosing the right smearing method is crucial for the accuracy of energies and forces ([see the official VASP ISMEAR guidelines](https://vasp.at/wiki/Number_of_k_points_and_method_for_smearing)).

| System type | ISMEAR | SIGMA | Notes |
|---------|--------|-------|------|
| Metal | 1 | 0.1~0.2 | Methfessel-Paxton, improves k-point sampling near the Fermi surface. **Never use for semiconductors**. |
| Semiconductor (band gap > 0.5 eV) | 0 | 0.05 | Gaussian, the safest fallback. Avoids a reduced band gap or spurious states caused by artificial smearing. |
| Insulator (band gap > 2 eV) | 0 | 0.01~0.05 | Same as above; SIGMA can be set even smaller. |
| Unknown type (exploratory) | 0 | 0.05 | **The safest fallback for structural relaxation**. If the DOS after the calculation shows a metal, recompute with `ISMEAR=1`. |
| Accurate DOS or static total energy | -5 | / | Tetrahedron method (Blöchl corrections). Very accurate, but **must never be used for structural relaxation of metals** (it produces wrong forces from the derivatives), and requires a sufficiently dense k-point mesh. |
| Molecules/isolated atoms (non-periodic) | 0 | 0.01 | Very small Gaussian smearing. |

---

## ISIF Selection Guide

| ISIF | Degrees of freedom optimized | Use case |
|------|---------|---------|
| 2 | Atomic positions only | Lattice constants known and only atomic positions need optimizing (e.g. surfaces, interface adsorption, defects), as well as **2D material relaxation**. |
| 3 | Atomic positions + cell shape + volume | Standard full relaxation for bulk materials; the most common choice. |
| 4 | Atomic positions + cell shape (fixed volume) | For equation-of-state (EOS) volume scans to obtain an accurate bulk modulus. |
| 7 | Volume only (fixed shape and atomic positions) | Pure cell volume contraction/expansion tests. |

---

## DFT+U Parameter Reference

The following are commonly used literature values (based on the Dudarev method, effective value Ueff = U - J). The choice of U often depends on the physical quantity of interest (e.g. band gap, redox potential, or lattice constant); prefer the original literature for the target material.

| Compound/element | Orbital | Ueff (eV) | Source and notes |
|-----------|------|----------|---------|
| FeO, Fe₂O₃ (Fe) | d | 4.0 ~ 5.3 | MP database uses 5.3 for most Fe oxides |
| NiO (Ni) | d | 6.0 ~ 6.4 | MP database uses 6.2 |
| CoO (Co) | d | 3.3 ~ 5.0 | MP database uses 3.32 |
| MnO (Mn) | d | 3.9 ~ 4.5 | MP database uses 3.9 |
| TiO₂ (Ti) | d | 4.2 | Materials Project (MP) recommended value |
| VO₂ (V) | d | 3.1 ~ 3.25| MP database uses 3.25 |
| CeO₂ (Ce) | f | 5.0 | Classic value for the strongly correlated Ce f orbitals |

*Note: some of the values above follow the fitting standards of the Materials Project database. For the full set of element U values, see: [Materials Project Hubbard U Values](https://docs.materialsproject.org/methodology/materials-methodology/calculation-details/gga+u-calculations/hubbard-u-values)*

---

## Official Reference Links
* [VASP Wiki: Number of k-points and smearing methods](https://vasp.at/wiki/Number_of_k_points_and_method_for_smearing)
* [VASP Wiki: Volume relaxation and Pulay stress elimination (Volume Relaxation)](https://vasp.at/wiki/Volume_relaxation)
* [VASP Wiki: Ionic relaxation algorithms (IBRION)](https://vasp.at/wiki/IBRION)
* [Materials Project: How U values are chosen in GGA+U calculations, with list](https://docs.materialsproject.org/methodology/materials-methodology/calculation-details/gga+u-calculations/hubbard-u-values)
