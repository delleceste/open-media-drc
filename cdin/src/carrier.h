/*
 * omdrc-cdin — the carrier (clock) detector.
 *
 * The silence gate answers "are the samples zero?".  This one answers a
 * different question that the gate cannot see: "are samples arriving at all?"
 *
 * Both are needed because a CD player has two ways of going quiet:
 *
 *   pause   the transport keeps the S/PDIF carrier up and sends 0x0000, so
 *           frames arrive at the full rate and the silence gate handles it;
 *   stop    the carrier goes away entirely.  The ESI U24XL slaves its ADC
 *           clock to the incoming carrier, so with no carrier there is no
 *           clock, and the capture device stops being fed.
 *
 * gate.h assumed the second case would surface as a short read and land in
 * NO_CARRIER.  It does not: ossdev_read_full() loops until the buffer is full,
 * so an unclocked input yields a *slow* full read, never a short one.  The
 * frames that do trickle through are whatever the receiver makes of an empty
 * wire — not zeros — so the silence gate scores them as music and the daemon
 * holds the output forever, dribbling.  Measured on a stopped transport: about
 * 500 frames/s against a nominal 44100, i.e. 1.1% of rate, `silence 0%`.
 *
 * That dribble is audible.  It accumulates in the output device's buffer until
 * the buffer is full and is then flushed as one short burst — with an 8820
 * frame buffer filling at 500 frames/s, a burst roughly every 17 seconds, each
 * one a step in and a step out: the "double bump" this detector exists to end.
 *
 * So the test is a rate test, over a window: below a fraction of nominal, the
 * input is not being clocked and there is nothing to play.  The margin is
 * enormous (1% observed against a 50% default threshold), which is what makes
 * a plain ratio safe here rather than something adaptive.
 *
 * Limitation: this needs *some* frames to arrive, because it is driven by the
 * capture thread's own reads.  An input that stops dead mid-read blocks in
 * read(2) and never reaches this code — the playback side sees that one as the
 * ring draining out.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum carrier_verdict {
	CARRIER_SAME,	/* no change (or the window is still filling) */
	CARRIER_LOST,	/* edge: the input just stopped being clocked */
	CARRIER_BACK,	/* edge: frames are arriving at rate again */
};

struct carrier {
	double   min_hz;	/* below this over a window = not clocked */
	double   window_secs;
	double   window_start;
	double   last_hz;	/* rate measured over the window just closed */
	uint64_t frames;	/* frames seen in the window now filling */
	bool     live;
	bool     judged;	/* at least one window has closed */
	bool     always;	/* detector disabled: live, unconditionally */
};

/*
 * min_percent <= 0 disables the detector entirely: the carrier then reads as
 * live forever and CARRIER_LOST is never returned, which is the behaviour from
 * before this check existed.
 *
 * The carrier starts NOT live, and stays that way until a full window has been
 * measured at rate.  The first window to close always reports an edge, even
 * though "not live" is where it began: a daemon that comes up to a dead wire
 * should say so — the state it lands in is what the panel shows — rather than
 * sit in a bare "idle" that reads like a disc between tracks.
 *
 * Starting live would mean a daemon that comes up while the transport is off
 * acquires the output on the first dribbled period and only
 * gives it back a window later — one needless grab of the device, and one
 * needless bump, every time the service starts.  Waiting costs nothing on the
 * other side: the ring is already rolling, so the lead an episode starts from
 * is buffered history, not audio recorded after the decision.
 */
void carrier_init(struct carrier *c, int rate, int min_percent, int window_ms,
    double now);

/* Forget the window in progress and the verdict (a new capture session is
   judged on its own frames, not on the previous one's). */
void carrier_reset(struct carrier *c, double now);

/*
 * Feed one captured period, with the time it was received.  The verdict is
 * edge-triggered — LOST and BACK are each returned once per transition — so a
 * caller can log and act on the transition itself; use carrier_live() for the
 * level, which is what gates whether an episode may start.
 */
enum carrier_verdict carrier_feed(struct carrier *c, size_t frames, double now);

bool carrier_live(const struct carrier *c);

/* The rate over the last closed window, in frames/s: the number worth putting
   in a log line, because it says *how* dead the input is. */
double carrier_hz(const struct carrier *c);
