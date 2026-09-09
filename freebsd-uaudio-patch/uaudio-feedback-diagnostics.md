# Continuous `uaudio(4)` feedback and long-session diagnostics

This is a generic FreeBSD USB Audio patch series prompted by rare audible
clicks with an OKTO DAC8 Stereo.  It contains no OKTO VID/PID check or other
device-specific rule.  The code uses only USB Audio descriptors and the
selected stream's rate, sample stride, service interval, and packet limit.

Nothing in this series changes PCM samples.  `bitperfect=1` and `vchans=0`
remain the required PCM settings.  These patches address USB packet timing:
an asynchronous DAC owns the audio clock and tells the host, through its
feedback endpoint, how many sample frames each USB interval should carry.

## Reviewable series

Apply these after the two existing upstream-style clock patches, in order:

| Patch | Scope | Diff lines |
|---|---|---:|
| `uaudio-upstream-0003-bind-exact-endpoints.c.patch` | Bind each transfer to the data/feedback endpoint selected for that alternate setting. | 26 |
| `uaudio-upstream-0004-honor-synch-address.c.patch` | Prefer the feedback endpoint named by `bSynchAddress`, retaining the old first-match fallback for broken descriptors. | 27 |
| `uaudio-upstream-0005-linux-feedback-decode.c.patch` | Decode and validate 3-byte Q10.14 and 4-byte Q16.16 feedback using Linux's rules. | 161 |
| `uaudio-upstream-0006-continuous-feedback.c.patch` | Keep feedback transfers in flight and retain the historical path behind a runtime rollback. | 143 |
| `uaudio-upstream-0007-feedback-packet-scheduler.c.patch` | Size every playback packet with a Linux-style Q16.16 phase accumulator. | 193 |
| `uaudio-upstream-0008-feedback-diagnostics.c.patch` | Add a small persistent counter snapshot; user space owns timestamps and history. | 220 |

The last diff contains about 130 added code lines; the larger number includes
unified-diff context.  There is no kernel event ring, log buffer, or per-packet
accounting.  Each intermediate source and the complete result build with
`-Werror` on this FreeBSD 15.1 system.

The old `uaudio-feedback-follow.c.patch` is an unbuilt superseded sketch and
must not be installed.  It mutates nominal packet state from an infrequent
feedback callback, lacks the complete endpoint/format/staleness handling here,
and conflicts with the current patched source.

## Linux reference

The arithmetic follows current Linux `sound/usb/endpoint.c`:

- feedback is represented as sample frames per base USB frame/microframe in
  Q16.16;
- 3-byte Q10.14 and 4-byte Q16.16 feedback are normalized with binary-shift
  autodetection;
- values below nominal by more than 12.5%, above nominal by more than 50%, or
  above `wMaxPacketSize` capacity are rejected;
- every data packet adds `feedback_q16 << datainterval` to a fractional phase,
  and the integer result is capped at the endpoint's maximum frame count.

