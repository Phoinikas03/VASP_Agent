#!/bin/bash
# Run the 27 Sol27LC systems without domain skills, one GPU per system.
#
# Usage: AGENT_ROOT=/path/outside/repo run_batch.sh [rep]      (default rep1)
# Optional: EXCLUDE_GPU_INDEX=5,7   nvidia-smi indices to leave unused
# Environment variables of 06/scripts/launch_agent.sh apply (VASP_ENV_SCRIPT,
# VASPAGENT_CONDA_PREFIX).
REP="${1:-rep1}"
: "${AGENT_ROOT:?set AGENT_ROOT to a directory outside the repository}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$HERE/../../.." && pwd -P)"
mapfile -t ALL < <(nvidia-smi --query-gpu=index,uuid --format=csv,noheader | tr -d ' ')
UUIDS=()
for row in "${ALL[@]}"; do
  idx="${row%%,*}"; uuid="${row#*,}"
  if [[ ",${EXCLUDE_GPU_INDEX:-}," == *",$idx,"* ]]; then continue; fi
  UUIDS+=("$uuid")
done
mapfile -t SYSTEMS < <(ls -d "$REPO/data/sol27lc/structures"/*/ | xargs -n1 basename | sort)
declare -A OWNER=()
free_slot() {
  while true; do
    for ((g=0; g<${#UUIDS[@]}; g++)); do
      pid="${OWNER[$g]:-}"
      if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then echo "$g"; return; fi
    done
    sleep 15
  done
}
mkdir -p "$AGENT_ROOT/runs/$REP"
for s in "${SYSTEMS[@]}"; do
  g=$(free_slot)
  (
    source "${VASP_ENV_SCRIPT:-$HOME/env_vasp}"
    export OMP_NUM_THREADS=1
    if [ -n "${VASPAGENT_CONDA_PREFIX:-}" ]; then export PATH="$VASPAGENT_CONDA_PREFIX/bin:$PATH"; fi
    export VASP_BIN_DIR="$(dirname "$(command -v vasp_std)")"
    exec python "$HERE/run_agent_noskill.py" --system "$s" --gpu-uuid "${UUIDS[$g]}" \
      --agent-root "$AGENT_ROOT" --rep "$REP"
  ) > "$AGENT_ROOT/runs/$REP/$s.log" 2>&1 &
  OWNER[$g]=$!
  echo "launched $s -> gpu slot $g"
done
wait
