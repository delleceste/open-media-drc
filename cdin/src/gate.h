/*
 * omdrc-cdin — the digital-silence gate.
 *
 * This is the decision that lets the daemon stay running while the CD player
 * is off: when the wire has carried nothing but exact zeros for long enough,
 * the input is idle and the output device can be released, so MPD and mpv get
 * /dev/dsp.play back without anyone having to stop this service.
 *
 * A CD's silence is literally 0x0000, so there is no threshold to tune on the
 * sample side — only on the time side, and that one matters:
 *
 *   too short  and Red Book's 2 s inter-track pause, or a finger on Pause,
 *              tears the output down mid-disc and costs a `lead` to resume;
 *   too long   and a stopped player keeps holding the chain.
 *
 * The default (see --idle-after) is an order of magnitude above the 2 s gap and
 * still releases the device within seconds of the music stopping.  Note this
 * is silence on the *wire*: a player that drops carrier instead never reaches
 * the gate at all, it fails the read and lands in NO_CARRIER.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum gate_verdict {
	GATE_AUDIO,	/* something non-zero arrived: this is a live stream */
	GATE_SILENT,	/* zeros, but not yet long enough to call it idle */
	GATE_IDLE,	/* the silence run just reached the threshold */
};

struct silence_gate {
	uint64_t idle_after_frames;	/* 0 disables the gate entirely */
	uint64_t silent_frames;		/* length of the current silence run */
	bool     idle;			/* GATE_IDLE already reported for this run */
};

/* idle_after_ms <= 0 disables the gate: GATE_IDLE is then never returned and
   the daemon keeps the output open exactly as Phase 1 did. */
void gate_init(struct silence_gate *g, int idle_after_ms, int rate);

/*
 * Feed one captured period.  GATE_IDLE is edge-triggered — returned once per
 * silence run, not for every period after the threshold — so the caller can
 * treat it as the transition itself rather than having to remember whether it
 * has already acted on it.
 */
enum gate_verdict gate_feed(struct silence_gate *g, bool silent, size_t frames);

/* Forget the current run (a new capture session starts neither silent nor
   idle, whatever the previous one ended as). */
void gate_reset(struct silence_gate *g);

/* Length of the current silence run, in frames.  0 while audio is playing. */
uint64_t gate_silent_frames(const struct silence_gate *g);
