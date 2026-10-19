/* SocketCAN helpers: open/bind, send, measured-frame packing. */
#include "common.h"

volatile sig_atomic_t g_running = 1;
int g_verbose = 0;
const char *g_progname = "vecu";

void warn_once(const char *fmt, ...)
{
    static int seen;
    va_list ap;
    if (seen) return;
    seen = 1;
    fprintf(stderr, "[%s] ", g_progname);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
}

static void on_signal(int sig)
{
    (void)sig;
    g_running = 0;
}

void install_signal_handlers(void)
{
    struct sigaction sa;
    sigset_t block_mask, old_mask;

    /* Block SIGINT and SIGTERM during handler installation to prevent
     * race where a signal arrives after sigaction() but before the
     * main loop checks g_running. */
    sigemptyset(&block_mask);
    sigaddset(&block_mask, SIGINT);
    sigaddset(&block_mask, SIGTERM);
    sigprocmask(SIG_BLOCK, &block_mask, &old_mask);

    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sa.sa_flags = SA_RESTART;  /* restart select()/recv() on signal */
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    /* Unblock signals - now safe to receive them */
    sigprocmask(SIG_SETMASK, &old_mask, NULL);
}

int can_open(const char *ifname)
{
    int fd = socket(PF_CAN, SOCK_RAW, CAN_RAW);
    if (fd < 0) { perror("socket(PF_CAN)"); return -1; }

    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    snprintf(ifr.ifr_name, sizeof(ifr.ifr_name), "%s", ifname);
    if (ioctl(fd, SIOCGIFINDEX, &ifr) < 0) {
        fprintf(stderr, "interface '%s' not found (run scripts/setup_vcan.sh)\n", ifname);
        close(fd);
        return -1;
    }

    struct sockaddr_can addr;
    memset(&addr, 0, sizeof(addr));
    addr.can_family  = AF_CAN;
    addr.can_ifindex = ifr.ifr_ifindex;
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind"); close(fd); return -1;
    }

    /* NOTE — do NOT disable CAN_RAW_LOOPBACK here (tried; reverted):
     * vcan has no IFF_ECHO, so same-host receivers get frames exclusively via
     * the socket-layer loopback clone. Disabling it leaves TX counters moving
     * but delivers 0 frames to receivers (measured on WSL2 kernel 6.18).
     * Side effect of that clone path: it bypasses the TX qdisc, so tc/netem
     * delay/loss cannot affect receiver-visible frames on vcan (documented
     * in docs/ARCHITECTURE.md §6; meaningful only with real hardware). */

    /* enlarge receive buffer so bursty bus load does not overflow */
    int rcvbuf = 4 * 1024 * 1024;
    if (setsockopt(fd, SOL_SOCKET, SO_RCVBUFFORCE, &rcvbuf, sizeof(rcvbuf)) < 0 &&
        setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf)) < 0) {
        warn_once("setsockopt(SO_RCVBUF, %d) failed: %s", rcvbuf, strerror(errno));
    } else {
        /* unprivileged SO_RCVBUF is silently capped at net.core.rmem_max */
        int eff = 0; socklen_t elen = sizeof(eff);
        if (getsockopt(fd, SOL_SOCKET, SO_RCVBUF, &eff, &elen) == 0 && eff < rcvbuf)
            warn_once("rx buffer capped at %d bytes (wanted %d; raise net.core.rmem_max?)",
                      eff, rcvbuf);
    }

    struct timeval tv = { .tv_sec = 0, .tv_usec = 100 * 1000 };
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    return fd;
}

int can_send(int fd, canid_t id, const uint8_t data[8], uint8_t dlc)
{
    struct can_frame f;
    memset(&f, 0, sizeof(f));
    f.can_id  = id;
    f.can_dlc = dlc;
    if (data && dlc) memcpy(f.data, data, dlc);
    ssize_t n;
    do {
        n = write(fd, &f, sizeof(f));
    } while (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK));
    if (n != (ssize_t)sizeof(f)) {
        if (errno == ENOBUFS) return -ENOBUFS; /* tx queue full: count & drop */
        perror("write(can)");
        return -1;
    }
    return 0;
}

void meas_pack(struct can_frame *f, canid_t id, uint16_t seq, uint16_t value)
{
    uint32_t tx = (uint32_t)(now_ms() & 0xFFFFFFFFull);
    memset(f, 0, sizeof(*f));
    f->can_id  = id;
    f->can_dlc = 8;
    f->data[0] = (uint8_t)(seq & 0xFF);
    f->data[1] = (uint8_t)(seq >> 8);
    f->data[2] = (uint8_t)(tx & 0xFF);
    f->data[3] = (uint8_t)((tx >> 8) & 0xFF);
    f->data[4] = (uint8_t)((tx >> 16) & 0xFF);
    f->data[5] = (uint8_t)((tx >> 24) & 0xFF);
    f->data[6] = (uint8_t)(value & 0xFF);
    f->data[7] = (uint8_t)(value >> 8);
}

int meas_unpack(const struct can_frame *f, uint16_t *seq, uint32_t *tx_ms,
                uint16_t *value)
{
    if (f->can_dlc < 8) return -1;
    if (seq)   *seq   = (uint16_t)(f->data[0] | (f->data[1] << 8));
    if (tx_ms) *tx_ms = (uint32_t)f->data[2] | ((uint32_t)f->data[3] << 8) |
                         ((uint32_t)f->data[4] << 16) | ((uint32_t)f->data[5] << 24);
    if (value) *value = (uint16_t)(f->data[6] | (f->data[7] << 8));
    return 0;
}

uint32_t ms_diff_u32(uint32_t now32, uint32_t tx32) { return now32 - tx32; }
uint16_t seq_diff_u16(uint16_t a, uint16_t b) { return (uint16_t)(a - b); }
