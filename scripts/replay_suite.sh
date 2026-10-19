#!/usr/bin/env bash
# Run every replay scenario against a fresh ECU (Track A3/A4).
#
# Each scenario gets its own ecu_feature instance so scenarios stay independent
# experiments (no inherited brake state). Exit code 0 = every property held,
# 1 = at least one violation (CI gate).
#
# Usage: scripts/replay_suite.sh [iface]        (default: vcan0 or $VECU_IFACE)
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IFACE="${1:-${VECU_IFACE:-vcan0}}"
ECU_DURATION="${ECU_DURATION:-8}"
PYTHON="${PYTHON:-python3}"

rc=0
pass=0
fail=0
for scenario in "$ROOT"/replay/scenarios/*.yaml; do
    name="$(basename "$scenario" .yaml)"
    "$ROOT/bin/ecu_feature" --iface "$IFACE" --duration "$ECU_DURATION" \
        >/dev/null 2>&1 &
    ecu=$!
    sleep 0.5
    if "$PYTHON" "$ROOT/scripts/replay.py" --iface "$IFACE" \
            --scenario "$scenario" --quiet; then
        echo "PASS $name"
        pass=$((pass + 1))
    else
        echo "FAIL $name"
        fail=$((fail + 1))
        rc=1
    fi
    kill "$ecu" 2>/dev/null
    wait "$ecu" 2>/dev/null
done

echo "replay suite: $pass passed, $fail failed (reports in reports/replay/)"
exit $rc
