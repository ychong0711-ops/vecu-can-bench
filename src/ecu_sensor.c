/* vECU-Bench :: ecu_sensor -- "ProcessingECU"-like node.
 * Simulates vehicle sensors and publishes periodic CAN frames:
 *   0x100 SENSOR_SPEED 20 ms (0.1 km/h)   sine sweep 20..80 km/h
 *   0x101 SENSOR_DIST  50 ms (cm)         sawtooth 2000..50 (approaching)
 *   0x102 SENSOR_TEMP 100 ms (0.1 degC)   ~80 degC + noise
 *   0x300 HEARTBEAT    1 s   (value = uptime seconds)
 * Every frame carries [seq u16][tx_ms u32][value u16] (see include/common.h).
 */
#include "common.h"

typedef struct {
    uint64_t t0;
    uint16_t seq_speed, seq_dist, seq_temp, seq_hb;
    unsigned rand_state;
} sensor_state_t;

static void tx_meas(node_t *n, canid_t id, uint16_t *seq, uint16_t value)
{
    struct can_frame f;
    meas_pack(&f, id, (*seq)++, value);
    if (can_send(n->fd, f.can_id, f.data, f.can_dlc) < 0)
        LOGN("tx 0x%03X failed", id);
}

static void task_speed(node_t *n)
{
    sensor_state_t *s = n->state;
    double t = (double)(now_ms() - s->t0) / 1000.0;
    double kph = 50.0 + 30.0 * sin(2.0 * M_PI * t / 20.0);
    tx_meas(n, CAN_ID_SENSOR_SPEED, &s->seq_speed, (uint16_t)(kph * 10.0));
}

static void task_dist(node_t *n)
{
    sensor_state_t *s = n->state;
    uint64_t el = (now_ms() - s->t0) % 10000;      /* 10 s approach cycle */
    uint16_t cm = (uint16_t)(2000 - (el * 1950) / 10000);
    tx_meas(n, CAN_ID_SENSOR_DIST, &s->seq_dist, cm);
}

static void task_temp(node_t *n)
{
    sensor_state_t *s = n->state;
    int noise = (int)(s->rand_state = s->rand_state * 1103515245u + 12345u) % 20;
    tx_meas(n, CAN_ID_SENSOR_TEMP, &s->seq_temp, (uint16_t)(800 + noise));
}

static void task_hb(node_t *n)
{
    sensor_state_t *s = n->state;
    tx_meas(n, CAN_ID_HB_SENSOR, &s->seq_hb,
            (uint16_t)((now_ms() - s->t0) / 1000));
}

int main(int argc, char **argv)
{
    g_progname = "ecu_sensor";
    const char *iface = "vcan0";
    uint32_t duration = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--iface") && i + 1 < argc)        iface = argv[++i];
        else if (!strcmp(argv[i], "--duration") && i + 1 < argc) duration = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--verbose"))                  g_verbose = 1;
        else { fprintf(stderr, "usage: %s [--iface vcan0] [--duration s] [--verbose]\n", g_progname); return 2; }
    }
    install_signal_handlers();

    sensor_state_t s = { .t0 = now_ms(), .rand_state = 42 };
    node_t n = { .fd = can_open(iface), .name = "SENSOR", .node_id = 1,
                 .on_rx = NULL, .state = &s };
    if (n.fd < 0) return 1;

    node_add_task(&n, "speed", 20,  task_speed);
    node_add_task(&n, "dist",  50,  task_dist);
    node_add_task(&n, "temp",  100, task_temp);
    node_add_task(&n, "hb",    1000, task_hb);

    fprintf(stderr, "[%s] up on %s (duration=%us)\n", g_progname, iface, duration);
    int rc = node_run(&n, duration);
    node_print_stats(&n);
    close(n.fd);
    return rc;
}
