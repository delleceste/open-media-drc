/*
 * Ring buffer tests — the data path runs at ratio 1.0, so a bug here is the
 * one thing that can silently corrupt audio.  Covers wrap-around, the
 * drop-oldest overflow policy, blocking reads and shutdown wake-up, plus the
 * back-pressured write and end-of-data read used by the WAV test source.
 */
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "ring.h"

#define FRAME 8		/* 2ch x 32-bit, as the daemon runs */

static int failures;

#define CHECK(cond, ...) do {                                            \
	if (!(cond)) {                                                   \
		printf("FAIL %s:%d: ", __func__, __LINE__);              \
		printf(__VA_ARGS__);                                     \
		printf("\n");                                            \
		failures++;                                              \
	}                                                                \
} while (0)

static void
fill_pattern(unsigned char *b, size_t n, unsigned char seed)
{
	for (size_t i = 0; i < n; i++)
		b[i] = (unsigned char)(seed + i);
}

static void
test_roundtrip(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	unsigned char in[8 * FRAME], out[8 * FRAME];

	fill_pattern(in, sizeof(in), 1);
	CHECK(ring_write(r, in, sizeof(in)) == 0, "unexpected drop");
	CHECK(ring_fill(r) == sizeof(in), "fill %zu", ring_fill(r));
	CHECK(ring_read(r, out, sizeof(out)) == sizeof(out), "short read");
	CHECK(memcmp(in, out, sizeof(in)) == 0, "data mismatch");
	CHECK(ring_fill(r) == 0, "not drained");
	ring_free(r);
}

static void
test_wraparound(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	unsigned char in[10 * FRAME], out[10 * FRAME];

	/* Push the head past the midpoint so the next write must wrap. */
	fill_pattern(in, sizeof(in), 100);
	ring_write(r, in, sizeof(in));
	ring_read(r, out, sizeof(out));

	fill_pattern(in, sizeof(in), 200);
	CHECK(ring_write(r, in, sizeof(in)) == 0, "unexpected drop on wrap");
	CHECK(ring_read(r, out, sizeof(out)) == sizeof(out), "short read on wrap");
	CHECK(memcmp(in, out, sizeof(in)) == 0, "wrap corrupted the data");
	ring_free(r);
}

static void
test_overflow_drops_oldest(void)
{
	struct ring *r = ring_new(4 * FRAME, FRAME);
	unsigned char in[6 * FRAME], out[4 * FRAME];
	size_t dropped;

	fill_pattern(in, sizeof(in), 0);
	dropped = ring_write(r, in, sizeof(in));
	CHECK(dropped == 2 * FRAME, "dropped %zu, want %d", dropped, 2 * FRAME);
	CHECK(ring_fill(r) == 4 * FRAME, "fill %zu", ring_fill(r));

	/* What survives must be the NEWEST frames: the tail of the input. */
	ring_read(r, out, sizeof(out));
	CHECK(memcmp(out, in + 2 * FRAME, sizeof(out)) == 0,
	    "overflow kept the wrong frames");
	ring_free(r);
}

static void
test_frame_alignment(void)
{
	/* A capacity that is not a whole number of frames must be rounded down,
	   never left ragged: a partial frame would shift every later sample. */
	struct ring *r = ring_new(10 * FRAME + 3, FRAME);

	CHECK(ring_capacity(r) % FRAME == 0, "capacity %zu not frame aligned",
	    ring_capacity(r));
	CHECK(ring_capacity(r) == 10 * FRAME, "capacity %zu", ring_capacity(r));
	ring_free(r);
}

struct waiter {
	struct ring *r;
	size_t got;
	int    woke;
};

static void *
blocking_reader(void *arg)
{
	struct waiter *w = arg;
	unsigned char buf[4 * FRAME];

	w->got = ring_read(w->r, buf, sizeof(buf));
	w->woke = 1;
	return NULL;
}

static void
test_read_blocks_until_data(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	struct waiter w = { .r = r };
	unsigned char in[4 * FRAME];
	pthread_t tid;

	pthread_create(&tid, NULL, blocking_reader, &w);
	usleep(50000);
	CHECK(w.woke == 0, "reader did not block on an empty ring");

	fill_pattern(in, sizeof(in), 7);
	ring_write(r, in, sizeof(in));
	pthread_join(tid, NULL);
	CHECK(w.got == sizeof(in), "reader got %zu", w.got);
	ring_free(r);
}

