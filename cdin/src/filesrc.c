#include <dirent.h>
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
 * Depth of the prefetch buffer, the largest slip the pacing clock will absorb
 * before it stops trying to catch up, and how long a transport mutes while the
 * sled moves.  See prefetch_thread() and pace() below.
 */
#define PREFETCH_MS	4000
#define PREFETCH_MIN	(256 * 1024)
#define SLIP_LIMIT_S	0.100
#define SEEK_MUTE_MS	300		/* README: "brief mute (0.1-1 s)" */
#define MAX_EVENTS	64

enum ev_kind {
	EV_SKIP, EV_PREV, EV_SEEK, EV_PAUSE, EV_DROPOUT, EV_STOP
};

/*
 * One scripted transport action.  Each element is owned by exactly one thread —
 * EV_DROPOUT by the pacing side (it is a wire event: no frames arrive), all the
 * others by the producer (they change what is on the disc) — so `fired` needs
 * no synchronisation.
 */
struct event {
	double       at;	/* seconds into the stream */
	enum ev_kind kind;
	double       arg;	/* seek/pause seconds, dropout milliseconds */
	bool         fired;
};

struct track {
	char  *name;		/* basename, for logging */
	int    fd;
	off_t  data_off;
	off_t  data_len;	/* frame-aligned */
};

struct filesrc {
	/* format — one per disc, by definition */
	int      rate;
	int      channels;
	int      bits;
	size_t   frame_bytes;
	double   rate_paced;	/* nominal rate with the ppm offset applied */

	struct track *tracks;
	int      ntracks;
	size_t   gap_bytes;	/* inter-track digital silence */
	bool     loop;

	/* producer state (prefetch thread only) */
	int      cur;
	off_t    pos;		/* bytes consumed within the current track */
	size_t   silence_left;	/* bytes of digital silence still to emit */
	uint64_t bytes_made;	/* bytes, not frames: a chunk need not be a whole
				   number of frames (64 KiB is not a multiple of
				   a 24-bit stereo frame), and truncating the
				   division would make the produced clock run
				   slow and fire scripted events late */
	_Atomic int carrier_lost;	/* read by the consumer once the stream ends */

	/* consumer state (capture thread only) */
	double   t0;
	uint64_t frames_out;
	bool     started;
	bool     stalled;	/* edge state for the underrun warning */

	struct event events[MAX_EVENTS];
	int      nevents;

