# HSE Parameter Reference

## Standard HSE06 Parameters (suitable for most materials)

```
LHFCALC = .TRUE.
HFSCREEN = 0.2       # Screening parameter (Å⁻¹), HSE06 standard value
AEXX     = 0.25      # Exact-exchange mixing fraction, HSE06 standard value
ALGO     = Damped    # Recommended for large systems; All can be used for small systems
TIME     = 0.4       # Used together with ALGO=Damped
PRECFOCK = Fast      # Speeds up HF integrals; slightly lower accuracy, usually acceptable
```

---

## Empirical Parameters by Material Type

| Material type | HFSCREEN | AEXX | ALGO | Notes |
|---------|---------|------|------|------|
| Standard covalent semiconductors (Si, Ge, GaAs) | 0.2 | 0.25 | Damped | HSE06 default; works well |
| Wide-gap oxides (ZnO, TiO₂, Al₂O₃) | 0.2 | 0.25~0.30 | All | Gap may still be underestimated; AEXX can be increased moderately |
| 2D materials (MoS₂, WS₂, h-BN) | 0.2 | 0.25 | Damped | High k-point density required; a denser mesh is recommended |
| Perovskites (MAPbI₃, CsPbBr₃) | 0.2 | 0.25 | All | Add SOC (LSORBIT=.TRUE.) |
| Magnetic materials (Fe₂O₃, NiO) | 0.2 | 0.25 | All | Add ISPIN=2 and initial MAGMOM moments |
| Strongly correlated systems (with d/f orbitals) | 0.2 | 0.25 | All | Consider adding DFT+U, or use HSE+U |

---

## Parameter Notes

- **HFSCREEN**: Parameter that screens the long-range HF exchange; 0.2 Å⁻¹ corresponds to HSE06, 0.0 to PBE0 (exact exchange at all ranges, more expensive).
- **AEXX**: Exact-exchange mixing fraction. The HSE06 standard is 0.25; if the gap is systematically underestimated, try 0.30~0.35.
- **ALGO=Damped vs All**:
  - `All`: more robust; suited to small systems or hard-to-converge cases
  - `Damped`: faster, suited to large systems; use with `TIME=0.4`
- **PRECFOCK=Fast**: Lowers the precision of HF integrals to speed up the calculation; the effect on the gap is usually < 0.05 eV. If high accuracy is needed, change to `Normal`.
