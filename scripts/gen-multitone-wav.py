#!/usr/bin/env python3
"""Generate test material for measuring a chain's HIGH-FREQUENCY behaviour.

    gen-multitone-wav.py OUT.wav [--rate 44100] [--chain-rate 192000]
                         [--mode multitone|overs] [--bits 16|24|32]

Two modes, because the two things left unmeasured after the 2026-09-08..11
investigation fail in different ways and need different signals.

`--mode multitone` (default) — the transfer function
=====================================================
A sum of `--tones` sines, log-spaced from `--fmin` to `--fmax`, each landing
on an exact FFT bin of the analysis grid.  hf-response.py divides the capture
by this known input and reports gain and phase per tone.  Unlike
resampler-residual.py it does NOT fit amplitude: a chain that attenuates
18 kHz by 1 dB shows up as -1 dB, where a free fit would absorb it and still
report a deep residual.  That blind spot is why this file exists.

Two properties make the measurement trustworthy, and both are checked at
generation time rather than assumed:

* **All tone bins are odd.**  Every even-order product (harmonics 2f, sums and
  differences f1±f2) therefore lands on an even bin, which carries no tone —
  so the empty bins measure distortion instead of hiding it.
* **No third-order product lands on a tone.**  2*k1-k2 is odd and *could*
  collide; every candidate bin is rejected until none does.  Without this the
  tool reports a clean spectrum precisely when the chain is distorting.

The period is chosen so one FFT bin means the same frequency at the source
rate and at the chain rate — the trick CROSS-OS-FILTER-COEFFICIENTS.md used
for the two coefficient sets.  Pass `--chain-rate` when the chain will
resample; bin k of the capture is then bin k of the source, exactly, with no
interpolation or windowing error anywhere in the comparison.

`--mode overs` — intersample overshoot
======================================
A sine at exactly rate/4 with 45 degrees of phase.  Its samples sit at
+-0.7071*A, so a file whose SAMPLE peak is -0.1 dBFS has a TRUE peak of
+2.91 dBFS.  Nothing is wrong with the file; the overshoot lives between the
samples, as it does in any loud commercial master.  Resample it and those
overshoots become real samples, and an integer output format clamps them.

This is the only signal here that must be played loud.  Every measurement in
the investigation ran at -46 dBFS (the nulls) or -90 dBFS (the tones), so a
level-dependent defect could not have appeared in any of them.

Determinism: fully deterministic for a given seed on one machine.  Across
operating systems the last bit of a sample may differ (libm), which does not
matter — hf-response.py works from the manifest's bins, never from bytes.
"""
import argparse
import hashlib
import json
import math
import struct
import wave
from math import gcd
from pathlib import Path

import numpy as np

FS32 = 2 ** 31


def period_length(rate, chain_rate, seconds):
    """Samples per period such that rate/N == chain_rate/M for integer M."""
    unit = rate // gcd(rate, chain_rate)
    m = max(1, round(seconds * rate / unit))
    n = unit * m
    while n % 4:                      # --mode overs needs rate/4 on a bin
        m += 1
        n = unit * m
    return n


def pick_bins(n, rate, fmin, fmax, count):
    """Odd bins, log-spaced, with no third-order product landing on a tone."""
    spacing = rate / n
    want = np.geomspace(fmin, fmax, count)
    chosen = []
    for f in want:
        k0 = int(round(f / spacing)) | 1
        for delta in range(0, 512, 2):
            for k in ((k0 + delta), (k0 - delta)):
                if k < 1 or k * spacing >= rate / 2:
                    continue
                if k in chosen:
                    continue
                # reject if k collides with a 3rd-order product of the set,
                # or would create one that lands on an already-chosen tone
                bad = any(2 * a - k in chosen or 2 * k - a in chosen or
                          k == 2 * a - b
                          for a in chosen for b in chosen)
                if not bad:
                    chosen.append(k)
                    break
            else:
                continue
            break
    return sorted(set(chosen))


