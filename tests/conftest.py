"""Shared fixtures/helpers for vECU-Bench tests.

Tests requiring a live vcan interface skip automatically when the kernel
module or permissions are unavailable (e.g. restricted containers).
"""
import os
import socket
import struct
import subprocess
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IFACE = os.environ.get("VECU_IFACE", "vcan0")


@pytest.fixture(scope="session")
def build():
    subprocess.run(["make", "-C", ROOT], check=True, capture_output=True)
    return ROOT


@pytest.fixture(scope="session")
def vcan(build):
    r = subprocess.run([os.path.join(ROOT, "scripts", "setup_vcan.sh"), IFACE],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"vcan unavailable: {(r.stderr or r.stdout).strip()[:160]}")
    return IFACE


class Node:
    def __init__(self, args):
        self.p = subprocess.Popen(args, cwd=ROOT,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)

    def alive(self):
        return self.p.poll() is None

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()


@pytest.fixture
def nodes(vcan):
    started = []

    def _start(*args, settle=0.3):
        n = Node([str(a) for a in args])
        started.append(n)
        time.sleep(settle)
        assert n.alive(), f"node exited early: {args}"
        return n

    yield _start
    for n in started:
        n.stop()


def bin_path(name):
    return os.path.join(ROOT, "bin", name)


def can_socket(iface=IFACE, timeout=1.0):
    s = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.settimeout(timeout)
    s.bind((iface,))
    return s


def meas_payload(seq, value):
    """8-byte measured-frame payload: [seq u16][tx_ms u32][value u16] LE."""
    tx = int(time.clock_gettime(time.CLOCK_MONOTONIC) * 1000) & 0xFFFFFFFF
    return struct.pack("<HIH", seq, tx, value)


def pack_frame(can_id, data):
    return struct.pack("=IB3x", can_id, len(data)) + bytes(data).ljust(8, b"\0")


def unpack_frame(raw):
    can_id, dlc = struct.unpack("=IB3x", raw[:8])
    return can_id & 0x1FFFFFFF, raw[8:8 + dlc]


def unpack_meas(data):
    seq, tx, value = struct.unpack("<HIH", bytes(data[:8]))
    return seq, tx, value


def load_json(path):
    import json
    with open(path) as f:
        return json.load(f)
