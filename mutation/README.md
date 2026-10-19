# mutation/ — Track A2 뮤테이션 테스팅 작업영역

계획: `../vecu-track-a.plan.md` §A2 (시드 12개 확정 → 최종 15개)

## 구성

| 항목 | 경로 | 설명 |
|---|---|---|
| 시드 카탈로그 | `seeds.yaml` | 15개 시드 (14 스코어 + 1 등가 검증) — SWC(`src/swc_brake.c`) 대상 |
| 러너 | `scripts/mutate.py` | 트리 복제 → 시드 적용 → `make` → `pytest` → 판정 → 리포트, `--jobs` 병렬 |
| 작업 트리 (임시) | `work/` | 시드 적용용 사본 — gitignore 대상, 커밋 금지 |
| 리포트 | `reports/mutation/mutation.{json,md}` | 탐지율 %, 시드별 최초 실패 테스트, 생존/등가/무효 목록 |

## 실행

```
make mutation                                  # 기본: test_logic + test_functional (권장)
make mutation MUTATE_ARGS="--jobs 4 --tests tests/test_logic.py"   # 병렬 (버스 없는 선택 시)
python3 scripts/mutate.py --list               # 시드 목록
python3 scripts/mutate.py --only brake_ramp_divisor
```

## 측정 결과 (2026-10-19, WSL2 kernel 6.18, vcan0)

```
seeds: 15   scored: 14   equivalent verified: 1   invalid: 0
detected: 14   survived: 0
mutation score: 100.0%   (gate: >= 90%)
runtime: ≈ 75 s serial (69–81 s 측정) / 22.5 s (--jobs 4, test_logic만)
```

시드별 탐지 테스트:

| 시드 | 탐지 테스트 |
|---|---|
| `brake_ramp_divisor`, `brake_plus_instead_of_minus`, `brake_threshold_margin`, `brake_clamp_ceiling` | `test_brake_threshold_boundaries` |
| `brake_stale_guard_drop` | `test_brake_stale_distance` |
| `light_threshold_flip`, `light_speed_threshold`, `light_priority_inversion` | `test_light_boundaries_and_priority` |
| `light_stale_dist_guard_drop`, `light_stale_speed_guard_drop` | `test_light_stale_inputs` |
| `pack_shifts_dropped` | `test_src_seq_wraparound` |
| `unpack_seq_mask_drop` | `test_cmd_brake_pack_unpack_roundtrip` |
| `compute_brake_uses_seq`, `compute_cmd_packs_light` | `test_compute_umbrella_matches_granular` |

## A2.4 루프에서 나온 실제 발견 2건

1. **등가 뮤턴트 (데드 코드 발견)** — `clamp_upper_equivalent`:
   1차 실행에서 `(b > 100 ? 100 : b)` → `99` 시드가 생존했다. 원인은 시드 오류가
   아니라 **등가 뮤턴트**다: 분기 진입 조건이 `dist_cm < 300`이므로
   `b = (300-dist)/3 ≤ 100`이 항상 성립 → 상한 클램프 분기는 **도달 불가(데드
   코드)**이며 어떤 테스트로도 구별할 수 없다. 조치:
   - 시드를 "상한 클램프 제거"로 바꾸고 `equivalent: true`로 표기
   - 러너가 **등가 주장을 검증**한다 (생존해야 정상, 죽으면 `unexpected_kill`로 플래그)
   - 스코어에서 제외하되 리포트에 근거와 함께 명시
2. **커버리지 갭 (우산 러너블 미테스트)** — A1에서 신설한 `swc_brake_compute()`
   는 granular API만 테스트되어 FFI 커버리지가 없었다. `test_compute_umbrella_
   matches_granular`를 추가하고, 이를 조준한 시드 2종을 추가해 **테스트가 실제로
   문다는 것을 증명**했다 (`compute_*` 시드는 해당 테스트 없이는 생존한다).

## 완료 기준 (A2.4)

- [x] `make mutation` 1커맨드로 `reports/mutation/mutation.{json,md}` 생성
- [x] 탐지율 ≥ 90% → **100.0% (14/14)** 달성
- [x] 미탐지 대응 루프 1회 수행 (등가 판정 + 테스트 추가 각 1건)

*생성: A0 (2026-10-19) — A2 착수 시 내용 채움, A2 완료 (2026-10-19).*
