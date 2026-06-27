#!/usr/bin/env python3
"""detect-clicks.py — find clicks / glitches in a captured PCM tone.

Designed for the DRC-chain debugging in this repo: you play a STEADY sine tone
through MPD (so soxr resamples it to the DRC rate, e.g. 44.1k -> 192k) and tap
the resampled stream (the MPD/soxr output) into a raw PCM file.  A perfectly
resampled steady sine obeys, sample-for-sample, the second-order recurrence

    x[n] = 2*cos(w)*x[n-1] - x[n-2]        (w = 2*pi*f0/fs)

so the predictor RESIDUAL  r[n] = x[n] - 2*cos(w)*x[n-1] + x[n-2]  is ~0 for a
clean tone and SPIKES at any discontinuity (a click, a dropout edge, a sample
the resampler got wrong).  That makes clicks trivially separable from the tone
itself, with no dependence on amplitude or phase.  We also flag dropouts
(silence gaps) and stuck/repeated-sample runs inside the active region.

Input: interleaved raw PCM (default stereo S32_LE @ 192000).  numpy only.

Usage:
  detect-clicks.py CAPTURE.raw [--rate 192000] [--channels 2]
                   [--fmt s32le|s16le|f32le] [--f0 997] [--k 12]
"""
import argparse
import sys

import numpy as np

_FMTS = {"s32le": ("<i4", False), "s16le": ("<i2", False), "f32le": ("<f4", True)}


def load(path, fmt, channels):
    dt, is_float = _FMTS[fmt]
    raw = np.fromfile(path, dtype=dt)
    n = raw.size // channels
    x = raw[: n * channels].reshape(n, channels).astype(np.float64)
    if not is_float:
        x /= float(np.iinfo(dt).max)
    return x  # shape (n, channels), range ~[-1, 1]


def estimate_f0(x, fs):
    """Dominant frequency via FFT peak with parabolic interpolation."""
    n = x.size
    w = np.hanning(n)
    X = np.abs(np.fft.rfft(x * w))
    k = int(np.argmax(X[1:]) + 1)
    # parabolic interpolation around the peak bin
    if 1 <= k < X.size - 1:
        a, b, c = X[k - 1], X[k], X[k + 1]
        denom = (a - 2 * b + c)
        delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
    else:
        delta = 0.0
    return (k + delta) * fs / n


def find_active(mono, eps):
    """First/last sample whose 1ms-smoothed envelope exceeds eps."""
    env = np.abs(mono)
    nz = np.nonzero(env > eps)[0]
    if nz.size == 0:
        return None
    return nz[0], nz[-1]


def group_events(idx, gap, fs):
    """Group sorted sample indices that are within `gap` samples into events."""
    if idx.size == 0:
        return []
    splits = np.nonzero(np.diff(idx) > gap)[0] + 1
    groups = np.split(idx, splits)
    return groups


def analyze_channel(mono, fs, f0, k, name):
    out = {"name": name, "events": [], "dropouts": [], "stuck": []}
    peak = np.abs(mono).max()
    if peak < 1e-6:
        out["note"] = "channel is silent"
        return out
    eps = peak * 1e-3
    act = find_active(mono, eps)
    if act is None:
        out["note"] = "no active region"
        return out
    a, b = act
    # trim 5 ms off each edge to avoid the tone's onset/offset transient
    pad = int(0.005 * fs)
    a = min(a + pad, b)
    b = max(b - pad, a)
    seg = mono[a : b + 1]
    out["active_s"] = (a / fs, b / fs)
    out["active_frames"] = seg.size

    if f0 is None:
        f0 = estimate_f0(seg, fs)
    out["f0"] = f0
    w = 2 * np.pi * f0 / fs
    coef = 2 * np.cos(w)

    # second-order linear-prediction residual of the steady tone
    r = seg[2:] - coef * seg[1:-1] + seg[:-2]
    out["peak"] = peak

    # robust threshold: median + k * (1.4826 * MAD)  on |r|
    ar = np.abs(r)
    med = np.median(ar)
    mad = np.median(np.abs(ar - med)) * 1.4826
    sigma = mad if mad > 0 else (ar.std() or 1e-12)
    thr = med + k * sigma
    out["resid_rms"] = float(np.sqrt((r ** 2).mean()))
    out["resid_floor_db"] = 20 * np.log10(max(out["resid_rms"], 1e-12) / peak)
    out["thr"] = float(thr)

    hot = np.nonzero(ar > thr)[0]
    for g in group_events(hot, gap=int(0.002 * fs), fs=fs):
        pk = int(g[np.argmax(ar[g])])
        out["events"].append(
            {
                "t": (a + 2 + pk) / fs,
                "samp": a + 2 + pk,
                "mag": float(ar[pk]),
                "mag_db": 20 * np.log10(max(ar[pk], 1e-12) / peak),
                "width": int(g[-1] - g[0] + 1),
            }
        )

    # dropouts: near-zero runs inside the active region (>= 0.5 ms)
    zero = np.abs(seg) < eps
    minrun = max(int(0.0005 * fs), 16)
    runs = _runs(zero)
    for s, e in runs:
        if e - s >= minrun:
            out["dropouts"].append({"t": (a + s) / fs, "len_ms": (e - s) / fs * 1e3})

    # stuck/repeated identical samples (>= 8 in a row) — a held value during a glitch
    same = np.diff(seg) == 0
    for s, e in _runs(same):
        if e - s >= 8:
            out["stuck"].append({"t": (a + s) / fs, "len": int(e - s + 1)})

    return out


