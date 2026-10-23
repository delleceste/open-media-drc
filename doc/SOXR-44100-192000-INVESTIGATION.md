# 44.1 kHz / 24-bit → 192 kHz: resampler and stereo investigation

Date: 2026-10-23. The reported symptom is substantial high-frequency loss and
reduced stereo width/depth on FreeBSD with the OKTO DAC8, compared with Arch.
The source is 24-bit/44.1 kHz; MPD resamples to the 192 kHz BruteFIR chain.
The OKTO machine is currently unreachable. Measurements below are from `dal`,
not from that machine. No production configuration or service was changed.

## What supplies MPD's resampler?

MPD's `soxr` plugin calls the separate **libsoxr** library. Arch's SoX executable
comes from **sox_ng**, but that is a different dependency. Official package
metadata lists [libsoxr as an MPD dependency](https://archlinux.org/packages/extra/x86_64/mpd/),
[libsoxr 0.1.3-5](https://archlinux.org/packages/extra/x86_64/libsoxr/), and
[SoX with the sox_ng upstream](https://archlinux.org/packages/extra/x86_64/sox/).
These are repository versions, not an inventory of the unavailable Arch host.

On `dal`, `ldd /usr/local/bin/musicpd` resolves `libsoxr.so.0` to
`/usr/local/lib/libsoxr.so.0`; `pkg info libsoxr` reports 0.1.3_3, with SIMD,
PFFFT, AVFFT and OpenMP disabled. The installed MPD configuration selects
`plugin "soxr"`, `quality "very high"`. MPD 0.24.13's
`src/pcm/SoxrResampler.cxx` maps this to `soxr_quality_spec(SOXR_VHQ, 0)`,
uses one thread by default, and FLOAT32 interleaved input/output. This is the
same source API on both operating systems. CPU kernels/build flags can differ;
the target host's actual library, config and recipe still need verification.

The local MPD executable currently cannot be newly launched: its FFmpeg
`libavutil.so.60`, `libavformat.so.62`, `libavcodec.so.62` and `libavfilter.so.11`
dependencies are missing. This is a separate local installation problem, not
evidence about playback on the OKTO machine. The direct libsoxr probe does not
need those libraries and completed successfully.

## Direct test of the installed library

[scripts/soxr-response-probe.c](../scripts/soxr-response-probe.c) uses MPD's
recipe at 44100→192000, with a half-scale impulse exactly representable in
24-bit PCM. It measures frequency response, identical-channel consistency,
and left-to-right leakage. It calls the library directly and produces no
sound. Results: [soxr-response-dal-44100-192000.txt](../bp-results/soxr-response-dal-44100-192000.txt).

| Frequency | Gain |
|---|---:|
| 1–12 kHz | within 0.000001 dB of unity |
| 16 kHz | −0.000202 dB |
| 19 kHz | −0.002996 dB |
| 20 kHz | −0.006221 dB |
| 21 kHz | −4.037782 dB |

The reported recipe is 28-bit precision, phase response 50 (linear),
passband_end 0.911328320 and stopband_begin 1.0, relative to source Nyquist.
The roll-off near 21 kHz is the reconstruction filter's transition band.
There is no measured broad treble loss in this library test. Removing the
known impulse delay leaves approximately zero phase at the measured frequencies.
Identical L/R impulses gave **zero differing output samples** across 192,000
frames. A left-only impulse gave **exactly zero right output**.

This tests the installed filter kernel and channel handling. It does not test
MPD's streaming wrapper, output conversion, resampling overshoot/clipping on
music, the target machine's build, FIR coefficients, USB delivery or analogue
output. A pass cannot identify the cause of the listening difference.

Build on FreeBSD:

```sh
cc -O2 -Wall -Wextra -Werror -I/usr/local/include \
  scripts/soxr-response-probe.c -L/usr/local/lib -lsoxr -lm \
  -o /tmp/omdrc-soxr-response
/tmp/omdrc-soxr-response
```

On Arch, omit `-I/usr/local/include` and `-L/usr/local/lib`. Compare the recipe,
engine, gain/phase and stereo results, not just package names or binary hashes.

## Why existing results do not close this case

The S16 virtual_oss expansion finding does not explain this 24-bit scenario
when MPD actually negotiates S32. The source audit supports transparent normal
S32 arithmetic apart from the negative full-scale endpoint clamp; it cannot
exclude every scheduling or delivery failure.

The earlier cross-OS exact FIR null used rate-matched material. The old
resampler residual test fits amplitude and phase freely, so a small residual
alone does not prove correct frequency response. The more relevant
`high-frequency-120.green-v1-fdw6-192k-freebsd-response.json` tests 44.1/24
through a 192 kHz chain and agrees with today's direct library measurement
(−0.003 dB at 19 kHz). It is a FreeBSD submitted-PCM result from the DacMagic
setup; there is no corresponding committed Arch capture for that test here.
The analyser normalizes each channel to its own 1 kHz amplitude. It therefore
cannot establish absolute L/R gain balance, interchannel delay/phase or
crosstalk, which matter to the reported soundstage change.

The documented listening sessions scored 4/4 and 7/10. Their post-hoc pooled
11/14 has nominal p≈0.029, while the document explicitly labels the result
suggestive because pooling was decided afterwards. This warrants investigation;
it neither localizes the fault nor proves a particular software mechanism.
A repeated listening session need not precede technical localization.

## The separate SoX dependency

SoX/sox_ng **can affect generated FIR coefficients**. `scripts/REW2raw.sh`
uses `sox ... rate -v -L -s` and deployment converts generated filter assets.
The September investigation already found different generated RAW files on
the two OSes despite equal source/configuration files. That particular filter
comparison showed only very small differences, but it does not establish the
current listening design's deployed state. Git synchronization alone does not
prove identical active files, selected design, channel assignment, attenuation,
or runtime BruteFIR gain/delay settings. Read the active process configuration
and hash the actual L/R coefficient files. For a controlled comparison, use the
same coefficient bytes on both boots; separately explain any generated-file
differences instead of assuming that they are audible.

## Next localization on the OKTO machine

1. Inventory both running setups: MPD executable/version and loaded libsoxr,
   selected config and quality, active output's negotiated rate/format,
   ReplayGain/normalization/volume, BruteFIR runtime channel gain/delay and the
   actual loaded coefficient files. Record the OKTO route, volume and filter
   settings. Preserve originals before any change.
2. Resample the exact test track to 192 kHz **once**, preserve headroom, and
   distribute that identical PCM file to both boots. Keep BruteFIR, coefficients,
   attenuation and DAC at 192 kHz. If the difference persists, live MPD
   44.1→192 resampling is not necessary for it. If it disappears, compare the
   live resampler/output-conversion paths against this fixed-file control;
   that observation alone does not establish a libsoxr defect.
3. Capture the same 44.1/24 music passage and channel-distinct probes before
   BruteFIR and after BruteFIR on each OS. Measure absolute gain, response,
   relative L/R phase/delay, leakage and mid/side energy, retaining the source
   and provenance. Do not normalize each channel independently or fit away the
   differences being sought. Compare resampled streams after alignment with
   appropriate numerical tolerance; repeat on the same OS to measure its
   repeatability rather than assuming all differences are inherent.
4. If submitted PCM agrees, retain USB completion/underrun evidence and capture
   both OKTO analogue channels together through one independent stereo ADC.
   Compare repeated same-OS captures before interpreting cross-OS differences.
   The existing [REW procedure](OKTO-REW-CROSS-OS-PROCEDURE.md) covers channel
   response; simultaneous stereo capture is needed to preserve interchannel
   timing for the soundstage question.

No evidence currently identifies sox_ng as the MPD resampling culprit, and the
local libsoxr measurement does not reproduce the stated loss. The first missing
boundary is the unavailable OKTO host's actual post-MPD stereo signal and active
runtime provenance. Replacing virtual_oss or changing DAC rates now would add
variables before that boundary is measured.
