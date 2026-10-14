# Bass depth: algorithms

Every algorithm of the bass depth work, as implemented. The journal of how
it was arrived at, the case studies and the open questions are in
[BASS-DEPTH-DETECTOR.md](BASS-DEPTH-DETECTOR.md); this file is the
reference for what the code does and why.

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
`fmin` and `fmax` (29–66 Hz for bowed contexts: below the cello's lowest
note, C2 = 65.4 Hz). For each candidate:

- **dominance** = energy on its first 6 harmonics (3-bin sums) / energy in
  25–1500 Hz (`SAL_BAND`);
- **peaks** = how many of those 6 harmonics stand 10 dB above a 31-bin
  running median (`prom_db`); with `with_fund=True`, whether harmonic 1
  itself is one of them;
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
  divided by the frame's band power (80–700 Hz, `CTX_BAND`, geometric mean
  of L and R), so a few loud seconds cannot outweigh many quiet ones.
- **coh_comb**: magnitude-squared-style coherence on the comb bins
  (harmonics ≥ 2 within 80–700 Hz, ±1 bin): Σ|ΣL*·R| / Σ√(ΣL²·ΣR²), summed
  over frames then bins (`_coh`). Estimation follows Carter, Knapp &
  Nuttall (1973). A close pickup is coherent between channels; diffuse
  hall sound is not (Cook et al. 1955).
- **coh_ratio (×coh)**: coh_comb divided by the orchestra's coherence where
  the basses rest, measured by a **matched reference** (`MatchedReference`):
  coherence measured over few frames reads high whatever the source, so the
  orchestra is measured over frames of the same shape: for each run of
  consecutive frames in the set compared, a random run of the same length
  (pieces of ≤ 64) from where the basses rest; 8 draws, median. Without
  it, a single pluck read ×2–3 on orchestra noise alone.
- **fund_db**: per frame, the fundamental's level over the mean of
  harmonics 2–6 (mono, ±1 bin), median over frames. A heuristic (distance
  and hall favour the fundamental, a close mic the upper harmonics), register
  and EQ dependent.
- **balance_db** / **balance_fund_db**: R−L level on the comb harmonics /
  on the fundamental, equal-weight frames. Position, not depth. Level
  ratios are not biased by short passages. The fundamental is the basses
  alone below C2 or when cellos double an octave up; the harmonics can mix
  in the cellos.
- **clarity_db** (pizzicato, `pizz_clarity`): per isolated pluck, comb
  energy in the first 80 ms over 96–600 ms: a per-note C80 (ISO 3382-1).

### 1.5 Passages for the timeline (`context_blocks`)

Runs of each context closer than 2 s (`BLOCK_GAP_S`) are merged into
passages. A passage gets its own cues and score when it has ≥ 12 frames
(0.8 s, `BLOCK_MIN_FRAMES`), a pizzicato passage ≥ 5 (one pluck,
`PIZZ_BLOCK_MIN_FRAMES`); shorter ones are listed without (grey).

### 1.6 The depth score (`depth_score`)

From solo and with-sections only (the contexts that ordered the labelled
recordings): seconds-weighted ×coh mapped linearly 1.0 → 10, 2.2 → 0
(`SCORE_COH`), seconds-weighted solo fund_db mapped −15 dB → 0, +10 dB → 10
(`SCORE_FUND`), both clipped; score = ⅔ coherence part + ⅓ fundamental part
(`SCORE_W_FUND`), or the coherence part alone without solo. Bands: ≥ 7
green, 4–7 yellow, < 4 red (`SCORE_BANDS`); under 10 s of material it is
marked provisional. The anchors were set by hand on three recordings: the
score is **unproven** (section 4 is how it gets tested).

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

Each part is one player, recorded anechoically by several microphones; the
frontal one (mic 6) is used. File names are parsed by the longest known
part name (`parse_name`: "vla" is the viola, not "vl" + divisi "a").
A section of N players repeats the recorded parts with 5–30 ms offsets and
±1.5 dB (`section_players`). Copies of one performance are more alike than
different players: a limitation of the material.

### 4.2 Geometry (`geometry.py`)

A shoebox hall (default 24 × 40 × 15 m, RT60 per octave 2.4 s at 63 Hz to
1.3 s at 4 kHz, scaled by `hall_with_rt` to a chosen mid-band RT). y runs
from the wall behind the orchestra; looking at the stage from the
audience, +x is left. American seating (`SECTIONS`): first violins left
front, cellos and violas right, basses back right (centre 4.5 m from the
back wall). Players at 1.2 m. The main pair is centred 2 m beyond the stage
edge at 3.5 m height, tilted 15° down (`main_pair`), in four setups
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
- Checked against theory (`tests/test_bassval.py`): coherences as above,
  direct timing and level, direct-to-reverberant ratio within 2 dB of
  Sabine.

### 4.4 Rendering (`render.py`)

Every player is convolved with its responses to the main pair (one shared
tail realisation per player) and summed. With a spot, every player is also
convolved with its response to the spot (bleed included); the spot is
scaled so its basses sit `spot_db` relative to the main pair's basses,
constant-power panned to the basses' direction from the main pair
(`pan_gains`), and either delayed by the extra path to the main pair
(**aligned**) or not (**unaligned**). The output is normalised to −6 dBFS
peak.

**Ground truth** per scene: the basses' mean distance to the main pair, their
mean direct-to-reverberant ratio at the main pair (direct energy over the
rest of the responses), their lateral offset, and the spot's true Δ.

### 4.5 Scenes (`scenes.py`)

A scene = piece, main pair, hall RT, bass shift along the stage (negative =
further back), spot (none / aligned / unaligned, level), seed. Grids vary
one thing around a reference: **distance** (3 setups × 4 bass positions,
−2…+4 m), **spot** (3 setups × 3 spot conditions), **hall** (RT 1.4 / 1.9 /
2.6 s × 2 bass positions), **smoke**.

### 4.6 Evaluation (`evaluate.py`)

The detector runs in-process with its defaults (`detector_cues`). For each
cue with an expected direction (further back -> score up, ×coh down,
fundamental up), the report gives:

- per group (piece × setup × hall, 4 distances) Spearman ρ against the true
  distance, its mean, and how many groups are monotonic the expected way
  (ρ = ±1);
- ρ over all scenes against distance and against the true DRR (does the cue
  compare across setups?);
- spot verdicts per condition, and the found delay against the true Δ;
- the spread a change of hall causes over the spread a change of distance
  causes, per cue.

Command line: `cd tools; python3 -m bassval run --stems DIR --grid distance
--out results.jsonl`, `python3 -m bassval report results.jsonl`, and
`python3 -m bassval render … --wav out.wav` to listen to a scene.
