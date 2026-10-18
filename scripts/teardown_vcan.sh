#!/usr/bin/env bash
set -euo pipefail
IFACE="${1:-vcan0}"
ip link delete dev "$IFACE" 2>/dev/null || true
echo "vcan interface $IFACE removed"
