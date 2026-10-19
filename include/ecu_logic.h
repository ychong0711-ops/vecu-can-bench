/* vECU-Bench :: ecu_logic -- COMPAT shim (Track A1).
 * The control logic moved to the SWC layer: include/swc_brake.h + src/swc_brake.c.
 * Legacy names below keep existing call sites (tests/test_logic.py FFI,
 * older code) compiling unchanged. New code must include swc_brake.h.
 * SPDX-License-Identifier: MIT
 */
#ifndef VECU_ECU_LOGIC_H
#define VECU_ECU_LOGIC_H

#include "swc_brake.h"

/* legacy constants -> SWC constants */
#define FEATURE_LIGHT_OFF      SWC_LIGHT_OFF
#define FEATURE_LIGHT_LOW_BEAM SWC_LIGHT_LOW_BEAM
#define FEATURE_LIGHT_HAZARD   SWC_LIGHT_HAZARD

/* legacy inline names -> SWC externs (same signatures, same results) */
static inline uint8_t feature_brake_level(int have_dist, uint16_t dist_cm)
{
    return swc_brake_level(have_dist, dist_cm);
}

static inline uint8_t feature_light_state(int have_dist, uint16_t dist_cm,
                                          int have_speed, uint16_t speed_01kmh)
{
    return swc_light_state(have_dist, dist_cm, have_speed, speed_01kmh);
}

static inline uint16_t feature_cmd_brake_pack(uint16_t dist_seq, uint8_t brake_level)
{
    return swc_cmd_brake_pack(dist_seq, brake_level);
}

static inline void feature_cmd_brake_unpack(uint16_t value, uint8_t *src_seq8,
                                            uint8_t *brake_level)
{
    swc_cmd_brake_unpack(value, src_seq8, brake_level);
}

/* ctypes FFI entry points (src/ecu_logic.c -> swc_brake.c) */
uint8_t  ecu_logic_brake_level(int have_dist, uint16_t dist_cm);
uint8_t  ecu_logic_light_state(int have_dist, uint16_t dist_cm,
                               int have_speed, uint16_t speed_01kmh);
uint16_t ecu_logic_cmd_brake_pack(uint16_t dist_seq, uint8_t brake_level);
void     ecu_logic_cmd_brake_unpack(uint16_t value, uint8_t *src_seq8,
                                    uint8_t *brake_level);
void     ecu_logic_compute(const swc_inputs_t *in, swc_outputs_t *out);

#endif /* VECU_ECU_LOGIC_H */
