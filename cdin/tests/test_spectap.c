/*
 * Spectrum-tap tests.
 *
 * This tap sits in the playback path, one line above the write to the DAC, so
 * its failure modes are not "the spectrum looks wrong" — they are dropouts on
 * a disc that is playing.  Every property that keeps it harmless is asserted
 * here rather than trusted:
 *
 *   * a missing FIFO, or one nobody is reading, must cost nothing and must
 *     never block;
 *   * a reader that stops draining must lose bytes, not stall the writer —
 *     this is the one that would be audible;
 *   * a reader that goes away must be noticed, so the tap stops writing into
 *     a dead pipe and re-arms for the next time the analyzer opens it.
 *
 * The pipe buffer is finite and small (64 KiB typical), which is what makes
 * the drop test possible: write more than that without reading and a blocking
 * implementation would hang the test instead of failing it.
 */
#include <sys/stat.h>
#include <sys/types.h>

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "log.h"
#include "spectap.h"

static int failures;

#define CHECK(cond, ...) do {                                            \
	if (!(cond)) {                                                   \
		printf("FAIL %s:%d: ", __func__, __LINE__);              \
		printf(__VA_ARGS__);                                     \
		printf("\n");                                            \
		failures++;                                              \
	}                                                                \
} while (0)

#define PERIOD 4096

static char fifo_path[256];
static unsigned char period[PERIOD];

static void
make_fifo(void)
{
	snprintf(fifo_path, sizeof(fifo_path), "/tmp/omdrc-spectap-test.%ld.%d",
	    (long)time(NULL), (int)getpid());
	unlink(fifo_path);
	if (mkfifo(fifo_path, 0600) != 0) {
		printf("FAIL: cannot create %s: %s\n", fifo_path, strerror(errno));
		exit(1);
	}
}

/*
 * The tap only re-probes for a reader every retry_secs, so a test that opens
 * the read end must wait out one retry window before expecting bytes.  Built
 * with retry 1 and one spare second so this does not become a flaky test.
 */
static void
settle(void)
{
	struct timespec ts = { .tv_sec = 2, .tv_nsec = 0 };

	nanosleep(&ts, NULL);
}

static void
test_no_path_is_disabled_not_an_error(void)
{
	CHECK(spectap_open(NULL, 1) == NULL, "NULL path should disable the tap");
	CHECK(spectap_open("", 1) == NULL, "empty path should disable the tap");
	/* The disabled tap must be safe to use, so callers need no branch. */
	spectap_write(NULL, period, sizeof(period));
	spectap_close(NULL);
}

static void
test_missing_fifo_never_blocks_and_never_writes(void)
{
	struct spectap *t;
	uint64_t written = 1, dropped = 1;
	int i;

	/* Deliberately not created: the daemon does not create it, and a path
	   that is simply absent is the normal "analyzer not configured" case. */
	t = spectap_open("/tmp/omdrc-spectap-test.does-not-exist", 1);
	CHECK(t != NULL, "a path should still produce a tap object");
	for (i = 0; i < 100; i++)
		spectap_write(t, period, sizeof(period));
	CHECK(!spectap_attached(t), "must not report a reader when there is no FIFO");
	spectap_stats(t, &written, &dropped);
	CHECK(written == 0, "wrote %llu bytes with no FIFO",
	    (unsigned long long)written);
	spectap_close(t);
}

static void
test_no_reader_costs_nothing(void)
{
	struct spectap *t;
	uint64_t written = 1;
	int i;

	make_fifo();
	t = spectap_open(fifo_path, 1);
	for (i = 0; i < 100; i++)
		spectap_write(t, period, sizeof(period));
	CHECK(!spectap_attached(t), "attached with nobody holding the read end");
	spectap_stats(t, &written, NULL);
	CHECK(written == 0, "wrote %llu bytes with no reader",
	    (unsigned long long)written);
	spectap_close(t);
	unlink(fifo_path);
}

