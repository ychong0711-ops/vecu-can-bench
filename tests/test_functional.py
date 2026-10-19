"""Functional tests: DBC consistency, payload layout, control logic, echo."""
import os
import struct
import time

import pytest

from conftest import (ROOT, bin_path, can_socket, load_json, meas_payload,
                      nodes, pack_frame, unpack_frame, unpack_meas, vcan)

# CAN ID map from generated idmap_gen (single source of truth: dbc/vecu.dbc)
try:
    from idmap_gen import NAME_TO_ID as IDS
except ImportError:
    # fallback for environments where idmap_gen is not importable
    IDS = {
        "SENSOR_SPEED": 0x100, "SENSOR_DIST": 0x101, "SENSOR_TEMP": 0x102,
        "CMD_BRAKE": 0x200, "CMD_LIGHT": 0x201,
        "HB_SENSOR": 0x300, "HB_FEATURE": 0x301, "HB_ASSISTANT": 0x302,
        "LOADGEN": 0x400, "PING": 0x500, "PONG": 0x501,
    }


def test_dbc_matches_id_map():
    """dbc/vecu.dbc parses and contains exactly the documented IDs."""
    cantools = pytest.importorskip("cantools")
    db = cantools.database.load_file(os.path.join(ROOT, "dbc", "vecu.dbc"))
    got = {m.name: m.frame_id for m in db.messages}
    assert got == IDS
    brake = db.get_message_by_name("CMD_BRAKE")
    assert {s.name for s in brake.signals} >= {"BrakeLevel", "SrcSeq", "Seq", "TxMs"}


def test_meas_payload_roundtrip():
    payload = meas_payload(seq=0x1234, value=0xABCD)
    seq, _tx, value = unpack_meas(payload)
    assert (seq, value) == (0x1234, 0xABCD)


def test_feature_brake_logic(nodes):
    """dist < 300 cm must produce brake > 0; far distance must release it."""
    nodes(bin_path("ecu_feature"))
    s = can_socket(timeout=2.0)
    try:
        # --- approaching obstacle: dist = 100 cm -> expect strong brake
        seq = 0
        deadline = time.monotonic() + 1.5
        brakes = []
        while time.monotonic() < deadline:
            s.send(pack_frame(IDS["SENSOR_DIST"], meas_payload(seq, 100)))
            seq += 1
            try:
                # bounded drain: the node publishes every 100 ms forever, so
                # an unbounded recv loop would never see the 1 s idle timeout
                inner_end = time.monotonic() + 0.4
                while time.monotonic() < inner_end:
                    cid, data = unpack_frame(s.recv(16))
                    if cid == IDS["CMD_BRAKE"]:
                        brakes.append(unpack_meas(data)[2] & 0xFF)
            except (TimeoutError, OSError):
                pass
            time.sleep(0.04)
        assert brakes, "no CMD_BRAKE observed"
        assert max(brakes) > 50, f"expected hard braking, got {max(brakes)}"

        # --- clear road: dist = 1800 cm -> brake must return to 0
        deadline = time.monotonic() + 1.5
        brakes_far = []
        while time.monotonic() < deadline:
            s.send(pack_frame(IDS["SENSOR_DIST"], meas_payload(seq, 1800)))
            seq += 1
            try:
                # bounded drain (same as above)
                inner_end = time.monotonic() + 0.4
                while time.monotonic() < inner_end:
                    cid, data = unpack_frame(s.recv(16))
                    if cid == IDS["CMD_BRAKE"]:
                        brakes_far.append(unpack_meas(data)[2] & 0xFF)
            except (TimeoutError, OSError):
                pass
            time.sleep(0.04)
        assert brakes_far and brakes_far[-1] == 0, "brake not released"
    finally:
        s.close()


def test_assistant_pong_echo(nodes):
    """PING must be echoed as PONG preserving seq and tx timestamp."""
    nodes(bin_path("ecu_assistant"))
    s = can_socket(timeout=2.0)
    try:
        payload = meas_payload(seq=7, value=0)
        s.send(pack_frame(IDS["PING"], payload))
        t0 = time.monotonic()
        while time.monotonic() - t0 < 2.0:
            cid, data = unpack_frame(s.recv(16))
            if cid == IDS["PONG"]:
                seq, tx, _ = unpack_meas(data)
                exp_seq, exp_tx, _ = unpack_meas(payload)
                assert seq == exp_seq and tx == exp_tx
                return
        pytest.fail("no PONG received within 2 s")
    finally:
        s.close()
