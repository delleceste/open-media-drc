# OKTO / FreeBSD uaudio audit against Linux 7.2.3

Date: 2026-09-22. Target: MPD/Qobuz → 192 kHz DRC → OKTO DAC8 STEREO,
on the same dual-boot computer. This is an audit, not a driver change.

## Result and limits

The patches fix substantial clock/feedback defects, but **“Linux-style
scheduler” does not establish equivalent transport behaviour or sound**.
There are still differences in feedback application latency, duplex policy,
clock validation, error visibility, and recovery. None establishes the cause
of the reported loss of air, detail, or dynamics. Conversely, a DAC lock and
zero uaudio error counters do not exclude an audible fault.

The user specifically distinguishes **continuous tonal/spatial degradation**
from clicks and dropouts. For that symptom, the underrun/recovery findings below
are secondary correctness findings, not the leading explanation. A successful
audit must investigate the analogue waveform's level, channel relationships,
frequency/phase response, noise, and nonlinear behaviour. A transport fix is
not a demonstrated improvement in any of those properties.

No missing Linux USB-kernel processing stage was found that restores detail,
air or dynamics. For ordinary PCM, the examined paths copy samples and manage
delivery. FreeBSD's Q16.16 arithmetic does not low-pass or compress the music.
This is a bounded source finding, not a dismissal of the listening report or
a proof of equal analogue output.

The most useful remaining investigations for this particular playback path are:

1. Measure the OKTO's actual analogue level and confirm its control state on
   both systems; host sample equality cannot exclude device-side attenuation.
2. Compare the actual MPD/Qobuz/192 kHz/120.green output samples, including
   continuity, while also collecting PCM underruns and USB completions.
3. Compare analogue output using an independent ADC. If submitted samples agree
   but analogue response, noise, distortion or channel relationships differ,
   investigate device state, conversion timing and electrical coupling.
4. Measure feedback response and packet scheduling with the OKTO, especially
   the current FreeBSD 8 ms batching versus Linux's feedback-interval limit.
   This is a mechanism investigation, not a substitute for demonstrating an
   analogue effect. Electrical coupling and internal DAC behaviour cannot be
   decided from host driver source.

At audit time the attached device is the office Cambridge Audio DAC100
(`22e8:dac4`); the user confirms the OKTO is at home. No playback was interrupted,
kernel replaced, mixer changed, or audio setting changed. No new OKTO listening,
transport, or analogue measurement was possible in this session.

## Sources and identity

- Actual FreeBSD source: `/usr/src/sys/dev/sound/usb/uaudio.c`, plus PCM
  `channel.c`, `feeder.c`, `feeder_chain.c`, `mixer.c`, USB `usb_transfer.c`, and
  controller `xhci.c`. The checked `/usr/src` is not a Git checkout.
- Running OS: FreeBSD 15.1-RELEASE-p2, `releng/15.1-n283596-aadd58dddcbc`.
- Linux root is already mounted at
  `/media/KINGSTON_SM2280S3G2120G_50026B726706A3BC_s1`; its home partition is `s2`.
  Installed modules and pacman metadata identify **7.2.3-arch1-3**. The saved
  OKTO Linux probe independently reports this same release.
- No complete Linux source/build tree was found in the installed `/usr/src`
  or module directory. The comparison uses upstream stable **v7.2.3**, fetched
  from Greg Kroah-Hartman's tree, not an unpinned `master` or a Linux 6.x tree.
  Arch's exact downstream patchset and binary/source correspondence were not
  reproduced; that remains a provenance limitation.
- Downloaded reference files are in `/tmp/uaudio-audit-20260922.4BBHUM`:
  `sound/usb/{endpoint,pcm,clock,format,stream,mixer,mixer_quirks,quirks,implicit,
  card,helper}.c`, headers, `quirks-table.h`, and host `xhci{,-ring}.c`.
  Functions relevant to the findings were inspected; this is not a claim to
  have exhaustively verified every line of the Linux USB/ALSA subsystem.

