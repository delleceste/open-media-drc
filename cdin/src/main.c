/*
 * omdrc-cdin — S/PDIF capture bridge into the open-media-drc chain.
 *
 * capture -> ring (holding a lead of a few seconds) -> playback.  No
 * resampler: the data path is a memcpy, so what reaches BruteFIR is
 * bit-identical to what the CD transport sent.
 *
 * The daemon is meant to stay running whether or not there is a disc, which is
 * what the NO_CARRIER/IDLE/PLAYING state machine below is for.  The output is
 * opened only while audio is actually on the wire and released again after a
 * long run of digital silence, so an idle CD input does not hold
 * /dev/dsp.play against MPD and mpv — and, on FreeBSD, is not an extra cuse
 * client handle for virtual_oss to wait on when drc.sh restarts it for a rate
 * change (see ../../VIRTUAL_OSS_CUSE_DEADLOCK.md).
 *
 *   NO_CARRIER  no frames arrive: the device is closed and retried
 *   IDLE        frames arrive and are all exact zeros: no output held
 *   PLAYING     audio on the wire: the output is open and being written
 *
 * Still to come: drift resync during the inter-track silence, marked
 * TODO(phase2b).
 */
#include <errno.h>
#include <getopt.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "carrier.h"
#include "cdin.h"
#include "convert.h"
#include "filesrc.h"
#include "gate.h"
#include "log.h"
#include "ossdev.h"
#include "outsel.h"
#include "ring.h"

volatile sig_atomic_t cdin_stop;
volatile sig_atomic_t cdin_io_abort;
volatile sig_atomic_t cdin_release;

#define TICK_MS 100		/* stats sampling period */

/*
 * What the wire is doing.  The distinction that matters is between a player
 * that has stopped sending frames (NO_CARRIER — the device is suspect and gets
 * reopened) and one that is sending frames of nothing (IDLE — the device is
 * fine, there is simply no music), because only the second can be told apart
 * from music by looking at the samples.
 */
enum cdin_state {
	CDIN_NO_CARRIER = 0,
	CDIN_IDLE,
	CDIN_PLAYING,
};

static const char *const state_name[] = {
	[CDIN_NO_CARRIER] = "no-carrier",
	[CDIN_IDLE]       = "idle",
	[CDIN_PLAYING]    = "playing",
};

struct config {
	const char *in_path;
	/* The preference list as given (see outsel.h): which device the disc is
	 * written to depends on whether the DRC chain is up, and that is not
	 * this daemon's to decide. */
	const char *out_list;
	/* The one the startup probe settled on, and the only one used from then
	 * on.  "none" = capture-only measurement. */
	const char *out_path;
	const char *log_path;
	int    rate;
	int    channels;
	int    bits;
	int    lead_ms;
	int    ring_ms;
	size_t period_frames;
	int    stats_secs;	/* 0 = no periodic stats */
	int    idle_after_ms;	/* silence before the output is released; 0 = never */
	int    carrier_min_pct;	/* % of rate below which the input is unclocked; 0 = off */
	int    retry_secs;
	int    out_bits;	/* 0 = negotiate with the device */
	double in_ppm;		/* file source only: simulated clock offset */
	int    gap_ms;		/* file source only: silence between tracks */
	const char *transport;	/* file source only: scripted transport events */
	bool   loop;
	bool   probe;
	enum cdin_loglevel level;
};

struct session {
	struct config  *cfg;
	struct ossdev  *in;
	struct ossdev  *out;
	struct filesrc *file;	/* non-NULL when the input is a WAV, not a device */
	struct ring    *ring;
	size_t          period_bytes;		/* ring/playback side (out format) */
	size_t          frame_bytes;		/* out frame size */
	size_t          in_period_bytes;	/* capture side (in format) */
	size_t          in_frame_bytes;
	size_t          lead_bytes;		/* the pre-fill an episode starts from */
	int             in_bits;
	int             out_bits;
	bool            measure_only;

	_Atomic uint64_t frames_in;
	_Atomic uint64_t frames_out;
	_Atomic uint64_t drop_bytes;
	_Atomic uint64_t starves;
	_Atomic uint64_t periods_total;
	_Atomic uint64_t periods_silent;
	_Atomic int      failed;
	_Atomic int      draining;	/* end of input: an empty ring is expected */
	_Atomic int      running;	/* playback has begun writing the device */
	_Atomic int      input_done;	/* source ended; no more frames will arrive */
	_Atomic int      state;		/* enum cdin_state */

	pthread_t        cap_tid;
	pthread_t        play_tid;
	bool             cap_running;
	bool             play_running;
};

/* ── helpers ───────────────────────────────────────────────────────────── */

