/*
 * Which device the disc gets written to.
 *
 * The list is the policy: /dev/dsp.play first because a chain that is up
 * should carry the disc through BruteFIR, /dev/dsp.dac second because DRC off
 * still has to play.  These tests pin the parsing of that list — the ordering
 * it must preserve, and the malformed input it has to refuse rather than
 * silently reduce to something that would send audio to the wrong device.
 */
#include "outsel.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static void
test_single_entry_is_unchanged(void)
{
	struct outsel o;

	assert(outsel_parse(&o, "/dev/dsp.play") == 1);
	assert(strcmp(o.path[0], "/dev/dsp.play") == 0);
}

static void
test_order_is_preference_order(void)
{
	struct outsel o;

	assert(outsel_parse(&o, "/dev/dsp.play,/dev/dsp.dac") == 2);
	assert(strcmp(o.path[0], "/dev/dsp.play") == 0);
	assert(strcmp(o.path[1], "/dev/dsp.dac") == 0);
}

static void
test_blanks_and_empty_entries_are_ignored(void)
{
	struct outsel o;

	/* An rc.conf line survives being edited by hand. */
	assert(outsel_parse(&o, " /dev/dsp.play ,, /dev/dsp.dac ") == 2);
	assert(strcmp(o.path[0], "/dev/dsp.play") == 0);
	assert(strcmp(o.path[1], "/dev/dsp.dac") == 0);
}

static void
test_nothing_usable_is_an_error(void)
{
	struct outsel o;

	/* Not "fall back to a default": a list that says nothing must stop the
	 * daemon, because guessing here means guessing which device gets the
	 * audio. */
	assert(outsel_parse(&o, "") == -1);
	assert(outsel_parse(&o, "   ") == -1);
	assert(outsel_parse(&o, ",,,") == -1);
	assert(outsel_parse(&o, NULL) == -1);
}

static void
test_too_many_entries_is_an_error(void)
{
	struct outsel o;

	assert(outsel_parse(&o, "a,b,c,d") == OUTSEL_MAX);
	assert(outsel_parse(&o, "a,b,c,d,e") == -1);
}

static void
test_an_overlong_list_is_refused_not_truncated(void)
{
	struct outsel o;
	char big[OUTSEL_STORAGE + 16];

	memset(big, 'x', sizeof(big) - 1);
	big[sizeof(big) - 1] = '\0';
	/* Truncating a device path would name a DIFFERENT device. */
	assert(outsel_parse(&o, big) == -1);
}

static void
test_describe_reads_as_a_sentence(void)
{
	struct outsel o;
	char buf[128];

	assert(outsel_parse(&o, "/dev/dsp.play,/dev/dsp.dac") == 2);
	assert(strcmp(outsel_describe(&o, buf, sizeof(buf)),
	    "/dev/dsp.play or /dev/dsp.dac") == 0);

	assert(outsel_parse(&o, "/dev/dsp.play") == 1);
	assert(strcmp(outsel_describe(&o, buf, sizeof(buf)),
	    "/dev/dsp.play") == 0);
}

int
main(void)
{
	test_single_entry_is_unchanged();
	test_order_is_preference_order();
	test_blanks_and_empty_entries_are_ignored();
	test_nothing_usable_is_an_error();
	test_too_many_entries_is_an_error();
	test_an_overlong_list_is_refused_not_truncated();
	test_describe_reads_as_a_sentence();
	printf("outsel: all tests passed\n");
	return 0;
}
