"""Unit tests for the extracted feature-ECU control logic (SWC-style).

Builds src/ecu_logic.c into a shared library (gcc -shared -fPIC) in a temp
dir and exercises it through ctypes. No vcan interface, no node binaries and
no Makefile required: these tests run standalone wherever gcc exists.
"""
import ctypes
import os
import shutil
import subprocess
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "ecu_logic.c")
INC = os.path.join(ROOT, "include")

GCC = shutil.which("gcc") or shutil.which("cc")

# CMD_LIGHT payload values (mirror include/ecu_logic.h)
LIGHT_OFF, LIGHT_LOW_BEAM, LIGHT_HAZARD = 0, 1, 2


@pytest.fixture(scope="module")
def logic():
    """Compile src/ecu_logic.c -> libecu_logic.so and load it via ctypes."""
    if not GCC:
        pytest.skip("gcc not available")
    tmp = tempfile.mkdtemp(prefix="vecu_logic_")
    so = os.path.join(tmp, "libecu_logic.so")
    r = subprocess.run([GCC, "-shared", "-fPIC", "-I", INC, SRC, "-o", so],
                       capture_output=True, text=True)
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        pytest.skip(f"cannot build ecu_logic.so: {r.stderr.strip()[:200]}")
    lib = ctypes.CDLL(so)
    u8, u16 = ctypes.c_uint8, ctypes.c_uint16
    lib.ecu_logic_brake_level.argtypes = [ctypes.c_int, u16]
    lib.ecu_logic_brake_level.restype = u8
    lib.ecu_logic_light_state.argtypes = [ctypes.c_int, u16, ctypes.c_int, u16]
    lib.ecu_logic_light_state.restype = u8
    lib.ecu_logic_cmd_brake_pack.argtypes = [u16, u8]
    lib.ecu_logic_cmd_brake_pack.restype = u16
    lib.ecu_logic_cmd_brake_unpack.argtypes = \
        [u16, ctypes.POINTER(u8), ctypes.POINTER(u8)]
    lib.ecu_logic_cmd_brake_unpack.restype = None
    yield lib
    del lib
    shutil.rmtree(tmp, ignore_errors=True)


def unpack(lib, value):
    src = ctypes.c_uint8()
    brk = ctypes.c_uint8()
    lib.ecu_logic_cmd_brake_unpack(ctypes.c_uint16(value),
                                   ctypes.byref(src), ctypes.byref(brk))
    return src.value, brk.value


def test_brake_threshold_boundaries(logic):
    """dist < 300 cm: clamp((300-dist)/3, 0..100); dist >= 300 cm: 0."""
    # inside the ramp: exact integer-division values at the edges
    assert logic.ecu_logic_brake_level(1, 0) == 100      # b=100 -> clamped to 100
    assert logic.ecu_logic_brake_level(1, 150) == 50     # (300-150)/3
    assert logic.ecu_logic_brake_level(1, 297) == 1      # (300-297)/3
    assert logic.ecu_logic_brake_level(1, 299) == 0      # (300-299)/3 = 0 (int div)
    # boundary: 300 itself is NOT < 300 -> brake off
    assert logic.ecu_logic_brake_level(1, 300) == 0
    assert logic.ecu_logic_brake_level(1, 301) == 0
    assert logic.ecu_logic_brake_level(1, 65535) == 0


def test_brake_stale_distance(logic):
    """have_dist == 0 (stale/unknown) must force brake 0 regardless of value."""
    assert logic.ecu_logic_brake_level(0, 0) == 0
    assert logic.ecu_logic_brake_level(0, 100) == 0      # stale garbage value
    assert logic.ecu_logic_brake_level(0, 299) == 0


def test_light_boundaries_and_priority(logic):
    """dist < 150 -> hazard; else speed > 70.0 km/h -> low beam; hazard wins."""
    # hazard boundary
    assert logic.ecu_logic_light_state(1, 149, 0, 0) == LIGHT_HAZARD
    assert logic.ecu_logic_light_state(1, 150, 0, 0) == LIGHT_OFF
    # low-beam boundary (speed in 0.1 km/h units)
    assert logic.ecu_logic_light_state(1, 500, 1, 700) == LIGHT_OFF
    assert logic.ecu_logic_light_state(1, 500, 1, 701) == LIGHT_LOW_BEAM
    # hazard has priority over low beam (else-if chain)
    assert logic.ecu_logic_light_state(1, 149, 1, 900) == LIGHT_HAZARD
    assert logic.ecu_logic_light_state(1, 150, 1, 900) == LIGHT_LOW_BEAM


def test_light_stale_inputs(logic):
    """Stale distance disables hazard; missing speed disables low beam."""
    # stale dist: hazard suppressed even at dist=100, low beam still possible
    assert logic.ecu_logic_light_state(0, 100, 1, 800) == LIGHT_LOW_BEAM
    assert logic.ecu_logic_light_state(0, 100, 0, 0) == LIGHT_OFF
    # no speed sample yet: light stays off on a clear road
    assert logic.ecu_logic_light_state(1, 500, 0, 900) == LIGHT_OFF


def test_src_seq_wraparound(logic):
    """Low-byte carry wraps 255->0 (and seq wraps 65535->0) without loss."""
    # seq 255 -> 256: carried src_seq8 must go 0xFF -> 0x00
    assert logic.ecu_logic_cmd_brake_pack(255, 42) == 0xFF2A
    assert logic.ecu_logic_cmd_brake_pack(256, 42) == 0x002A
    # u16 seq wrap 65535 -> 0: carried byte 0xFF -> 0x00
    assert logic.ecu_logic_cmd_brake_pack(65535, 1) == 0xFF01
    assert logic.ecu_logic_cmd_brake_pack(0, 1) == 0x0001


def test_cmd_brake_pack_unpack_roundtrip(logic):
    """pack/unpack are inverse: brake level and src_seq8 survive the wire."""
    for seq in (0, 1, 255, 256, 511, 65535):
        for brake in (0, 1, 50, 100):
            value = logic.ecu_logic_cmd_brake_pack(seq, brake)
            src_seq8, level = unpack(logic, value)
            assert src_seq8 == (seq & 0xFF)
            assert level == brake
