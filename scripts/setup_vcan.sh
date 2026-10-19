#!/usr/bin/env bash
# Create and bring up a virtual CAN interface (vcan).
# Requires: Linux with the 'vcan' kernel module, root or CAP_NET_ADMIN.
set -euo pipefail
IFACE="${1:-vcan0}"
modprobe vcan 2>/dev/null || true
if ! ip link show "$IFACE" >/dev/null 2>&1; then
    ip link add dev "$IFACE" type vcan
fi
ip link set "$IFACE" up
ip link set "$IFACE" txqueuelen 10000   # avoid ENOBUFS under heavy load
echo "vcan interface $IFACE is up"
ip -details link show "$IFACE" | head -3
