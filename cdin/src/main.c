/*
 * omdrc-cdin — S/PDIF capture bridge into the open-media-drc chain.
 *
 * Phase 1: capture -> ring (holding a lead of a few seconds) -> playback.
 * No resampler: the data path is a memcpy, so what reaches BruteFIR is
 * bit-identical to what the CD transport sent.
 *
 * Phase 2 (not yet here) adds the NO_CARRIER/IDLE/PLAYING state machine:
 * lazy output open, and drift resync during the digital silence between
 * tracks.  The seams are marked TODO(phase2).
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

#include "cdin.h"
#include "convert.h"
#include "filesrc.h"
#include "log.h"
#include "ossdev.h"
#include "ring.h"

volatile sig_atomic_t cdin_stop;
volatile sig_atomic_t cdin_io_abort;

#define TICK_MS 100		/* stats sampling period */

struct config {
	const char *in_path;
	const char *out_path;	/* "none" = capture-only measurement */
	const char *log_path;
	int    rate;
	int    channels;
	int    bits;
	int    lead_ms;
	int    ring_ms;
	size_t period_frames;
	int    stats_secs;	/* 0 = no periodic stats */
	int    retry_secs;
	int    out_bits;	/* 0 = negotiate with the device */
	double in_ppm;		/* file source only: simulated clock offset */
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

/* ── worker threads ────────────────────────────────────────────────────── */

static void *
capture_thread(void *arg)
{
	struct session *s = arg;
	unsigned char *buf;
	ssize_t rc;

	unsigned char *wide = NULL;
	bool widening = s->in_bits != s->out_bits;

	if ((buf = malloc(s->in_period_bytes)) == NULL ||
	    (widening && (wide = malloc(s->period_bytes)) == NULL)) {
		log_err("capture: out of memory");
		atomic_store(&s->failed, 1);
		ring_shutdown(s->ring);
		free(buf);
		return NULL;
	}

	while (!cdin_stop && !atomic_load(&s->failed)) {
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
				log_info("end of input file; draining %.0f ms "
				    "of lead", bytes_to_ms(s, ring_fill(s->ring)));
				while (!cdin_stop &&
				    ring_fill(s->ring) >= s->period_bytes)
					sleep_ms(20);
				cdin_stop = 1;
				break;
			}
			/* TODO(phase2): this is the NO_CARRIER edge — stop the
			   CD or unplug the coax and we land here.  For now the
			   session ends and the main loop reopens the device. */
			log_warn("capture %s: short read (%zd of %zu bytes) — "
			    "carrier lost?", s->in->path, rc, s->in_period_bytes);
			atomic_store(&s->failed, 1);
			break;
		}

		atomic_fetch_add(&s->periods_total, 1);
		if (buffer_is_silent(buf, (size_t)rc))
			atomic_fetch_add(&s->periods_silent, 1);

		if (!s->measure_only) {
			size_t dropped;

			if (widening) {
				convert_widen(buf, s->in_bits, wide, s->out_bits,
				    s->cfg->period_frames * (size_t)s->cfg->channels);
				dropped = ring_write(s->ring, wide, s->period_bytes);
			} else {
				dropped = ring_write(s->ring, buf, (size_t)rc);
			}
			if (dropped > 0)
				atomic_fetch_add(&s->drop_bytes, dropped);
		}
		atomic_fetch_add(&s->frames_in,
		    (uint64_t)rc / s->in_frame_bytes);
	}

	free(buf);
	free(wide);
	ring_shutdown(s->ring);
	return NULL;
}

static void *
playback_thread(void *arg)
{
	struct session *s = arg;
	size_t lead_bytes, lowwater;
	unsigned char *buf;
	bool starving = false;
	ssize_t rc;

	if ((buf = malloc(s->period_bytes)) == NULL) {
		log_err("playback: out of memory");
		atomic_store(&s->failed, 1);
		return NULL;
	}

	lead_bytes = (size_t)((double)s->cfg->lead_ms / 1000.0 *
	    s->cfg->rate) * s->frame_bytes;
	lowwater = s->period_bytes * 2;

	log_info("playback: pre-filling to a %d ms lead before opening the "
	    "stream", s->cfg->lead_ms);
	while (!cdin_stop && !atomic_load(&s->failed) &&
	    ring_fill(s->ring) < lead_bytes) {
		if (atomic_load(&s->input_done)) {
			log_warn("playback: input ended after %.0f ms, short of "
			    "the %d ms lead — playing out what arrived",
			    bytes_to_ms(s, ring_fill(s->ring)), s->cfg->lead_ms);
			break;
		}
		sleep_ms(20);
	}
	if (cdin_stop || atomic_load(&s->failed)) {
		free(buf);
		return NULL;
	}
	log_info("playback: lead reached (%.0f ms buffered), starting",
	    bytes_to_ms(s, ring_fill(s->ring)));
	atomic_store(&s->running, 1);

	while (!cdin_stop && !atomic_load(&s->failed)) {
		size_t fill = ring_fill(s->ring);

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
			atomic_store(&s->failed, 1);
			break;
		}
		if ((size_t)rc < s->period_bytes) {
			log_err("playback %s: short write (%zd of %zu bytes)",
			    s->out->path, rc, s->period_bytes);
			atomic_store(&s->failed, 1);
			break;
		}
		atomic_fetch_add(&s->frames_out, (uint64_t)rc / s->frame_bytes);
	}

	free(buf);
	return NULL;
}