static void
test_shutdown_wakes_reader(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	struct waiter w = { .r = r };
	pthread_t tid;

	pthread_create(&tid, NULL, blocking_reader, &w);
	usleep(50000);
	CHECK(w.woke == 0, "reader did not block");

	ring_shutdown(r);
	pthread_join(tid, NULL);		/* hangs here if shutdown is broken */
	CHECK(w.got == 0, "read returned %zu after shutdown", w.got);
	ring_free(r);
}

static void
test_interrupt_wakes_one_reader_without_shutdown(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	struct waiter w = { .r = r };
	unsigned char in[4 * FRAME], out[4 * FRAME];
	pthread_t tid;

	pthread_create(&tid, NULL, blocking_reader, &w);
	usleep(50000);
	ring_interrupt_reader(r);
	pthread_join(tid, NULL);
	CHECK(w.got == 0, "interrupted read returned %zu", w.got);

	/* Interruption is one-shot: the ring remains usable for the next episode. */
	fill_pattern(in, sizeof(in), 23);
	ring_write(r, in, sizeof(in));
	CHECK(ring_read(r, out, sizeof(out)) == sizeof(out),
	    "ring remained interrupted");
	CHECK(memcmp(in, out, sizeof(in)) == 0,
	    "interrupt damaged buffered data");
	ring_free(r);
}

static void
test_impossible_read_fails_instead_of_deadlocking(void)
{
	struct ring *r = ring_new(4 * FRAME, FRAME);
	unsigned char out[8 * FRAME];

	CHECK(ring_read(r, out, sizeof(out)) == 0,
	    "read larger than capacity did not fail");
	CHECK(ring_read(r, out, FRAME + 1) == 0,
	    "non-frame-aligned read did not fail");
	ring_free(r);
}

static void *
blocking_waiter(void *arg)
{
	struct waiter *w = arg;

	w->got = ring_wait_fill(w->r, 8 * FRAME) ? 1 : 0;
	w->woke = 1;
	return NULL;
}

static void
test_wait_fill(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	unsigned char in[8 * FRAME];

	fill_pattern(in, sizeof(in), 3);
	ring_write(r, in, sizeof(in));
	CHECK(ring_wait_fill(r, 8 * FRAME) == true,
	    "wait_fill blocked although the ring already held enough");
	CHECK(ring_wait_fill(r, 4 * FRAME) == true,
	    "wait_fill blocked on a smaller request");
	ring_free(r);
}

static void
test_wait_fill_wakes_on_shutdown(void)
{
	/* The pre-fill wait must not strand the playback thread when the
	   capture side dies before the lead is ever reached. */
	struct ring *r = ring_new(16 * FRAME, FRAME);
	struct waiter w = { .r = r };
	pthread_t tid;

	pthread_create(&tid, NULL, blocking_waiter, &w);
	usleep(50000);
	CHECK(w.woke == 0, "waiter did not block below the target fill");

	ring_shutdown(r);
	pthread_join(tid, NULL);		/* hangs here if shutdown is broken */
	CHECK(w.got == 0, "wait_fill returned true after shutdown");
	ring_free(r);
}


/* ── back-pressured write and end-of-data read (the WAV prefetch path) ──── */

struct writer {
	struct ring   *r;
	unsigned char *buf;
	size_t         n;
	size_t         wrote;
	int            done;
};

static void *
blocking_writer(void *arg)
{
	struct writer *w = arg;

	w->wrote = ring_write_full(w->r, w->buf, w->n);
	w->done = 1;
	return NULL;
}

static void
test_write_full_waits_for_space(void)
{
	/* The prefetch thread must never drop: asked for more than fits, it
	   has to wait for the reader rather than discard the oldest. */
	struct ring *r = ring_new(4 * FRAME, FRAME);
	unsigned char in[8 * FRAME], out[8 * FRAME];
	struct writer w = { .r = r, .buf = in, .n = sizeof(in) };
	pthread_t tid;

	fill_pattern(in, sizeof(in), 7);
	pthread_create(&tid, NULL, blocking_writer, &w);
	usleep(50000);
	CHECK(w.done == 0, "write_full returned without waiting for space");
	CHECK(ring_fill(r) == 4 * FRAME, "ring not full at %zu", ring_fill(r));

	CHECK(ring_read(r, out, 4 * FRAME) == 4 * FRAME, "short read");
	CHECK(ring_read(r, out + 4 * FRAME, 4 * FRAME) == 4 * FRAME,
	    "short second read");
	pthread_join(tid, NULL);

	CHECK(w.wrote == sizeof(in), "wrote %zu of %zu", w.wrote, sizeof(in));
	CHECK(memcmp(in, out, sizeof(in)) == 0,
	    "data corrupted across the back-pressure boundary");
	ring_free(r);
}

