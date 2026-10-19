# mutation/ — Track A2 뮤테이션 테스팅 작업영역

계획: `../vecu-track-a.plan.md` §A2 (시드 12개 확정)

## 향후 구성 (A2 착수 시)

| 항목 | 경로 | 설명 |
|---|---|---|
| 시드 카탈로그 | `seeds.yaml` | 12개 시드 버그 정의 (임계 off-by-one, 경계 flip, 연산자 교체, stale-data, seq wrap …) |
| 작업 트리 (임시) | `work/` | 시드 적용용 사본 — **gitignore 대상, 커밋 금지** |
| 실행 결과 | `result-<seed>.json` | 시드별 탐지/미탐지 + 최초 실패 테스트 |

런너는 `scripts/mutate.py` (A2.2) 가 여기를 오가며:
트리 복제 → 시드 1건 적용 → `make` → `pytest` → 판정 → 원복.

## 완료 기준 (A2.4)

- `make mutation` 1커맨드로 `reports/mutation/mutation.{json,md}` 생성
- 탐지율 ≥ 90% (미탐지 시드에는 테스트 추가 루프 1회 필수)

*생성: A0 (2026-10-19) — 내용은 A2 착수 시 채워집니다.*
