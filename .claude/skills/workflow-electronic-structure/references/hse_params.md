# HSE Parameter Reference

## Standard HSE06 Parameters (suitable for most materials)

```text
LHFCALC = .TRUE.
HFSCREEN = 0.2
AEXX     = 0.25
ALGO     = Damped
TIME     = 0.4
PRECFOCK = Fast
```

## Empirical Parameters by Material Type

| Material type | HFSCREEN | AEXX | ALGO | Notes |
|---------|---------|------|------|------|
| Standard covalent semiconductors (Si, Ge, GaAs) | 0.2 | 0.25 | Damped | HSE06 default; works well |
| Wide-gap oxides (ZnO, TiO₂, Al₂O₃) | 0.2 | 0.25~0.30 | All | Gap may still be underestimated; AEXX can be increased moderately |
| 2D materials (MoS₂, WS₂, h-BN) | 0.2 | 0.25 | Damped | High k-point density required; a denser mesh is recommended |
| Perovskites (MAPbI₃, CsPbBr₃) | 0.2 | 0.25 | All | Often needs SOC |
| Magnetic materials (Fe₂O₃, NiO) | 0.2 | 0.25 | All | Add ISPIN=2, MAGMOM |
| Strongly correlated systems (with d/f orbitals) | 0.2 | 0.25 | All | Consider DFT+U or HSE+U |

## Parameter Notes

- `HFSCREEN`: `0.2` is the HSE06 standard; `0.0` approaches PBE0
- `AEXX`: standard value `0.25`; if the gap is systematically underestimated, try `0.30~0.35`
- `ALGO = All`: more robust, but slower
- `ALGO = Damped`: faster, suited to large systems; use with `TIME = 0.4`
- `PRECFOCK = Fast`: usually gives a significant speedup, but a few systems need to revert to `Normal`
