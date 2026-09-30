#!/bin/bash
# Wrapper: VASP environment + the agent's Python environment, one system per GPU.
# Arguments are captured first because sourcing the Intel/MKL environment script
# clobbers "$@".
#   VASP_ENV_SCRIPT         script that puts vasp_std/vasp_gpu on PATH (default ~/env_vasp)
#   VASPAGENT_CONDA_PREFIX  Python environment with requirements.txt installed
#                           (default: the python already on PATH)
ARGS=("$@")
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "${VASP_ENV_SCRIPT:-$HOME/env_vasp}"
export OMP_NUM_THREADS=1
if [ -n "${VASPAGENT_CONDA_PREFIX:-}" ]; then
  export PATH="$VASPAGENT_CONDA_PREFIX/bin:$PATH"
fi
export VASP_BIN_DIR="$(dirname "$(command -v vasp_std)")"
exec python "$HERE/run_agent_lc.py" "${ARGS[@]}"
