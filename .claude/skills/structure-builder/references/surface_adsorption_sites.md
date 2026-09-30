# Surface Adsorption Sites

## Common sites on fcc(111)

- `ontop`: adsorbate anchor atom above one surface atom.
- `bridge`: anchor atom between two neighboring surface atoms.
- `fcc`: threefold hollow site above an fcc continuation site.
- `hcp`: threefold hollow site above an hcp continuation site.

## CO/fcc(111) p(2x2)

For one CO on p(2x2) fcc(111), coverage is 1/4 ML because the top surface layer has four surface metal atoms per lateral supercell. Treat Pt(111), Pd(111), Rh(111), and Ir(111) as the same routine structure family unless the user specifies a different slab model.

Recommended initial structures:

- fcc upright
- fcc tilted_x
- fcc tilted_y
- ontop upright
- ontop tilted_x
- ontop tilted_y
- bridge upright
- hcp upright

Use C-down CO as the default adsorption anchor. In scripts, specify this with `--anchor-symbol C` or rely on the CO default in `build_adsorption.py`. For benchmark-aligned CO/fcc(111), "orientation" means the molecular-axis geometry (`upright`, `tilted_x`, `tilted_y`) and must not be interpreted as C-down vs O-down. O-down/reverse can be generated only when the user explicitly asks for end-group screening beyond the standard benchmark.

For geometry optimizations, fix the same bottom slab layers in clean and adsorbed slabs, commonly the bottom 2 layers for a 4-layer p(2x2) slab.

## Common rutile oxide (110) sites and variants

Treat CO on rutile RuO2(110) and IrO2(110) as routine slab adsorption setup. Common labels include:

- `cus`: coordinatively unsaturated metal site.
- `bridge-O`: bridging oxygen row.
- `cus-O`: oxygen species at or near a cus site.
- `stoichiometric`: pristine (110) slab.
- `reduced`: oxygen-deficient surface model.
- `O-vacancy`: explicit oxygen vacancy model.
- `O-rich`: oxygen-rich surface model.

For reduced or O-vacancy structures, record which oxygen atom/site was removed and keep the corresponding clean/reduced surface and adsorbed structure in matching cells.
