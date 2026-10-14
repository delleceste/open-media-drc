# Bass depth detector (`tools/bass_depth.py`)

Goal: tell from an orchestral recording whether the double basses sit at the
back of the stage or are spot-mic'd "into the mix". If it works, it becomes a
real omdrc tool (it lives in `tools/` for that reason).

Status as of 2026-10-12: **v1 synthetically validated; first real recording
analysed** (see "Case study 1" below). More labelled recordings to come.

## How to run

```sh
tools/bass_depth.py track.flac [more.flac ...] --plot DIR [--json out.json]
tools/bass_depth.py track.flac --segments 4:12-4:30,7:05-7:40   # known exposed bass
tools/bass_depth.py --selftest        # synthetic natural / spot / aligned spot
```

Deps: numpy, scipy, ffmpeg on PATH (decodes any FLAC/rate to 16 kHz stereo),
matplotlib only for `--plot`. No pip installs needed on this box.

## What v1 detects

An **unaligned bass spot mic**: the basses reach the mix through the spot
first, then Δ later through the main pair (Δ = path difference / 343 m/s,
typically 5–40 ms). Verdicts: `none`, `likely` (GCC only), `double-arrival`
(GCC + AC agree), `possible` (AC only), `insufficient` (too little exposed
bass or fewer than 4 distinct notes).

Pipeline:
1. **Bass frames**: per 256 ms frame, best f0 in 29–66 Hz (below cello C2).
   Bass frame = first 6 harmonics carry ≥ 50% of 25–1500 Hz energy (`--dom`)
   AND ≥ 5 of those 6 are spectral peaks (`--min-peaks`). Accepts ~68% of
   exposed-bass frames, ~6% of tutti (synthetic).
2. **Pool per semitone**, then per note compute:
   - **GCC** (decides): coherence-weighted PHAT of the L/R cross-spectrum in
     150–2500 Hz. Unaligned spot shows at +(Δ+ITD_R) and −(Δ+ITD_L).
   - **AC** (corroborates): mono whitened autocorrelation.
