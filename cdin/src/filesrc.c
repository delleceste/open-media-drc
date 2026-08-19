#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "cdin.h"
#include "filesrc.h"
#include "log.h"
#include "ring.h"

/*
 * Depth of the prefetch buffer, and the largest slip the pacing clock will
 * absorb before it stops trying to catch up.  See the notes on the prefetch
 * thread and on pace() below.
 */
#define PREFETCH_MS	4000
#define PREFETCH_MIN	(256 * 1024)
#define SLIP_LIMIT_S	0.100

struct filesrc {
	int      fd;
	bool     loop;
	double   rate_paced;	/* nominal rate with the ppm offset applied */
	int      rate;
	int      channels;
	int      bits;
	size_t   frame_bytes;
	off_t    data_off;
	off_t    data_len;
	off_t    pos;		/* bytes consumed within the data chunk */
	double   t0;
	uint64_t frames_out;
	bool     started;

	/* Prefetch: the medium is not the source clock (see filesrc.h). */
	struct ring *stage;
	pthread_t    tid;
	bool         tid_ok;
	_Atomic int  done;		/* prefetch reached end of data */
	_Atomic int  rderr;		/* errno from the prefetch thread */
	bool         stalled;		/* edge state for the underrun warning */
	uint64_t     stalls;
	uint64_t     slips;
};

/* Little-endian readers: the WAV container is LE and so is every host we
   build for; a big-endian port would need byte swaps here. */
static uint16_t
rd16(const unsigned char *p)
{
	return (uint16_t)(p[0] | (p[1] << 8));
}

