#!/usr/bin/env python3
"""Measure how cleanly a chain delivered the gen-tone-wav.py tones.

Usage:
    resampler-residual.py PREFIX|FILE.wire.raw [--rate R] [--json OUT]

PREFIX is a bitperfect_runner.py artifact prefix (rate read from PREFIX.json);
a bare .wire.raw needs --rate.

Method
======
The capture is the raw S32_LE stereo stream that reached the USB wire.  The
tone region is located (the file is silent around it), one second is trimmed
from each end so a resampler's start-up and tail ringing are not counted, and
each channel's sine — 997 Hz left, 1499 Hz right by default — is fitted by
least squares (amplitude, phase, DC; frequency refined over a tiny grid).  The
fit is subtracted.  What remains is everything the chain did that is not the
tone: a resampler's aliasing, imaging and noise, plus any truncation.

Reading it
==========
    error      residual RMS relative to the tone's RMS, in dB.  More negative
               is cleaner.
    floor      where 32-bit rounding alone would put the residual.  A result
               within a few dB of it means the chain added nothing measurable.

With the default -90 dBFS tone the window between tone and floor is about
95 dB.  A rate-MATCHED pass-through should land on the floor (the bytes are
the source's, bit for bit).  A rate-MISMATCHED run shows the resampler: a very
high quality one (soxr VHQ) stays at or near the floor; a fast one sits tens
of dB above it, with spurs listed below the summary.

Nothing here compares two machines directly — run it on each and compare the
numbers, or null the captures with bitperfect-null.py.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

FS32 = 2.0 ** 31
FLOOR_DB = 20 * math.log10((1 / math.sqrt(12)) / FS32)   # 32-bit rounding RMS


def load(target, rate):
    p = Path(target)
    if p.suffix == ".raw":
        raw, meta = p, None
    else:
        raw = Path(str(p) + ".wire.raw")
        meta = Path(str(p) + ".json")
    info = {}
    if meta and meta.is_file():
        info = json.loads(meta.read_text())
        # The wire runs at the chain's rate; `rate` in the sidecar is the
        # material's, and differs from it exactly when a resampler was in use.
        rate = (rate or info.get("wire_rate")
                or (info.get("chain") or {}).get("brutefir_rate")
                or info.get("rate"))
    if not rate:
        sys.exit("need --rate (no PREFIX.json next to the capture)")
    a = np.fromfile(raw, dtype="<i4")
    a = a[: (len(a) // 2) * 2].reshape(-1, 2).astype(np.float64)
    return a, int(rate), info


def fit(x, f, rate):
    """Least-squares sine fit; refine f over a small grid; return (fit, f, amp)."""
    n = np.arange(len(x), dtype=np.float64)
    best = None
    for df in np.linspace(-0.02, 0.02, 41):
        w = 2 * np.pi * (f + df) / rate
        A = np.column_stack([np.cos(w * n), np.sin(w * n), np.ones_like(n)])
        coef, *_ = np.linalg.lstsq(A, x, rcond=None)
        r = x - A @ coef
        e = float(r @ r)
        if best is None or e < best[0]:
            best = (e, A @ coef, f + df, math.hypot(coef[0], coef[1]))
    return best[1], best[2], best[3]


def spurs(res, rate, k=5):
    win = np.blackman(len(res))
    spec = np.abs(np.fft.rfft(res * win)) / (win.sum() / 2)
    freqs = np.fft.rfftfreq(len(res), 1 / rate)
    db = 20 * np.log10(np.maximum(spec, 1e-30) / FS32)
    out, taken = [], np.zeros(len(db), bool)
    for i in np.argsort(db)[::-1]:
        if taken[max(0, i - 20):i + 20].any():
            continue
        taken[i] = True
        out.append({"hz": round(float(freqs[i]), 1), "dbfs": round(float(db[i]), 1)})
        if len(out) == k:
            break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture")
    ap.add_argument("--rate", type=int)
    ap.add_argument("--freq-l", type=float, default=997.0)
    ap.add_argument("--freq-r", type=float, default=1499.0)
    ap.add_argument("--seconds", type=float, default=6.0, help="analysis window")
    ap.add_argument("--json")
    a = ap.parse_args()

    x, rate, info = load(a.capture, a.rate)
    active = np.nonzero(np.abs(x).sum(axis=1))[0]
    if len(active) < rate * 3:
        sys.exit("capture holds less than 3 s of signal — nothing to measure")
    start = active[0] + rate                      # skip 1 s of start-up
    stop = min(active[-1] - rate, start + int(a.seconds * rate))
    seg = x[start:stop]

    result = {"capture": a.capture, "rate": rate,
              "window_s": round(len(seg) / rate, 3),
              "floor_db": round(FLOOR_DB, 1),
              "provenance_os": (info.get("chain", {}).get("provenance", {}) or {}).get("os")
                               or info.get("os"),
              "material": info.get("input"),
              "channels": {}}
    for ch, f in ((0, a.freq_l), (1, a.freq_r)):
        s = seg[:, ch]
        model, fhat, amp = fit(s, f, rate)
        r = s - model
        tone_rms = amp / math.sqrt(2)
        res_rms = float(np.sqrt(np.mean(r * r)))
        err = 20 * math.log10(max(res_rms, 1e-30) / tone_rms)
        res_dbfs = 20 * math.log10(max(res_rms, 1e-30) / FS32)
        result["channels"]["LR"[ch]] = {
            "tone_hz": round(fhat, 3),
            "tone_dbfs": round(20 * math.log10(amp / FS32), 2),
            "residual_rms_dbfs": round(res_dbfs, 1),
            "error_db": round(err, 1),
            "above_floor_db": round(res_dbfs - FLOOR_DB, 1),
            "max_abs_residual_lsb": int(np.max(np.abs(np.rint(r)))),
            "top_spurs": spurs(r, rate),
        }

    print(f"{a.capture}  ({rate} Hz, {result['window_s']} s analysed, "
          f"32-bit floor {result['floor_db']} dBFS)")
    for c, v in result["channels"].items():
        print(f"  {c}: tone {v['tone_hz']:g} Hz at {v['tone_dbfs']} dBFS | residual "
              f"{v['residual_rms_dbfs']} dBFS rms = {v['error_db']} dB below tone, "
              f"{v['above_floor_db']:+} dB vs floor, max |r| {v['max_abs_residual_lsb']} LSB")
        print("     spurs: " + ", ".join(f"{s['hz']:g} Hz {s['dbfs']} dBFS"
                                          for s in v["top_spurs"]))
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
