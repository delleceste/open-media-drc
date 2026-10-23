# virtual_oss in the OMDRC chain — can it cost sound quality?

**Date:** 2026-10-23 · **Host:** `dal`, FreeBSD 15.1-RELEASE (sources in `/usr/src`)
**Trigger:** the first double-blind sessions (`SOUND-QUALITY-INVESTIGATION.md`
§10.1) hint that FreeBSD sounds "not as airy and defined" as Linux.
**Scope:** read the virtual_oss sources and man page, and decide for each stage
of the configuration `drc.sh` uses whether it can change samples. No running
system was changed. The two models in §4–§5 are arithmetic, not captures.

Builds on `FREEBSD-AUDIO-SOURCE-AUDIT-2026-09-11.md`, which first found the
S16 expansion noise (§4 here). This study adds three things: the noise's
spectrum, the MPD half of the same path, and which material actually reaches
that path.

## 1. Result in one paragraph

For **32-bit clients at the chain rate**, virtual_oss is an exact identity in
this configuration. Every stage is either an integer multiply by 128 and a
shift right by 7, or disabled. That is the path the blind-test track took
(44.1 kHz/24 → MPD S32), so **virtual_oss cannot explain the blind-test
result**. However, **every 16-bit source** takes a different path on
FreeBSD than on Linux. MPD hands virtual_oss S16. If the chain rate differs
from the track's, MPD first truncates its float resampler output to 16 bits,
with no dither. virtual_oss then adds ±1 LSB16 of low-frequency-tilted noise
when it widens to 32 bits. On Linux the same track stays 32-bit to BruteFIR,
because snd-aloop gives both sides one format. Modelled worst case:
error floor about −94 dBFS in-band on FreeBSD, about −190 dBFS on Linux, and
signal-correlated harmonics on low-level tones. That is a real, FreeBSD-only
change to the samples, at the level where "air" lives. It is not yet
established that anyone can hear it.

## 2. The configuration under study

`drc.sh:82`, started at `drc.sh:2092`:

```
virtual_oss -D /tmp/virtual_oss.pid -r <rate> -i 8 -C 2 -c 2 -b 32 \
            -s 200ms -f /dev/null -a 0 -d dsp.play -L dsp.loop
```

| Option | Effect in the source |
|---|---|
| `-r <rate>` | one mixer rate; `SNDCTL_DSP_SPEED` refuses other rates unless `-S` (`main.c:1276`), so MPD resamples (soxr) |
| `-i 8` | `rtprio(RTP_PRIO_REALTIME, 8)` for the process |
| `-C 2 -c 2` | 2 mix channels, 2 device channels; `format_remix()` is a no-op |
| `-b 32` | profile width 32 bits; mixer works in `int64_t` |
| `-s 200ms` | `voss_dsp_samples` = rate × 0.2: the mixer moves **200 ms blocks** (38 400 frames at 192 kHz) |
| `-f /dev/null` | null backend for both rx and tx: rx returns zeros, tx discards |
| `-a 0` | `rx_shift = tx_shift = 0`: no gain |
| `-d dsp.play` | MPD's client device, **not** synchronized |
| `-L dsp.loop` | loopback device, **synchronized**: its reader (BruteFIR) paces the engine |

Consumers: MPD writes `dsp.play` with `mixer_type "none"` (`mpd/musicpd.conf.in`),
so no `SETPLAYVOL` ever reaches virtual_oss and `tx_volume` stays 128. BruteFIR
reads `dsp.loop` as `S32_LE` (`etc/open-media-drc/brutefir_defaults.conf:50`).
`omdrc-cdin` opens `dsp.play` lazily, only while a disc plays, at 32 bits by
default (`cdin/src/main.c:617`, `.bits = 32`), so it is not a second client
during MPD playback.

## 3. Stage-by-stage walk through the engine

Locations are relative to `/usr/src/usr.sbin/virtual_oss/virtual_oss/` unless
stated.

