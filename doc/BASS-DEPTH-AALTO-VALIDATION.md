# Corrected Aalto validation — 2026-10-15

Detector 1.6 and renderer 2, with unchanged detector defaults and score
anchors. This report supersedes the causal interpretation of the historical
renderer-1 experiment. Its obsolete tables have been removed from the current
guide; Git history retains the development record. For the dataset background
and its role in development/calibration, see the [Aalto chapter](BASS-DEPTH-DETECTOR.md#aalto-validation-what-it-is-and-how-it-shapes-the-tool).

## Materials and design

Original MP3 instrument stems were downloaded from the Aalto Media Technology
host: [Beethoven](https://mediatech.aalto.fi/images/research/virtualacoustics/recordings/beethoven_mp3.zip),
[Bruckner](https://mediatech.aalto.fi/images/research/virtualacoustics/recordings/bruckner_mp3.zip),
and [Mahler](https://mediatech.aalto.fi/images/research/virtualacoustics/recordings/mahler_mp3.zip).
All archives passed ZIP integrity checks. The unchanged loader selects
21, 37 and 39 mic-6 files, respectively, including one, two and one
double-bass files. The renderer uses each selected recording once, without
multiplying players, and uses full excerpt durations.

**Loader coverage limitation:** Bruckner contains 46 mic-6 files, but the
current filename parser accepts only optional `a`/`b` suffixes. It omits
`vl1c`–`vl1h`, `vl2c`–`vl2d`, and `vlac` (nine string recordings). Non-frontal
microphones are also excluded by design. This run audits the existing loader
and detector as implemented; it is not a test of a sum of every distributed
Bruckner track. The omitted files do not include basses, but changing other
strings can affect mixture masking and the reference. Their role must be
resolved before claiming full-corpus coverage. Input hashes enumerate exactly
what was used; the loader was not silently changed during validation.

These are **real performances in simulated acoustics**, not recordings of
players physically moved to measured positions. Geometry is known within the
simulator. The experiments do not provide perceived-depth listening labels.

- Distance: three pieces × three arrays (AB, ORTF, XY) × four stage positions
  × three paired seeds = 108 scenes.
- Hall and spot controls add 90 unique scenes: ORTF with RT 1.4/2.6 s at two
  positions; aligned/unaligned bass spots at −6 dB in all three arrays.
  The RT 1.9 s and no-spot controls come from the distance grid.
- Radial diagnostic: 108 isolated-bass scenes, with fixed bearings and mean
  source ranges 8/10/12/14 m. Scaling source rays preserves each player's
  bearing; source heights and section dimensions change too. This is a diagnostic, not a model
  of an orchestra changing seating. In radial JSON, `truth.bass_x_m` is the
  renderer's nominal section-centre field and is not used; distance and
  DRR are computed from the transformed player positions.
- Annotation check: the three corresponding denoised bass stems and dry
  orchestra sums from [PHENICX-Anechoic](https://zenodo.org/records/840025).
  The archive matched MD5 `7fec47568263476ecac0103aef608629`. Its WAV files
  are processed derivatives, not evidence of original lossless provenance.
- Stronger-spot sensitivity: ORTF, seed 1, all three pieces, aligned and
  unaligned spots at −6/0/+6/+12 dB. These 24 evaluations include six −6 dB
  overlaps with the primary grid, leaving 18 additional conditions. They
  test sensitivity to gain without changing detection thresholds. Spot dB
  is the renderer's ratio of isolated-bass energy in the added spot to
  isolated-bass energy in the stereo main pickup, before panning/mixing; it
  is not a level relative to the entire orchestra. The scenes use the unmodified full renderer at every gain/delay. The summarizer
  checks the six actual Aalto −6 dB scalar results against the primary grid.
  An earlier additive-remix shortcut matched synthetic waveforms to 1e-12
  but failed this detector-output equivalence check on real excerpts, so
  those results were discarded and all 24 scenes rendered directly.

Seeds vary simulated player placement/room realizations while retaining the
recordings. They are not independent musical performances. Within each seed,
distance comparisons preserve the other players and dry performances. Source
hashes, algorithm hashes and library versions accompany every primary result.

## Distance results

The six-metre stage translation gives actual mean bass-to-pair ranges of
7.77–13.41 m across the seeded layouts. Each group holds piece, array, hall
and seed fixed. There are 27 possible four-distance groups.

| Cue | Expected sign vs distance | Available scenes /108 | Complete groups /27 | Mean rho | Strictly monotonic, expected direction |
|---|---:|---:|---:|---:|---:|
| Depth score | + | 79 | 19 | −0.41 | 0/19 |
| Track coherence ratio | − | 79 | 19 | −0.16 | 2/19 |
| Solo fundamental balance | + | 36 | 9 | −0.67 | 0/9 |
| Pizzicato clarity | − | 94 | 21 | +0.04 | 0/21 |
| Pizzicato coherence ratio | − | 87 | 17 | −0.31 | 3/17 |
| Fixed frames, isolated-bass coherence | − | 72 | 18 | +0.21 | 0/18 |
| Fixed frames, mixture coherence | − | 72 | 18 | +0.21 | 0/18 |
| Fixed frames, isolated-bass fundamental | + | 72 | 18 | −0.27 | 0/18 |

Mean rho excludes constant groups, for which rank correlation is undefined.
For the score, eight of the 19 complete groups are constant; its mean is
therefore over 11 varying groups. All other displayed complete groups vary.
Strict monotonicity is a demanding diagnostic, not the sole criterion for
reliability: the sign, magnitude, coverage and paired controls matter too.

In Beethoven all nine score correlations have the opposite sign to the
intended interpretation (mean −0.64). In Mahler seven of nine complete
score groups are constant and the other two have positive correlations.
Only one Bruckner group has a score at all four positions, and it is constant.

There is some directional information: the track coherence ratio has the
expected sign in 10/19 complete groups, and pizzicato coherence in 12/17.
Those occasional successes should not be discarded. They do not provide a
consistent distance scale across recordings and arrays. The coherence
contribution clips at its maximum in **57 of the 79 scored scenes**; this
is not the same as saying the whole score is always ten.

The fixed-source measurements remove mixture-dependent frame/pitch selection
from the comparison. Their failure to give a consistent distance trend means
the problem is not explained solely by missing or misidentified bass in the
orchestra mix. The radial experiment below also removes changing bearing.

## Hall and primary spot controls

Hall comparisons hold ORTF, piece and seed fixed, require both stage positions
in all three RT settings (1.4/1.9/2.6 s), and compare mean cue ranges. The
position span here is **4 m of stage translation**, unlike the 6 m distance
grid; neither is exactly the radial source-to-pair distance change.

| Cue | Complete matched groups /9 | Hall spread / position spread |
|---|---:|---:|
| Score | 6 | 0.65 |
| Track coherence ratio | 6 | 1.42 |
| Solo fundamental | 3 | 0.61 |
| Pizzicato clarity | 7 | 1.50 |

Room decay changes can therefore move coherence ratio and clarity more than
the tested position change. A spread ratio alone gives neither the direction
of a cue nor an uncertainty estimate; incomplete groups are excluded explicitly.

At −6 dB there are 27 paired cases for each spot condition. No positive spot
verdict was produced: no-spot controls gave 25 `insufficient` and two `none`;
aligned and unaligned spots each gave 24 `insufficient` and three `none`.
This is mostly abstention, not demonstrated perfect specificity. For the
unaligned target cases, all three non-abstaining results were misses.

| Added spot | Cue | Available pairs /27 | Expected change | Pairs in expected direction | Median change |
|---|---|---:|---|---:|---:|
| Aligned | Score | 18 | decrease | 1 | 0.00 |
| Unaligned | Score | 18 | decrease | 5 | 0.00 |
| Aligned | Coherence ratio | 18 | increase | 7 | −0.0066 |
| Unaligned | Coherence ratio | 18 | increase | 13 | +0.0112 |
| Aligned | Solo fundamental | 9 | decrease | 1 | +4.90 dB |
| Unaligned | Solo fundamental | 8 | decrease | 4 | −0.14 dB |

Nine score pairs in each alignment are unchanged. The unaligned coherence
ratio often moves in the expected direction; this positive result matters,
but it does not translate into consistent score behavior or spot detection.

## Stronger spots: sensitivity is piece-dependent

ORTF, seed 1, unchanged thresholds. Scores at each spot gain:

| Piece | Spot | −6 dB | 0 dB | +6 dB | +12 dB |
|---|---|---:|---:|---:|---:|
| Beethoven | unaligned | 6.88 | 6.46 | 7.70 | 7.97 |
| Beethoven | aligned | 7.29 | 8.14 | 8.47 | 8.87 |
| Bruckner | unaligned | absent | absent | absent | absent |
| Bruckner | aligned | absent | absent | absent | absent |
| Mahler | unaligned | 10.00 | 10.00 | 10.00 | 10.00 |
| Mahler | aligned | 10.00 | 10.00 | 7.37 | 7.21 |

The stronger aligned spot produces the intended lower score in Mahler,
but the opposite trend in Beethoven. Bruckner remains unscored. This is
evidence that a cue can respond to a spot in some material, not that the
score measures depth consistently. The −6 dB failures alone would not have
established this gain-dependent behavior.

Across these 24 evaluations there are 20 `insufficient` and four `none`
spot verdicts, with no positive detection. The four `none` cases are
Beethoven at +6/+12 dB, for both alignments; only the unaligned cases are
the check's intended target. Most conditions abstain, so this does not
establish false-positive performance.

## Annotation check: selection is also a limitation

PHENICX note labels are independent of this detector. Recall and pitch
accuracy below use only FFT windows wholly inside annotated notes in the
detector's 29–66 Hz range. Correct pitch means within half a semitone.
Detection uses the union of solo/with-sections/tutti context masks; the
pizzicato overlay is not evaluated. This does not evaluate every bass note
or validate the semantic context names.

| Piece | Eligible frames (64 ms hop) | Isolated bass: detected with correct pitch | Dry mix: detected with correct pitch | Dry mix: detected, regardless of pitch |
|---|---:|---:|---:|---:|
| Beethoven | 412 | 65.8% | 25.0% | 38.1% |
| Bruckner | 142 | 14.1% | 12.7% | 12.7% |
| Mahler | 432 | 44.7% | 17.8% | 18.5% |

Detection while any annotated bass note is active has 80.8%, 100% and 100%
precision in the three dry mixes. This permissive activity metric does not
prove that the selected energy belongs to bass: other instruments can play
at the same time, and much of each excerpt contains bass activity. The
stricter pitch-qualified recalls expose the missing material.

The WAV sum is a diagnostic based on the distributed PHENICX tracks; it is
not substituted for the original MP3 orchestra in the primary scene grid.
Tracks are summed without gain adjustment or time shifts, padding shorter
ends with zeros (the Beethoven bass file ends earlier than the other tracks).
Overlapping FFT frames are not independent observations.

## Radial control: removing bearing changes does not establish a cue

Stable low-bass source selection supplies 175 frames (11.2 s) in Beethoven,
6 (0.384 s) in Bruckner and 142 (9.088 s) in Mahler. Bruckner is excluded
from radial coherence/fundamental conclusions because it has fewer than 20
selected frames. Mahler also has less than ten seconds of material. The
source frame/pitch selection is fixed across the scenes for each piece.

The renderer's labelled DRR decreases monotonically with range in all 27
radial groups. The usable detector cues do not follow it consistently:

| Isolated-bass cue | Expected sign vs distance | Mean Spearman rho (18 usable groups) | Strictly monotonic in expected direction |
|---|---:|---:|---:|
| Raw harmonic coherence | negative | +0.20 | 0/18 |
| Mono fundamental balance | positive | −0.41 | 0/18 |
| Channel-power fundamental balance | positive | −0.40 | 0/18 |

Beethoven's fundamental balance has rho −0.8 in every array/seed group,
opposite the score's assumed direction. Mahler has mixed/weak relationships.
The lack of consistency therefore survives removal of mixture detection,
other instruments and changing bearing, within this particular simulator
and the limited selected material.

The supplementary fundamental alternative averages left/right channel
powers before computing harmonic ratios. This removes mono-sum cancellation
in the phase-only regression control; it does not remove instrument-spectrum
or room-transfer effects. It is a diagnostic, not a production change.

## What this changes about the tool

The corrected tests reach the same practical conclusion as the audit, now
with direct evidence from the real Aalto performances: **the current score
should not be treated as a reliable bass-depth scale**. No tested parameter
provided consistent distance behavior across these pieces and arrays.
Some coherence and spot responses have the expected sign, so the result is
not a claim that the cues never react to acoustics. Their response is too
condition-dependent, incomplete or saturated to justify the present mapping.

The reference-sampling and renderer repairs remain worthwhile. They remove
specific biases and make the experiment interpretable; they do not make the
heuristics identify depth. A channel-power fundamental alternative removes
one confound but fails the radial check, while independent note annotations
show that selection also needs work. Retuning the same two score anchors
would not address these findings.

No detector defaults, anchors, runtime UI or services were changed by this
validation. The documentation withdraws unsupported depth interpretations;
any future product change to hide or replace the score is separate. The
[Aalto chapter](BASS-DEPTH-DETECTOR.md#aalto-validation-what-it-is-and-how-it-shapes-the-tool)
describes how to develop and calibrate a replacement without confusing
simulator geometry, musical-source accuracy and perceptual labels.

## Scope of the conclusion

The simulator is one shoebox with point sources, frequency-independent early
reflection absorption, a statistical late tail, and very small recorded
sections. Its DRR labels are mean per-player broadband RIR energy bookkeeping,
not measured section DRR. The original low-frequency recording limitations
are not compensated. The PHENICX checks additionally involve denoising and
the distributed track arrangement. There are no independent listening labels.

Consequently, these experiments can reject reliability claims for this
heuristic in the tested conditions; they cannot prove that acoustic depth is
unmeasurable or that every possible estimator fails. A response to one
controlled change is not a calibrated depth scale. Nor do low depth accuracy
and useful-looking timelines establish correct instrument identification:
the annotation experiment tests that separately and finds substantial misses.

Balance values remain measurements of the selected frequency bins. Their
attribution exclusively to double basses, especially in mixtures, is not
validated by this experiment.

## Reproduction

Audio remains outside version control under `build/bass-audit/audio`.
The main CLI and supplementary runners produce derived JSON only:

```sh
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
PYTHONPATH=tools python3 -m bassval run \
  --stems build/bass-audit/audio/stems --grid distance --seeds 1,2,3 \
  --section-mode recorded --out build/bass-audit/distance-v2.jsonl
python3 tools/bassval_controls.py --stems build/bass-audit/audio/stems \
  --out build/bass-audit/controls-v2.jsonl
python3 tools/bassval_followup.py radial --stems build/bass-audit/audio/stems \
  --out build/bass-audit/radial-v3.jsonl
python3 tools/bassval_followup.py annotations \
  --phenicx build/bass-audit/audio/phenicx/PHENICX-Anechoic \
  --out build/bass-audit/annotations-v3.jsonl
python3 tools/bassval_spot_levels.py --stems build/bass-audit/audio/stems \
  --out build/bass-audit/spot-levels-v3.jsonl
python3 tools/bassval_summarize.py --runs build/bass-audit \
  --out doc/bass-validation-2026-10-15
```

Use fresh output files for changed inputs/code. Supplementary runners refuse
to overwrite files. The summarizer rejects incomplete/duplicate grids and
incompatible primary provenance. It counts complete four-distance groups,
including constant cues, rather than silently reporting only successful
or varying subsets. The exploratory `report.txt` retains the original
reporter's convention of accepting three available distances; use
`summary.json` for the stricter denominator.

## Evidence and checks

Derived measurements are retained under
[`bass-validation-2026-10-15/`](bass-validation-2026-10-15/):

- [`manifest.json`](bass-validation-2026-10-15/manifest.json): artifact and
  runner hashes, with row counts.
- [`summary.json`](bass-validation-2026-10-15/summary.json): complete-group
  distance/radial statistics, paired hall/spot effects, annotations and gain sweep.
- [`report.txt`](bass-validation-2026-10-15/report.txt): the original exploratory
  reporter, with its less strict available-distance denominator.
- `distance.jsonl.gz`, `controls.jsonl.gz`, `radial.jsonl.gz`,
  `annotations.jsonl.gz`, `spot-levels.jsonl.gz`: individual measurements,
  source hashes and algorithm identities. Read with `gzip -cd FILE` or Python
  `gzip.open(FILE, 'rt')`; no audio or distributed note labels are committed.

The full bass suite passed **66 tests**. New regressions cover radial bearing
and range, the stereo phase confound, annotation denominators, missing/constant
summary groups, and waveform equivalence of the diagnostic spot remix. The
last of these does not imply detector-output equivalence; the published gain
sweep uses full rendering. Targeted tests were rerun after supplementary-tool
changes. The final summarizer checks expected grids, compatible provenance,
fixed source-selection digests and all six overlapping −6 dB scalar baselines.
The stored algorithm hashes were checked against the audited source files.
These are offline tools and documentation; deployment and APK installation
are not needed.