def _runs(boolarr):
    """Yield (start, end_inclusive) index pairs of True runs."""
    if boolarr.size == 0:
        return []
    d = np.diff(boolarr.astype(np.int8))
    starts = list(np.nonzero(d == 1)[0] + 1)
    ends = list(np.nonzero(d == -1)[0])
    if boolarr[0]:
        starts = [0] + starts
    if boolarr[-1]:
        ends = ends + [boolarr.size - 1]
    return list(zip(starts, ends))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--rate", type=int, default=192000)
    ap.add_argument("--channels", type=int, default=2)
    ap.add_argument("--fmt", default="s32le", choices=sorted(_FMTS))
    ap.add_argument("--f0", type=float, default=None, help="expected tone Hz (default: auto-detect)")
    ap.add_argument("--k", type=float, default=12.0, help="threshold = median + k*MAD on |residual|")
    ap.add_argument("--max-list", type=int, default=40)
    a = ap.parse_args()

    x = load(a.capture, a.fmt, a.channels)
    fs = a.rate
    print(f"== {a.capture} ==")
    print(f"format {a.fmt} {a.channels}ch @ {fs} Hz   frames={x.shape[0]}  dur={x.shape[0]/fs:.3f}s")

    total_events = 0
    for ch in range(a.channels):
        res = analyze_channel(x[:, ch], fs, a.f0, a.k, name=f"ch{ch}")
        print(f"\n-- {res['name']} --")
        if "note" in res:
            print(f"   {res['note']}")
            continue
        print(f"   tone f0          : {res['f0']:.2f} Hz   peak={res['peak']:.4f}")
        print(f"   active region    : {res['active_s'][0]:.3f}..{res['active_s'][1]:.3f}s  ({res['active_frames']} frames)")
        print(f"   residual floor   : {res['resid_rms']:.2e}  ({res['resid_floor_db']:.1f} dB below peak)")
        print(f"   click threshold  : {res['thr']:.2e}  (median+{a.k:g}*MAD)")
        ev = res["events"]
        print(f"   CLICKS detected  : {len(ev)}")
        total_events += len(ev)
        for e in ev[: a.max_list]:
            print(f"      t={e['t']:8.4f}s  samp={e['samp']:>9}  mag={e['mag']:.3e} ({e['mag_db']:+.1f} dB)  width={e['width']}smp")
        if len(ev) > a.max_list:
            print(f"      ... and {len(ev) - a.max_list} more")
        if len(ev) >= 2:
            ts = np.array([e["t"] for e in ev])
            d = np.diff(ts)
            print(f"   inter-click gap  : mean={d.mean()*1e3:.1f}ms  min={d.min()*1e3:.1f}ms  max={d.max()*1e3:.1f}ms"
                  + ("   (PERIODIC -> buffer-boundary?)" if d.std() < 0.2 * d.mean() else "   (irregular)"))
        if res["dropouts"]:
            print(f"   DROPOUTS (silence): {len(res['dropouts'])}")
            for dp in res["dropouts"][: a.max_list]:
                print(f"      t={dp['t']:8.4f}s  len={dp['len_ms']:.2f}ms")
        if res["stuck"]:
            print(f"   STUCK runs       : {len(res['stuck'])}")
            for st in res["stuck"][: a.max_list]:
                print(f"      t={st['t']:8.4f}s  len={st['len']}smp")

    print(f"\nRESULT total_clicks={total_events}")
    return 0 if total_events == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
