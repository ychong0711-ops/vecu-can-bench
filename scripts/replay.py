#!/usr/bin/env python3
"""vECU-Bench HIL 시나리오 리플레이 (Track A3 — 구현 예정).

결정된 구현 방식: Python 전용 노드 (vecu-track-a.plan.md §A3.1, cmt-a3rp03).

기능 (A3 착수 시 구현):
  - replay/scenarios/*.yaml 센서 타임라인을 vcan0에 실시간 재생
  - --property 로 속성 검사:
      P1: SENSOR_DIST < 300 관측 후 300 ms 내 CMD_BRAKE.brake > 0
      P2: 정상 주행(300cm 초과 지속)에서 brake == 0 유지
  - 위반 시 타임스탬프·프레임 ID 포함 리포트, pytest(tests/test_hil_replay.py) 편입

Usage (예정):
  replay.py --scenario replay/scenarios/approach_brake.yaml --duration 10
  replay.py --scenario ... --property p1,p2
"""
import sys


def main() -> int:
    print("replay.py: not implemented yet (Track A3, planned day ~7). "
          "See ../vecu-track-a.plan.md §A3.", file=sys.stderr)
    return 77  # EX_CONFIG-ish: reserved for A3


if __name__ == "__main__":
    raise SystemExit(main())
