# Bass depth detector (`tools/bass_depth.py`)

Current implementation: **detector 1.6 / bassval renderer 2**.
The detector reports candidate low-bass passages, stereo/spectral measurements,
an experimental 0–10 depth score, and a possible unaligned-spot verdict.

The [corrected Aalto validation](BASS-DEPTH-AALTO-VALIDATION.md) is complete.
**The current score is not a reliable depth measure in the tested conditions.**
It was available in 79/108 distance scenes; none of the 19 complete
four-distance groups ranked every position in the expected order. Beethoven
scores consistently trended in the opposite direction; many Mahler scores
were constant. Some coherence and spot responses had the expected direction,
but did not generalize across pieces and arrays. These are real performances
rendered in simulated acoustics, without independent listening labels.

The annotation check also found substantial misses and pitch errors. Contexts
and timelines are detector hypotheses, not validated double-bass activity.
Balance describes the selected bins; its attribution exclusively to basses
in a mixture is not established. More detected seconds improve sampling but
do not establish correct instrument identification or depth accuracy.

The previous scoreboards, renderer-1 tables and their causal explanations
have been removed from this guide because they do not describe valid current
validation. Git history preserves the development record. The current
[algorithm reference](BASS-DEPTH-ALGORITHMS.md),
[implementation audit](BASS-DEPTH-AUDIT-FIXES.md), and
[remaining estimator limitations](BASS-DEPTH-FOLLOWUP-AUDIT.md) provide details.

## How to run

```sh
python3 tools/bass_depth.py track.flac --plot DIR --json out.json
python3 tools/bass_depth.py track.flac --segments 4:12-4:30,7:05-7:40
python3 tools/bass_depth.py --selftest
```

Requires NumPy, SciPy and ffmpeg; matplotlib is needed only for plots.
ffmpeg decodes to 16 kHz stereo. JSON stores the experimental score under
`depth`. Passing synthetic selftests checks those constructed cases; it does
not validate depth ranking on orchestra recordings.

## Reading the measurements

- **Contexts:** `solo`, `with-sections` and `tutti` use harmonic energy,
  peak and loudness gates in the 29–66 Hz candidate fundamental range.
  `pizzicato` is an onset/decay overlay. Other low instruments, overlapping
  harmonics and noise can satisfy these gates.
- **Coherence:** consistency of the interchannel relationship over selected
  frames and harmonic bins. A stable harmonic source can remain coherent
  through reverberation; coherence is not distance or diffuseness itself.
- **Coherence ratio:** selected-context coherence divided by a matched
  reference from other frames. Detector 1.6 matches frequency masks,
  weights and sampling geometry, and abstains without a usable reference.
  Different source spectra and temporal statistics remain a confound.
  A ratio near one does not establish a shared acoustic depth.
- **Fundamental balance:** fundamental power relative to harmonics 2–6,
  measured from the mono sum. Timbre, register, EQ, room transfer and stereo
  phase cancellation affect it. Positive does not establish distance.
- **Pizzicato clarity:** early attack energy relative to a short tail.
  This musical heuristic is not an impulse-response C80 measurement and
  did not provide a consistent distance ordering in the corrected tests.
- **Stereo balance:** right-minus-left level on selected harmonics and,
  separately, on the fundamental. Positive means more energy on the right.
  This measures the recording, not the listening room or a guaranteed
  instrument position.

## Experimental score and spot verdict

The score maps solo/with-sections coherence ratio from 1.0 → 10 to 2.2 → 0,
and solo fundamental balance from −15 dB → 0 to +10 dB → 10, clipping both.
When both contribute, their weights are ⅔ and ⅓; without a usable solo
fundamental, coherence alone contributes. Missing matched coherence leaves
no score. Under ten seconds of coherence support or of contributing solo
fundamental support makes the score provisional; `fund_seconds` exposes
the latter. All scores carry `experimental: true`.

The existing green/yellow/red bands and back/forward text are heuristic UI
labels, not validated depth categories. Anchors were chosen on three
recordings and were held unchanged for validation. Retuning them on the
same recordings or on visible-score listening taps would not validate them.

The spot check searches for repeated arrivals using pitch-masked GCC and
mono autocorrelation. `likely`, `double-arrival` and `possible` are detector
hypotheses; `insufficient` is an abstention. `none` means no qualifying
pattern was found, not that no spot microphone exists. Delay-aligned spots
are outside this check's intended target. Corrected Aalto controls did not
establish reliable unaligned-spot detection; see the report for coverage and
gain sensitivity rather than extrapolating synthetic success rates.

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

## Aalto validation: what it is and how it shapes the tool

### What Aalto supplies

