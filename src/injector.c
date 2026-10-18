/* vECU-Bench :: injector -- deterministic bus-load generator.
 * Converts a target bus-load percentage into frames-per-second for the
 * nominal 500 kbit/s reference:  fps = pct/100 * 500000 / (47 + 8*dlc)
 * Paces transmission on absolute 1-ms deadlines (clock_nanosleep), carrying
 * the fractional remainder across ticks for long-run accuracy.
 */
#include "common.h"

static uint16_t g_seq;
static uint64_t g_sent, g_enobufs;

int main(int argc, char **argv)
{
    g_progname = "injector";
    const char *iface = "vcan0";
    uint32_t duration = 0;
    double load_pct = 10.0;
    int dlc = 8;
    canid_t id = CAN_ID_LOADGEN;
    for (int i = 1; i < argc; i++) {
        if      (!strcmp(argv[i], "--iface") && i + 1 < argc)    iface = argv[++i];
        else if (!strcmp(argv[i], "--duration") && i + 1 < argc) duration = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--load-pct") && i + 1 < argc) load_pct = atof(argv[++i]);
        else if (!strcmp(argv[i], "--dlc") && i + 1 < argc)      dlc = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--id") && i + 1 < argc)       id = (canid_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--verbose"))                  g_verbose = 1;
        else { fprintf(stderr,
                "usage: %s [--iface vcan0] [--load-pct 25] [--duration s] "
                "[--dlc 8] [--id 0x400] [--verbose]\n", g_progname);
               return 2; }
    }
    if (dlc < 0 || dlc > 8) { fprintf(stderr, "dlc must be 0..8\n"); return 2; }
    if (load_pct < 0 || load_pct > 95) { fprintf(stderr, "load-pct must be 0..95\n"); return 2; }
    install_signal_handlers();

    int fd = can_open(iface);
    if (fd < 0) return 1;

    double fps = load_pct / 100.0 * (double)VECU_BITRATE_KBIT * 1000.0 / frame_bits_nominal(dlc);
    fprintf(stderr, "[injector] target %.2f%% bus load -> %.0f fps (dlc=%d, id=0x%03X, duration=%us)\n",
            load_pct, fps, dlc, id, duration);

    uint64_t t0 = now_ms();
    uint64_t next_ms = t0 + 1;
    double acc = 0;
    uint8_t data[8]; memset(data, 0xAA, sizeof(data));

    while (g_running) {
        uint64_t nm = now_ms();
        if (duration && nm >= t0 + (uint64_t)duration * 1000ull) break;

        struct timespec ts = {
            .tv_sec  = (time_t)(next_ms / 1000),
            .tv_nsec = (long)((next_ms % 1000) * 1000000L)
        };
        int sr = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL);
        if (sr != 0 && sr != EINTR) break;
        if (!g_running) break;
        next_ms += 1;
        if (next_ms < now_ms()) next_ms = now_ms();   /* fell behind: resync */

        acc += fps / 1000.0;
        long k = (long)acc;
        acc -= (double)k;
        for (long j = 0; j < k; j++) {
            data[0] = (uint8_t)(g_seq & 0xFF);
            data[1] = (uint8_t)(g_seq >> 8);
            int r = can_send(fd, id, data, (uint8_t)dlc);
            if (r == -ENOBUFS) { g_enobufs++; continue; }
            if (r < 0) break;
            g_sent++; g_seq++;
        }
    }

    double el = (double)(now_ms() - t0) / 1000.0;
    fprintf(stderr, "[injector] done: sent=%llu enobufs=%llu over %.2fs (%.0f fps actual)\n",
            (unsigned long long)g_sent, (unsigned long long)g_enobufs, el,
            el > 0 ? g_sent / el : 0);
    close(fd);
    return 0;
}
