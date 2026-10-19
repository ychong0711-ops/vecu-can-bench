#!/usr/bin/env bash
# CI helper (Track A4): bring up vcan on GitHub-hosted Ubuntu runners.
#
# Those runner images ship a stripped kernel module set: `modprobe vcan` fails
# with "Module vcan not found in directory /lib/modules/<kver>" because
# drivers/net/can (vcan.ko) lives in linux-modules-extra, which the image does
# not install. Install the package matching the *running* kernel first, then
# delegate to the regular setup script.
#
# Usage: sudo ./scripts/ci_setup_vcan.sh [iface]      (default: vcan0)
set -euo pipefail

iface="${1:-vcan0}"
here="$(cd "$(dirname "$0")" && pwd)"

if ! modprobe vcan 2>/dev/null && [ ! -d /sys/module/vcan ]; then
    kver="$(uname -r)"
    echo "[ci] vcan module missing for $kver -> installing linux-modules-extra"
    apt-get update
    apt-get install -y --no-install-recommends "linux-modules-extra-$kver" \
        || apt-get install -y --no-install-recommends linux-modules-extra-azure
    depmod -a "$kver" || true
    modprobe vcan
fi

exec "$here/setup_vcan.sh" "$iface"
