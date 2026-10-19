#!/usr/bin/env python3
"""vECU-Bench HIL-style scenario replay + property checker (Track A3).

Determined implementation: Python-only replay node (vecu-track-a.plan.md
§A3.1, decision cmt-a3rp03). It drives the ECU under test over a real vcan
bus and decides whether the observed actuator traffic satisfies the timing
properties — no CAN frames are faked at the socket level beyond the sensor
input itself.

Sources
  --scenario replay/scenarios/*.yaml   scripted sensor timeline (preferred)
  --from-frames reports/*/frames.csv   replay a monitor recording (SENSOR_*)

Properties (latency budget defaults to 300 ms = 3 x the 100 ms CMD period)
  P1 brake_response : a SENSOR_DIST < 300 cm observation must be braked —
                      either CMD_BRAKE.brake > 0 was already active at that
                      moment, or it starts within the budget. (State-based:
                      a fresh command is not required when the ECU is already
                      braking; the literal "new command within 300 ms" form
                      produced false positives when the obstacle cleared
                      inside one command period — see the emergency_brake
                      scenario in replay/scenarios/.)
  P2 brake_release  : CMD_BRAKE.brake > 0 while the whole preceding budget
                      window shows SENSOR_DIST >= 300 cm is a violation
                      (brake must release on a clear road)
  P3 cmd_liveness   : CMD_BRAKE must keep arriving with gaps <= cadence budget
                      (250 ms) — holds for stalled sensors too

Exit codes: 0 = all properties hold, 1 = violation(s), 2 = usage/config error.

Usage:
  python3 scripts/replay.py --scenario replay/scenarios/approach_brake.yaml
  python3 scripts/replay.py --from-frames reports/demo/frames.csv
  python3 scripts/replay.py --scenario ... --no-property      # replay only
"""
import argparse
import json
import os
import socket
import struct
import sys
import time

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML required: pip install -r requirements.txt")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# CAN ids (mirror include/id_map.h; same values the conftest asserts against)
ID_SENSOR_SPEED = 0x100
ID_SENSOR_DIST = 0x101
ID_SENSOR_TEMP = 0x102
ID_CMD_BRAKE = 0x200
ID_CMD_LIGHT = 0x201
ID_HB_SENSOR = 0x300
ID_HB_FEATURE = 0x301
ID_HB_ASSISTANT = 0x302

NAME_TO_ID = {
    "SENSOR_SPEED": ID_SENSOR_SPEED, "SENSOR_DIST": ID_SENSOR_DIST,
    "SENSOR_TEMP": ID_SENSOR_TEMP, "CMD_BRAKE": ID_CMD_BRAKE,
    "CMD_LIGHT": ID_CMD_LIGHT, "HB_SENSOR": ID_HB_SENSOR,
    "HB_FEATURE": ID_HB_FEATURE, "HB_ASSISTANT": ID_HB_ASSISTANT,
}

DIST_RAMP_CM = 300          # SWC braking ramp starts below this (swc_brake.c)
DEFAULT_LATENCY_MS = 300    # response budget (3 x the 100 ms CMD period)
DEFAULT_CADENCE_MS = 250    # liveness budget for CMD_BRAKE gaps

CAN_RAW_RECV_OWN_MSGS = 4   # linux/can/raw.h (needed, see open_socket)


# --------------------------------------------------------------------------
# CAN helpers (mirror tests/conftest.py — scripts must stay importable alone)
# --------------------------------------------------------------------------
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
    if len(data) < 8:
        return None
    return struct.unpack("<HIH", data[:8])   # seq, tx_ms, value


def open_socket(iface, timeout=0.05):
    """CAN_RAW socket that also receives its own sends.

    CAN_RAW_RECV_OWN_MSGS is REQUIRED here: the checker's input is the sensor
    timeline *this process injects* (P1/P2 look at observed SENSOR_DIST), and
    without self-reception P1 would be vacuous (no distance observations at
    all) — a checker that cannot fail is worthless. The ECU's own frames are
    delivered by the vcan loopback like in every other test.
    """
    s = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    try:
        s.setsockopt(socket.SOL_CAN_RAW, CAN_RAW_RECV_OWN_MSGS, 1)
    except OSError as e:  # pragma: no cover - kernel without the option
        s.close()
        raise SystemExit(f"{iface}: cannot enable CAN_RAW_RECV_OWN_MSGS "
                         f"(needed for property checking): {e}")
    s.settimeout(timeout)
    return s


# --------------------------------------------------------------------------
# Scenario loading
# --------------------------------------------------------------------------
def resolve_id(spec):
    if isinstance(spec, int):
        return spec
    if isinstance(spec, str):
        if spec in NAME_TO_ID:
            return NAME_TO_ID[spec]
        try:
            return int(spec, 0)
        except ValueError:
            pass
    raise SystemExit(f"unknown CAN id spec: {spec!r}")


