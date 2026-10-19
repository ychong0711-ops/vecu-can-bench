/* vECU-Bench :: swc_brake -- AUTOSAR-style SWC layer of the feature ECU.
 *
 * Pure control logic: NO OS / CAN / RTE dependencies (stdint + own header
 * only) — enforced by scripts/check_layering.py hooked into `make lint`.
 * The runnable-style umbrella is swc_brake_compute(): inputs arrive as plain
 * data (filled by the RTE from CAN observations), outputs go back the same
 * way; the driver layer in ecu_feature.c does all frame I/O.
 *
 * Behavior is bit-identical to the previous ecu_logic.h inline code
 * (baseline commit 24cbad2); the legacy names remain as compat shims.
 * SPDX-License-Identifier: MIT
 */
#ifndef VECU_SWC_BRAKE_H
#define VECU_SWC_BRAKE_H

#include <stdint.h>

/* CMD_LIGHT payload values */
#define SWC_LIGHT_OFF      0
#define SWC_LIGHT_LOW_BEAM 1
#define SWC_LIGHT_HAZARD   2

/* SWC input port (RTE-filled observations) */
typedef struct {
    int      have_dist;     /* dist observed at least once */
    uint16_t dist_cm;       /* raw DBC value (1 cm) */
    uint16_t dist_seq;      /* SENSOR_DIST seq — E2E correlation source */
    int      have_speed;    /* speed observed at least once */
    uint16_t speed_01kmh;   /* raw DBC value (0.1 km/h) */
} swc_inputs_t;

/* SWC output port (consumed by the RTE -> driver transmit path) */
typedef struct {
    uint8_t  brake;         /* brake level 0..100 % */
    uint8_t  light;         /* SWC_LIGHT_* state */
    uint16_t cmd_brake;     /* wire value: (src_seq8 << 8) | brake */
} swc_outputs_t;

/* Periodic runnable (100 ms): compute every actuator command at once. */
void swc_brake_compute(const swc_inputs_t *in, swc_outputs_t *out);

/* Granular API (same signatures as the legacy feature_* helpers). */
uint8_t  swc_brake_level(int have_dist, uint16_t dist_cm);
uint8_t  swc_light_state(int have_dist, uint16_t dist_cm,
                         int have_speed, uint16_t speed_01kmh);
uint16_t swc_cmd_brake_pack(uint16_t dist_seq, uint8_t brake_level);
void     swc_cmd_brake_unpack(uint16_t value, uint8_t *src_seq8,
                              uint8_t *brake_level);

#endif /* VECU_SWC_BRAKE_H */
