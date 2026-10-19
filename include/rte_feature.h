/* vECU-Bench :: rte_feature -- RTE layer of the feature ECU.
 *
 * AUTOSAR-style Runtime Environment: a plain inter-layer port struct plus
 * Rte_Read_/Rte_Write_ accessors. The driver layer (ecu_feature.c on_rx /
 * transmit tasks) writes observations & sends commands; the SWC runnable
 * reads observations and writes actuator commands. No logic, no frame I/O,
 * no OS/CAN headers (stdint only) — enforced by scripts/check_layering.py.
 * SPDX-License-Identifier: MIT
 */
#ifndef VECU_RTE_FEATURE_H
#define VECU_RTE_FEATURE_H

#include <stdint.h>

/* RTE port instance: cached sensor signals + command outputs. */
typedef struct {
    /* observable signal cache (driver rx path writes) */
    int      have_dist, have_speed;
    uint16_t dist_cm;
    uint16_t dist_seq;       /* last SENSOR_DIST seq (E2E correlation) */
    uint64_t dist_rx_ms;     /* arrival time (staleness observability) */
    uint16_t speed_01kmh;
    /* actuator command outputs (SWC runnable writes, driver task sends) */
    uint8_t  cmd_brake;      /* brake level 0..100 */
    uint8_t  cmd_light;      /* light state */
    uint16_t cmd_brake_value;/* wire value incl. src_seq8 */
} rte_feature_t;

/* ---- write side: observations in (called from the CAN rx callback) ---- */
static inline void Rte_Write_SensorDist(rte_feature_t *r, uint16_t dist_cm,
                                        uint16_t seq, uint64_t rx_ms)
{
    r->dist_cm = dist_cm;
    r->dist_seq = seq;
    r->dist_rx_ms = rx_ms;
    r->have_dist = 1;
}

static inline void Rte_Write_SensorSpeed(rte_feature_t *r, uint16_t speed_01kmh)
{
    r->speed_01kmh = speed_01kmh;
    r->have_speed = 1;
}

/* ---- read side: observations out (called from the SWC runnable) ----
 * Returns 1 when the signal has been observed at least once, else 0 and
 * the out-parameters are cleared to their zero/unknown state. */
static inline int Rte_Read_SensorDist(const rte_feature_t *r, uint16_t *dist_cm,
                                      uint16_t *seq, uint64_t *rx_ms)
{
    *dist_cm = r->dist_cm;
    *seq     = r->dist_seq;
    *rx_ms   = r->dist_rx_ms;
    return r->have_dist;
}

static inline int Rte_Read_SensorSpeed(const rte_feature_t *r, uint16_t *speed_01kmh)
{
    *speed_01kmh = r->speed_01kmh;
    return r->have_speed;
}

/* ---- write side: commands out (called from the SWC runnable) ---- */
static inline void Rte_Write_CMD_BRAKE(rte_feature_t *r, uint8_t level,
                                       uint16_t wire_value)
{
    r->cmd_brake = level;
    r->cmd_brake_value = wire_value;
}

static inline void Rte_Write_CMD_LIGHT(rte_feature_t *r, uint8_t state)
{
    r->cmd_light = state;
}

/* ---- read side: commands in (called from the driver transmit path) ---- */
static inline uint8_t Rte_Read_CMD_BRAKE(const rte_feature_t *r, uint16_t *wire_value)
{
    if (wire_value) *wire_value = r->cmd_brake_value;
    return r->cmd_brake;
}

static inline uint8_t Rte_Read_CMD_LIGHT(const rte_feature_t *r)
{
    return r->cmd_light;
}

#endif /* VECU_RTE_FEATURE_H */
