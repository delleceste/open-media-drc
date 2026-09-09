# FreeBSD vs Linux USB audio: feedback scheduling and sound-quality assessment

**System assessed:** Intel NUC (2016), FreeBSD 15.1-RELEASE-p2, OKTO Research
DAC8 Stereo, open-media-drc/MPD/BruteFIR and direct browser playback  
**Assessment date:** 2026-09-08  
**FreeBSD driver:** the locally patched `snd_uaudio.ko`, built from
`/usr/src/sys/dev/sound/usb/uaudio.c`

## Executive conclusion

The current patched FreeBSD driver passed a continuous 30-minute direct
44.1 kHz test using the OKTO's explicit feedback endpoint and the Linux-style
Q16.16 packet scheduler. It received about 1.8 million valid feedback packets
without a new malformed/stale feedback event, USB playback error, short
transfer, or stream restart. This is strong evidence that the new scheduler is
stable on this DAC.

That result does **not** prove that patched FreeBSD and Linux are universally
equivalent:

- Linux's USB-audio implementation is older, more widely exercised, and much
  broader in device quirks, implicit-feedback support, clock validation, XRUN
  handling, low-latency scheduling, power management, and native DSD formats.
- The local FreeBSD patches are not yet upstream-reviewed or widely tested.
- Some FreeBSD cross-direction clock/configuration weaknesses remain outside
  the deliberately reviewable patch series.
- The Chromium hiccups observed during this work did not exercise the intended
  explicit-feedback scheduler. `sndiod` had opened the DAC full-duplex, which
  activated FreeBSD's capture-derived clock path. That launcher defect has now
  been corrected, but browser playback still needs another listening test.

For a dedicated, unattended audiophile appliance, **Linux remains the safest
choice today**, primarily because it has the more mature and broadly supported
USB-audio stack. That is a reliability and maintenance judgment—not evidence
that Linux changes correctly delivered PCM samples into inherently
better-sounding samples.

When both systems:

1. deliver identical PCM samples;
2. keep the DAC supplied without underruns;
3. use the DAC's asynchronous local clock correctly; and
4. avoid electrically coupled host noise,

there is no established software mechanism by which Linux should have better
analogue sound merely because its kernel generated the USB packets. A perceived
difference without a controlled test is not proof, but neither should actual
clicks or dropouts be dismissed as “audiophile imagination.”

## 1. Three different meanings of “sound quality”

The discussion is clearer if three layers are kept separate.

### 1.1 Sample integrity

Bit-perfect playback means that the numerical PCM words reaching the output
path are the intended words. On this FreeBSD system the required settings are:

```text
dev.pcm.0.bitperfect=1
dev.pcm.0.play.vchans=0
```

The feedback patches do not edit, filter, dither, attenuate, or resample PCM
samples. They change how many complete sample frames are placed in each USB
isochronous packet.

### 1.2 Stream integrity

Even bit-perfect software can click if it fails to deliver samples on time. A
lost/short transfer, wrong packet-size sequence, stale feedback value, FIFO
underflow, clock reprogramming during playback, or userspace producer stall can
insert silence, omit samples, repeat samples, or force a DAC relock.

Those are genuine sound-quality failures. They are usually heard as clicks,
pops, gaps, pitch errors, or silence—not as a subtle change in tonal colour.

### 1.3 Analogue noise and conversion-clock integrity

The OKTO is an asynchronous USB DAC. Its oscillator is the audio-clock master;
the host follows the rate reported by the DAC. USB packet arrival timing is
therefore not supposed to become the conversion clock directly. The DAC's input
buffer and clock architecture are meant to decouple host scheduling jitter from
sample-conversion timing.

A host can still affect analogue measurements through electrical mechanisms:
USB ground/common-mode current, RF coupling, power-supply leakage, or a DAC
firmware/PLL implementation that reacts badly to endpoint or clock-control
activity. These possibilities require analogue measurements; they cannot be
proved from bit-perfect tests or listening impressions alone.

## 2. What explicit USB feedback means

An asynchronous playback endpoint consumes samples according to the DAC's local
oscillator. Because no real oscillator is exactly nominal, the DAC returns a
feedback value such as 44,101 Hz rather than exactly 44,100 Hz. More precisely,
the USB feedback packet expresses sample frames per USB frame or microframe as
a fixed-point number.

