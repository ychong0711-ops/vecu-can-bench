/* vECU-Bench :: ecu_feature -- "FeatureECU"-like node (composition layer).
 *
 * AUTOSAR-style split (Track A1, see ../vecu-track-a.plan.md):
 *   ecu_feature.c  driver/composition: CAN rx -> Rte_Write, runnable
 *                  dispatch (100 ms), Rte_Read -> transmit. Frame I/O only.
 *   rte_feature.h  RTE port + Rte_Read_/Rte_Write_ accessors.
 *   swc_brake.h/.c SWC: pure control logic (no OS/CAN headers; gated).
 *
 * Behavior (bit-identical to baseline 24cbad2):
 *   brake_level = dist < 300 cm ? clamp((300-dist)/3, 0..100) : 0
 *   light_state = dist < 150 cm ? 2 (hazard) : speed > 70 km/h ? 1 (low beam) : 0
 * Publishes 0x200 CMD_BRAKE / 0x201 CMD_LIGHT every 100 ms; the CMD_BRAKE
 * value embeds the last SENSOR_DIST seq byte for E2E correlation.
 */
#include "common.h"
#include "rte_feature.h"
#include "swc_brake.h"

typedef struct {
    rte_feature_t rte;       /* RTE port: signal cache + command outputs */
    /* driver-layer frame sequencing + TX failure accounting */
    uint16_t seq_brake, seq_light, seq_hb;
    uint64_t tx_fail_brake, tx_fail_light, tx_fail_hb;
} feature_state_t;

static void on_rx(node_t *n, const struct can_frame *f)
{
    feature_state_t *s = n->state;
    uint16_t seq, value; uint32_t tx;
    if (meas_unpack(f, &seq, &tx, &value) < 0) return;
    switch (f->can_id) {
    case CAN_ID_SENSOR_SPEED:
        Rte_Write_SensorSpeed(&s->rte, value);
        break;
    case CAN_ID_SENSOR_DIST:
        Rte_Write_SensorDist(&s->rte, value, seq, now_ms());
        break;
    default: break;
    }
    (void)n;
}

/* TX failure accounting: can_send() failures are invisible in non-verbose
 * mode, so count them per publish path and report at shutdown; the first
 * brake-path failure additionally warns immediately (seen-once). */

/* 100 ms runnable: RTE read -> SWC -> RTE write -> driver transmit. */
static void task_cmd(node_t *n)
{
    feature_state_t *s = n->state;

    swc_inputs_t in;
    swc_outputs_t out;
    uint64_t dist_rx_ms = 0;
    in.have_dist   = Rte_Read_SensorDist(&s->rte, &in.dist_cm,
                                         &in.dist_seq, &dist_rx_ms);
    in.have_speed  = Rte_Read_SensorSpeed(&s->rte, &in.speed_01kmh);
    swc_brake_compute(&in, &out);
    Rte_Write_CMD_BRAKE(&s->rte, out.brake, out.cmd_brake);
    Rte_Write_CMD_LIGHT(&s->rte, out.light);

    uint64_t age = in.have_dist ? (now_ms() - dist_rx_ms) : 0;
    if (age > 500) LOGN("sensor data stale (%llu ms)", (unsigned long long)age);

    /* driver: transmit the RTE command outputs */
    struct can_frame f;
    meas_pack(&f, CAN_ID_CMD_BRAKE, s->seq_brake++, out.cmd_brake);
    if (can_send(n->fd, f.can_id, f.data, f.can_dlc) < 0) {
        if (s->tx_fail_brake == 0)
            warn_once("can_send(CMD_BRAKE) failing - brake commands dropped");
        SATURATING_INC(&s->tx_fail_brake);
        LOGN("can_send brake failed");
    }

    meas_pack(&f, CAN_ID_CMD_LIGHT, s->seq_light++, out.light);
    if (can_send(n->fd, f.can_id, f.data, f.can_dlc) < 0) {
        SATURATING_INC(&s->tx_fail_light);
        LOGN("can_send light failed");
    }
}

static void task_hb(node_t *n)
{
    feature_state_t *s = n->state;
    struct can_frame f;
    meas_pack(&f, CAN_ID_HB_FEATURE, s->seq_hb++, 0);
    if (can_send(n->fd, f.can_id, f.data, f.can_dlc) < 0) {
        SATURATING_INC(&s->tx_fail_hb);
        LOGN("can_send hb failed");
    }
    if (g_verbose)
        fprintf(stderr, "[feature] speed=%.1f km/h dist=%u cm brake=%u light=%u\n",
                s->rte.speed_01kmh / 10.0, s->rte.dist_cm,
                Rte_Read_CMD_BRAKE(&s->rte, NULL), Rte_Read_CMD_LIGHT(&s->rte));
}

int main(int argc, char **argv)
{
    g_progname = "ecu_feature";
    const char *iface = "vcan0";
    uint32_t duration = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--iface") && i + 1 < argc)        iface = argv[++i];
        else if (!strcmp(argv[i], "--duration") && i + 1 < argc) duration = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--verbose"))                  g_verbose = 1;
        else { fprintf(stderr, "usage: %s [--iface vcan0] [--duration s] [--verbose]\n", g_progname); return 2; }
    }
    install_signal_handlers();

    feature_state_t s; memset(&s, 0, sizeof(s));
    node_t n = { .fd = can_open(iface), .name = "FEATURE", .node_id = 2,
                 .on_rx = on_rx, .state = &s };
    if (n.fd < 0) return 1;

    node_add_task(&n, "cmd", 100,  task_cmd);
    node_add_task(&n, "hb",  1000, task_hb);

    fprintf(stderr, "[%s] up on %s (duration=%us)\n", g_progname, iface, duration);
    int rc = node_run(&n, duration);
    node_print_stats(&n);
    if (s.tx_fail_brake || s.tx_fail_light || s.tx_fail_hb)
        fprintf(stderr, "[%s] TX failures: brake=%llu light=%llu hb=%llu\n",
                g_progname, (unsigned long long)s.tx_fail_brake,
                (unsigned long long)s.tx_fail_light, (unsigned long long)s.tx_fail_hb);
    close(n.fd);
    return rc;
}
