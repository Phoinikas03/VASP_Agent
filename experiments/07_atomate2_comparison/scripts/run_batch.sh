#!/bin/bash
# Run one arm of experiment 07.
#
#   run_batch.sh atomate2-lc   27 Sol27LC systems, one GPU each, in parallel
#   run_batch.sh atomate2-bg   5 band-gap materials, one GPU each, in parallel
#   run_batch.sh agent-bg      5 band-gap materials, one at a time, each with all
#                              usable GPUs (24 h budget per material)
#
# The VASP Agent Sol27LC arm is Run 1 of experiment 06 (06/scripts/run_batch.sh rep1).
# Run extract_bandgap_structures.py before the band-gap arms.
# Optional: EXCLUDE_GPU_INDEX=2,5  nvidia-smi indices to leave unused
ARM="${1:?arm required: atomate2-lc | atomate2-bg | agent-bg}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
EXP="$(cd "$HERE/.." && pwd -P)"
REPO="$(cd "$EXP/../.." && pwd -P)"
mapfile -t ALL < <(nvidia-smi --query-gpu=index,uuid --format=csv,noheader | tr -d ' ')
UUIDS=()
for row in "${ALL[@]}"; do
  idx="${row%%,*}"; uuid="${row#*,}"
  if [[ ",${EXCLUDE_GPU_INDEX:-}," == *",$idx,"* ]]; then continue; fi
  UUIDS+=("$uuid")
done
BANDGAP=(Si GaAs GaP ZnO Cu2O)

if [ "$ARM" = "agent-bg" ]; then
  ALLOC=$(IFS=,; echo "${UUIDS[*]}")
  mkdir -p "$EXP/runs_agent_bandgap/rep1"
  for m in "${BANDGAP[@]}"; do
    "$HERE/launch.sh" "$HERE/run_agent_bg.py" --system "$m" --gpu-uuid "$ALLOC" \
      > "$EXP/runs_agent_bandgap/rep1/$m.log" 2>&1
  done
  exit 0
fi

if [ "$ARM" = "atomate2-lc" ]; then
  mapfile -t ITEMS < <(ls -d "$REPO/data/sol27lc/structures"/*/ | xargs -n1 basename | sort)
  DRIVER="$HERE/run_atomate2_lc.py"; FLAG=--system; OUT="$EXP/runs_atomate2_sol27lc"
else
  ITEMS=("${BANDGAP[@]}")
  DRIVER="$HERE/run_atomate2_bg.py"; FLAG=--material; OUT="$EXP/runs_atomate2_bandgap"
fi
mkdir -p "$OUT"
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
for s in "${ITEMS[@]}"; do
  g=$(free_slot)
  nohup "$HERE/launch.sh" "$DRIVER" "$FLAG" "$s" --gpu-uuid "${UUIDS[$g]}" --outroot "$OUT" \
    > "$OUT/$s.log" 2>&1 &
  OWNER[$g]=$!
  echo "launched $s -> gpu slot $g"
done
wait
