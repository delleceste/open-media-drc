/*
 * Carrier-detector tests.
 *
 * This detector exists because of a bug that was audible but invisible to
 * every check the daemon already had: a stopped CD transport drops the S/PDIF
 * carrier, the ESI's clock goes with it, and the capture dribbles ~500 frames
 * a second of non-zero rubbish instead of 44100.  Reads stayed full (so the
 * NO_CARRIER path never fired) and samples stayed non-zero (so the silence
 * gate never fired), and the output device was held for hours, bumping once
 * per buffer-fill.  The numbers in test_the_bug_this_was_written_for() are the
 * ones measured on that box.
 *
 * The failure modes to guard are symmetric: judge too eagerly and a real disc
 * loses its opening seconds or gets torn down at a rate wobble; judge too
 * slowly, or never re-arm, and the dribble comes back.
 */
#include <stdio.h>

#include "carrier.h"

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
#define MINPCT 50		/* the daemon's default threshold */
#define WINDOW 2000		/* ms */

/*
 * Feed `secs` seconds of a stream arriving at `hz` frames/s, in periods, and
 * return the number of LOST edges (negated) or BACK edges seen.  Time is
 * driven from the frame count, exactly as it is on the wire: this is what
 * makes a dead input's periods land seconds apart.
 */
static void
feed(struct carrier *c, double hz, double secs, double *now,
    int *lost, int *back)
{
	double end = *now + secs;
	double step = (double)PERIOD / hz;	/* how long a period takes */

	while (*now + step <= end) {
		*now += step;
		switch (carrier_feed(c, PERIOD, *now)) {
		case CARRIER_LOST: if (lost != NULL) (*lost)++; break;
		case CARRIER_BACK: if (back != NULL) (*back)++; break;
		case CARRIER_SAME: break;
		}
	}
}

static void
test_a_stream_at_rate_is_live(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	carrier_init(&c, RATE, MINPCT, WINDOW, now);
	feed(&c, RATE, 10, &now, &lost, &back);
	CHECK(carrier_live(&c), "a full-rate stream did not read as live");
	CHECK(back == 1, "expected exactly one BACK edge, got %d", back);
	CHECK(lost == 0, "a full-rate stream reported %d LOST edges", lost);
}

static void
test_it_starts_not_live(void)
{
	struct carrier c;

	/* Before any window has closed there is no evidence either way, and
	   the daemon must not grab the output on no evidence. */
	carrier_init(&c, RATE, MINPCT, WINDOW, 0);
	CHECK(!carrier_live(&c), "the carrier claimed to be live before it "
	    "had measured anything");
}

static void
test_starting_up_to_a_dead_wire_says_so(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	/*
	 * "Not live" is also where it starts, so a naive edge check would stay
	 * silent here — and the daemon would sit in a bare "idle" that reads
	 * like a disc between tracks instead of naming the dead wire.
	 */
	carrier_init(&c, RATE, MINPCT, WINDOW, now);
	feed(&c, 500, 10, &now, &lost, &back);
	CHECK(lost == 1, "starting up to a dead input gave %d LOST edges, "
	    "expected 1", lost);
}

static void
test_the_bug_this_was_written_for(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	/* The measured numbers: 500 frames/s against a nominal 44100, every
	   sample non-zero, reads never short. */
	carrier_init(&c, RATE, MINPCT, WINDOW, now);
	feed(&c, 500, 60, &now, &lost, &back);
	CHECK(!carrier_live(&c),
	    "a 500 Hz dribble against 44100 nominal read as a live stream");
	CHECK(back == 0, "the dribble was reported live %d times", back);
	CHECK(carrier_hz(&c) > 400 && carrier_hz(&c) < 600,
	    "measured %.1f Hz, expected ~500", carrier_hz(&c));
}

static void
test_a_stream_that_stops_is_noticed_promptly(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	carrier_init(&c, RATE, MINPCT, WINDOW, now);
	feed(&c, RATE, 10, &now, NULL, NULL);
	CHECK(carrier_live(&c), "setup: the stream was not live");

	feed(&c, 500, 10, &now, &lost, &back);
	CHECK(lost == 1, "expected exactly one LOST edge, got %d", lost);
	CHECK(!carrier_live(&c), "the stopped input still read as live");
}

static void
test_the_edge_fires_once_not_every_period(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	/* Level would make the caller re-log and re-release on every window
	   for as long as the player stays off — which is overnight. */
	carrier_init(&c, RATE, MINPCT, WINDOW, now);
	feed(&c, RATE, 10, &now, NULL, NULL);
	feed(&c, 500, 600, &now, &lost, &back);
	CHECK(lost == 1, "10 minutes of dead input gave %d LOST edges", lost);
}