def load_scenario(path):
    """Parse a scenario YAML into {'name','duration_ms','events','source'}.

    events = sorted list of (t_ms, can_id, value); 'steps' are expanded at
    each signal's period, 'until_ms' stops a signal early (stall scenarios).
    """
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    duration_ms = int(doc.get("duration_ms", 0))
    events = []
    for sig in doc.get("signals", []):
        cid = resolve_id(sig["id"])
        period = int(sig.get("period_ms", 20))
        if period <= 0:
            raise SystemExit(f"{path}: period_ms must be > 0")
        until = int(sig.get("until_ms", duration_ms))
        steps = sorted(sig.get("steps", []), key=lambda s: int(s["at_ms"]))
        if not steps:
            raise SystemExit(f"{path}: signal {sig['id']} has no steps")
        for i, step in enumerate(steps):
            t_start = int(step["at_ms"])
            t_end = int(steps[i + 1]["at_ms"]) if i + 1 < len(steps) else until
            t_end = min(t_end, until, duration_ms or t_end)
            value = int(step["value"])
            if not 0 <= value <= 0xFFFF:
                raise SystemExit(f"{path}: value {value} out of u16 range")
            t = t_start
            while t < t_end:
                events.append((t, cid, value))
                t += period
    if not events:
        raise SystemExit(f"{path}: no events produced")
    last_t = max(e[0] for e in events)
    duration_ms = max(duration_ms, last_t + 50)
    events.sort(key=lambda e: e[0])
    return {"name": doc.get("name", os.path.splitext(os.path.basename(path))[0]),
            "description": doc.get("description", ""),
            "duration_ms": duration_ms, "events": events, "source": path}


