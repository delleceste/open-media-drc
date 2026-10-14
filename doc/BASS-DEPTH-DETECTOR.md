# Bass depth detector (`tools/bass_depth.py`)

Goal: tell from an orchestral recording whether the double basses sit at the
back of the stage or are spot-mic'd "into the mix". If it works, it becomes a
real omdrc tool (it lives in `tools/` for that reason).

Status as of 2026-10-14: spot-mic detector (v1.1) plus per-context depth
cues and a **0–10 depth score** with a 🟢 / 🟡 / 🔴 marker. The score is
calibrated on only three recordings labelled by ear. Treat it as provisional
and recalibrate it (`SCORE_*` constants in the script) as labelled material
comes in. The scoreboard below collects every recording analysed so far.

## How to run

```sh
tools/bass_depth.py track.flac [more.flac ...] --plot DIR [--json out.json]
tools/bass_depth.py track.flac --segments 4:12-4:30,7:05-7:40   # known exposed bass
tools/bass_depth.py --selftest        # synthetic natural / spot / aligned spot
```

Deps: numpy, scipy, ffmpeg on PATH (decodes any FLAC/rate to 16 kHz stereo),
matplotlib only for `--plot`. No pip installs needed on this box.

Each file's report ends its context block with a line like

```
   DEPTH: 8.3/10 ● green (back / natural) — ×coh 1.17, solo fund +4.7 dB, 89s
```

The dot is coloured on a terminal (`NO_COLOR=1` turns that off). Given
several files (e.g. all movements of a symphony), the script also prints a
combined score, `== all N files together`. JSON output carries the same
numbers under `"depth"`.

## Live on the box: tracking sessions

