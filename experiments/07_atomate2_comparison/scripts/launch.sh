#!/bin/bash
# Wrapper: VASP environment + Python environment, then run a driver.
# Usage: launch.sh <driver.py> [driver arguments]
#   VASP_ENV_SCRIPT      script that puts vasp_std/vasp_gpu on PATH (default ~/env_vasp)
#   PYTHON_ENV_PREFIX    Python environment to use (default: the python already on PATH);
#                        atomate2 drivers need an environment with atomate2 installed,
#                        agent drivers one with requirements.txt installed
ARGS=("$@")
source "${VASP_ENV_SCRIPT:-$HOME/env_vasp}"
export OMP_NUM_THREADS=1
export PYTHONWARNINGS=ignore
if [ -n "${PYTHON_ENV_PREFIX:-}" ]; then export PATH="$PYTHON_ENV_PREFIX/bin:$PATH"; fi
export VASP_BIN_DIR="$(dirname "$(command -v vasp_std)")"
exec python "${ARGS[@]}"
