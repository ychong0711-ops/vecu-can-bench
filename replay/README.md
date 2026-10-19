# replay/ — Track A3 HIL 스타일 시나리오 리플레이

계획: `../vecu-track-a.plan.md` §A3 (결정: Python 전용 노드, cmt-a3rp03)

## 구성

| 항목 | 경로 | 설명 |
|---|---|---|
| 시나리오 | `scenarios/*.yaml` | 센서 타임라인 (period + step + until_ms) |
| 엔진/검사기 | `../scripts/replay.py` | vcan0 실시간 재생 + 속성 검사 + JSON 리포트 |
| 테스트 | `../tests/test_hil_replay.py` | 시나리오 3종(정상/접근·제동/스톨) + 위반 검출 + 결정적 프로퍼티 단위 테스트 |

## 사용

```
python3 scripts/replay.py --scenario replay/scenarios/approach_brake.yaml
python3 scripts/replay.py --from-frames reports/demo/frames.csv   # monitor 녹화 재생
python3 scripts/replay.py --scenario ... --latency-ms 300 --no-property
python3 -m pytest tests/test_hil_replay.py -v
```

종료 코드: `0` 전 속성 충족 / `1` 위반 / `2` 사용 오류(리포트는 `reports/replay/<name>.json`).

## 속성 (A3.2)

| ID | 속성 | 정의 |
|---|---|---|
| P1 | brake_response | `SENSOR_DIST < 300` 관측은 제동되어야 한다 — **관측 시점에 이미 brake>0**이거나, 아니면 300 ms(=3×명령주기) 내 제동 시작 |
| P2 | brake_release | 직전 300 ms 창 전체가 `SENSOR_DIST >= 300`인데 `brake > 0`이면 위반 (빈 도로에서 해제 실패) |
| P3 | cmd_liveness | `CMD_BRAKE` 프레임 간격 ≤ 250 ms (센서 스톨 중에도 유지) |

리플레이 소켓은 `CAN_RAW_RECV_OWN_MSGS`를 켠다 — 검사 대상인 센서 관측치를 스스로 수신하지 못하면 P1이 **공허하게 통과**하기 때문이다(실패할 수 없는 검사기는 무가치).

## A3에서 나온 실제 발견 — P1 정의 정교화

`emergency_brake` 시나리오(장애물 400–1000 ms, 1000 ms에 해제)를 반복 실행하면
**3회 중 2회** P1 오탐(관측 921–981 ms, 즉 장애물 마지막 100 ms)이 발생했다.

원인: ECU 명령 주기는 100 ms이고, 마지막 구간 관측(921–981 ms)에 대한 "새 명령"은
1000 ms 틱인데 그 시점엔 도로가 이미 비어 있어 **정상적으로 brake=0을 낸다**(해제).
문자적 정의("관측 후 300 ms 내 brake>0 관측")는 이 정상 동작을 위반으로 판정한다.

조치:
1. P1을 **상태 기반**으로 정교화 — "관측 시점에 이미 제동 중"이면 충족
   (안전 의도: 근거리 표본이 제동되지 않고 방치되어서는 안 된다)
2. 하네스에 **버스 워밍업**(ECU 첫 `CMD_BRAKE` 대기, 기본 300 ms) 추가 —
   타임라인이 콜드 버스에서 시작되지 않게 해 시작 위상 의존성 제거
3. 회귀 고정: `test_p1_state_based_accepts_clearing_obstacle`(결정적),
   `test_p1_flags_unanswered_close_range`(엄격성 유지), `test_replay_emergency_brake_no_violations`(실 ECU)
4. 수정 후 반복 검증: **6회 × 4시나리오 = 24회 실행, 위반 0**

## 시나리오 목록

| 파일 | 내용 | 검증 의도 |
|---|---|---|
| `cruise.yaml` | 18 m 고정, 75 km/h | P2: 제동이 명령되지 않아야 함 |
| `approach_brake.yaml` | 18 m → 1.5 m(600 ms) → 해제(1200 ms) | P1 응답, P2 해제(마지막 명령 brake=0) |
| `emergency_brake.yaml` | 18 m → 0.6 m(400 ms) → 해제(1000 ms) | 강제동(80%) + P1 경계(오탐 회귀) |
| `sensor_stall.yaml` | 제동 중(200 cm) 600 ms에 DIST 정지 | P3 생존성 + stale 경로(age>500 ms) |

*생성: A3 (2026-10-19).*
