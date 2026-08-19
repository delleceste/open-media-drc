/*
 * omdrc-cdin — WAV file source, used in place of a capture device.
 *
 * A file has no clock, so it is *paced*: frames are handed out on a monotonic
 * deadline at the file's nominal rate, which is what a real S/PDIF capture
 * device does.  Without pacing the ring would fill instantly and the
 * drop-oldest policy would discard almost everything.
 *
 * The pace can be offset by a configurable ppm, which turns this into the
 * drift rig: the daemon's whole design rests on how the lead behaves when the
 * source and the DAC disagree, and this makes that testable without waiting
 * hours for a real few-ppm difference to show.
 *
 * The medium is deliberately NOT part of the emulation.  A capture device
 * delivers on the USB isochronous schedule and cannot stall; a WAV on an
 * external disk can block for seconds, and inline reads put that latency
 * straight onto the daemon's lead.  So the file is prefetched on its own
 * thread and the paced read is served from RAM — the rig emulates the CD
 * player's clock, not the disk's seek time.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

struct filesrc;

/*
 * ppm offsets the emitted rate: +1000 means the source runs 0.1% fast, so the
 * lead grows; negative drains it.
 */
struct filesrc *filesrc_open(const char *path, bool loop, double ppm,
	    char *err, size_t errsz);
void            filesrc_close(struct filesrc *f);

int    filesrc_rate(const struct filesrc *f);
int    filesrc_channels(const struct filesrc *f);
int    filesrc_bits(const struct filesrc *f);
double filesrc_seconds(const struct filesrc *f);

/* Block until the emulated clock says these frames are due, then deliver them.
   Returns n, a short count at end of data, or -1 on a read error. */
ssize_t filesrc_read(struct filesrc *f, void *buf, size_t n);

/* Rig health, not audio health: stalls counts prefetch underruns (the medium
   could not sustain realtime), slips counts times the pacing schedule was
   shifted rather than allowed to burst.  Both should be 0. */
uint64_t filesrc_stalls(const struct filesrc *f);
uint64_t filesrc_slips(const struct filesrc *f);
