/*
 * omdrc-cdin — shared definitions.
 *
 * Bridges an S/PDIF capture device (CD transport -> ESI U24 XL) into the
 * open-media-drc chain by writing into the virtual_oss loopback that BruteFIR
 * reads, i.e. the seat mpv already takes (see video/lib/drc-audio.sh).
 *
 * The two ends run on independent clocks: capture is slaved to the CD, the DAC
 * to its own crystal.  No resampler reconciles them.  Instead a lead of a few
 * seconds is held in the ring, and drift will eventually be absorbed during
 * the digital silence between tracks (TODO(phase2b)).  See doc/CD-INPUT.md for
 * the arithmetic: at 50 ppm a 2 s lead covers ~11 h of gapless audio, so drift
 * cannot cause a discontinuity inside a disc — which is why that resync is not
 * yet needed, and why the daemon can already be left running for weeks: the
 * silence between discs releases the output device rather than resyncing it.
 */
#pragma once

#include <stdbool.h>
#include <signal.h>
#include <stdatomic.h>

#define CDIN_VERSION "0.1.0"

/*
 * Set by the SIGINT/SIGTERM handler.  Blocking transfers in ossdev.c consult
 * it so that a signal interrupts them instead of being retried forever, while
 * an EINTR from anything else still resumes the transfer — a partial transfer
 * must not be abandoned mid-frame or the stream loses alignment.
 */
extern volatile sig_atomic_t cdin_stop;

/*
 * Set atomically while a session is being torn down (a device error, or a
 * reopen) without the whole daemon stopping.  Transfers stop retrying EINTR
 * and return, so worker threads can be joined before devices are closed.
 */
extern _Atomic bool cdin_io_abort;

/* SIGHUP interrupts playback without also aborting capture. */
extern _Atomic bool cdin_output_abort;
