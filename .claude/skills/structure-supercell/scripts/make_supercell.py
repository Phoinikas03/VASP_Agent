import argparse
import sys
import os
try:
    from pymatgen.core import Structure
except ImportError:
    print("Error: pymatgen is not installed. Please install it using 'pip install pymatgen'.")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description='Supercell generator using pymatgen')
    parser.add_argument('--input', default='POSCAR', help='Input POSCAR file')
    parser.add_argument('--output', default='POSCAR_supercell', help='Output file name')
    parser.add_argument('--scaling', nargs='+', type=int, help='Scaling factors (e.g., 2 2 2 or 3 3 1)')
    parser.add_argument('--info', action='store_true', help='Only show info, do not write')

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: {args.input} not found.")
        sys.exit(1)

    try:
        struct = Structure.from_file(args.input)
        orig_count = len(struct)

        if not args.scaling or len(args.scaling) != 3:
            print("Error: Please provide 3 scaling factors (e.g., --scaling 2 2 2)")
            sys.exit(1)

        factors = args.scaling
        new_count = orig_count * factors[0] * factors[1] * factors[2]

        if args.info:
            print(f"INFO: Original atoms: {orig_count}")
            print(f"INFO: Target atoms: {new_count}")
            print(f"INFO: Scaling matrix: {factors[0]}x{factors[1]}x{factors[2]}")
            return

        # Perform supercell transformation
        struct.make_supercell(factors)
        struct.to(fmt="poscar", filename=args.output)
        print(f"SUCCESS: Supercell created with {new_count} atoms and saved to {args.output}")

    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