| # | Stage | Code | Sample-changing? (S32 client, matched rate) |
|---|---|---|---|
| 1 | Client write → `tx_ring[1]` → `format_import()` → `tx_ring[0]` (int64) | `main.c:881` | No. `pvc->sample_rate == voss_dsp_sample_rate`, so the libsamplerate branch is skipped. S32 import is a sign-extending byte copy (`format.c:76`) |
| 2 | Engine pacing | `virtual_oss.c:345`; `lib/virtual_oss/null/null.c:71` | No. With `dsp.loop` open, `voss_has_synchronization > 0`, so the null rx does not sleep; the loop is paced by BruteFIR's reads and capped at 2× rate |
| 3 | Read MPD's ring | `virtual_oss.c:80` | Only on underrun: a partial block is zero-filled (`:95`), an empty one is skipped. That would be a dropout, not a tonal change. The FreeBSD self-null (21 s, 0 differing samples) shows it does not happen in steady state |
| 4 | Equalizer | `eq.c` `vclient_tx_equalizer` | No: `tx_filter_size == 0` (no `-F`/`-G`) |
| 5 | Mixer: `(*src * volume) << shift` plus optional noise | `virtual_oss.c:140–203`, `:544–567` | No. `shift_fmt = 32 − 32 = 0`, `shift_orig = 0`, `shift = −7`, `volume = 128`: `(x·128)>>7 = x` exactly. `shift_orig > 0` is false, so **no noise** |
| 6 | Audio-delay locator, monitors | `virtual_oss.c:511`, `:635–770` | No: none configured |
| 7 | Loopback feed (`buffer_temp` → BruteFIR's `rx_ring`) | `virtual_oss.c:775–830` | No. Same arithmetic as row 5 (`rx_shift 0`, `rx_volume 128`, S32 reader); per-profile compressor off (no `-g`) |
| 8 | Output compressor | `virtual_oss.c:845`, `compressor.c` | No: `enabled` is 0 (no `-x`), and there is no `-t` control device to turn it on |
| 9 | Loopback export to BruteFIR | `format.c:196` | Clips to ±0x7FFFFFFF: only −2³¹ becomes −2³¹+1, 1 LSB at full scale. MPD already clamps, and BruteFIR attenuates afterwards |
| 10 | tx to `/dev/null` with "jitter fix" repeat/skip | `virtual_oss.c:864` | Irrelevant: null `delay()` returns −1 and the data is discarded |

**Conclusion for the S32 path:** identity, which agrees with the cross-OS null
(§6.8 of the investigation, 0 differing samples). The source explains why the
null came out the way it did.

## 4. The S16 path inside virtual_oss

For a client that negotiated `AFMT_S16_NE`, with `-b 32` and `-a 0`
(`virtual_oss.c:544–551`):

```
shift_fmt  = 32 − 16 = 16
shift_orig = 0 + 16 = 16      → selector picks the noise branch (virtual_oss.c:180)
shift      = 16 − 7 = 9
out        = (x · 128) << 9  +  vclient_noise(state, 128, 16)
           =  x · 65536      +  noise
```

`vclient_noise()` (`main.c:192`) is a 24-bit Galois LFSR (`0xFFFF1D`), scaled
here to ±65536 S32 units, i.e. **±1 LSB of 16-bit**. It is added to every
sample the client delivers, **including digital silence**. It runs whether or
not the material was already dithered.

What the 2026-09-11 audit did not look at is the noise's spectrum. Each output
is the previous LFSR state halved plus an optional constant. That makes it a
first-order low-pass of a binary sequence, not white noise
(`freebsd-virtual-oss-patch/audit/voss-noise-spectrum.py`):

| Property | Value |
|---|---|
| range, S32 units | −65 536 … +65 534 |
| RMS, full band | −95.08 dBFS (matches the earlier reproducer) |
| lag-1 autocorrelation | **0.500** |
| PSD 20–1000 Hz vs top 10 % of band | **+9.5 dB**: tilted toward low frequencies |
| in-band (20 Hz–20 kHz) RMS at 192 kHz | **−98.0 dBFS**: half the power is audible-band; white noise of the same RMS would be −101.9 |
| in-band RMS at 44.1 kHz | −95.2 dBFS |

So virtual_oss's "dither" is the opposite of noise shaping: most of its power
sits where hearing is most sensitive. It is also not TPDF, so it does not
decorrelate a truncation that happened upstream (§5).

## 5. How 16-bit material gets there, and what Linux does instead

MPD 0.24.13 source, read at the tag:

1. `DRC-native` is `format "*:*:*"`, so the output format is the decoder's:
   a 16-bit FLAC (Qobuz "CD quality", any CD rip) is `S16`.
2. The OSS plugin maps S16 straight to `AFMT_S16_NE`
   (`OssOutputPlugin.cxx:394`) and tries the requested format first. virtual_oss
   lists S16 in `VSUPPORTED_AFMT`, so S16 is accepted. (24-bit sources fall back
   to S32, because MPD undefines `AFMT_S24_*` off Linux.)
3. When the chain rate differs from the track's (e.g. a 44.1 kHz track with the
   chain at 192 kHz), soxr runs in float (`SoxrResampler.cxx:226`) and
   `PcmConvert` then converts **float → S16**, the output's format.
   `FloatToIntegerSampleConvert` is `SL(src * factor)` (`FloatConvert.hxx:26`):
   **truncation toward zero, no rounding, no dither**. MPD's `PcmDither` is used
   only for 24/32 → 16, not for float → 16.