static uint32_t
rd32(const unsigned char *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
	    ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static double
now_monotonic(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static int
read_exact(int fd, void *buf, size_t n)
{
	size_t done = 0;
	ssize_t rc;

	while (done < n) {
		rc = read(fd, (char *)buf + done, n - done);
		if (rc > 0)
			done += (size_t)rc;
		else if (rc == 0)
			return -1;
		else if (errno != EINTR)
			return -1;
	}
	return 0;
}

/* Walk the RIFF chunks for 'fmt ' and 'data'. */
static int
parse_wav(struct filesrc *f, char *err, size_t errsz)
{
	unsigned char hdr[12], ch[8], fmt[40];
	uint16_t format, channels, bits;
	uint32_t rate, chunk_len;
	bool have_fmt = false;
	off_t off;

	if (read_exact(f->fd, hdr, sizeof(hdr)) != 0 ||
	    memcmp(hdr, "RIFF", 4) != 0 || memcmp(hdr + 8, "WAVE", 4) != 0) {
		snprintf(err, errsz, "not a RIFF/WAVE file");
		return -1;
	}
	off = 12;

	for (;;) {
		if (lseek(f->fd, off, SEEK_SET) == (off_t)-1 ||
		    read_exact(f->fd, ch, sizeof(ch)) != 0) {
			snprintf(err, errsz, have_fmt
			    ? "no 'data' chunk" : "no 'fmt ' chunk");
			return -1;
		}
		chunk_len = rd32(ch + 4);

		if (memcmp(ch, "fmt ", 4) == 0) {
			size_t want = chunk_len < sizeof(fmt) ? chunk_len : sizeof(fmt);

			if (want < 16 || read_exact(f->fd, fmt, want) != 0) {
				snprintf(err, errsz, "truncated 'fmt ' chunk");
				return -1;
			}
			format = rd16(fmt);
			channels = rd16(fmt + 2);
			rate = rd32(fmt + 4);
			bits = rd16(fmt + 14);

			/* 1 = PCM, 0xFFFE = EXTENSIBLE (whose subformat GUID
			   starts with the same 16-bit tag). */
			if (format == 0xFFFE && want >= 26)
				format = rd16(fmt + 24);
			if (format != 1) {
				snprintf(err, errsz,
				    "not linear PCM (format tag %u)", format);
				return -1;
			}
			if (bits != 16 && bits != 24 && bits != 32) {
				snprintf(err, errsz, "%u-bit samples are not "
				    "supported (use 16, 24 or 32)", bits);
				return -1;
			}
			f->rate = (int)rate;
			f->channels = (int)channels;
			f->bits = (int)bits;
			f->frame_bytes = (size_t)(bits == 24 ? 3 : bits / 8) *
			    channels;
			have_fmt = true;
		} else if (memcmp(ch, "data", 4) == 0) {
			if (!have_fmt) {
				snprintf(err, errsz, "'data' precedes 'fmt '");
				return -1;
			}
			f->data_off = off + 8;
			f->data_len = (off_t)chunk_len;
			return 0;
		}

		off += 8 + chunk_len + (chunk_len & 1);	/* chunks are padded */
	}
}

/*
 * Pull the file into the staging ring as fast as the medium allows.
 *
 * This thread exists because the medium is not the source clock.  A capture
 * device delivers on the USB isochronous schedule and cannot stall; a file on
 * a spinning USB disk can block for seconds.  With the read inline in
 * filesrc_read() that latency landed directly on the daemon's lead — one
 * 3 s stall on an external drive drained a 2 s lead to 23 ms and starved the
 * playback thread, which is a property of the test rig, not of the design
 * under test.  Prefetching keeps the pacing loop reading out of RAM.
 */
static void *
prefetch_thread(void *arg)
{
	struct filesrc *f = arg;
	unsigned char *buf;
	const size_t bufsz = 64 * 1024;

	if ((buf = malloc(bufsz)) == NULL) {
		atomic_store(&f->rderr, ENOMEM);
		ring_set_eof(f->stage);
		return NULL;
	}

	while (!cdin_stop && !cdin_io_abort) {
		size_t want = bufsz;
		ssize_t rc;

		if (f->pos >= f->data_len) {
			if (!f->loop)
				break;
			if (lseek(f->fd, f->data_off, SEEK_SET) == (off_t)-1) {
				atomic_store(&f->rderr, errno);
				break;
			}
			f->pos = 0;
		}
		if ((off_t)want > f->data_len - f->pos)
			want = (size_t)(f->data_len - f->pos);

		rc = read(f->fd, buf, want);
		if (rc > 0) {
			f->pos += rc;
			/* Back-pressure, never drop: a discarded frame here
			   would corrupt the very signal under measurement. */
			if (ring_write_full(f->stage, buf, (size_t)rc) <
			    (size_t)rc)
				break;			/* shutting down */
		} else if (rc == 0) {
			f->pos = f->data_len;		/* short file */
		} else if (errno != EINTR) {
			atomic_store(&f->rderr, errno);
			break;
		}
	}

	free(buf);
	atomic_store(&f->done, 1);
	ring_set_eof(f->stage);
	return NULL;
}

struct filesrc *
filesrc_open(const char *path, bool loop, double ppm, char *err, size_t errsz)
{
	struct filesrc *f;
	struct stat st;
	size_t stage_bytes;

	if ((f = calloc(1, sizeof(*f))) == NULL) {
		snprintf(err, errsz, "out of memory");
		return NULL;
	}
	if ((f->fd = open(path, O_RDONLY)) == -1) {
		snprintf(err, errsz, "%s", strerror(errno));
		free(f);
		return NULL;
	}
	if (parse_wav(f, err, errsz) != 0) {
		close(f->fd);
		free(f);
		return NULL;
	}

	/* A data length that overruns the file (or a zero length, which some
	   writers emit for streamed output) is clamped to what is really there. */
	if (fstat(f->fd, &st) == 0) {
		off_t avail = st.st_size - f->data_off;

		if (f->data_len == 0 || f->data_len > avail)
			f->data_len = avail;
	}
	f->data_len -= f->data_len % (off_t)f->frame_bytes;
	if (f->data_len <= 0) {
		snprintf(err, errsz, "no audio frames in the 'data' chunk");
		close(f->fd);
		free(f);
		return NULL;
	}

	f->loop = loop;
	f->rate_paced = (double)f->rate * (1.0 + ppm / 1e6);
	if (lseek(f->fd, f->data_off, SEEK_SET) == (off_t)-1) {
		snprintf(err, errsz, "seek to the data chunk: %s", strerror(errno));
		close(f->fd);
		free(f);
		return NULL;
	}

	stage_bytes = (size_t)((double)PREFETCH_MS / 1000.0 * f->rate) *
	    f->frame_bytes;
	if (stage_bytes < PREFETCH_MIN)
		stage_bytes = PREFETCH_MIN;
	if ((f->stage = ring_new(stage_bytes, f->frame_bytes)) == NULL) {
		snprintf(err, errsz, "cannot allocate a %zu byte prefetch buffer",
		    stage_bytes);
		close(f->fd);
		free(f);
		return NULL;
	}
	if (pthread_create(&f->tid, NULL, prefetch_thread, f) != 0) {
		snprintf(err, errsz, "cannot create the prefetch thread: %s",
		    strerror(errno));
		ring_free(f->stage);
		close(f->fd);
		free(f);
		return NULL;
	}
	f->tid_ok = true;
	log_debug("input file: %zu byte prefetch buffer (%d ms), so medium "
	    "latency stays off the lead", stage_bytes, PREFETCH_MS);
	return f;
}

void
filesrc_close(struct filesrc *f)
{
	if (f == NULL)
		return;
	if (f->tid_ok) {
		/* Shut the ring down first: the thread may be parked in
		   ring_write_full() waiting for space that will never come.
		   The signal breaks it out of a read() on a slow medium (the
		   handler is a no-op; the EINTR is the point). */
		ring_shutdown(f->stage);
		pthread_kill(f->tid, SIGUSR1);
		pthread_join(f->tid, NULL);
	}
	ring_free(f->stage);
	close(f->fd);
	free(f);
}

int    filesrc_rate(const struct filesrc *f)     { return f->rate; }
int    filesrc_channels(const struct filesrc *f) { return f->channels; }
int    filesrc_bits(const struct filesrc *f)     { return f->bits; }

double
filesrc_seconds(const struct filesrc *f)
{
	return (double)f->data_len / (double)f->frame_bytes / (double)f->rate;
}

/*
 * Wait until the emulated source clock says the next chunk is due.
 *
 * The schedule is absolute (t0 + frames/rate), which is what keeps a ppm
 * offset from accumulating rounding error.  The cost is that once the source
 * falls behind, every past deadline fires at once and the frames come out in a
 * burst — and a burst is exactly what a capture device cannot do.  On the
 * hardware the frames it could not hand over are simply gone; here they would
 * be replayed at many times realtime, which permanently inflates the lead by
 * the duration of the stall (a 3 s stall took a 2 s lead to 4.9 s and it never
 * came back down) and poisons the drift estimate with a step change.
 *
 * So beyond SLIP_LIMIT_S the whole schedule is shifted forward instead.  That
 * threshold is three orders of magnitude above the drift being emulated — a
 * 50 ppm source slips 50 us per second — so it can only ever trip on a stall.
 */
static void
pace(struct filesrc *f)
{
	struct timespec due;
	double t, now, slip;

	if (!f->started) {
		f->t0 = now_monotonic();
		f->started = true;
		return;
	}

	t = f->t0 + (double)f->frames_out / f->rate_paced;
	now = now_monotonic();
	slip = now - t;

	if (slip > SLIP_LIMIT_S) {
		f->t0 += slip;
		f->slips++;
		log_debug("input file: pacing slipped %.0f ms; resuming the "
		    "schedule from now rather than bursting to catch up",
		    slip * 1000.0);
		return;
	}
	if (slip >= 0.0)
		return;				/* already due */

	due.tv_sec = (time_t)t;
	due.tv_nsec = (long)((t - (double)due.tv_sec) * 1e9);
	while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &due, NULL) ==
	    EINTR) {
		if (cdin_stop || cdin_io_abort)
			return;
	}
}