static void
test_write_full_wakes_on_shutdown(void)
{
	struct ring *r = ring_new(4 * FRAME, FRAME);
	unsigned char in[8 * FRAME];
	struct writer w = { .r = r, .buf = in, .n = sizeof(in) };
	pthread_t tid;

	fill_pattern(in, sizeof(in), 9);
	pthread_create(&tid, NULL, blocking_writer, &w);
	usleep(50000);
	CHECK(w.done == 0, "writer did not block on a full ring");

	ring_shutdown(r);
	pthread_join(tid, NULL);		/* hangs here if shutdown is broken */
	CHECK(w.wrote == 4 * FRAME, "wrote %zu, expected the 4 frames that fit",
	    w.wrote);
	ring_free(r);
}

static void
test_read_some_drains_after_eof(void)
{
	/* End of data must release the reader with the tail of the file, not
	   strand it waiting for frames that will never come. */
	struct ring *r = ring_new(16 * FRAME, FRAME);
	unsigned char in[3 * FRAME], out[8 * FRAME];

	fill_pattern(in, sizeof(in), 11);
	ring_write(r, in, sizeof(in));
	ring_set_eof(r);
	CHECK(ring_is_eof(r), "eof not reported");

	CHECK(ring_read_some(r, out, 8 * FRAME) == 3 * FRAME,
	    "did not hand back the partial tail");
	CHECK(memcmp(in, out, sizeof(in)) == 0, "tail data mismatch");
	CHECK(ring_read_some(r, out, 8 * FRAME) == 0,
	    "a drained ring at eof must report end, not block");
	ring_free(r);
}

static void
test_read_some_rounds_to_frames(void)
{
	/* A partial frame left by a short disk read must stay in the ring:
	   handing out half a frame would swap the channels downstream. */
	struct ring *r = ring_new(16 * FRAME, FRAME);
	unsigned char in[2 * FRAME + 3], out[8 * FRAME];

	fill_pattern(in, sizeof(in), 13);
	ring_write(r, in, sizeof(in));
	ring_set_eof(r);
	CHECK(ring_read_some(r, out, 8 * FRAME) == 2 * FRAME,
	    "returned a partial frame");
	CHECK(ring_fill(r) == 3, "the partial frame was not kept back");
	ring_free(r);
}

struct some_reader {
	struct ring   *r;
	unsigned char  buf[8 * FRAME];
	size_t         got;
	int            done;
};

static void *
blocking_read_some(void *arg)
{
	struct some_reader *sr = arg;

	sr->got = ring_read_some(sr->r, sr->buf, sizeof(sr->buf));
	sr->done = 1;
	return NULL;
}

static void
test_read_some_wakes_on_eof(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	struct some_reader sr = { .r = r };
	unsigned char in[3 * FRAME];
	pthread_t tid;

	fill_pattern(in, sizeof(in), 17);
	ring_write(r, in, sizeof(in));
	pthread_create(&tid, NULL, blocking_read_some, &sr);
	usleep(50000);
	CHECK(sr.done == 0, "read_some returned before the request was met");

	ring_set_eof(r);
	pthread_join(tid, NULL);		/* hangs here if eof does not wake */
	CHECK(sr.got == 3 * FRAME, "got %zu, expected the 3 buffered frames",
	    sr.got);
	ring_free(r);
}

static void
test_reset_clears_eof(void)
{
	/* run_session() reuses one ring across device reopens; a stale eof
	   would make every later read return end of data immediately. */
	struct ring *r = ring_new(16 * FRAME, FRAME);
	unsigned char in[2 * FRAME], out[2 * FRAME];

	ring_set_eof(r);
	ring_reset(r);
	CHECK(!ring_is_eof(r), "eof survived a reset");
	fill_pattern(in, sizeof(in), 19);
	ring_write(r, in, sizeof(in));
	CHECK(ring_read(r, out, sizeof(out)) == sizeof(out),
	    "the ring is unusable after a reset");
	ring_free(r);
}