def low_crest_phases(bins, n, seed, rounds=200):
    """Random phases, then iteratively clip and restore to flatten the peak."""
    rng = np.random.default_rng(seed)
    phase = rng.uniform(0, 2 * np.pi, len(bins))
    for _ in range(rounds):
        spec = np.zeros(n // 2 + 1, dtype=complex)
        spec[bins] = np.exp(1j * phase)
        x = np.fft.irfft(spec, n)
        peak = np.abs(x).max()
        x = np.clip(x, -0.85 * peak, 0.85 * peak)
        spec = np.fft.rfft(x)
        phase = np.angle(spec[bins])
    return phase


def synth(bins, phase, n):
    spec = np.zeros(n // 2 + 1, dtype=complex)
    spec[bins] = np.exp(1j * phase)
    x = np.fft.irfft(spec, n)
    return x / np.abs(x).max()


def true_peak(x, oversample=8):
    """Peak of the reconstructed waveform, via zero-stuffed band-limited interp."""
    n = len(x)
    spec = np.fft.rfft(x)
    big = np.zeros(n * oversample // 2 + 1, dtype=complex)
    big[: len(spec)] = spec * oversample
    return float(np.abs(np.fft.irfft(big, n * oversample)).max())


def write_wav(path, left, right, rate, bits):
    """left/right are native integers for `bits` (S16, S24 or S32 range)."""
    frames = np.stack([left, right], axis=1).reshape(-1)
    if bits == 32:
        payload = frames.astype("<i4").tobytes()
    elif bits == 16:
        payload = frames.astype("<i2").tobytes()
    elif bits == 24:
        b = frames.astype("<i4").tobytes()          # low 3 bytes are the S24 value
        payload = b"".join(b[i:i + 3] for i in range(0, len(b), 4))
    else:
        raise SystemExit("--bits must be 16, 24 or 32")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(bits // 8)
        w.setframerate(rate)
        w.writeframes(payload)


def quantise(x, bits):
    """Float in [-1, 1) to the native integer grid of `bits`."""
    full = 2 ** (bits - 1)
    return np.clip(np.rint(x * (full - 1)), -full, full - 1).astype(np.int64)


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("wav")
    p.add_argument("--rate", type=int, default=44100, help="source/material rate")
    p.add_argument("--chain-rate", type=int, default=None,
                   help="rate the chain will run at (default: same as --rate)")
    p.add_argument("--mode", choices=("multitone", "overs"), default="multitone")
    p.add_argument("--bits", type=int, default=32, choices=(16, 24, 32))
    p.add_argument("--tones", type=int, default=96,
               help="about 30 per decade: ~10 above 10 kHz")
    p.add_argument("--fmin", type=float, default=20.0)
    p.add_argument("--fmax", type=float, default=None,
                   help="default 0.476*rate (21.0 kHz at 44.1 k)")
    p.add_argument("--level", type=float, default=-20.0,
                   help="multitone RMS dBFS (default -20)")
    p.add_argument("--peak", type=float, default=-0.1,
                   help="overs mode SAMPLE peak dBFS (default -0.1)")
    p.add_argument("--period-seconds", type=float, default=2.0)
    p.add_argument("--repeats", type=int, default=6)
    p.add_argument("--seed", type=int, default=20260911)
    a = p.parse_args()

    chain_rate = a.chain_rate or a.rate
    fmax = a.fmax if a.fmax else 0.476 * a.rate
    n = period_length(a.rate, chain_rate, a.period_seconds)
    spacing = a.rate / n

    if a.mode == "overs":
        k = n // 4
        bins = [k]
        phase = np.array([math.pi / 4])
        base = synth(bins, phase, n)
        scale = 10 ** (a.peak / 20.0)
        x = base * scale
        tp = true_peak(x)
        left = right = quantise(x, a.bits)
        manifest_level = {"sample_peak_dbfs": 20 * math.log10(np.abs(x).max()),
                          "true_peak_dbfs": 20 * math.log10(tp)}
    else:
        bins = pick_bins(n, a.rate, a.fmin, fmax, a.tones)
        if len(bins) < 4:
            raise SystemExit("could not place enough tones; raise --period-seconds")
        phase = low_crest_phases(bins, n, a.seed)
        base = synth(bins, phase, n)
        rms_now = math.sqrt(float((base ** 2).mean()))
        x = base * (10 ** (a.level / 20.0) / rms_now)
        if np.abs(x).max() >= 1.0:
            raise SystemExit(f"--level {a.level} clips this multitone "
                             f"(peak {20*math.log10(np.abs(x).max()):+.2f} dBFS); "
                             f"lower it")
        tp = true_peak(x)
        left = right = quantise(x, a.bits)
        manifest_level = {"rms_dbfs": a.level,
                          "sample_peak_dbfs": 20 * math.log10(np.abs(x).max()),
                          "true_peak_dbfs": 20 * math.log10(tp),
                          "crest_db": 20 * math.log10(np.abs(x).max()) - a.level}

    tiled_l = np.tile(left, a.repeats)
    tiled_r = np.tile(right, a.repeats)
    out = Path(a.wav)
    write_wav(out, tiled_l, tiled_r, a.rate, a.bits)

    manifest = {
        "mode": a.mode,
        "rate": a.rate,
        "chain_rate": chain_rate,
        "bits": a.bits,
        "period_samples": n,
        "repeats": a.repeats,
        "bin_spacing_hz": spacing,
        "bins": [int(b) for b in bins],
        "freqs_hz": [float(b * spacing) for b in bins],
        "amplitudes": [float(np.abs(np.fft.rfft(x)[b]) * 2 / n) for b in bins],
        "phases_rad": [float(v) for v in phase],
        "seed": a.seed,
        "wav_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        **manifest_level,
    }
    tones = out.with_suffix(".tones.json")
    tones.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"{out}: {len(tiled_l)} frames, S{a.bits} stereo @ {a.rate} Hz")
    print(f"  period {n} samples ({n/a.rate:.3f} s), bin spacing {spacing:.4f} Hz, "
          f"bin-exact at {chain_rate} Hz "
          f"(M = {n * chain_rate // a.rate})")
    if a.mode == "multitone":
        print(f"  {len(bins)} tones, {bins[0]*spacing:.1f} Hz .. {bins[-1]*spacing:.1f} Hz, "
              f"RMS {a.level:g} dBFS, sample peak "
              f"{manifest['sample_peak_dbfs']:+.2f} dBFS, "
              f"crest {manifest['crest_db']:.1f} dB")
    else:
        print(f"  {bins[0]*spacing:.1f} Hz at 45 deg: sample peak "
              f"{manifest['sample_peak_dbfs']:+.2f} dBFS, "
              f"TRUE peak {manifest['true_peak_dbfs']:+.2f} dBFS")
    print(f"  manifest {tones}")
    print(f"  sha256 {manifest['wav_sha256']}")


if __name__ == "__main__":
    main()
