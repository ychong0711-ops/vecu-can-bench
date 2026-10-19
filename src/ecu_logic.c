/* vECU-Bench :: ecu_logic.c -- ctypes FFI wrappers (Track A1).
 * Thin delegation to the SWC layer; symbols unchanged so
 * tests/test_logic.py keeps its fixture and call sites.
 * Link with src/swc_brake.c (the test fixture compiles both).
 * SPDX-License-Identifier: MIT
 */
#include "ecu_logic.h"

uint8_t ecu_logic_brake_level(int have_dist, uint16_t dist_cm)
{
    return swc_brake_level(have_dist, dist_cm);
}

uint8_t ecu_logic_light_state(int have_dist, uint16_t dist_cm,
                              int have_speed, uint16_t speed_01kmh)
{
    return swc_light_state(have_dist, dist_cm, have_speed, speed_01kmh);
}

uint16_t ecu_logic_cmd_brake_pack(uint16_t dist_seq, uint8_t brake_level)
{
    return swc_cmd_brake_pack(dist_seq, brake_level);
}

void ecu_logic_cmd_brake_unpack(uint16_t value, uint8_t *src_seq8,
                                uint8_t *brake_level)
{
    swc_cmd_brake_unpack(value, src_seq8, brake_level);
}
