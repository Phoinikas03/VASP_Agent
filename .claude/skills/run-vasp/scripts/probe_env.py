#!/usr/bin/env python3
import os
import sys
import json
import shutil
import subprocess
import socket

def check_command(cmd):
    return shutil.which(cmd) is not None


def dependency_status():
    """Whether the core executables are on PATH (for proactive hazard clearing during probing)."""
    return {
        "mpirun_found": check_command("mpirun"),
        "vasp_std_found": check_command("vasp_std"),
        "vasp_gpu_found": check_command("vasp_gpu"),
    }


def get_gpu_info():
    if not check_command('nvidia-smi'):
        return {"has_gpu": False, "count": 0}
    try:
        out = subprocess.check_output(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'], text=True)
        gpus = out.strip().split('\n')
        return {"has_gpu": True, "count": len(gpus), "models": gpus}
    except Exception:
        return {"has_gpu": False, "count": 0}

def probe_environment():
    hostname = socket.gethostname()
    is_login_node = any(keyword in hostname.lower() for keyword in ['login', 'ln', 'head'])
    
    info = {
        "hostname": hostname,
        "is_login_node": is_login_node,
        "cpu_cores_total": os.cpu_count(),
        "gpu_info": get_gpu_info(),
        "scheduler": "slurm" if check_command('sbatch') else ("pbs" if check_command('qsub') else "none"),
        "dependencies": dependency_status(),
    }
    return info

if __name__ == "__main__":
    print(json.dumps(probe_environment(), indent=2))