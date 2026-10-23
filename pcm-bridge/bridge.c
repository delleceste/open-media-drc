/* Single-stream OSS byte transport. No sample arithmetic or DAC access. */
#include <sys/types.h>
#include <sys/soundcard.h>
#include <sys/filio.h>
#include <cuse.h>
#include <pthread.h>
#include <pthread_np.h>

#include <stdatomic.h>
#include <stdbool.h>
#include <signal.h>
#include <errno.h>
#include <getopt.h>
#include <inttypes.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "queue.h"

#define FRAME 8u
#define WORKERS 8
#define COPY_MAX 65536u
struct endpoint { bool writer, opened, busy, copying; unsigned fragment; };
static struct endpoint play = {.writer = true}, capture;
static struct pcm_queue queue;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed = PTHREAD_COND_INITIALIZER;
static atomic_bool stopping;
static bool failed;
static uint64_t generation = 1, resets, failures;
static int rate;

static void wake(void) { pthread_cond_broadcast(&changed); cuse_poll_wakeup(); }
static void fail_session(bool fault)
{
    if (!failed) { failed = true; failures += fault; generation++; }
    wake();
}
static struct timespec deadline(unsigned ms)
{
    struct timespec t;
    clock_gettime(CLOCK_REALTIME, &t);
    t.tv_nsec += (long)(ms % 1000) * 1000000;
    t.tv_sec += ms / 1000 + t.tv_nsec / 1000000000;
    t.tv_nsec %= 1000000000;
    return t;
}
/* Timed only to observe libcuse cancellation (SIGHUP does not wake a condvar).
 * This is not an audio clock: all data movement is driven by client demand. */
