#!/usr/bin/env bash
# The compute-cost benchmark: four archetype pods plus a mixed pod.
#
#   1. timing  - 250 games per pod, 2 shards each: 10 processes, fewer than the
#                12 physical cores, so games are not timed under contention.
#   2. memory  - the same 250 games under tracemalloc, 4 shards each. Timings
#                are not used from this pass, so contention does not matter.
#   3. pool    - one uninstrumented 1000-game production run per pod, one at a
#                time, through the real worker pool: the actual bill.
#
# Usage: bash bench/run_cost_bench.sh [path/to/archetypes.py]
# Limit a pass with TIMING_LABELS / MEMORY_LABELS / POOL_LABELS, e.g. to rerun
# only what an engine change invalidated; an empty value skips the pass.
set -u
cd "$(dirname "$0")/.."

OUT=bench/results
mkdir -p "$OUT"
LOG="$OUT/progress.log"
echo "start $(date)" >> "$LOG"

if [ "${1:-}" != "" ]; then
  python "$1" >> "$LOG" 2>&1
fi

GAMES=250
POOL=1000
LABELS="aggro combo control stax mixed"
TIMING_LABELS="${TIMING_LABELS-$LABELS}"
MEMORY_LABELS="${MEMORY_LABELS-$LABELS}"
POOL_LABELS="${POOL_LABELS-$LABELS}"

decks_for() {
  case "$1" in
    mixed) echo "bench/decks/aggro.txt bench/decks/combo.txt bench/decks/control.txt bench/decks/stax.txt" ;;
    *) echo "bench/decks/$1.txt --mirror" ;;
  esac
}

for label in $TIMING_LABELS; do
  for k in 0 1; do
    # shellcheck disable=SC2046
    python -m mtgfish.tools.cost $(decks_for "$label") --games "$GAMES" --label "$label" \
      --shard "$k/2" --out "$OUT/$label.timing.$k.jsonl" >> "$LOG" 2>&1 &
  done
done
wait
echo "timing done ($TIMING_LABELS) $(date)" >> "$LOG"

for label in $MEMORY_LABELS; do
  for k in 0 1 2 3; do
    # shellcheck disable=SC2046
    python -m mtgfish.tools.cost $(decks_for "$label") --games "$GAMES" --label "$label" \
      --memory --shard "$k/4" --out "$OUT/$label.memory.$k.jsonl" >> "$LOG" 2>&1 &
  done
done
wait
echo "memory done ($MEMORY_LABELS) $(date)" >> "$LOG"

for label in $POOL_LABELS; do
  # shellcheck disable=SC2046
  python -m mtgfish.tools.cost $(decks_for "$label") --pool "$POOL" --label "$label" \
    --out "$OUT/$label.pool.json" >> "$LOG" 2>&1
  echo "pool $label done $(date)" >> "$LOG"
done
echo "all done $(date)" >> "$LOG"
