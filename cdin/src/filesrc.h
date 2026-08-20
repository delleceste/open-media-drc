/*
 * omdrc-cdin — a simulated CD transport, used in place of a capture device.
 *
 * The rig stands in for everything upstream of /dev/dspN: the disc, the
 * transport, and the S/PDIF link.  What it emulates is the CD player's *clock*
 * and the *shape* of its output — not the storage it happens to read from.
 *
 *   Clock.  A file has none, so frames are handed out on a monotonic deadline
 *           at 44.1 kHz, which is what a real capture device does.  The pace
 *           can be offset by a configurable ppm, which turns this into the
 *           drift rig: the daemon's whole design rests on how the lead behaves
 *           when the source and the DAC disagree, and this makes that testable
 *           without waiting hours for a real few-ppm difference to show.
 *
 *   Disc.   Point --in at a directory and its WAVs become the tracks of a
 *           disc, played in order with Red Book's 2 s of exact digital silence
 *           between them.  That silence is not decoration: it is what the
 *           silence gate looks for, so a --gap longer than --idle-after is how
 *           the output release/re-acquire cycle is exercised without a CD
 *           player.  A single file is simply a one-track disc.
 *
 *   Transport.  --transport scripts the buttons: skip, prev, seek, pause, stop,
 *           and the carrier dropout that README.md calls the real hazard.  Each
 *           produces on the wire what the corresponding action produces on a
 *           real player (see the table in README.md).
 *
 * The medium is deliberately NOT part of the emulation.  A capture device
 * delivers on the USB isochronous schedule and cannot stall; a WAV on an
 * external disk can block for seconds, and inline reads put that latency
 * straight onto the daemon's lead.  So the disc is prefetched on its own
 * thread and the paced read is served from RAM — the rig emulates the CD
 * player's clock, not the disk's seek time.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

struct filesrc;

struct filesrc_cfg {
	const char *path;	/* a WAV file, or a directory of them = a disc */
	bool        loop;	/* restart the disc at the end */
	double      ppm;	/* +n means the source runs fast, so the lead grows */
	int         gap_ms;	/* digital silence between tracks (Red Book: 2000) */
	const char *transport;	/* event script, or NULL — see filesrc_open() */
};

/*
 * transport is a comma-separated list of AT:EVENT, where AT is seconds into
 * the stream:
 *
 *   skip          next track, after the brief mute a real sled makes
 *   prev          previous track, likewise
 *   seek=[+-]N    jump N seconds within the track (fast forward / rewind)
 *   pause=N       N seconds of digital silence; the carrier stays up and the
 *                 position is held, which is what Pause does on most players
 *   dropout=N     the carrier drops for N MILLISECONDS and no frames arrive at
 *                 all.  This is the one event that eats the lead, and it is the
 *                 hazard a seconds-scale lead exists to absorb
 *   stop          the carrier drops for good and the stream ends
 *
 * e.g. "30:skip,45:pause=4,60:dropout=800,90:stop"
 */
struct filesrc *filesrc_open(const struct filesrc_cfg *cfg, char *err,
	    size_t errsz);
void            filesrc_close(struct filesrc *f);

int    filesrc_rate(const struct filesrc *f);
int    filesrc_channels(const struct filesrc *f);
int    filesrc_bits(const struct filesrc *f);
int    filesrc_tracks(const struct filesrc *f);
double filesrc_seconds(const struct filesrc *f);	/* the whole disc */

/* Block until the emulated clock says these frames are due, then deliver them.
   Returns n, a short count when the disc ends or the carrier drops, or -1 on a
   read error. */
ssize_t filesrc_read(struct filesrc *f, void *buf, size_t n);

/* Rig health, not audio health: stalls counts prefetch underruns (the medium
   could not sustain realtime), slips counts times the pacing schedule was
   shifted rather than allowed to burst.  Both should be 0 — they mean the host
   could not feed the rig, which is a fault of neither the daemon nor the
   design.  Simulated dropouts are deliberate and counted separately. */
uint64_t filesrc_stalls(struct filesrc *f);
uint64_t filesrc_slips(struct filesrc *f);
uint64_t filesrc_dropouts(struct filesrc *f);

/* True when the stream ended because the simulated carrier dropped (a `stop`
   event) rather than because the disc ran out.  The state machine has to tell
   these apart — one reopens the device, the other does not — so the rig must
   be able to produce both. */
bool filesrc_carrier_lost(struct filesrc *f);
