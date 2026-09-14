#!/usr/bin/env python3
"""Measure a chain's gain and phase per frequency, with the top octave in mind.

    hf-response.py PREFIX|FILE.wire.raw --tones OUT.tones.json [--rate R]
                   [--json REPORT.json]

PREFIX is a bitperfect_runner.py artifact prefix (the wire's rate is read from
PREFIX.json); a bare .wire.raw needs --rate.  The manifest comes from
gen-multitone-wav.py and carries the bins, so the analysis never has to guess
what was played.

Why this exists
===============
Every measurement in the 2026-09-08..11 investigation either compared bytes at
a matched rate, or fitted a low-frequency tone and reported what was left over.
Neither can see a high-frequency gain error:

* a null test needs identical bytes, so it cannot run at all once a resampler
  is in the path;
* resampler-residual.py fits amplitude and phase freely, so a chain that rolls
  18 kHz off by 1 dB has that dB absorbed into the fit and still reports a
  residual at the 32-bit floor.

The complaint being chased is a loss of air and detail.  Nothing measured so
far addressed the top octave at all.  This does, by dividing the capture by a
known input instead of fitting it.

Method
======
The source is periodic with `period_samples` at the source rate, so it is
periodic with M = period_samples * wire_rate / source_rate at the wire, and M
is an integer by construction (gen-multitone-wav.py chose the period that way).
Analysis therefore takes whole periods and needs no window function, no
interpolation and no alignment: an unknown capture offset is a pure delay, and
a pure delay is removed as the linear term of the phase fit.

    gain(k)  = |Y[k]| / |X[k]|            absolute, not fitted
    phase(k) = angle(Y[k]) - (linear fit)  delay removed, deviation reported

Several consecutive periods are analysed separately and compared.  Two periods
of the same periodic signal must produce the same spectrum; if they do not, the
chain dropped or inserted samples, which is itself a result.

Reading it
==========
    gain re 1 kHz   what a listener would call tonal balance.  A resampler's
                    passband edge belongs in the 20 kHz..Nyquist row and is
                    expected; anything in the rows below it is not.
    phase dev       departure from linear phase, degrees, delay removed.
                    The offset printed with it is the capture's position within
                    the period, not the chain's absolute latency.
    above Nyquist   the source has nothing there.  Energy here is a resampler
                    image, and its frequency says which one.
    empty bins      distortion and noise, in bins no tone occupies.  Split at
                    10 kHz because intermodulation from clipping lands high.
    clipped         samples clamped at full scale.  Nonzero means the chain ran
                    out of headroom -- see `--mode overs`, and note that this
                    can only appear at a realistic listening level.

`--mode overs` captures are analysed as a single tone: clip count, true peak at
the wire, and harmonic distortion.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

FS32 = 2.0 ** 31
FULL = 2 ** 31 - 1

BANDS = ((20.0, 1000.0), (1000.0, 10000.0), (10000.0, 16000.0),
         (16000.0, 20000.0), (20000.0, None))


def load_capture(target, rate):
    p = Path(target)
    if p.suffix == ".raw":
        raw, meta = p, None
    else:
        raw, meta = Path(str(p) + ".wire.raw"), Path(str(p) + ".json")
    info = {}
    if meta and meta.is_file():
        info = json.loads(meta.read_text())
        rate = (rate or info.get("wire_rate")
                or (info.get("chain") or {}).get("brutefir_rate")
                or info.get("rate"))
    if not rate:
        sys.exit("need --rate (no PREFIX.json next to the capture)")
    a = np.fromfile(raw, dtype="<i4")
    a = a[: (len(a) // 2) * 2].reshape(-1, 2)
    return a, int(rate), info


def active_region(a, margin_s, rate):
    """Trim silence, then `margin_s` more from each end for settling/ringing."""
    mag = np.abs(a).max(axis=1)
    loud = np.flatnonzero(mag > mag.max() // 64)
    if loud.size == 0:
        sys.exit("capture is silent")
    lo, hi = int(loud[0]), int(loud[-1]) + 1
    pad = int(margin_s * rate)
    lo, hi = lo + pad, hi - pad
    if hi - lo <= 0:
        sys.exit("nothing left after trimming; lower --margin")
    return lo, hi


def spectra(x, m, count):
    """`count` consecutive M-point spectra of one channel, scaled to full scale."""
    out = []
    for i in range(count):
        seg = x[i * m:(i + 1) * m].astype(np.float64) / FS32
        out.append(np.fft.rfft(seg) * 2 / m)
    return out


def db(v):
    return 20 * math.log10(v) if v > 0 else -np.inf


def estimate_delay(y, xref, bins, m):
    """Delay in wire samples, to sub-sample resolution.

    A capture starts at an arbitrary offset and BruteFIR adds its own latency,
    so the phase can turn many times between one tone and the next.  Unwrapping
    a sparse tone set cannot recover that -- it silently returns nonsense.  The
    delay is instead found where it is unambiguous: the cross-spectrum's
    inverse transform, peak-interpolated, then removed before any phase is
    reported.
    """
    c = np.zeros(m // 2 + 1, dtype=complex)
    cross = y[bins] * np.conj(xref)
    mag = np.abs(cross)
    c[bins] = cross / np.where(mag > 0, mag, 1.0)
    r = np.fft.irfft(c, m)
    k = int(np.argmax(r))
    a0, a1, a2 = r[(k - 1) % m], r[k], r[(k + 1) % m]
    den = a0 - 2 * a1 + a2
    d = k + (0.5 * (a0 - a2) / den if den else 0.0)
    return d - m if d > m / 2 else d


def band_rows(freqs, gain_db, phase_dev, nyq):
    rows = []
    for lo, hi in BANDS:
        top = hi if hi is not None else nyq
        sel = [i for i, f in enumerate(freqs) if lo <= f < top]
        if not sel:
            continue
        g = [gain_db[i] for i in sel]
        pd = [abs(phase_dev[i]) for i in sel]
        worst = max(sel, key=lambda i: abs(gain_db[i]))
        rows.append({
            "band": f"{lo/1000:g}-{top/1000:.2f} kHz",
            "tones": len(sel),
            "mean_db": float(np.mean(g)),
            "worst_db": float(gain_db[worst]),
            "worst_hz": float(freqs[worst]),
            "max_phase_dev_deg": float(max(pd)),
            "transition": hi is None,
        })
    return rows


def analyse_multitone(a, wire_rate, man, args):
    n = man["period_samples"]
    m = n * wire_rate // man["rate"]
    if n * wire_rate % man["rate"]:
        sys.exit(f"period {n} at {man['rate']} Hz is not a whole number of "
                 f"samples at {wire_rate} Hz -- regenerate with "
                 f"--chain-rate {wire_rate}")
    lo, hi = active_region(a, args.margin, wire_rate)
    avail = (hi - lo) // m
    if avail < 2:
        sys.exit(f"need 2 whole periods ({2*m} frames) in the capture, have {hi-lo}")
    count = min(args.windows, avail)
    bins = np.array(man["bins"])
    amps = np.array(man["amplitudes"])
    xref = amps * np.exp(1j * np.array(man["phases_rad"]))
    spacing = man["bin_spacing_hz"]
    freqs = bins * spacing
    src_nyq_bin = n // 2

    chans = {}
    for ch, name in ((0, "left"), (1, "right")):
        specs = spectra(a[lo:hi, ch], m, count)
        ref = specs[0]
        y = np.mean(specs, axis=0)
        scale = float(np.abs(y[bins]).max()) or 1.0
        drift = (max(float(np.abs(s - ref).max()) for s in specs[1:]) / scale
                 if count > 1 else 0.0)

        gain = np.abs(y[bins]) / amps
        gain_db = np.array([db(g) for g in gain])
        anchor = int(np.argmin(np.abs(freqs - 1000.0)))
        rel_db = gain_db - gain_db[anchor]

        delay = estimate_delay(y, xref, bins, m)
        resid = y[bins] * np.conj(xref) * np.exp(2j * np.pi * bins * delay / m)
        dev = np.degrees(np.angle(resid))

        occupied = set()
        for k in bins:
            occupied.update((k - 1, k, k + 1))
        below = [k for k in range(1, src_nyq_bin) if k not in occupied]
        above = list(range(src_nyq_bin + 1, len(y)))
        pw = np.abs(y) ** 2 / 2
        sig = float(pw[bins].sum())

        def rel(idx):
            return db(math.sqrt(float(pw[list(idx)].sum()) / sig)) if len(idx) else -np.inf

        lo_bins = [k for k in below if k * spacing < 10000]
        hi_bins = [k for k in below if k * spacing >= 10000]
        worst_img = max(above, key=lambda k: pw[k]) if above else None

        chans[name] = {
            "gain_db": gain_db.tolist(),
            "rel_db": rel_db.tolist(),
            "phase_dev_deg": dev.tolist(),
            "bands": band_rows(freqs, rel_db, dev, man["rate"] / 2),
            "delay_ms": float(1000.0 * delay / wire_rate),
            "mean_gain_db": float(gain_db.mean()),
            "empty_below_10k_db": rel(lo_bins),
            "empty_above_10k_db": rel(hi_bins),
            "above_nyquist_db": rel(above),
            "worst_image_hz": float(worst_img * spacing) if worst_img else None,
            "period_drift": drift,
        }
    clipped = int(np.count_nonzero(np.abs(a[lo:hi].astype(np.int64)) >= FULL))
    return {"periods": count, "period_wire_samples": m,
            "clipped_samples": clipped, "channels": chans}


def analyse_overs(a, wire_rate, man, args):
    lo, hi = active_region(a, args.margin, wire_rate)
    seg = a[lo:hi].astype(np.float64) / FS32
    clipped = int(np.count_nonzero(np.abs(a[lo:hi].astype(np.int64)) >= FULL))
    f0 = man["freqs_hz"][0]
    out = {"clipped_samples": clipped,
           "clipped_pct": 100.0 * clipped / max(1, seg.size),
           "source_true_peak_dbfs": man.get("true_peak_dbfs"),
           "channels": {}}
    n = (len(seg) // 1024) * 1024
    for ch, name in ((0, "left"), (1, "right")):
        x = seg[:n, ch]
        spec = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
        f = np.fft.rfftfreq(n, 1 / wire_rate)
        k0 = int(np.argmin(np.abs(f - f0)))
        win = lambda k: spec[max(0, k - 4):k + 5].sum()
        fund = win(k0)
        harm = 0.0
        for h in range(2, 12):
            fh = h * f0
            if fh >= wire_rate / 2:
                break
            harm += win(int(np.argmin(np.abs(f - fh))))
        out["channels"][name] = {
            "sample_peak_dbfs": db(float(np.abs(x).max())),
            "thd_db": db(math.sqrt(harm / fund)) if fund > 0 else -np.inf,
        }
    return out


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("capture")
    p.add_argument("--tones", required=True, help="OUT.tones.json from the generator")
    p.add_argument("--rate", type=int, help="wire rate, if no PREFIX.json")
    p.add_argument("--windows", type=int, default=3, help="periods to analyse")
    p.add_argument("--margin", type=float, default=1.0,
                   help="seconds trimmed each end for settling (default 1.0)")
    p.add_argument("--max-hf-dev", type=float, default=0.10,
                   help="dB, gain re 1 kHz, up to 20 kHz (default 0.10)")
    p.add_argument("--max-image-db", type=float, default=-100.0,
                   help="dB re signal, above the source Nyquist (default -100)")
    p.add_argument("--json")
    a = p.parse_args()

    man = json.loads(Path(a.tones).read_text())
    cap, wire_rate, info = load_capture(a.capture, a.rate)

    print(f"capture       {a.capture}")
    print(f"material      {man['rate']} Hz / S{man['bits']}, mode {man['mode']}")
    print(f"wire          {wire_rate} Hz"
          + ("  (RESAMPLED in the chain)" if wire_rate != man["rate"] else "  (rate matched)"))

    if man["mode"] == "overs":
        r = analyse_overs(cap, wire_rate, man, a)
        print(f"source true peak {r['source_true_peak_dbfs']:+.2f} dBFS "
              f"(sample peak {man['sample_peak_dbfs']:+.2f} dBFS)")
        for name, c in r["channels"].items():
            print(f"  {name:<5} wire sample peak {c['sample_peak_dbfs']:+.2f} dBFS   "
                  f"THD {c['thd_db']:.1f} dB")
        print(f"  clipped at the wire: {r['clipped_samples']} samples "
              f"({r['clipped_pct']:.3f} %)")
        ok = r["clipped_samples"] == 0
        print(f"\n{'PASS' if ok else 'FAIL'}: "
              + ("no sample reached full scale"
                 if ok else "the chain clamped intersample overshoot"))
        verdict = {"pass": ok, **r}
    else:
        r = analyse_multitone(cap, wire_rate, man, a)
        print(f"analysed      {r['periods']} periods of {r['period_wire_samples']} "
              f"frames ({len(man['bins'])} tones)")
        fails = []
        for name, c in r["channels"].items():
            print(f"\n{name}:  mean gain {c['mean_gain_db']:+.3f} dB")
            print(f"  {'band':<16}{'tones':>6}{'mean':>9}{'worst':>9}"
                  f"{'at':>11}{'phase':>9}")
            for b in c["bands"]:
                tag = "  (resampler transition)" if b["transition"] else ""
                print(f"  {b['band']:<16}{b['tones']:>6}{b['mean_db']:>+9.3f}"
                      f"{b['worst_db']:>+9.3f}{b['worst_hz']:>10.0f}Hz"
                      f"{b['max_phase_dev_deg']:>8.2f}d{tag}")
                if not b["transition"] and abs(b["worst_db"]) > a.max_hf_dev:
                    fails.append(f"{name} {b['band']}: {b['worst_db']:+.3f} dB re 1 kHz "
                                 f"at {b['worst_hz']:.0f} Hz")
            print(f"  empty bins   <10 kHz {c['empty_below_10k_db']:8.1f} dB"
                  f"   >=10 kHz {c['empty_above_10k_db']:8.1f} dB")
            img = (f" (worst at {c['worst_image_hz']:.0f} Hz)"
                   if c["worst_image_hz"] else "")
            print(f"  above source Nyquist {c['above_nyquist_db']:8.1f} dB{img}")
            print(f"  offset {c['delay_ms']:.3f} ms (mod period)"
                  f"   period-to-period drift {db(c['period_drift']):.1f} dB")
            if c["above_nyquist_db"] > a.max_image_db:
                fails.append(f"{name}: image energy {c['above_nyquist_db']:.1f} dB")
        if r["clipped_samples"]:
            fails.append(f"{r['clipped_samples']} samples clamped at full scale")
        print()
        if fails:
            print("FAIL")
            for f in fails:
                print(f"  {f}")
        else:
            print(f"PASS: flat to {a.max_hf_dev} dB re 1 kHz up to 20 kHz, "
                  f"images below {a.max_image_db:g} dB, nothing clipped")
        verdict = {"pass": not fails, "failures": fails, **r}

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"capture": str(a.capture), "wire_rate": wire_rate,
             "manifest": man, "result": verdict}, indent=2) + "\n")
        print(f"\nwrote {a.json}")
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
