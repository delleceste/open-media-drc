# FreeBSD `uaudio(4)` — missing clock-validity wait on rate change (44.1 kHz cold-open silence)

Second, independent `uaudio(4)` fix for the OKTO DAC8 STEREO. The first patch
(`uaudio.c.patch`, see [`README.md`](README.md) /
[`FreeBSD-uaudio-shared-clock-bug.md`](FreeBSD-uaudio-shared-clock-bug.md))
stops the idle capture channel from *yanking* the shared clock back to 48 kHz
(the continuous flicker). This patch *targets* a **different** bug exposed underneath it: the **first open
at a 44.1 kHz-family rate plays silent**.

> ## ⚠️ RESULT: this patch is USELESS on this DAC (tested 2026-06-21)
>
> Built into `snd_uaudio.ko` on FreeBSD 15.1-RELEASE (`n283562`) and tested:
> **it does not fix the cold-open silence.** The root-cause theory below
> (the clock goes *invalid* during the crystal relock and the UAC2 Clock
> Validity control exposes it) is **wrong for the OKTO** — the device reports
> `CLOCK_VALID=1` on the very first poll, so `uaudio20_wait_clock_valid()`
> returns in **0 ms** and waits for nothing. Listening test confirms it: a
> single cold 44.1 kHz open is still **silent**; only `DAC_PRIME_CYCLES` (≈3
> opens) makes it audible. See [Status](#status).
>
> The patch is harmless and spec-compliant (a no-op when the clock is already
> valid), so it can stay as an upstream candidate — but it earns nothing here,
> and **the real fix must be looked for elsewhere under `/usr/src`** (see
> [Next](#next-where-to-look-under-usrsrc)).

## Symptom

With the capture-disable patch applied (no more flicker), playing a 44.1 kHz-
family track (44.1 / 88.2 / …) after the DAC was last on the 48 kHz family
produces **no audio on the first open**, even though everything looks healthy:
brutefir streams, `dev.pcm.0.feedback_rate` reads the right rate, the device
shows "play". Re-opening the stream two or three times eventually starts the
audio. The 48 kHz family (48 / 96 / 192 kHz) always plays on the first open.

Confirmed by listening test (2026-06): 1 open at 44.1 k = silent, 3 opens =
audible; 192 kHz = audible immediately. See
[`../OKTO-DAC8-silent-first-open.md`](../OKTO-DAC8-silent-first-open.md).

## Root cause

In `uaudio_chan_configure()` (the `CHAN_OP_START` path) the driver:

1. `usbd_set_alt_interface_index()` — selects the streaming alt-setting,
2. `uaudio20_set_speed()` — `SET_CUR` on the clock source's
   `CS_SAM_FREQ_CONTROL` (programs the new rate),
3. `usbd_transfer_setup()` — and **immediately starts isochronous transfers**.

There is **no wait for the clock to relock** between (2) and (3). `uaudio(4)`
never reads the UAC2 **Clock Validity control** (`UA20_CS_CLOCK_VALID_CONTROL`,
defined in `uaudioreg.h` but referenced nowhere in `uaudio.c`).

The OKTO derives its two rate families from two different master crystals
(≈22.5792 MHz for the 44.1 kHz family, ≈24.576 MHz for the 48 kHz family). Its
default enumerated rate is 384000 Hz, so cold/idle the device sits on the 48 kHz
crystal. Therefore:

- Opening a **48 kHz-family** rate stays on the same crystal → clock already
  valid → audio on the first open.
- Opening a **44.1 kHz-family** rate forces a **crystal-domain switch** → the
  clock goes *invalid* while the PLL relocks → uaudio streams immediately into
  the unlocked clock → the device renders that stream silent. A later open (the
  rate is retained, so the clock is valid by then) plays.

The asynchronous feedback endpoint reports a healthy rate throughout — it is
independent of whether the analog clock has locked — which is why nothing on the
host side detects the silence.

USB Audio 2.0 §5.2.5.1.1 defines the Clock Validity control for exactly this
case. Linux's `snd-usb-audio` tolerates the relock in practice; FreeBSD does not
wait at all.

## The fix (`uaudio-clock-valid.c.patch`)

Add `uaudio20_wait_clock_valid()`: after a successful `uaudio20_set_speed()`,
poll the clock source's `CLOCK_VALID` control (`GET_CUR`) until it reports valid,
**before** transfers start.

- **Bounded & best-effort.** Polls every `UAUDIO20_CLOCK_VALID_DELAY_MS` (10 ms)
  up to `UAUDIO20_CLOCK_VALID_POLLS` (40) ⇒ ~400 ms cap, then continues anyway.
  If the control is unreadable (device/clock doesn't implement it) it returns at
  once. So it can never wedge anything.
- **No new thread, no hang.** It runs in the **USB explore process** (the
  channel start is dispatched there via `uaudio_chan_reconfigure()` →
  `usb_proc_explore_msignal()`), a sleepable context holding no mutex at this
  point — the same context where `uaudio20_set_speed()` already issues blocking
  `usbd_do_request()` calls. It sleeps with `usb_pause_mtx()` (no busy-wait).
  Worst case it delays *this one stream's start* by up to the cap; the explore
  process is shared for config/hotplug, hence the deliberately short cap.
- **Device-agnostic.** Unlike the capture-disable patch, this is not gated on the
  OKTO VID/PID — it's spec-compliant behaviour that is a no-op for devices whose
  clock is already valid. It is a reasonable **upstream** candidate.

The hope was that this would make the host-side `DAC_PRIME_CYCLES` workaround in
`drc.sh` unnecessary for 44.1 kHz. **It did not** — see the RESULT banner above
and [Status](#status): the OKTO reports the clock valid immediately, so there is
nothing for this wait to do.

## Tuning / debug

- Raise `UAUDIO20_CLOCK_VALID_POLLS` if 44.1 kHz is still occasionally silent
  (longer relock budget); lower it if stream start feels sluggish.
- With the `USB_DEBUG` build, `hw.usb.uaudio.debug=6` logs `clockid=N valid
  after M ms` / `not valid after … continuing anyway`, so you can see the actual
  relock time and size the cap from data.

## Status

**Tested 2026-06-21 on FreeBSD 15.1-RELEASE (`n283562`): INEFFECTIVE on the
OKTO.** Built into `snd_uaudio.ko` and measured with `hw.usb.uaudio.debug=6`:
on a genuine 48k→44.1k crystal switch (feedback 48001→44101, count-delta
verified) the kernel prints `uaudio20_wait_clock_valid: clockid=41 valid after
0 ms` every time — the device's Clock Validity control returns valid on the
first poll, so the wait is a no-op. Listening test (park on 48k crystal via
`drc.sh 192000`, single cold `drc.sh 44100` with `DAC_PRIME_CYCLES=0`, 16/44.1
album): **silent**; re-running with `DAC_PRIME_CYCLES=2` (≈3 opens): **audible**.

Conclusion: the OKTO's UAC2 Clock Validity control is as "optimistic" as its
async feedback endpoint (§Root cause) — neither exposes the relock period — so a
validity-based wait cannot fix this. The capture-disable `uaudio.c.patch` still
works; `DAC_PRIME_CYCLES=2` in `drc.sh` stays required.

The patch remains harmless and spec-compliant, so it is kept applied as a
possible upstream candidate, but it does nothing for this DAC.

## Next: where to look under `/usr/src`

The validity-control angle is a dead end *for this device*. The silence is a
device-side **audio-routing** quirk that no host-visible status (feedback rate,
clock validity) reflects, and which clears only after ≈3 open/close cycles. So
the next investigation should target the open/teardown sequence in
`sys/dev/sound/usb/uaudio.c`, not the clock controls — candidate angles:

- `uaudio_chan_configure()` / `CHAN_OP_START` ordering: what differs between the
  1st and 3rd open? Try reordering `usbd_set_alt_interface_index()` vs
  `uaudio20_set_speed()`, or a deliberate alt-setting bounce (set alt 0 → alt N)
  before starting transfers, mimicking what the extra prime opens do.
- Whether issuing the `SET_CUR` sample-rate request (or an explicit clock-source
  reselect) twice, or adding a dwell *after* `usbd_transfer_setup()` before the
  first isoc frame, changes the routing.
- Compare the on-the-wire control sequence of a working (3rd) open vs the silent
  (1st) open — `usbdump`/`USB_DEBUG` trace `sys/dev/usb/` — to find the request
  the device actually needs.
- For reference, how Linux `snd-usb-audio` (`sound/usb/`) sequences the same
  device's rate change + interface start (it plays on the first open).