static double
now_monotonic(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static void
sleep_ms(int ms)
{
	struct timespec ts = { .tv_sec = ms / 1000,
	                       .tv_nsec = (long)(ms % 1000) * 1000000L };

	nanosleep(&ts, NULL);
}

static void
on_signal(int sig)
{
	(void)sig;
	cdin_stop = 1;
}

/*
 * SIGHUP: give the output device back now, and do not take it again for a few
 * seconds.
 *
 * This is what drc.sh sends before it stops virtual_oss.  Holding a cuse
 * client handle open while the server exits is what wedges the teardown
 * (VIRTUAL_OSS_CUSE_DEADLOCK.md: cuse_server_free() spins until every client
 * handle is gone, uninterruptibly, and only a reboot recovers it), and a rate
 * change restarts virtual_oss under whatever is playing.  The hold-off is the
 * point: a disc that is still spinning would otherwise re-acquire the device
 * on the very next period, which is precisely the handle we just let go of.
 */
static void
on_release(int sig)
{
	(void)sig;
	cdin_release = 1;
}

/*
 * Delivered by pthread_kill() only to break a worker out of a blocking
 * read()/write().  It must do nothing: the EINTR is the whole point.
 */
static void
on_wake(int sig)
{
	(void)sig;
}

/* Digital silence on a CD is exactly zero, so no threshold is needed. */
static bool
buffer_is_silent(const unsigned char *buf, size_t n)
{
	const uint64_t *w = (const uint64_t *)(const void *)buf;
	size_t nw = n / sizeof(*w), i;
	uint64_t acc = 0;

	for (i = 0; i < nw; i++)
		acc |= w[i];
	for (i = nw * sizeof(*w); i < n; i++)
		acc |= buf[i];
	return acc == 0;
}

static double
bytes_to_ms(const struct session *s, size_t bytes)
{
	return (double)bytes / (double)s->frame_bytes /
	    (double)s->cfg->rate * 1000.0;
}

/*
 * Move to a new state, logging the transition once.  Every one of these lines
 * is a diagnostic the web panel reads back, so the shape is deliberate and
 * stable: "state <name>: <why>", with the name from state_name[] above.
 */
static void
set_state(struct session *s, enum cdin_state to, const char *why)
{
	enum cdin_state from = (enum cdin_state)atomic_exchange(&s->state, (int)to);

	if (from == to)
		return;
	if (to == CDIN_NO_CARRIER)
		log_warn("state %s: %s", state_name[to], why);
	else
		log_info("state %s: %s", state_name[to], why);
}

/* ── worker threads ────────────────────────────────────────────────────── */

static void *
capture_thread(void *arg)
{
	struct session *s = arg;
	struct silence_gate gate;
	struct carrier carrier;
	char why[160];
	unsigned char *buf;
	ssize_t rc;

	unsigned char *wide = NULL;
	bool widening = s->in_bits != s->out_bits;
	double hold_until = 0.0;
	/* Long enough to cover a drc.sh chain rebuild: it stops virtual_oss,
	   restarts it at the new rate and waits up to 5 s for the loopback
	   node to appear before brutefir is started on top. */
	const int hold_secs = s->cfg->retry_secs * 2 > 6
	    ? s->cfg->retry_secs * 2 : 6;

	if ((buf = malloc(s->in_period_bytes)) == NULL ||
	    (widening && (wide = malloc(s->period_bytes)) == NULL)) {
		log_err("capture: out of memory");
		atomic_store(&s->failed, 1);
		ring_shutdown(s->ring);
		free(buf);
		return NULL;
	}

	gate_init(&gate, s->cfg->idle_after_ms, s->cfg->rate);
	/* 2 s: long enough that a dead input (whose periods land seconds apart,
	   because that is what "dead" does to the read rate) still closes a
	   window, short enough that a disc starting loses nothing audible —
	   the lead it starts from is history out of the ring, not audio
	   recorded after the decision. */
	carrier_init(&carrier, s->cfg->rate, s->cfg->carrier_min_pct, 2000,
	    now_monotonic());

	while (!cdin_stop && !atomic_load(&s->failed)) {
		size_t frames;
		bool silent;

		if (cdin_release) {
			cdin_release = 0;
			hold_until = now_monotonic() + (double)hold_secs;
			gate_reset(&gate);
			carrier_reset(&carrier, now_monotonic());
			set_state(s, CDIN_IDLE, "release requested (SIGHUP)");
			log_info("holding off the output for %d s", hold_secs);
		}

		rc = s->file != NULL
		    ? filesrc_read(s->file, buf, s->in_period_bytes)
		    : ossdev_read_full(s->in, buf, s->in_period_bytes);
		if (rc < 0) {
			if (cdin_stop || cdin_io_abort)
				break;
			log_err("capture %s: read failed: %s", s->cfg->in_path,
			    strerror(errno));
			atomic_store(&s->failed, 1);
			break;
		}
		if ((size_t)rc < s->in_period_bytes) {
			if (s->file != NULL) {
				/* End of the test file: let the lead play out
				   rather than cutting it off, then stop. */
				if (rc > 0 && !s->measure_only) {
					size_t fr = (size_t)rc / s->in_frame_bytes;

					if (widening) {
						convert_widen(buf, s->in_bits,
						    wide, s->out_bits,
						    fr * (size_t)s->cfg->channels);
						ring_write(s->ring, wide,
						    fr * s->frame_bytes);
					} else {
						ring_write(s->ring, buf, (size_t)rc);
					}
				}
				atomic_store(&s->draining, 1);
				atomic_store(&s->input_done, 1);
				log_info("%s; draining %.0f ms of lead",
				    filesrc_carrier_lost(s->file)
				    ? "the carrier dropped"
				    : "end of the disc",
				    bytes_to_ms(s, ring_fill(s->ring)));
				/* Only an episode that is actually writing the
				   device can drain the ring; a disc that ended
				   while idle has nobody to hand it to. */
				while (!cdin_stop && atomic_load(&s->running) &&
				    ring_fill(s->ring) >= s->period_bytes)
					sleep_ms(20);
				cdin_stop = 1;
				break;
			}
			/*
			 * A short read means the frames stopped arriving: the
			 * coax was pulled, the interface went away, or the
			 * player dropped carrier rather than sending silence.
			 * That is NO_CARRIER — the device itself is suspect, so
			 * the session ends and the main loop reopens it.  A
			 * player that mutes instead keeps the frames coming and
			 * is handled by the silence gate below, without any of
			 * this.
			 */
			set_state(s, CDIN_NO_CARRIER, "short read on the capture "
			    "device — the carrier is gone");
			log_warn("capture %s: short read (%zd of %zu bytes)",
			    s->in->path, rc, s->in_period_bytes);
			atomic_store(&s->failed, 1);
			break;
		}

		frames = (size_t)rc / s->in_frame_bytes;
		silent = buffer_is_silent(buf, (size_t)rc);
		atomic_fetch_add(&s->periods_total, 1);
		if (silent)
			atomic_fetch_add(&s->periods_silent, 1);

		/*
		 * The period goes into the ring BEFORE the state moves on it.
		 * The ring runs as a rolling window whether or not anything is
		 * playing, and an episode begins by trimming it to the lead —
		 * so the period that carried the first sample of music has to
		 * be in there already, or starting playback would discard the
		 * very sample that started it.
		 */
		if (!s->measure_only) {
			size_t dropped;

			if (widening) {
				convert_widen(buf, s->in_bits, wide, s->out_bits,
				    frames * (size_t)s->cfg->channels);
				dropped = ring_write(s->ring, wide,
				    frames * s->frame_bytes);
			} else {
				dropped = ring_write(s->ring, buf, (size_t)rc);
			}
			/*
			 * Dropping the oldest audio out of a rolling window is
			 * that window working, not audio being lost.  It is a
			 * discontinuity only if somebody is draining the ring
			 * at the other end, which is what `running` means —
			 * PLAYING alone is not enough, because that is a fact
			 * about the WIRE (a disc is spinning), not about the
			 * output device.
			 *
			 * The gap between the two is not small: while the
			 * output is unavailable — MPD holding the DAC, say —
			 * the state is PLAYING for as long as the retries take
			 * and the ring wraps over and over.  Counting those
			 * would report megabytes of "discarded audio, one
			 * discontinuity per drop" for a stretch in which
			 * nothing played and nothing could have.  Every byte
			 * of it was going to be discarded anyway: acquiring
			 * the device begins by trimming the ring back to one
			 * lead.  The news there is the unavailability, which
			 * is already logged as the error it is.
			 */
			if (dropped > 0 &&
			    atomic_load(&s->state) == CDIN_PLAYING &&
			    atomic_load(&s->running))
				atomic_fetch_add(&s->drop_bytes, dropped);
		}
		atomic_fetch_add(&s->frames_in, (uint64_t)frames);

		/*
		 * Is the input still being clocked?  This is asked before the
		 * silence gate because it can overrule it: an unclocked input
		 * delivers non-zero rubbish, which the gate reads as music.
		 * See carrier.h — this is the check that ends the dribble.
		 */
		switch (carrier_feed(&carrier, frames, now_monotonic())) {
		case CARRIER_LOST:
			snprintf(why, sizeof(why), "%.0f frames/s against %d "
			    "nominal — the transport is stopped or the cable "
			    "is out", carrier_hz(&carrier), s->cfg->rate);
			/* Not `failed`: unlike a short read, nothing is wrong
			   with the device, so there is nothing to reopen.  The
			   frames keep being read and the ring keeps rolling;
			   only the output is given back. */
			set_state(s, CDIN_NO_CARRIER, why);
			break;
		case CARRIER_BACK:
			/* Clocked again, but not necessarily playing: a disc
			   sitting in a pause is clocked and silent, and it is
			   the gate below that promotes it to PLAYING. */
			if (atomic_load(&s->state) == CDIN_NO_CARRIER) {
				snprintf(why, sizeof(why), "%.0f frames/s — "
				    "the input is clocked again",
				    carrier_hz(&carrier));
				set_state(s, CDIN_IDLE, why);
			}
			break;
		case CARRIER_SAME:
			break;
		}

		switch (gate_feed(&gate, silent, frames)) {
		case GATE_AUDIO:
			if (now_monotonic() < hold_until)
				break;		/* SIGHUP hold-off */
			/* Non-zero samples off an unclocked wire are not a
			   stream, and starting an episode on them is what held
			   the output device for hours. */
			if (!carrier_live(&carrier))
				break;
			if (atomic_load(&s->state) != CDIN_PLAYING) {
				/*
				 * Trim here, on this thread, BEFORE the state
				 * moves.  The ring has been rolling through the
				 * silence and is full of it, so the lead is
				 * already buffered and the trim is what turns
				 * that history into the pre-fill.
				 *
				 * Doing it on the playback thread instead left
				 * a window — a few tens of ms — in which this
				 * thread was writing into a FULL ring with the
				 * state already PLAYING, and every one of those
				 * writes was counted as a drop.  The audio was
				 * stale silence and no listener could have
				 * heard the difference, but `drops` is what a
				 * discontinuity is read off: it threw away the
				 * drift reference at the start of every single
				 * episode.
				 */
				ring_keep_last(s->ring, s->lead_bytes);
				set_state(s, CDIN_PLAYING,
				    "audio on the wire");
			}
			break;
		case GATE_IDLE:
			if (atomic_load(&s->state) == CDIN_PLAYING)
				set_state(s, CDIN_IDLE,
				    "digital silence long enough to release "
				    "the output");
			break;
		case GATE_SILENT:
			break;
		}
	}

	free(buf);
	free(wide);
	ring_shutdown(s->ring);
	return NULL;
}

/*
 * One playing episode: hold the output device, write the ring to it, and give
 * it back when the input goes quiet.  Returns 0 when the episode ended because
 * the input went idle (the ordinary case) and -1 when it ended on an error.
 */
static int
play_episode(struct session *s, unsigned char *buf, size_t lead_bytes,
    size_t lowwater)
{
	bool starving = false;
	size_t trimmed;
	ssize_t rc;

	/*
	 * The capture thread already trimmed the ring to the lead when it made
	 * the transition; this catches the other case, where opening the output
	 * had to be retried for seconds and the ring filled again while we
	 * waited.  Those seconds could not have been played, so they are not
	 * audio being thrown away.  A no-op in the ordinary path.
	 */
	trimmed = ring_keep_last(s->ring, lead_bytes);
	if (trimmed > 0)
		log_debug("playback: trimmed %.0f ms while the output was "
		    "unavailable, lead now %.0f ms", bytes_to_ms(s, trimmed),
		    bytes_to_ms(s, ring_fill(s->ring)));

	while (!cdin_stop && !atomic_load(&s->failed) &&
	    ring_fill(s->ring) < lead_bytes) {
		if (atomic_load(&s->input_done)) {
			log_warn("playback: input ended after %.0f ms, short of "
			    "the %d ms lead — playing out what arrived",
			    bytes_to_ms(s, ring_fill(s->ring)), s->cfg->lead_ms);
			break;
		}
		if (atomic_load(&s->state) != CDIN_PLAYING)
			return 0;	/* silence returned before we started */
		sleep_ms(20);
	}
	if (cdin_stop || atomic_load(&s->failed))
		return 0;

	log_info("playback: lead reached (%.0f ms buffered), starting",
	    bytes_to_ms(s, ring_fill(s->ring)));
	atomic_store(&s->running, 1);

	while (!cdin_stop && !atomic_load(&s->failed)) {
		size_t fill = ring_fill(s->ring);

		/*
		 * Leaving PLAYING is a decision the capture side has already
		 * made after a long run of exact zeros, so what is left in the
		 * ring is that same silence: there is nothing to drain, and
		 * writing it out would only hold the device longer.
		 */
		if (atomic_load(&s->state) != CDIN_PLAYING)
			break;

		/* Edge-triggered so one starvation event counts once. */
		if (fill < lowwater && !starving && !atomic_load(&s->draining)) {
			starving = true;
			atomic_fetch_add(&s->starves, 1);
			log_warn("playback: lead down to %.0f ms", bytes_to_ms(s, fill));
		} else if (fill >= lowwater * 2) {
			starving = false;
		}

		if (ring_read(s->ring, buf, s->period_bytes) == 0)
			break;		/* ring shut down */

		rc = ossdev_write_full(s->out, buf, s->period_bytes);
		if (rc < 0) {
			if (errno == EINTR && cdin_stop)
				break;
			log_err("playback %s: write failed: %s", s->out->path,
			    strerror(errno));
			return -1;
		}
		if ((size_t)rc < s->period_bytes) {
			log_err("playback %s: short write (%zd of %zu bytes)",
			    s->out->path, rc, s->period_bytes);
			return -1;
		}
		atomic_fetch_add(&s->frames_out, (uint64_t)rc / s->frame_bytes);
	}
	return 0;
}

static void *
playback_thread(void *arg)
{
	struct session *s = arg;
	size_t lead_bytes, lowwater;
	unsigned char *buf;
	char err[256];
	bool warned = false;

	if ((buf = malloc(s->period_bytes)) == NULL) {
		log_err("playback: out of memory");
		atomic_store(&s->failed, 1);
		return NULL;
	}

	lead_bytes = s->lead_bytes;
	lowwater = s->period_bytes * 2;

	while (!cdin_stop && !atomic_load(&s->failed)) {
		if (atomic_load(&s->state) != CDIN_PLAYING) {
			sleep_ms(TICK_MS / 2);
			continue;
		}

		/*
		 * The lazy open, and the whole point of the state machine: the
		 * output is held only while a disc is actually playing, so an
		 * idle CD input leaves /dev/dsp.play to MPD and mpv — and, on
		 * FreeBSD, is not an extra cuse client handle for virtual_oss
		 * to wait on when drc.sh tears it down for a rate change.
		 *
		 * The width was settled once at startup.  It is not
		 * renegotiated here: the ring is laid out in that width, so a
		 * different answer now would not fit the buffer the frames are
		 * already in.  A device that has genuinely changed width (a
		 * virtual_oss started with another -b) says so in this error
		 * and wants the daemon restarted.
		 */
		if (ossdev_open(s->out, s->cfg->out_path, false, s->cfg->rate,
		    s->cfg->channels, s->out_bits, s->cfg->period_frames,
		    err, sizeof(err)) != 0) {
			if (!warned) {
				log_err("playback %s: unavailable — %s "
				    "(retrying every %d s)", s->cfg->out_path,
				    err, s->cfg->retry_secs);
				warned = true;
			}
			sleep_ms(s->cfg->retry_secs * 1000);
			continue;
		}
		if (warned) {
			log_info("playback %s: available", s->cfg->out_path);
			warned = false;
		}
		log_info("playback %s: acquired", s->cfg->out_path);

		if (play_episode(s, buf, lead_bytes, lowwater) != 0)
			atomic_store(&s->failed, 1);

		atomic_store(&s->running, 0);
		ossdev_close(s->out);
		log_info("playback %s: released", s->cfg->out_path);
	}

	free(buf);
	atomic_store(&s->running, 0);
	if (s->out->fd != -1) {
		ossdev_close(s->out);
		log_info("playback %s: released", s->cfg->out_path);
	}
	return NULL;
}

/* ── stats ─────────────────────────────────────────────────────────────── */

struct stats_state {
	double   t0;		/* session start */
	double   t_run;		/* when playback began writing */
	uint64_t in_at_run;	/* counter snapshots at that instant */
	uint64_t out_at_run;
	double   t_rate;	/* rate window: re-based on every discontinuity */
	uint64_t in_at_rate;
	uint64_t out_at_rate;
	bool     run_seen;
	unsigned clean_intervals;	/* emitted intervals fully after t_run */
	double   ref_t;		/* reference for the drift estimate */
	double   ref_lead_ms;
	bool     ref_set;
	uint64_t last_starves;	/* discontinuity detectors (see stats_emit) */
	uint64_t last_drops;
	double   last_mean_ms;
	bool     have_last_mean;
	double   sum_lead_ms;
	double   min_lead_ms;
	double   max_lead_ms;
	unsigned samples;
	uint64_t last_periods, last_silent;
};

static void
stats_reset_interval(struct stats_state *st)
{
	st->sum_lead_ms = 0.0;
	st->min_lead_ms = 1e18;
	st->max_lead_ms = -1e18;
	st->samples = 0;
}

static void
stats_emit(struct session *s, struct stats_state *st)
{
	double now = now_monotonic(), mean, elapsed, rate_elapsed;
	double in_hz, out_hz, step, step_ms;
	uint64_t fin, fout, periods, silent, starves, drops;
	char drift[96], silencetxt[32], rig[64];
	bool discontinuity;

	if (st->samples == 0)
		return;

	mean = st->sum_lead_ms / st->samples;
	fin = atomic_load(&s->frames_in);
	fout = atomic_load(&s->frames_out);

	/*
	 * Both rates are measured from the instant playback began, not from
	 * session start: the pre-fill seconds carry input frames but no output
	 * frames, and that one-off asymmetry biases the output rate low for
	 * minutes (a 2 s pre-fill is still a 2% error at 100 s).  The window is
	 * then re-based on every discontinuity below, for the same reason the
	 * drift reference is: an 800 ms dropout leaves a cumulative average
	 * reading low for the rest of the session, long after the event that
	 * caused it has scrolled off.
	 */
	elapsed = st->run_seen ? now - st->t_run : now - st->t0;
	rate_elapsed = st->run_seen ? now - st->t_rate : elapsed;
	if (elapsed <= 0 || rate_elapsed <= 0)
		return;
	in_hz = (double)(fin - st->in_at_rate) / rate_elapsed;
	out_hz = (double)(fout - st->out_at_rate) / rate_elapsed;

	periods = atomic_load(&s->periods_total);
	silent = atomic_load(&s->periods_silent);
	if (periods > st->last_periods) {
		snprintf(silencetxt, sizeof(silencetxt), "%.0f%%",
		    100.0 * (double)(silent - st->last_silent) /
		    (double)(periods - st->last_periods));
	} else {
		snprintf(silencetxt, sizeof(silencetxt), "n/a");
	}
	st->last_periods = periods;
	st->last_silent = silent;

	/*
	 * Drift is measured from the CHANGE in lead, not from the frame
	 * counters: the counters include each device's constant buffer offset,
	 * which would swamp a ppm-scale figure, whereas that offset cancels in
	 * a difference.  d(lead)/dt is exactly (f_capture - f_playback)/f.
	 *
	 * The lead is quantised by one period, so the estimate needs minutes to
	 * settle; the reported +/- is that quantisation over the elapsed time.
	 */
	/*
	 * Drift is only readable off an UNBROKEN lead trace.  A starve, a ring
	 * overflow, or a step in the lead too large to be drift all mean the
	 * lead moved for a reason that is not the clocks, and measuring across
	 * such an event reports the step rather than the drift: a 3.3 s jump
	 * inside a 60 s window once read as +54361 ppm.  Any of them drops the
	 * reference and starts the measurement again.
	 *
	 * The step threshold is four periods, floored at 50 ms.  Real drift
	 * moves the lead by microseconds per second, so nothing short of a
	 * discontinuity can reach it.
	 */
	starves = atomic_load(&s->starves);
	drops = atomic_load(&s->drop_bytes);
	step_ms = 4.0 * bytes_to_ms(s, s->period_bytes);
	if (step_ms < 50.0)
		step_ms = 50.0;
	step = st->have_last_mean
	    ? (mean > st->last_mean_ms ? mean - st->last_mean_ms
	                               : st->last_mean_ms - mean)
	    : 0.0;
	discontinuity = starves != st->last_starves ||
	    drops != st->last_drops || step > step_ms;
	st->last_starves = starves;
	st->last_drops = drops;
	st->last_mean_ms = mean;
	st->have_last_mean = true;

	if (discontinuity) {
		st->ref_set = false;
		st->clean_intervals = 0;
		st->t_rate = now;		/* the rate window restarts too */
		st->in_at_rate = fin;
		st->out_at_rate = fout;
		snprintf(drift, sizeof(drift), "drift ref dropped (lead jumped)");
	} else if (!st->ref_set) {
		/*
		 * Skip the first clean interval before anchoring: the lead is
		 * still settling right after playback starts, and anchoring on
		 * a settling value turns the whole estimate into an artefact of
		 * the transient rather than a measurement of the clocks.
		 */
		if (++st->clean_intervals < 2) {
			snprintf(drift, sizeof(drift), "drift settling (anchor)");
		} else {
			st->ref_set = true;
			st->ref_t = now;
			st->ref_lead_ms = mean;
			snprintf(drift, sizeof(drift), "drift ref set");
		}
	} else {
		double dt = now - st->ref_t;

		if (dt >= 60.0) {
			double ppm = (mean - st->ref_lead_ms) * 1000.0 / dt;
			double period_ms = bytes_to_ms(s, s->period_bytes);
			double err = period_ms * 1000.0 / dt;
			double lead_s = mean / 1000.0;
			double hours;

			if (ppm < -0.001) {
				hours = lead_s / (-ppm * 1e-6) / 3600.0;
				snprintf(drift, sizeof(drift),
				    "drift %+.1f ppm (+/-%.1f), lead drains in %.0f h",
				    ppm, err, hours);
			} else if (ppm > 0.001) {
				double head_s =
				    (bytes_to_ms(s, ring_capacity(s->ring)) - mean) / 1000.0;

				hours = head_s / (ppm * 1e-6) / 3600.0;
				snprintf(drift, sizeof(drift),
				    "drift %+.1f ppm (+/-%.1f), ring fills in %.0f h",
				    ppm, err, hours);
			} else {
				snprintf(drift, sizeof(drift), "drift ~0 ppm");
			}
		} else {
			snprintf(drift, sizeof(drift),
			    "drift settling (%.0f s of %.0f)", dt, 60.0);
		}
	}

	/*
	 * Simulated-transport accounting, reported only when there is something
	 * to say.  dropouts are deliberate (the script asked for them); stalls
	 * and slips are the rig failing to be fed, which is a fault of neither
	 * the daemon nor the design and must not be read as one.
	 */
	rig[0] = '\0';
	if (s->file != NULL) {
		uint64_t st_stalls = filesrc_stalls(s->file);
		uint64_t st_slips = filesrc_slips(s->file);
		uint64_t st_drops = filesrc_dropouts(s->file);

		if (st_drops > 0 && st_stalls == 0 && st_slips == 0)
			snprintf(rig, sizeof(rig), "  dropouts %llu",
			    (unsigned long long)st_drops);
		else if (st_stalls > 0 || st_slips > 0)
			snprintf(rig, sizeof(rig), "  dropouts %llu  "
			    "rig stalls %llu slips %llu",
			    (unsigned long long)st_drops,
			    (unsigned long long)st_stalls,
			    (unsigned long long)st_slips);
	}

	if (s->measure_only) {
		log_info("[stats] capture-only  in %.3f Hz  frames %llu  "
		    "silence %s  up %.0f s%s", in_hz,
		    (unsigned long long)fin, silencetxt, elapsed, rig);
	} else {
		log_info("[stats] lead %.0f ms (min %.0f, max %.0f)  %s  "
		    "in %.3f Hz  out %.3f Hz  frames %llu/%llu  "
		    "drops %llu B  starves %llu  silence %s  up %.0f s%s",
		    mean, st->min_lead_ms, st->max_lead_ms, drift, in_hz, out_hz,
		    (unsigned long long)fin, (unsigned long long)fout,
		    (unsigned long long)atomic_load(&s->drop_bytes),
		    (unsigned long long)atomic_load(&s->starves),
		    silencetxt, elapsed, rig);
	}
	stats_reset_interval(st);
}

/* ── session ───────────────────────────────────────────────────────────── */

/* Runs until shutdown or a device error.  Returns 0 on clean stop, -1 if the
   session failed and the devices should be reopened. */
static int
run_session(struct session *s)
{
	struct stats_state st;
	int ticks_per_stat;

	ring_reset(s->ring);
	atomic_store(&s->failed, 0);
	atomic_store(&s->frames_in, 0);
	atomic_store(&s->frames_out, 0);
	atomic_store(&s->drop_bytes, 0);
	atomic_store(&s->starves, 0);
	atomic_store(&s->periods_total, 0);
	atomic_store(&s->periods_silent, 0);
	atomic_store(&s->draining, 0);
	atomic_store(&s->running, 0);
	atomic_store(&s->input_done, 0);

	memset(&st, 0, sizeof(st));
	st.t0 = now_monotonic();
	stats_reset_interval(&st);

	cdin_io_abort = 0;
	s->cap_running = s->play_running = false;

	if (pthread_create(&s->cap_tid, NULL, capture_thread, s) != 0) {
		log_err("cannot create the capture thread: %s", strerror(errno));
		return -1;
	}
	s->cap_running = true;

	if (!s->measure_only) {
		if (pthread_create(&s->play_tid, NULL, playback_thread, s) != 0) {
			log_err("cannot create the playback thread: %s",
			    strerror(errno));
			atomic_store(&s->failed, 1);
		} else {
			s->play_running = true;
		}
	}

	ticks_per_stat = s->cfg->stats_secs > 0
	    ? s->cfg->stats_secs * 1000 / TICK_MS : 0;

	while (!cdin_stop && !atomic_load(&s->failed)) {
		double lead = bytes_to_ms(s, ring_fill(s->ring));

		/* Nothing before playback starts is a measurement: the lead is
		   ramping from zero to the target by construction. */
		if (!st.run_seen) {
			if (!atomic_load(&s->running) && !s->measure_only) {
				sleep_ms(TICK_MS);
				continue;
			}
			st.run_seen = true;
			st.t_run = st.t_rate = now_monotonic();
			st.in_at_run = st.in_at_rate = atomic_load(&s->frames_in);
			st.out_at_run = st.out_at_rate = atomic_load(&s->frames_out);
			stats_reset_interval(&st);
		} else if (!atomic_load(&s->running) && !s->measure_only) {
			/*
			 * The episode ended and the output was closed.  Every
			 * figure here is a rate or a difference measured against
			 * an open device, so none of them survives that: emit
			 * what the episode came to, then start over rather than
			 * carrying a closed device's numbers into the next one.
			 */
			stats_emit(s, &st);
			memset(&st, 0, sizeof(st));
			st.t0 = now_monotonic();
			stats_reset_interval(&st);
			sleep_ms(TICK_MS);
			continue;
		}

		st.sum_lead_ms += lead;
		if (lead < st.min_lead_ms)
			st.min_lead_ms = lead;
		if (lead > st.max_lead_ms)
			st.max_lead_ms = lead;
		st.samples++;

		if (ticks_per_stat > 0 && st.samples >= (unsigned)ticks_per_stat)
			stats_emit(s, &st);
		sleep_ms(TICK_MS);
	}

	/*
	 * Tear down in an order that cannot touch a closed device: tell the
	 * transfers to give up on EINTR, wake both threads out of whatever
	 * syscall they are parked in, then JOIN them.  Only after the join is
	 * it safe for the caller to close the devices or free the ring.
	 */
	cdin_io_abort = 1;
	ring_shutdown(s->ring);
	if (s->cap_running)
		pthread_kill(s->cap_tid, SIGUSR1);
	if (s->play_running)
		pthread_kill(s->play_tid, SIGUSR1);
	if (s->cap_running)
		pthread_join(s->cap_tid, NULL);
	if (s->play_running)
		pthread_join(s->play_tid, NULL);
	s->cap_running = s->play_running = false;
	cdin_io_abort = 0;

	return atomic_load(&s->failed) ? -1 : 0;
}

/* ── startup ───────────────────────────────────────────────────────────── */

/*
 * Open the playback device at a width it will actually accept.
 *
 * With bitperfect=1 the format feeder is gone, so the device rejects anything
 * but its native width — and a CD is 16-bit while the DRC chain runs S32_LE.
 * Try the source width first (no conversion is always truest), then wider ones,
 * never narrower: widening is lossless byte placement, narrowing is not.
 */
static int
open_playback_negotiated(struct ossdev *d, const struct config *cfg,
    const char *path, int in_bits, char *err, size_t errsz)
{
	char first_err[256] = "";
	int candidates[3], n = 0, i;

	if (cfg->out_bits != 0) {
		candidates[n++] = cfg->out_bits;	/* forced by --out-bits */
	} else {
		candidates[n++] = in_bits;
		if (in_bits != 32)
			candidates[n++] = 32;
		if (in_bits != 24 && in_bits < 24)
			candidates[n++] = 24;
	}

	for (i = 0; i < n; i++) {
		if (i > 0 && !convert_can_widen(in_bits, candidates[i]))
			continue;
		if (ossdev_open(d, path, false, cfg->rate,
		    cfg->channels, candidates[i], cfg->period_frames, err,
		    errsz) == 0) {
			if (candidates[i] != in_bits)
				log_info("output opened at %d-bit; widening "
				    "%d -> %d bit losslessly (left-justified, "
				    "no arithmetic)", candidates[i], in_bits,
				    candidates[i]);
			return 0;
		}
		/*
		 * Keep the FIRST failure to report.  The later candidates are
		 * fallbacks, and their complaints are about themselves: a
		 * 24-bit period is 6144 bytes, which the OSS fragment encoding
		 * rejects as not a power of two before the device is even
		 * touched.  Returning that one would answer "why can I not
		 * reach /dev/dsp.play?" with an arithmetic remark about a
		 * width nobody asked for.
		 */
		if (first_err[0] == '\0')
			snprintf(first_err, sizeof(first_err), "%s", err);
		if (i + 1 < n)
			log_debug("playback %s at %d-bit: %s — trying wider",
			    path, candidates[i], err);
	}
	snprintf(err, errsz, "%s", first_err);
	return -1;
}

static void
usage(FILE *out)
{
	fprintf(out,
"omdrc-cdin " CDIN_VERSION " — bridge an S/PDIF capture device into the DRC chain\n"
"\n"
"usage: omdrc-cdin [options]\n"
"\n"
"  -i, --in dev       capture device               (default /dev/dsp.capture)\n"
"                     The role symlink omdrc_audio keeps on the capture\n"
"                     card; pcm unit numbers move with USB attach order.\n"
"  -o, --out list     playback device, or a comma-separated preference\n"
"                     list, or 'none' for a capture-only measurement\n"
"                     (default /dev/dsp.play,/dev/dsp.dac)\n"
"                     /dev/dsp.play is the virtual_oss loopback BruteFIR\n"
"                     reads, and only exists while the DRC chain is up;\n"
"                     /dev/dsp.dac is the DAC itself, which is the right\n"
"                     target when DRC is off.  The list is tried in order\n"
"                     at startup and the first device that opens is kept\n"
"                     for the life of the process -- the ring is laid out\n"
"                     in the width that device negotiated.  drc.sh\n"
"                     restarts the daemon when the chain comes up or goes\n"
"                     down, which is how the choice is re-taken.\n"
"  -r, --rate hz      sample rate                             (default 44100)\n"
"  -c, --channels n   channel count                               (default 2)\n"
"  -b, --bits n       source width: 16, 24 or 32                 (default 32)\n"
"      --out-bits n   force the output width instead of negotiating it.  By\n"
"                     default the output is opened at the source width, and\n"
"                     if the device refuses (bitperfect=1 removes the format\n"
"                     feeder, and a CD is 16-bit while the chain runs S32_LE)\n"
"                     a wider one is used with lossless left-justification.\n"
"  -L, --lead ms      target lead to buffer                 (default 2000 ms)\n"
"                     The one number that matters: it is simultaneously the\n"
"                     drift margin, the startup delay, and the lag on every\n"
"                     CD transport control.  At 50 ppm a 2000 ms lead covers\n"
"                     ~11 h of gapless audio, so drift cannot bite inside a\n"
"                     disc; the lower bound is set by transport seeks and USB\n"
"                     stalls, not by drift.  Tune down while watching\n"
"                     'starves' in the stats — see README.md.\n"
"  -B, --ring ms      ring capacity                        (default 8000 ms)\n"
"  -p, --period n     period in frames; the byte count must be a power of\n"
"                     two                                    (default 1024)\n"
"  -s, --stats secs   stats interval                              (default 5)\n"
"  -R, --retry secs   device retry interval                       (default 2)\n"
"      --idle-after ms  digital silence on the wire before the output device\n"
"                     is released, so an idle CD input stops holding\n"
"                     /dev/dsp.play against MPD and mpv       (default 15000)\n"
"                     Must stay well clear of the 2 s Red Book inter-track\n"
"                     pause, or a disc would tear its own output down between\n"
"                     tracks.  0 disables it: the output is then held for the\n"
"                     whole run.\n"
"      --carrier-min pct  percentage of the nominal rate below which the\n"
"                     input counts as unclocked and the output is released\n"
"                                                               (default 50)\n"
"                     This is the OTHER way a player goes quiet.  A pause\n"
"                     keeps the carrier up and sends zeros (--idle-after\n"
"                     handles that one); a stop drops the carrier, and an\n"
"                     interface that slaves its clock to the carrier then\n"
"                     dribbles a few hundred non-zero frames a second instead\n"
"                     of the full rate.  Reads stay full and samples stay\n"
"                     non-zero, so nothing else notices, and the trickle is\n"
"                     audible as a burst each time the output buffer fills.\n"
"                     The gap is huge (~1%% of rate observed against a 50%%\n"
"                     threshold), so this needs no tuning; 0 disables it.\n"
"  -l, --log file     also log to this file\n"
"  -d, --debug        emit periodic stats\n"
"  -v, --verbose      more verbose; repeat for debug\n"
"  -P, --probe        open both devices, report, exit\n"
"\n"
" SIGHUP releases the output device and does not re-acquire it for a few\n"
" seconds.  drc.sh sends it before stopping virtual_oss: an open cuse client\n"
" handle is what wedges that teardown, and a rate change restarts virtual_oss\n"
" under whatever happens to be playing.\n"
"\n"
" Simulating a CD transport instead of a capture device: point --in at a WAV\n"
" file, or at a DIRECTORY of them to play a whole disc in name order.  The\n"
" source is paced on a monotonic deadline exactly as real hardware delivers,\n"
" and its rate/width/channels are adopted for the whole chain.\n"
"      --gap ms       exact digital silence between tracks (default 2000, the\n"
"                     Red Book pause).  0 makes it a gapless disc.  This is\n"
"                     what the silence gate sees, so a gap longer than\n"
"                     --idle-after is how you test that the output is really\n"
"                     released and reacquired.\n"
"      --in-ppm n     offset the source's pace, in ppm.  This is the drift\n"
"                     rig: +n grows the lead, -n drains it.  Real hardware\n"
"                     differs by a few ppm and takes hours to show;\n"
"                     --in-ppm -5000 drains a 2000 ms lead in ~400 s.\n"
"      --transport s  script the buttons, as AT:EVENT separated by commas,\n"
"                     AT being seconds into the stream:\n"
"                       skip / prev       change track, after the mute a real\n"
"                                         sled makes\n"
"                       seek=[+-]N        fast forward / rewind N seconds\n"
"                       pause=N           N s of digital silence; the carrier\n"
"                                         stays up and the position is held\n"
"                       dropout=N         the carrier drops for N MS and no\n"
"                                         frames arrive.  The one event that\n"
"                                         eats the lead, and the hazard the\n"
"                                         lead exists to absorb\n"
"                       stop              the carrier drops for good\n"
"                     e.g. --transport 30:skip,45:pause=4,60:dropout=800\n"
"      --loop         restart the disc at the end\n"
"  -h, --help         this help\n"
"  -V, --version      version\n");
}

int
main(int argc, char **argv)
{
	struct config cfg = {
		.in_path = "/dev/dsp.capture",
		.out_list = "/dev/dsp.play,/dev/dsp.dac",
		.rate = 44100,
		.channels = 2,
		.bits = 32,
		.lead_ms = 2000,
		.ring_ms = 8000,
		.period_frames = 1024,
		.stats_secs = 0,
		.retry_secs = 2,
		.idle_after_ms = 15000,
		.carrier_min_pct = 50,
		.gap_ms = 2000,
		.level = CDL_INFO,
	};
	struct ossdev in, out;
	struct session s;
	struct sigaction sa;
	struct stat stbuf;
	char err[256];
	int verbose = 0, opt, rc = 0;
	int out_bits = 0;		/* settled once by the probe below */
	size_t out_frame_bytes = 0;
	struct outsel outs;
	char outdesc[256];
	bool warned_in = false, warned_out = false;

	static const struct option longopts[] = {
		{ "in",       required_argument, NULL, 'i' },
		{ "out",      required_argument, NULL, 'o' },
		{ "rate",     required_argument, NULL, 'r' },
		{ "channels", required_argument, NULL, 'c' },
		{ "bits",     required_argument, NULL, 'b' },
		{ "lead",     required_argument, NULL, 'L' },
		{ "ring",     required_argument, NULL, 'B' },
		{ "period",   required_argument, NULL, 'p' },
		{ "stats",    required_argument, NULL, 's' },
		{ "retry",    required_argument, NULL, 'R' },
		{ "log",      required_argument, NULL, 'l' },
		{ "debug",    no_argument,       NULL, 'd' },
		{ "verbose",  no_argument,       NULL, 'v' },
		{ "out-bits", required_argument, NULL, 1002 },
		{ "in-ppm",   required_argument, NULL, 1000 },
		{ "loop",     no_argument,       NULL, 1001 },
		{ "gap",      required_argument, NULL, 1003 },
		{ "idle-after", required_argument, NULL, 1005 },
		{ "carrier-min", required_argument, NULL, 1006 },
		{ "transport", required_argument, NULL, 1004 },
		{ "probe",    no_argument,       NULL, 'P' },
		{ "help",     no_argument,       NULL, 'h' },
		{ "version",  no_argument,       NULL, 'V' },
		{ NULL, 0, NULL, 0 }
	};

	while ((opt = getopt_long(argc, argv, "i:o:r:c:b:L:B:p:s:R:l:dvPhV",
	    longopts, NULL)) != -1) {
		switch (opt) {
		case 'i': cfg.in_path = optarg; break;
		case 'o': cfg.out_list = optarg; break;
		case 'r': cfg.rate = atoi(optarg); break;
		case 'c': cfg.channels = atoi(optarg); break;
		case 'b': cfg.bits = atoi(optarg); break;
		case 'L': cfg.lead_ms = atoi(optarg); break;
		case 'B': cfg.ring_ms = atoi(optarg); break;
		case 'p': cfg.period_frames = (size_t)atol(optarg); break;
		case 's': cfg.stats_secs = atoi(optarg); break;
		case 'R': cfg.retry_secs = atoi(optarg); break;
		case 'l': cfg.log_path = optarg; break;
		case 'd': if (cfg.stats_secs == 0) cfg.stats_secs = 5; verbose++; break;
		case 'v': verbose++; break;
		case 1002: cfg.out_bits = atoi(optarg); break;
		case 1000: cfg.in_ppm = atof(optarg); break;
		case 1001: cfg.loop = true; break;
		case 1003: cfg.gap_ms = atoi(optarg); break;
		case 1005: cfg.idle_after_ms = atoi(optarg); break;
		case 1006: cfg.carrier_min_pct = atoi(optarg); break;
		case 1004: cfg.transport = optarg; break;
		case 'P': cfg.probe = true; break;
		case 'h': usage(stdout); return 0;
		case 'V': printf("omdrc-cdin " CDIN_VERSION "\n"); return 0;
		default:  usage(stderr); return 2;
		}
	}

	if (verbose >= 2)
		cfg.level = CDL_DEBUG;
	cdin_log_init(cfg.log_path, cfg.level);

	if (cfg.rate <= 0 || cfg.channels <= 0) {
		log_err("rate and channel count must be positive");
		return 2;
	}
	if (cfg.lead_ms < 250) {
		log_warn("a %d ms lead leaves nothing to absorb a transport "
		    "seek or a USB stall; 1000-3000 ms is the useful range",
		    cfg.lead_ms);
	}
	/*
	 * Red Book puts 2 s of exact zeros between tracks and the rig can be
	 * told to put more.  A gate shorter than that releases the output in
	 * the gap and re-acquires it for the next track, which costs a lead's
	 * delay every few minutes — the one failure mode this threshold has.
	 */
	if (cfg.idle_after_ms > 0 && cfg.idle_after_ms < 3000) {
		log_warn("--idle-after %d ms is close to the 2 s Red Book "
		    "inter-track pause; the output would be released and "
		    "re-acquired between tracks", cfg.idle_after_ms);
	}
	if (cfg.ring_ms <= cfg.lead_ms) {
		log_warn("ring capacity (%d ms) must exceed the lead (%d ms); "
		    "raising it to %d ms", cfg.ring_ms, cfg.lead_ms,
		    cfg.lead_ms * 4);
		cfg.ring_ms = cfg.lead_ms * 4;
	}

	memset(&sa, 0, sizeof(sa));
	sa.sa_handler = on_signal;	/* no SA_RESTART: blocking I/O must break */
	sigaction(SIGINT, &sa, NULL);
	sigaction(SIGTERM, &sa, NULL);
	sa.sa_handler = on_wake;
	sigaction(SIGUSR1, &sa, NULL);
	sa.sa_handler = on_release;
	sigaction(SIGHUP, &sa, NULL);
	signal(SIGPIPE, SIG_IGN);

	if (outsel_parse(&outs, cfg.out_list) < 0) {
		log_err("--out %s: not a usable device list (at most %d "
		    "comma-separated paths)", cfg.out_list, OUTSEL_MAX);
		cdin_log_close();
		return 1;
	}
	/* Until the probe below settles it, the "output" is the whole list.
	 * Naming the first entry here would make an unavailable chain look
	 * like a broken device when the fallback is sitting right behind it. */
	cfg.out_path = outs.path[0];

	log_info("omdrc-cdin " CDIN_VERSION " starting: in=%s out=%s "
	    "%d Hz %d-bit %dch, lead %d ms, ring %d ms, period %zu frames, "
	    "idle after %d ms of silence",
	    cfg.in_path, outsel_describe(&outs, outdesc, sizeof(outdesc)),
	    cfg.rate, cfg.bits, cfg.channels,
	    cfg.lead_ms, cfg.ring_ms, cfg.period_frames, cfg.idle_after_ms);

	memset(&s, 0, sizeof(s));
	s.cfg = &cfg;
	s.in = &in;
	s.out = &out;
	/* "none" is only ever given alone; a list containing it is a typo, and
	 * treating it as measure-only would silently drop the real device. */
	s.measure_only = outs.count == 1 && strcmp(outs.path[0], "none") == 0;
	in.fd = out.fd = -1;

	/*
	 * A character device is an OSS capture device.  Anything else is the
	 * simulated transport: a WAV file, or a directory of them standing in
	 * for a disc.  Its own format wins, because the output has to be
	 * opened to match whatever the source actually is.
	 */
	if (stat(cfg.in_path, &stbuf) == 0 && !S_ISCHR(stbuf.st_mode)) {
		struct filesrc_cfg fc = {
			.path = cfg.in_path,
			.loop = cfg.loop,
			.ppm = cfg.in_ppm,
			.gap_ms = cfg.gap_ms,
			.transport = cfg.transport,
		};

		if ((s.file = filesrc_open(&fc, err, sizeof(err))) == NULL) {
			log_err("input %s: %s", cfg.in_path, err);
			cdin_log_close();
			return 1;
		}
		cfg.rate = filesrc_rate(s.file);
		cfg.channels = filesrc_channels(s.file);
		cfg.bits = filesrc_bits(s.file);
		if (cfg.loop)
			log_info("the disc repeats at the end");
		if (cfg.in_ppm != 0.0)
			log_info("simulated source drift: %+.1f ppm", cfg.in_ppm);
	}

	if (cfg.probe) {
		int ok = 1, i;

		for (i = 0; i < outs.count; i++) {
			if (open_playback_negotiated(&out, &cfg, outs.path[i],
			    cfg.bits, err, sizeof(err)) != 0) {
				log_warn("playback %s: %s", outs.path[i], err);
				continue;
			}
			ossdev_close(&out);
			log_info("playback %s: available, %d-bit",
			    outs.path[i], out.bits);
			cfg.out_path = outs.path[i];
			ok = 0;
			break;
		}
		if (ok != 0)
			log_err("playback: none of %s could be opened",
			    outsel_describe(&outs, outdesc, sizeof(outdesc)));
		if (s.file != NULL) {
			log_info("input %s: readable, %d track%s", cfg.in_path,
			    filesrc_tracks(s.file),
			    filesrc_tracks(s.file) == 1 ? "" : "s");
		} else if (ossdev_open(&in, cfg.in_path, true, cfg.rate,
		    cfg.channels, cfg.bits, cfg.period_frames, err,
		    sizeof(err)) == 0) {
			ossdev_close(&in);
		} else {
			log_err("capture %s: %s", cfg.in_path, err);
			ok = 1;
		}
		filesrc_close(s.file);
		cdin_log_close();
		return ok;
	}

	/*
	 * Settle the output width once, before anything is held for long.  It
	 * is a property of the device rather than of the moment — with
	 * bitperfect=1 the format feeder is gone and /dev/dsp.play accepts
	 * exactly one width — and the ring has to be laid out in it, so asking
	 * again at every episode would put extra opens on the chain and learn
	 * nothing.  The probe releases the device immediately: from here on it
	 * is held only while music is actually playing.
	 */
	while (!cdin_stop && !s.measure_only && out_bits == 0) {
		char why[OUTSEL_MAX][256];
		int i;

		for (i = 0; i < outs.count; i++) {
			if (open_playback_negotiated(&out, &cfg, outs.path[i],
			    cfg.bits, err, sizeof(err)) != 0) {
				/* Kept per candidate.  Reporting only the last
				 * one answers "why is the CD input silent?"
				 * with whatever the FALLBACK happened to say
				 * ("Device busy", because BruteFIR legitimately
				 * holds the DAC) and hides the answer, which is
				 * on the preferred device ("device set 96000
				 * Hz, not 44100" — the chain is not at CD
				 * rate).  Both lines together are the whole
				 * story and neither alone is. */
				snprintf(why[i], sizeof(why[i]), "%s", err);
				continue;
			}
			/* Settled, for the life of the process: the ring is
			 * about to be laid out in this width and this device
			 * is the one it fits.  drc.sh restarts the daemon when
			 * the chain comes up or goes down, which is how the
			 * answer changes. */
			cfg.out_path = outs.path[i];
			out_bits = out.bits;
			out_frame_bytes = out.frame_bytes;
			ossdev_close(&out);
			log_info("playback %s: available, %d-bit%s",
			    cfg.out_path, out_bits,
			    i == 0 ? "" : " (the preferred device is not there)");
			warned_out = false;
			break;
		}
		if (out_bits != 0)
			break;
		if (!warned_out) {
			log_err("playback unavailable — no output device could "
			    "be opened (retrying every %d s):", cfg.retry_secs);
			for (i = 0; i < outs.count; i++)
				log_err("  %s: %s", outs.path[i], why[i]);
			warned_out = true;
		}
		sleep_ms(cfg.retry_secs * 1000);
	}

	/*
	 * The capture device, on the other hand, IS held for the life of a
	 * session: it is the only thing that can tell us whether there is a
	 * carrier, and it costs nobody anything — it is the interface's own
	 * node, not a virtual_oss client.
	 */
	while (!cdin_stop) {
		if (s.file == NULL && in.fd == -1) {
			if (ossdev_open(&in, cfg.in_path, true, cfg.rate,
			    cfg.channels, cfg.bits, cfg.period_frames, err,
			    sizeof(err)) != 0) {
				set_state(&s, CDIN_NO_CARRIER,
				    "the capture device cannot be opened");
				if (!warned_in) {
					log_err("capture %s: unavailable — %s "
					    "(waiting for the device, retrying "
					    "every %d s)", cfg.in_path, err,
					    cfg.retry_secs);
					warned_in = true;
				}
				sleep_ms(cfg.retry_secs * 1000);
				continue;
			}
			log_info("capture %s: available", cfg.in_path);
			warned_in = false;
		}

		if (s.ring == NULL) {
			size_t ifb = s.file != NULL
			    ? (size_t)(cfg.bits == 24 ? 3 : cfg.bits / 8) *
			      (size_t)cfg.channels
			    : in.frame_bytes;
			size_t ofb = out_bits != 0 ? out_frame_bytes : ifb;
			size_t cap = (size_t)((double)cfg.ring_ms / 1000.0 *
			    cfg.rate) * ofb;

			s.in_bits = cfg.bits;
			s.out_bits = out_bits != 0 ? out_bits : cfg.bits;
			s.in_frame_bytes = ifb;
			s.in_period_bytes = cfg.period_frames * ifb;
			s.frame_bytes = ofb;
			s.period_bytes = cfg.period_frames * ofb;
			s.lead_bytes = (size_t)((double)cfg.lead_ms / 1000.0 *
			    cfg.rate) * ofb;
			if ((s.ring = ring_new(cap, ofb)) == NULL) {
				log_err("cannot allocate a %zu byte ring", cap);
				rc = 1;
				break;
			}
			log_info("ring: %zu bytes (%d ms at %d Hz)", cap,
			    cfg.ring_ms, cfg.rate);
		}

		set_state(&s, CDIN_IDLE, "capture open, waiting for audio");

		if (run_session(&s) != 0 && !cdin_stop) {
			set_state(&s, CDIN_NO_CARRIER,
			    "the capture session ended on a device error");
			log_warn("session ended on a device error; reopening in %d s",
			    cfg.retry_secs);
			ossdev_close(&in);
			sleep_ms(cfg.retry_secs * 1000);
		}
	}

	log_info("shutting down");
	filesrc_close(s.file);
	ossdev_close(&in);
	/* The playback thread owns `out` and has already been joined by
	   run_session(), which closes it on the way out of every episode. */
	ring_free(s.ring);
	cdin_log_close();
	return rc;
}
