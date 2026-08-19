/*
 * omdrc-cdin — single-producer / single-consumer byte ring, frame aligned.
 *
 * Overflow policy is deliberately asymmetric:
 *
 *   write  never blocks.  If the capture thread blocked here, the OSS capture
 *          buffer would overrun instead — an uncontrolled loss we cannot
 *          measure.  Dropping our own oldest frames is the same amount of lost
 *          audio, but counted and logged.
 *   read   blocks until the requested amount is available, which is exactly
 *          "wait for the lead to be there".
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

struct ring;

struct ring *ring_new(size_t capacity_bytes, size_t frame_bytes);
void         ring_free(struct ring *r);

/* Copy n bytes in, discarding the oldest data if necessary.  Returns the
   number of bytes discarded (0 in normal operation). */
size_t ring_write(struct ring *r, const void *buf, size_t n);

/* Copy n bytes in, waiting for space instead of discarding.  For producers
   that CAN be back-pressured — a file being prefetched off disk — where
   dropping would corrupt the very signal the test is measuring.  Returns n,
   or a short count if the ring was shut down while waiting. */
size_t ring_write_full(struct ring *r, const void *buf, size_t n);

/* Block until n bytes are available, then copy them out.  Returns n, or 0 if
   the ring was shut down while waiting. */
size_t ring_read(struct ring *r, void *buf, size_t n);

/* Like ring_read(), but a producer that has signalled end of data releases
   the reader instead of stranding it: whatever is left is copied out (frame
   aligned, at most n).  Returns the byte count; 0 means the data is exhausted
   or the ring was shut down. */
size_t ring_read_some(struct ring *r, void *buf, size_t n);

/* Block until at least n bytes are buffered.  Returns false on shutdown. */
bool   ring_wait_fill(struct ring *r, size_t n);

/* Producer side: no more data will ever arrive.  Distinct from shutdown —
   readers may still drain what is buffered, they just stop waiting for more. */
void   ring_set_eof(struct ring *r);
bool   ring_is_eof(struct ring *r);

size_t ring_fill(struct ring *r);
size_t ring_capacity(struct ring *r);

/* Wake every blocked waiter and make all future waits fail. */
void   ring_shutdown(struct ring *r);
void   ring_reset(struct ring *r);
