# Mutation report — Track A2

> CI artifact copy (latest `ci.yml` run). Regenerate with `make mutation`.

- seeds: **15**  (scored: 14, equivalent verified: 1, invalid: 0)
- detected: **14**  survived: **0**
- **mutation score: 100.0%** (gate: >= 90.0%)
- test selection: `tests/test_logic.py tests/test_functional.py`

| seed | status | detecting test | note |
|---|---|---|---|
| `brake_ramp_divisor` | detected | tests/test_logic.py::test_brake_threshold_boundaries | ramp slope /3 -> /2 (weaker braking) |
| `brake_plus_instead_of_minus` | detected | tests/test_logic.py::test_brake_threshold_boundaries | ramp offset 300-dist -> 300+dist (inverted ramp) |
| `brake_threshold_margin` | detected | tests/test_logic.py::test_brake_threshold_boundaries | brake engage range <300 -> <250 cm (late braking) |
| `brake_clamp_ceiling` | detected | tests/test_logic.py::test_brake_threshold_boundaries | clamp ceiling 100 -> 90 % (max braking foreshortened) |
| `clamp_upper_equivalent` | equivalent |  | upper clamp removed entirely (b <= 100 always) — EQUIVALENT mutant |
| `brake_stale_guard_drop` | detected | tests/test_logic.py::test_brake_stale_distance | have_dist guard dropped (stale distance commands braking) |
| `light_threshold_flip` | detected | tests/test_logic.py::test_light_boundaries_and_priority | hazard boundary <150 -> <=150 cm (off-by-one flip) |
| `light_speed_threshold` | detected | tests/test_logic.py::test_light_boundaries_and_priority | low-beam threshold 70.0 -> 60.0 km/h |
| `light_priority_inversion` | detected | tests/test_logic.py::test_light_boundaries_and_priority | hazard/low-beam priority (else-if) turned into overwrite (if) |
| `light_stale_dist_guard_drop` | detected | tests/test_logic.py::test_light_stale_inputs | have_dist guard dropped in hazard branch |
| `light_stale_speed_guard_drop` | detected | tests/test_logic.py::test_light_stale_inputs | have_speed guard dropped in low-beam branch |
| `pack_shifts_dropped` | detected | tests/test_logic.py::test_src_seq_wraparound | src_seq8 shift removed from CMD_BRAKE wire value |
| `unpack_seq_mask_drop` | detected | tests/test_logic.py::test_cmd_brake_pack_unpack_roundtrip | unpack reads src_seq8 from the low byte (E2E join breaks) |
| `compute_brake_uses_seq` | detected | tests/test_logic.py::test_compute_umbrella_matches_granular | runnable feeds dist_seq into the ramp instead of dist_cm |
| `compute_cmd_packs_light` | detected | tests/test_logic.py::test_compute_umbrella_matches_granular | runnable packs the light state into the CMD_BRAKE wire value |

## Equivalent mutants (verified survivors, excluded)

- `clamp_upper_equivalent` — upper clamp removed entirely (b <= 100 always) — EQUIVALENT mutant
