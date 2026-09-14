# High-frequency response test — procedure

**Status: tooling written and unit-tested 2026-09-11; not yet run on either
host.** This closes the gap named in `SOUND-QUALITY-INVESTIGATION.md` §10 and
in the 2026-09-11 source audit's step 3.

## 1. Why this test and not the existing ones

The complaint is a loss of **air and detail** — a top-octave complaint. Nothing
measured so far went near the top octave, and the two existing instruments
cannot:

| instrument | why it cannot answer this |
|---|---|
| `bitperfect-null.py` | needs identical bytes, so it stops working the moment a resampler is in the path — and the resampled case is the everyday one |
| `resampler-residual.py` | fits amplitude and phase freely, so 1 dB of attenuation at 18 kHz is absorbed into the fit and the residual still reads at the 32-bit floor |

There is a second gap of the same shape. Every measurement in the campaign ran
at **−46.4 dBFS** (the nulls) or **−90 dBFS** (the tones). A defect that only
appears at a realistic listening level could not have shown up in any of them.

| | material | peak | rates |
|---|---|---|---|
| cross-OS null §6.8 | counter | −46.4 dBFS | matched |
| FreeBSD self-null §6.6 | counter | −46.4 dBFS | matched |
| rate-mismatch residual | two-tone | −90 dBFS | mismatched |
| **this test** | **multitone** | **−20 dBFS RMS** | **either** |
| **this test, `overs`** | **fs/4 @ 45°** | **−0.1 dBFS sample, +2.9 dBFS true** | **either** |

## 2. The tools

```
scripts/gen-multitone-wav.py    material + a .tones.json manifest
scripts/hf-response.py          gain and phase per tone, from the capture
tests/test_hf_response.py       14 tests; run before trusting a result
```

`hf-response.py` divides the capture by the known input. It does not fit
amplitude, so attenuation reads as attenuation. The period is chosen so one FFT
bin is the same frequency at the source rate and at the chain rate, which means
the comparison works **through a resampler** with no interpolation, no window
function and no alignment step — the trick `CROSS-OS-FILTER-COEFFICIENTS.md`
used for the two coefficient sets.

Run the unit tests first. They pin the properties the result depends on,
including the one that already bit: unwrapping phase across BruteFIR's latency
returns nonsense that looks like a measurement (the first version reported
349° of "deviation" for a bit-perfect identity chain).

```sh
python3 -m unittest tests.test_hf_response
```

## 3. Procedure

Use `configs/flat` so BruteFIR is an identity and any deviation is the rest of
the chain. `attenuation 0.0` there, so full scale passes.

### 3.1 Instrument check — matched rate

```sh
./drc.sh 44100                       # GEOMETRY=flat
python3 scripts/gen-multitone-wav.py /tmp/mt-44.wav --rate 44100 --chain-rate 44100
python3 scripts/bitperfect_runner.py --source mpd:DRC-native --route drc \
        --material /tmp/mt-44.wav --out bp-results/hf-44-44
python3 scripts/hf-response.py bp-results/hf-44-44 --tones /tmp/mt-44.tones.json \
        --json bp-results/hf-44-44.hf.json
```

**Must PASS, flat to 0.001 dB.** The bytes are the source's, so anything else
is the measurement rig, not the chain. Do not proceed past a failure here.

### 3.2 The question — 44.1 kHz material through a 192 kHz chain

```sh
./drc.sh 192000
python3 scripts/gen-multitone-wav.py /tmp/mt-192.wav --rate 44100 --chain-rate 192000
python3 scripts/bitperfect_runner.py --source mpd:DRC-native --route drc \
        --allow-resample --material /tmp/mt-192.wav --out bp-results/hf-44-192
python3 scripts/hf-response.py bp-results/hf-44-192 --tones /tmp/mt-192.tones.json \
        --json bp-results/hf-44-192.hf.json
```

Repeat at `--chain-rate 48000`, `88200`, `96000`. And at each bit depth —
`--bits 16`, `--bits 24`, `--bits 32` — because on FreeBSD the negotiated OSS
width changes what `virtual_oss` does (16-bit clients get noise added on
expansion; see the 2026-09-11 audit) while on Linux `snd-aloop` forces S32 and
the question cannot arise.

### 3.3 The level-dependent test

```sh
python3 scripts/gen-multitone-wav.py /tmp/ov.wav --mode overs \
        --rate 44100 --chain-rate 192000 --bits 16
```

The file's **sample** peak is −0.1 dBFS — a legal, non-clipping file — and its
**true** peak is +2.91 dBFS. Resample it and the overshoot becomes real
samples that an integer format must clamp. Play it through the chain and run
`hf-response.py` as above: it reports the clamped sample count and THD.

This is a worst-case probe, not music. For the realistic figure: a
music-like signal with 3 dB of limiting resamples to +0.87 dBFS true peak with
**7.4 % of samples over full scale** and a clip error at **−39.5 dBFS** — some
55 dB above anything else this project has measured. Nothing in the chain
provides headroom ahead of it (`volume_normalization "no"`, `mixer_type
"disabled"`, no replaygain, and BruteFIR's attenuation is downstream).

## 4. How to read the result

| result | meaning |
|---|---|
| flat to ±0.05 dB re 1 kHz up to 20 kHz, matched and resampled, both hosts | the top octave is intact; the digital path is exonerated for HF as well as for bytes, and the next test is analogue |
| a tilt or rolloff below 20 kHz on one host only | the first OS-asymmetric mechanism of audible size found in this project |
| a tilt on **both** hosts when resampled, flat when matched | the resampler, not the OS — run the chain at the source rate |
| `20 kHz..Nyquist` row down by a dB or more, rows below it flat | soxr's passband edge (VHQ ends at 91.3 % of Nyquist ≈ 20.1 kHz). Expected. Not a defect |
| energy above the source Nyquist | resampler images. The frequency printed beside it identifies which |
| nonzero `clipped` | the chain ran out of headroom at a realistic level — a defect neither OS's tests could have shown |
| `period-to-period drift` above −120 dB | the chain lost or inserted samples during the capture, which is itself a finding |

## 5. Pitfalls

1. **Stop the renderer.** `qobuzconnect2mpd` repopulates MPD's queue; clearing
   it is not enough. Two cross-OS captures were ruined this way.
2. **Confirm the negotiated OSS format**, not `mpc status '%audioformat%'` —
   that reports MPD's decoder. An ioctl trace or the capture's own width.
3. **Do not byte-compare** these captures across hosts. Compare the reported
   gain/phase curves. A resampled capture is not sample-identical between two
   runs of soxr, let alone two machines.
4. `--margin` defaults to 1 s each end. BruteFIR at 192 kHz with 524 288 taps
   has a long fill; raise it if the first period looks odd.
