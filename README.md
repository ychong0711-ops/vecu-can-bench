# vECU-Bench

**A virtual ECU network testbed on Linux SocketCAN for measuring CAN latency,
frame loss and bus load — with automated regression testing.**

Built as a hands-on portfolio project for automotive embedded software:
three cooperating virtual ECUs (sensor → control logic → actuator, modeled
after a classic AUTOSAR demo vehicle), a passive measurement node, a
deterministic bus-load injector, `tc/netem`-based fault injection, a pytest
regression suite (functional + performance thresholds) running in CI, and a
Python analysis stage producing percentile statistics, plots and reports.

```
                    vcan0  (virtual CAN bus, modeled @ 500 kbit/s)
   ┌───────────┬────────────┬───────────────┬────────────┬───────────┐
   │ ecu_sensor│ ecu_feature│ ecu_assistant │  injector  │  monitor  │
   │ 20/50/100 │  brake &   │ actuators +   │ deterministic│ passive │
   │ ms periodic│ light ctrl│ PING→PONG echo│ load gen   │ recorder  │
   └───────────┴────────────┴───────────────┴────────────┴───────────┘
        │  SENSOR_* 0x100-0x102   │  CMD_* 0x200/0x201        │ CSV+JSON
        └────────────────────────┴───────────────────────────►│ analysis/
                     tc/netem: delay · jitter · loss          ▼
                                              reports/ (stats, plots, report.md)
```

## What is measured

| Metric | Method |
|---|---|
| One-way latency | every frame embeds `[seq u16][tx_ms u32][value u16]`; monitor computes `rx_ms − tx_ms` (same `CLOCK_MONOTONIC` host) and reports p50/p95/p99/max per CAN ID |
| Frame loss | 16-bit sequence-gap counting per ID (wrap-safe, reorder-tolerant) |
| Round-trip time | monitor sends `PING 0x500`, assistant echoes `PONG 0x501` verbatim |
| Bus load | nominal frame bits `47 + 8·dlc` vs 500 kbit/s, 1-s windows (avg + max); worst-case stuffing bits also accumulated |
| End-to-end reaction | `SENSOR_DIST` seq (low byte) is carried in `CMD_BRAKE.SrcSeq` → sensor-sample-to-brake-command chain latency incl. the 100 ms control cycle |
| Scheduling jitter | each node tracks `max_late` per periodic task (deadline vs. actual start) |

## Quickstart

```bash
# requires: Linux (native/VM) with the vcan module, gcc, make, python3
sudo modprobe vcan
./scripts/setup_vcan.sh          # creates vcan0
make                             # builds bin/{ecu_sensor,ecu_feature,ecu_assistant,monitor,injector}

# AddressSanitizer + UBSan build (separate output dir: bin-asan/)
make asan

# Static analysis (requires cppcheck: sudo apt-get install cppcheck)
make lint

# Regenerate & verify ID map from DBC (single source of truth)
python3 scripts/gen_idmap.py
python3 scripts/gen_idmap.py --check

# 20 s full-stack demo with 20 % background load + report generation
./scripts/run_demo.sh            # -> reports/demo/{frames.csv,summary.json,analysis.json,report.md,*.png}

# full experiment matrix (baseline / load 20,50 % / netem delay,loss / combo)
./scripts/run_matrix.sh          # -> reports/<scenario>/... + reports/matrix/matrix.md

# regression suite (functional logic + performance thresholds on live vcan)
pip install -r requirements.txt   # pytest, pandas, numpy, matplotlib, tabulate, cantools
make test
```

> **⚠️ WSL2 Limitation: Integration Tests Require `vcan`**
>
> **Stock WSL2 kernels do not include the `vcan` kernel module.** The integration test suite (`make test`) and demo scripts (`run_demo.sh`, `run_matrix.sh`) **only run in GitHub Actions CI** (see `.github/workflows/ci.yml`), where `vcan` is available.
>
> **To run locally, use one of:**
> - **Native Linux** (recommended)
> - **Virtual Machine** — VirtualBox, VMware, or similar with a standard Linux distro
> - **WSL2 with a custom kernel** that enables `CONFIG_VCAN=m` (see [WSL kernel build guide](https://learn.microsoft.com/en-us/windows/wsl/use-custom-kernel))

## Repository layout

```
include/common.h     IDs, measured-frame layout, bit formulas, node API
src/can_util.c       SocketCAN open/bind/send, payload packing
src/sched.c          cooperative periodic-task loop (select + deadlines)
src/ecu_sensor.c     simulated speed/distance/temperature publisher
src/ecu_feature.c    brake & light control logic (dist<300 cm → brake)
src/ecu_assistant.c  actuator simulation + PING/PONG echo
src/monitor.c        passive recorder: CSV, per-ID loss/latency, bus load, JSON summary
src/injector.c       target-% bus-load generator (absolute-deadline pacing)
dbc/vecu.dbc         signal database (cantools-compatible, validated in CI)
scripts/             vcan setup/teardown, netem presets, scenario & matrix runners
tests/               pytest: DBC consistency, control logic, echo, loss/RTT/load thresholds
analysis/analyze.py  CSV → percentile stats, latency CDF, bus-load series, E2E join, matrix compare
```

## Test matrix (CI-enforced)

| Test | Asserts |
|---|---|
| `test_dbc_matches_id_map` | DBC parses (cantools) and mirrors `common.h` |
| `test_feature_brake_logic` | dist=100 cm → brake>50 %; dist=1800 cm → brake released |
| `test_assistant_pong_echo` | PONG preserves seq + tx timestamp |
| `test_baseline_zero_loss` | zero frames lost on idle bus (0x100/0x101/0x102/0x200/0x201) |
| `test_ping_rtt_low` | RTT p99 < 10 ms |
| `test_busload_accuracy` | injector @25 % measures within 21–29 % |
| `test_latency_under_load` | @40 % load: SENSOR_DIST p99 < 20 ms, loss < 0.5 % |
| `test_netem_delay_visible` | netem 10 ms±2 ms shows up as RTT p50 ≥ 15 ms |

## Measurement methodology & limitations

* vcan does **not** simulate bit timing, arbitration or error frames — bus load
  is computed against a nominal 500 kbit/s reference, and latency figures
  reflect kernel queueing/scheduling, not wire propagation. This is a
  *functional + system-level* testbed; for bit-accurate behavior, pair it with
  hardware (e.g. PCAN/SocketCAN adapter) using the same tooling.
* All nodes share one host clock (`CLOCK_MONOTONIC`), so one-way latency from
  the embedded `tx_ms` is exact (no clock-sync error) — but only valid
  single-host. The PING/PONG RTT path additionally validates the monitor.
* `tc/netem` on **vcan** shapes only the real frame behind the socket-layer
  loopback clone that receivers actually see — so delay/loss presets are a
  **no-op for same-host delivery** (verified: `netem loss 100%` still delivered
  every frame; `delay 500ms` measured 0.05 ms). The scenario matrix's
  `delay*`/`loss*` cells therefore show no delta on vcan; netem becomes
  meaningful when swapping in a real adapter transmitting across hosts
  (`tests/test_performance.py::test_netem_delay_visible` probes this and
  skips with the reason when ineffective).

## Extension tracks

Three deepening tracks are documented in `docs/ARCHITECTURE.md` §7:
**(a)** ECU-verification emphasis (layered SWC/RTE-like architecture, mutation
testing, HIL-style replay), **(b)** real-time/systems analysis (formal timing
properties, RM utilization vs. measured response times, trace anomaly
detection), **(c)** timing-analysis emphasis (WCRT estimation, jitter
decomposition, worst-case scenario search).

## License

MIT
