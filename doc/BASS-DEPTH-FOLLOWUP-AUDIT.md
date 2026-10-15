# Bass-depth follow-up audit, 2026-10-15

Audited commit: `b52ddd3762791da9ea066807d0bb10822b69298f`
(detector 1.6, renderer 2).

## What was actually tested with Aalto

The repository records an 81-scene Aalto experiment with detector 1.5 and
renderer 1. Its causal conclusions were subsequently withdrawn because the
reference estimator was biased and scene changes also changed unrelated
random realizations. The last audit explicitly says that the corrected
implementation was tested with synthetic stems, not rerun with Aalto.

This follow-up searched `/home/giacomo` and `/media/USBHD2` for the expected
bass stems and bassval JSON results and found none. Downloading the Aalto
index failed with `curl: (6) Could not resolve host: users.aalto.fi`.
The current execution environment does not permit requesting network
escalation. Consequently **no new Aalto result is claimed here**.

## Findings

1. **The fundamental cue contains stereo phase cancellation.** In
   `tools/bass_depth.py:583`, power is measured after summing the channels.
   A change in interchannel delay changes this spectrum even when both
   channels retain exactly the same source spectrum. A controlled 80 Hz
   six-harmonic signal, with amplitudes 1/k, gave the following results:

   | Right-channel delay | Fundamental cue | Harmonic coherence | R/L balance |
   |---:|---:|---:|---:|
   | 0 ms | 9.076 dB | 1.000 | 0.000 dB |
   | 0.5 ms | 9.678 dB | 1.000 | 0.000 dB |
   | 1.0 ms | 11.279 dB | 1.000 | 0.000 dB |
   | 1.5 ms | 12.935 dB | 1.000 | 0.000 dB |
   | 1.75 ms | 13.318 dB | 1.000 | 0.000 dB |

   This is a 4.242 dB cue change without a simulated depth change. The
   largest delay corresponds to about 0.60 m of acoustic path difference,
   comparable to the AB spacing. This is a constructed confound, not an
   Aalto measurement or an estimate of its prevalence in recordings.
   Channel-power averaging would remove this particular cancellation;
   it would not establish a distance relationship or remove timbre/EQ bias.

2. **The distance grid also changes bearing.** It moves bass players along
   the stage y axis at fixed x and microphone position. Source bearing,
   directional-microphone gain, and interchannel path difference therefore
   change with distance. This is a legitimate stage-placement experiment,
   but it cannot isolate radial distance. Add a fixed-bearing radial sweep
   before attributing a spectral/coherence trend specifically to distance.

3. **The source-controlled diagnostic is useful but is not annotated truth.**
   `source_selection` uses the detector's own estimated pitches and gates.
   Holding those frames fixed removes mixture-dependent selection, but
   does not establish pitch accuracy, bass activity accuracy, or perceptual
   depth. Similarly, harmonic R/L balance is a direct measurement of the
   selected mixture bins; its attribution exclusively to double basses
   still needs an isolated-source comparison.

4. **Reference matching repairs sampling bias, not identifiability.** The
   latest change matches masks, weights and run geometry, isolates random
   streams, and abstains when references cannot be matched. These are
   meaningful repairs. None makes a different musical passage a calibrated
   reverberation reference. A stationary harmonic signal can have unit
   interchannel coherence at several delays, as the control above shows.

## Reproduction of the phase control

From the repository root, with NumPy/SciPy installed:

```sh
OPENBLAS_NUM_THREADS=1 PYTHONPATH=tools python3 - <<'PY'
import numpy as np
import bass_depth as b
t = np.arange(b.SR * 12) / b.SR
frames = np.arange(10, 160)
pitches = np.full(190, 80.)
for delay in [0, .0005, .001, .0015, .00175]:
    L = sum(np.sin(2*np.pi*80*k*t)/k for k in range(1, 7))
    R = sum(np.sin(2*np.pi*80*k*(t-delay))/k for k in range(1, 7))
    c = b.context_cues(L, R, pitches, frames, b.SR/b.NFFT, None)
    print(delay*1000, {k: round(c[k], 6)
          for k in ('coh_comb', 'fund_db', 'balance_db')})
PY
```

## Conclusion and outstanding experiment

The practical conclusion remains: no current cue has demonstrated reliable
bass-depth estimation. That is weaker than proving that depth cannot be
estimated, or that the corrected detector fails on Aalto. The latter
experiment is still outstanding; the old table cannot answer it.

Once original stems are accessible, run the full distance, hall and spot
grids with recorded parts and paired seeds 1,2,3 into a fresh provenance-
tracked results file. Compare isolated bass and mixture measurements on
fixed source frames, include the radial control, and report missing cue
coverage as well as correlations. Keep score anchors unchanged. Any
candidate surviving those controls still needs held-out performances,
different acoustics and independent listening judgments before calling
it a reliable perceived-depth parameter.

Verification in this follow-up: all 61 `test_bass*.py` tests passed,
all three built-in synthetic spot selftests passed, and the card's JavaScript
syntax check passed. All three selftests returned **no depth score**; their
passing verdicts therefore do not validate depth ranking. The printed reason
(`no bass context with enough frames`) is also incomplete: there are tens of
seconds of detected solo/with-sections material, but no usable matched ratio.
No detector or runtime component was modified or deployed.