static int wait_change(uint64_t gen)
{
    struct timespec t = deadline(20);
    pthread_cond_timedwait(&changed, &lock, &t);
    if (atomic_load(&stopping) || failed || generation != gen) return CUSE_ERR_OTHER;
    if (cuse_got_peer_signal() == 0) return CUSE_ERR_SIGNAL;
    return 0;
}
static int bridge_open(struct cuse_dev *dev, int flags)
{
    struct endpoint *e = cuse_dev_get_priv0(dev);
    int direction = flags & (CUSE_FFLAG_READ | CUSE_FFLAG_WRITE);
    if (direction != (e->writer ? CUSE_FFLAG_WRITE : CUSE_FFLAG_READ))
        return CUSE_ERR_INVALID;
    pthread_mutex_lock(&lock);
    int error = 0;
    if (atomic_load(&stopping)) error = CUSE_ERR_NO_DEVICE;
    else if (e->opened || failed) error = CUSE_ERR_BUSY;
    else {
        e->opened = true; e->fragment = 4096;
        while (e->fragment > queue.capacity) e->fragment /= 2;
        cuse_dev_set_per_file_handle(dev, e);
        wake();
    }
    pthread_mutex_unlock(&lock);
    return error;
}
/* Drain only this queue, not downstream FIR or hardware latency. */
static int drain_locked(int flags)
{
    uint64_t gen = generation;
    struct timespec until = deadline(5000);
    if (queue.used % FRAME) return CUSE_ERR_INVALID;
    while (queue.used) {
        if (failed || atomic_load(&stopping)) return CUSE_ERR_OTHER;
        if (flags & CUSE_FFLAG_NONBLOCK) return CUSE_ERR_WOULDBLOCK;
        struct timespec now = deadline(0);
        if (now.tv_sec > until.tv_sec ||
            (now.tv_sec == until.tv_sec && now.tv_nsec >= until.tv_nsec))
            return CUSE_ERR_OTHER;
        int error = wait_change(gen);
        if (error) return error;
    }
    return 0;
}
static int bridge_close(struct cuse_dev *dev, int flags)
{
    struct endpoint *e = cuse_dev_get_priv0(dev);
    pthread_mutex_lock(&lock);
    int error = 0;
    /* libcuse waits for calls on this file before invoking close. */
    if (e->writer && !failed && !atomic_load(&stopping)) error = drain_locked(flags);
    e->opened = false;
    fail_session(error != 0 || queue.used != 0);
    /* A survivor belongs to the old session and must close before reopening. */
    if (!play.opened && !capture.opened) {
        pcm_queue_reset(&queue); failed = false; generation++;
    }
    wake();
    pthread_mutex_unlock(&lock);
    return error;
}
static int transfer(struct cuse_dev *dev, int flags, void *user, int len, bool writing)
{
    struct endpoint *e = cuse_dev_get_priv0(dev);
    if (writing != e->writer || len < 0) return CUSE_ERR_INVALID;
    if (!len) return 0;
    if (!writing && (unsigned)len < FRAME) return CUSE_ERR_INVALID;
    size_t n = (unsigned)len < COPY_MAX ? (unsigned)len : COPY_MAX;
    unsigned char *tmp = malloc(n);
    if (!tmp) return CUSE_ERR_NO_MEMORY;
    pthread_mutex_lock(&lock);
    int result = 0;
    if (e->busy) { result = CUSE_ERR_BUSY; goto out; }
    e->busy = true;
    uint64_t gen = generation;
    for (;;) {
        if (failed || atomic_load(&stopping)) { result = CUSE_ERR_OTHER; break; }
        size_t available = writing ? queue.capacity - queue.used : queue.used / FRAME * FRAME;
        if (available) {
            if (n > available) n = available;
            if (!writing) n = n / FRAME * FRAME;
            e->copying = true;
            if (!writing) pcm_queue_peek(&queue, tmp, n);
            pthread_mutex_unlock(&lock);
            result = writing ? cuse_copy_in(user, tmp, (int)n) : cuse_copy_out(tmp, user, (int)n);
            pthread_mutex_lock(&lock);
            e->copying = false;
            /* Reset refuses active copies. A peer close can invalidate a
             * session, but cannot reuse its storage while this file is open. */
            if (!result && gen != generation) result = CUSE_ERR_OTHER;
            if (!result) {
                if (writing) pcm_queue_write(&queue, tmp, n);
                else pcm_queue_consume(&queue, n);
                result = (int)n;
            }
            break;
        }
        if (flags & CUSE_FFLAG_NONBLOCK) { result = CUSE_ERR_WOULDBLOCK; break; }
        result = wait_change(gen);
        if (result) break;
    }
    e->busy = false; wake();
out:
    pthread_mutex_unlock(&lock);
    free(tmp);
    return result;
}
static int bridge_read(struct cuse_dev *d, int f, void *p, int n) { return transfer(d, f, p, n, false); }
static int bridge_write(struct cuse_dev *d, int f, const void *p, int n) { return transfer(d, f, (void *)p, n, true); }
static int bridge_poll(struct cuse_dev *d, int flags, int events)
{
    (void)flags;
    struct endpoint *e = cuse_dev_get_priv0(d);
    pthread_mutex_lock(&lock);
    int result = 0;
    if (failed || atomic_load(&stopping)) result = CUSE_POLL_ERROR;
    else if (e->writer && queue.used < queue.capacity) result = events & CUSE_POLL_WRITE;
    else if (!e->writer && queue.used >= FRAME) result = events & CUSE_POLL_READ;
    pthread_mutex_unlock(&lock);
    return result;
}
static int bridge_ioctl(struct cuse_dev *dev, int flags, unsigned long cmd, void *user)
{
    struct endpoint *e = cuse_dev_get_priv0(dev);
    int value = 0, error = 0;
    bool input = cmd == SNDCTL_DSP_SETFMT || cmd == SNDCTL_DSP_CHANNELS ||
        cmd == SNDCTL_DSP_SPEED || cmd == SNDCTL_DSP_SETFRAGMENT || cmd == SNDCTL_DSP_STEREO || cmd == FIONBIO || cmd == FIOASYNC;
    if (input && (error = cuse_copy_in(user, &value, sizeof(value)))) return error;
    pthread_mutex_lock(&lock);
    if (atomic_load(&stopping)) { error = CUSE_ERR_NO_DEVICE; goto done; }
    switch (cmd) {
    case FIONBIO: /* Kernel supplies current nonblocking flag on each call. */
        goto done;
    case FIOASYNC:
        if (value) error = CUSE_ERR_INVALID; /* No SIGIO implementation. */
        goto done;
    case SNDCTL_DSP_SETFMT:
        if (value != AFMT_QUERY && value != AFMT_S32_LE) error = CUSE_ERR_INVALID;
        value = AFMT_S32_LE; break;
    case SNDCTL_DSP_CHANNELS:
        if (value != 0 && value != 2) error = CUSE_ERR_INVALID;
        value = 2; break;
    case SNDCTL_DSP_STEREO:
        if (value != 1) error = CUSE_ERR_INVALID;
        value = 1; break;
    case SNDCTL_DSP_SPEED:
        if (value != 0 && value != rate) error = CUSE_ERR_INVALID;
        value = rate; break;
    case SOUND_PCM_READ_RATE: value = rate; break;
    case SOUND_PCM_READ_CHANNELS: value = 2; break;
    case SOUND_PCM_READ_BITS: value = 32; break;
    case SNDCTL_DSP_GETFMTS: value = AFMT_S32_LE; break;
    case SNDCTL_DSP_GETCAPS: value = 0; break; /* no trigger, mmap or duplex */
    case SNDCTL_DSP_SETFRAGMENT: {
        unsigned shift = (unsigned)value & 0xffff;
        if (shift < 3 || shift > 20 || (1u << shift) > queue.capacity) error = CUSE_ERR_INVALID;
        else e->fragment = 1u << shift;
        break;
    }
    case SNDCTL_DSP_GETBLKSIZE: value = (int)e->fragment; break;
    case SNDCTL_DSP_GETODELAY:
        if (!e->writer) error = CUSE_ERR_INVALID;
        value = (int)queue.used; break;
    case SNDCTL_DSP_GETISPACE:
    case SNDCTL_DSP_GETOSPACE: {
        if ((cmd == SNDCTL_DSP_GETOSPACE) != e->writer) { error = CUSE_ERR_INVALID; break; }
        if (failed) { error = CUSE_ERR_OTHER; break; }
        size_t bytes = e->writer ? queue.capacity - queue.used : queue.used / FRAME * FRAME;
        audio_buf_info info = { .fragments = (int)(bytes / e->fragment),
            .fragstotal = (int)(queue.capacity / e->fragment),
            .fragsize = (int)e->fragment, .bytes = (int)bytes };
        pthread_mutex_unlock(&lock);
        return cuse_copy_out(&info, user, sizeof(info));
    }
    case SNDCTL_DSP_RESET:
        if (play.copying || capture.copying) error = CUSE_ERR_BUSY;
        else { pcm_queue_reset(&queue); generation++; resets++; wake(); }
        goto done;
    case SNDCTL_DSP_SYNC:
        if (!e->writer) error = CUSE_ERR_INVALID;
        else if (e->busy) error = CUSE_ERR_BUSY;
        else { e->busy = true; error = drain_locked(flags); e->busy = false; wake(); }
        goto done;
    default: error = CUSE_ERR_INVALID; goto done;
    }
    pthread_mutex_unlock(&lock);
    return error ? error : cuse_copy_out(&value, user, sizeof(value));
done:
    pthread_mutex_unlock(&lock);
    return error;
}
static const struct cuse_methods methods = {
    .cm_open = bridge_open, .cm_close = bridge_close, .cm_read = bridge_read,
    .cm_write = bridge_write, .cm_ioctl = bridge_ioctl, .cm_poll = bridge_poll
};
static void noop(int sig) { (void)sig; }
static void *worker(void *arg)
{
    (void)arg;
    while (!atomic_load(&stopping)) {
        if (cuse_wait_and_process() && !atomic_load(&stopping)) {
            /* An interrupt of the server wait is harmless. */
            if (errno != EINTR) { kill(getpid(), SIGTERM); break; }
        }
    }
    return NULL;
}
static int number(const char *s, int min, int max)
{
    char *end; errno = 0;
    long n = strtol(s, &end, 10);
    if (errno || !*s || *end || n < min || n > max) {
        fprintf(stderr, "invalid numeric argument: %s\n", s); exit(2);
    }
    return (int)n;
}
static bool valid_name(const char *s)
{
    if (strncmp(s, "dsp.", 4) || strlen(s) > 63) return false;
    for (; *s; s++) if (!((*s >= 'a' && *s <= 'z') || (*s >= 'A' && *s <= 'Z') ||
        (*s >= '0' && *s <= '9') || *s == '.' || *s == '_')) return false;
    return true;
}
int main(int argc, char **argv)
{
    const char *play_name = "dsp.omdrcbridgeplay", *capture_name = "dsp.omdrcbridgeloop";
    const char *client_user = NULL;
    int capacity_ms = 200;
    static const struct option options[] = {
        {"rate", required_argument, NULL, 'r'}, {"capacity-ms", required_argument, NULL, 'b'},
        {"play", required_argument, NULL, 'p'}, {"capture", required_argument, NULL, 'c'},
        {"format", required_argument, NULL, 'f'}, {"channels", required_argument, NULL, 'n'},
        {"user", required_argument, NULL, 'u'},
        {"help", no_argument, NULL, 'h'}, {NULL, 0, NULL, 0}
    };
    int opt;
    while ((opt = getopt_long(argc, argv, "", options, NULL)) != -1) {
        switch (opt) {
        case 'r': rate = number(optarg, 8000, 384000); break;
        case 'b': capacity_ms = number(optarg, 1, 2000); break;
        case 'p': play_name = optarg; break;
        case 'c': capture_name = optarg; break;
        case 'f': if (strcmp(optarg, "s32le")) return 2; break;
        case 'n': if (number(optarg, 2, 2) != 2) return 2; break;
        case 'u': client_user = optarg; break;
        case 'h': puts("omdrc-pcm-bridge --rate HZ [--capacity-ms 200] [--format s32le]\n"
                      "  [--channels 2] [--user LOGIN] [--play dsp.NAME] [--capture dsp.NAME]\n"
                      "Experimental foreground bridge; defaults to isolated test nodes.\n"
                      "SIGUSR1: counters; SIGTERM/SIGINT: shutdown."); return 0;
        default: return 2;
        }
    }
    if (!rate || optind != argc || !valid_name(play_name) || !valid_name(capture_name) ||
        !strcmp(play_name, capture_name)) { fprintf(stderr, "invalid/missing arguments; see --help\n"); return 2; }
    uid_t client_uid = getuid();
    gid_t client_gid = getgid();
    if (client_user) {
        struct passwd *account = getpwnam(client_user);
        if (!account) { fprintf(stderr, "unknown client user: %s\n", client_user); return 2; }
        client_uid = account->pw_uid;
        client_gid = account->pw_gid;
    }
    size_t capacity = (size_t)rate * capacity_ms / 1000 * FRAME;
    if (pcm_queue_init(&queue, capacity)) return 1;
    sigset_t signals;
    sigemptyset(&signals); sigaddset(&signals, SIGTERM); sigaddset(&signals, SIGINT);
    sigaddset(&signals, SIGUSR1); pthread_sigmask(SIG_BLOCK, &signals, NULL);
    struct sigaction action = {.sa_handler = noop};
    sigemptyset(&action.sa_mask); sigaction(SIGUSR2, &action, NULL);
    /* libcuse sends SIGHUP to interrupt a signalled/closing client call. */
    sigaction(SIGHUP, &action, NULL);
    if (cuse_init()) { fprintf(stderr, "cuse_init failed: load cuse and check /dev/cuse permissions\n"); return 1; }
    struct cuse_dev *a = cuse_dev_create(&methods, &play, NULL, client_uid, client_gid, 0600, "%s", play_name);
    struct cuse_dev *b = a ? cuse_dev_create(&methods, &capture, NULL, client_uid, client_gid, 0600, "%s", capture_name) : NULL;
    if (!a || !b) {
        fprintf(stderr, "cannot create test devices\n");
        if (a) cuse_dev_destroy(a);
        cuse_uninit(); return 1;
    }
    pthread_t threads[WORKERS]; unsigned started = 0;
    for (; started < WORKERS; started++) if (pthread_create(&threads[started], NULL, worker, NULL)) break;
    if (started != WORKERS) kill(getpid(), SIGTERM);
    fprintf(stderr, "ready rate=%d format=s32le channels=2 capacity=%zu play=/dev/%s capture=/dev/%s\n",
        rate, capacity, play_name, capture_name);
    int sig;
    do {
        sigwait(&signals, &sig);
        pthread_mutex_lock(&lock);
        struct pcm_queue snapshot = queue;
        uint64_t gen = generation, reset_count = resets, failure_count = failures;
        bool writer_open = play.opened, reader_open = capture.opened, session_failed = failed;
        if (sig != SIGUSR1) { atomic_store(&stopping, true); wake(); }
        pthread_mutex_unlock(&lock);
        /* Slow diagnostic consumers must not hold the audio queue lock. */
        fprintf(stderr, "generation=%" PRIu64 " accepted=%" PRIu64 " delivered=%" PRIu64
            " queued=%zu discarded=%" PRIu64 " high_water=%zu resets=%" PRIu64
            " failures=%" PRIu64 " writer=%d reader=%d failed=%d\n", gen,
            snapshot.accepted, snapshot.delivered, snapshot.used, snapshot.discarded,
            snapshot.high_water, reset_count, failure_count, writer_open, reader_open, session_failed);
    } while (sig == SIGUSR1);
    /* Interrupt idle GET_COMMAND waits and join before destroying libcuse's
     * mutex or device objects. Retry covers the check-to-ioctl signal race. */
    for (unsigned i = 0; i < started; i++) {
        int err;
        do {
            pthread_kill(threads[i], SIGUSR2);
            struct timespec t = deadline(100);
            err = pthread_timedjoin_np(threads[i], NULL, &t);
        } while (err == ETIMEDOUT);
    }
    cuse_dev_destroy(b); cuse_dev_destroy(a); cuse_uninit();
    pcm_queue_free(&queue);
    return started == WORKERS ? 0 : 1;
}
