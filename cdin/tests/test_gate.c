/*
 * Silence-gate tests.
 *
 * The gate decides when the output device is released, and both of its
 * mistakes are silent ones.  Fire too early and a disc tears its own output
 * down in the 2 s Red Book pause, costing a lead's delay between every pair of
 * tracks; fire too late — or twice — and the release either never happens or
 * happens again on every period afterwards.  Neither shows up as a crash or a
 * warning, so the threshold, its edge behaviour, and the fact that one
 * non-zero sample resets the whole run are asserted here rather than trusted.
 */
#include <stdio.h>

#include "gate.h"

static int failures;

#define CHECK(cond, ...) do {                                            \
	if (!(cond)) {                                                   \
		printf("FAIL %s:%d: ", __func__, __LINE__);              \
		printf(__VA_ARGS__);                                     \
		printf("\n");                                            \
		failures++;                                              \
	}                                                                \
} while (0)

#define RATE   44100
#define PERIOD 1024		/* frames, the daemon's default */

/* Feed n silent periods and return how many of them reported GATE_IDLE. */
static int
feed_silence(struct silence_gate *g, int periods)
{
	int i, idles = 0;

	for (i = 0; i < periods; i++)
		if (gate_feed(g, true, PERIOD) == GATE_IDLE)
			idles++;
	return idles;
}

static void
test_audio_is_audio(void)
{
	struct silence_gate g;

	gate_init(&g, 15000, RATE);
	CHECK(gate_feed(&g, false, PERIOD) == GATE_AUDIO,
	    "a non-silent period did not report audio");
	CHECK(gate_silent_frames(&g) == 0,
	    "audio left a silence run behind it");
}

static void
test_silence_below_threshold_is_not_idle(void)
{
	struct silence_gate g;
	/* 2 s of zeros: exactly the Red Book inter-track pause, and the case
	   that must NOT release the device. */
	int periods = 2 * RATE / PERIOD;

	gate_init(&g, 15000, RATE);
	CHECK(feed_silence(&g, periods) == 0,
	    "a 2 s inter-track pause went idle");
	CHECK(gate_feed(&g, false, PERIOD) == GATE_AUDIO,
	    "the next track did not report audio");
}

static void
test_threshold_fires_once(void)
{
	struct silence_gate g;
	int periods = 20 * RATE / PERIOD;	/* well past a 15 s gate */

	gate_init(&g, 15000, RATE);
	CHECK(feed_silence(&g, periods) == 1,
	    "the idle edge did not fire exactly once in 20 s of silence");
}

static void
test_threshold_is_where_it_says(void)
{
	struct silence_gate g;
	int i;
	int at = -1;

	gate_init(&g, 1000, RATE);
	for (i = 0; i < 200; i++)
		if (gate_feed(&g, true, PERIOD) == GATE_IDLE) {
			at = i;
			break;
		}
	CHECK(at >= 0, "a 1 s gate never fired");
	/* The run is counted in frames, so the edge lands on the first period
	   whose cumulative frames reach the threshold — not one before it. */
	CHECK(at >= 0 && (uint64_t)(at + 1) * PERIOD >= RATE,
	    "the gate fired at period %d, before 1 s of frames had arrived", at);
	CHECK(at >= 0 && (uint64_t)at * PERIOD < RATE,
	    "the gate fired at period %d, later than the first period past 1 s",
	    at);
}

static void
test_audio_resets_the_run(void)
{
	struct silence_gate g;
	int periods = 14 * RATE / PERIOD;	/* just short of a 15 s gate */

	gate_init(&g, 15000, RATE);
	CHECK(feed_silence(&g, periods) == 0, "14 s of silence went idle");
	CHECK(gate_feed(&g, false, PERIOD) == GATE_AUDIO, "audio not reported");
	/* One sample of music has to buy a full threshold again, or a quiet
	   passage would accumulate its way to a release across the music. */
	CHECK(feed_silence(&g, periods) == 0,
	    "the silence run survived a period of audio");
	CHECK(feed_silence(&g, 2 * RATE / PERIOD) == 1,
	    "the gate did not fire once the new run reached the threshold");
}

static void
test_idle_rearms_after_audio(void)
{
	struct silence_gate g;
	int periods = 20 * RATE / PERIOD;

	gate_init(&g, 15000, RATE);
	CHECK(feed_silence(&g, periods) == 1, "first idle edge missing");
	CHECK(gate_feed(&g, false, PERIOD) == GATE_AUDIO, "audio not reported");
	CHECK(feed_silence(&g, periods) == 1,
	    "the gate did not re-arm for the next silence run");
}

static void
test_zero_disables_the_gate(void)
{
	struct silence_gate g;

	/* --idle-after 0 is the escape hatch back to holding the output for
	   the whole run; it must never report idle, however long the zeros. */
	gate_init(&g, 0, RATE);
	CHECK(feed_silence(&g, 600 * RATE / PERIOD) == 0,
	    "a disabled gate went idle");
	gate_init(&g, -1, RATE);
	CHECK(feed_silence(&g, 600 * RATE / PERIOD) == 0,
	    "a negative --idle-after went idle");
}

static void
test_reset_clears_the_run(void)
{
	struct silence_gate g;

	gate_init(&g, 15000, RATE);
	feed_silence(&g, 14 * RATE / PERIOD);
	CHECK(gate_silent_frames(&g) > 0, "silence was not accumulated");
	gate_reset(&g);
	CHECK(gate_silent_frames(&g) == 0, "reset left a silence run behind");
	CHECK(feed_silence(&g, 14 * RATE / PERIOD) == 0,
	    "the run carried across a reset");
}

int
main(void)
{
	test_audio_is_audio();
	test_silence_below_threshold_is_not_idle();
	test_threshold_fires_once();
	test_threshold_is_where_it_says();
	test_audio_resets_the_run();
	test_idle_rearms_after_audio();
	test_zero_disables_the_gate();
	test_reset_clears_the_run();

	if (failures == 0)
		printf("all gate tests passed\n");
	return failures != 0;
}
