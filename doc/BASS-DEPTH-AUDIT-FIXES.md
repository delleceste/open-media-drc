# Bass depth audit fixes — detector 1.6 / renderer 2

The audit found implementation problems in the detector and in the experiment
used to evaluate it. The changes below repair those problems; they do not
establish that coherence or fundamental balance estimates perceived depth.
The score anchors have not been retuned to fit recordings or their reputations.

## Detector

- Reference spectra now use the target's per-frame harmonic masks and its
  frequency weights. Matching only the number of frames left a pitch-dependent
  bias because each comb bin received fewer observations than its reference.
- Reference draws preserve complete runs and short gaps between overlapping FFT
  windows. Each frame is used at most once per draw; long runs are not split to
  make a reference fit. Queries are deterministic and independent of call order.
  If a complete match cannot be made, the ratio is absent rather than estimated
  from an unmatched sample. This is conservative: some tracks/passages will
  have fewer scores than before.
- All four detected contexts, including pizzicato, are excluded from reference
  candidates. A three-frame guard excludes windows overlapping detected bass.
  This does not guarantee that undetected notes or long bass tails are absent.
- Passage scores use the same context rules as track scores. Solo uses coherence
  and its fundamental; with-sections uses coherence. Tutti and pizzicato keep
  descriptive measurements but receive no depth score. Balance and spectral
  measurements remain available without a matching reference.
- Scores expose `fund_seconds` and are provisional when either contributing
  cue has less than ten seconds of material. All scores are explicitly
  experimental in JSON, terminal output and the card.

The matched-reference estimator still compares sources with different spectra
and temporal statistics. It is not a calibrated DRR estimator. In particular,
stable harmonic sources can remain highly coherent through reverberation.

## Simulator and evaluation

- Independent deterministic random streams separate performance, player layout,
  main responses and spot responses, keyed by section/player/purpose. Tail
  capsules also use separate streams. Adding a muted spot now preserves the
  main recording; moving basses preserves other sections. Changing RT preserves
  player layout and dry performances.
- `--section-mode recorded` uses the supplied performances once each. The old
  section multiplication is available explicitly as `--section-mode copies`.
  Reports flag copied performances and single recorded bass parts. These
  conditions cannot validate the coherence behavior of independent musicians.
- Main arrays are tilted rigidly, preserving their included angle. Default XY
  and Blumlein diffuse coherence now agree with the documented 0.75 and 0.
- The renderer exposes unnormalised main, isolated bass, other-section and dry
  bass signals to diagnostics. `fixed.*` measures the main bass and main mix on
  frames/pitches selected from the unchanged dry bass source, and records a
  selection digest. This tests cue behavior separately from automatic mixture
  selection. Source-selected pitches are still algorithm estimates, not manual
  annotations. The fixed diagnostic measures the main pickup before spot mixing.
- Raw context coherence and pizzicato clarity are retained in evaluation output.
  Distance groups include seed and section mode; `--seeds 1,2,3` supplies paired
  repeats. Hall comparisons use only distances represented in every hall with
  the cue available. Duplicate controls are counted once, conflicting rows are
  rejected, and scene keys distinguish fractional parameters.
- Results store source hashes, excerpt length, algorithm code hashes, versions
  and NumPy/SciPy versions. Resume rejects stale or missing provenance. Use a
  new output file after changing the algorithm, input files or excerpt length.

Room energy labels remain mean per-player broadband RIR bookkeeping; they are
not measured section DRR. Early absorption remains frequency independent and
the sources omnidirectional. The Aalto source paper reports residual room
effects below 100 Hz; this loader has no compensation for them. Independent
performances, measured responses and listening judgments remain necessary for
stronger validation.

## Verification

Run from the repository root:

```sh
OPENBLAS_NUM_THREADS=1 python3 -W ignore::ResourceWarning -m unittest discover -s tests -p 'test_bass*.py' -q
OPENBLAS_NUM_THREADS=1 python3 tools/bass_depth.py --selftest
node --check omdrc-ctrl/src/kiosk/static/widgets/bass-depth.js
```

The original repair suite had 61 passing tests; the completed follow-up
adds five validation regressions, for 66 passing bass tests.
New regressions cover a muted spot, unrelated sections, moved basses, changed
RT, rigid array rotation, generated-tail ensemble coherence, noise/pitch-variety
bias, reference reuse/short gaps/abstention, pizzicato contamination, context
scoring, material support, provenance and report grouping.

All three built-in synthetic spot tests pass. A command-line smoke run on
synthetic MP3 stems rendered two distances at seeds 1 and 2, preserving exactly
164 source-selected frames per scene; resume scheduled no additional scenes.
That smoke run verifies integration, not musical or perceptual validity.

The corrected full-excerpt Aalto validation is now complete: 198 unique
primary scenes plus radial, annotation and stronger-spot checks. See
[BASS-DEPTH-AALTO-VALIDATION.md](BASS-DEPTH-AALTO-VALIDATION.md) for results,
source URLs, artifacts and reproduction. The current score did not provide
reliable depth ordering. The earlier renderer-1 experiment and its physical
explanations are superseded; implementation repairs alone do not establish
validity.

## Supplied recordings, rerun with 1.6

The full Rattle Mahler 2/II and Minnesota/Vänskä Sibelius 2/I files under
`/media/USBHD2/musica/bass-analysis` were decoded and analysed.

| Measurement | Rattle Mahler 2/II | Minnesota Sibelius 2/I |
|---|---:|---:|
| Experimental score | 8.44 | 6.67 |
| Track coherence ratio | 0.9173 | 0.9333 |
| Solo fundamental | −1.67 dB | −16.04 dB |
| Solo support for fundamental | 3.264 s | 2.368 s |
| Solo + with-sections support | 17.408 s | 25.024 s |
| Provisional | yes | yes |
| Detected plucks | 21 | 25 |
| Pizzicato coherence ratio | 1.0052 | 1.0610 |
| Spot verdict | insufficient | none |

In 1.5 the two track scores were 8.44 and 5.93 and neither was marked
provisional. Bass detection, fundamental and balance are unchanged in these
reruns; reference ratios and score semantics changed. Both coherence parts
now clip at ten, leaving their fundamental measurements to differentiate the
track scores. This is another reason to keep the scores experimental.

Minnesota's critical reputation is background, not a depth label. The listener
reports a tentative impression that it may not be particularly deep. Neither
that impression nor the reputation was used to choose the new algorithm or
adjust its anchors. Independent blind judgments remain outstanding.
