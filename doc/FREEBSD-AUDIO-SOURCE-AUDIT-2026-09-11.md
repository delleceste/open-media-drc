# FreeBSD / Linux audio source audit — 2026-09-11

The audit found a concrete sample-changing path not exercised by the successful
32-bit nulls: **virtual_oss adds noise when expanding a 16-bit client into the
configured 32-bit mixer, even at unity gain.** Its calculated noise is about
−95.08 dBFS RMS, before room correction. This is a candidate for investigation,
not an established explanation for the reported loss of air and detail.

The patched uaudio feedback arithmetic agrees with Linux 7.2.3. I found no
steady-state high-frequency attenuation in that path. However, the existing
tests establish a narrower result than “every digital candidate is excluded”:
they compare **submitted USB payloads**, not verified reception or conversion by
the DAC, and do not characterize all input widths, levels, frequencies, or
stream transitions.

No production source, configuration, service, or FreeBSD file was changed.
The companion C program is an isolated arithmetic reproducer.

## 1. What was actually inspected

The FreeBSD filesystem was mounted from `/dev/sda5` with
`ro,ufstype=ufs2,nosuid,nodev,noexec`. `findmnt` confirmed read-only UFS.
The session mount point was `/tmp/freebsd-audio-audit.9glznW`.

Inspected the installed filesystem's:

- `/usr/src/sys/dev/sound/usb/uaudio.c`: local clock/feedback patches included.
- `/usr/src/sys/dev/sound/pcm/{channel,feeder,buffer}.c`: playback refill and
  underrun handling, including the caller of the USB driver's ring copy.
- `/usr/src/usr.sbin/virtual_oss/virtual_oss/{main,virtual_oss,format,ring,eq,compressor}.c`
  and `int.h`, plus `/usr/src/lib/virtual_oss/null/null.c`.
- Installed `virtual_oss`, its build-tree executable, its null backend,
  `snd_uaudio.ko`, MPD's library dependencies and package version, audio
  configuration, and the newer investigation artifacts on that partition.

