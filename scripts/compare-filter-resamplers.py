#!/usr/bin/env python3
"""Compare two per-rate BruteFIR coefficient sets built from the same design.

Why this exists
===============
`deploy_filter.py` does not ship the coefficients a design produced — it
regenerates them on the installing host by resampling the design's source
impulse with whatever SoX is present (`REW2raw.sh`).  FreeBSD has SoX 14.4.2,
Arch has the sox_ng fork, so the same design deploys as two different filters
and the cross-OS null test refuses to run.  See
`doc/CROSS-OS-FILTER-COEFFICIENTS.md`.

"Different" is easy to establish with sha256.  The question this answers is
the one that follows: *different by how much, and is either one better?*

How the comparison is exact
===========================
Both candidates are L-times upsamples of one source impulse, and the DFT bin
spacing works out identical:

    192000 / 524288  ==  48000 / 131072  ==  0.3662109375 Hz

so bin k of a candidate is the *same frequency* as bin k of the source.  The
responses can be compared bin for bin with no interpolation, no resampling of
the comparison itself, and no window.  Two things are then measured:

  passband   below the source's Nyquist the ideal is H_up(f) == H_src(f),
             because REW2raw applies a source_rate/target_rate gain that makes
             the upsampled FIR carry the same frequency response, not L times
             it.  Deviation here is what reaches the listener.

  imaging    above the source's Nyquist the ideal is exactly zero.  Anything
             present is the resampler's own image-rejection residue, and it is
             ultrasonic by construction.

A resampler can be better at one and worse at the other; report both rather
than collapsing them into a verdict.

Usage
=====
    ./scripts/compare-filter-resamplers.py \\
        --source  filters/<geom>/source/<variant>/FLX-trimmed-48k.wav \\
        --a       filters/<geom>/192000/@<variant>/L.raw --a-name "SoX 14.4.2" \\
        --b       /mnt/arch/.../192000/@<variant>/L.raw  --b-name "sox_ng 14.8"

Exit status is 0 whatever the numbers say: this reports, it does not judge.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys

import numpy as np

# Bands to report, chosen so the audible range is not averaged together with
# the region where a 48 kHz-sourced filter has nothing but resampler residue.
BANDS = ((20, 1000), (1000, 10000), (10000, 20000), (20000, 22050))


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    """Minimal float32/int WAV reader — `wave` cannot open float32 WAVs.

    Only the fmt and data chunks are needed, and the files this reads are
    written by SoX with a non-extended fmt chunk that soxi itself warns about.
    """
    raw = path.read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise SystemExit(f"{path}: not a RIFF/WAVE file")
    pos, fmt = 12, None
    data = None
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        size = struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise SystemExit(f"{path}: missing fmt or data chunk")
    tag, channels, rate, _, _, bits = fmt
    if tag == 3 and bits == 32:
        samples = np.frombuffer(data, dtype="<f4").astype(np.float64)
    elif tag == 1 and bits == 16:
        samples = np.frombuffer(data, dtype="<i2").astype(np.float64) / 2 ** 15
    elif tag == 1 and bits == 32:
        samples = np.frombuffer(data, dtype="<i4").astype(np.float64) / 2 ** 31
    else:
        raise SystemExit(f"{path}: unsupported format tag {tag} / {bits} bits")
    if channels != 1:
        samples = samples.reshape(-1, channels)[:, 0]
    return samples, rate


def describe(name: str, source: np.ndarray, cand: np.ndarray,
             src_rate: int) -> dict:
    n = source.size
    k = n // 2 + 1                       # bins 0 .. source Nyquist
    if cand.size % n:
        raise SystemExit(
            f"{name}: {cand.size} taps is not a whole multiple of the "
            f"source's {n} — these are not the same design")
    hs = np.fft.rfft(source)
    hc = np.fft.rfft(cand)
    freq = np.arange(k) * src_rate / n

    out = {"name": name, "ratio": cand.size // n, "bands": [], "freq": freq}
    for lo, hi in BANDS:
        m = (freq >= lo) & (freq < hi)
        if not m.any():
            continue
        ref = np.abs(hs[m])
        mag = 20 * np.log10(np.abs(hc[:k][m]) / ref)
        phase = np.angle(hc[:k][m] / hs[m]) * 180 / np.pi
        out["bands"].append({"lo": lo, "hi": hi,
                             "mag": float(np.abs(mag).max()),
                             "phase": float(np.abs(phase).max())})

    image = np.abs(hc[k:])               # ideal: zero
    scale = np.abs(hs).max()
    out["image_peak"] = 20 * np.log10(image.max() / scale)
    out["image_rms"] = 20 * np.log10(np.sqrt((image ** 2).mean()) / scale)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, type=Path,
                   help="the design's source impulse WAV")
    p.add_argument("--a", required=True, type=Path, help="FLOAT64_LE .raw")
    p.add_argument("--b", required=True, type=Path, help="FLOAT64_LE .raw")
    p.add_argument("--a-name", default="A")
    p.add_argument("--b-name", default="B")
    args = p.parse_args()

    source, rate = read_wav_mono(args.source)
    a = np.fromfile(args.a, dtype="<f8")
    b = np.fromfile(args.b, dtype="<f8")
    if a.size != b.size:
        raise SystemExit(f"tap counts differ: {a.size} vs {b.size}")

    ra = describe(args.a_name, source, a, rate)
    rb = describe(args.b_name, source, b, rate)

    same = np.array_equal(a, b)
    print(f"source : {args.source.name}  {source.size} taps @ {rate} Hz")
    print(f"target : {a.size} taps  ({ra['ratio']}x upsample, "
          f"{rate * ra['ratio']} Hz)")
    if same:
        print("\nthe two coefficient sets are byte-identical — nothing to compare")
        return 0

    diff = a - b
    peak = np.abs(source).max()
    print(f"\ndirect difference between the two sets:")
    print(f"  taps differing   {np.count_nonzero(diff)} / {a.size}")
    print(f"  peak difference  {20 * np.log10(np.abs(diff).max() / np.abs(a).max()):+.1f} dB "
          f"relative to the filter's own peak tap")
    print(f"  rms difference   {20 * np.log10(np.sqrt((diff ** 2).mean()) / np.sqrt((a ** 2).mean())):+.1f} dB")

    print(f"\nreproduction of the source response "
          f"(ideal: identical; worst case per band)")
    print(f"  {'band':>18}   {ra['name']:>26}   {rb['name']:>26}   closer")
    for ba, bb in zip(ra["bands"], rb["bands"]):
        better = (ra["name"] if ba["mag"] < bb["mag"] * 0.999 else
                  rb["name"] if bb["mag"] < ba["mag"] * 0.999 else "tie")
        print(f"  {ba['lo']:7d}-{ba['hi']:7d} Hz   "
              f"{ba['mag']:11.6f} dB {ba['phase']:8.4f}°   "
              f"{bb['mag']:11.6f} dB {bb['phase']:8.4f}°   {better}")

    print(f"\nimage rejection above {rate // 2} Hz "
          f"(ideal: silence; dB relative to the passband peak)")
    for key, label in (("image_peak", "peak"), ("image_rms", "rms ")):
        win = ra["name"] if ra[key] < rb[key] else rb["name"]
        print(f"  {label}   {ra['name']}: {ra[key]:+7.2f}   "
              f"{rb['name']}: {rb[key]:+7.2f}   -> {win} better by "
              f"{abs(ra[key] - rb[key]):.2f} dB")

    print("\nRemember which half of this matters: the passband reaches the "
          "listener,\nthe image-rejection region is above the source's Nyquist "
          "and does not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