	struct ring *stage;
	pthread_t    tid;
	bool         tid_ok;
	_Atomic int      done;		/* producer finished; nothing more will arrive */
	_Atomic int      rderr;		/* errno from the producer */
	_Atomic uint64_t stalls;
	_Atomic uint64_t slips;
	_Atomic uint64_t dropouts;
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

/* mm:ss for logging; a disc is never long enough to need hours. */
static const char *
mmss(double seconds, char *out, size_t outsz)
{
	int s = (int)(seconds + 0.5);

	snprintf(out, outsz, "%d:%02d", s / 60, s % 60);
	return out;
}

/* ── WAV parsing ───────────────────────────────────────────────────────── */

struct wavfmt {
	int rate, channels, bits;
};

/* Walk the RIFF chunks for 'fmt ' and 'data'. */
static int
parse_wav(int fd, struct wavfmt *wf, off_t *data_off, off_t *data_len,
    char *err, size_t errsz)
{
	unsigned char hdr[12], ch[8], fmt[40];
	uint16_t format, channels, bits;
	uint32_t rate, chunk_len;
	bool have_fmt = false;
	off_t off;

	if (lseek(fd, 0, SEEK_SET) == (off_t)-1 ||
	    read_exact(fd, hdr, sizeof(hdr)) != 0 ||
	    memcmp(hdr, "RIFF", 4) != 0 || memcmp(hdr + 8, "WAVE", 4) != 0) {
		snprintf(err, errsz, "not a RIFF/WAVE file");
		return -1;
	}
	off = 12;

	for (;;) {
		if (lseek(fd, off, SEEK_SET) == (off_t)-1 ||
		    read_exact(fd, ch, sizeof(ch)) != 0) {
			snprintf(err, errsz, have_fmt
			    ? "no 'data' chunk" : "no 'fmt ' chunk");
			return -1;
		}
		chunk_len = rd32(ch + 4);

		if (memcmp(ch, "fmt ", 4) == 0) {
			size_t want = chunk_len < sizeof(fmt) ? chunk_len : sizeof(fmt);

			if (want < 16 || read_exact(fd, fmt, want) != 0) {
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
			if (channels == 0) {
				snprintf(err, errsz, "zero channels");
				return -1;
			}
			wf->rate = (int)rate;
			wf->channels = (int)channels;
			wf->bits = (int)bits;
			have_fmt = true;
		} else if (memcmp(ch, "data", 4) == 0) {
			if (!have_fmt) {
				snprintf(err, errsz, "'data' precedes 'fmt '");
				return -1;
			}
			*data_off = off + 8;
			*data_len = (off_t)chunk_len;
			return 0;
		}

		off += 8 + chunk_len + (chunk_len & 1);	/* chunks are padded */
	}
}

/* ── track list ────────────────────────────────────────────────────────── */

static bool
is_wav_name(const char *name)
{
	size_t n = strlen(name);

	return n > 4 && name[0] != '.' && strcasecmp(name + n - 4, ".wav") == 0;
}

static int
name_cmp(const void *a, const void *b)
{
	return strcmp(*(const char *const *)a, *(const char *const *)b);
}

/*
 * Collect the tracks: a directory becomes a disc (its WAVs in name order,
 * which is how rippers number them), a plain file a one-track disc.
 */
static char **
collect_paths(const char *path, int *count, char *err, size_t errsz)
{
	struct stat st;
	char **paths = NULL;
	int n = 0, cap = 0;
	DIR *d;
	struct dirent *de;

	if (stat(path, &st) != 0) {
		snprintf(err, errsz, "%s", strerror(errno));
		return NULL;
	}
	if (!S_ISDIR(st.st_mode)) {
		if ((paths = malloc(sizeof(*paths))) == NULL) {
			snprintf(err, errsz, "out of memory");
			return NULL;
		}
		if ((paths[0] = strdup(path)) == NULL) {
			free(paths);
			snprintf(err, errsz, "out of memory");
			return NULL;
		}
		*count = 1;
		return paths;
	}

	if ((d = opendir(path)) == NULL) {
		snprintf(err, errsz, "%s", strerror(errno));
		return NULL;
	}
	while ((de = readdir(d)) != NULL) {
		char full[1024];

		if (!is_wav_name(de->d_name))
			continue;
		if (n == cap) {
			char **grown;

			cap = cap ? cap * 2 : 16;
			if ((grown = realloc(paths, (size_t)cap * sizeof(*paths))) == NULL) {
				snprintf(err, errsz, "out of memory");
				goto fail;
			}
			paths = grown;
		}
		snprintf(full, sizeof(full), "%s/%s", path, de->d_name);
		if ((paths[n] = strdup(full)) == NULL) {
			snprintf(err, errsz, "out of memory");
			goto fail;
		}
		n++;
	}
	closedir(d);

	if (n == 0) {
		snprintf(err, errsz, "no .wav files in the directory");
		free(paths);
		return NULL;
	}
	qsort(paths, (size_t)n, sizeof(*paths), name_cmp);
	*count = n;
	return paths;

fail:
	closedir(d);
	while (n-- > 0)
		free(paths[n]);
	free(paths);
	return NULL;
}

/* ── transport script ──────────────────────────────────────────────────── */

static int
ev_cmp(const void *a, const void *b)
{
	const struct event *x = a, *y = b;

	return x->at < y->at ? -1 : x->at > y->at ? 1 : 0;
}

static int
parse_transport(struct filesrc *f, const char *spec, char *err, size_t errsz)
{
	char *copy, *save = NULL, *tok;

	if ((copy = strdup(spec)) == NULL) {
		snprintf(err, errsz, "out of memory");
		return -1;
	}

	for (tok = strtok_r(copy, ",", &save); tok != NULL;
	    tok = strtok_r(NULL, ",", &save)) {
		struct event ev = { 0 };
		char name[32];
		char *colon, *eq;

		if ((colon = strchr(tok, ':')) == NULL) {
			snprintf(err, errsz, "'%s': expected AT:EVENT", tok);
			goto fail;
		}
		*colon = '\0';
		ev.at = atof(tok);
		if (ev.at < 0) {
			snprintf(err, errsz, "'%s': negative time", tok);
			goto fail;
		}

		snprintf(name, sizeof(name), "%s", colon + 1);
		if ((eq = strchr(name, '=')) != NULL) {
			*eq = '\0';
			ev.arg = atof(eq + 1);
		}

		if (strcmp(name, "skip") == 0) {
			ev.kind = EV_SKIP;
		} else if (strcmp(name, "prev") == 0) {
			ev.kind = EV_PREV;
		} else if (strcmp(name, "stop") == 0) {
			ev.kind = EV_STOP;
		} else if (strcmp(name, "seek") == 0) {
			ev.kind = EV_SEEK;
			if (eq == NULL) {
				snprintf(err, errsz, "seek needs =SECONDS");
				goto fail;
			}
		} else if (strcmp(name, "pause") == 0) {
			ev.kind = EV_PAUSE;
			if (eq == NULL || ev.arg <= 0) {
				snprintf(err, errsz, "pause needs =SECONDS");
				goto fail;
			}
		} else if (strcmp(name, "dropout") == 0) {
			ev.kind = EV_DROPOUT;
			if (eq == NULL || ev.arg <= 0) {
				snprintf(err, errsz, "dropout needs =MILLISECONDS");
				goto fail;
			}
		} else {
			snprintf(err, errsz, "unknown event '%s' (skip, prev, "
			    "seek, pause, dropout, stop)", name);
			goto fail;
		}

		if (f->nevents == MAX_EVENTS) {
			snprintf(err, errsz, "more than %d events", MAX_EVENTS);
			goto fail;
		}
		f->events[f->nevents++] = ev;
	}

	free(copy);
	qsort(f->events, (size_t)f->nevents, sizeof(f->events[0]), ev_cmp);
	return 0;

fail:
	free(copy);
	return -1;
}

/* ── producer ──────────────────────────────────────────────────────────── */

static double
produced_seconds(const struct filesrc *f)
{
	return (double)f->bytes_made / (double)f->frame_bytes / (double)f->rate;
}

/* Where we are on the disc, for log lines that have to be correlated with the
   stats: the producer runs seconds ahead of what is being heard, so wall-clock
   timestamps alone would be misleading. */
static double
disc_position(const struct filesrc *f)
{
	double s = 0.0;
	int i;

	for (i = 0; i < f->cur; i++)
		s += (double)f->tracks[i].data_len / (double)f->frame_bytes /
		    f->rate;
	return s + (double)f->pos / (double)f->frame_bytes / f->rate;
}

static void
seek_to_track(struct filesrc *f, int idx)
{
	f->cur = idx;
	f->pos = 0;
	lseek(f->tracks[idx].fd, f->tracks[idx].data_off, SEEK_SET);
}

static void
add_silence_ms(struct filesrc *f, double ms)
{
	size_t frames = (size_t)(ms / 1000.0 * f->rate);

	f->silence_left += frames * f->frame_bytes;
}

/* Apply every content event that is now due.  Returns false to end the stream. */
static bool
apply_due_events(struct filesrc *f)
{
	double now = produced_seconds(f);
	char pos[16];
	int i;

	for (i = 0; i < f->nevents; i++) {
		struct event *ev = &f->events[i];

		if (ev->fired || ev->kind == EV_DROPOUT || ev->at > now)
			continue;
		ev->fired = true;

		switch (ev->kind) {
		case EV_SKIP:
		case EV_PREV: {
			int next = ev->kind == EV_SKIP ? f->cur + 1 : f->cur - 1;

			if (next < 0)
				next = 0;
			if (next >= f->ntracks) {
				log_info("transport: skip past the last track "
				    "at %s — the disc ends",
				    mmss(disc_position(f), pos, sizeof(pos)));
				return false;
			}
			log_info("transport: %s to track %d/%d (%s) at %s",
			    ev->kind == EV_SKIP ? "skip" : "prev", next + 1,
			    f->ntracks, f->tracks[next].name,
			    mmss(disc_position(f), pos, sizeof(pos)));
			/* The mute a real sled makes — not the 2 s inter-track
			   pause, which a skip cuts through. */
			f->silence_left = 0;
			add_silence_ms(f, SEEK_MUTE_MS);
			seek_to_track(f, next);
			break;
		}
		case EV_SEEK: {
			off_t delta = (off_t)(ev->arg * f->rate) *
			    (off_t)f->frame_bytes;
			off_t want = f->pos + delta;
			struct track *t = &f->tracks[f->cur];

			if (want < 0)
				want = 0;
			if (want > t->data_len)
				want = t->data_len;
			want -= want % (off_t)f->frame_bytes;
			log_info("transport: %s %+.1f s within track %d at %s",
			    ev->arg < 0 ? "rewind" : "fast forward", ev->arg,
			    f->cur + 1, mmss(disc_position(f), pos, sizeof(pos)));
			f->pos = want;
			lseek(t->fd, t->data_off + want, SEEK_SET);
			add_silence_ms(f, SEEK_MUTE_MS);
			break;
		}
		case EV_PAUSE:
			log_info("transport: pause %.1f s at %s — carrier stays "
			    "up, digital silence on the wire", ev->arg,
			    mmss(disc_position(f), pos, sizeof(pos)));
			add_silence_ms(f, ev->arg * 1000.0);
			break;
		case EV_STOP:
			log_info("transport: stop at %s — the carrier drops",
			    mmss(disc_position(f), pos, sizeof(pos)));
			atomic_store(&f->carrier_lost, 1);
			return false;
		case EV_DROPOUT:
			break;			/* handled on the pacing side */
		}
	}
	return true;
}

/* Bytes we may produce before the next content event comes due, so an event
   lands within a frame of its scripted time instead of at chunk granularity. */
static size_t
bytes_until_next_event(const struct filesrc *f, size_t want)
{
	double now = produced_seconds(f), best = -1.0;
	size_t limit;
	int i;

	for (i = 0; i < f->nevents; i++) {
		const struct event *ev = &f->events[i];

		if (ev->fired || ev->kind == EV_DROPOUT || ev->at <= now)
			continue;
		best = ev->at - now;
		break;				/* the array is time-sorted */
	}
	if (best < 0.0)
		return want;

	limit = (size_t)(best * f->rate) * f->frame_bytes;
	if (limit < f->frame_bytes)
		limit = f->frame_bytes;
	return limit < want ? limit : want;
}

static bool
emit(struct filesrc *f, const unsigned char *buf, size_t n)
{
	if (ring_write_full(f->stage, buf, n) < n)
		return false;			/* shutting down */
	f->bytes_made += n;
	return true;
}

/* Move to the next track, inserting the inter-track silence.  Returns false at
   the end of the disc. */
static bool
advance_track(struct filesrc *f)
{
	char pos[16];

	if (f->cur + 1 < f->ntracks) {
		f->silence_left += f->gap_bytes;
		seek_to_track(f, f->cur + 1);
		log_info("transport: track %d/%d (%s) begins at %s",
		    f->cur + 1, f->ntracks, f->tracks[f->cur].name,
		    mmss(disc_position(f), pos, sizeof(pos)));
		return true;
	}
	if (f->loop) {
		f->silence_left += f->gap_bytes;
		seek_to_track(f, 0);
		log_info("transport: disc repeats from track 1");
		return true;
	}
	log_info("transport: end of the disc");
	return false;
}

/*
 * Produce the disc's byte stream as fast as the medium allows.
 *
 * This runs on its own thread because the medium is not the source clock.  A
 * capture device delivers on the USB isochronous schedule and cannot stall; a
 * file on a spinning USB disk can block for seconds.  With the read inline in
 * filesrc_read() that latency landed directly on the daemon's lead — one 3 s
 * stall on an external drive drained a 2 s lead to 23 ms and starved the
 * playback thread, which is a property of the test rig, not of the design
 * under test.  Prefetching keeps the pacing loop reading out of RAM.
 */
static void *
prefetch_thread(void *arg)
{
	struct filesrc *f = arg;
	unsigned char *buf;
	const size_t bufsz = 64 * 1024 - (64 * 1024) % f->frame_bytes;
	bool lap_progress = true;

	if ((buf = malloc(bufsz)) == NULL) {
		atomic_store(&f->rderr, ENOMEM);
		goto done;
	}

	while (!cdin_stop && !atomic_load(&cdin_io_abort)) {
		struct track *t = &f->tracks[f->cur];
		size_t want;
		ssize_t rc;

		if (!apply_due_events(f))
			break;

		if (f->silence_left > 0) {
			/* Digital silence is exactly zero on a CD, so this is
			   what the daemon's detector must see. */
			want = f->silence_left < bufsz ? f->silence_left : bufsz;
			want = bytes_until_next_event(f, want);
			want -= want % f->frame_bytes;
			memset(buf, 0, want);
			if (!emit(f, buf, want))
				break;
			f->silence_left -= want;
			continue;
		}

		if (f->pos >= t->data_len) {
			if (!lap_progress) {
				/* The file shrank under us: without this the
				   loop would seek back and spin forever. */
				atomic_store(&f->rderr, EIO);
				log_err("input %s: no data where the header "
				    "promised some", t->name);
				break;
			}
			if (!advance_track(f))
				break;
			lap_progress = false;
			continue;
		}

		want = bufsz;
		if ((off_t)want > t->data_len - f->pos)
			want = (size_t)(t->data_len - f->pos);
		want = bytes_until_next_event(f, want);

		rc = read(t->fd, buf, want);
		if (rc > 0) {
			f->pos += rc;
			lap_progress = true;
			/* Back-pressure, never drop: a discarded frame here
			   would corrupt the very signal under measurement. */
			if (!emit(f, buf, (size_t)rc))
				break;
		} else if (rc == 0) {
			f->pos = t->data_len;		/* short file */
		} else if (errno != EINTR) {
			atomic_store(&f->rderr, errno);
			break;
		}
	}

	free(buf);
done:
	atomic_store(&f->done, 1);
	ring_set_eof(f->stage);
	return NULL;
}

/* ── open / close ──────────────────────────────────────────────────────── */

static void
free_tracks(struct filesrc *f)
{
	int i;

	for (i = 0; i < f->ntracks; i++) {
		if (f->tracks[i].fd != -1)
			close(f->tracks[i].fd);
		free(f->tracks[i].name);
	}
	free(f->tracks);
	f->tracks = NULL;
	f->ntracks = 0;
}

static int
load_tracks(struct filesrc *f, char **paths, int n, char *err, size_t errsz)
{
	struct wavfmt wf, first = { 0 };
	struct stat st;
	int i;

	if ((f->tracks = calloc((size_t)n, sizeof(*f->tracks))) == NULL) {
		snprintf(err, errsz, "out of memory");
		return -1;
	}
	for (i = 0; i < n; i++)
		f->tracks[i].fd = -1;

	for (i = 0; i < n; i++) {
		struct track *t = &f->tracks[i];
		const char *slash = strrchr(paths[i], '/');
		char sub[192];

		if ((t->fd = open(paths[i], O_RDONLY)) == -1) {
			snprintf(err, errsz, "%s: %s", paths[i], strerror(errno));
			return -1;
		}
		f->ntracks = i + 1;	/* so free_tracks() closes it if we bail */
		if (parse_wav(t->fd, &wf, &t->data_off, &t->data_len, sub,
		    sizeof(sub)) != 0) {
			snprintf(err, errsz, "%s: %s", paths[i], sub);
			return -1;
		}
		if (i == 0) {
			first = wf;
			f->frame_bytes =
			    (size_t)(wf.bits == 24 ? 3 : wf.bits / 8) *
			    (size_t)wf.channels;
		} else if (wf.rate != first.rate || wf.bits != first.bits ||
		    wf.channels != first.channels) {
			/* A disc has one format by definition, and the daemon
			   opens the output once to match it. */
			snprintf(err, errsz, "%s is %d Hz %d-bit %dch but the "
			    "first track is %d Hz %d-bit %dch; a disc has one "
			    "format", paths[i], wf.rate, wf.bits, wf.channels,
			    first.rate, first.bits, first.channels);
			return -1;
		}

		/* A data length that overruns the file (or a zero length, which
		   some writers emit for streamed output) is clamped to what is
		   really there. */
		if (fstat(t->fd, &st) == 0) {
			off_t avail = st.st_size - t->data_off;

			if (t->data_len == 0 || t->data_len > avail)
				t->data_len = avail;
		}
		t->data_len -= t->data_len % (off_t)f->frame_bytes;
		if (t->data_len <= 0) {
			snprintf(err, errsz, "%s: no audio frames in the "
			    "'data' chunk", paths[i]);
			return -1;
		}
		if (lseek(t->fd, t->data_off, SEEK_SET) == (off_t)-1) {
			snprintf(err, errsz, "%s: seek to the data chunk: %s",
			    paths[i], strerror(errno));
			return -1;
		}

		snprintf(sub, sizeof(sub), "%s", slash ? slash + 1 : paths[i]);
		if ((t->name = strdup(sub)) == NULL) {
			snprintf(err, errsz, "out of memory");
			return -1;
		}
	}

	f->rate = first.rate;
	f->channels = first.channels;
	f->bits = first.bits;
	return 0;
}

struct filesrc *
filesrc_open(const struct filesrc_cfg *cfg, char *err, size_t errsz)
{
	struct filesrc *f;
	char **paths;
	size_t stage_bytes;
	char total[16], one[16];
	int npaths, i;

	if ((paths = collect_paths(cfg->path, &npaths, err, errsz)) == NULL)
		return NULL;

	if ((f = calloc(1, sizeof(*f))) == NULL) {
		snprintf(err, errsz, "out of memory");
		goto free_paths;
	}
	if (load_tracks(f, paths, npaths, err, errsz) != 0)
		goto fail;
	if (cfg->transport != NULL &&
	    parse_transport(f, cfg->transport, err, errsz) != 0)
		goto fail;

	f->loop = cfg->loop;
	f->rate_paced = (double)f->rate * (1.0 + cfg->ppm / 1e6);
	f->gap_bytes = (size_t)(cfg->gap_ms / 1000.0 * f->rate) * f->frame_bytes;

	stage_bytes = (size_t)((double)PREFETCH_MS / 1000.0 * f->rate) *
	    f->frame_bytes;
	if (stage_bytes < PREFETCH_MIN)
		stage_bytes = PREFETCH_MIN;
	if ((f->stage = ring_new(stage_bytes, f->frame_bytes)) == NULL) {
		snprintf(err, errsz, "cannot allocate a %zu byte prefetch buffer",
		    stage_bytes);
		goto fail;
	}

	log_info("disc: %d track%s, %s, %d Hz %d-bit %dch, %d ms between tracks",
	    f->ntracks, f->ntracks == 1 ? "" : "s",
	    mmss(filesrc_seconds(f), total, sizeof(total)), f->rate, f->bits,
	    f->channels, cfg->gap_ms);
	for (i = 0; i < f->ntracks; i++) {
		log_debug("  track %d: %s (%s)", i + 1, f->tracks[i].name,
		    mmss((double)f->tracks[i].data_len / (double)f->frame_bytes /
		    f->rate, one, sizeof(one)));
	}
	log_debug("input: %zu byte prefetch buffer (%d ms), so medium latency "
	    "stays off the lead", stage_bytes, PREFETCH_MS);

	if (pthread_create(&f->tid, NULL, prefetch_thread, f) != 0) {
		snprintf(err, errsz, "cannot create the prefetch thread: %s",
		    strerror(errno));
		goto fail;
	}
	f->tid_ok = true;

	for (i = 0; i < npaths; i++)
		free(paths[i]);
	free(paths);
	return f;

fail:
	free_tracks(f);
	ring_free(f->stage);
	free(f);
free_paths:
	for (i = 0; i < npaths; i++)
		free(paths[i]);
	free(paths);
	return NULL;
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
	free_tracks(f);
	ring_free(f->stage);
	free(f);
}

int filesrc_rate(const struct filesrc *f)     { return f->rate; }
int filesrc_channels(const struct filesrc *f) { return f->channels; }
int filesrc_bits(const struct filesrc *f)     { return f->bits; }
int filesrc_tracks(const struct filesrc *f)   { return f->ntracks; }

double
filesrc_seconds(const struct filesrc *f)
{
	double s = 0.0;
	int i;

	for (i = 0; i < f->ntracks; i++)
		s += (double)f->tracks[i].data_len / (double)f->frame_bytes /
		    f->rate;
	return s;
}

uint64_t filesrc_stalls(struct filesrc *f)   { return atomic_load(&f->stalls); }
uint64_t filesrc_slips(struct filesrc *f)    { return atomic_load(&f->slips); }
uint64_t filesrc_dropouts(struct filesrc *f) { return atomic_load(&f->dropouts); }
bool filesrc_carrier_lost(struct filesrc *f)  { return atomic_load(&f->carrier_lost) != 0; }

/* ── pacing ────────────────────────────────────────────────────────────── */

static void
sleep_until(double t)
{
	struct timespec due;

	due.tv_sec = (time_t)t;
	due.tv_nsec = (long)((t - (double)due.tv_sec) * 1e9);
	while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &due, NULL) ==
	    EINTR) {
		if (cdin_stop || atomic_load(&cdin_io_abort))
			return;
	}
}

