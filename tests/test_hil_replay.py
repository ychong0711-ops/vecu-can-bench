"""HIL-style replay tests (Track A3): real ECU on vcan0 + property checker.

Two passing scenarios (cruise, approach+brake), one stalled-sensor liveness
scenario, and one deliberately violating run (no ECU at all) that proves the
checker actually catches what it claims to catch.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import replay  # noqa: E402  (path inserted above)

SCENARIOS = os.path.join(ROOT, "replay", "scenarios")
IFACE = os.environ.get("VECU_IFACE", "vcan0")


def ecu(name="ecu_feature"):
    return os.path.join(ROOT, "bin", name)


def run_scenario(filename, duration_cap_ms=None, settle_ms=400):
    sc = replay.load_scenario(os.path.join(SCENARIOS, filename))
    events, duration = sc["events"], sc["duration_ms"]
    if duration_cap_ms:                      # shorten long replays if needed
        events = [e for e in events if e[0] <= duration_cap_ms]
        duration = duration_cap_ms
    obs = replay.run_replay(IFACE, events, duration, settle_ms=settle_ms)
    sc["events"], sc["duration_ms"] = events, duration
    return sc, obs


def test_replay_cruise_keeps_brake_off(nodes):
    """Clear road: no P1/P2 violations (brake must not be commanded)."""
    nodes(ecu())
    sc, obs = run_scenario("cruise.yaml")
    violations, counts = replay.check_properties(obs)
    assert counts == {"P1": 0, "P2": 0, "P3": 0}, violations
    # sanity: the ECU was really on the bus and stayed released
    brakes = [v & 0xFF for _, cid, v in obs if cid == replay.ID_CMD_BRAKE]
    assert brakes, "no CMD_BRAKE frames observed at all"
    assert max(brakes) == 0


def test_replay_approach_brake_response(nodes):
    """Close-range observation must be answered within the latency budget."""
    nodes(ecu())
    sc, obs = run_scenario("approach_brake.yaml")
    violations, counts = replay.check_properties(obs)
    assert counts["P1"] == 0, violations
    assert counts["P3"] == 0, violations
    # sanity: the scenario really did provoke braking (not a vacuous pass)
    brakes = [v & 0xFF for _, cid, v in obs if cid == replay.ID_CMD_BRAKE]
    assert max(brakes) > 0, "expected a braking command after the approach"
    # and it released again once the road was clear (P2 window logic would
    # have flagged a stuck brake; assert the released state was observed too)
    assert brakes[-1] == 0, "brake must be released after the road clears"


def test_replay_sensor_stall_keeps_command_liveness(nodes):
    """A stalled distance feed must not stop CMD_BRAKE (P3 liveness)."""
    nodes(ecu())
    sc, obs = run_scenario("sensor_stall.yaml")
    violations, counts = replay.check_properties(obs)
    assert counts["P3"] == 0, violations
    # the stall starts at 600 ms: frames must still arrive well after that
    late = [t for t, cid, _ in obs
            if cid == replay.ID_CMD_BRAKE and t > sc["duration_ms"] - 500]
    assert len(late) >= 3, f"liveness lost after the stall: {late}"


def test_property_checker_catches_silent_ecu(vcan):
    """Deliberate violation: with no ECU running the checker must complain.

    This is the checker's own confidence test — a silent bus means every
    close-range observation is unanswered (P1) and no command ever appears
    (P3).
    """
    sc, obs = run_scenario("approach_brake.yaml", duration_cap_ms=800)
    violations, counts = replay.check_properties(obs)
    assert counts["P1"] > 0, f"P1 should fire without an ECU: {violations}"
    assert counts["P3"] > 0, f"P3 should fire without an ECU: {violations}"
    kinds = {v["property"] for v in violations}
    assert {"P1", "P3"} <= kinds
    # the replay itself worked: the sensor timeline was observed on the bus
    assert any(cid == replay.ID_SENSOR_DIST for _, cid, _ in obs)
    # and the report carries positions, as required by A3.2
    assert all("at_ms" in v and "detail" in v for v in violations)


# --- deterministic property tests (no vcan needed) ------------------------
# These pin the P1 formulation that the emergency_brake scenario exposed:
# the literal "a new brake command within the budget" rule flags correct
# behaviour when the obstacle clears inside one command period.
def test_p1_state_based_accepts_clearing_obstacle():
    obs = [
        # continuous braking at the command period (P3 needs a regular cadence)
        (400, replay.ID_CMD_BRAKE, 80),      # braking already active
        (500, replay.ID_CMD_BRAKE, 80),
        (600, replay.ID_CMD_BRAKE, 80),
        (700, replay.ID_CMD_BRAKE, 80),
        (800, replay.ID_CMD_BRAKE, 80),
        (900, replay.ID_CMD_BRAKE, 80),
        (920, replay.ID_SENSOR_DIST, 60),    # last samples of the obstacle
        (980, replay.ID_SENSOR_DIST, 60),
        (1000, replay.ID_SENSOR_DIST, 1800),  # road clears
        (1000, replay.ID_CMD_BRAKE, 0),      # next command releases: correct
        (1100, replay.ID_CMD_BRAKE, 0),
    ]
    violations, counts = replay.check_properties(obs)
    assert counts == {"P1": 0, "P2": 0, "P3": 0}, violations


def test_p1_flags_unanswered_close_range():
    obs = [
        (0, replay.ID_CMD_BRAKE, 0),
        (100, replay.ID_SENSOR_DIST, 100),   # close range, never braked
        (200, replay.ID_CMD_BRAKE, 0),
        (300, replay.ID_CMD_BRAKE, 0),
        (400, replay.ID_CMD_BRAKE, 0),
    ]
    violations, counts = replay.check_properties(obs)
    assert counts["P1"] == 1, violations


def test_replay_emergency_brake_no_violations(nodes):
    """Hard braking at 60 cm: full replay must stay clean (regression).

    Before the P1 refinement this scenario reported intermittent false
    positives (the obstacle clears at 1000 ms, inside a command period).
    """
    nodes(ecu())
    sc, obs = run_scenario("emergency_brake.yaml")
    violations, counts = replay.check_properties(obs)
    assert counts == {"P1": 0, "P2": 0, "P3": 0}, violations
    brakes = [v & 0xFF for _, cid, v in obs if cid == replay.ID_CMD_BRAKE]
    assert max(brakes) >= 70, f"expected ~80 % braking at 60 cm, got {max(brakes)}"