static void
test_it_rearms_when_the_disc_comes_back(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	carrier_init(&c, RATE, MINPCT, WINDOW, now);
	feed(&c, RATE, 10, &now, NULL, NULL);
	feed(&c, 500, 30, &now, &lost, &back);
	CHECK(lost == 1, "setup: expected one LOST, got %d", lost);

	feed(&c, RATE, 10, &now, &lost, &back);
	CHECK(carrier_live(&c), "the carrier did not come back with the disc");
	CHECK(back == 1, "expected one BACK edge, got %d", back);
}

static void
test_a_pause_at_full_rate_is_not_a_carrier_loss(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	/*
	 * A paused transport keeps the carrier and sends zeros: frames still
	 * arrive at 44100.  That is the silence gate's job, and this detector
	 * must keep its hands off it — otherwise a finger on Pause tears the
	 * output down instead of holding it through the gap.
	 */
	carrier_init(&c, RATE, MINPCT, WINDOW, now);
	feed(&c, RATE, 120, &now, &lost, &back);
	CHECK(lost == 0, "two minutes of paused-but-clocked input gave %d "
	    "LOST edges", lost);
	CHECK(carrier_live(&c), "a clocked pause read as no carrier");
}

static void
test_a_small_clock_offset_is_not_a_carrier_loss(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	/* The file rig can offset the clock by ppm, and real transports are
	   not exact either.  Nothing near nominal may trip this. */
	carrier_init(&c, RATE, MINPCT, WINDOW, now);
	feed(&c, RATE * 0.999, 60, &now, &lost, &back);
	CHECK(lost == 0, "a -1000 ppm offset gave %d LOST edges", lost);
	CHECK(carrier_live(&c), "a slightly slow clock read as no carrier");
}

static void
test_half_rate_is_the_documented_boundary(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	/* At the default threshold, 49% is dead and 51% is alive.  Asserting
	   the boundary keeps --carrier-min meaning what its help text says. */
	carrier_init(&c, RATE, MINPCT, WINDOW, now);
	feed(&c, RATE * 0.51, 20, &now, &lost, &back);
	CHECK(carrier_live(&c), "51%% of nominal read as no carrier");

	carrier_init(&c, RATE, MINPCT, WINDOW, now = 0);
	feed(&c, RATE * 0.49, 20, &now, &lost, &back);
	CHECK(!carrier_live(&c), "49%% of nominal read as a live stream");
}

static void
test_zero_disables_the_detector(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	/* The escape hatch: --carrier-min 0 restores the old behaviour for an
	   interface this ratio does not suit. */
	carrier_init(&c, RATE, 0, WINDOW, now);
	CHECK(carrier_live(&c), "a disabled detector did not read as live");
	feed(&c, 1, 600, &now, &lost, &back);
	CHECK(carrier_live(&c), "a disabled detector went dead anyway");
	CHECK(lost == 0, "a disabled detector reported %d LOST edges", lost);
}

static void
test_reset_forgets_the_verdict(void)
{
	struct carrier c;
	double now = 0;
	int lost = 0, back = 0;

	carrier_init(&c, RATE, MINPCT, WINDOW, now);
	feed(&c, RATE, 10, &now, NULL, NULL);
	CHECK(carrier_live(&c), "setup: the stream was not live");

	carrier_reset(&c, now);
	CHECK(!carrier_live(&c), "reset kept the previous session's verdict");
	feed(&c, RATE, 10, &now, &lost, &back);
	CHECK(back == 1, "expected one BACK edge after reset, got %d", back);
}

int
main(void)
{
	test_it_starts_not_live();
	test_starting_up_to_a_dead_wire_says_so();
	test_a_stream_at_rate_is_live();
	test_the_bug_this_was_written_for();
	test_a_stream_that_stops_is_noticed_promptly();
	test_the_edge_fires_once_not_every_period();
	test_it_rearms_when_the_disc_comes_back();
	test_a_pause_at_full_rate_is_not_a_carrier_loss();
	test_a_small_clock_offset_is_not_a_carrier_loss();
	test_half_rate_is_the_documented_boundary();
	test_zero_disables_the_detector();
	test_reset_forgets_the_verdict();

	if (failures == 0)
		printf("all carrier tests passed\n");
	return failures != 0;
}
