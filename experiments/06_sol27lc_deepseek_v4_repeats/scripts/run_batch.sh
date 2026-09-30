#!/bin/bash
# Run VASP Agent over all 27 Sol27LC systems, one GPU per system, at most one
# system per GPU. GPU ownership is tracked by driver PID, not by GPU memory: an
# agent spends minutes preparing inputs before it launches VASP.
#
# Usage: run_batch.sh [rep]          (default rep1)
# Optional: EXCLUDE_GPU_INDEX=5,7    nvidia-smi indices to leave unused
REP="${1:-rep1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
EXP="$(cd "$HERE/.." && pwd -P)"
REPO="$(cd "$EXP/../.." && pwd -P)"
RUNS="${RUNS_DIR:-$EXP/runs}"
mapfile -t ALL < <(nvidia-smi --query-gpu=index,uuid --format=csv,noheader | tr -d ' ')
UUIDS=()
for row in "${ALL[@]}"; do
  idx="${row%%,*}"; uuid="${row#*,}"
  if [[ ",${EXCLUDE_GPU_INDEX:-}," == *",$idx,"* ]]; then continue; fi
  UUIDS+=("$uuid")
done
mapfile -t SYSTEMS < <(ls -d "$REPO/data/sol27lc/structures"/*/ | xargs -n1 basename | sort)
NGPU=${#UUIDS[@]}
declare -A OWNER=()
echo "rep=$REP systems=${#SYSTEMS[@]} gpus=$NGPU"

free_slot() {
  while true; do
    for ((g=0; g<NGPU; g++)); do
      pid="${OWNER[$g]:-}"
      if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then echo "$g"; return; fi
    done
    sleep 15
  done
}

mkdir -p "$RUNS/$REP"
for s in "${SYSTEMS[@]}"; do
  outdir="$RUNS/$REP/$s"
  if [ -f "$outdir/run_meta.json" ] && grep -q '"status": "completed"' "$outdir/run_meta.json"; then
    echo "skip $s (already completed)"; continue
  fi
  g=$(free_slot)
  nohup "$HERE/launch_agent.sh" --system "$s" --gpu-uuid "${UUIDS[$g]}" --rep "$REP" \
        --runs-dir "$RUNS" > "$RUNS/$REP/$s.log" 2>&1 &
  OWNER[$g]=$!
  echo "  launched $s -> gpu slot $g pid=${OWNER[$g]}"
done
wait
echo "batch done: $REP"
