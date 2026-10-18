#!/usr/bin/env bash
# Run one measurement scenario end-to-end.
# Usage: ./scripts/run_scenario.sh <name> <duration_s> [netem_preset] [load_pct]
set -euo pipefail
NAME="${1:?scenario name}"; DUR="${2:?duration s}"
NETEM="${3:-clear}"; LOAD="${4:-0}"
IFACE="${IFACE:-vcan0}"
OUT="reports/${NAME}"
mkdir -p "$OUT"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

"$ROOT/scripts/setup_vcan.sh" "$IFACE" >/dev/null
"$ROOT/scripts/netem.sh" "$NETEM" "$IFACE" >/dev/null

"$ROOT/bin/monitor"       --iface "$IFACE" --duration "$DUR" \
    --csv "$OUT/frames.csv" --summary "$OUT/summary.json" --ping-interval 10 &
MON=$!
"$ROOT/bin/ecu_sensor"    --iface "$IFACE" --duration "$DUR" &
"$ROOT/bin/ecu_feature"   --iface "$IFACE" --duration "$DUR" &
"$ROOT/bin/ecu_assistant" --iface "$IFACE" --duration "$DUR" &
if [ "$LOAD" != "0" ]; then
    "$ROOT/bin/injector"  --iface "$IFACE" --load-pct "$LOAD" --duration "$DUR" &
fi
wait $MON || true
kill %2 %3 %4 %5 2>/dev/null || true
"$ROOT/scripts/netem.sh" clear "$IFACE" >/dev/null
echo "{\"scenario\":\"$NAME\",\"netem\":\"$NETEM\",\"load_pct\":$LOAD,\"duration\":$DUR}" > "$OUT/meta.json"
echo "scenario '$NAME' -> $OUT (summary.json + frames.csv)"
