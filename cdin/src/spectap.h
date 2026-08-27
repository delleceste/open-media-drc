/*
 * spectap — a lossy, non-blocking copy of the playback stream on a FIFO.
 *
 * The control panel's spectrum analyzer is a FIFO reader: it FFTs raw S32_LE
 * stereo and knows nothing about who wrote it.  It was built against MPD's
 * secondary `fifo` output, which is empty for the whole of a disc — CD audio
 * never passes through MPD, it goes capture -> this daemon -> the output.  So
 * the analyzer sees nothing during CD playback unless the bridge hands it the
 * same PCM, which is all this is.
 *
 * Three properties matter, in this order:
 *
 *   1. It can NEVER stall playback.  The fd is O_NONBLOCK and a short or
 *      EAGAIN write is discarded, not retried.  A spectrum display is worth
 *      exactly zero dropouts, so a reader that stops draining loses frames
 *      rather than backing up into the audio path.
 *   2. The reader's presence is the whole protocol.  Opening a FIFO O_WRONLY
 *      fails with ENXIO while nobody holds the read end, so the daemon simply
 *      retries on a slow cadence and starts teeing the moment the analyzer
 *      opens it.  When the analyzer goes away the next write gets EPIPE and
 *      the tap closes and goes back to waiting.  No IPC, no state to keep in
 *      sync, nothing to clean up after a crash on either side.
 *   3. It never creates the FIFO.  omdrcctrl does that, as its own user, with
 *      its own permissions; this daemon usually runs as root and a root-made
 *      world-writable FIFO in a shared directory is a hazard for a feature
 *      nobody asked to be always on.  No FIFO means no tap, which is the
 *      correct behaviour when the analyzer is not configured.
 */
#ifndef CDIN_SPECTAP_H
#define CDIN_SPECTAP_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct spectap;

/*
 * Create a tap for `path`.  Returns NULL when `path` is NULL or empty, which
 * is the disabled case and is not an error: spectap_write() and
 * spectap_close() both accept NULL, so callers need no conditionals.
 * `retry_secs` bounds how often a closed tap re-probes for a reader.
 */
struct spectap *spectap_open(const char *path, int retry_secs);

/*
 * Offer `n` bytes to the tap.  Never blocks, never fails, never reports: a
 * caller in the playback path must not have to care about the outcome.
 */
void spectap_write(struct spectap *t, const void *buf, size_t n);

/* Bytes handed over and bytes dropped since the tap was created. */
void spectap_stats(const struct spectap *t, uint64_t *written, uint64_t *dropped);

/* True while a reader is attached — the CD card reports this in its stats. */
bool spectap_attached(const struct spectap *t);

void spectap_close(struct spectap *t);

#endif /* CDIN_SPECTAP_H */
