# Bass depth: algorithms

Detector 1.6 / bassval renderer 2. Implementation reference. Current usage and interpretation are in
[BASS-DEPTH-DETECTOR.md](BASS-DEPTH-DETECTOR.md); this file is the
reference for what the code does. The completed
[Aalto validation](BASS-DEPTH-AALTO-VALIDATION.md) found no reliable depth
scale from these cues in the tested conditions.

| Part | Code | Section |
|---|---|---|
| Detector | `tools/bass_depth.py` | [1](#1-the-detector-toolsbass_depthpy) |
| Live sessions on the box | `omdrc-ctrl/src/bass_live.py` | [2](#2-live-sessions-omdrc-ctrlsrcbass_livepy) |
| Calibration from taps | `tools/bass_depth_calibrate.py` | [3](#3-calibration-from-taps-toolsbass_depth_calibratepy) |
| Validation against known geometry | `tools/bassval/` | [4](#4-validation-against-known-geometry-toolsbassval) |

Conventions throughout: stereo signals are (L, R); "R−L" in dB is positive
when the right channel is louder; a lag is positive when R arrives later.

## 1. The detector (`tools/bass_depth.py`)

### 1.1 Input and framing

`load_audio`: ffmpeg decodes any file to stereo float at **16 kHz** (`SR`);
nothing above 4 kHz is used. Frames are 4096 samples (256 ms, `NFFT`) with a
1024-sample hop (64 ms, `HOP`), Hann window (`spectra`). 3.9 Hz bins resolve
the 30–66 Hz fundamentals of the basses.

### 1.2 Finding the basses: the harmonic comb (`bass_salience`)

A bowed or plucked bass note is a harmonic comb: energy at f0, 2·f0, 3·f0…
For every frame, candidate f0s are tried on a 0.1-semitone grid between
`fmin` and `fmax` (29–66 Hz for bowed contexts, including the cello's lowest
note, C2 = 65.4 Hz). For each candidate:

- **dominance** = energy on its first 6 harmonics (3-bin sums) / energy in
  25–1500 Hz (`SAL_BAND`);
- **peaks** = how many of those 6 harmonics stand 10 dB above a 31-bin
  running median (`prom_db`); there is no separate fundamental-presence gate;
- **odd share**: a subharmonic comb (f0/2 of a real note) catches the even
  half only, so the rank is dominance × min(1, odd share / 0.3) × peaks.

The best candidate gives the frame's f0, dominance and peak count; the
frame's 25–1500 Hz energy also comes out. **Gate**: frames more than 40 dB
(`--gate-db`) below the 99th-percentile energy are ignored.

### 1.3 Contexts (`classify_contexts`)

Per frame, the basses are **playing** when the comb holds ≥ 10 % of
25–1500 Hz (`--present-dom`) and ≥ 5 of 6 harmonics are peaks
(`--min-peaks`), or ≥ 4 in the loudest 30 % of frames
(`--tutti-min-peaks`: in a tutti some harmonics drown). A 3-frame median
removes single-frame flicker. Then:

- **solo**: playing and dominance ≥ 0.7 (`--solo-dom`);
- **tutti**: playing, not solo, dominance < 0.5, in the loudest 30 %;
- **with sections**: playing, neither;
- **pizzicato** (an overlay, on its own comb searched up to `--pizz-fmax`
  100 Hz, G2, because plucked lines sit higher; above C2 it can include
  cellos): on a 16 ms grid (`comb_envelope`: harmonics 2–6 of the frame's
  f0, 64 ms window, only where that comb is playing) an attack is a rise of
  ≥ 6 dB (`--pizz-rise`) within 64 ms, followed at once by a decay (−1.5 dB
  by 80 ms: no bowed plateau), never rising again by 2 dB, and ≥ 4 dB
  (`--pizz-drop`) down by 320 ms. Known limit: plucks closer than ~320 ms
  apart are missed.

### 1.4 Depth cues per context (`context_cues`)

Over the frames of a context or passage:

- **Equal-weight frames** (`_equal_weight`): each frame's spectra are
  divided by the square root of its geometric-mean L/R band power
  (80–700 Hz, `CTX_BAND`), so a few loud seconds cannot outweigh many quiet ones.
- **coh_comb**: weighted magnitude coherence (not magnitude-squared coherence) on the comb bins
  (harmonics ≥ 2 within 80–700 Hz, ±1 bin): Σ|ΣL*·R| / Σ√(ΣL²·ΣR²), summed
  over frames then bins (`_coh`). Estimation follows Carter, Knapp &
  Nuttall (1973), using magnitude rather than its square. Diffuse-field
  coherence depends on frequency, microphone spacing and patterns; it is
  not generally zero. Stable harmonic sources can remain highly coherent
  through reverberation, so this is not a direct depth measurement.
- **coh_ratio (×coh)**: coh_comb divided by a matched reference
  (`MatchedReference`). The reference uses exactly the target's per-frame
  harmonic masks, complete consecutive-run lengths (including short gaps whose FFT windows overlap),
  and frequency weights
  (the target's √(ΣL²·ΣR²)). Eight deterministic draws, median; no frame is
  reused within a draw and runs are never split. Different reference runs
  have a three-frame guard so their FFT windows do not overlap. Queries do
  not depend on earlier queries. If any complete draw cannot fit, the ratio
  is absent; raw coherence, spectrum and balance remain available.
  Reference candidates exclude **all four** detected contexts, including
  pizzicato, and three frames on each side to avoid overlapping windows.
  This controls mask/count bias, not every source-dependent statistical
  bias. Undetected bass or a reverberant tail can still contaminate a
  reference; this is not a calibrated distance or DRR measurement.
- **fund_db**: per frame, the fundamental's level over the mean of
  harmonics 2–6 (mono, maximum within ±1 bin for each harmonic), median over frames. A heuristic affected by register, EQ, room transfer and stereo phase
  cancellation. The assumed positive relationship with distance did not
  hold consistently in corrected validation.
- **balance_db** / **balance_fund_db**: R−L level on the comb harmonics /
  on the fundamental, equal-weight frames. Position, not depth. Level
  ratios do not have the same finite-sample bias as coherence. Below C2,
  cello fundamentals are excluded, but other low instruments and noise can
  still contribute. Harmonics can mix in the cellos. Neither measure alone
  identifies the instrument or establishes its perceived position.
- **clarity_db** (pizzicato, `pizz_clarity`): per isolated pluck, comb
  energy in the first 80 ms over 96–600 ms. This is a truncated musical
  attack/tail heuristic inspired by C80, not ISO 3382-1 clarity measured
  from a room impulse response.

### 1.5 Passages for the timeline (`context_blocks`)

Runs of each context closer than 2 s (`BLOCK_GAP_S`) are merged into
passages. A passage gets its own descriptive cues when it has ≥ 12 frames
(0.8 s, `BLOCK_MIN_FRAMES`), a pizzicato passage ≥ 5 (one pluck,
`PIZZ_BLOCK_MIN_FRAMES`). Only solo and with-sections receive scores,
using their actual context and the same rule as the track; tutti and
pizzicato remain unscored. A missing matching reference also leaves a
passage unscored, without suppressing its balance or spectral cues.

### 1.6 The depth score (`depth_score`)

From solo and with-sections only: seconds-weighted ×coh mapped linearly 1.0 → 10, 2.2 → 0
(`SCORE_COH`), seconds-weighted solo fund_db mapped −15 dB → 0, +10 dB → 10
(`SCORE_FUND`), both clipped; score = ⅔ coherence part + ⅓ fundamental part
(`SCORE_W_FUND`), or the coherence part alone without solo. Bands: ≥ 7
green, 4–7 yellow, < 4 red (`SCORE_BANDS`); under 10 s of coherence material **or of solo material contributing the
fundamental** it is marked provisional. `fund_seconds` exposes that separate
support. All scores carry `experimental: true`; existing anchors are retained
for comparison and are not recalibrated to fit the two recordings. The anchors were set by hand on three recordings: the
score did **not provide reliable distance ordering** in the corrected
[Aalto validation](BASS-DEPTH-AALTO-VALIDATION.md).

### 1.7 The spot-microphone check

An **unaligned** bass spot (mixed without the delay matching its distance)
makes every bass note arrive twice, Δ = 5–40 ms apart. Pipeline (`analyze`):

1. **Bass frames**: dominance ≥ 0.5 (`--dom`) and ≥ 5 peaks, runs ≥ 3
   frames. **Reference frames**: gated, not bass, dominance < 0.25.
2. **Pool per semitone**: the cross-spectra L·R*, |L|², |R|², |M|² (M =
   mid) are summed per note (`accumulate`); a note needs ≥ 4 frames, the
   track ≥ 40 bass frames and ≥ 4 notes, else "insufficient".
3. **GCC** (decides, `gcc_curve`): per note, PHAT-weighted cross-spectrum
   (Knapp & Carter 1976) times its magnitude-squared coherence, in 150–
   2500 Hz (`BASS_BAND`) with raised-cosine edges (`band_taper`), turned
   into a complex lag curve (`analytic_lags`) and corrected for the window's
   own autocorrelation. Source spectra cancel in the phase: what remains is
   the L→R transfer, main peak at the main pair's time difference (searched
   within ±2 ms, `MAIN_ITD_MS`), side peaks at ±Δ.
4. **AC** (corroborates, `ac_curve`): autocorrelation of the whitened mono
   power spectrum (divided by its 200 Hz running mean). Weaker: within a
   note the spectrum exists only on harmonic lines, so Δ is ambiguous
   modulo the period.
5. **Pitch-masked mean** (`group_pitches`, `pitch_masked_mean`): a harmonic
   source repeats every lag feature at multiples of its period T0. Per
   note, lags within 1 ms (`--pitch-tol`, widened by the main time
   difference for GCC) of k·T0 are masked, for the note itself, for other
   bass notes bleeding into its frames (non-shared harmonics within 10 dB,
   `--leak-db`) and for the octave of each. The per-note curves are
   averaged (weights √frames) where unmasked; only a delay fixed by the
   geometry survives. Where fewer notes remain the noise rises: the curve
   is divided by its per-lag noise floor (√Σw²/Σw relative to the unmasked
   average) before peaks are scored.
6. **Peaks** (`lag_peaks`) in 5–60 ms (`--min-lag`, `--max-lag`), lags with
   coverage < 0.3 skipped, scored by robust z ((value − median) / 1.4826
   MAD). A peak counts with z ≥ 5.5 (`--z`), ≥ −20 dB under the main GCC
   peak (−26 dB for AC), and ≥ 6 dB (`--specific-db`) above the orchestra
   reference's GCC at the same lag (`peak_rel_to_ref`): a delay the whole
   orchestra shares is hall geometry (a stage wall), not a bass spot.
7. **Verdict** (`verdict`): a GCC peak -> "likely", "double-arrival" if an
   AC peak (z ≥ 3) agrees within 1.5 ms; an AC peak alone -> "possible";
   else "none". Blind to delay-aligned spots, to recordings with no
   inter-channel delay (coincident pairs, panned spots), and to spots
   ≥ 8 dB under the main pickup.

### 1.8 Versions

`VERSION` names the results; it is bumped whenever results change, so the
boxes re-analyse and calibration compares like with like.

## 2. Live sessions (`omdrc-ctrl/src/bass_live.py`)

- **Sessions**: nothing is fetched or analysed outside a session the
  listener starts (`BassStore.start_session`).
- **Worker** (`BassLive.step`, every 4 s): a new track playing in a session
  is identified (`identify` = the DR log's album and track keys), skipped
  if already analysed by this version, else `analyse`d: local files read in
  place, streams fetched again (`download`, the DR Measure job's), kept on
  the music drive under `save_dir` as `<artist>/<album (year)>/<NN -
  title>.<ext>` (`kept_name`, `sniff_ext`) unless the drive is away
  (`save_status`: the folder must exist and lie on a mount other than /,
  `mount_of`; it is never created). Local tracks already on the same drive
  are not copied (`_keep_local`). The detector runs as a subprocess at
  `nice 19`, one at a time (`run_detector`); results are compacted
  (`compact`).
- **Taps** (`BassLive.tap`): the position heard is MPD's elapsed time minus
  the DRC chain's delay (`heard_position`); the passage under it is the
  longest one containing it, else the latest ending < 3 s before it
  (`block_at`).
- **Sharing**: sessions, taps and analyses (timelines included) are
  exported deterministically (`export_rows`) to `bass/<box>.jsonl` by the
  DR log's git sync (`dr_sync.GitSync` extras). Other boxes' files are read
  from the sync's clone for the card's list (`catalog`, `track_view`);
  this box's analyses can be redone from the kept copy (`reanalyse_kept`).

## 3. Calibration from taps (`tools/bass_depth_calibrate.py`)

Joins each tap with its passage (`block_at`), for one detector version;
verdicts map to forward 0, in between 5, back 10. Reports, per context,
Spearman ρ of the verdict with the passage's score, −×coh and fund_db, the
band agreement (green/yellow/red vs back/between/forward), taps that fell
where no bass was found, per-track mean verdicts against track scores,
and, with ≥ 10 scored taps, a least-squares fit verdict ≈ a + b·×coh +
c·fund as a proposal to review. One listener's taps are data, not
validation.

## 4. Validation against known geometry (`tools/bassval/`)

Recordings are built where the truth is known, and every cue is checked
against it. The audio comes from the Aalto anechoic orchestra recordings
(Pätynen, Pulkki & Lokki 2008; free for academic research; not in the
repository).

### 4.1 Parts and sections (`stems.py`)

Each part is one recorded performance, captured by several microphones;
the frontal one (mic 6) is used. File names are parsed by the longest known
part name (`parse_name`: "vla" is the viola, not "vl" + divisi "a").

The default `--section-mode recorded` uses each supplied part once. It does
not invent independent players from copies. `--section-mode copies` retains
the older section sizes with 5–30 ms fixed offsets and ±1.5 dB
(`section_players`) as an explicitly labelled diagnostic. Independent-part
counts and section mode are saved; reports warn about copies and single
bass performances. A single sustained harmonic source can remain coherent
through reverberation, so neither mode establishes how a real section behaves.

The Aalto source paper reports residual room effects below 100 Hz; the loader
does not compensate them. Source directionality and low-frequency source-room
colour remain limitations of these stimuli.

### 4.2 Geometry (`geometry.py`)

A shoebox hall (default 24 × 40 × 15 m, RT60 per octave 2.4 s at 63 Hz to
1.3 s at 4 kHz, scaled by `hall_with_rt` to a chosen mid-band RT). y runs
from the wall behind the orchestra; looking at the stage from the
audience, +x is left. American seating (`SECTIONS`): first violins left
front, cellos and violas right, basses back right (centre 4.5 m from the
back wall). Players at 1.2 m. The main pair is centred 2 m beyond the stage
edge at 3.5 m height, rigidly tilted 15° down, preserving capsule angles (`main_pair`), in four setups
(`SETUPS`): AB (omnis 60 cm apart), ORTF (cardioids 17 cm, ±55°), XY
(coincident cardioids ±45°), Blumlein (coincident figure-of-eights ±45°).
First-order patterns g(θ) = a + (1 − a)·cos θ (`Mic.gain`). A bass spot is a
cardioid 1.5 m in front of the section at 2.2 m, aimed at it (`spot_mic`).

### 4.3 Room impulse responses (`room.py`)

- **Early part** (`early_response`): image sources (Allen & Berkley 1979)
  of a shoebox: per axis, coordinate (1 − 2q)·x + 2n·L after |2n − q|
  reflections (`_axis_images`, `image_sources`); every image within
  T_MIX = 80 ms of the direct sound contributes β^bounces / r × the mic
  pattern for its direction, β = √(1 − α), α = 0.25, placed at its exact
  delay with a 32-tap Hann-windowed sinc (`_place`).
- **Late part** (`late_tails`): Gaussian noise split into octave bands
  (2nd-order Butterworth, `_band_filters`), each decaying at its band's
  RT60 (e^(−6.91 t / RT)), faded in over 10 ms from T_MIX after the direct
  sound. For a pair, the second tail is mixed from the first and an
  independent one in the frequency domain, Y2 = γ·Y1 + √(1 − |γ|²)·N2, with
  γ(f) the **diffuse-field coherence** of the two capsules
  (`diffuse_coherence`): ∫ g1·g2·e^(−jk u·d) dΩ / √(∫g1² ∫g2²) over 2000
  directions (Fibonacci sphere); for spaced omnis this is sin(kd)/kd (Cook
  et al. 1955), for coincident cardioids at 90° 0.75, for Blumlein 0.
- **Level of the tail** (`tail_energy`): Sabine's diffuse energy 1/r_c²,
  r_c = 0.057·√(V/RT) (`critical_distance`), times the pattern's diffuse
  share (1 omni, ⅓ cardioid and figure-8, `diffuse_energy_factor`), times
  the fraction left after T_MIX, e^(−13.8·T_MIX/RT).
- Checked against theory (`tests/test_bassval.py`): array coherence, an
  ensemble of generated tails, direct timing/level, and per-player energy
  bookkeeping within 2 dB of Sabine. These tests do not establish perceptual
  validity or frequency-specific DRR in the musical mix. Early absorption
  remains frequency independent; the late RT is specified separately.

### 4.4 Rendering (`render.py`)

Stable random streams keyed by seed, section, player and purpose separate
performance, layout, main responses and spot responses. Each tail capsule
also has its own stream. A spot cannot change the main recording; moving
basses cannot change other sections.

Every player is convolved with its responses to the main pair (one shared
tail realisation per player) and summed. With a spot, every player is also
convolved with its response to the spot (bleed included); the spot is
scaled so its basses sit `spot_db` relative to the main pair's basses,
constant-power panned to the basses' direction from the main pair
(`pan_gains`), and either delayed by the extra path to the main pair
(**aligned**) or not (**unaligned**). The output is normalised to −6 dBFS
peak.

**Ground truth** per scene: the basses' mean distance to the main pair, their
mean per-player broadband direct-to-reverberant ratio at the main pair
(from ray/tail energy bookkeeping, not a measurement of the mixed section), their lateral offset, and the spot's true Δ.

### 4.5 Scenes (`scenes.py`)

A scene = piece, main pair, hall RT, bass shift along the stage (negative =
further back), spot (none / aligned / unaligned, level), seed and section mode. Grids vary
one thing around a reference: **distance** (3 setups × 4 bass positions,
−2…+4 m), **spot** (3 setups × 3 spot conditions), **hall** (RT 1.4 / 1.9 /
2.6 s × 2 bass positions), **smoke**.

### 4.6 Evaluation (`evaluate.py`)

The detector runs in-process with its defaults (`detector_cues`). Raw comb
coherence and pizzicato clarity are retained alongside the existing cues.
An additional `fixed.*` diagnostic selects sustained frames and pitches
from the unchanged dry bass performance, then measures the isolated main
bass and the main mixture on those same frames across scenes. It saves a
selection digest. These are source-selected frames, not manually annotated
pitch truth, and measure the main pickup before spot mixing. This separates
automatic mixture selection from cue behaviour.

For each
cue with an expected direction (further back -> score up, ×coh down,
fundamental up), the report gives:

- per group (piece × setup × hall × seed × section mode, 4 distances) Spearman ρ against the true
  distance, its mean, and how many groups are monotonic the expected way
  (ρ = ±1);
- ρ over all scenes against distance and against the true DRR (does the cue
  compare across setups?);
- spot verdicts per condition, and the found delay against the true Δ;
- hall/distance spread only on distance positions shared by every hall
  with that cue available; each seed and section mode is paired separately.

`--seeds 1,2,3` runs paired repeats. Reports deduplicate overlapping grid
controls and reject conflicting results or mixed provenance. Run rows store
code hashes, detector/renderer versions, NumPy/SciPy versions, sample rate,
excerpt length and SHA-256 of the source files. Resume refuses old or changed
provenance rather than silently reusing results. Use a new output file after
changing code, sources or excerpt length. Scene keys preserve fractional
parameters. Repeats do not by themselves supply confidence intervals.

Command line: `cd tools; python3 -m bassval run --stems DIR --grid distance
--out results.jsonl`, `python3 -m bassval report results.jsonl`, and
`python3 -m bassval render … --wav out.wav` to listen to a scene.

### Completed validation and supplementary runners

The [2026-10-15 report](BASS-DEPTH-AALTO-VALIDATION.md) documents the actual
recordings, experiment scope, limitations and results. `bassval_controls.py`
adds the hall/spot grid without duplicating distance controls;
`bassval_followup.py` runs fixed-bearing isolated-bass and independent note
annotation checks; `bassval_spot_levels.py` tests stronger spot gains.
`bassval_summarize.py` requires complete expected grids, preserves missing
and constant-group denominators, checks overlapping spot baselines, and
exports the derived evidence. Its strict four-distance statistics differ
from the exploratory reporter, which accepts three available distances.