Here **Aalto** means the anechoic orchestra recording project associated
with Aalto University and the work of Jukka Pätynen, Ville Pulkki and Tapio
Lokki, documented in *Anechoic recording system for symphony orchestra*
(2008). Musicians recorded instrument parts separately in an anechoic chamber,
following a conductor video and a piano guide so their performances could
be combined. This supplies separate musical sources for acoustic experiments,
rather than a finished commercial stereo recording. The published collection
contains Mozart, Beethoven, Bruckner and Mahler excerpts; our tests use the
last three. The distributed original downloads are MP3, principally frontal
microphone 6. See the [Aalto project description](https://research.cs.aalto.fi/acoustics/virtual-acoustics/research/acoustic-measurement-and-analysis/85-anechoic-recordings.html).

“Anechoic” does not guarantee an uncoloured bass source: the recording
chamber was designed to be anechoic above about 100 Hz, while this detector
searches fundamentals below that. The source recordings therefore impose
limits on low-frequency interpretation. See the authors' [recording-system
paper](https://users.aalto.fi/~ktlokki/Publs/001538.pdf).

[PHENICX-Anechoic](https://zenodo.org/records/840025) adds denoised derivatives
and aligned note annotations. We use its text note labels to test bass
selection and pitch separately. Those WAVs are processed material, not an
original lossless replacement. Its supplied Roomsim responses are not the
responses used by our renderer. The original audio and annotations remain
local; the repository stores derived measurements and provenance.

### Turning recordings into controlled tests

A commercial mix does not reveal the original bass distance, microphone
geometry or spot gain. With separate Aalto parts, `tools/bassval` can hold the
performance fixed and deliberately change one acoustic condition. Each part
is convolved with simulated source-to-microphone impulse responses, then
summed into a stereo scene. Independent random streams preserve unrelated
players and main responses in paired comparisons. Recorded parts are used
once each, rather than copied into an artificial large section. The existing
loader omits nine Bruckner string files with unsupported filename suffixes;
the report lists this coverage limitation and the exact source counts.

The corrected experiment asks separate questions:

| Control | What it tests | What it cannot establish alone |
|---|---|---|
| Four bass positions, three arrays, paired seeds | Whether a cue orders known simulated distances | Perceived depth or generalization to real halls |
| Changed RT at matched positions | Sensitivity to room decay relative to position | Invariance to all room acoustics |
| Aligned/unaligned spots and gain sweep | Response to a known added microphone contribution | Presence or absence of spots in an unknown recording |
| Fixed-source frames, isolated bass and mixture | Whether mixture selection explains cue failures | Accuracy of the detector's own source pitch estimates |
| Fixed-bearing radial sweep | Whether changing bearing explains distance results | A physically realistic reseating of the orchestra |
| PHENICX note labels | In-range activity and pitch coverage | Articulation accuracy or perceived depth |

The [dedicated validation report](BASS-DEPTH-AALTO-VALIDATION.md) contains the
complete design, downloads, results, limitations and commands. In particular,
198 unique primary scenes cover distance/hall/spot conditions; the radial,
annotation and stronger-spot experiments answer supplementary questions.
Neither a seed nor an overlapping FFT frame is a new musical performance.

### How this develops and calibrates the tool

Aalto is a test bench for deciding which measurements deserve a role in the
tool. A useful candidate should respond in the intended direction to a
controlled change, retain adequate coverage, and survive changes of piece,
array and room. Negative controls should expose confounds before we give
measurements a depth label. Annotation failures direct work toward source
selection; fixed-source failures direct work toward the cue itself. The
current results show both problems, so merely adjusting score anchors is
not an adequate repair.

**This run validates existing behavior; it does not fit new parameters.**
Detector thresholds, score anchors and weights were frozen throughout.
The earlier score anchors came from three listening examples, not from
Aalto. The implementation audit repaired experimental controls and reference
sampling; the corrected recordings then tested those repairs without moving
the target to make the result look better.

For a future replacement, calibration should proceed in this order:

1. Define the target explicitly: source detection, simulated distance/DRR,
   or perceived depth. They require different labels and success criteria.
2. Separate development material from held-out performances and acoustics
   before selecting features or tuning thresholds. Splitting only seeds
   leaks the same musical performance across both sets. The three pieces
   here are already inspected development evidence, not a fresh test set.
3. Tune only on the development set. Track missing estimates, pitch errors,
   saturation, ranking and false alarms/abstentions, not just mean correlation.
4. Freeze the candidate and evaluate untouched material and measured room
   responses. Reject mappings whose direction changes across conditions.
5. Calibrate any perceptual labels against independent blind listening,
   hiding score/colour and recording reputation. Test those labels on a
   separate listening set before enabling a claimed depth scale.

This workflow uses Aalto to challenge and improve the tool, rather than to
assign unquestioned “back” labels to rendered files. The present evidence
supports withdrawing confidence in the current score's depth interpretation;
it does not demonstrate that no better estimator can work.

## Further work

The evidence supports retiring the current depth interpretation rather than
further tuning its anchors. This validation changes no production behavior.
Any replacement needs held-out performances, varied or measured acoustics,
reliable source selection and independent blind listening judgments.
Direct-to-reverberant or attack-based alternatives remain research proposals,
not demonstrated improvements. Listening sessions should hide existing scores
when collecting independent judgments; reputation is not a depth label.
