#!/usr/bin/env python3
"""Generate a fabricated brickwall-mastered stress WAV for headroom_calc.py.

    gen-stress-master-wav.py OUT.wav [--rate 48000] [--seconds 3.0]
                             [--crest-db 6.0] [--seed 20260915]

Why this exists
===============
headroom_calc.py bounded BruteFIR's convolution two ways: peak |H(f)| (the
steady-state sine gain) and |cumsum(h)| (the impulse step response). Neither
bounds real program material. The mechanism a real track exposed on
2026-09-15 (120.green @v1.FDW6: measured +0.157 dBFS over full scale through
a fixed 8 dB attenuation both of those tests called safe with 5+ dB to spare)
is the filter's EXCESS PHASE: it re-times frequency bands without changing
their magnitude, which raises the crest factor of anything already
brickwalled to 0 dBFS. No magnitude-domain test can see that -- only
convolving a real, full-scale, low-crest master through the filter and
measuring the actual output peak bounds it (see project memory:
attenuation-8db-is-needed-not-oversized.md, "no magnitude-domain metric can
ever bound this... an honest audit is empirical").

This file is that real signal: a clean-room synthesis, not sampled or
derived from any copyrighted recording, built the same way as
gen-multitone-wav.py's low-crest technique (broadband content, then
iterative clip-and-restore) with two additions that match the measured
failure:

* energy tilted +6 dB from 2 kHz to 18 kHz -- the band the 2026-09-15
  analysis found contributed most on both sides of the filter (5-20 kHz was
  the single biggest contributor to both the input and the clipped output);
* periodic broadband transient bursts, because the crest-raising effect
  concentrates in short (~5 ms) windows around attacks (measured 4.90 ->
  12.99 dB in a 5 ms window there), not in the sustained bed.

Deterministic for a fixed --seed. Regenerate here rather than treat the
committed WAV as an opaque binary -- this script IS its provenance.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

try:
    import wave
except ImportError:  # pragma: no cover - stdlib, always present
    raise


def shaped_bed(n: int, rate: int, rng: np.random.Generator) -> np.ndarray:
    """Broadband noise bed, spectrum tilted +6 dB from 2 kHz to 18 kHz."""
    freqs = np.fft.rfftfreq(n, d=1.0 / rate)
    spec = rng.normal(size=freqs.size) + 1j * rng.normal(size=freqs.size)
    tilt_db = np.clip((freqs - 2000.0) / 16000.0, 0.0, 1.0) * 6.0
    spec *= 10.0 ** (tilt_db / 20.0)
    spec[0] = 0.0
    bed = np.fft.irfft(spec, n)
    return bed / np.abs(bed).max()


def transient_bursts(n: int, rate: int, rng: np.random.Generator,
                      spacing_s: float = 0.4, attack_s: float = 0.003,
                      decay_s: float = 0.015) -> np.ndarray:
    """Periodic broadband bursts (drum-hit-like), amplitude jittered per hit."""
    out = np.zeros(n)
    spacing = int(spacing_s * rate)
    attack = max(1, int(attack_s * rate))
    decay = max(1, int(decay_s * rate))
    dur = attack + decay * 6
    t = np.arange(dur)
    envelope = np.where(t < attack, t / attack, np.exp(-(t - attack) / decay))
    for start in range(0, max(0, n - dur), spacing):
        jitter = int(start + rng.integers(-spacing // 8, spacing // 8 + 1))
        jitter = max(0, min(n - dur, jitter))
        burst = rng.normal(size=dur) * envelope
        amp = 1.0 + 0.6 * rng.random()   # hit-to-hit level jitter
        out[jitter:jitter + dur] += burst * amp
    peak = np.abs(out).max()
    return out / peak if peak > 0 else out


def brickwall(x: np.ndarray, target_crest_db: float, rounds: int = 400):
    """Iteratively hard-clip and renormalise until crest factor <= target.

    Matches gen-multitone-wav.py's low_crest_phases technique (clip, then
    restore full scale, repeat) but operates in the time domain on a mixed
    signal rather than adjusting FFT-bin phases -- a brickwall limiter clips
    in time, and repeated clipping is exactly how a loudness-war master
    gets its broadband harmonic content, not an artefact to avoid here.
    """
    x = x / np.abs(x).max()
    crest_db = 20.0 * np.log10(np.abs(x).max() / np.sqrt(float((x ** 2).mean())))
    for _ in range(rounds):
        rms = np.sqrt(float((x ** 2).mean()))
        crest_db = 20.0 * np.log10(np.abs(x).max() / rms)
        if crest_db <= target_crest_db:
            break
        threshold = np.abs(x).max() * 0.985
        x = np.clip(x, -threshold, threshold)
        x = x / np.abs(x).max()
    return x, crest_db


def write_wav_mono(path: Path, x: np.ndarray, rate: int, bits: int = 24) -> None:
    full = 2 ** (bits - 1)
    ints = np.clip(np.rint(x * (full - 1)), -full, full - 1).astype(np.int64)
    if bits == 24:
        raw = ints.astype("<i4").tobytes()
        payload = b"".join(raw[i:i + 3] for i in range(0, len(raw), 4))
    elif bits == 16:
        payload = ints.astype("<i2").tobytes()
    else:
        raise SystemExit("--bits must be 16 or 24")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(bits // 8)
        w.setframerate(rate)
        w.writeframes(payload)


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("wav")
    p.add_argument("--rate", type=int, default=48000)
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--crest-db", type=float, default=6.0,
                   help="target crest factor after brickwall limiting (default 6.0 dB)")
    p.add_argument("--bits", type=int, default=24, choices=(16, 24))
    p.add_argument("--seed", type=int, default=20260915)
    a = p.parse_args()

    rng = np.random.default_rng(a.seed)
    n = int(round(a.seconds * a.rate))

    bed = shaped_bed(n, a.rate, rng) * 0.5
    bursts = transient_bursts(n, a.rate, rng)
    mix = bed + bursts
    x, crest_db = brickwall(mix, a.crest_db)

    out = Path(a.wav)
    write_wav_mono(out, x, a.rate, a.bits)

    manifest = {
        "rate": a.rate, "seconds": a.seconds, "bits": a.bits, "seed": a.seed,
        "target_crest_db": a.crest_db, "achieved_crest_db": crest_db,
        "sample_peak_dbfs": 20.0 * np.log10(np.abs(x).max()),
        "wav_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
    }
    out.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"{out}: {n} frames, S{a.bits} mono @ {a.rate} Hz, "
          f"crest {crest_db:.2f} dB (target {a.crest_db}), "
          f"peak {manifest['sample_peak_dbfs']:+.2f} dBFS")
    print(f"  manifest {out.with_suffix('.json')}")
    print(f"  sha256 {manifest['wav_sha256']}")


if __name__ == "__main__":
    main()
