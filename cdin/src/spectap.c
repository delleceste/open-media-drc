#include "spectap.h"
#include "log.h"

#include <sys/stat.h>
#include <sys/types.h>

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

struct spectap {
	char     *path;
	int       fd;		/* -1 while no reader is attached */
	int       retry_secs;
	double    next_try;	/* monotonic seconds; 0 = try now */
	uint64_t  written;
	uint64_t  dropped;
	bool      logged_attach;
};

static double
mono_now(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

/*
 * Writing to a pipe whose reader has closed raises SIGPIPE, and its default
 * disposition TERMINATES THE PROCESS — the analyzer being stopped would kill
 * the audio bridge mid-disc.  FreeBSD has no per-descriptor suppression for
 * pipes (F_SETNOSIGPIPE is a macOS/NetBSD extension, SO_NOSIGPIPE is sockets
 * only), so the disposition is the only place to fix this.
 *
 * The daemon already ignores SIGPIPE process-wide, so this changes nothing
 * there.  It exists because this module promises never to disturb playback,
 * and a promise that silently depends on the caller having set a signal
 * disposition is not a promise — it is a trap for the next caller, which is
 * exactly what the test binary turned out to be.  A caller that has installed
 * its own handler keeps it: only the lethal default is replaced.
 */
static void
ensure_sigpipe_ignored(void)
{
	struct sigaction cur;

	if (sigaction(SIGPIPE, NULL, &cur) != 0)
		return;
	if (cur.sa_handler != SIG_DFL)
		return;			/* the caller has an opinion; respect it */
	cur.sa_handler = SIG_IGN;
	cur.sa_flags = 0;
	sigemptyset(&cur.sa_mask);
	(void)sigaction(SIGPIPE, &cur, NULL);
}

struct spectap *
spectap_open(const char *path, int retry_secs)
{
	struct spectap *t;

	if (path == NULL || *path == '\0')
		return NULL;		/* disabled, not an error */

	ensure_sigpipe_ignored();

	if ((t = calloc(1, sizeof(*t))) == NULL)
		return NULL;
	if ((t->path = strdup(path)) == NULL) {
		free(t);
		return NULL;
	}
	t->fd = -1;
	t->retry_secs = retry_secs > 0 ? retry_secs : 2;
	t->next_try = 0.0;
	log_info("spectrum tap: %s (waiting for a reader)", t->path);
	return t;
}

/*
 * Try to attach.  A FIFO opened O_WRONLY|O_NONBLOCK returns ENXIO while no
 * reader holds the other end — that is the ordinary idle case, not a fault,
 * so it is silent.  ENOENT is equally ordinary: omdrcctrl creates the FIFO
 * only when the analyzer is configured, and this daemon deliberately does not
 * create it (see spectap.h).
 */
static void
try_attach(struct spectap *t)
{
	double now = mono_now();
	int fd;

	if (now < t->next_try)
		return;
	t->next_try = now + (double)t->retry_secs;

	fd = open(t->path, O_WRONLY | O_NONBLOCK);
	if (fd < 0) {
		if (errno != ENXIO && errno != ENOENT && !t->logged_attach) {
			/* Something structural — a plain file where a FIFO was
			   expected, or permissions.  Say it once. */
			log_warn("spectrum tap %s: %s", t->path, strerror(errno));
			t->logged_attach = true;
		}
		return;
	}
	t->fd = fd;
	t->logged_attach = false;
	log_info("spectrum tap %s: reader attached", t->path);
}

static void
detach(struct spectap *t, const char *why)
{
	if (t->fd < 0)
		return;
	close(t->fd);
	t->fd = -1;
	t->next_try = mono_now() + (double)t->retry_secs;
	log_info("spectrum tap %s: reader gone (%s)", t->path, why);
}

void
spectap_write(struct spectap *t, const void *buf, size_t n)
{
	ssize_t rc;

	if (t == NULL || n == 0)
		return;
	if (t->fd < 0) {
		try_attach(t);
		if (t->fd < 0)
			return;
	}

	rc = write(t->fd, buf, n);
	if (rc < 0) {
		if (errno == EAGAIN || errno == EWOULDBLOCK) {
			/* The reader is behind.  Dropping is the contract: the
			   analyzer re-syncs on the next period, and nothing
			   about playback is allowed to depend on it. */
			t->dropped += n;
			return;
		}
		if (errno == EINTR) {
			t->dropped += n;
			return;
		}
		detach(t, errno == EPIPE ? "closed the FIFO" : strerror(errno));
		t->dropped += n;
		return;
	}
	t->written += (uint64_t)rc;
	if ((size_t)rc < n)
		t->dropped += n - (size_t)rc;	/* partial: never retried */
}

void
spectap_stats(const struct spectap *t, uint64_t *written, uint64_t *dropped)
{
	if (written != NULL)
		*written = t == NULL ? 0 : t->written;
	if (dropped != NULL)
		*dropped = t == NULL ? 0 : t->dropped;
}

bool
spectap_attached(const struct spectap *t)
{
	return t != NULL && t->fd >= 0;
}

void
spectap_close(struct spectap *t)
{
	if (t == NULL)
		return;
	if (t->fd >= 0)
		close(t->fd);
	free(t->path);
	free(t);
}
