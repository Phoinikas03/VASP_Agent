#!/usr/bin/env python3
import os
import sys
import shlex
import shutil
import subprocess
import argparse
from pathlib import Path


def verify_local_dependencies(exe: str, env_script: str = "") -> None:
    """Consistent with real execution: if env_script is provided, check mpirun / the VASP executable after sourcing it."""
    env_p = Path(env_script) if env_script else None
    if env_p and env_p.exists():
        check = (
            "set -e; source "
            + shlex.quote(str(env_p.resolve()))
            + "; command -v mpirun >/dev/null; command -v "
            + shlex.quote(exe)
            + " >/dev/null"
        )
        r = subprocess.run(["bash", "-c", check], capture_output=True, text=True)
        if r.returncode != 0:
            print(
                f"ERROR: After sourcing {env_script}, mpirun or {exe} not found (command not found).",
                file=sys.stderr,
            )
            sys.exit(1)
        return
    if not shutil.which("mpirun"):
        print(
            "ERROR: mpirun not found in PATH (command not found). "
            "Load MPI module or set PATH before quick_test, or pass --env-script.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not shutil.which(exe):
        print(
            f"ERROR: {exe} not found in PATH (command not found). "
            "Load VASP module or set PATH before quick_test, or pass --env-script.",
            file=sys.stderr,
        )
        sys.exit(1)

def setup_quick_test_incar(incar_path):
    backup_path = incar_path.with_name("INCAR.bak_test")
    shutil.copy(incar_path, backup_path)
    
    with open(incar_path, "a") as f:
        f.write("\n# --- INJECTED FOR QUICK TEST ---\n")
        f.write("NELM = 5\n")   # run only 5 electronic steps
        f.write("NSW = 0\n")    # disable ionic relaxation
        f.write("NWRITE = 1\n") # reduce output
    return backup_path

def run_quick_test(
    work_dir,
    exe="vasp_std",
    env_script="",
    log_file="vasp_quick_test.log",
    np=2,
):
    work_dir = Path(work_dir).resolve()
    incar_path = work_dir / "INCAR"
    
    if not incar_path.exists():
        print("Error: INCAR not found.")
        return

    print("Configuring INCAR for quick test (NELM=5, NSW=0)...")
    backup_path = setup_quick_test_incar(incar_path)

    cmd = f"mpirun -np {int(np)} {exe} > {shlex.quote(log_file)} 2>&1"
    if env_script and Path(env_script).exists():
        cmd = f"source {env_script} && {cmd}"

    verify_local_dependencies(exe, env_script)
    print(f"Executing quick test: {cmd}")
    print(f"Quick-test log: {work_dir / log_file}")
    try:
        proc = subprocess.run(cmd, cwd=work_dir, shell=True, executable="/bin/bash")
        if proc.returncode != 0:
            print(f"ERROR: quick test command exited with code {proc.returncode}", file=sys.stderr)
            sys.exit(proc.returncode)
        print("\n[Test Finished] Checking OSZICAR for basic electronic convergence starts...")
        if (work_dir / "OSZICAR").exists():
            subprocess.run(["head", "-n", "10", str(work_dir / "OSZICAR")])
    finally:
        print("\nRestoring original INCAR...")
        shutil.move(backup_path, incar_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=str, default=".")
    parser.add_argument("--exe", type=str, default="vasp_std")
    parser.add_argument("--env-script", type=str, default="")
    parser.add_argument("--log-file", type=str, default="vasp_quick_test.log")
    parser.add_argument("--np", type=int, default=2)
    args = parser.parse_args()
    run_quick_test(args.work_dir, args.exe, args.env_script, args.log_file, args.np)