/*
 * A carrier dropout is a WIRE event, so it belongs here and not in the
 * producer: the point is that no frames arrive at all while the wall clock
 * keeps running, which is what drains the lead.  Shifting t0 by the duration
 * is what makes those frames *gone* rather than replayed in a burst, exactly
 * as they are gone on real hardware.
 */
static void
apply_due_dropout(struct filesrc *f)
{
	double now = (double)f->frames_out / (double)f->rate;
	int i;

	for (i = 0; i < f->nevents; i++) {
		struct event *ev = &f->events[i];
		double secs;

		if (ev->fired || ev->kind != EV_DROPOUT || ev->at > now)
			continue;
		ev->fired = true;
		secs = ev->arg / 1000.0;

		log_warn("transport: carrier dropout of %.0f ms — no frames on "
		    "the wire; the lead absorbs it", ev->arg);
		sleep_until(now_monotonic() + secs);
		f->t0 += secs;
		atomic_fetch_add(&f->dropouts, 1);
	}
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
		atomic_fetch_add(&f->slips, 1);
		log_debug("input: pacing slipped %.0f ms; resuming the schedule "
		    "from now rather than bursting to catch up", slip * 1000.0);
		return;
	}
	if (slip >= 0.0)
		return;				/* already due */

	sleep_until(t);
}

ssize_t
filesrc_read(struct filesrc *f, void *buf, size_t n)
{
	bool first = !f->started;
	size_t got;

	apply_due_dropout(f);
	pace(f);
	if (cdin_stop || atomic_load(&cdin_io_abort))
		return -1;

	/*
	 * Reading out of the staging ring, never off the medium: this call is
	 * on the clock and must not inherit disk latency.  If the ring is
	 * short here the medium genuinely could not sustain realtime, which is
	 * worth saying out loud — it is the one failure the rig can suffer
	 * that the real capture path cannot.  The first call is exempt: there
	 * is no deadline yet, so an empty stage means the producer has simply
	 * not run, not that it cannot keep up.
	 */
	if (!first && ring_fill(f->stage) < n && !atomic_load(&f->done)) {
		if (!f->stalled) {
			f->stalled = true;
			log_warn("input: the medium is not keeping up — "
			    "prefetch buffer ran dry (%llu times); the lead "
			    "will suffer, the daemon is not at fault",
			    (unsigned long long)
			    (atomic_fetch_add(&f->stalls, 1) + 1));
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
		return 0;			/* disc ended or carrier dropped */
	}
	f->frames_out += got / f->frame_bytes;
	return (ssize_t)got;
}
