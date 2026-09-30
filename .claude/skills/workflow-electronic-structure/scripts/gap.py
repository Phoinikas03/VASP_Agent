"""
Extract band-gap information from vasprun.xml.
Usage: python scripts/gap.py [path to vasprun.xml]
Default path: ./vasprun.xml
"""
import json
import sys

from pymatgen.io.vasp import Vasprun


def gap_calc(path="./vasprun.xml"):
    vasprun = Vasprun(path, parse_projected_eigen=True)
    band_structure = vasprun.get_band_structure()
    band_gap = band_structure.get_band_gap()

    result = {
        "energy_eV": band_gap.get("energy"),
        "direct": band_gap.get("direct"),
        "transition": band_gap.get("transition"),
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    xml_path = sys.argv[1] if len(sys.argv) > 1 else "./vasprun.xml"
    gap_calc(xml_path)
