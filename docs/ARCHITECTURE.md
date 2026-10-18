# vECU-Bench — Architecture & Design Notes

## 1. Design goals

1. A **real running ECU network** (3 cooperating nodes) instead of a mock-up,
   so every claim in a report is backed by measured data.
2. **Industry-shaped artifacts**: DBC signal database, periodic task model,
   seq/timestamp framing, regression thresholds in CI — the vocabulary ECU
   teams use.
3. **Reproducible measurements**: one command re-runs any scenario and
   regenerates statistics + plots.
4. **Honest scope**: vcan limitations are documented, not hidden (§6).

## 2. Node model

Every node is a single-threaded C program: a `select()` loop wakes on
(a) incoming CAN frames and (b) the earliest periodic-task deadline.
Tasks run cooperatively; each task records its worst lateness
(`max_late`) — the scheduling-jitter metric.

| Node | Role | Periodic tasks | Reactive |
|---|---|---|---|
| `ecu_sensor` | publish simulated vehicle state | speed 20 ms, dist 50 ms, temp 100 ms, hb 1 s | — |
| `ecu_feature` | control logic | cmd 100 ms, hb 1 s | caches SENSOR_* on rx |
| `ecu_assistant` | actuators + probe target | hb 1 s, status 2 s | CMD_* → actuator state; PING → PONG |
| `monitor` | measurement | optional PING every N ms | records **all** frames |
| `injector` | load generation | 1 ms pacing tick | — |

Control law (demo): `brake = clamp((300 − dist_cm)/3, 0..100)` when
`dist < 300 cm`; `light = hazard` if `dist < 150`, `low beam` if `speed > 70 km/h`.

## 3. Measured-frame layout (classic CAN, dlc = 8)

```
byte:  0    1    2    3    4    5    6    7
      [ seq u16  ] [ tx_ms u32 (CLOCK_MONOTONIC & 0xFFFFFFFF) ] [ value u16 ]
```

* `seq` — per-ID counter → loss via wrap-safe gap counting
  (`d = (seq − last − 1) mod 2^16`; counted only when `0 < d < 2^15`,
  so late duplicates are ignored, not misread as loss).
* `tx_ms` — sender timestamp; monitor's one-way latency is
  `(rx_ms − tx_ms) mod 2^32`, exact because both come from the same host clock.
  Samples ≥ 60 s are discarded as implausible (`lat_dropped`).
* `value` — application signal per DBC. `CMD_BRAKE` packs
  `(SrcSeq8 << 8) | BrakeLevel`, where `SrcSeq8` is the low byte of the
  triggering `SENSOR_DIST` seq → enables the **end-to-end reaction-time**
  join in analysis (sensor sample → brake command on the bus).

## 4. Bus-load accounting

Nominal bits of a CAN 2.0A data frame: `47 + 8·dlc`
(SOF+ID+ctrl+CRC+ACK+EOF+IFS = 47). Worst-case bit stuffing adds
`floor((34 + 8·dlc − 1)/4)`; the monitor accumulates both.
`load% = bits / (500 kbit/s × window)`. The injector inverts the formula to
hit a target load: `fps = pct/100 × 500000 / (47 + 8·dlc)`, paced on absolute
1 ms deadlines with fractional-remainder carry (long-run accurate).

## 5. Data flow

```
nodes ──(vcan0)──► monitor ──► frames.csv (rx_us,id,dlc,seq,tx_ms,lat_ms,val)
                        └────► summary.json (machine-readable, used by pytest)
frames.csv ──► analysis/analyze.py ──► analysis.json, report.md,
                                       latency_cdf.png, bus_load.png, e2e_reaction.png
run_matrix.sh ──► reports/<scenario>/… ──► analyze.py --matrix ──► matrix.{csv,md}, matrix_rtt.png
```

## 6. Validity & limitations (read before quoting numbers)

* **vcan is a kernel software bus**: no bit timing, no arbitration, no error
  frames, no wire propagation. Absolute latency numbers are *system-level*
  (scheduling + queueing) figures, not wire figures. Rankings and regressions
  remain meaningful; for bit-accurate work swap vcan for a real adapter
  (PCAN/PEAK, Kvaser, SocketCAN-compatible) — no code changes needed.
* One-way latency relies on a **shared host clock** — valid single-host only.
  The PING/PONG RTT path is the clock-independent cross-check.
* `tc/netem` shapes the qdisc realistically at OS level; `loss` there is
  *interface drop*, complementing (not replacing) seq-gap loss accounting.
* Receive buffers are enlarged (4 MB, `SO_RCVBUFFORCE` when permitted) and
  `txqueuelen` raised by `setup_vcan.sh` to avoid measurement artifacts under
  high injected load; ENOBUFS on tx is counted by the injector, not hidden.

## 7. Extension tracks (pick one to deepen)

### (a) ECU-verification track  *(automotive SW engineering flavor)*
* Refactor `ecu_feature` into layers: SWC (pure logic, unit-testable) /
  RTE-like interface structs / CAN driver — mirrors AUTOSAR layering.
* **Mutation testing**: script that injects N seeded bugs (threshold off-by-one,
  stale-data use, seq wrap bug…) into a copy of the tree; measure the test
  suite's detection rate → quantitative verification claim.
* HIL-style **scenario replay**: record `frames.csv`, replay scripted sensor
  timelines via a Python node, assert actuator responses (property:
  "brake > 0 within 300 ms of dist < 300").

### (b) Real-time / systems-analysis track  *(CPS/verification flavor)*
* Write the **timing contract** as a table (period, deadline, WCET estimate)
  and check measured response times + `max_late` jitter against it.
* Rate-monotonic analysis: compute utilization `U = Σ Ci/Ti`, compare the
  Liu–Layland bound with measured schedulability under injected load.
* Trace **anomaly detection**: statistical baseline (mean/σ of inter-arrival
  per ID) → deviation alarms → simple runtime-verification properties
  ("CMD_BRAKE must follow SENSOR_DIST < 300 cm within 2 cycles").

### (c) Timing-analysis track  *(WCRT/OS flavor)*
* Decompose RTT into components (node wakeup, handler, qdisc, monitor) via
  extra timestamps; publish a jitter budget.
* **Worst-case scenario search**: sweep `(load %, netem delay, loss)` grid,
  record p99.9/max RTT per cell → heat map of worst-case behavior
  (measurement-based whole-system timing analysis, in the spirit of
  fuzzing-based approaches like FRET [RTSS '25]).
* Compare measurement-based WCRT with a compositional estimate
  (per-stage budgets summed) and discuss the gap.

## 8. References

* SocketCAN: Linux kernel `Documentation/networking/can.rst`; `man 7 netdevice`
* CSS Electronics, *CAN bus explained* (frame format, bit stuffing)
* cantools (DBC parsing), pytest, pandas/matplotlib
* Liu & Layland, *Scheduling Algorithms for Multiprogramming in a Hard-Real-Time
  Environment* (1973) — RM bound
* Berger et al., *Dynamic Fuzzing-Based Whole-System Timing Analysis*, IEEE RTSS 2025
