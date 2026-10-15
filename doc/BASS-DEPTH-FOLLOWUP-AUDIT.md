# Bass-depth follow-up audit, 2026-10-15

Audited commit: `b52ddd3762791da9ea066807d0bb10822b69298f`
(detector 1.6, renderer 2).

## Validation status

The initial download from `users.aalto.fi` failed DNS resolution. Original
MP3 stems were subsequently obtained from `mediatech.aalto.fi`, and annotated
WAV derivatives from PHENICX. The corrected full-excerpt experiment is now
complete: see [the Aalto validation report](BASS-DEPTH-AALTO-VALIDATION.md)
for results, source URLs, provenance and reproducible commands. The old
renderer-1 experiment is superseded and is not evidence for the current code.

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
   but it cannot isolate radial distance. The completed fixed-bearing radial sweep also failed to establish a
   consistent distance cue; it is documented in the validation report.

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

5. **The existing loader omits nine Bruckner mic-6 string recordings.**
   `stems.parse_name` recognizes `a`/`b` suffixes but not `c`–`h`. The corrected
   run therefore uses 37 of 46 frontal files for that piece. No bass file is
   omitted, but orchestra masking/reference behavior can change with the
   additional strings. The validation report documents the exact exclusions;
   full-corpus coverage must not be claimed. The current implementation was
   held fixed for this audit.

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

## Conclusion after corrected validation

The corrected Aalto distance, hall and spot grids, radial control and
independent annotation check are complete. They support the practical
conclusion that the current cues do not supply reliable bass-depth estimation
in the tested conditions. They do not prove that depth cannot be estimated.
Channel-power averaging removes the specific phase-cancellation confound
above but did not rescue the fundamental cue's distance relationship.

The [validation report](BASS-DEPTH-AALTO-VALIDATION.md) includes missing and
constant cues, condition-specific successes, stronger spots and simulator
limitations. All 66 bass tests pass, including five new validation regressions.
The three built-in spot selftests passed during the initial audit, but all
returned no depth score; their passing verdicts do not validate depth ranking.
No detector or runtime component was modified or deployed in this follow-up.
