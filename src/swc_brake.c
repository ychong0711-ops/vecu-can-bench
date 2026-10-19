/* vECU-Bench :: swc_brake -- SWC implementation (pure, OS-free).
 * Layering gate: includes limited to swc_brake.h / <stdint.h>.
 * SPDX-License-Identifier: MIT
 */
#include "swc_brake.h"

/* brake_level = dist < 300 cm ? clamp((300 - dist) / 3, 0..100) : 0.
 * Stale/unknown distance (have_dist == 0) yields 0. */
uint8_t swc_brake_level(int have_dist, uint16_t dist_cm)
{
    uint8_t brake = 0;
    if (have_dist && dist_cm < 300) {
        int b = (300 - (int)dist_cm) / 3;
        brake = (uint8_t)(b < 0 ? 0 : (b > 100 ? 100 : b));
    }
    return brake;
}

/* light: hazard if dist < 150 cm, else low beam if speed > 70.0 km/h */
uint8_t swc_light_state(int have_dist, uint16_t dist_cm,
                        int have_speed, uint16_t speed_01kmh)
{
    uint8_t light = SWC_LIGHT_OFF;
    if (have_dist && dist_cm < 150) light = SWC_LIGHT_HAZARD;           /* hazard  */
    else if (have_speed && speed_01kmh > 700) light = SWC_LIGHT_LOW_BEAM;
    return light;
}

/* CMD_BRAKE value = (src_seq8 << 8) | brake_level for E2E correlation. */
uint16_t swc_cmd_brake_pack(uint16_t dist_seq, uint8_t brake_level)
{
    return (uint16_t)(((dist_seq & 0xFFu) << 8) | brake_level);
}

void swc_cmd_brake_unpack(uint16_t value, uint8_t *src_seq8, uint8_t *brake_level)
{
    *src_seq8   = (uint8_t)((value >> 8) & 0xFFu);
    *brake_level = (uint8_t)(value & 0xFFu);
}

/* Umbrella runnable: same results as calling the granular API individually. */
void swc_brake_compute(const swc_inputs_t *in, swc_outputs_t *out)
{
    out->brake = swc_brake_level(in->have_dist, in->dist_cm);
    out->light  = swc_light_state(in->have_dist, in->dist_cm,
                                  in->have_speed, in->speed_01kmh);
    out->cmd_brake = swc_cmd_brake_pack(in->dist_seq, out->brake);
}
