/* vECU-Bench :: monitor -- passive measurement node.
 * Records every frame on the bus to CSV and computes:
 *   - per-ID frame loss  (16-bit sequence gaps, wrap-safe)
 *   - one-way latency    (rx_ms - embedded tx_ms, same CLOCK_MONOTONIC host)
 *   - PING/PONG RTT      (active probing, --ping-interval)
 *   - bus load           (nominal bits 47+8*dlc vs 500 kbit/s, 1-s windows)
 * Emits a machine-readable JSON summary (--summary) for tests/analysis.
 */
#include "common.h"
#include "id_map.h"

typedef struct { uint32_t *v; size_t n, cap; } u32arr;
typedef struct { uint64_t *v; size_t n, cap; } u64arr;

typedef struct {
    uint64_t count, lost, dup;
    int      init;
    uint16_t last_seq;
    u32arr   lat;
    uint64_t lat_dropped;
} idstat_t;

typedef struct {
    FILE    *csv;
    uint64_t start_us, frames_total, rx_errors, enobufs;
    uint16_t ping_seq;
    double   ping_interval_ms;
    uint64_t pings_sent;
    idstat_t st[VECU_ID_MAP_TRACK_COUNT];
    uint64_t bits_total, bits_worst_total;
    u64arr   win_bits;
    size_t   nwin;
    uint64_t duration_ms;
    size_t   max_lat_samples;
    uint64_t lat_overflow;
} mon_t;
static void u32_push(u32arr *a, uint32_t x)
{
    if (a->n == a->cap) {
        size_t newcap = a->cap ? a->cap * 2 : 8192;
        uint32_t *nv = (uint32_t *)realloc(a->v, newcap * sizeof(uint32_t));
        if (!nv) { perror("realloc"); exit(1); }
        if (a->cap < newcap) {
            memset(nv + a->cap, 0, (newcap - a->cap) * sizeof(uint32_t));
        }
        a->v = nv;
        a->cap = newcap;
    }
    a->v[a->n++] = x;
}

static void u64_push(u64arr *a, uint64_t x)
{
    if (a->n == a->cap) {
        size_t newcap = a->cap ? a->cap * 2 : 3600;  /* default 1 hour */
        uint64_t *nv = (uint64_t *)realloc(a->v, newcap * sizeof(uint64_t));
        if (!nv) { perror("realloc"); exit(1); }
        if (a->cap < newcap) {
            memset(nv + a->cap, 0, (newcap - a->cap) * sizeof(uint64_t));
        }
        a->v = nv;
        a->cap = newcap;
    }
    a->v[a->n++] = x;
}
static int cmp_u32(const void *a, const void *b)
{
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return (x > y) - (x < y);
}
/* nearest-rank percentile on sorted array */
static uint32_t pct(const u32arr *a, double q)
{
    if (!a->n) return 0;
    size_t idx = (size_t)ceil(q / 100.0 * (double)a->n);
    if (idx == 0) idx = 1;
    if (idx > a->n) idx = a->n;
    return a->v[idx - 1];
}


static int track_index(canid_t id)
{
    for (int i = 0; i < VECU_ID_MAP_TRACK_COUNT; i++)
        if (VECU_ID_MAP_TRACK[i].id == id) return i;
    return -1;
}

static void mon_on_rx(node_t *n, const struct can_frame *f)
{
    mon_t *m = n->state;
    uint64_t rx_us = now_us();
    uint64_t rx_ms = rx_us / 1000;
    uint32_t rx32 = (uint32_t)(rx_ms & 0xFFFFFFFFull);
    m->frames_total++;

    int ti = track_index(f->can_id);
    uint16_t seq = 0; uint32_t tx = 0; uint16_t val = 0;
    int has_meas = (meas_unpack(f, &seq, &tx, &val) == 0);

    /* CSV: rx_us,id,dlc,seq,tx_ms,lat_ms,val */
    if (m->csv) {
        if (has_meas) {
            uint32_t lat = ms_diff_u32(rx32, tx);
            if (lat < 60000u)
                fprintf(m->csv, "%llu,0x%03X,%u,%u,%u,%u,%u\n",
                        (unsigned long long)rx_us, f->can_id, f->can_dlc,
                        seq, tx, lat, val);
            else
                fprintf(m->csv, "%llu,0x%03X,%u,%u,%u,,%u\n",
                        (unsigned long long)rx_us, f->can_id, f->can_dlc,
                        seq, tx, val);
        } else {
            fprintf(m->csv, "%llu,0x%03X,%u,,,,\n",
                    (unsigned long long)rx_us, f->can_id, f->can_dlc);
        }
    }

    /* bus load windows */
    m->bits_total       += (uint64_t)frame_bits_nominal(f->can_dlc);
    m->bits_worst_total += (uint64_t)frame_bits_worst(f->can_dlc);
    size_t w = (size_t)((rx_us - m->start_us) / 1000000ull);
    if (w >= m->win_bits.n) {
        /* grow array to cover this window (fills gaps with zeros) */
        while (m->win_bits.n <= w) {
            u64_push(&m->win_bits, 0);
        }
    }
    m->win_bits.v[w] += (uint64_t)frame_bits_nominal(f->can_dlc);
    if (w + 1 > m->nwin) m->nwin = w + 1;

    if (ti < 0 || !has_meas) return;
    idstat_t *s = &m->st[ti];
    s->count++;

    /* sequence-gap loss accounting (wrap-safe, reorder-tolerant) */
    if (!s->init) { s->init = 1; s->last_seq = seq; }
    else {
        uint16_t d = seq_diff_u16(seq, s->last_seq);   /* (seq-last) mod 2^16 */
        if (d == 0)            s->dup++;
        else if (d < 32768)  { s->lost += (uint64_t)(d - 1); s->last_seq = seq; }
        /* d >= 32768: late/reordered duplicate -> ignore */
    }

    uint32_t lat = ms_diff_u32(rx32, tx);
    if (lat < 60000u) {
        if (!m->max_lat_samples || s->lat.n < m->max_lat_samples) {
            u32_push(&s->lat, lat);
        } else {
            SATURATING_INC(&m->lat_overflow);
        }
    } else {
        s->lat_dropped++;
    }
}