4. virtual_oss widens that S16 to S32 and adds §4's noise.

On **Linux** the loopback is snd-aloop. BruteFIR already holds its capture side
at S32, and aloop limits the playback side to the same format (investigation
§6.2: `S32_LE` on every hop). So MPD's ALSA output gets S32 and converts float
→ S32 directly: no 16-bit requantization, no added noise. *(Inferred from
aloop's format linking and the 192 kHz/24-bit hw_params record. It has not
been observed with a 16-bit track playing. See §7, step 1.)*

Modelled with a −20 / −60 dBFS 997 Hz tone at 192 kHz, from soxr's float output
to the samples BruteFIR reads (`freebsd-virtual-oss-patch/audit/s16-path-model.py`):

| Path | In-band error RMS | H3 / H5 re tone, −20 dBFS | H3 / H5 re tone, −60 dBFS |
|---|---|---|---|
| Linux, float → S32 | −218.6 / −193.5 dBFS | −180 / −179 dB | −140 / −145 dB |
| FreeBSD, float → S16 only | −96.1 / −95.8 dBFS | −84 / −88 dB | **−44 / −47 dB** |
| FreeBSD, + virtual_oss noise | −94.0 / −93.8 dBFS | −84 / −88 dB | **−44 / −47 dB** |

A pure undithered tone is the **worst case**. Real 16-bit masters carry
their own dither, which partly decorrelates the second truncation. But
truncation toward zero always leaves a −½·sign(x) LSB term that follows the
signal. That term is signal-correlated distortion, about 96 dB below full
scale, and it falls on reverb tails and ambience: exactly the low-level content
people call "air". At **matched** rate (16-bit track, chain at 44.1 kHz)
there is no MPD truncation, and only virtual_oss's noise remains (about
−95 dBFS, tilted).

How it sits against the 16-bit source's own floor (TPDF dither plus
quantization, ≈ −96 dBFS RMS): the FreeBSD path adds about as much error again.
That is roughly +4 dB on the noise floor, and part of the addition is
correlated with the signal.
The Linux path adds nothing measurable.

## 6. What this does and does not explain