/* ── stats ─────────────────────────────────────────────────────────────── */

struct stats_state {
	double   t0;		/* session start */
	double   t_run;		/* when playback began writing */
	uint64_t in_at_run;	/* counter snapshots at that instant */
	uint64_t out_at_run;
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
	double now = now_monotonic(), mean, elapsed, in_hz, out_hz, step, step_ms;
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
	 * minutes (a 2 s pre-fill is still a 2% error at 100 s).
	 */
	elapsed = st->run_seen ? now - st->t_run : now - st->t0;
	if (elapsed <= 0)
		return;
	in_hz = (double)(fin - st->in_at_run) / elapsed;
	out_hz = (double)(fout - st->out_at_run) / elapsed;

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

	/* Test-rig health, reported only when it is not perfect: these say the
	   WAV source could not be fed, which is not a fault of the daemon. */
	rig[0] = '\0';
	if (s->file != NULL &&
	    (filesrc_stalls(s->file) > 0 || filesrc_slips(s->file) > 0)) {
		snprintf(rig, sizeof(rig), "  rig stalls %llu slips %llu",
		    (unsigned long long)filesrc_stalls(s->file),
		    (unsigned long long)filesrc_slips(s->file));
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
			st.t_run = now_monotonic();
			st.in_at_run = atomic_load(&s->frames_in);
			st.out_at_run = atomic_load(&s->frames_out);
			stats_reset_interval(&st);
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
    int in_bits, char *err, size_t errsz)
{
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
		if (ossdev_open(d, cfg->out_path, false, cfg->rate,
		    cfg->channels, candidates[i], cfg->period_frames, err,
		    errsz) == 0) {
			if (candidates[i] != in_bits)
				log_info("output opened at %d-bit; widening "
				    "%d -> %d bit losslessly (left-justified, "
				    "no arithmetic)", candidates[i], in_bits,
				    candidates[i]);
			return 0;
		}
		if (i + 1 < n)
			log_debug("playback %s at %d-bit: %s — trying wider",
			    cfg->out_path, candidates[i], err);
	}
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
"  -i, --in dev       capture device                     (default /dev/dsp1)\n"
"  -o, --out dev      playback device, or 'none' for a capture-only\n"
"                     measurement                   (default /dev/dsp.play)\n"
"                     /dev/dsp.play is the virtual_oss loopback BruteFIR\n"
"                     reads; use /dev/dsp0 to write the DAC directly and\n"
"                     bypass the chain.\n"
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
"  -l, --log file     also log to this file\n"
"  -d, --debug        emit periodic stats\n"
"  -v, --verbose      more verbose; repeat for debug\n"
"  -P, --probe        open both devices, report, exit\n"
"\n"
" Testing without a capture device: pass a WAV file to --in and it is used as\n"
" the source, paced on a monotonic deadline exactly as a real capture device\n"
" would deliver it.  Its rate/width/channels are adopted for the whole chain.\n"
"      --in-ppm n     offset the file's pace, in ppm.  This is the drift rig:\n"
"                     +n grows the lead, -n drains it.  Real hardware differs\n"
"                     by a few ppm and takes hours to show; --in-ppm -5000\n"
"                     drains a 2000 ms lead in ~400 s.\n"
"      --loop         restart the file at end of data\n"
"  -h, --help         this help\n"
"  -V, --version      version\n");
}