Linux comparison uses the upstream stable **v7.2.3** tag, matching the base
version of the `7.2.3-arch1-3` cross-OS capture and current running kernel.
This is not a claim that every Arch downstream patch was compared. Sources:
[endpoint.c](https://github.com/gregkh/linux/blob/v7.2.3/sound/usb/endpoint.c),
[pcm.c](https://github.com/gregkh/linux/blob/v7.2.3/sound/usb/pcm.c),
[clock.c](https://github.com/gregkh/linux/blob/v7.2.3/sound/usb/clock.c),
[format.c](https://github.com/gregkh/linux/blob/v7.2.3/sound/usb/format.c),
[implicit.c](https://github.com/gregkh/linux/blob/v7.2.3/sound/usb/implicit.c),
[quirks.c](https://github.com/gregkh/linux/blob/v7.2.3/sound/usb/quirks.c), and
[aloop.c](https://github.com/gregkh/linux/blob/v7.2.3/sound/drivers/aloop.c).
The downloaded Makefile confirms `VERSION=7`, `PATCHLEVEL=2`, `SUBLEVEL=3`.

### Source is not always the installed binary

`/boot/kernel/snd_uaudio.ko` exactly matches the build-tree module by SHA256.
That supports its provenance; it cannot establish what a previous boot loaded.

For virtual_oss, the installed executable is stripped while the build-tree
executable retains symbols. Their full hashes differ, but their entire `.text`
sections are byte-identical: file offset `0x5410`, length `0xd55c`.
Disassembly of that matching code confirms both the noise-generating mixer and
the patched synchronized waits (`tx_enabled`, `tx_written`, `rx_enabled`).

In contrast, the present `/usr/src` virtual_oss files lack those wait-loop
patches. Do not infer that the running executable lacks a fix simply because
today's source lacks it, or rebuild assuming this source preserves every
installed fix. The noise and synchronization findings below were cross-checked
against the executable where relevant.

## 2. New candidate: noise on 16-bit expansion

Relevant FreeBSD source locations, relative to `/usr/src`:

| Location | Behavior |
|---|---|
| `usr.sbin/virtual_oss/virtual_oss/main.c:191` | `vclient_noise()` produces scaled pseudo-random noise |
| `main.c:289` | Noise seeds initialize to 1; both volumes initialize to unity, 128 |
| `virtual_oss.c:139` | Mixer adds `vclient_noise()` when passed a noise pointer |
| `virtual_oss.c:175` | `shift_orig > 0` selects the noise-enabled branch |
| `virtual_oss.c:545` | Playback computes width expansion plus configured gain shift |

With this project's `-b 32 -a 0`, a client actually opened at S16 has:

```text
shift_fmt  = 32 - 16 = 16
shift_orig = 0 + 16 = 16
volume     = 128
output     = input * 65536 + vclient_noise(..., 128, 16)
```

Thus the low bits are not simply zero-filled. The noise is approximately
±one **16-bit** sample step expressed in S32, not ±one S32 step. BruteFIR's
`dither: false` does not control this upstream operation. Neither `-a 0` nor
omitting `-S` disables it.

I copied the noise routine into the
[standalone reproducer](../freebsd-virtual-oss-patch/audit/virtual-oss-noise.c)
and exercised the unity-gain expansion arithmetic over ten million zero
samples per width. This tests arithmetic, not FreeBSD CUSE or USB playback.
The RMS reference is the full-scale magnitude `2^31`, across all generated
samples; it is not an A-weighted or audible-band measurement.

| Client → mixer | Minimum / maximum residual, S32 units | Noise RMS |
|---|---:|---:|
| S16 → S32 | −65536 / +65534 | −95.08026 dBFS |
| S24 → S32 | −256 / +255 | −143.24503 dBFS |
| S32 → S32 | 0 / 0 | zero |

Reproduce from the repository root:

```sh
cc -O2 -Wall -Wextra -Werror \
  freebsd-virtual-oss-patch/audit/virtual-oss-noise.c \
  -lm -o /tmp/voss-noise-audit
/tmp/voss-noise-audit
```

The installed machine code calls `vclient_noise` from the positive-shift mixer
branches and adds its result to the output. This is not merely a behavior in
unused source.

### Why previous captures missed it

The cross-OS FIR null used material that MPD decoded to S32. The resampler
residual captures explicitly used S32 tone files. Neither exercises an S16
client's expansion. The 16-bit rows in the newer rate-guard matrix check
reported rates; they are not 16-bit sample-integrity captures.

Also, `mpc status '%audioformat%'` reports the **decoder** format. The claim in
the original investigation that this alone proves the negotiated OSS format
is too strong.

There is nevertheless a concrete route to S16. The installed MPD is 0.24.13.
Its [OSS plugin](https://github.com/MusicPlayerDaemon/MPD/blob/v0.24.13/src/output/plugins/OssOutputPlugin.cxx)
tries the requested format first (`oss_setup_sample_format`, line 510), maps
S16 directly to `AFMT_S16_NE` (line 394), and virtual_oss accepts S16.
`DRC-native` uses `format "*:*:*"`. This makes native 16-bit playback a strong
candidate to reach the branch, but an actual ioctl trace or loopback capture
is needed to establish it for a particular listening session. Other MPD
processing can change the format presented to the output plugin.

MPD also explicitly undefines both 24-bit OSS formats on non-Linux builds
(lines 28–35), then falls back to S32. That explains why the feared packed
24-bit mismatch did not occur, including in `resamp` mode. The hypothetical
S24 row above describes virtual_oss's arithmetic, not proof MPD uses that row.

### How much does this explain?

It disproves universal transparency across input widths. It does **not** prove
that −95 dBFS noise causes a large perceptual change, nor can this expansion
path explain a difference during verified S32 → S32 playback. If the complaint
persists with native 24-bit material decoded/output as S32 at matching rates,
this candidate cannot be the complete explanation.

## 3. The clock-domain explanation in the old document is wrong

`BIT-PERFECT-VERIFICATION.md` says `-f /dev/null` inevitably leaves virtual_oss
free-running against the DAC, requiring BruteFIR to drop/duplicate samples.
It overlooks uppercase **`-L`**:

1. `main.c:2244` creates `dsp.loop` with `synchronized = true`.
2. Opening it increments `voss_has_synchronization` (`main.c:484`).
3. `lib/virtual_oss/null/null.c:76` calls the nominal-rate software wait only
   when that count is zero. The installed null backend has this conditional.
4. `vclient_write_linear()` waits for the synchronized reader to make room
   (`virtual_oss.c:113`); the installed version additionally checks that
   reading remains enabled.

Consequently, an active BruteFIR loopback reader can pace the bridge through
backpressure from its DAC output. The engine also has a protective **2× rate
ceiling**, which is not an independent nominal sample-rate clock.

```text
DAC consumption → BruteFIR output space → BruteFIR reads loopback
                → synchronized virtual_oss ring makes room → MPD can write
```

This does not rule out scheduling starvation. The ordinary `-d dsp.play`
producer is not itself synchronized: if it fails to supply a block in time,
`vclient_read_linear()` can pad missing samples with zeros. But there is no
inevitable oscillator drift caused by the configured null backend, and no
source evidence for the document's specific claim of inaudible one-sample
corrections every few minutes.

For comparison, Linux aloop supports either its default jiffies timer or an
external ALSA timer. On the current Linux boot, `timer_source` is `hw:0,0,0`,
and card 0 is the DacMagic. That is current state, not proof of the timer source
used during every historical OKTO capture.

## 4. uaudio versus Linux 7.2.3

The comparison is against the **locally patched** FreeBSD driver.

| Mechanism | FreeBSD local source | Linux 7.2.3 | Assessment |
|---|---|---|---|
| PCM container selection | `uaudio.c:2324–2326`, `:2440–2504`; one retained format per rate | `format.c:31–140`; subslot width and valid bits tracked separately | Existing OKTO captures establish the same 4-byte alt 1. No byte-placement culprit found |
| Feedback decoding | `uaudio.c:2623–2773`; normalized fixed point, bounded shift autodetection | `endpoint.c:1854–1912` | Equivalent normal OKTO feedback arithmetic |
| Packet sizing | `uaudio.c:2997–3009`; fractional phase, capped whole frames | `endpoint.c:163–181` | Same phase equation; no PCM filtering or gain operation |
| Stale feedback | `uaudio.c:2933–2952`; after max(250 ms, four feedback intervals), revert to nominal | `endpoint.c:1854–1912`; rejected/missing updates retain last accepted rate | Real policy difference; test only if feedback becomes stale |
| Active capture | `uaudio.c:2883–2910`; capture-derived correction overrides explicit feedback | Dedicated explicit/implicit endpoint relationships | A duplex client can change the FreeBSD clock-following path |
| Missing/short feedback | `uaudio.c:2650–2654`; counted bad, shift autodetection reset | `endpoint.c:1854–1856`; ignored | Known diagnostic/compatibility defect; not evidence of degraded OKTO samples |
| Endpoint discovery | `uaudio.c:2081–2111`; requires feedback usage, searches forward | `pcm.c:330–414`, `snd_usb_audioformat_set_sync_ep()` | FreeBSD can miss unconventional descriptors; OKTO feedback was observed working |
| Clock setup | `uaudio.c:1597–1770`; park, read/possibly set, settle, arm | `clock.c:600–666`; topology resolution and clock validity checks | Linux has stronger clock validity handling. Both may continue after rate readback mismatch; do not claim Linux always refuses it |
| Playback underrun | `channel.c:390–426`, `feeder.c:307–352`; refill, insert silence, count xruns | `pcm.c:1520–1600`, `endpoint.c:163–233`; availability-aware packet preparation in the applicable playback mode | Distinct recovery policies; both require observation beyond steady sample arithmetic |

The scheduler uses the same essential calculation on both systems:

```text
phase = (previous_phase & 65535) + (feedback_rate_q16 << endpoint_interval_shift)
packet_frames = min(phase >> 16, endpoint_capacity_frames)
```

Changing packet frame counts changes transport pacing, not the numerical PCM
values. Same arithmetic does not imply identical USB request grouping or
electrical activity: FreeBSD has two playback transfers with the configured
1–8 ms grouping; Linux derives its queue from endpoint and ALSA buffer/period
parameters. That difference needs delivery/analogue evidence to implicate it.

The existing 30-minute direct playback soak is useful evidence against gross
feedback instability on the OKTO. It is not a 30-minute test of the whole
MPD → virtual_oss → BruteFIR chain or every possible duplex state.

### Correction to the earlier feedback-series audit

`freebsd-uaudio-patch/AUDIT-feedback-series.md` says FreeBSD blindly replays
stale ring bytes on starvation and has no underrun handling. That conclusion
stops at the USB callback's final copy. The callback first invokes `chn_intr()`.
The PCM refill path reaches `feed_root()`, which fills shortages with silence
and increments `ch->xruns` after startup. A `feeder_root`-only chain therefore
excludes format/rate/volume conversion, but **does not exclude inserted
silence on underrun**. The custom uaudio diagnostic counters also do not replace
the PCM underrun count.

Likewise, Linux's `avail` check is conditional on its playback mode; the prior
audit's unconditional Linux-versus-FreeBSD safety comparison was too broad.

## 5. What the captures prove, and what they discard

I reran the existing cross-OS null from the actual raw files:

```text
compared 3,525,692 frames = 18.363 s
alignment confidence = 1.0
differing samples = 0; maximum difference = 0 S32 LSB
```

That result is reproducible. Its interpretation needs these limits:

- `scripts/bitperfect-lib.py:226` keeps Linux usbmon **submission** events
  (`typ == 'S'`) and discards completions.
- `scripts/bitperfect-lib.py:284` keeps FreeBSD **SUBM-ISOC** payloads.
- FreeBSD's `sys/dev/usb/usb_transfer.c:2909` takes that submission snapshot
  before calling the host-controller endpoint's `start` method. Its separate
  completion snapshot is at line 2477. The distinction is explicit in the
  kernel, not just terminology in the capture tool.
- Both flatten packets into raw PCM. The null sees no packet timestamps,
  completion errors, missed service intervals, feedback history, or control
  requests. Isochronous delivery is not established merely by submitting bytes.
- A clean interval rules out differing submitted samples in that interval.
  It cannot rule out failures elsewhere, DAC buffer behavior, or an analogue
  difference with unchanged submitted samples.
- The FIR comparison used a relatively low-level counter signal. It does not
  sweep level-dependent processing or the frequency response of every other
  format/rate path. The noise findings are specifically outside its input-width
  coverage.

The valid conclusion is “the compared submitted PCM streams are identical,”
not “everything digital and all intermittent faults have been excluded.”
This distinction leaves the original successful measurement intact.

## 6. Newer FreeBSD results found on the partition

The Linux checkout describes the FreeBSD rate-mismatch half as pending. The
mounted FreeBSD checkout already contains its completed September 11 results:
`home/giacomo/open-media-drc/doc/RATE-MISMATCH-PROCEDURE.md`, section 8,
and `bp-results/rate-*-freebsd-*`.

It reports MPD 0.24.13 with soxr, DacMagic 100, the expected 12-row rate-guard
matrix, and the same residuals as Linux: +4.0 dB above the S32 rounding floor
for 44.1→192 kHz, +2.5 dB for 192→44.1 kHz. The forced-resamp 24-bit counter
passed its source comparison. The package database and installed MPD's
`libsoxr.so.0` dependency independently support the recorded build details.

I reran the analyser against the actual FreeBSD 44.1→192 kHz raw capture:
both channels reproduce −193.4 dBFS residual RMS, +4.0 dB above the floor,
and maximum residual 2 S32 LSB.

These are good results for the tested 997/1499 Hz S32 tones. They cannot by
themselves establish high-frequency passband equality: the analyser fits
amplitude and phase freely, and these tones do not probe the top octave.
For example, attenuation of a sine can disappear into its fitted amplitude
while leaving a very low residual. A high-frequency gain/phase comparison
needs the known input or a reference response, not just residual depth.

The newer reports were inspected in place; this audit does not overwrite or
merge either checkout's earlier investigation documents.

## 7. The shortest useful next investigation

1. **Test the S16 branch first on FreeBSD.** Use a matched 44.1 kHz chain and
   native output. Play actual PCM16 material, then an S32 WAV containing the
   exact same values multiplied by 65536, with no resampling or added dither.
   Confirm the negotiated OSS format, not just MPD's decoder format. Compare
   the loopback samples or use the flat FIR route and USB tap. A zero segment
   within a playing file plus a quiet tone makes the predicted noise easy to
   identify. Expect about ±65536 S32 units before the FIR if the S16 expansion
   branch is reached; the expanded-S32 control should lack that added noise.
   Preserve the real music level and check whether the listening difference
   follows the width. Do not substitute `resamp` for this control: that changes
   rate/output policy too.
2. **Retain delivery evidence during the actual complaint.** Capture submitted
   payloads together with completion status, timestamps, feedback packets and
   control requests. Record deltas in both PCM underruns and uaudio counters,
   plus `source` (explicit feedback should be 1), `feedback_age_ms`, and active
   playback/capture clients. Run through normal track transitions and for long
   enough to include an audible event. A DAC FIFO fault can still require an
   analogue capture even when host completions look normal.
3. **Measure the top octave and normal signal levels.** Compare known
   10/15/18/20 kHz tones or a sweep, with matched and mismatched rates and the
   relevant input widths. Include gain and phase versus the reference, not
   only the freely fitted residual. This addresses the reported high-frequency
   change much more directly than another quiet 1 kHz tone.
4. **If those are equal, compare analogue output.** Keep DAC input, hardware
   volume/filter state, programme, filters and cabling controlled. Capture its
   output on each OS, account for recorder clock drift, and compare level,
   response, noise and distortion. Combine that with level-matched blinded
   listening. Source inspection cannot decide whether host electrical activity
   or DAC firmware behavior changes the analogue result.

The S16 mechanism deserves the first targeted test. There is currently no
evidence-backed reason to install another feedback-scheduler patch as a cure
for the tonal complaint.

## 8. Source fingerprints

SHA256 of the principal inspected files:

```text
FreeBSD uaudio.c
efb03e61db945e50b9197f21d24e2d3d09538e303477c73298a7f1e8c5233520
FreeBSD virtual_oss main.c
c9446d5c308eae6257c17aeff609d5f6937880022a2f6741928d54f243110c38
FreeBSD virtual_oss virtual_oss.c
4c60086ce53dd537ac7a7abe9a0e88c4dc22333388a43fe10fb038bfc31866a6
FreeBSD null backend source
33b3756151b89b57acf847d80693d4265be3370b1fbae365e4eb1b6b54a3c0f6
Installed snd_uaudio.ko (also build-tree module)
186c9f1f621ccc18e2a2c1b4285b4370b9d1b1c4b388b47d7bbb60a83fd8865b
Installed virtual_oss (stripped)
2da0c52158a7eb4847e73f862383748767f70a96331bba28fbedca032cba7c23
Build-tree virtual_oss (same .text, retains symbols)
fe4c04a099d4bbc9d3688d4aed10413dad25ba0180af19760ab0ba91712a09f9
Linux v7.2.3 endpoint.c
65766d62068b7bbc6c2c6a9a88ad4246bb00fd0aac0d7a5cd0a359a46fd31ba5
Linux v7.2.3 pcm.c
8714248d7e9d7c99167d72698b9376b9efb9d21845d8c30366df7c21c4a1affb
Linux v7.2.3 clock.c
28783339504ce3c444f983f0706090a9b9c5b4df68887ce4e4a38ddb36023863
MPD v0.24.13 OssOutputPlugin.cxx
81c22aa0ed620e29fbfb2fcf17bf3acea69d99222f1e8f6946cfdf2f99f9c61d
```
