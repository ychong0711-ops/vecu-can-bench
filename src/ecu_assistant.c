/* vECU-Bench :: ecu_assistant -- "AssistantECU"-like node.
 * Consumes CMD_BRAKE/CMD_LIGHT, simulates actuators, logs state changes and
 * answers PING (0x500) with PONG (0x501) echoing seq + tx timestamp so the
 * monitor can measure round-trip latency through the whole stack.
 */
#include "common.h"

typedef struct {
    uint16_t seq_hb;
    uint8_t  brake, light;      /* actuator state */
    uint8_t  p_brake, p_light;  /* previous (for change logging) */
    uint64_t pongs;
    uint64_t max_echo_us;       /* worst ping->pong handling time */
} assistant_state_t;

static void on_rx(node_t *n, const struct can_frame *f)
{
    assistant_state_t *s = n->state;
    switch (f->can_id) {
    case CAN_ID_CMD_BRAKE: {
        uint16_t seq, val; uint32_t tx;
        if (meas_unpack(f, &seq, &tx, &val) == 0) {
            s->brake = (uint8_t)(val & 0xFF);
            if (s->brake != s->p_brake) {
                LOGN("actuator brake %u -> %u", s->p_brake, s->brake);
                s->p_brake = s->brake;
            }
        }
        break;
    }
    case CAN_ID_CMD_LIGHT: {
        uint16_t seq, val; uint32_t tx;
        if (meas_unpack(f, &seq, &tx, &val) == 0) {
            s->light = (uint8_t)(val & 0xFF);
            if (s->light != s->p_light) {
                LOGN("actuator light %u -> %u", s->p_light, s->light);
                s->p_light = s->light;
            }
        }
        break;
    }
    case CAN_ID_PING: {
        uint64_t t_in = now_us();
        /* echo payload verbatim, flip ID */
        if (can_send(n->fd, CAN_ID_PONG, f->data, f->can_dlc) < 0) {
            LOGN("can_send pong failed");
        } else {
            uint64_t dt = now_us() - t_in;
            s->pongs++;
            if (dt > s->max_echo_us) s->max_echo_us = dt;
        }
        break;
    }
    default: break;
    }
}

static void task_hb(node_t *n)
{
    assistant_state_t *s = n->state;
    struct can_frame f;
    meas_pack(&f, CAN_ID_HB_ASSISTANT, s->seq_hb++, 0);
    if (can_send(n->fd, f.can_id, f.data, f.can_dlc) < 0) {
        LOGN("can_send hb failed");
    }
}

static void task_status(node_t *n)
{
    assistant_state_t *s = n->state;
    if (g_verbose)
        fprintf(stderr, "[assistant] brake=%u light=%u pongs=%llu max_echo=%llu us\n",
                s->brake, s->light, (unsigned long long)s->pongs,
                (unsigned long long)s->max_echo_us);
}

int main(int argc, char **argv)
{
    g_progname = "ecu_assistant";
    const char *iface = "vcan0";
    uint32_t duration = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--iface") && i + 1 < argc)        iface = argv[++i];
        else if (!strcmp(argv[i], "--duration") && i + 1 < argc) duration = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--verbose"))                  g_verbose = 1;
        else { fprintf(stderr, "usage: %s [--iface vcan0] [--duration s] [--verbose]\n", g_progname); return 2; }
    }
    install_signal_handlers();

    assistant_state_t s; memset(&s, 0, sizeof(s));
    node_t n = { .fd = can_open(iface), .name = "ASSISTANT", .node_id = 3,
                 .on_rx = on_rx, .state = &s };
    if (n.fd < 0) return 1;

    node_add_task(&n, "hb",     1000, task_hb);
    node_add_task(&n, "status", 2000, task_status);

    fprintf(stderr, "[%s] up on %s (duration=%us)\n", g_progname, iface, duration);
    int rc = node_run(&n, duration);
    node_print_stats(&n);
    fprintf(stderr, "[assistant] pongs=%llu max_echo=%llu us\n",
            (unsigned long long)s.pongs, (unsigned long long)s.max_echo_us);
    close(n.fd);
    return rc;
}