The host must use that value to choose a sequence of integer packet sizes whose
long-term average follows the fractional rate. At 44.1 kHz, for example, no
single 1 ms packet can contain 44.1 frames: packets must contain an appropriate
mixture of 44 and 45 frames.

The purpose of feedback is therefore **flow control between two clocks**. It
does not alter PCM values and is not DSP.

## 3. The relevant stock-FreeBSD weaknesses

### 3.1 Nominal scheduling plus a once-per-second correction batch

The historical FreeBSD path scheduled the nominal rate continuously but sampled
explicit feedback only about once per second. It converted the rate difference
into a small integer correction and spent that correction greedily near the
start of the next interval.

This can maintain the long-term average, but it bunches corrections instead of
distributing fractional timing evenly. A tolerant DAC FIFO may hide it
completely; a less tolerant device may expose a periodic disturbance.

### 3.2 Feedback information was not treated as a continuously varying rate

Simply polling the old algorithm more frequently would be wrong: the old
`jitter_curr` value represented a correction batch, not a per-packet
fractional rate. Reapplying the full batch many times per second would
over-correct.

A safe change therefore required a different packet scheduler, not merely a
faster feedback transfer.

### 3.3 Weak feedback-endpoint association

Stock descriptor walking did not retain and bind the selected alternate
setting's exact feedback endpoint robustly. The new code:

- associates the data and feedback endpoints per alternate setting;
- honours `bSynchAddress` when the descriptor provides it;
- retains a compatible fallback for devices with incomplete descriptors; and
- binds transfers to the exact parsed endpoint rather than accepting an
  unrelated endpoint with similar attributes.

The OKTO's descriptors omit `bSynchAddress`, so the compatible explicit
feedback endpoint fallback is required for this device.

### 3.4 Incomplete feedback format handling

USB Audio devices return three-byte Q10.14 or four-byte Q16.16-style feedback,
and real devices sometimes need binary-shift normalization. The new FreeBSD
decode follows Linux's normalization approach and rejects implausible values
outside a bounded window or beyond the endpoint's packet capacity.

### 3.5 Borrowing capture when explicit feedback already exists

FreeBSD historically auto-started a capture endpoint as an implicit estimate of
playback clock drift whenever an asynchronous device also exposed capture. It
did this even when playback had a dedicated explicit-feedback endpoint.

On the OKTO this meant:

- a second isochronous stream;
- a second interface being configured;
- extra shared-clock interactions; and
- capture packet lengths overriding the feedback value the DAC explicitly
  supplied.

The `prefer_feedback=1` policy avoids auto-starting capture when a usable
explicit endpoint exists.

### 3.6 Clock programming order and redundant writes

The local clock patches also address hazards separate from packet scheduling:

- park the streaming interface before a UAC2 clock-rate change;
- program and settle the clock before arming the playback alternate setting;
- read back the existing rate and avoid a redundant `SET_CUR`;
- prevent another active direction from having its shared clock reprogrammed;
- abandon an incompatible stale capture alternate setting.

These changes matter most during open, close, and sample-rate transitions.
Repeatedly programming a shared clock after playback is armed can make some DAC
firmware reload a PLL or lose lock.

### 3.7 Lack of persistent evidence

Rare clicks are hard to diagnose from counters that disappear after detach or
reboot. The patched driver exposes a coherent snapshot, and the FreeBSD-only web
service journals stream transitions, minute summaries, anomaly deltas, and
listener click markers.

## 4. Linux as the implementation reference

The choices were deliberately taken from current upstream Linux
`snd-usb-audio`, not invented as OKTO-specific tuning.

### 4.1 Per-packet fractional scheduling

Linux stores the measured rate as `freqm` and sizes packets with a fractional
phase accumulator. The essential upstream code is:

```c
phase = (ep->phase & 0xffff) + (ep->freqm << ep->datainterval);
ret = min(phase >> 16, ep->maxframesize);
```