static void mon_task_ping(node_t *n)
{
    mon_t *m = n->state;
    struct can_frame f;
    meas_pack(&f, CAN_ID_PING, m->ping_seq++, 0);
    if (can_send(n->fd, f.can_id, f.data, f.can_dlc) == 0) m->pings_sent++;
}

static double win_load_pct(uint64_t bits)
{
    return (double)bits / ((double)VECU_BITRATE_KBIT * 1000.0) * 100.0;
}

static void emit_json_u32arr(FILE *fp, const char *key, const u32arr *a)
{
    if (!a->n) { fprintf(fp, "\"%s\":null", key); return; }
    u32arr sarr = { .v = malloc(a->n * sizeof(uint32_t)), .n = a->n };
    if (!sarr.v) { perror("malloc"); exit(1); }
    memcpy(sarr.v, a->v, a->n * sizeof(uint32_t));
    qsort(sarr.v, sarr.n, sizeof(uint32_t), cmp_u32);
    fprintf(fp, "\"%s\":{\"n\":%zu,\"p50\":%u,\"p95\":%u,\"p99\":%u,\"max\":%u}",
            key, sarr.n, pct(&sarr, 50), pct(&sarr, 95), pct(&sarr, 99),
            sarr.v[sarr.n - 1]);
    free(sarr.v);
}

static void write_summary(mon_t *m, const char *path, const char *iface)
{
    FILE *fp = fopen(path, "w");
    if (!fp) { perror("summary fopen"); return; }
    double dur_s = m->duration_ms / 1000.0;
    if (dur_s <= 0) dur_s = 1e-9;
    double avg = (double)m->bits_total / ((double)VECU_BITRATE_KBIT * 1000.0 * dur_s) * 100.0;
    double maxw = 0;
    for (size_t i = 0; i < m->nwin; i++)
        if (win_load_pct(m->win_bits.v[i]) > maxw) maxw = win_load_pct(m->win_bits.v[i]);

    fprintf(fp, "{\n");
    fprintf(fp, "  \"iface\": \"%s\",\n  \"bitrate_kbit\": %d,\n", iface, VECU_BITRATE_KBIT);
    fprintf(fp, "  \"duration_s\": %.3f,\n  \"frames_total\": %llu,\n", dur_s,
            (unsigned long long)m->frames_total);
    fprintf(fp, "  \"rx_errors\": %llu,\n", (unsigned long long)m->rx_errors);
    fprintf(fp, "  \"bus\": {\"bits_nominal\": %llu, \"bits_worst\": %llu, "
                "\"avg_load_pct\": %.3f, \"max_load_pct_1s\": %.3f, \"windows_1s\": %zu},\n",
            (unsigned long long)m->bits_total, (unsigned long long)m->bits_worst_total,
            avg, maxw, m->nwin);
    fprintf(fp, "  \"ids\": {\n");
    for (int i = 0; i < VECU_ID_MAP_TRACK_COUNT; i++) {
        idstat_t *s = &m->st[i];
        double loss = s->count + s->lost ? 100.0 * (double)s->lost / (double)(s->count + s->lost) : 0.0;
        fprintf(fp, "    \"0x%03X\": {\"name\":\"%s\",\"count\":%llu,\"lost\":%llu,"
                    "\"loss_pct\":%.4f,\"dup\":%llu,",
                VECU_ID_MAP_TRACK[i].id, VECU_ID_MAP_TRACK[i].name, (unsigned long long)s->count,
                (unsigned long long)s->lost, loss, (unsigned long long)s->dup);
        emit_json_u32arr(fp, "lat_ms", &s->lat);
        fprintf(fp, ",\"lat_dropped\":%llu}", (unsigned long long)s->lat_dropped);
        if (i < VECU_ID_MAP_TRACK_COUNT - 1) fprintf(fp, ",\n"); else fprintf(fp, "\n");
    }
    fprintf(fp, "  },\n");
    fprintf(fp, "  \"ping\": {\"sent\": %llu, \"interval_ms\": %.1f}",
            (unsigned long long)m->pings_sent, m->ping_interval_ms);
    if (m->lat_overflow > 0) {
        fprintf(fp, ",\n  \"lat_overflow\": %llu", (unsigned long long)m->lat_overflow);
    }
    fprintf(fp, "\n}\n");
    fclose(fp);
}