ssize_t
filesrc_read(struct filesrc *f, void *buf, size_t n)
{
	size_t got;

	pace(f);
	if (cdin_stop || cdin_io_abort)
		return -1;

	/*
	 * Reading out of the staging ring, never off the medium: this call is
	 * on the clock and must not inherit disk latency.  If the ring is
	 * short here the medium genuinely could not sustain realtime, which is
	 * worth saying out loud — it is the one failure the rig can suffer
	 * that the real capture path cannot.
	 */
	if (ring_fill(f->stage) < n && !atomic_load(&f->done)) {
		if (!f->stalled) {
			f->stalled = true;
			f->stalls++;
			log_warn("input file: the medium is not keeping up — "
			    "prefetch buffer ran dry (%llu times); the lead "
			    "will suffer, the daemon is not at fault",
			    (unsigned long long)f->stalls);
		}
	} else {
		f->stalled = false;
	}

	got = ring_read_some(f->stage, buf, n);
	if (got == 0) {
		int e = atomic_load(&f->rderr);

		if (e != 0) {
			errno = e;
			return -1;
		}
		return 0;			/* clean end of data */
	}
	f->frames_out += got / f->frame_bytes;
	return (ssize_t)got;
}

uint64_t filesrc_stalls(const struct filesrc *f) { return f->stalls; }
uint64_t filesrc_slips(const struct filesrc *f)  { return f->slips; }