The FreeBSD Q16.16 mode now uses the same arithmetic and caps the result by the
endpoint's maximum packet capacity. See Linux
[`sound/usb/endpoint.c`](https://github.com/torvalds/linux/blob/master/sound/usb/endpoint.c#L2465-L2494).

### 4.2 Feedback normalization and bounds

Linux normalizes three- and four-byte feedback to its internal fixed-point
representation, checks the result against nominal-rate and maximum-packet
bounds, and continuously updates the measured frequency. The FreeBSD patch uses
the same broad acceptance policy: nominal minus one eighth through nominal plus
one half, additionally capped by endpoint capacity.

This range is a validity/safety policy. It is not an audiophile adjustment.

### 4.3 Endpoint interval and association

Linux tracks a distinct sync endpoint, its interface/alternate setting, data
interval, `bRefresh`, and maximum feedback size. The FreeBSD patch adopts the
parts required for explicit-feedback devices, including exact endpoint binding
and interval-aware stale detection.

### 4.4 Clock readback and writability

Linux resolves and validates UAC2/UAC3 clock sources, checks whether sample
frequency is writable, reads the current rate, avoids rewriting an already
correct rate unless a device quirk demands it, and validates the clock after a
change. See Linux
[`sound/usb/clock.c`](https://github.com/torvalds/linux/blob/master/sound/usb/clock.c#L2493-L2669).

The local FreeBSD changes add successful-readback-based write suppression and a
rollback control, but they do not yet reproduce all of Linux's clock topology,
writability, validation, and quirk behavior.

### 4.5 Explicit and implicit feedback are different

Linux has dedicated implicit-feedback machinery, generic UAC2 matching, and a
large device-specific quirk table. See
[`sound/usb/implicit.c`](https://github.com/torvalds/linux/blob/master/sound/usb/implicit.c)
and the documented
[`implicit_fb` option](https://docs.kernel.org/next/sound/alsa-configuration.html).

The OKTO supplies an explicit feedback endpoint, so the new FreeBSD Q16.16 path
does not need Linux's implicit-feedback quirk machinery for normal playback.

## 5. The two FreeBSD runtime choices

### 5.1 Packet scheduler

```text
hw.usb.uaudio.feedback_mode=0  legacy FreeBSD correction cadence
hw.usb.uaudio.feedback_mode=1  continuous Linux-style Q16.16 scheduling
```

#### Mode 0: legacy

- Reads explicit feedback at the historical low cadence.
- Converts the difference from nominal into integer correction samples.
- Applies those corrections through the old jitter path.
- Provides a conservative runtime rollback.
- Does **not** mean “no feedback.”

Its flaw is correction bunching and slow reaction to a changed feedback rate.
Its advantage is that it preserves the long-used FreeBSD behavior.

#### Mode 1: Linux-style Q16.16

- Keeps feedback transfers in flight continuously.
- Retains the measured fractional rate.
- Uses a phase accumulator for every playback packet.
- Distributes 44/45-frame or similar packet choices across time.
- Rejects malformed, implausible, oversized, or stale feedback.
- Falls back safely when valid feedback is unavailable.

This is the preferred test and production mode for the OKTO.

### 5.2 Clock-source policy

```text
hw.usb.uaudio.prefer_feedback=0  allow borrowed capture-derived clocking
hw.usb.uaudio.prefer_feedback=1  prefer the explicit feedback endpoint
```

This is a policy about which endpoint supplies rate information, not about
whether PCM is bit-perfect.

An actually requested capture stream continues to win in duplex operation so
two independent estimators do not fight. Therefore an application that opens
the DAC read/write can still produce diagnostics `source=2`, even when
`prefer_feedback=1`.

## 6. Switching safely

The packet scheduler is intentionally hot-switchable.

From the FreeBSD-only web UI:

- **Packet scheduler → legacy** sets `feedback_mode=0`;
- **Packet scheduler → Linux-style Q16.16** sets `feedback_mode=1`.

The same emergency rollback from a shell is:

```sh
sudo sysctl hw.usb.uaudio.feedback_mode=0
```

Restore Q16.16 with:

```sh
sudo sysctl hw.usb.uaudio.feedback_mode=1
```

The live transition Q16.16 → legacy → Q16.16 was tested during a direct silent
stream. Diagnostics changed source 1 → 0 → 1 without a new USB error or short
transfer.

The `prefer_feedback` policy is different because it influences which
endpoints are started. Stop and reopen playback before changing it. The web UI
therefore disables only the clock-source buttons while streaming.

Current recommended settings:

```text
feedback_mode=1
prefer_feedback=1
bitperfect=1
play.vchans=0
```

## 7. Does this fix buffering only, or can it improve sound quality?

### Direct effects

The scheduler addresses USB flow control, buffer occupancy, endpoint
configuration, and clock-control correctness. It does not modify sample data.

If the old behavior caused a FIFO underflow, overrun, endpoint stall, clock
relock, omitted sample, or inserted silence, the new behavior can plainly
improve audible quality by removing clicks, pops, gaps, or silence.

### What it should not do

Once both implementations deliver an uninterrupted, bit-identical stream to a
properly asynchronous DAC, smoother host packet sizes should not by itself
change frequency response, distortion, stereo image, timbre, or “air.” The
DAC's local clock—not USB Start-of-Frame timing—determines conversion timing.

Claims of subtler improvement require evidence such as:

- level-matched blind comparison;
- repeated trials rather than OS-known sighted switching;
- analogue FFT/J-test measurements at the DAC output;
- simultaneous USB traces and driver counters;
- confirmation that both chains use identical rate, format, mixer, volume, and
  DSP state.

### A legitimate residual possibility

A particular DAC could couple USB traffic or control operations into its
analogue output or PLL. Avoiding an unnecessary capture stream and redundant
clock writes reduces such activity, which is prudent. Whether it produces an
audible or measurable analogue improvement on the OKTO is an empirical hardware
question, not something kernel source alone can establish.

## 8. What still lags behind Linux

The local series closes the explicit-feedback problem needed by this OKTO, but
FreeBSD still lacks or trails Linux in several areas.

1. **Atomic cross-direction configuration.** Playback and capture are not
   configured as one generation-tracked transaction. A rapid stop/start can
   coalesce operations while another interface remains armed.

2. **Stable cross-direction state snapshots.** Some `running` and
   `cur_alt` observations cross different locking domains. Scalar reads do not
   tear, but the pair is not a guaranteed coherent snapshot.

3. **Incompatible simultaneous rates.** A shared-clock guard can decline to
   reprogram the clock while the second software stream is still configured for
   its requested rate. Linux has more complete rejection/error propagation;
   FreeBSD should return a clear busy/error result instead of allowing a
   hardware/software-rate disagreement.

4. **Clock-control metadata.** The local readback patch does not yet provide
   Linux's full `bmControls` writability checks, clock-selector policy, source
   validity traversal, post-change validation, and device-quirk escape hatches.

5. **Implicit-feedback breadth.** Linux supports generic and device-specific
   implicit feedback, fixed endpoint mappings, playback-first requirements,
   and exceptional empty-packet behavior. FreeBSD's borrowed-capture model is
   much less expressive.

6. **Endpoint lifecycle and XRUN reporting.** Linux has explicit endpoint
   states, reference counting, prepared/ready URB queues, low-latency playback,
   and ALSA XRUN notification/recovery. The FreeBSD code is simpler and exposes
   fewer precise failure semantics to userspace.

7. **Device quirk coverage.** Linux carries a large, actively maintained set of
   per-device workarounds for broken descriptors, delayed control messages,
   interface sequencing, rate validation, implicit feedback, and other firmware
   behavior. The local FreeBSD series is intentionally generic and small.

8. **Native DSD.** Linux ALSA defines DSD_U8/U16/U32 formats and USB-audio DSD
   quirks; see
   [ALSA's PCM format ABI](https://github.com/torvalds/linux/blob/master/include/uapi/sound/asound.h)
   and [Linux USB-audio format parsing](https://github.com/torvalds/linux/blob/master/sound/usb/format.c).
   FreeBSD's OSS sound layer has no comparable native DSD PCM format, and
   `uaudio` skips the OKTO RAW/DSD alternate setting. DoP may still be possible
   as ordinary PCM if the DAC accepts it, but that is distinct from native DSD.

9. **Upstream review and regression population.** Linux's implementation is
   exercised across far more hardware. The FreeBSD patches build cleanly and
   pass this machine's tests but remain local and carry a kernel-update
   maintenance burden.

For comparison, upstream FreeBSD's current driver remains visible in
[FreeBSD `sys/dev/sound/usb/uaudio.c`](https://github.com/freebsd/freebsd-src/blob/main/sys/dev/sound/usb/uaudio.c).

## 9. Evidence gathered on this machine

### Direct Q16.16 soak

A 30-minute 44.1 kHz S32_LE digital-silence stream wrote 635,043,840 bytes
directly to `/dev/dsp.dac`.

Observed:

- one uninterrupted stream generation;
- active source 1, explicit feedback;
- approximately 1,799,980 valid feedback updates;
- feedback Q16.16 minimum/maximum 361276/361277;
- displayed feedback rate 44,101 Hz;
- zero new bad, errored, or stale feedback events;
- zero short transfers and zero short bytes;
- zero new playback errors;
- no new xHCI/USB timeout or underrun kernel message;
- ample CPU headroom in the controlled workload.

The persistent record is:

```text
~/.local/state/omdrc/audio-diagnostics.jsonl
```

The existing `play_errors=1` counter predates this soak. The journal records it
during an earlier generation using capture-derived source 2; it did not increase
during the Q16.16 test.

### Chromium finding

During the unusable Chromium session:

- Chromium consumed substantial CPU;
- `sndiod` had opened `/dev/dsp0` read/write;
- diagnostics reported source 2, capture feedback;
- Q16.16 explicit scheduling was therefore not the active packet source;
- driver malformed/stale/short-transfer counters did not increase.

The no-DRC launcher now starts sndiod as playback-only:

```sh
sndiod -r RATE -f rsnd/UNIT -m play -s default
```

This prevents a browser-only session from opening the OKTO capture endpoint.
The corrected browser path still needs an audible retest under the same video
and CPU load.

## 10. Wi-Fi and the 2016 Intel NUC

Turning Wi-Fi off cannot correct Q16.16 arithmetic or USB clock-feedback logic.
It may help in two narrower cases:

- the Wi-Fi device/driver produces enough interrupt or CPU pressure to starve
  userspace or USB scheduling; or
- RF/current from the radio couples electrically into the DAC, USB cable,
  ground, or analogue system.

Neither effect should be assumed. The controlled FreeBSD test had ample CPU
headroom and no xHCI error, so there is no evidence from that run that Wi-Fi is
hurting USB delivery.

A sensible audiophile-appliance experiment is to use wired Ethernet, disable
Wi-Fi, and repeat the same playback while marking clicks. This is cheap and
reversible. A subjective difference should then be confirmed blindly or with
analogue measurements before being treated as real.

For local files or MPD playback, disabling unused radios is reasonable hygiene.
For YouTube, network quality and browser scheduling can themselves cause
dropouts, so compare wired networking rather than testing with no network.

## 11. Is Linux capable of sounding better?

### In a fault-free bit-perfect comparison

Probably not for a kernel-audio reason alone. If both systems supply the same
samples continuously and the OKTO remains locked to its own oscillator, they
should be functionally equivalent at the DAC input. A claimed subtle difference
then needs controlled listening or analogue evidence.

### In the real system as operated

Yes, Linux can produce a better result if FreeBSD clicks, opens the wrong
endpoint, mishandles a rate transition, triggers a DAC relock, or suffers a
userspace scheduling failure. “Better” in that case means absence of a concrete
fault, not mystical operating-system sonics.

Linux can also be safer with unfamiliar USB interfaces because its driver knows
more device quirks and implements more feedback/clock topologies.

## 12. Practical recommendation

| Priority | Recommendation |
|---|---|
| Maximum set-and-forget reliability | Use Linux with ALSA and the already verified bit-perfect open-media-drc chain. |
| Continue FreeBSD development/testing | Use Q16.16 + explicit feedback, keep diagnostics enabled, and retain the hot legacy rollback. |
| Critical listening on patched FreeBSD | Prefer MPD/BruteFIR or another controlled playback path until Chromium playback-only sndiod is retested. |
| Diagnosing a click | Press **I heard a click**, then correlate the marker with feedback, short-transfer, playback-error, CPU, and application logs. |
| Comparing OS sound | Match level/rate/format/DSP exactly and use blinded repeated trials plus an analogue capture if possible. |

The honest bottom line is:

- **Linux is presently the safer hi-fi appliance choice.**
- **Patched FreeBSD has not shown a transport defect in the controlled OKTO
  Q16.16 test and is reasonable to keep testing.**
- **There is no basis to claim that glitch-free, bit-perfect Linux has inherently
  superior sound to equally glitch-free, bit-perfect FreeBSD.**
- **There is also no basis to dismiss the previously heard clicks:** they were
  real enough to investigate, and the browser session was using an unintended
  full-duplex/capture-derived path.

