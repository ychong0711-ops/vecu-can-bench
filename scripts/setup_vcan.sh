#!/usr/bin/env bash
# Create and bring up a virtual CAN interface (vcan).
# Requires: Linux with the 'vcan' kernel module, root or CAP_NET_ADMIN.
set -euo pipefail
IFACE="${1:-vcan0}"
if ! modprobe vcan 2>/dev/null && [ ! -d /sys/module/vcan ]; then
    echo "vcan: cannot load the 'vcan' kernel module (required)." >&2
    echo "  * stock WSL2 kernels do not ship it — build a custom kernel with" >&2
    echo "    CONFIG_VCAN=m (see README) or use native Linux / a VM" >&2
    echo "  * on CI runners install the matching modules package first:" >&2
    echo "    sudo ./scripts/ci_setup_vcan.sh $IFACE" >&2
    exit 1
fi
if ! ip link show "$IFACE" >/dev/null 2>&1; then
    ip link add dev "$IFACE" type vcan
fi
ip link set "$IFACE" up
ip link set "$IFACE" txqueuelen 10000   # avoid ENOBUFS under heavy load
echo "vcan interface $IFACE is up"
ip -details link show "$IFACE" | head -3
