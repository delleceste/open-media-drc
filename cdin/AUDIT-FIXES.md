# omdrc-cdin audit fixes

This note records the code audit performed against `README.md`, the audible or
operational consequence of each defect, and the correction on branch
`codex/cdin-audit-fixes`.

## Fixed defects

### Carrier loss cut the buffered lead immediately

A capture short-read or error set the session-wide failure flag and shut the
ring down. Playback consequently abandoned the lead and closed the device at an
arbitrary PCM sample, contrary to the documented rule that Stop is heard one
lead late.

Capture termination is now represented as ring EOF, distinct from shutdown.
An episode which already holds the output drains every buffered frame before
the input is reopened. A partial final capture read is retained rather than
discarded.

### WAV EOF discarded the final sub-period tail

The old drain loop stopped as soon as less than one application period remained,
while playback only requested full periods. Most WAV lengths are not multiples
of 1024 frames, so the last frames could be omitted and the output cut abruptly.

Playback now uses the ring's EOF-aware short read while draining, preserves all
complete source frames, and pads only the remainder of the final OSS write with
exact digital silence.

### SIGHUP could not release a blocked output

The capture thread alone processed the release flag. A playback thread blocked
in `write()` or waiting on an empty ring could therefore retain the
`/dev/dsp.play` CUSE handle indefinitely—the exact condition SIGHUP exists to
prevent during a virtual_oss restart.

The main session loop now dispatches release independently of capture, changes
state before playback can reacquire the device, interrupts a blocked output
write, and has a one-shot ring-reader interruption for the empty-ring case.
Capture observes a generation counter and applies the normal reacquisition
hold-off when it next runs.

### Packed 24-bit stereo could never open

Application period bytes were required to be a power of two. Packed S24 stereo
uses six-byte frames, and no positive multiple of six is a power of two, so
advertised 24-bit capture failed before opening the device.

Application transfer periods and OSS fragment sizes are now separate. The OSS
fragment is the next power of two (within its 16-byte to 64-KiB range), while
application reads and writes remain frame-aligned and may span fragments.

### Forced narrowing silently produced silence

The first forced `--out-bits` candidate bypassed widening-policy validation.
The capture path then treated any unequal widths as widening; the converter's
safe fallback for an unsupported pair emitted zeros.

Forced output width is now rejected unless it equals the source width or is a
supported lossless widening. The check is repeated after a WAV source replaces
the command-line input format.

### Percent-scale rate substitution invalidated the lead proof

OSS setup accepted a reported rate almost one percent away from the requested
rate. Such a mismatch drains a two-second lead in minutes, not the hours implied
by crystal drift, and all lead/stat calculations still used the requested rate.

The configured nominal rate must now be accepted exactly. Independent hardware
clock ppm remains visible as ring drift, which is the design being measured.

### Output-open delays discarded audio without counting it

If playback-device acquisition took longer than one lead, the episode trimmed
older captured audio before starting but described it as harmless and left the
drop counter unchanged. Playback could begin at an arbitrary waveform position
while diagnostics still reported no loss.

Such trimming is now logged as a warning and added to `drops`, invalidating the
drift reference in the same way as every other discontinuity.

### Inter-thread control flags had data races

`cdin_io_abort` was `volatile sig_atomic_t` but was read and written by ordinary
threads. ThreadSanitizer confirmed races on the abort/stop control path.

The inter-thread abort flags are now C atomics. `cdin_stop` remains the minimal
`volatile sig_atomic_t` flag written by SIGINT/SIGTERM handlers, and ordinary
worker code no longer writes it to end a file-source session.

### Impossible ring reads could wait forever

A ring shorter than one playback period made `ring_read()` wait for a fill level
the ring could never reach. Runtime configuration now rejects that geometry,