def load_frames_csv(path, ids=(ID_SENSOR_DIST, ID_SENSOR_SPEED)):
    """Replay a monitor recording: rx_us,id,dlc,seq,tx_ms,lat_ms,val."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) < 7:
                continue
            try:
                rx_us = int(parts[0])
                cid = int(parts[1], 16) if parts[1].lower().startswith("0x") \
                    else int(parts[1])
                value = int(parts[6])
            except ValueError:
                continue          # header or a frame without a measured value
            if cid in ids:
                rows.append((rx_us, cid, value))
    if not rows:
        raise SystemExit(f"{path}: no sensor frames found")
    rows.sort()
    t0 = rows[0][0]
    events = [((rx_us - t0) // 1000, cid, value) for rx_us, cid, value in rows]
    duration_ms = max(e[0] for e in events) + 50
    return {"name": os.path.splitext(os.path.basename(path))[0],
            "description": "replay of monitor CSV recording",
            "duration_ms": duration_ms, "events": events, "source": path}


# --------------------------------------------------------------------------
# Replay engine
# --------------------------------------------------------------------------
def run_replay(iface, events, duration_ms, settle_ms=400, recv_timeout=0.002,
               warmup_ms=300):
    """Send the sensor timeline over `iface` and collect observed frames.

    Returns observations as a sorted list of (t_ms, can_id, value) relative to
    the start of the replay; t_ms is the local receive time.

    `warmup_ms` first waits (up to that long) for the ECU's first CMD_BRAKE
    frame so the timeline never starts against a cold bus — otherwise the
    property windows would depend on the ECU's startup phase.
    """
    sock = open_socket(iface)
    obs = []
    try:
        warm_end = time.monotonic() + warmup_ms / 1000.0
        while time.monotonic() < warm_end:
            sock.settimeout(recv_timeout)
            try:
                raw = sock.recv(16)
            except socket.timeout:
                continue
            except OSError:
                continue
            if unpack_frame(raw)[0] == ID_CMD_BRAKE:
                break                       # ECU is alive and publishing
        t0 = time.monotonic()
        end = t0 + (duration_ms + settle_ms) / 1000.0
        idx, total = 0, len(events)
        while True:
            now = time.monotonic()
            if now >= end:
                break
            elapsed_ms = (now - t0) * 1000.0
            while idx < total and events[idx][0] <= elapsed_ms:
                _, cid, value = events[idx]
                try:
                    sock.send(pack_frame(cid, meas_payload(idx & 0xFFFF, value)))
                except OSError as e:
                    raise SystemExit(f"replay send failed on {iface}: {e}")
                idx += 1
            sock.settimeout(recv_timeout)
            try:
                raw = sock.recv(16)
            except socket.timeout:
                continue
            except OSError:
                continue
            t_ms = int((time.monotonic() - t0) * 1000)
            cid, data = unpack_frame(raw)
            meas = unpack_meas(data)
            if meas is None:
                continue
            obs.append((t_ms, cid, meas[2]))
    finally:
        sock.close()
    obs.sort(key=lambda o: o[0])
    return obs


# --------------------------------------------------------------------------
# Property checker (A3.2)
# --------------------------------------------------------------------------
def check_properties(obs, latency_ms=DEFAULT_LATENCY_MS,
                     cadence_ms=DEFAULT_CADENCE_MS, max_per_property=5):
    """Evaluate P1/P2/P3 over the observation list.

    Returns (violations, counts): `violations` is capped per property for
    readability, `counts` is the full per-property tally.
    """
    dist = [(t, v) for t, cid, v in obs if cid == ID_SENSOR_DIST]
    cmd = [(t, v & 0xFF) for t, cid, v in obs if cid == ID_CMD_BRAKE]
    violations = []

    # P1: brake response after a close-range observation. State-based: an
    # observation is answered if braking was already active at that moment
    # (`brake_at(t) > 0`) or if a command starts braking within the budget.
    # Requiring a *new* command would flag correct behaviour whenever the
    # obstacle clears inside one command period (the last in-flight samples
    # are then answered by the release command, not by a fresh brake).
    for t, d in dist:
        if d >= DIST_RAMP_CM:
            continue
        already = [b for ct, b in cmd if ct <= t]
        if already and already[-1] > 0:
            continue
        answered = any(t <= ct <= t + latency_ms and b > 0 for ct, b in cmd)
        if not answered:
            violations.append({
                "property": "P1", "at_ms": t,
                "detail": f"SENSOR_DIST={d}cm at {t}ms: not braked at that "
                          f"moment and no CMD_BRAKE>0 within {latency_ms}ms"})

    # P2: brake released on a sustained clear road
    for ct, b in cmd:
        if b == 0:
            continue
        window = [d for t, d in dist if ct - latency_ms <= t <= ct]
        if window and min(window) >= DIST_RAMP_CM:
            violations.append({
                "property": "P2", "at_ms": ct,
                "detail": f"CMD_BRAKE={b} at {ct}ms although SENSOR_DIST>="
                          f"{min(window)}cm for the whole preceding "
                          f"{latency_ms}ms"})

    # P3: command liveness (holds during sensor stalls)
    if not cmd:
        violations.append({
            "property": "P3", "at_ms": None,
            "detail": f"no CMD_BRAKE frame observed during the replay "
                      f"({len(obs)} frames total) — ECU silent?"})
    else:
        prev = cmd[0][0]
        for t, _ in cmd[1:]:
            if t - prev > cadence_ms:
                violations.append({
                    "property": "P3", "at_ms": t,
                    "detail": f"CMD_BRAKE gap of {t - prev}ms > {cadence_ms}ms"})
            prev = t

    counts = {"P1": 0, "P2": 0, "P3": 0}
    shown = {"P1": 0, "P2": 0, "P3": 0}
    capped = []
    for v in violations:
        counts[v["property"]] += 1
        if shown[v["property"]] < max_per_property:
            shown[v["property"]] += 1
            capped.append(v)
    return capped, counts


def evaluate(scenario, obs, latency_ms=DEFAULT_LATENCY_MS,
             cadence_ms=DEFAULT_CADENCE_MS):
    violations, counts = check_properties(obs, latency_ms, cadence_ms)
    return {
        "scenario": scenario["name"],
        "source": scenario["source"],
        "description": scenario.get("description", ""),
        "duration_ms": scenario["duration_ms"],
        "events_sent": len(scenario["events"]),
        "frames_observed": len(obs),
        "latency_ms": latency_ms,
        "cadence_ms": cadence_ms,
        "counts": counts,
        "violations": violations,
        "passed": sum(counts.values()) == 0,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--scenario", help="scenario YAML (replay/scenarios/*.yaml)")
    src.add_argument("--from-frames", help="monitor frames.csv to replay")
    ap.add_argument("--iface", default=os.environ.get("VECU_IFACE", "vcan0"))
    ap.add_argument("--latency-ms", type=int, default=DEFAULT_LATENCY_MS)
    ap.add_argument("--cadence-ms", type=int, default=DEFAULT_CADENCE_MS)
    ap.add_argument("--settle-ms", type=int, default=400,
                    help="extra observation time after the timeline")
    ap.add_argument("--no-property", action="store_true",
                    help="replay only, skip the property checks")
    ap.add_argument("--out", help="report path (default reports/replay/<name>.json)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    scenario = load_scenario(args.scenario) if args.scenario \
        else load_frames_csv(args.from_frames)

    if not args.quiet:
        print(f"[replay] {scenario['name']}: {len(scenario['events'])} events, "
              f"{scenario['duration_ms']}ms timeline on {args.iface}")
    obs = run_replay(args.iface, scenario["events"], scenario["duration_ms"],
                     settle_ms=args.settle_ms)

    report = evaluate(scenario, obs, args.latency_ms, args.cadence_ms)
    if args.no_property:
        report["passed"] = None

    out = args.out or os.path.join(ROOT, "reports", "replay",
                                   f"{scenario['name']}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    if not args.quiet:
        print(f"[replay] observed {report['frames_observed']} frames "
              f"(P1={report['counts']['P1']} P2={report['counts']['P2']} "
              f"P3={report['counts']['P3']} violations)")
        for v in report["violations"]:
            print(f"  [{v['property']}] {v['detail']}")
        print(f"[replay] report: {out}")

    if args.no_property:
        return 0
    if report["passed"]:
        if not args.quiet:
            print("[replay] PASS — all properties hold")
        return 0
    print(f"[replay] FAIL — {sum(report['counts'].values())} violation(s)",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
