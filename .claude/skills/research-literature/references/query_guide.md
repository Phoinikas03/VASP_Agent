# arXiv Query Term Reference

## Core Principle

**Write only the material system name; do not add method or physical-quantity keywords.**

arXiv ranks by relevance, and computational materials papers already contain words such as DFT/HSE/VASP;
adding them only over-restricts the results and misses papers that describe the same method in different terms.

---

## Chemical Formula Conventions

| Case | Recommended | Not recommended |
|------|---------|--------|
| Single compound | `"SrTiO3"` | `"SrTiO3" HSE06 band gap VASP` |
| Material family | `"perovskite oxide"` | `perovskite DFT+U Hubbard U value` |
| 2D material | `"MoS2 monolayer"` | `"MoS2" band gap experimental optical` |
| Li-ion battery material | `"LiCoO2"` | `"LiCoO2" DFT+U Hubbard U` |
| Rare-earth compound | `"CeO2"` | `"CeO2" f-electron DFT+U` |

Add a single qualifier only when the chemical formula itself is ambiguous (e.g. to distinguish bulk from monolayer).

---

## Query Terms for Common Material Systems

| Material type | Example query terms |
|---------|-----------|
| Titanate perovskites | `"BaTiO3"` / `"SrTiO3"` / `"PbTiO3"` |
| Iron-based perovskites | `"BiFeO3"` / `"LaFeO3"` |
| Halide perovskites | `"MAPbI3"` / `"CsPbBr3"` |
| Transition metal oxides | `"TiO2"` / `"VO2"` / `"Fe2O3"` |
| 2D materials | `"MoS2 monolayer"` / `"WSe2 monolayer"` / `"hBN monolayer"` |
| Li-ion cathodes | `"LiCoO2"` / `"LiFePO4"` / `"LiMn2O4"` |
| Topological materials | `"Bi2Se3"` / `"Bi2Te3"` |
| Rare-earth oxides | `"CeO2"` / `"La2O3"` |
| Magnetic materials | `"NiO"` / `"MnO"` / `"CoO"` |
| III-V semiconductors | `"GaAs"` / `"InP"` / `"GaN"` |
| II-VI semiconductors | `"ZnO"` / `"CdS"` / `"ZnSe"` |

---

## Adjustment Strategies When Search Results Are Poor

1. Replace the chemical formula with the mineral name: `"rutile"` instead of `"TiO2"`
2. Switch to a broader category: `"transition metal dichalcogenide"` instead of `"MoS2"`
3. Try without quotes: `SrTiO3` (allows the terms to be split for matching)
4. After at most 2 adjustments, switch to web search