FreeBSD source SHA256:
`efb03e61db945e50b9197f21d24e2d3d09538e303477c73298a7f1e8c5233520`.
Both `/boot/kernel/snd_uaudio.ko` and the corresponding `/usr/obj` module have
SHA256 `186c9f1f621ccc18e2a2c1b4285b4370b9d1b1c4b388b47d7bbb60a83fd8865b`.
The loaded module exposes the patch sysctls. Matching files and live controls
support deployment, but do not constitute a fresh reproducible build or a hash
of the module already resident in kernel memory.

## Findings

### 1. Feedback arithmetic parity does not include timing parity

**Confirmed implementation difference; plausible transport risk, audible
consequence unproven.**

FreeBSD `uaudio.c:1810` onward derives transfer length from `buffer_ms`;
`uaudio.c:2997` uses the current feedback value for every packet in that
transfer. There are two audio transfers and one single-frame feedback transfer
(`uaudio.c:677`). The current sysctl and `/etc/sysctl.conf:28` specify 8 ms.
For the OKTO's high-speed OUT endpoint, `bInterval=1`, this means 64
microframe packets planned in each audio callback. Feedback arriving after a
batch was prepared cannot resize that already queued batch. Two batches cover
approximately 16 ms of audio at nominal rate.

Linux limits audio URB packet count using the explicit feedback interval and
maintains four sync URBs. The OKTO's saved descriptor says feedback
`bInterval=4`, i.e. 1 ms; this limits each audio URB to eight microframes before
other sizing constraints. Its overall queued duration is a separate quantity:
smaller URBs do not mean no queued latency. See
[endpoint.c, data/sync endpoint setup](https://github.com/gregkh/linux/blob/v7.2.3/sound/usb/endpoint.c#L1187)
and [card.h](https://github.com/gregkh/linux/blob/v7.2.3/sound/usb/card.h#L6).

The important difference is when a new feedback value can affect packets and
how much feedback work is queued, not the precision of Q16.16. A DAC whose
feedback varies with FIFO occupancy may respond differently to additional
delay. This needs FIFO/packet evidence; it does not automatically imply altered
frequency response or conversion-clock jitter.

Do not set FreeBSD to 1 ms and call that Linux parity: its two-transfer queue
would then have much less scheduling slack. A controlled 8/4/2 ms experiment
must reopen the stream and check underruns/completions. A future implementation
could separate batch duration from queue depth, but that is not implemented here.

### 2. Clean uaudio diagnostics do not prove uninterrupted audio

**Confirmed observability gap, directly relevant to the DRC workload.**

`uaudio.c:2874` calls `chn_intr()` before copying the next PCM buffer.
`pcm/channel.c:419` updates and refills the hardware buffer;
`pcm/feeder.c:310` (`feed_root`) pads missing playback data with silence and
increments the PCM channel's `xruns`. These samples can travel in successful,
full-length USB transfers. The new uaudio diagnostics do not include those PCM
xruns, application/virtual_oss starvation, or the DAC's internal FIFO state.

This corrects finding 4 of `AUDIT-feedback-series.md`: ordinary producer
starvation is not adequately described as unconditional stale-byte replay.
There is a PCM refill/silence path above the USB callback. Other failure modes
still need investigation, but the call chain cannot be omitted.

Also do not overstate Linux's `avail` protection: in `pcm.c:1542` it is enabled
for the low-latency path outside draining; it is not an unconditional guard on
every Linux playback mode. The installed Linux BruteFIR defaults contain
`ignore_xrun: true`, so application recovery policy also matters.

Consequence: capture `/dev/sndstat` with per-channel detail before/after the
same musical passage, as well as uaudio counters and BruteFIR/MPD logs. Audible
silence insertion would more naturally produce interruptions/distortion than
a stable treble shelf; there is no evidence yet it caused the reported dullness.

### 3. Host-controller recovery can hide a scheduling discontinuity

**Confirmed diagnostic limitation; no observed OKTO failure established.**

`usb_transfer.c:3691`, `usbd_xfer_get_isochronous_start_frame()`, detects a
schedule that is too late or not synchronized and moves the next transfer into
the future. `xhci.c:2158` uses that result to resynchronize, but uaudio does not
count this event. A scheduling gap can therefore matter even when subsequently
submitted data completes successfully.

Separately, FreeBSD `xhci.c:1030` converts an isochronous error into successful
status with the affected TD's data remaining untransferred. This is not total
invisibility: uaudio's short-byte/short-transfer counters may expose the loss.
However, the original per-packet cause is lost at that interface. Linux keeps
per-packet statuses such as missed service and transaction errors in
[xhci-ring.c](https://github.com/gregkh/linux/blob/v7.2.3/drivers/usb/host/xhci-ring.c#L2388).
That does not mean every such Linux event becomes an ALSA userspace error.

`scripts/audit-usbdump-transport.py` correctly says USBPF lacks transfer IDs
and per-frame error codes. Submission hashes prove what the host submitted,
not what the DAC converted. Host trace timestamps are not electrical bus
timestamps. Controller resynchronization needs separate tracing/counters.

### 4. The shared-clock guard still permits an incompatible duplex stream

**Confirmed correctness defect; conditional relevance to the user's path.**

In `uaudio.c:1662`, any active opposite direction causes a shared-clock write
to be skipped, even if its rate differs. Configuration then proceeds with the
requested rate's software framing. A capture-open at a different rate can
therefore leave software and hardware disagreeing. The running/alternate
observations also do not form a per-clock, locked ownership transaction.

Linux represents clock ownership separately and constrains stream parameters
against endpoints already in use. FreeBSD needs to reject incompatible requests
before arming the second stream and propagate failure to the PCM client.
Merely refusing `SET_CUR` is not a complete transaction.

For normal playback-only MPD → BruteFIR this should be dormant. Verify on the
OKTO that no sndiod/browser/other client opens its capture interface.

### 5. An open capture stream overrides explicit feedback

**Confirmed remaining Linux-policy difference.**

`uaudio.c:2884` treats a configured capture alternate as the timing source;
`uaudio.c:2933` permits Q16.16 explicit scheduling only when capture is absent.
Thus `feedback_mode=1` and `prefer_feedback=1` do not guarantee that the patched
scheduler is controlling playback. The live diagnostic must report `source=1`;
`source=2` means capture-derived timing.

Linux associates explicit/implicit sources through the selected endpoint
configuration (`pcm.c:330`, `implicit.c`); it does not simply replace an
explicit source because another capture stream was opened.

This is significant for the OKTO's capture interface, previously reported not
to provide useful captured audio. It is less likely for the confirmed MPD/DRC
path than for the earlier browser/sndiod case, but must be checked at home.

### 6. Clock validity is still not checked like Linux

**Confirmed missing control semantics; mainly startup/transition risk.**

FreeBSD parks the interface, reads the clock, skips a redundant write, writes
when needed, and waits up to its configured settling delay. These are useful
fixes. But failed writes/parking and a wrong readback can still lead to arming;
the post-write readback checks only the last changed clock. It does not use
the selected stream's complete clock topology or validate the source's
clock-valid control. See `uaudio.c:1610–1756` and clock traversal at `5439`.

Linux checks writability and resolves/validates the clock source, including
after a same-rate open. It returns errors for invalid clocks. Linux itself
only logs and continues for some rate-readback mismatches, so it must not be
described as rejecting every mismatch. See
[clock.c](https://github.com/gregkh/linux/blob/v7.2.3/sound/usb/clock.c#L561).

The recorded OKTO source `0x29` advertises `bmControls=0x07`: writable rate and
readable validity. That missing validity check applies to this device, unlike
many generic quirks. The descriptor shows one internal source behind selector
`0x28`; wrong selection among multiple independent oscillators is not supported
by the recorded topology. Repeat the control trace after a cold boot and a
44.1→192 kHz transition. A persistent sonic effect from startup state remains
a firmware hypothesis until measured.

### 7. Feedback discovery regressions from the previous audit remain

**Confirmed defects, not demonstrated culprits on the recorded OKTO.**

`uaudio.c:2081` requires feedback usage bits and searches only forward from the
current descriptor. Some Linux-supported descriptor layouts or usage-0 feedback
endpoints are missed. `uaudio.c:1791` then binds endpoint zero when none was
recognized, with `no_pipe_ok`, rather than preserving stock wildcard behaviour.

Also, a nonzero `bSynchAddress` is merely preferred: if no match exists, the
helper returns another feedback endpoint. Linux validates a supplied association
and can reject it. Therefore patch 0004 does not strictly enforce that field.
Any fix must avoid mistaking an implicit-feedback data endpoint for a 3/4-byte
explicit value; “accept every opposite-direction isoch endpoint” is too broad.

The saved OKTO has an explicit feedback endpoint and working feedback, so these
are upstream/regression blockers rather than evidence for its current timbre.

### 8. Missing feedback and stale feedback are not Linux-equivalent

**Confirmed differences; conditional flow-control risk.**

`uaudio.c:2650–2771` counts empty, short, and zero feedback as bad and resets
format autodetection. Linux ignores short/failed/zero reports without resetting
the detected shift. Existing diagnostics can exaggerate malformed feedback.

`uaudio.c:2939` drops a valid rate after at least 250 ms without a fresh report
and returns to nominal packet sizing. Linux retains the last accepted measured
rate. Nominal free-running is not necessarily safe indefinitely for an async
DAC: accumulated sample deficit/excess is `rate_error_Hz × elapsed_seconds`.
Neither holding the last value nor reverting to nominal is universally correct
for a failed device. Recovery needs an explicit policy and observability.

Full-speed UAC1 `bRefresh` handling is also absent. It does not explain this
high-speed UAC2 OKTO. Starting the empty record-sync callback remains pointless
but is not an established playback corruption mechanism.

### 9. No missing PCM “quality” processing found in the packet math

**Checked negative finding, limited to the examined PCM path.**

The sample-rate normalization, fractional phase accumulation and packet-capacity
clamp follow Linux's design. The callback copies consecutive PCM bytes; changing
packet length does not itself discard or duplicate a sample. Neither normal USB
PCM copy path applies a treble filter, compressor, gain, or interpolation.

The FreeBSD fallback can change how fast samples are delivered, and starvation
can change their contents via the PCM layer. Those are separate mechanisms.
The broad accepted feedback range is not proof of the DAC's actual oscillator
rate or correctness: measure the observed range and long-term delivered count.

### 10. The 24/32-bit alternate-setting theory is weakened by actual Linux data

**Real format-selection limitation, not an established cross-OS difference here.**

FreeBSD `uaudio.c:2322` uses subslot size rather than valid-bit resolution, and
retains a single preferred format per rate. Linux retains format precision
metadata and chooses per stream (`format.c:35`, `pcm.c:93`). This matters for
native 16-bit selection, other devices, and DSD.

However, `bp-results/dac-format-linux.json` records the OKTO at **192000 Hz,
S32_LE, interface 1, alt 1**, whose descriptor says **24 valid bits in four-byte
slots**. FreeBSD's source and recorded S32 stream are consistent with choosing
that same first alternate, though the FreeBSD JSON explicitly has
`selected_alt: null`; a fresh `GET_INTERFACE`/trace would confirm it directly.

Thus “Linux uses the 32-valid-bit alt and FreeBSD loses eight audible bits” is
unsupported for the saved comparison. Dither/truncation after floating-point
DRC deserves numerical checking, but both saved defaults disable dither and
the Linux example also uses alt 1. Zero-padding a genuine 16/24-bit source
preserves its sample values; that fact alone does not prove an entire DRC chain.

### 11. Hardware gain/control state is outside the bit-perfect guarantee

**Potentially important audible mechanism; OKTO state not measured today.**

FreeBSD's bitperfect mode bypasses converting software feeders
(`feeder_chain.c:689`); it does not prohibit USB feature-unit volume/mute
requests. Mixer values are mapped into the device's control range
(`uaudio.c:5968`), and generic FreeBSD mixer defaults include 75 for volume/PCM
(`mixer.c:70`). A percentage is not a universal dB value or a unity guarantee.

Today's Cambridge mixer displays 0.75 for volume and PCM. This is **not evidence
of OKTO attenuation**: those controls can be emulated software controls, and
bitperfect can bypass them. Do not interpret the readout as a measured 25%
signal reduction or turn it up as an alleged fix.

On the OKTO, record actual supported feature controls, their GET_CUR values,
front-panel level, output mode and analogue test-tone amplitude on both boots.
Linux's ALSA control state and FreeBSD's mixer initialization/restoration can
differ even when PCM payload hashes match. Also check per-channel polarity,
balance and any device-resident filter/mode settings if exposed. Their presence
or OS-dependent change is a hypothesis, not established by this audit.

The [manufacturer's Stereo manual](https://www.oktoresearch.com/assets/dac8stereo/dac8stereo_owners_manual.pdf)
provides a particularly relevant check: **Volume → USB sync** permits host
control of master volume and defaults to off. It also documents balance,
left/right output source selection, PCM reconstruction filters and harmonic
compensation. Record these settings on both boots. **DPLL BW AES** is documented
for AES/EBU, so it is not a justified USB adjustment. No source evidence here
shows that either OS changes the reconstruction filter or harmonic settings.

USB packet-arrival variation must not be equated with D/A aperture jitter. The
device advertises asynchronous USB: samples are buffered and consumed in the
device's clock domain. An audible timing/noise effect would require a mechanism
inside the actual hardware, such as supply/ground coupling or clock circuitry
sensitive to host activity. Those remain testable hypotheses, with no magnitude
or audibility established by this code comparison. The same physical PC can
have different electrical activity under different OSes, but that observation
alone is not evidence that this OKTO's output is affected.

### 12. The relevant installed DRC files match, but the whole Qobuz run is unproven

**Useful exclusion with a clearly bounded scope.**

The currently running FreeBSD command selects
`120.green/brutefir-192000@120.green.multipos.fdw6.conf`. Linux's saved
`last_arg` selects `192000 @120.green.multipos.fdw6`. The corresponding installed
configuration files compare equal, including **8.0 dB attenuation on both
channels**. Their installed RAW coefficients have identical hashes:

| Channel | SHA256 on both installations |
|---|---|
| L | `3795ef751a8bad481cbc6e013aef518a19f060609e07c1af7f057990704bac4a` |
| R | `b4ccd78c67da134d1b8cc8209c30a00b133f1779c677d33e84793d01db4b8b6b` |

Both installed MPD configurations specify soxr “very high”, no volume
normalization, disabled software mixer, and a 192000:24:2 resampling output.
This checks configured intent, not library versions, the selected MPD output,
runtime gain, source/master identity, Qobuz delivery or actual negotiated format.

BruteFIR defaults still differ: FreeBSD **8192,64**, Linux **32768,16**. Both
give 524288 taps, float64 processing and S32 output without dither. The difference
changes latency/CPU scheduling; it is not a missing filter tail. Saved
`null-192000-partitioning.json` reports zero differing samples over 26.857 s
for a previous 120.blue partition comparison. Do not extrapolate that test to
every input/workload, and do not increase FreeBSD's block size casually: its
virtual_oss buffer budget differs from ALSA.

## Evidence that must retain its original scope

- `bp-results/null-192000-xos-fbsdcoef.json`: previous 120.blue, 192 kHz,
  identical coefficients and partitions, **18.363 s / 3,525,692 stereo frames,
  zero differing samples**. Strong evidence that these OS paths can agree;
  not a new measurement of this 120.green/Qobuz/OKTO listening session.
- `high-frequency-120.green-v1-fdw6-192k-freebsd-vs-source.json`: reported
  submitted-sample response difference up to 19 kHz is at most **0.003 dB**.
  This is an older fixture/variant and an office DAC test, not an OKTO analogue
  sweep or a proof about all music.
- September 14 transport captures identify the **DacMagic 100**, not the OKTO.
- September 6 OKTO control traces support removal of the redundant clock write
  and borrowed capture startup. The trace is a host request trace, not a physical
  bus analyzer. It does not prove the internal DAC settled or its output matches.
- Today's Cambridge diagnostics show explicit feedback, no recorded USB
  anomalies, bitperfect enabled, and zero vchans. These do not measure today's
  PCM xruns or the home DAC.

## Other possible causes and their status

| Mechanism | Assessment for this case |
|---|---|
| Resampling response, aliasing, wrong source rate | Compare the same saved source through the full actual MPD route; configured soxr equality is insufficient. |
| ReplayGain, player gain, clipping, safety limiter, runtime BruteFIR changes | Inspect active state and capture quiet passages plus peaks; equal files cannot exclude runtime changes. |
| Wrong channel mapping, polarity, balance, unexpected mix | Stereo-distinct fixture and actual payload comparison; no such defect found in normal copy path. |
| Kernel EQ/software volume/SRC | Bypassed on an actually bitperfect hardware channel; check the entire upstream route as well. |
| Missing PCM-specific OKTO quirk | No `152a:88c5` entry found in fetched quirks/implicit/mixer tables. Vendor `152a` selects `DSD_RAW`, not a PCM sound-quality workaround. |
| Native DSD versus PCM/DoP | Linux supports additional DSD modes; not an explanation for the stated 192 kHz PCM DRC path. |
| Full-speed feedback/bRefresh, UAC3, complex implicit feedback | Real general driver gaps, outside the recorded OKTO high-speed explicit UAC2 path. |
| CPU/IRQ starvation, realtime scheduling, memory pressure | Can cause underruns or USB schedule gaps; collect under representative load rather than infer from idle CPU. |
| USB power management, controller scheduling, radio activity, PSU/ground noise | OS policies differ; dropout mechanisms need transport evidence, analogue coupling needs an independent ADC. No specific cause established. |
| DAC PLL/oscillator/FIFO/DSP state after boot or rate change | Lock alone is insufficient evidence; check clock validity and analogue output after controlled starts. |
| Sighted comparison/level differences | Control level, musical source and trial order; an unblinded report alone cannot identify a kernel defect. |

## Focused home experiment

Use the existing `doc/OKTO-UAUDIO-REPRODUCTION.md` with the following corrections
and additions, preserving the current setup before each test:

1. Identify the actual OKTO `ugen` and `pcm` units and `/dev/dsp.dac` target.
   Use the user's real **192000 @120.green.multipos.fdw6** configuration, not
   an assumed `@v1-fdw6` alias. Save firmware/descriptors, active format/alt,
   MPD output, gain, BruteFIR configuration/coefficient hashes and DAC level.
2. Use the same locally saved source/master on both boots for repeatability,
   through MPD's actual DRC route. Then reproduce via Qobuz separately.
   Include a stereo-distinct continuity probe and real music with quiet detail
   and peaks. Avoid changing several variables together.
3. Capture submitted PCM **and completions/feedback/control requests**. Collect
   PCM xruns before/after, `source=1`, feedback distribution, short bytes/errors,
   and application logs. Analyze a steady interval separately from intentional
   starts/stops; raw host-event maximum gaps across a restart are misleading.
4. Compare aligned audio samples across systems without fitting away gain or
   EQ. Quantify gain, polarity, channel differences, discontinuities, spectra,
   noise and peak handling. Exact RAW coefficient equality is already available
   for the current variant, so preserve it for this experiment.
5. If a transport hypothesis remains, reopen between **8, 4, 2 ms** FreeBSD
   batch tests, keeping everything else fixed. Record actual packet timing and
   errors. Smaller buffers increase CPU/scheduling risk; do not treat a smaller
   number as inherently better sound or as full Linux emulation.
6. Capture the same OKTO analogue outputs into an **independent** ADC, at matched
   measured level and fixed gain, under both OSes. Account for independent ADC
   clock drift when aligning long captures; report any correction. Compare
   frequency response, noise/distortion and transient/peak behaviour. Keep USB
   connection, DAC settings, amplifier path and test material constant. A
   blinded listening comparison can accompany this, not replace the measurements.

## Patch follow-up priorities

Before claiming broad upstream readiness: repair endpoint association/fallback,
separate absent from malformed feedback, and reject incompatible shared-clock
opens. Add clock-valid checking with appropriate device-error handling. Extend
diagnostics to PCM starvation and controller scheduling discontinuities before
using “all counters zero” as a clean-bill-of-health statement. Consider batch
duration/queue-depth redesign only with measured OKTO feedback/transport data.

The audit does **not** justify adding EQ, dithering, clock delays, disabling
radios, forcing a different bit depth, or altering the production kernel as a
presumed cure. The unexplained listening result remains open; the source and
saved evidence narrow where the next measurement will be informative.
