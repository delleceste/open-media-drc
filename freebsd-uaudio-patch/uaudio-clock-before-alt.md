# `uaudio(4)` clock-before-alt reorder — rate-change cold-open silence fix candidate

**Status: built OK, installed, and partially listening-tested (2026-07-06,
FreeBSD 15.1, USB_DEBUG, `-Werror` clean).** Module built from a tree that
also has `uaudio.c.patch` (capture-disable) applied, and installed to
`/boot/kernel/snd_uaudio.ko` (stock backed up at
`/boot/kernel/snd_uaudio.ko.orig`). **First listening results:** with
`DAC_PRIME_CYCLES=0`, a cold open at 192 kHz was audible on the first try, and
a 192 kHz → 44.1 kHz crystal switch was audible with zero primes. Both were
single-attempt spot checks; the full test-plan matrix below (reverse crystal
switch, same-family changes, MPD-direct mixed-rate queue, virtual_oss,
browser) was **cut short by an unrelated virtual_oss livelock** (see
[`../freebsd-virtual-oss-patch/ROOTCAUSE-settrigger-sync-engine-deadlock.md`](../freebsd-virtual-oss-patch/ROOTCAUSE-settrigger-sync-engine-deadlock.md))
and still needs to be run to completion after a reboot.

Patch: [`uaudio-clock-before-alt.c.patch`](uaudio-clock-before-alt.c.patch)

## What it targets

The **rate-change cold-open silence** (`../OKTO-DAC8-silent-first-open.md`):
on the first open after a sample-rate change the OKTO DAC8 shows the correct
rate on its display and streams healthy USB, but **routes no audio**.

> **Scope update 2026-07-06 (user report):** this is NOT limited to
> 44.1 kHz ↔ 48 kHz master-crystal crossings — **any rate change** (playing
> files or virtual_oss at different rates) can produce the silent open. The
> earlier listening tests that narrowed it to crystal switches were too
> optimistic. This also explains why silence still occurs with the current
> workaround in place: `drc.sh`'s `DAC_PRIME_CYCLES` prime is keyed on
> *crystal-family* crossings and **skips same-family rate changes** — and it
> only covers the drc.sh path anyway, not MPD-direct rate changes,
> virtual_oss, or the `*-nodrc` browser launchers.

This is a **driver-level** fix candidate that triggers on **every rate
change** (same-family included) and covers *every* client of `/dev/dsp0`.

## The ordering bug

Stock `uaudio_configure_msg_sub()` starts a stream in this order:

1. `SET_INTERFACE` → **streaming** alt-setting (arms the device's stream path)
2. `SET_CUR` sample rate on the UAC2 Clock Source (**possibly a crystal
   switch**) — yanked underneath the already-armed interface
3. start isochronous transfers immediately

Linux `snd-usb-audio` — which plays the first open fine on the same hardware —
does the opposite: it parks the interface at **alt 0**, programs the clock
while the streaming interface is idle, *then* selects the streaming
alt-setting and starts URBs.

If the Thesycon firmware latches its internal stream/routing configuration at
`SET_INTERFACE` time, FreeBSD's order arms the stream against the *old*
crystal and then switches the clock under it → the whole stream renders
silent. It also explains exactly why the open/close prime works: every close
issues `SET_INTERFACE(alt 0)`, so the *next* open's `SET_INTERFACE(alt N)`
happens with the clock already on the target crystal.

## What the patch does

In the `CHAN_OP_START` path, for UAC2 devices only:

1. **Park** the streaming interface at alt 0 first (free no-op wire-wise when
   already parked — `usbd_set_alt_interface_index()` short-circuits equal
   indices; only costs a request in reconfigure-while-running cases).
2. **Program the clock** (`SET_CUR` on every clock entity for the direction,
   unchanged loop) while the streaming interface is idle. Legal at any time:
   the UAC2 clock lives on the AudioControl interface, not the AS interface.
3. On a genuine **rate change** (tracked in `sc->sc_clock_rate`), sleep
   `hw.usb.uaudio.clock_settle_ms` (default **100**, `RWTUN`, clamped to
   2000) so a crystal relock can complete before the stream is armed.
4. Then `SET_INTERFACE` → streaming alt, transfer setup, start — as before.

UAC1 devices are untouched (their rate control lives on the streaming
*endpoint*, so the alt-setting genuinely must be selected first — the
original placement stays for them). UAC3 behaviour is unchanged (no standard
rate control was programmed before either; the misleading `/* FALLTHROUGH */`
comment is corrected).

## Why this is not the reverted clock-valid patch

`uaudio20_wait_clock_valid` (removed 2026-06-21) polled the Clock Validity
control *after* the alt-setting was already selected — and the OKTO reports
valid in 0 ms regardless, so it was a no-op **and it left the broken ordering
in place**. This patch changes the *ordering*; the settle delay is a fixed,
tunable pause (not validity-gated), applied only on an actual rate change,
**before** the alt-setting arms the stream.

## Test plan (listening tests — the only signal that counts)

Machine signals cannot verify this (`feedback_rate`/validity look healthy while
silent). Install per [`README.md`](README.md), then:

1. `DAC_PRIME_CYCLES=0` in the environment / config so drc.sh does **not**
   mask the result.
2. Park the DAC on the 48k crystal (`drc.sh 192000`), then a single cold
   `drc.sh 44100` → must be audible first try.
3. Park on the 44.1k crystal (`drc.sh 44100`), then a single `drc.sh resamp`
   (→192k) → must be audible first try.
4. **Same-crystal changes too** (per the 2026-07-06 scope update): 48k→96k,
   44.1k→88.2k, and back → audible first try each time.
5. MPD-direct (`drc.sh off`) playing a queue of mixed-rate tracks
   (44.1/48/88.2/96k) → every track audible from its first sample.
6. virtual_oss chain at a different rate than the previous stream → audible.
7. Browser (`firefox-nodrc`) first launch after a rate change → audible
   (also remove/disable the launcher's `dd` prime for the test).

If a crossing is still silent, raise `sysctl hw.usb.uaudio.clock_settle_ms`
(try 500, 1000 — runtime tunable, no rebuild) — that discriminates "ordering
fix insufficient" from "relock needs more time". If it works at 100 ms, try 0
to learn whether the reorder alone suffices.

If confirmed by ear: set `DAC_PRIME_CYCLES=0` permanently, drop the browser
launcher prime, and this (plus the shared-clock guard for the flicker) is the
shape to propose upstream.

## Interaction with the other patches

- **2026-07-07 update:** the capture-disable workaround (`uaudio.c.patch`)
  has been **retired** and replaced by
  [`uaudio-shared-clock-fix.c.patch`](uaudio-shared-clock-fix.c.patch); the
  current tree/build is stock + this patch + shared-clock-fix. On a
  capture-enabled driver, async playback also starts the capture channel for
  jitter info; the shared-clock fix rate-aligns that stream and guards the
  shared clock — this patch does not address the flicker, only the ordering.
- **Replaces the need for** the reverted `uaudio-clock-valid` approach.
- **Complements** the shared-clock fix: the fix stops a secondary stream from
  reprogramming a busy shared clock; this patch fixes *when* the clock is
  programmed relative to `SET_INTERFACE`.