Reference: [Linux `sound/usb/endpoint.c`](https://github.com/torvalds/linux/blob/master/sound/usb/endpoint.c).

FreeBSD retains two deliberate compatibility rules.  An actually running
capture stream remains the clock estimator so two controllers do not fight.
If feedback is unavailable or older than the greater of 250 ms and four
feedback intervals, playback falls back to the existing nominal/capture
scheduler.

## Runtime controls

```text
hw.usb.uaudio.feedback_mode=1   # continuous Q16.16 packet scheduler
hw.usb.uaudio.prefer_feedback=1 # explicit endpoint instead of borrowed capture
```

Both default to 1.  `feedback_mode=0` restores the historical FreeBSD
once-per-second integer correction without rebuilding or reloading the module.

`feedback_mode` can be hot-switched while playback is running.  Transfer
callbacks observe the new scheduler immediately; the web UI leaves this switch
available during a stream for emergency rollback and controlled A/B tests.

`prefer_feedback=0` allows the existing rate-aligned borrowed-capture path and
determines which endpoints are started.  Stop and reopen playback before
changing this clock-source policy so the new choice actually takes effect.

The acceptance window and timeout are intentionally not tunable: they are
safety policy derived from Linux and the endpoint interval, not sound-quality
controls.

## Small diagnostics ABI

Each playback PCM exposes one coherent, versioned snapshot:

```sh
sysctl -n dev.pcm.N.uaudio_diagnostics
```

It reports current rate/source/feedback state and these monotonic counters:

- `feedback_updates`, `feedback_bad`, `feedback_errors`, `feedback_stale`;
- `play_short_transfers`, `play_short_bytes`, `play_errors`;
- `generation`, incremented at every configured playback stream.

Source 0 is nominal fallback, 1 explicit feedback, and 2 capture-derived.
Counters survive stream close and reset when the USB device detaches.  Feedback
min/max gauges reset for each stream because Q16.16 values differ by sample
rate.  Normal operation does not print feedback packets; callback tracing is
at debug level 6.

## Persistent journal and FreeBSD-only UI

On FreeBSD, `omdrcctrl` polls the coherent snapshot every two seconds and
writes sparse JSONL records to:

```text
${OMDRC_STATE_DIR}/audio-diagnostics.jsonl
```

It records attach/stream transitions, anomaly deltas immediately, and one
summary per minute, rotating at 5 MB.  The **I heard a click** button records a
listener marker with the latest snapshot.  Thus a rare click from a long
session remains reviewable after playback stops; the kernel does not need a
large logging subsystem.

The USB audio integrity card, its JavaScript polling, and the background
monitor are rendered/started only on FreeBSD; they are absent on Linux.  Both
switches show actual kernel readback.  The scheduler remains hot-switchable
during playback, while the endpoint policy stays disabled until the DSP is idle.

For an unprivileged service account, writes need only this narrow sudo rule:

```sudoers
<AUDIO_USER> ALL=(root) NOPASSWD: /sbin/sysctl hw.usb.uaudio.feedback_mode=0, \
    /sbin/sysctl hw.usb.uaudio.feedback_mode=1, \
    /sbin/sysctl hw.usb.uaudio.prefer_feedback=0, \
    /sbin/sysctl hw.usb.uaudio.prefer_feedback=1
```

Read-only monitoring needs no privilege.  The backend whitelists only those
two names and values 0/1, permits the scheduler's supported live switch,
requires idle playback for the endpoint policy, uses `sudo -n`, and verifies
the readback.

## Validation status

The series applies cleanly to the exact current `/usr/src` source (the three
earlier local fixes installed) and reconstructs the tested final source
byte-for-byte.  All six stages build independently with `-Werror`, including
the final diagnostics stage.

The resulting single `snd_uaudio.ko` is installed and loaded by the current
kernel.  The exact earlier three-patch module remains at
`/boot/kernel/snd_uaudio.ko.pre-feedback-20260908` for rollback.

On 2026-09-08 a 30-minute, 44.1 kHz, 32-bit digital-silence stream ran directly
to the OKTO with `feedback_mode=1`, `prefer_feedback=1`, `bitperfect=1`, and
`play.vchans=0`.  It remained in one stream generation with source 1 (explicit
feedback).  About 1,799,980 valid 1 ms feedback packets arrived; no feedback
bad/error/stale counter, short-transfer counter, or playback-error counter
changed.  Feedback remained 361276--361277 Q16.16, reported as 44,101 Hz.

A live Q16.16 to legacy to Q16.16 switch was also exercised during a direct
silent stream.  The active source moved 1 to 0 to 1 immediately and playback
returned to explicit feedback without a new error or short transfer.

The earlier Chromium hiccups exercised a different path: diagnostics showed
source 2 because sndiod's default full-duplex mode had opened `/dev/dsp0` for
capture even though Chromium only needed playback.  The browser launcher now
starts `sndiod -m play -s default` to avoid that path; a browser listening retest
is still required.

This validates driver transport and scheduler stability under an idle-host
direct stream; it does not substitute for long music playback under realistic
CPU and I/O load.

A click marker correlated with `play_short_*`, `play_errors`, `feedback_bad`,
or `feedback_stale` is evidence of a transport/driver event.  A marker with no
counter movement means the cause is elsewhere or below these observability
points.
