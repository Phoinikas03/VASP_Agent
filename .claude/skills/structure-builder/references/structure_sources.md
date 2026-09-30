# Structure Sources

Use this reference when choosing how to obtain or construct a structure.

## Preferred source order

1. User-supplied POSCAR/CIF/CONTCAR.
2. Exact Materials Project id through `MP_API`.
3. Formula search through Materials Project, followed by user confirmation of the selected entry.
4. Literature-reported structure parameters.
5. Programmatic construction with ASE or pymatgen.

## Library selection

- Use ASE for standard surfaces such as Pt(111), Cu(111), Ni(111), Pd(111), simple gas molecules, and direct `add_adsorbate` workflows.
- Use pymatgen for reading/writing VASP formats, validating structures, and cutting slabs from arbitrary bulk structures.
- Consider ACAT or AutoCat when the task requires many adsorption sites, alloy surfaces, multiple adsorbates, or coverage enumeration.

Do not manually type a full POSCAR from memory.