To calibrate against the ear without moving files around, the panel runs the
detector while you listen (omdrc-ctrl README, "Bass depth: tracking
sessions"):

1. On the box you listen on, open **Bass depth** (web UI Listen tab, or the
   kiosk page) and **Start tracking session**, naming the recordings you mean
   to evaluate. Outside a session nothing is fetched or analysed.
2. Play them. Each track is analysed in a few seconds and kept on the USB
   drive under `musica/bass-analysis/<artist>/<album (year)>/` (local tracks
   already on that drive stay where they are).
3. Whenever the basses catch your ear, tap **Forward**, **In between** or
   **Back**, and add a note if you like. The tap records the moment heard and
   the timeline passage under it.
4. **Stop session.** Within the DR log's sync interval the session, the taps
   and the analyses reach the repository (`bass/<box>.jsonl`).
5. On dal: pull a clone of the repository and run
   `tools/bass_depth_calibrate.py <clone>`. It lists, per context, how the
   passage scores and cues follow the verdicts, which taps fell where the
   detector found no bass, and, with enough taps, a least-squares proposal for
   new anchors. Proposals are reviewed with the user, never applied by
   themselves. With the drive here, the same tracks can be re-analysed and
   listened to together.

`VERSION` in `bass_depth.py` names the detector's results: bump it whenever a
change alters them, so the boxes re-analyse and calibration compares like
with like.

## Scoreboard

Higher score = basses further back, in the hall. 🟢 ≥ 7 back / natural,
🟡 4–7 in between, 🔴 < 4 forward. "Material" = seconds of solo plus
with-sections bass the score rests on; under 10 s it is provisional.

| Recording | Label by ear | Score | Material |
|---|---|---|---|
| Shostakovich 2 + 12/I, BSO / Nelsons (DG) | back, good | 🟢 9.2 | 102 s |
| Mahler 3/I, Czech Phil / Bychkov | not convincingly back | 🟡 4.8 | 78 s |
| Dvořák 8/I, Budapest FO / Fischer | weak depth | 🟡 5.7 | 24 s |
| Sibelius 2/I, Minnesota / Vänskä (BIS) | praised for depth and soundstage | 🟡 6.0 | 25 s |
| Mahler 2/II, Rattle | opening pizzicato placed wrong | 🟢 8.3 | 17 s |
| Shostakovich 15, NRPO / Wigglesworth (BIS) | — | 🟢 9.8 | 267 s |
| Shostakovich 1, NRPO / Wigglesworth (BIS) | — | 🟢 9.0 | 144 s |
| Shostakovich 13, CSO / Muti | — | 🟢 8.5 | 631 s |
| Shostakovich 11, NRPO / Wigglesworth | — | 🟢 7.8 | 430 s |
| Nelsons album by symphony: 2 / 3 / 12/I / 12/II–IV / 13 | — | 🟢 9.9 / 🟢 9.3 (8 s, provisional) / 🟢 9.0 / 🟢 7.6 / 🟢 9.3 | |

Detector 1.4 (matched reference, see "Matched reference" below). The
three labelled rows set the scale. **After the fix the score separates the
labels only weakly and mostly through the fundamental cue**: bowed-bass
coherence is ×1.14 for Nelsons against ×1.25–1.35 for the other two, and
×1.13 for the Minnesota Sibelius, which reviews praise and which scores
yellow only for its weak fundamental (−16 dB). Nearly everything else is
green. Treat the score as unproven.

## Reading the results

The per-context lines and the tables below use these terms.

**Contexts.** Every 256 ms frame where the basses are found playing is put in
one of three groups:

- **solo**: the basses alone. Their notes carry at least 70 % of the energy
  between 25 and 1500 Hz.
- **with sections**: basses clearly present (at least 10 %) together with
  other instruments, e.g. with the cellos or under the woodwinds. Usually
  the largest group.
- **tutti**: basses under the full orchestra, in the loudest 30 % of the
  track.

A fourth overlay, **pizzicato**, marks plucked notes (see below).

**Seconds** (e.g. "solo 41 s") is how much music a context was measured on.
It tells you how far to trust the numbers next to it: 40 s is solid, 4 s is
not.

**Coherence** says how similar the left and right channels are at a given
frequency, from 0 (unrelated) to 1 (one predicts the other exactly).

- A microphone close to the basses picks up mostly their direct sound, which
  reaches both channels in the same way: high coherence. The basses sound
  close and forward.
- Heard from the main microphones far away, the basses arrive mostly as hall
  reflections from every direction. Left and right then differ: low
  coherence. The basses sound back and spacious.

It is measured only on the bass harmonics between 80 and 700 Hz, only in the
frames of that context.

**The × factor.** Raw coherence depends on the hall and the main microphones
(a dry hall makes everything coherent). So the script divides the basses'
coherence by the orchestra's, measured in the same band where the basses are
silent:

    × = bass coherence ÷ orchestra coherence

- ×1.0: the basses are as diffuse as the rest of the orchestra, in the same
  acoustic space.
- ×1.6 and more: the basses are much drier and more direct than everything
  else, as when a spot microphone brings them forward.

Example: Nelsons Sym 12/I has basses 0.47, orchestra 0.39, so ×1.18.

**Fundamental** (dB) is the level of the note's lowest partial against the
mean of harmonics 2–6. Positive: a round, fundamental-heavy bass, the way the
hall delivers it to distant microphones. Negative: a brighter bass with
strong upper harmonics and bow detail, the way a close microphone hears it.
Labelled recordings: Nelsons +9.9, Mahler/Bychkov −14.3, Dvořák/Fischer
−11.6 dB. This cue is weaker than coherence. It also depends on which notes
are played and on the mixing EQ, and it is this project's own rule of thumb,
not an established measure.

**Pizzicato clarity** (dB) is the energy of a plucked note in its first 80 ms
against the following half second. A distant pickup and the hall fill the
tail, so lower = more distant. Few plucks are found (see below), and so far
this cue does not order the labelled recordings.

**Reading them together.** Low × (near 1.0–1.3) with a positive fundamental
points to basses back and natural. High × with a negative fundamental points
to basses brought forward. When the two disagree (Wigglesworth 15: ×1.64 with
+8.2 dB) the score lands in the middle.

## Depth score (0–10)

Only the cues that ordered all three labelled recordings correctly go in:
solo and with-sections ×coh, and solo fundamental. Tutti coherence and
pizzicato clarity stay out until they do.

1. **Coherence part.** The seconds-weighted mean × of solo and
   with-sections, mapped on a straight line: ×1.0 → 10, ×2.2 → 0, clipped.
2. **Fundamental part.** The seconds-weighted solo fundamental, mapped:
   −15 dB → 0, +10 dB → 10, clipped.
3. **Score** = ⅔ coherence part + ⅓ fundamental part. With no solo
   passage, the coherence part alone.
4. **Marker:** 🟢 ≥ 7 back / natural, 🟡 4–7 in between, 🔴 < 4 forward.
   Under 10 s of material it is flagged provisional.

Example, Nelsons 2 + 12/I: × 1.23 gives 8.1; +9.9 dB gives 10.0;
⅔·8.1 + ⅓·10.0 = 8.7 🟢.

The anchors and weights were chosen by hand so that three recordings fall
where the ear put them. Three points cannot test a scale. Every new labelled
recording should first be scored blind, then compared with the ear, before
the anchors are moved.

## How it works, in simple words

1. **Decoding.** ffmpeg turns any file into 16 kHz stereo. Everything the
   method uses lies below 4 kHz.
2. **Spectra.** The track is cut into overlapping 256 ms slices. Each slice
   goes through an FFT, which says how much energy (and what phase) every
   frequency has, in steps of 3.9 Hz: fine enough to separate 30 Hz notes.
3. **Finding the basses.** A bowed bass note is a *harmonic comb*: energy at
   its fundamental f0 and at 2·f0, 3·f0, … For every slice the script tries
   each candidate f0 between 29 and 66 Hz (below the cello's lowest note)
   and keeps the one whose first six teeth hold the most energy. Enough
   share, and enough teeth that are real spectral peaks, mean the basses are
   playing. The share decides the context: solo, with sections, tutti.
   Harmonic-comb pitch estimation of this kind is standard in music signal
   processing [Klapuri 2003]; this one is a simple version restricted to
   the bass range.
4. **Coherence.** Each slice is first scaled to the same loudness, so every
   moment counts the same however loud it is. Then, for each frequency, the
   script adds up over all slices of a context the left-right cross-spectrum
   and the two channels' powers.
   The coherence is how much of the left-right relationship stays consistent
   across slices [Carter, Knapp & Nuttall 1973]. Why distance lowers it: in a
   diffuse reverberant field the correlation between two microphones falls
   with frequency × spacing [Cook et al. 1955], while direct sound stays
   correlated. Concert-hall acoustics measures spaciousness the same way at
   the listener's ears (IACC) [ISO 3382-1; Beranek 2004]. Dividing by the
   orchestra's coherence, so that each recording is compared with itself, is
   this project's own choice.
5. **Fundamental vs harmonics.** The level of harmonic 1 against harmonics
   2–6, read from the same slices. Distance changes a source's level, its
   direct-to-reverberant ratio and its spectrum, and listeners use all three
   to judge distance [Zahorik, Brungart & Bronkhorst 2005; Bronkhorst &
   Houtgast 1999]. Using this particular spectral ratio as a depth cue is
   this project's heuristic, kept because it ordered the labelled recordings.
6. **Pizzicato.** On a finer 16 ms grid the script follows the energy of the
   bass harmonics and looks for a sharp rise (≥ 6 dB within 64 ms) followed
   at once by a steady decay. That is onset detection [Bello et al. 2005] plus
   a test that the note is plucked, not bowed. The clarity value is a per-note
   version of the hall clarity index C80 [ISO 3382-1].
7. **Spot-microphone check.** A spot microphone near the basses, mixed in
   without a matching delay, makes each bass note arrive twice: through the
   spot, then a few milliseconds later through the main pair. Generalised
   cross-correlation with phase transform (GCC-PHAT) [Knapp & Carter 1976]
   compares left and right with the spectrum's colour removed, so delays
   stand out as peaks. The project's addition is pitch masking: bass notes
   repeat every feature at their own period, so those lags are hidden note
   by note, and only a delay common to all notes survives.
8. **Score.** The linear mapping and weighting above.

## Sources

- Knapp, C. H. & Carter, G. C. (1976). The generalized correlation method
  for estimation of time delay. *IEEE Trans. Acoustics, Speech, and Signal
  Processing* 24(4), 320–327. — GCC-PHAT delay estimation (step 7).
- Carter, G. C., Knapp, C. H. & Nuttall, A. H. (1973). Estimation of the
  magnitude-squared coherence function via overlapped fast Fourier transform
  processing. *IEEE Trans. Audio and Electroacoustics* AU-21(4), 337–344. —
  coherence from averaged, overlapping FFT frames (step 4).
- Cook, R. K., Waterhouse, R. V., Berendt, R. D., Edelman, S. & Thompson,
  M. C. (1955). Measurement of correlation coefficients in reverberant sound
  fields. *J. Acoust. Soc. Am.* 27(6), 1072–1077. — two-point correlation
  in a diffuse field is sin(kr)/kr: reverberation decorrelates (step 4).
- ISO 3382-1:2009. Acoustics — Measurement of room acoustic parameters —
  Part 1: Performance spaces. — definitions of IACC and clarity C80
  (steps 4 and 6).
- Beranek, L. L. (2004). *Concert Halls and Opera Houses: Music, Acoustics,
  and Architecture*, 2nd ed. Springer. — how IACC, clarity and bass
  reverberation relate to what listeners hear in halls.
- Zahorik, P., Brungart, D. S. & Bronkhorst, A. W. (2005). Auditory distance
  perception in humans: a summary of past and present research. *Acta
  Acustica united with Acustica* 91(3), 409–420. — level, direct-to-
  reverberant ratio and spectrum as distance cues (step 5).
- Bronkhorst, A. W. & Houtgast, T. (1999). Auditory distance perception in
  rooms. *Nature* 397, 517–520. — the direct-to-reverberant ratio drives
  perceived distance in rooms.
- Klapuri, A. P. (2003). Multiple fundamental frequency estimation based on
  harmonicity and spectral smoothness. *IEEE Trans. Speech and Audio
  Processing* 11(6), 804–816. — harmonic-comb pitch estimation (step 3).
- Bello, J. P., Daudet, L., Abdallah, S., Duxbury, C., Davies, M. &
  Sandler, M. B. (2005). A tutorial on onset detection in music signals.
  *IEEE Trans. Speech and Audio Processing* 13(5), 1035–1047. — onset
  detection (step 6).
- Pätynen, J., Pulkki, V. & Lokki, T. (2008). Anechoic recording system for
  symphony orchestra. *Acta Acustica united with Acustica* 94(6), 856–865. —
  anechoic orchestra recordings, a possible source of controlled test
  material (next steps).

Not from the literature, and so the first things to doubt: the × factor
against the orchestra, the fundamental-vs-harmonics cue, the pitch masking,
the context thresholds, and the score's anchors and weights.

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

## Bass contexts (2026-10-14)

`analyze_contexts` classifies frames and measures depth cues per context:

- **solo**: bass comb ≥ 70 % of 25–1500 Hz (`--solo-dom`).
- **tutti**: basses present (in loud frames 4 of 6 comb peaks are enough,
  `--tutti-min-peaks`), comb share < 50 %, frame in the loudest 30 %.
- **with-sections**: basses present, neither of the above.
- **pizzicato** (overlay): on a 16 ms envelope of harmonics 2–6, rise ≥ 6 dB
  within 64 ms, −1.5 dB by 80 ms (no bowed plateau), never re-rising by 2 dB,
  −4 dB by 320 ms. Synthetic: 15–40 % of plucks found, ≤ 1 bowed note flagged.
  Low recall, high precision. A hall holds a low pizzicato up, so 9 dB/320 ms
  found nothing.

Cues per context: `coh_ratio` = L/R coherence on the bass harmonics (80–700 Hz)
over the orchestra's in frames where the basses rest; `fund_db` = fundamental vs
mean of harmonics 2–6; pizzicato `clarity_db` = first 80 ms vs 96–600 ms.
A first version compared the harmonics with the bins between them in the same
frames. It ranked the labelled recordings backwards (between harmonics sits
mostly the bass's own reverb) and was dropped.

### Calibration on the labelled recordings

| Recording (label) | score | solo ×coh | solo fund | with ×coh | tutti ×coh | pizz clarity |
|---|---|---|---|---|---|---|
| Nelsons/BSO 2+12 (back) | 🟢 8.7 | 1.34 | +9.9 | 1.17 | 1.46 | −1.9 (1 note) |
| Bychkov Mahler 3 (not back) | 🔴 3.3 | 1.69 | −14.3 | 1.59 | 1.46 | −6.1 |
| Fischer Dvořák 8 (weak depth) | 🔴 3.9 | 2.20 | −11.6 | 1.48 | 1.27 | −3.2 |

(Equal-weight coherence. Energy-weighted, the first version: Nelsons
1.38 / 1.13 / 1.32, Mahler 1.64 / 1.58 / 1.30, Dvořák 2.34 / 1.80 / 1.49.)

Solo coherence and solo fundamental order all three as labelled.
With-sections coherence separates Nelsons from the other two but no longer
ranks Dvořák below Mahler. Tutti coherence and pizzicato clarity do not
separate them (yet).

### Unlabelled: Muti/CSO Sym 13, Wigglesworth/NRPO (BIS) Sym 1, 11, 15

| Recording | score | solo s | solo ×coh | solo fund | with s | with ×coh | v1.1 delay |
|---|---|---|---|---|---|---|---|
| Muti/CSO 13 (5 mvts) | 🟢 8.5 | 306 | 1.22 | +2.9 | 326 | 1.00 | I: +5.8 ms "double arrival" |
| Wigglesworth 11 | 🟢 7.4 | 123 | 1.16 | −3.8 | 307 | 1.14 | IV: −5.8 ms (weak) |
| Wigglesworth 1 | 🟢 8.0 | 47 | 1.14 | +2.7 | 97 | 1.22 | IV: −5.9 ms (weak) |
| Wigglesworth 15 | 🟢 8.9 | 88 | 1.04 | +8.2 | 179 | 1.21 | none |

Per movement: Muti 🟢 8.6 / 🟡 6.9 / 🟢 8.8 / 🟢 8.1 / 🟢 8.0; Wigglesworth 11
🟢 7.8 / 🟡 5.4 / 🟢 8.7 / 🟡 5.6; Wigglesworth 1 🟢 7.5 / 🟡 5.2 (8 s) /
🟢 8.7 / 🟡 6.7; Wigglesworth 15 🔴 3.7 (7 s) / 🟢 9.3 / 🟡 4.6 (11 s) / 🟢 9.2.
(Energy-weighted, these four read 7.5 / 5.7 / 7.5 / 6.2: the per-recording
verdicts in the text below were written then.)

The 5.8 ms GCC peak in Muti 13/I is persistent (6 of 13 minutes at z ≥ 5.8,
unmasked, + side only, 9 dB above the orchestra). In the two Wigglesworth
tracks it appears in one minute each. A bass spot ~2 m closer than the main
pair and a reflection off the stage wall ~1 m behind the basses look alike
to GCC; v1 cannot tell them apart.

### Nelsons/BSO album by symphony: is 13 shallower? (2026-10-14)

The user asked whether Sym 13 has less depth than 2, 3 and 12 on the same
album. Only tracks 01, 02 and 07 were complete downloads; the others are
`.flac.part` files with 50–75 % zero-filled blocks. `flac -d -F` recovers
the received frames: about a third of each track, in 4–5 s pieces, with no
clipped or glitched seconds. Contexts and cues are frame-local, so the
pieces still work; the GCC verdict on them is weaker.

| Nelsons/BSO | score | solo s | solo ×coh | solo fund | with s | with ×coh | tutti ×coh |
|---|---|---|---|---|---|---|---|
| Sym 2 (complete) | 🟢 9.2 | 17 | 1.36 | +11.8 | 35 | 1.05 | 1.24 |
| Sym 3 (partial) | 🟢 7.5 (8 s, provisional) | 4 | 1.28 | +4.5 | 4 | 1.36 | 1.39 |
| Sym 12/I (complete) | 🟢 8.6 | 20 | 1.29 | +8.9 | 47 | 1.19 | 1.56 |
| Sym 12/II–IV (partial) | 🟡 6.2 | 13 | 1.45 | +3.6 | 24 | 1.56 | 1.81 |
| Sym 13 (partial, 5 mvts) | 🟢 8.8 | 41 | 0.98 | +4.7 | 48 | 1.18 | 1.39 |

Sym 13 per movement: 🟡 6.9 / 🟡 6.1 (3 s) / 🟢 9.5 / 🟢 9.4 / 🟡 6.6.
Equal-weight values; the energy-weighted ones quoted in the text below
gave the same conclusion.

Sym 13 is not shallower by these cues. Its coherence sits at the "back"
end of the album, and its fundamental (+4.7 dB) is below Sym 2 and 12/I but
far from Mahler/Dvořák (−12 to −14). Its solo notes are E1–C2, below the
bass soloist and chorus. The voices do raise the reference coherence (0.47
against 0.40 in Sym 2 and 12/I), which flatters 13's ratio. Its raw bass
coherence (~0.56 in solo) is still mid-album (0.47–0.70). No movement of 13
gets a delay verdict. On the same work, Nelsons 13 reads slightly further
back than Muti 13 (1.38 / +2.9 / 1.20).

### Equal-weight coherence (2026-10-14)

Question from the user: why does Nelsons Sym 2 Ia, labelled good, score only
6.8 when Sym 12/I scores 9.7?

Coherence was summed over frames with each frame's energy as its weight. In
Sym 2 Ia, a few loud low passages (1:30–5:12, 25–120 Hz holding 83–98 % of
the energy, notes down to A♯0 = 29 Hz, which is the contrabassoon's lowest
note and below a 4-string bass) have coherence 0.8–0.9. That is about 5 s
of the 35 s, but those seconds carry most of the energy. The rest of the
basses sit at 0.50–0.56. The orchestra reference does not depend on level
(0.37–0.42 in every loudness band), so loudness alone is not the cause.
Those passages are pitched (comb 8–16 dB above the bins between harmonics,
like bowed basses), so they are not drums. Whether they are basses,
contrabassoon or tuba is for the ear: listen at 1:28–1:35, 3:17–3:23,
4:35–4:43 and 5:08–5:13.

Fix (`_equal_weight`): each frame is scaled to unit power in 80–700 Hz before
summing, for the bass cue and the reference alike. Effect:

| | before | after |
|---|---|---|
| Nelsons Sym 2 Ia | 🟡 6.8 | 🟢 8.6 |
| Nelsons Sym 12/I | 🟢 9.7 | 🟢 8.6 |
| Mahler 3 / Bychkov | 🔴 3.5 | 🔴 3.3 |
| Dvořák 8 / Fischer | 🔴 2.2 | 🔴 3.9 |

The two labelled Nelsons tracks now agree, and the labelled order holds.
The score anchors were not moved.

The user's observation, in one recording: pizzicato often sounds prominent,
"in the speaker", while tuttis with the basses at the back sound deeper. So
depth can differ by context within one recording, and one number per
recording hides that. Per-context scores, and feedback per context, are the
natural next step, best collected live on the box (a Start / Stop section sampler with feedback: proposed, not built yet).

### Pizzicato above C2 (2026-10-14, detector 1.3)

The user's reference track, Mahler 2/II (Rattle, Qobuz album of 2011):
the opening pizzicato is prominent and, to the user, placed wrong. The
detector found 3 plucks in the whole movement and none where the user hears
them, about 20 s in.

What the score says (UE 1910, p. 57): strings only. From bar 5 the
**double basses alone play pizz., p**, while the divided cellos play arco
(sempre p, stacc. from bar 5), the second cellos doubling the bass line an
octave higher. From bar 13 the second cellos are also pizz. on the same line.
At rehearsal 2 the second cellos are marked "p (aber deutlich)"; the basses
rest three bars and return p.

Why it was missed:

1. The plucks around 20 s sound on E♭2 (78.8 Hz), F2 and D2 (bars 13–20:
   written E♭3 etc., sounding an octave lower), above the C2 (65.4 Hz)
   ceiling every context used. Below the ceiling they are clean combs:
   83–91 % of the low band on the comb, 5–6 harmonic peaks, rising 13–21 dB
   and falling 11–14 dB in 320 ms.
2. In bars 5–12 (A♭1, 52 Hz) the fundamental lies 7–11 dB under the hall's
   20–33 Hz rumble, and the second harmonic coincides with the second cellos'
   arco line: frames there fail the 5-of-6-peaks presence test. A looser
   rule (fundamental a peak + 3 harmonics, below B1) was tried: it finds
   2.5× more bass on this track and adds little on cello-only material, but
   cannot help where the fundamental is under the rumble, and it changes
   every score (Mahler 3 and Dvořák 8 move from red to yellow). Kept out
   for now.
3. A single pluck (0.4 s) never reached the 0.8 s a passage needed for a
   score of its own: pizzicato was always grey.

Fix (1.3): pizzicato is searched on its own comb up to `--pizz-fmax` 100 Hz
(G2), and a pluck needs 5 frames (0.3 s) for a score. Bowed contexts and the
track score keep the C2 ceiling, so the calibration does not move (Nelsons
8.6, Mahler 3 3.3, Dvořák 8 3.9). Above C2 a plucked cello line can be
counted (synthetic plucked cellos C♯2–G2: 15 notes in 6 min; G♯2 and above:
none); the card says so.

Result on the reference track: 21 plucks, among them 12.4, 19.1, 22.9 and
33.8 s, each **red** (×coh 2.1–3.0 against the orchestra at rest), while
the bowed bass reads ×1.13. The plucks reach the microphones far more
directly than the rest of the orchestra: consistent with the user's ear and
with a close pickup on the basses, not yet a proof of one. Synthetic recall
is unchanged (pizz 4–11 of 17–27 found, ≤ 1 bowed note flagged).

### Matched reference (2026-10-14, detector 1.4)

The user asked how the opening plucks of Mahler 2/II had come out red. They
had not earned it: coherence measured over a few frames reads high whatever
the source. On that track the orchestra at rest reads 0.27 over all its
8208 frames, but ×2.47 that over runs of 6 consecutive frames (one pluck),
×2.18 over 12 and ×1.71 over 50; bowed bass ×1.26 over all its frames and
×2.65 over 6-frame runs. Every short passage, and every context with little
material, was judged against the long-run value and leaned towards red.

Fix (`MatchedReference`): the orchestra is measured over frames shaped like
the ones it is compared with, i.e. the same lengths of runs of consecutive
frames, drawn at random from where the basses rest (8 draws, median). The
pizzicato of Mahler 2/II then reads ×1.13 (bowed ×0.90) instead of ×1.66 and
×2.1–3.4 per pluck.

| | 1.3 | 1.4 | bowed ×coh 1.3 → 1.4 |
|---|---|---|---|
| Nelsons 2 + 12/I (back) | 🟢 8.7 | 🟢 9.2 | 1.23 → 1.14 |
| Mahler 3 / Bychkov (not back) | 🔴 3.3 | 🟡 4.8 | 1.61 → 1.35 |
| Dvořák 8 / Fischer (weak) | 🔴 3.9 | 🟡 5.7 | 1.59 → 1.25 |
| Sibelius 2/I / Minnesota (praised) | 🔴 3.8 | 🟡 6.0 | – → 1.13 |
| Mahler 2/II / Rattle | 🟡 6.6 | 🟢 8.3 | 1.31 → 0.98 |

What it shows: much of the coherence cue's earlier separation was the bias.
The labelled order holds, but the margins are small, and two listening
observations so far concern **position**, not depth, and were confirmed by
level balance instead:

- Mahler 2/II, Rattle: the plucks move right as the second cellos join
  (bars 5–12, basses alone: 0.0 dB; from bar 13: +1.6 to +5.7 dB, together
  +2.6 dB) while the bowed basses sit at 0.0 dB.
- Sibelius 2/I, Minnesota: in 110–260 Hz the plucks at 1:56–2:08 lean left
  by 2–3.7 dB; the loudest bowed passages sit at −2.6…+0.4 dB, with right
  leans only below 110 Hz in places (+1.4…+3.8 dB at 6:34–6:38, 8:44). Inter-
  channel delays are 0 throughout: a coincident pair or panned spots.

Not pursued: a pizzicato detector for fast plucks (Sibelius 2/I from 2:00,
notes 88–160 ms apart, which the 256 ms frames and the 320 ms decay test
cannot see). Finding more plucks does not help while the cues do not track
the ear.