3. **Pitch-masked average across notes**: each note's curve repeats every
   feature (even GCC's main peak) at k·T0; mask those lags per note, average
   complex curves; only a geometry-fixed delay survives.
4. Peak must have robust z ≥ 5.5 (`--z`), be ≥ −20 dB vs main peak, and be
   ≥ 6 dB above the orchestra reference (non-bass frames, 300–4000 Hz) at the
   same lag (`--specific-db`). Search window 5–60 ms.

## Lessons from building it (don't relearn these)

- Pitch period (15–35 ms) overlaps Δ: naive cepstrum/autocorr finds pitch.
  Harmonic sources make GCC pitch-periodic too (false 21.4 ms = F#1 period).
- Mono AC is inherently weak: within a note the comb is sampled only at
  harmonics → Δ ambiguous mod T0. Hence GCC decides.
- A low-f0 comb fits subharmonics of any upper note (cello at 3·f0 passes an
  odd-harmonic test) → hence the "first 6 harmonics + peak count" rule.
- Bands: 150–2500 Hz beat 80–1500 (low harmonics carry long reverb).
- Min lag 5 ms: at 3 ms the main-peak skirt gave a false positive.
- L/R coherence did NOT separate aligned spot from natural synthetically
  (0.57 vs 0.60) — kept as informational only.

## Synthetic validation (8 seeds, Δ 12–33 ms, bass DRR 0 and −4 dB)

- unaligned spot: 16/16 detected, Δ within 0.6 ms
- natural: 16/16 none; aligned spot: 16/16 none
- spot 8 dB under the hall pickup: missed (sensitivity limit)
- **thin margin**: worst null z = 5.2 vs threshold 5.5 → calibrate on real data

## Next steps: labelled real tracks

Bring (FLACs from Qobuz, any rate):
- 3–4 tracks where the basses sound properly *back*
- 3–4 where they sound *forward / spotlit*
- 1–2 unsure
- best of all: **the same work, different labels/engineers**

For each, one line in a labels file, e.g. `tools/bass_depth_labels.txt`:

```
path/to/file.flac | good | 4:12-4:30 pizz basses exposed | basses woolly but behind
path/to/other.flac | bad | 1:05-1:40 | basses dry, sharp, narrow hard right
```

Then:
1. Run all with `--plot`; compare verdicts to labels.
2. For disagreements, inspect plots + listen to listed segments: wrong
   passages (tuba/contrabassoon/cello doubling)? aligned spot (v1 is blind
   to it)? coincident main pair?
3. Recalibrate `--z`, `--dom`, `--min-peaks` on the real set.
4. v2: relative depth cues for aligned spots — DRR proxy (onset energy vs
   tail) and pizzicato attack rise time, basses vs violins; build a "bass
   depth index".
5. Optional: Aalto anechoic orchestra recordings (Pätynen/Pulkki/Lokki 2008)
   + stage RIRs for controlled ground truth.
6. Once useful: integrate as a proper omdrc tool, commit, doc section.

Known risks on real material: tuba/contrabassoon pass the bass test; cellos
an octave up leak into the band; added reverb on the spot; coincident (XY/
Blumlein) main pairs have no ITD.

## Case study 1 (2026-10-12): Shostakovich, BSO / Nelsons (DG 2023, 24/96) — label: good depth

Tracks 01 (Sym 2 Ia), 02 (Sym 2 Ib), 07 (Sym 12 I), from the USBHD2 download.

Exposed bass found where the user said:
- Sym 2 Ia: 10:53–11:11 (A1/A♯1 then F♯1/G1, C1 → basses with C extension).
- Sym 12 I: bass passage 4:23–5:25 (mostly A♯1, also C2/B1/A1/G♯1/F1), again 5:33–5:46.
- Sym 2 Ib ~1:00 with the choir: basses never dominate (only 3.4 s of bass frames
  across the whole track) → unusable for v1.

v1 results:
- GCC (the deciding estimator): nothing above z 5.5 in any run; best peaks
  inconsistent across tracks (−17 ms, +8.7 ms, +25 ms) → **no unaligned spot**.
- AC: "possible" at 8.5 ms and 17.2 ms in BOTH tracks. **This is a pitch artefact.**
  17.2 ms = period of A♯1 and 8.6 ms = period of A♯2. A♯1 dominates both passages,
  and its energy (sustain + hall tail) leaks into the frames grouped under other notes.
  Example: Sym 2 F♯1 frames have more A♯1 comb energy than F♯1 energy, and Sym 12
  C2 frames have A♯1 only 7 dB below C2. Each group masks only its own k·T0.
- Main ITD 0.00 ms for both the bass and the orchestra. Either the main array is
  coincident, or the image is level-panned. Basses sit +1.5 dB to the right.

Fixes this suggests:
1. Pitch mask: per group, also mask k·T0 of every note whose comb is within ~10 dB of
   the group's own (or of the passage's dominant notes). Mask T0/2 as well
   (the octave-up partner).
2. Verdict: "AC only" should require the peak to survive across ≥2 notes
   with no shared pitch.
3. ITD≈0 → flag "coincident/level-panned: GCC delay method weak" in the report.

## Case study 2 (2026-10-14): Mahler 3/I, Czech Phil / Bychkov (2025, 24/96) — label: bass not convincingly back

User passage "around minute 9". Exposed bass is at 7:50–8:08 (A1) and 9:29–9:51 (D1/A1,
quiet). Around 9:00 itself the passage is loud and the basses never dominate.

v1 results:
- Whole track: **"likely unaligned spot" at +15.25 ms** (GCC z 5.8, −8.7 dB, 13 dB
  above the orchestra), corroborated by AC. **Not trustworthy:**
  - z is barely over the 5.5 threshold (the thin margin seen in synthetic tests).
  - The peak is one-sided (+ only: R later). A spot mic picked up by both main
    mics should show on both sides.
  - Split into 60 s windows, no window shows it (z ≤ 3.2 at +15.25). The best
    peak per window wanders (−51…+36 ms) at z ≈ 4, i.e. noise level.
  - It is not the C2 period (15.29 ms): the C2 group shows nothing there; the
    A1 group (z 5.1) and D1 group (z 2.6) carry it.
- The user's passages only (`--segments 7:50-8:08,9:28-9:52`): GCC none;
  AC 8.9 / 13.4 / 26.9 ms = A2 period, D1 half period, D1 period → pitch
  artefacts again (same weakness as case 1).

Cues that DO differ from case 1 (n = 2, so hints only):

| | Shostakovich/Nelsons (good) | Mahler/Bychkov (not back) |
|---|---|---|
| L/R coherence bass vs orchestra | 0.45–0.50 vs 0.39–0.46 (×1.1–1.2) | 0.37–0.42 vs 0.24 (×1.5–1.75) |
| energy < 70 Hz in exposed-bass seconds | 20–50 % | 1–7 % |

A bass much more coherent than the orchestra means its L/R pickup is more direct
(less diffuse hall) than everything else. A weak fundamental with strong upper
harmonics fits a close mic (bow and string detail over hall bass). Both cues are
confounded by dynamics, register (D1 fundamental is 37 Hz) and hall, and need more
labelled tracks before they become a v2 "depth index".

## Case study 3 (2026-10-14): Talking Heads, "Heaven" (Fear of Music, 1979, CD rip) — out of scope

A review claims the bass sits "well at the back"; the user hears it centre and very
front. A studio mix has no stage, so v1 does not apply. With `--fmax 110` it gives
"none": the GCC peaks (z 13–16) are stronger in the rest of the mix than in the bass,
i.e. mix-wide delays and reverb.

Mix measurements (`mixdepth.py`/`tail.py`, scratch scripts):
- 30–120 Hz: L/R coherence 0.95–0.99, side 15–20 dB under mid → mono, centre.
  From 120 Hz up: coherence 0.72–0.82, side −7…−9 dB.
- Bass band side/mid rises only +2.7 dB from attack to decay (−14.6 → −11.9 dB);
  the 700–2500 Hz band rises +1.3 dB. Almost no stereo ambience around the bass.
- 60–250 Hz holds ~38 % of the mix energy (kick included).

Nothing supports "at the back": dry, mono, loud all read as front. Caveat: mono
lows were standard for vinyl cutting in 1979, so centre/mono alone is no
depth cue. A dark tone (few upper harmonics) can read as distance, but it can't be
separated from the guitars in a full mix.

## Case study 4 (2026-10-14): Dvořák 8/I, Budapest Festival Orch. / Fischer (DSD64) — label: weak depth (from memory)

Only 7.7 s of exposed bass (G1 0:37, C2 1:22–1:30, B1 3:39–3:42, …), 5 notes.

v1: GCC best +15.25 ms (z 5.4, just under threshold), AC "possible" at 10.1 ms.
- AC 10.1 / 8.1 / 16.2 ms = G2, B2, B1 periods → pitch artefacts (G major movement).
- **GCC +15.25 ms is a method artefact; this also explains case 2.** It is the
  same lag as Mahler, in a different hall. It is the C2 period, and C2 carries
  about half the note weight. At the lags where a heavy note is masked,
  `pitch_masked_mean` averages fewer notes, so noise peaks are taller:
  95th percentile of the envelope 3.3× the baseline where coverage is < 0.6,
  2.2× where coverage is ≥ 0.95. The peak sits at coverage 0.49. The robust z
  is computed over all lags as if the noise were uniform. No 60 s window shows it.
  **Fix:** normalise the masked mean by its expected noise per lag
  (env · Σm / √Σm²), or z-score per coverage bin.

Cues (compare case 1 ×1.1–1.2 / 20–50 %, case 2 ×1.5–1.75 / 1–7 %):
- coherence bass 0.59 vs orchestra 0.28 → **×2.1**
- energy < 70 Hz in bass frames: median **3 %** (IQR 1–6 %)

Both cues sit on the "not back" side, further out than Mahler, which matches the
user's memory. So far 3 classical recordings, ordered consistently by both cues.

## v1.1 fixes (2026-10-14)

1. **Pitch leak** (`group_pitches`): each note group masks the periods of its own
   note, of other bass notes whose non-shared harmonics are within `--leak-db`
   (10 dB) of its own, and the octave (T0/2) of each. Harmonics within 3 bins of
   the group's own count as its own (Hann main lobe). With a 1.5-bin radius,
   semitone neighbours looked like leaks and coverage at the true Δ fell to 9 %.
2. **Noise scaling** (`pitch_masked_mean`): returns the noise floor of the
   weighted mean relative to the unmasked one (√Σm²/Σm); peaks and robust z
   are taken on env / noise. Reported dB levels still use the raw envelope.

Synthetic sweep (8 seeds × DRR 0/−4 dB): spot 15/16 (miss at z 5.4, Δ 33 ms,
DRR −4), Δ error ≤ 0.4 ms, false positives 0/32, worst null z 5.1. v1: 16/16, 5.2.

Real cases, before → after:

| Case | v1 | v1.1 |
|---|---|---|
| Sym 2 Ia, whole | AC possible 17.2 ms (A♯1) | none |
| Sym 2 Ia, 10:53–11:12 | AC possible 17.2 ms | none |
| Sym 12 I, whole | AC possible 8.1 ms | AC possible 8.0 ms |
| Sym 12 I, 4:23–5:46 | AC possible 8.5 ms (A♯2) | none |
| Mahler 3/I, whole | **likely spot +15.25 ms** (z 5.8) | none (z 5.2) |
| Mahler 3/I, 7:50–9:52 | AC possible 8.9 ms | AC possible 13.4 ms |
| Dvořák 8/I, whole | AC possible 10.1 ms; GCC +15.25 z 5.4 | AC possible 6.8 ms; GCC +15.19 z 4.7 |

AC "possible" still lands on periods of pitches above the bass range (6.8 ms =
D3, cellos), which no bass-range mask covers. Treat it as a listening hint only.

## Case study 5 (2026-10-14): Shostakovich 13/II "Humor", Chicago SO / Muti (live, 2020, 24/96) — blind guess

7.3 s of exposed bass in 23 short bits (loud, staccato scherzo), 4 notes.
v1.1: none (GCC −14.1 ms z 5.4, one-sided).
Cues: coherence bass 0.51 / orchestra 0.48 = **×1.07** (case-1-like, natural);
< 70 Hz median **4 %** (IQR 1–14 %; case-2/4-like, but short staccato notes
never build up their fundamental). Guess: natural, moderately back; low confidence.

## Case study 6 (2026-10-14): Naji Hakim, *Påskeblomst* 1. Hymne, St. Michael's Strings / Komulainen, Lehtola organ (DSD) — blind guess

52 s, 9 s low notes on 3 pitches → v1.1 "insufficient". With `--min-notes 1`:
AC possible 10.2 ms (unreliable on 3 notes). Cues: coherence **×1.00**,
< 70 Hz median **15 %** (IQR 4–23 %).
**Most low notes are probably organ pedal, not basses.** Pitch wobble on the
2nd–4th harmonics is 3–6 cents (sd) on C2 and F1, against 23–30 cents for bowed basses
(Nelsons Sym 12, 4:24–4:43). An A♯1 at 0:47 wobbles 17 cents (strings?). The
cues then describe the organ (far, in the room), not the string basses. A
pitch-steadiness test should become part of bass-frame detection (organ, also
synths, sustained tuba).