static void
test_a_reader_gets_the_bytes_unchanged(void)
{
	struct spectap *t;
	unsigned char got[PERIOD];
	uint64_t written = 0;
	int rfd;
	ssize_t n;
	size_t i;

	make_fifo();
	t = spectap_open(fifo_path, 1);
	rfd = open(fifo_path, O_RDONLY | O_NONBLOCK);
	CHECK(rfd >= 0, "cannot open the read end: %s", strerror(errno));

	spectap_write(t, period, sizeof(period));	/* triggers the attach */
	settle();
	spectap_write(t, period, sizeof(period));
	CHECK(spectap_attached(t), "did not attach with a reader present");

	n = read(rfd, got, sizeof(got));
	CHECK(n == (ssize_t)sizeof(got), "read %zd of %zu bytes", n, sizeof(got));
	if (n == (ssize_t)sizeof(got)) {
		for (i = 0; i < sizeof(got); i++)
			if (got[i] != period[i])
				break;
		CHECK(i == sizeof(got), "byte %zu differs: the tap must copy "
		    "the period verbatim", i);
	}
	spectap_stats(t, &written, NULL);
	CHECK(written >= sizeof(period), "accounted %llu bytes written",
	    (unsigned long long)written);

	close(rfd);
	spectap_close(t);
	unlink(fifo_path);
}

static void
test_a_reader_that_stops_draining_loses_bytes_instead_of_blocking(void)
{
	struct spectap *t;
	uint64_t written = 0, dropped = 0;
	int rfd, i;

	make_fifo();
	t = spectap_open(fifo_path, 1);
	rfd = open(fifo_path, O_RDONLY | O_NONBLOCK);
	CHECK(rfd >= 0, "cannot open the read end: %s", strerror(errno));

	spectap_write(t, period, sizeof(period));
	settle();

	/*
	 * Far more than any pipe buffer, and never read.  If spectap_write()
	 * blocked, this loop would not return — which in the real daemon is a
	 * stalled playback thread and an audible dropout.
	 */
	for (i = 0; i < 512; i++)
		spectap_write(t, period, sizeof(period));

	spectap_stats(t, &written, &dropped);
	CHECK(dropped > 0, "a full pipe must drop; dropped %llu",
	    (unsigned long long)dropped);
	CHECK(written + dropped >= 512 * (uint64_t)sizeof(period),
	    "every offered byte must be accounted: %llu written + %llu dropped",
	    (unsigned long long)written, (unsigned long long)dropped);
	/* Still attached: a slow reader is not a gone reader. */
	CHECK(spectap_attached(t), "dropping must not detach a live reader");

	close(rfd);
	spectap_close(t);
	unlink(fifo_path);
}

static void
test_a_departed_reader_detaches_and_the_tap_rearms(void)
{
	struct spectap *t;
	int rfd, i;

	make_fifo();
	t = spectap_open(fifo_path, 1);
	rfd = open(fifo_path, O_RDONLY | O_NONBLOCK);
	CHECK(rfd >= 0, "cannot open the read end: %s", strerror(errno));
	spectap_write(t, period, sizeof(period));
	settle();
	spectap_write(t, period, sizeof(period));
	CHECK(spectap_attached(t), "did not attach with a reader present");

	/* The analyzer stops: EPIPE on the next write is how the tap finds out. */
	close(rfd);
	for (i = 0; i < 4; i++)
		spectap_write(t, period, sizeof(period));
	CHECK(!spectap_attached(t), "must detach once the reader closes");

	/* And it must come back when the analyzer is started again. */
	rfd = open(fifo_path, O_RDONLY | O_NONBLOCK);
	CHECK(rfd >= 0, "cannot reopen the read end: %s", strerror(errno));
	settle();
	spectap_write(t, period, sizeof(period));
	CHECK(spectap_attached(t), "must re-attach when a reader returns");

	close(rfd);
	spectap_close(t);
	unlink(fifo_path);
}

int
main(void)
{
	size_t i;

	cdin_log_init(NULL, CDL_ERR);	/* keep the expected chatter out of the run */
	for (i = 0; i < sizeof(period); i++)
		period[i] = (unsigned char)(i * 7 + 1);

	test_no_path_is_disabled_not_an_error();
	test_missing_fifo_never_blocks_and_never_writes();
	test_no_reader_costs_nothing();
	test_a_reader_gets_the_bytes_unchanged();
	test_a_reader_that_stops_draining_loses_bytes_instead_of_blocking();
	test_a_departed_reader_detaches_and_the_tap_rearms();

	cdin_log_close();
	if (failures == 0)
		printf("all spectap tests passed\n");
	return failures != 0;
}
