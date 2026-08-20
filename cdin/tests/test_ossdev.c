#include <signal.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>

#include "cdin.h"
#include "ossdev.h"

/* ossdev.c's transfer helpers consult the daemon flags.  These tests exercise
   only the pure fragment calculation, but the complete object still links. */
volatile sig_atomic_t cdin_stop;
_Atomic bool cdin_io_abort;
_Atomic bool cdin_output_abort;

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
test_fragment_rounding(void)
{
	CHECK(ossdev_fragment_bytes(4096) == 4096,
	    "an exact fragment changed size");
	CHECK(ossdev_fragment_bytes(6144) == 8192,
	    "packed 24-bit stereo period did not round to 8192");
	CHECK(ossdev_fragment_bytes(1) == 16,
	    "minimum fragment is not 16 bytes");
	CHECK(ossdev_fragment_bytes(100000) == 65536,
	    "maximum fragment is not capped at 64 KiB");
}

int
main(void)
{
	test_fragment_rounding();
	if (failures == 0)
		printf("all ossdev tests passed\n");
	return failures != 0;
}
