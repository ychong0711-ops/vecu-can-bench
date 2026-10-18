/* FFI wrappers around the feature-ECU control logic (include/ecu_logic.h).
 * The logic itself lives header-only; these extern wrappers re-export it so
 * unit tests can build a shared library (gcc -shared -fPIC) and load it via
 * ctypes. Compiles standalone: needs only its own header, no other src file.
 */
#include "ecu_logic.h"

uint8_t ecu_logic_brake_level(int have_dist, uint16_t dist_cm)
{
    return feature_brake_level(have_dist, dist_cm);
}

uint8_t ecu_logic_light_state(int have_dist, uint16_t dist_cm,
                              int have_speed, uint16_t speed_01kmh)
{
    return feature_light_state(have_dist, dist_cm, have_speed, speed_01kmh);
}

uint16_t ecu_logic_cmd_brake_pack(uint16_t dist_seq, uint8_t brake_level)
{
    return feature_cmd_brake_pack(dist_seq, brake_level);
}

void ecu_logic_cmd_brake_unpack(uint16_t value, uint8_t *src_seq8,
                                uint8_t *brake_level)
{
    feature_cmd_brake_unpack(value, src_seq8, brake_level);
}