int main(int argc, char **argv)
{
    g_progname = "monitor";
    const char *iface = "vcan0", *csv_path = NULL, *sum_path = NULL;
    uint32_t duration = 0;
    double ping_iv = 0;
    for (int i = 1; i < argc; i++) {
        if      (!strcmp(argv[i], "--iface") && i + 1 < argc)    iface = argv[++i];
        else if (!strcmp(argv[i], "--duration") && i + 1 < argc) duration = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--csv") && i + 1 < argc)      csv_path = argv[++i];
        else if (!strcmp(argv[i], "--summary") && i + 1 < argc)  sum_path = argv[++i];
        else if (!strcmp(argv[i], "--ping-interval") && i + 1 < argc) ping_iv = atof(argv[++i]);
        else if (!strcmp(argv[i], "--max-samples") && i + 1 < argc) { ++i; /* value consumed in the second pass below */ }
        else if (!strcmp(argv[i], "--verbose"))                  g_verbose = 1;
        else { fprintf(stderr,
                "usage: %s [--iface vcan0] [--duration s] [--csv f.csv] "
                "[--summary f.json] [--ping-interval ms] [--max-samples n] [--verbose]\n", g_progname);
               return 2; }
    }
    install_signal_handlers();

    static mon_t m; memset(&m, 0, sizeof(m));
    m.ping_interval_ms = ping_iv;
    /* Default: 100,000 samples per tracked CAN ID.
     * Each sample is 4 bytes (uint32_t), so 100k = ~400 KB per ID.
     * With VECU_ID_MAP_TRACK_COUNT IDs, total ~400 KB * count.
     * User can override with --max-samples. */
    m.max_lat_samples = 100000;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--max-samples") && i + 1 < argc) {
            long long ms = atoll(argv[i + 1]);
            if (ms > 0) m.max_lat_samples = (size_t)ms;
            break;
        }
    }
    if (csv_path) {
        m.csv = fopen(csv_path, "w");
        if (!m.csv) { perror("csv fopen"); return 1; }
        fprintf(m.csv, "rx_us,id,dlc,seq,tx_ms,lat_ms,val\n");
    }

    node_t n = { .fd = can_open(iface), .name = "MONITOR", .node_id = 9,
                 .on_rx = mon_on_rx, .state = &m };
    if (n.fd < 0) return 1;
    m.start_us = now_us();
    if (ping_iv > 0) {
        uint32_t p = (uint32_t)(ping_iv < 1 ? 1 : ping_iv);
        node_add_task(&n, "ping", p, mon_task_ping);
    }

    fprintf(stderr, "[monitor] recording on %s (duration=%us ping=%gms csv=%s)\n",
            iface, duration, ping_iv, csv_path ? csv_path : "-");
    uint64_t t0 = now_ms();
    int rc = node_run(&n, duration);
    m.duration_ms = now_ms() - t0;

    if (m.csv) fclose(m.csv);
    if (sum_path) write_summary(&m, sum_path, iface);

    /* human summary */
    double maxw = 0;
    for (size_t i = 0; i < m.nwin; i++)
        if (win_load_pct(m.win_bits.v[i]) > maxw) maxw = win_load_pct(m.win_bits.v[i]);
    fprintf(stderr, "[monitor] %.1fs, %llu frames, bus avg %.2f%% (max 1s %.2f%%)\n",
            m.duration_ms / 1000.0, (unsigned long long)m.frames_total,
            (double)m.bits_total / ((double)VECU_BITRATE_KBIT * 1000.0 * (m.duration_ms / 1000.0 + 1e-9)) * 100.0,
            maxw);
    for (int i = 0; i < VECU_ID_MAP_TRACK_COUNT; i++) {
        idstat_t *s = &m.st[i];
        if (!s->count) continue;
        u32arr sorted = { .v = malloc(s->lat.n * sizeof(uint32_t) + 1), .n = s->lat.n };
        if (!sorted.v) { perror("malloc"); exit(1); }
        if (s->lat.n) { memcpy(sorted.v, s->lat.v, s->lat.n * sizeof(uint32_t));
                        qsort(sorted.v, sorted.n, sizeof(uint32_t), cmp_u32); }
        fprintf(stderr, "  0x%03X %-13s n=%-8llu lost=%-6llu lat ms p50=%u p95=%u p99=%u max=%u\n",
                VECU_ID_MAP_TRACK[i].id, VECU_ID_MAP_TRACK[i].name, (unsigned long long)s->count,
                (unsigned long long)s->lost, pct(&sorted, 50), pct(&sorted, 95),
                pct(&sorted, 99), sorted.n ? sorted.v[sorted.n - 1] : 0);
        free(sorted.v);
    }
    node_print_stats(&n);
    close(n.fd);
    return rc;
}
