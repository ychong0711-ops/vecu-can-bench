#!/usr/bin/env bash
# Fault-injection presets using tc/netem on the vcan interface.
# Usage: ./scripts/netem.sh <preset> [iface]
#   presets: clear | delay | jitter | loss | combo
set -euo pipefail
PRESET="${1:-clear}"
IFACE="${2:-vcan0}"
case "$PRESET" in
  clear)  tc qdisc del dev "$IFACE" root 2>/dev/null || true
          echo "netem cleared on $IFACE" ;;
  delay)  tc qdisc replace dev "$IFACE" root netem delay 10ms 2ms
          echo "$IFACE: delay 10ms +-2ms" ;;
  jitter) tc qdisc replace dev "$IFACE" root netem delay 5ms 10ms distribution normal
          echo "$IFACE: normal jitter 5ms +-10ms" ;;
  loss)   tc qdisc replace dev "$IFACE" root netem loss 1%
          echo "$IFACE: loss 1%" ;;
  combo)  tc qdisc replace dev "$IFACE" root netem delay 10ms 2ms loss 0.5%
          echo "$IFACE: delay 10ms+-2ms + loss 0.5%" ;;
  *) echo "unknown preset: $PRESET"; exit 2 ;;
esac
