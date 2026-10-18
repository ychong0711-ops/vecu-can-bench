/* Cooperative periodic-task node loop with select()-based CAN reception. */
#include "common.h"

int node_add_task(node_t *n, const char *name, uint32_t period_ms, task_fn fn)
{
    if (n->ntasks >= NODE_MAX_TASKS) return -1;
    struct task *t = &n->tasks[n->ntasks++];
    t->name = name; t->period_ms = period_ms; t->fn = fn;
    t->next_ms = 0; t->runs = 0; t->jitter_max_us = 0;
    return 0;
}

int node_run(node_t *n, uint32_t duration_s)
{
    uint64_t t0 = now_ms();
    uint64_t t_end = duration_s ? t0 + (uint64_t)duration_s * 1000ull : 0;

    for (int i = 0; i < n->ntasks; i++)
        n->tasks[i].next_ms = t0 + n->tasks[i].period_ms;

    while (g_running) {
        uint64_t now = now_ms();
        if (t_end && now >= t_end) break;

        uint64_t earliest = UINT64_MAX;
        for (int i = 0; i < n->ntasks; i++)
            if (n->tasks[i].next_ms < earliest) earliest = n->tasks[i].next_ms;
        if (earliest < now) earliest = now;
        uint64_t wait_ms = earliest - now;
        if (t_end && t_end - now < wait_ms) wait_ms = t_end - now;

        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(n->fd, &rfds);
        struct timeval tv = {
            .tv_sec  = (time_t)(wait_ms / 1000),
            .tv_usec = (suseconds_t)((wait_ms % 1000) * 1000)
        };
        int r = select(n->fd + 1, &rfds, NULL, NULL, &tv);
        if (r < 0) {
            if (errno == EINTR) continue;
            perror("select"); return -1;
        }
        if (r > 0 && FD_ISSET(n->fd, &rfds)) {
            struct can_frame f;
            ssize_t nb;
            while ((nb = recv(n->fd, &f, sizeof(f), MSG_DONTWAIT)) == (ssize_t)sizeof(f)) {
                if (n->on_rx) n->on_rx(n, &f);
            }
            if (nb < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                perror("recv"); return -1;
            }
        }

        now = now_ms();
        for (int i = 0; i < n->ntasks; i++) {
            struct task *t = &n->tasks[i];
            if (now >= t->next_ms) {
                uint64_t late_ms = now - t->next_ms;
                if (late_ms * 1000ull > t->jitter_max_us) t->jitter_max_us = late_ms * 1000ull;
                t->fn(n);
                t->runs++;
                t->next_ms += t->period_ms;
                if (t->next_ms < now) t->next_ms = now + t->period_ms;
            }
        }
    }
    return 0;
}

void node_print_stats(const node_t *n)
{
    fprintf(stderr, "[%s] task stats:\n", n->name);
    for (int i = 0; i < n->ntasks; i++) {
        const struct task *t = &n->tasks[i];
        fprintf(stderr, "  %-14s period=%4ums runs=%llu max_late=%llu us\n",
                t->name, t->period_ms, (unsigned long long)t->runs,
                (unsigned long long)t->jitter_max_us);
    }
}
