# `uaudio(4)` shared-clock guard — general fix candidate

**Status: written from a source audit, NOT built, NOT applied, NOT heard.**
Reference sketch for later — validate the diff context (line numbers / tabs)
against your tree before relying on `patch` to apply it cleanly.

Patch: [`uaudio-shared-clock-guard.c.patch`](uaudio-shared-clock-guard.c.patch)

## What it is

A **general, device-agnostic** alternative to the device-gated capture-disable
hack in [`uaudio.c.patch`](uaudio.c.patch). Instead of removing the OKTO's
capture interface by VID/PID, it teaches `uaudio` not to let an idle/secondary
stream reprogram a **shared** UAC2 sample clock that an active stream already
owns. This is the form that could be proposed upstream.

## Root cause (confirmed by testing, 2026-06-27)

The OKTO DAC8 STEREO (`0x152a:0x88c5`) exposes **one UAC2 Clock Source shared**
between its playback (AS interface 1) and capture (AS interface 2) interfaces.
On the 44.1 kHz family the idle capture side defaults to 48 kHz and reprograms
that shared clock out from under active 44.1 kHz playback → continuous USB
stream-lock loss / front-panel flicker.

Two committer-suggested workarounds (Christos Margiolis, bug #NNNNNN) both fix
the flicker, and both work by the **same mechanism — making the capture side use
the same rate as playback** so nothing reprograms the shared clock:

1. `sndctl play.rate=44100 rec.rate=44100` (vchans on, bitperfect off).
2. `sysctl hw.usb.uaudio.default_rate=44100` + re-enumerate (bitperfect).

That proves the bug is *"a secondary stream reprograms a shared clock,"* not
*"capture exists"* — so the right fix guards the clock, not the interface.

## The single clobber site

`uaudio` issues a UAC2 sample-rate `SET_CUR` in exactly one place: the clock
loop in `uaudio_configure_msg_sub()` (`uaudio20_set_speed(udev, mixer_iface_no,
x, chan_alt->sample_rate)`). The guard goes immediately before that call.

## How the guard works

- `uaudio20_clock_is_shared(sc, x)` — a clock entity feeding both an output and
  an input terminal has its bit set in **both** `sc_mixer_clocks.bit_output[]`
  and `bit_input[]`.
- `uaudio_dir_running_rate(chans)` — returns the rate of a currently `running`
  channel in the opposite direction (`usb_alt[cur_alt].sample_rate`), else 0.
- In the loop: if the clock is shared **and** the other direction is running at
  a **different** rate, skip the `SET_CUR` (`continue`) and adopt the locked
  rate. When nothing else runs, the clock is programmed normally.

Net effect: **first active stream owns the shared clock; the later stream
follows it** — the behaviour Linux `snd-usb-audio` gets implicitly.

## Known limitations / TODO before upstreaming

1. **Host-side framing mismatch (the real refinement).** When the guard skips,
   the skipped channel's `bytes_per_frame` / `sample_rem` are still computed
   from its *own* `chan_alt->sample_rate`, not the clock's actual rate — so a
   *real* secondary capture would run at the wrong pitch. Moot for the DAC8
   (vestigial capture, no analog inputs). Complete fix: re-select the secondary
   channel's alt-setting to the one whose `sample_rate == other` *before* this
   loop (adjust `set_alt`/`next_alt`), so framing matches the locked clock.
   This is the same "follow the active stream" gap behind the 16→24-bit
   observation (see `../`[okto-uaudio-fixed-format-at-attach memory] / the
   bug-report doc): `uaudio` fixes one `(rate,bits)` at attach instead of
   tracking the stream.
2. **Lockless read** of the other channel's `running`/`cur_alt` (runs in the USB
   explore process context). Worst case is one stale skip/no-skip; tightening it
   under the other channel's `lock` risks lock-ordering vs the explore lock.
   Keep lockless + documented, or snapshot carefully.
3. **Shared detection assumes** `uaudio20_mixer_find_clocks_sub()` sets both
   bits for a source feeding both terminal directions — confirm on this DAC with
   a one-line `DPRINTF` at attach.
4. `bool` is used for the helper return; switch to `int`/`uint8_t` if the build
   environment objects (kernel `bool` via `<sys/types.h>` should be fine).

## Build / test (when you pick this up)

This patch is **independent** of `uaudio.c.patch` (capture-disable) — do NOT
apply both; this one replaces it. Apply on a stock tree, build the module per
[`README.md`](README.md), then listen-test the 44.1 kHz family for flicker and
watch `dev.pcm.0.feedback_rate` stays at the playback rate.
