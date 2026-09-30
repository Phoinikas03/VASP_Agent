#!/bin/bash
#SBATCH --job-name=VASP_Batch
#SBATCH --nodes=1
#SBATCH --ntasks={{NTASKS}}
#SBATCH --time=24:00:00
#SBATCH --partition=compute

# Your cluster environment variables
# module load vasp

# The commands below are injected automatically by vasp_runner.py
{{COMMANDS}}