/*
 * ring_keep_last() is what turns the idle silence into the pre-fill: when
 * audio returns, the ring holds seconds of the zeros that preceded it, and the
 * playback side trims to exactly one lead rather than clearing and waiting.
 * The property that matters is WHICH bytes survive — the newest ones, ending
 * at the sample that ended the silence.  Keeping the oldest instead would
 * still produce a correctly-sized lead and would still play; it would just
 * play the wrong seconds, and nothing downstream could tell.
 */
static void
test_keep_last_keeps_the_newest(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	unsigned char in[12 * FRAME], out[4 * FRAME];

	fill_pattern(in, sizeof(in), 0);
	ring_write(r, in, sizeof(in));
	CHECK(ring_keep_last(r, 4 * FRAME) == 8 * FRAME, "wrong drop count");
	CHECK(ring_fill(r) == 4 * FRAME, "fill %zu", ring_fill(r));
	CHECK(ring_read(r, out, sizeof(out)) == sizeof(out), "short read");
	CHECK(memcmp(out, in + 8 * FRAME, sizeof(out)) == 0,
	    "kept the oldest bytes instead of the newest");
	ring_free(r);
}

static void
test_keep_last_across_wraparound(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	unsigned char in[10 * FRAME], out[10 * FRAME], scratch[10 * FRAME];

	/* Leave the head past the midpoint so the data to keep straddles the
	   end of the buffer — the case a plain memmove would get wrong. */
	fill_pattern(scratch, sizeof(scratch), 200);
	ring_write(r, scratch, sizeof(scratch));
	ring_read(r, out, sizeof(out));

	fill_pattern(in, sizeof(in), 1);
	ring_write(r, in, sizeof(in));
	CHECK(ring_keep_last(r, 6 * FRAME) == 4 * FRAME, "wrong drop count");
	CHECK(ring_read(r, out, 6 * FRAME) == 6 * FRAME, "short read");
	CHECK(memcmp(out, in + 4 * FRAME, 6 * FRAME) == 0,
	    "wrapped data came back wrong");
	ring_free(r);
}

static void
test_keep_last_shorter_ring_is_untouched(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	unsigned char in[3 * FRAME], out[3 * FRAME];

	/* The first episode after startup has not captured a full lead yet;
	   trimming must then be a no-op, not a truncation, so the caller's
	   pre-fill wait still has something to wait for. */
	fill_pattern(in, sizeof(in), 7);
	ring_write(r, in, sizeof(in));
	CHECK(ring_keep_last(r, 8 * FRAME) == 0, "dropped from a short ring");
	CHECK(ring_fill(r) == sizeof(in), "fill %zu", ring_fill(r));
	CHECK(ring_read(r, out, sizeof(out)) == sizeof(out), "short read");
	CHECK(memcmp(in, out, sizeof(in)) == 0, "data mismatch");
	ring_free(r);
}

static void
test_keep_last_rounds_to_frames(void)
{
	struct ring *r = ring_new(16 * FRAME, FRAME);
	unsigned char in[8 * FRAME];

	/* A frame-straggling target would leave the reader one channel out of
	   phase for the rest of the episode: left samples emerging as right. */
	fill_pattern(in, sizeof(in), 3);
	ring_write(r, in, sizeof(in));
	ring_keep_last(r, 4 * FRAME + 3);
	CHECK(ring_fill(r) % FRAME == 0, "fill %zu is not frame aligned",
	    ring_fill(r));
	CHECK(ring_fill(r) == 4 * FRAME, "fill %zu", ring_fill(r));
	ring_free(r);
}

int
main(void)
{
	test_roundtrip();
	test_wraparound();
	test_overflow_drops_oldest();
	test_frame_alignment();
	test_read_blocks_until_data();
	test_shutdown_wakes_reader();
	test_interrupt_wakes_one_reader_without_shutdown();
	test_impossible_read_fails_instead_of_deadlocking();
	test_wait_fill();
	test_wait_fill_wakes_on_shutdown();
	test_write_full_waits_for_space();
	test_write_full_wakes_on_shutdown();
	test_read_some_drains_after_eof();
	test_read_some_rounds_to_frames();
	test_read_some_wakes_on_eof();
	test_reset_clears_eof();
	test_keep_last_keeps_the_newest();
	test_keep_last_across_wraparound();
	test_keep_last_shorter_ring_is_untouched();
	test_keep_last_rounds_to_frames();

	if (failures == 0)
		printf("all ring tests passed\n");
	return failures != 0;
}