- **The blind test, no.** Its only track was 44.1 kHz/**24**. MPD presented S32
  to virtual_oss, which §3 shows is an identity. If the 11/14 hint holds up in
  the fixed 16-trial session, the cause is outside virtual_oss's arithmetic.
- **Everyday listening, possibly.** Much of the library and of Qobuz is
  16/44.1, and `120.*` designs run the chain at 192 kHz, so the truncation path
  is the *normal* path for that material. The original complaint (September)
  came from ordinary listening, not from one 24-bit track.
- **Not established as audible.** ≈ −94 dBFS in-band, before the 8 dB BruteFIR
  attenuation that both OSes apply equally. At 105 dB SPL peaks that is a floor
  near 10 dB SPL. Whether a listener can hear the added correlated component
  is an empirical question.
- **Non-arithmetic differences remain** and are outside this study. virtual_oss
  is a realtime-priority process that wakes in 200 ms bursts and moves 76 800
  samples per wake through cuse, while snd-aloop is a kernel timer. That changes
  CPU and bus activity, not samples. It belongs with the electrical candidates
  in investigation §10.

## 7. Recommended next steps

1. **Confirm the formats on the live chain.** On FreeBSD, play a 16/44.1 track
   with the chain at 192 kHz. Check MPD's *output* format, not
   `%audioformat%`, which is the decoder's (e.g. `mpd -v` log, or `truss -f`
   on the `SNDCTL_DSP_SETFMT` ioctl against `dsp.play`). On Linux, read
   `/proc/asound/card*/pcm0p/sub0/hw_params` for the aloop playback side with
   the same track.
2. **Measure it.** Feed a 16-bit −60 dBFS tone file through `DRC-native` at a
   mismatched rate and tap `loop:/dev/dsp.loop` (`scripts/verify-bitperfect.sh`).
   Expect §5's harmonics on FreeBSD and none on Linux. Then repeat at a matched
   rate to separate MPD's truncation from virtual_oss's noise.
3. **A cheap fix that removes both effects:** make MPD hand virtual_oss
   32-bit. Changing `DRC-native` to `format "*:32:*"` makes MPD convert
   float → S32 (or S16 → S32 by shifting, losslessly) before OSS, so virtual_oss
   never reaches its noise branch. `DRC-resamp` already effectively runs this
   way (its `:24:` falls back to S32). This is a configuration change, not a
   patch to FreeBSD. It should be made only after step 1 confirms the
   mechanism, and checked with the same loopback tap.
4. **Blind-test follow-up:** to probe this mechanism, the fixed-count session
   needs a 16-bit track at a mismatched rate. The current 24-bit track cannot
   show it.
5. **Upstream (optional):** virtual_oss's expansion noise is undocumented in
   `virtual_oss(8)` and has no switch. A report to the maintainer (`hselasky`)
   asking for an option to disable it, or for TPDF/high-pass noise instead of
   the low-tilted LFSR, would be reasonable once step 2 has numbers.

## 8. Source fingerprints

Hashes of what was read on `dal`. The first two equal the 2026-09-11 audit's
fingerprints from `bee`, so this is the same source. `bee`'s installed binary
also carries local wait-loop patches, which affect synchronization only and no
sample arithmetic.

```
/usr/src/usr.sbin/virtual_oss/virtual_oss/virtual_oss.c  4c60086ce53dd537ac7a7abe9a0e88c4dc22333388a43fe10fb038bfc31866a6
/usr/src/usr.sbin/virtual_oss/virtual_oss/main.c         c9446d5c308eae6257c17aeff609d5f6937880022a2f6741928d54f243110c38
/usr/src/usr.sbin/virtual_oss/virtual_oss/format.c       4f9c874a4c451cc83f34df0fb8491a89873536de228b0381723d7b7434b7f26c
/usr/src/lib/virtual_oss/null/null.c                     33b3756151b89b57acf847d80693d4265be3370b1fbae365e4eb1b6b54a3c0f6
/usr/sbin/virtual_oss (dal, unpatched base build)        a48a8fb211888927ee40508f04087e6a2ea92c64b006cf9188cf5e37be257dbe
MPD v0.24.13: src/pcm/Convert.cxx, src/pcm/FloatConvert.hxx, src/pcm/SoxrResampler.cxx,
              src/output/plugins/OssOutputPlugin.cxx (git tag v0.24.13)
```
