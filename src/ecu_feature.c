/* vECU-Bench :: ecu_feature -- "FeatureECU"-like node.
 * Consumes SENSOR_SPEED/SENSOR_DIST and runs the demo control logic:
 *   brake_level = dist < 300 cm ? clamp((300-dist)/3, 0..100) : 0
 *   light_state = dist < 150 cm ? 2 (hazard) : speed > 70 km/h ? 1 (low beam) : 0
 * Publishes 0x200 CMD_BRAKE / 0x201 CMD_LIGHT every 100 ms.
 * CMD_BRAKE value = (src_seq8 << 8) | brake_level, where src_seq8 is the low
 * byte of the last consumed SENSOR_DIST seq -> enables end-to-end correlation
 * (sensor sample -> actuator command) in the analysis step.
 */
#include "common.h"
#include "ecu_logic.h"

typedef struct {
    uint16_t seq_brake, seq_light, seq_hb;
    uint16_t speed;        /* 0.1 km/h */
    uint16_t dist_cm;
    uint16_t dist_seq;     /* last sensor seq (for E2E join) */
    uint64_t dist_rx_ms;   /* arrival time of last dist sample */
    int      have_dist, have_speed;
    uint8_t  brake, light;
    uint64_t tx_fail_brake, tx_fail_light, tx_fail_hb;
} feature_state_t;

static void on_rx(node_t *n, const struct can_frame *f)
{
    feature_state_t *s = n->state;
    uint16_t seq, value; uint32_t tx;
    if (meas_unpack(f, &seq, &tx, &value) < 0) return;
    switch (f->can_id) {
    case CAN_ID_SENSOR_SPEED:
        s->speed = value; s->have_speed = 1; break;
    case CAN_ID_SENSOR_DIST:
        s->dist_cm = value; s->dist_seq = seq;
        s->dist_rx_ms = now_ms(); s->have_dist = 1; break;
    default: break;
    }
    (void)n;
}

/* TX failure accounting: can_send() failures are invisible in non-verbose
 * mode, so count them per publish path and report at shutdown; the first
 * brake-path failure additionally warns immediately (seen-once). */

static void task_cmd(node_t *n)
{
    feature_state_t *s = n->state;
    /* --- control logic (pure, unit-tested: ecu_logic.h / tests/test_logic.py) --- */
    uint8_t brake = feature_brake_level(s->have_dist, s->dist_cm);
    uint8_t light = feature_light_state(s->have_dist, s->dist_cm,
                                        s->have_speed, s->speed);
    s->brake = brake; s->light = light;

    uint64_t age = s->have_dist ? (now_ms() - s->dist_rx_ms) : 0;
    if (age > 500) LOGN("sensor data stale (%llu ms)", (unsigned long long)age);

    struct can_frame f;
    meas_pack(&f, CAN_ID_CMD_BRAKE, s->seq_brake++,
              feature_cmd_brake_pack(s->dist_seq, brake));
    if (can_send(n->fd, f.can_id, f.data, f.can_dlc) < 0) {
        if (s->tx_fail_brake++ == 0)
            warn_once("can_send(CMD_BRAKE) failing - brake commands dropped");
        LOGN("can_send brake failed");
    }

    meas_pack(&f, CAN_ID_CMD_LIGHT, s->seq_light++, light);
    if (can_send(n->fd, f.can_id, f.data, f.can_dlc) < 0) {
        s->tx_fail_light++;
        LOGN("can_send light failed");
    }
}

static void task_hb(node_t *n)
{
    feature_state_t *s = n->state;
    struct can_frame f;
    meas_pack(&f, CAN_ID_HB_FEATURE, s->seq_hb++, 0);
    if (can_send(n->fd, f.can_id, f.data, f.can_dlc) < 0) {
        s->tx_fail_hb++;
        LOGN("can_send hb failed");
    }
    if (g_verbose)
        fprintf(stderr, "[feature] speed=%.1f km/h dist=%u cm brake=%u light=%u\n",
                s->speed / 10.0, s->dist_cm, s->brake, s->light);
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
