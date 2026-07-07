# `uaudio(4)` shared-clock fix — the proper (non-workaround) flicker fix

**Status 2026-07-07: APPLIED to `/usr/src`, builds `-Werror`-clean both
standalone on stock and on top of `uaudio-clock-before-alt.c.patch`; the
combined `.ko` is built in `/usr/obj` but NOT yet installed to
`/boot/kernel` and NOT yet listening-tested.** It replaces (and removes)
the device-gated capture-disable workaround (`uaudio.c.patch`, retired) —
the OKTO now enumerates **play + rec again** (`pcm0 (play/rec)` is the
*expected* sndstat output once installed).

Patch: [`uaudio-shared-clock-fix.c.patch`](uaudio-shared-clock-fix.c.patch)

## What it is

A **general, device-agnostic** fix for the shared-clock flicker
(bug #295933): instead of amputating the OKTO's vestigial capture
interface by VID/PID, it makes `uaudio` handle a UAC2 Clock Source that is
shared between playback and capture correctly. Three cooperating changes:

### 1. Rate-align the jitter-info record stream (`uaudio_chan_start()`)

On async playback, `uaudio_chan_need_both()` auto-starts the record
channel purely as a **jitter-information source**. Stock leaves that
channel at its own nominal rate (48 kHz default on the OKTO), which is
what actually clobbers the shared clock. The fix sets the record
channel's `set_alt` to the alt whose `sample_rate` matches the playback
rate *before* it is started (under the USB explore lock, which also
serializes `uaudio_chan_set_param_speed()`). This makes the rec-side
`SET_CUR` a same-value no-op **and** keeps the rec channel's expected
framing (`bytes_per_frame` / `sample_rem`) consistent with what the
device actually produces — so the derived jitter feedback is *valid*
instead of catastrophically wrong.

### 2. Shared-clock guard (UAC2 `SET_CUR` loop in `uaudio_configure_msg_sub()`)

Safety net for every other path: before issuing `SET_CUR` to a clock id,
if `uaudio20_clock_is_shared()` (the clock id is set in **both**
`sc_mixer_clocks.bit_output[]` and `bit_input[]`) and the *other*
direction is currently `running` at a **different** rate
(`uaudio_dir_running_rate()`), skip the `SET_CUR` — the locked rate wins.
First active stream owns the clock. Covers a genuinely-opened capture at
a conflicting rate (which then records at the wrong pitch — an inherent
UAC2 shared-clock constraint, documented, not fixable host-side).

### 3. Keep the explicit-feedback SYNC transfer always running

Stock only submits the feedback IN transfer when there is **no** record
channel, so re-enabling capture would freeze `dev.pcm.0.feedback_rate` —
which `drc.sh` uses as its chain-sanity signal. The fix always submits it
(one isochronous IN frame per second); jitter *use* of the feedback value
stays gated to the no-record case, the value is tracked purely as a
diagnostic.

## Why the original guard sketch alone was NOT enough (audit, 2026-07-07)

The earlier `uaudio-shared-clock-guard.c.patch` (retired) contained only
change 2 and marked the framing mismatch "moot for the DAC8". The audit
showed it is **not** moot: with capture re-enabled, `need_both()`
auto-starts the rec channel on every async playback start, and
`uaudio_chan_record_callback()` computes jitter from the rec channel's
*own* nominal framing. Guard-only at 44.1 kHz playback: rec expects
48 kHz worth of bytes, device (correctly locked at 44.1 kHz) delivers
fewer → `jitter_curr` pinned at its negative clamp (−2·`intr_frames`)
every interval → the play callback strips samples continuously → severe
pitch-down/starvation. Worse than the flicker it replaces. Change 1 is
therefore mandatory, and is also what makes the driver's
capture-as-feedback design actually work on the 44.1 kHz family (it
already worked at 48 kHz only because the rates happened to agree).

## Evidence the mechanism is sound

- Root cause + both committer-suggested workarounds (rate agreement via
  `sndctl`/`default_rate`) act by the same mechanism — see
  [`FreeBSD-uaudio-shared-clock-bug.md`](FreeBSD-uaudio-shared-clock-bug.md).
- Stock FreeBSD at the 48 kHz family already runs rec-as-jitter with
  agreeing rates and is stable — change 1 reproduces exactly that known-good
  configuration at every rate.

## Interaction with the other patches

- **Replaces** `uaudio.c.patch` (capture-disable workaround, retired) and
  `uaudio-shared-clock-guard.c.patch` (guard-only sketch, retired).
- **Independent of / combines with** `uaudio-clock-before-alt.c.patch`
  (cold-open silence): the single patch file applies cleanly on stock
  (offsets only) *and* on a tree with clock-before-alt applied. Apply
  order used locally: stock → clock-before-alt → shared-clock-fix.
- `uaudio-feedback-follow.c.patch` (unbuilt candidate) touches the same
  sync-callback region as change 3 — it **needs rebasing** before use.

## Test plan (after installing the .ko — see README)

1. `cat /dev/sndstat` → `pcm0: <OKTO...> (play/rec)` — capture is BACK,
   by design.
2. 44.1 kHz family playback (44.1/88.2/176.4/352.8 k), bitperfect → no
   flicker, stable front panel, `underruns 0`.
3. `sysctl dev.pcm.0.feedback_rate` tracks the playback rate (change 3).
4. Rate changes / cold opens still audible first try (clock-before-alt
   regression check; full matrix in
   [`uaudio-clock-before-alt.md`](uaudio-clock-before-alt.md)).
5. With `hw.usb.uaudio.debug=15`: exactly one effective clock rate; on
   the play start expect either a same-value rec `SET_CUR` or a
   "shared clock ID=41 busy" skip; **no** `44100` → `48000` overwrite
   pair.
6. Optional: open `/dev/dsp0` for recording at a mismatched rate during
   playback → playback must survive (guard); recording pitch will be
   wrong (documented limitation).
