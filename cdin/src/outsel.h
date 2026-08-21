/*
 * omdrc-cdin — choosing which device to write the disc to.
 *
 * There are two of them and which one is right depends on something outside
 * this daemon: whether the DRC chain is up.
 *
 *   /dev/dsp.play   virtual_oss's loopback.  Exists only while drc.sh has the
 *                   chain running, and is the correct target then: the disc
 *                   goes through BruteFIR and gets the room correction.
 *   /dev/dsp.dac    the DAC itself.  The right target when DRC is off, and the
 *                   exact mirror of what MPD does — drc.sh switches MPD
 *                   between its "DRC-native" and "OKTO-DAC" outputs for the
 *                   same reason.
 *
 * So the output is a PREFERENCE LIST, tried in order, first one that opens
 * wins.  That the loopback comes first is the whole policy: when both exist,
 * corrected audio beats uncorrected.
 *
 * The choice is made once, at startup, and never re-taken while the daemon
 * runs.  That is not laziness — the ring is laid out in the width the chosen
 * device negotiated (see open_playback_negotiated in main.c), so switching
 * devices mid-flight would mean re-laying a buffer that already has frames in
 * it.  Changing the answer means restarting the daemon, which is what drc.sh
 * does when it brings the chain up or takes it down.
 */
#pragma once

#include <stddef.h>

/* Two is the real answer (loopback, DAC); the slack is for a box that wants a
 * third target, and bounds the storage below. */
#define OUTSEL_MAX 4
#define OUTSEL_STORAGE 512

struct outsel {
	char        storage[OUTSEL_STORAGE];	/* the list, comma -> NUL */
	const char *path[OUTSEL_MAX];		/* into storage, in order */
	int         count;
};

/*
 * Split a comma-separated device list into candidates, in the order given.
 * Surrounding blanks are ignored and empty entries are dropped, so
 * "/dev/dsp.play, /dev/dsp.dac" and "/dev/dsp.play,/dev/dsp.dac" are the same
 * list.  Returns the number of candidates, or -1 when the list is empty, has
 * more than OUTSEL_MAX entries, or does not fit OUTSEL_STORAGE.
 */
int outsel_parse(struct outsel *o, const char *list);

/*
 * The list as it was given, rebuilt for a log line ("/dev/dsp.play or
 * /dev/dsp.dac").  Returns buf.
 */
const char *outsel_describe(const struct outsel *o, char *buf, size_t bufsz);
