sound: uaudio: finish the UAC2 shared-clock fix

Follow-up to 755685dd665e (MFC 6886e8a9a0aa, PR 295933,
[#2323](https://github.com/freebsd/freebsd-src/pull/2323)). That commit stopped
an idle capture channel from reprogramming a shared UAC2 Clock Source to a
*different* rate. Three things it left unfinished, one of which re-creates the
hazard the guard was added to prevent.

Please read commit 3 separately from 1 and 2 — see *Evidence* below.

### 1/3 — never rewrite a shared clock another stream owns

The guard skips only on a rate **mismatch**:

```c
if (other != 0 && other != chan_alt->sample_rate) { /* skip */ }
```

but the rate alignment added by the *same commit* (`uaudio_chan_match_rate()` in
`uaudio_chan_start()`) guarantees a **match**, so the write always falls
through. And `uaudio_chan_start()` auto-starts the capture stream for jitter
info while `uaudio_configure_msg()` configures playback first — so the capture
pass writes the clock *after* playback is armed and streaming.

Captured with `hw.usb.uaudio.debug=6`, opening at 44.1 kHz on an OKTO DAC8
STEREO (one Clock Source, id 41, in both direction bitmaps):

```
uaudio_configure_msg_sub:                             <- PLAY pass
uaudio20_set_speed: ifaceno=0 clockid=41 speed=44100       <- first write
uaudio_configure_msg_sub: clock rate changed to 44100 Hz; settling 100 ms
uaudio_configure_msg_sub: fps=8000 sample_rem=4100    <- play armed, transfers started
uaudio_configure_msg_sub:                             <- CAPTURE pass
uaudio20_set_speed: ifaceno=0 clockid=41 speed=44100       <- second write, same value
uaudio_chan_play_callback: transferring 2816 bytes    <- playback already streaming
```

Two writes, one settle, every open. Also adds a `GET_CUR` read-back so a write
that would change nothing is skipped and the programmed rate is verified —
what `set_sample_rate_v2v3()` does in Linux, with
`hw.usb.uaudio.clock_readback=0` as the equivalent of
`QUIRK_FLAG_ALWAYS_SET_RATE`. A write is only ever skipped on a *successful*
read-back, so devices without `GET_CUR` are unaffected. And a failed
`uaudio_chan_match_rate()` no longer leaves a stale capture alternate setting
armed at an unrelated rate.

### 2/3 — prefer an explicit feedback endpoint over the capture stream

`uaudio_chan_need_both()` starts the capture stream of *any* async playback
device that also exposes a capture interface, even when the playback alt
carries an explicit feedback endpoint — and
`uaudio_chan_play_sync_callback()` then discards the feedback value because it
gates on `sc_rec_chan[i].num_alt == 0`. Measured on the OKTO at 44.1 kHz over
20 s:

```
UE_ISOCHRONOUS_OK delta : 5038  -> 251.9/s   (usbconfig dump_stats)
dsp.play interrupts     : 2509  -> 125.4/s   (/dev/sndstat, hw.snd.verbose=2)
                          251.9 = 125.4 play + 125.4 capture + 1 feedback
```

Half the device's isochronous bandwidth, a second streaming interface armed
right after playback, and (on a shared clock) the extra programming pass from
1/3 — to recompute a number the device already reports. Worst on D/A converters
that advertise a UAC2 input terminal they cannot source: the capture interface
exists only in the descriptors, and we stream it for the lifetime of every
playback.

This is the behaviour change most worth review, hence
`hw.usb.uaudio.prefer_feedback` (default 1) to A/B it. It also closes a
pre-existing hole: gating on `cur_alt` rather than `num_alt` means playback no
longer free-runs with *no* feedback from either source when the capture stream
exists but never started or failed to configure.

### 3/3 — program the clock before arming the stream

Stock selects the streaming alt, *then* programs the clock, then starts
transfers. Linux parks at alt 0, programs, then selects the alt. This does the
same, plus `hw.usb.uaudio.clock_settle_ms` (default 100) on a verified rate
change, since a 44.1/48 kHz family change switches the master crystal on many
DACs. UAC1 untouched (its rate control is on the streaming endpoint).

### Evidence, and what I am not claiming

1/3 and 2/3 have a trace and a counter behind them and stand as defects
regardless of whether any device audibly misbehaves.

**3/3 does not.** It was written for an intermittent fault on one DAC where the
first open after a rate change renders the stream silent — correct rate on the
panel, healthy USB, feedback endpoint normal, no audio — and the only evidence
it helps is listening on that device. The fault is rare: 42 consecutive
open/play/close cycles across all eight rates, 21 of them into 44.1 kHz, were
all audible, so I cannot reproduce it on demand in either direction. The
reordering is defensible on its own terms and costs nothing when the rate has
not changed, but there is no measured proof. **Drop 3/3 if that is not enough;
1/3 and 2/3 stand without it.**

### Not fixed here, declared so they are not mistaken for oversights

* Only the interface *being configured* is parked, not every interface on that
  clock. With `usb_proc_msignal()`'s two-entry queue and
  `uaudio_chan_reconfigure()` keeping only the latest `operation`, a `STOP`
  followed quickly by a `START` can coalesce and leave the other direction's
  interface armed across a clock write. The real fix is one transaction over
  both directions with a generation counter — much larger than this series.
* `uaudio_dir_running_rate()` reads the other channel's `running` and `cur_alt`
  with neither the explore lock nor `chan->lock` held, because
  `uaudio_configure_msg()` deliberately drops the explore lock. Aligned scalar
  reads, so no tearing, but not a stable snapshot. This came in with
  755685dd665e — mine.
* Genuinely incompatible simultaneous rates are still not rejected: the guard
  declines to reprogram, but the second stream is configured and packetised for
  the rate it asked for. Software at rate B, hardware at rate A, silently.
  Linux returns `-EBUSY`.
* `bmControls` is not consulted; Linux checks
  `uac_v2v3_control_is_writeable(bmControls, UAC2_CS_CONTROL_SAM_FREQ)` and
  verifies rather than writes a read-only clock.

### Device coverage

No VID/PID or quirk entries — everything keys off descriptor facts the driver
already parses. The shared-clock guard needs a Clock Source in both direction
bitmaps; the read-back needs UAC2; `prefer_feedback` needs async playback plus a
feedback endpoint plus a capture interface. The topology behind 1/3 (one clock
feeding both directions) is common: XMOS/Thesycon reference designs, most
DAC+ADC interfaces, and every DAC shipping a vestigial capture path. On my host
the OKTO hits all three paths and the ESI U24XL (UAC1) hits none — no
behaviour change at all on that card.

### Testing

Builds `-Werror` clean with and without `USB_DEBUG`, each commit standalone and
stacked. 1/3 confirmed on the wire, 2/3 by counter differential. Clock read-back
verified live (`GET_CUR` → 44100, `CLOCK_VALID` → 1, `RANGE` → the 8 supported
rates). Not listening-tested with the series installed.
