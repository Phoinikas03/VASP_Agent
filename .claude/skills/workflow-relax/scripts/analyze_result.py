"""
Extract key structural relaxation results from OUTCAR and CONTCAR, and output a JSON report.
Usage: python scripts/analyze_result.py [work_dir]
Default directory: current directory

Output fields:
  final_energy_eV       - final total energy (eV)
  final_energy_per_atom - energy per atom (eV/atom)
  num_atoms             - total number of atoms
  max_force_eV_A        - final maximum atomic force (eV/Å)
  rms_force_eV_A        - final RMS force (eV/Å)
  pressure_kbar         - final pressure (kBar)
  volume_A3             - final cell volume (Å³)
  contcar_path          - path to the CONTCAR file
"""
import sys
import os
import re
import json


def analyze_result(work_dir="."):
    result = {
        "final_energy_eV": None,
        "final_energy_per_atom": None,
        "num_atoms": None,
        "max_force_eV_A": None,
        "rms_force_eV_A": None,
        "pressure_kbar": None,
        "volume_A3": None,
        "contcar_path": None,
    }

    outcar_path = os.path.join(work_dir, "OUTCAR")
    contcar_path = os.path.join(work_dir, "CONTCAR")

    if os.path.exists(contcar_path) and os.path.getsize(contcar_path) > 0:
        result["contcar_path"] = os.path.abspath(contcar_path)

    if not os.path.exists(outcar_path):
        result["error"] = "OUTCAR file does not exist"
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result

    with open(outcar_path, "r", errors="replace") as f:
        content = f.read()
        lines = content.splitlines()

    # Number of atoms
    m = re.search(r"NIONS\s*=\s*(\d+)", content)
    if m:
        result["num_atoms"] = int(m.group(1))

    # Final total energy (last occurrence)
    energies = re.findall(r"free  energy   TOTEN\s*=\s*([\-\d.]+)\s*eV", content)
    if energies:
        e = float(energies[-1])
        result["final_energy_eV"] = e
        if result["num_atoms"]:
            result["final_energy_per_atom"] = round(e / result["num_atoms"], 6)

    # Final maximum force and RMS force (last occurrence)
    forces = re.findall(r"FORCES: max atom, RMS\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)", content)
    if forces:
        result["max_force_eV_A"] = float(forces[-1][0])
        result["rms_force_eV_A"] = float(forces[-1][1])

    # Final pressure (last occurrence)
    pressures = re.findall(r"external pressure\s*=\s*([\-\d.]+)\s*kB", content)
    if pressures:
        result["pressure_kbar"] = float(pressures[-1])

    # Final cell volume (last occurrence)
    volumes = re.findall(r"volume of cell\s*:\s*([\d.]+)", content)
    if volumes:
        result["volume_A3"] = float(volumes[-1])

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


if __name__ == "__main__":
    work_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    analyze_result(work_dir)
