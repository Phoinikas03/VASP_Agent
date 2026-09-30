#!/usr/bin/env python3
import os
import argparse

def parse_args():
    parser = argparse.ArgumentParser(description="Batch-generate isotropically scaled POSCARs for EOS tests.")
    parser.add_argument("--poscar", default="POSCAR", help="Path to the initial POSCAR file")
    parser.add_argument("--scales", nargs="+", type=float,
                        help="List of scale factors, e.g. 0.96 0.98 1.00 1.02 1.04")
    parser.add_argument("--center-scale", type=float,
                        help="Generate evenly spaced refinement sampling points around this linear scale factor")
    parser.add_argument("--step", type=float,
                        help="Linear scale step used together with --center-scale or --min-scale/--max-scale")
    parser.add_argument("--points", type=int, default=5,
                        help="Number of points generated around --center-scale; an odd number is recommended so that the center point is included")
    parser.add_argument("--min-scale", type=float, help="Minimum linear scale factor when generating sampling points over a range")
    parser.add_argument("--max-scale", type=float, help="Maximum linear scale factor when generating sampling points over a range")
    parser.add_argument("--prefix", default="scale", help="Output directory prefix; by default generates scale_*.xxx")
    parser.add_argument("--dir-decimals", type=int, default=3,
                        help="Number of decimal places for the scale factor in directory names, default 3")
    return parser.parse_args()

def build_scales(args):
    if args.scales:
        return args.scales

    if args.center_scale is not None:
        if args.step is None or args.step <= 0:
            raise ValueError("A positive --step must be provided when using --center-scale.")
        if args.points <= 0:
            raise ValueError("--points must be a positive integer.")
        if args.points % 2 == 0:
            raise ValueError("--points should be odd so that the center scale factor is included.")
        half = args.points // 2
        return [args.center_scale + (i - half) * args.step for i in range(args.points)]

    if args.min_scale is not None or args.max_scale is not None:
        if args.min_scale is None or args.max_scale is None:
            raise ValueError("--min-scale and --max-scale must be provided together.")
        if args.step is None or args.step <= 0:
            raise ValueError("A positive --step must be provided when using --min-scale/--max-scale.")
        if args.min_scale > args.max_scale:
            raise ValueError("--min-scale cannot be greater than --max-scale.")
        n_steps = int(round((args.max_scale - args.min_scale) / args.step))
        scales = [args.min_scale + i * args.step for i in range(n_steps + 1)]
        if scales[-1] < args.max_scale - 1e-10:
            scales.append(args.max_scale)
        return scales

    raise ValueError("You must provide --scales, or --center-scale/--step, or --min-scale/--max-scale/--step.")

def generate_scaled_poscars(base_poscar, scales, prefix="scale", dir_decimals=3):
    if not os.path.exists(base_poscar):
        raise FileNotFoundError(f"Base file not found: {base_poscar}")

    with open(base_poscar, 'r') as f:
        lines = f.readlines()

    # The second line of POSCAR is the global scale factor
    try:
        base_scale = float(lines[1].strip())
    except ValueError:
        raise ValueError("The second line of POSCAR is not a valid number (expected the global scale factor).")

    seen_dirs = set()
    for scale in scales:
        dir_name = f"{prefix}_{scale:.{dir_decimals}f}"
        if dir_name in seen_dirs:
            print(f"Skipping duplicate directory name: {dir_name} (scale factor: {scale:.8f})")
            continue
        seen_dirs.add(dir_name)
        os.makedirs(dir_name, exist_ok=True)
        
        # Compute the new scale factor (linear scaling; the volume scales as the cube of this factor)
        new_scale = base_scale * scale
        
        # Copy the modified lines
        new_lines = lines.copy()
        new_lines[1] = f" {new_scale:.10f}\n"
        
        # Write to the subdirectory
        target_path = os.path.join(dir_name, "POSCAR")
        with open(target_path, 'w') as f:
            f.writelines(new_lines)
            
        print(f"Generated: {target_path} (scale factor: {scale:.3f})")

if __name__ == "__main__":
    args = parse_args()
    scales = build_scales(args)
    generate_scaled_poscars(args.poscar, scales, prefix=args.prefix, dir_decimals=args.dir_decimals)
