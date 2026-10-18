"""Performance/regression tests on a live vcan bus.

Thresholds are intentionally generous: they catch real regressions (broken
pacing, buffer overflows, logic stalls) without flaking on slow CI runners.
"""
import os
import subprocess
import time

import pytest

from conftest import (IFACE, ROOT, bin_path, load_json, nodes, vcan)

MON = bin_path("monitor")


def run_monitor(nodes_, dur, extra=()):
    sumf = "/tmp/vecu_summary.json"
    if os.path.exists(sumf):
        os.remove(sumf)
    nodes_(MON, "--iface", IFACE, "--duration", str(dur),
           "--summary", sumf, *extra, settle=0.2)
    # wait for the monitor process to finish naturally
    time.sleep(dur + 1.0)
    assert os.path.exists(sumf), "monitor produced no summary"
    return load_json(sumf)


def test_baseline_zero_loss(nodes):
    """Full stack, idle bus: no frame may be lost; heartbeats must flow."""
    for ecu in ("ecu_sensor", "ecu_feature", "ecu_assistant"):
        nodes(bin_path(ecu), "--iface", IFACE, "--duration", "8")
    s = run_monitor(nodes, 8)
    for hexid in ("0x100", "0x101", "0x102", "0x200", "0x201"):
        st = s["ids"][hexid]
        assert st["lost"] == 0, f"{hexid} lost {st['lost']} frames on idle bus"
        assert st["count"] > 20, f"{hexid} suspiciously few frames: {st['count']}"


def test_ping_rtt_low(nodes):
    """Active probe RTT through the assistant must stay well below 10 ms."""
    nodes(bin_path("ecu_assistant"), "--iface", IFACE, "--duration", "6")
    s = run_monitor(nodes, 6, extra=("--ping-interval", "5"))
    pong = s["ids"]["0x501"]
    assert pong["count"] > 500, f"too few PONGs: {pong['count']}"
    assert pong["lost"] == 0
    assert pong["lat_ms"]["p99"] < 10, f"RTT p99 too high: {pong['lat_ms']}"


def test_busload_accuracy(nodes):
    """Injector targeting 25% load must measure within 21..29%."""
    nodes(bin_path("injector"), "--iface", IFACE, "--load-pct", "25",
          "--duration", "6")
    s = run_monitor(nodes, 6)
    avg = s["bus"]["avg_load_pct"]
    assert 21.0 <= avg <= 29.0, f"bus load off target: {avg:.2f}%"


def test_latency_under_load(nodes):
    """With 40% background load, sensor frames keep p99 < 20 ms, loss < 0.5%."""
    for ecu in ("ecu_sensor", "ecu_feature", "ecu_assistant"):
        nodes(bin_path(ecu), "--iface", IFACE, "--duration", "8")
    nodes(bin_path("injector"), "--iface", IFACE, "--load-pct", "40",
          "--duration", "8")
    s = run_monitor(nodes, 8)
    dist = s["ids"]["0x101"]
    assert dist["loss_pct"] < 0.5
    assert dist["lat_ms"]["p99"] < 20, f"latency p99 under load: {dist['lat_ms']}"
    assert 35.0 <= s["bus"]["avg_load_pct"] <= 50.0


def test_netem_delay_visible(nodes):
    """netem delay 10ms+-2ms must be observable in RTT (p50 >= 15 ms)."""
    if os.geteuid() != 0 and not os.access("/usr/sbin/tc", os.X_OK):
        pytest.skip("tc not available")
    r = subprocess.run(["tc", "qdisc", "replace", "dev", IFACE, "root",
                        "netem", "delay", "10ms", "2ms"], capture_output=True)
    if r.returncode != 0:
        pytest.skip(f"netem unavailable: {r.stderr.decode()[:120]}")
    try:
        nodes(bin_path("ecu_assistant"), "--iface", IFACE, "--duration", "6")
        s = run_monitor(nodes, 6, extra=("--ping-interval", "10"))
        pong = s["ids"]["0x501"]
        assert pong["count"] > 100
        assert pong["lat_ms"]["p50"] >= 15, \
            f"netem delay not visible: {pong['lat_ms']}"
    finally:
        subprocess.run(["tc", "qdisc", "del", "dev", IFACE, "root"],
                       capture_output=True)