int
main(int argc, char **argv)
{
	struct config cfg = {
		.in_path = "/dev/dsp1",
		.out_path = "/dev/dsp.play",
		.rate = 44100,
		.channels = 2,
		.bits = 32,
		.lead_ms = 2000,
		.ring_ms = 8000,
		.period_frames = 1024,
		.stats_secs = 0,
		.retry_secs = 2,
		.level = CDL_INFO,
	};
	struct ossdev in, out;
	struct session s;
	struct sigaction sa;
	struct stat stbuf;
	char err[256];
	int verbose = 0, opt, rc = 0;
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
		{ "probe",    no_argument,       NULL, 'P' },
		{ "help",     no_argument,       NULL, 'h' },
		{ "version",  no_argument,       NULL, 'V' },
		{ NULL, 0, NULL, 0 }
	};

	while ((opt = getopt_long(argc, argv, "i:o:r:c:b:L:B:p:s:R:l:dvPhV",
	    longopts, NULL)) != -1) {
		switch (opt) {
		case 'i': cfg.in_path = optarg; break;
		case 'o': cfg.out_path = optarg; break;
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
	signal(SIGPIPE, SIG_IGN);

	log_info("omdrc-cdin " CDIN_VERSION " starting: in=%s out=%s "
	    "%d Hz %d-bit %dch, lead %d ms, ring %d ms, period %zu frames",
	    cfg.in_path, cfg.out_path, cfg.rate, cfg.bits, cfg.channels,
	    cfg.lead_ms, cfg.ring_ms, cfg.period_frames);

	memset(&s, 0, sizeof(s));
	s.cfg = &cfg;
	s.in = &in;
	s.out = &out;
	s.measure_only = strcmp(cfg.out_path, "none") == 0;
	in.fd = out.fd = -1;

	/*
	 * A character device is an OSS capture device; anything else is a WAV
	 * file standing in for one.  The file's own format wins, because the
	 * output has to be opened to match whatever the source actually is.
	 */
	if (stat(cfg.in_path, &stbuf) == 0 && !S_ISCHR(stbuf.st_mode)) {
		s.file = filesrc_open(cfg.in_path, cfg.loop, cfg.in_ppm, err,
		    sizeof(err));
		if (s.file == NULL) {
			log_err("input file %s: %s", cfg.in_path, err);
			cdin_log_close();
			return 1;
		}
		cfg.rate = filesrc_rate(s.file);
		cfg.channels = filesrc_channels(s.file);
		cfg.bits = filesrc_bits(s.file);
		log_info("input file %s: %d Hz %d-bit %dch, %.1f s%s%s",
		    cfg.in_path, cfg.rate, cfg.bits, cfg.channels,
		    filesrc_seconds(s.file), cfg.loop ? ", looping" : "",
		    cfg.in_ppm != 0.0 ? " (paced off nominal)" : "");
		if (cfg.in_ppm != 0.0)
			log_info("simulated source drift: %+.1f ppm", cfg.in_ppm);
	}

	if (cfg.probe) {
		int ok = 0;

		if (open_playback_negotiated(&out, &cfg, cfg.bits, err,
		    sizeof(err)) == 0) {
			ossdev_close(&out);
		} else {
			log_err("playback %s: %s", cfg.out_path, err);
			ok = 1;
		}
		if (s.file != NULL) {
			log_info("input file %s: readable", cfg.in_path);
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
	 * Open the output first so a run with no CD hardware still shows the
	 * chain is reachable.
	 * TODO(phase2): open it lazily, only once audio is actually present,
	 * so an idle CD input does not hold /dev/dsp.play against MPD and mpv.
	 */
	while (!cdin_stop) {
		if (!s.measure_only && out.fd == -1) {
			if (open_playback_negotiated(&out, &cfg, cfg.bits, err,
			    sizeof(err)) != 0) {
				if (!warned_out) {
					log_err("playback %s: %s — retrying "
					    "every %d s", cfg.out_path, err,
					    cfg.retry_secs);
					warned_out = true;
				}
				sleep_ms(cfg.retry_secs * 1000);
				continue;
			}
			warned_out = false;
		}

		if (s.file == NULL && in.fd == -1) {
			if (ossdev_open(&in, cfg.in_path, true, cfg.rate,
			    cfg.channels, cfg.bits, cfg.period_frames, err,
			    sizeof(err)) != 0) {
				if (!warned_in) {
					log_err("capture %s: %s — waiting for "
					    "the device, retrying every %d s",
					    cfg.in_path, err, cfg.retry_secs);
					warned_in = true;
				}
				sleep_ms(cfg.retry_secs * 1000);
				continue;
			}
			warned_in = false;
		}

		if (s.ring == NULL) {
			size_t ifb = s.file != NULL
			    ? (size_t)(cfg.bits == 24 ? 3 : cfg.bits / 8) *
			      (size_t)cfg.channels
			    : in.frame_bytes;
			size_t ofb = out.fd != -1 ? out.frame_bytes : ifb;
			size_t cap = (size_t)((double)cfg.ring_ms / 1000.0 *
			    cfg.rate) * ofb;

			s.in_bits = cfg.bits;
			s.out_bits = out.fd != -1 ? out.bits : cfg.bits;
			s.in_frame_bytes = ifb;
			s.in_period_bytes = cfg.period_frames * ifb;
			s.frame_bytes = ofb;
			s.period_bytes = cfg.period_frames * ofb;
			if ((s.ring = ring_new(cap, ofb)) == NULL) {
				log_err("cannot allocate a %zu byte ring", cap);
				rc = 1;
				break;
			}
			log_info("ring: %zu bytes (%d ms at %d Hz)", cap,
			    cfg.ring_ms, cfg.rate);
		}

		if (run_session(&s) != 0 && !cdin_stop) {
			log_warn("session ended on a device error; reopening in %d s",
			    cfg.retry_secs);
			ossdev_close(&in);
			if (!s.measure_only)
				ossdev_close(&out);
			sleep_ms(cfg.retry_secs * 1000);
		}
	}

	log_info("shutting down");
	filesrc_close(s.file);
	ossdev_close(&in);
	if (!s.measure_only)
		ossdev_close(&out);
	ring_free(s.ring);
	cdin_log_close();
	return rc;
}
