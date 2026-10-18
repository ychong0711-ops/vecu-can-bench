/* vECU-Bench :: ecu_logic -- pure control logic of the feature ECU.
 * SWC-style extraction from ecu_feature.c: no OS/CAN dependencies (stdint
 * only), so it is unit-testable in isolation (tests/test_logic.py).
 * Implemented header-only (static inline) so node binaries keep linking
 * without extra objects in the Makefile; src/ecu_logic.c re-exports the
 * same functions for shared-library (ctypes) unit tests.
 * Behavior is bit-identical to the original inline code in ecu_feature.c.
 * SPDX-License-Identifier: MIT
 */
#ifndef VECU_ECU_LOGIC_H
#define VECU_ECU_LOGIC_H

#include <stdint.h>

/* CMD_LIGHT payload values */
#define FEATURE_LIGHT_OFF      0
#define FEATURE_LIGHT_LOW_BEAM 1
#define FEATURE_LIGHT_HAZARD   2

/* brake_level = dist < 300 cm ? clamp((300 - dist) / 3, 0..100) : 0.
 * Stale/unknown distance (have_dist == 0) yields 0. */
static inline uint8_t feature_brake_level(int have_dist, uint16_t dist_cm)
{
    uint8_t brake = 0;
    if (have_dist && dist_cm < 300) {
        int b = (300 - (int)dist_cm) / 3;
        brake = (uint8_t)(b < 0 ? 0 : (b > 100 ? 100 : b));
    }
    return brake;
}

/* light: hazard if dist < 150 cm, else low beam if speed > 70.0 km/h */
static inline uint8_t feature_light_state(int have_dist, uint16_t dist_cm,
                                          int have_speed, uint16_t speed_01kmh)
{
    uint8_t light = FEATURE_LIGHT_OFF;
    if (have_dist && dist_cm < 150) light = FEATURE_LIGHT_HAZARD;      /* hazard  */
    else if (have_speed && speed_01kmh > 700) light = FEATURE_LIGHT_LOW_BEAM;
    return light;
}

/* CMD_BRAKE value = (src_seq8 << 8) | brake_level, where src_seq8 is the low
 * byte of the last consumed SENSOR_DIST seq -> enables end-to-end correlation
 * (sensor sample -> actuator command) in the analysis step. Wrap-safe: the
 * u16 seq wraps 65535->0 and the carried low byte wraps 255->0 by masking. */
static inline uint16_t feature_cmd_brake_pack(uint16_t dist_seq,
                                              uint8_t brake_level)
{
    return (uint16_t)(((uint16_t)(dist_seq & 0xFF) << 8) | brake_level);
}

static inline void feature_cmd_brake_unpack(uint16_t value, uint8_t *src_seq8,
                                            uint8_t *brake_level)
{
    if (src_seq8)    *src_seq8    = (uint8_t)(value >> 8);
    if (brake_level) *brake_level = (uint8_t)(value & 0xFF);
}

#endif /* VECU_ECU_LOGIC_H */
