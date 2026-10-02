#!/usr/bin/env python3
"""bass_depth.py — does an orchestral recording leave the double basses at the
back of the stage, or spot-mic them into the listener's lap?

v1: the unaligned spot-mic detector.

A bass spot microphone a metre from the section, mixed in without being
delayed to match its distance from the main array, makes every bass note reach
the mix twice: once through the spot, and Δ later through the main pair
(Δ ≈ distance / 343 m/s, typically 5–40 ms). That double arrival is what pulls
the basses forward: dry, sharp, and arriving before their own hall image.

The difficulty is that bass *pitch periods* (15–35 ms for E1..C2) sit in the
same lag range as Δ, so a plain autocorrelation or cepstrum finds the pitch.
Two estimators sidestep that, each in its own way:

  GCC   (decides) Inter-channel generalized cross-correlation (PHAT, coherence
        weighted) of the L/R cross-spectrum *averaged over all bass frames*.
        The source spectrum cancels in the phase, so pitch drops out and
        what remains is the L→R transfer: main peak at the main-pair ITD, and
        side peaks at ±Δ when a spot and the main pair both carry the basses.

  AC    (corroborates) Mono autocorrelation of the spectrally whitened bass band, averaged
        (as analytic signals) over bass frames while masking, per frame, the
        lags that are multiples of that frame's pitch period. Pitch peaks
        move from note to note and are masked; a fixed microphone delay
        stays put and adds up coherently. Weaker than GCC: within one note
        the spectrum only exists on the harmonic lines, which samples the
        comb once per harmonic and leaves Δ ambiguous modulo the period.

Both estimators pool frames per semitone and average the per-note curves
while masking each note's period multiples: a harmonic source repeats every
lag-domain feature (even GCC's main peak) at k·T0.

Both are compared against an orchestra reference (non-bass frames, 300–4000
Hz): a peak that the whole orchestra shares is hall/array geometry (e.g. a
stage-wall reflection), not a bass spot.

Bass frames are found automatically: a harmonic comb whose f0 lies below the
cello's lowest note (C2, 65.4 Hz) dominating 25–1500 Hz. Tuba and
contrabassoon pass this test too; use --segments to point at passages you
know are exposed basses.

Also reported, for information: bass-band L/R coherence and level
difference against the orchestra reference. A time-aligned spot leaves no
double arrival; catching it is v2's job (direct-to-reverberant ratio and
attack sharpness of the basses against the violins).

Thresholds are uncalibrated until run against recordings labelled by ear.

Usage:
  bass_depth.py FILE [FILE...] [--segments 4:12-4:30,5:02-5:20] [--plot DIR]
  bass_depth.py --selftest

Needs numpy, scipy and ffmpeg on PATH; matplotlib only for --plot.
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import find_peaks

SR = 16000          # analysis rate: everything of interest is below 4 kHz
NFFT = 4096         # 256 ms frames, 3.9 Hz bins: resolves 30 Hz fundamentals
HOP = 1024
C_SOUND = 343.0
EPS = 1e-20

BASS_BAND = (150.0, 2500.0)   # bass harmonics and bow noise, above the
                              # long low-frequency reverb
REF_BAND = (300.0, 4000.0)    # upper orchestra
SAL_BAND = (25.0, 1500.0)     # where bass dominance is judged
MAIN_ITD_MS = 2.0             # main-array ITD search, |τ| below this

NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
WIN = np.hanning(NFFT)
# Overlap of the analysis window with itself at lag τ: lag curves computed
# from single frames are attenuated by this, so divide it out.
WIN_AC = np.correlate(WIN, WIN, "full")[NFFT - 1:] / np.sum(WIN ** 2)


def note_name(f):
    m = int(round(69 + 12 * math.log2(f / 440.0)))
    return f"{NOTE[m % 12]}{m // 12 - 1}"


def fmt_time(s):
    return f"{int(s // 60)}:{s % 60:04.1f}"


def parse_time(t):
    parts = t.strip().split(":")
    s = 0.0
    for p in parts:
        s = s * 60 + float(p)
    return s


def load_audio(path):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0",
           "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout
    x = np.frombuffer(raw, dtype="<f4").reshape(-1, 2).T.astype(np.float64)
    return x


def spectra(ch, fidx):
    idx = fidx[:, None] * HOP + np.arange(NFFT)[None, :]
    return np.fft.rfft(ch[idx] * WIN, axis=1)


def band_taper(lo, hi, df, edge=50.0):
    """Band mask with raised-cosine edges, to keep lag-domain ringing low."""
    f = np.arange(NFFT // 2 + 1) * df
    w = np.zeros_like(f)
    inner = (f >= lo + edge) & (f <= hi - edge)
    w[inner] = 1.0
    lo_e = (f >= lo) & (f < lo + edge)
    w[lo_e] = 0.5 - 0.5 * np.cos(np.pi * (f[lo_e] - lo) / edge)
    hi_e = (f > hi - edge) & (f <= hi)
    w[hi_e] = 0.5 - 0.5 * np.cos(np.pi * (hi - f[hi_e]) / edge)
    return w


def analytic_lags(W, maxlag):
    """Analytic lag curve of a one-sided spectrum: real part is the ordinary
    correlation, magnitude its envelope (polarity/carrier-phase agnostic)."""
    full = np.zeros(W.shape[:-1] + (NFFT,), dtype=np.complex128)
    full[..., :NFFT // 2 + 1] = W
    c = np.fft.ifft(full, axis=-1) * 2.0
    return c[..., :maxlag + 1], c[..., NFFT - maxlag:]


# ---------------------------------------------------------------- pass 1

def bass_salience(mono, nfr, fmin, fmax, chunk=256, nlow=6, prom_db=10.0):
    """Per frame: the low f0 whose first `nlow` harmonics carry the largest
    share of the 25–1500 Hz energy (that share is the 'dominance'), how many
    of those harmonics stand out as peaks, and the frame energy.

    Only the first harmonics count: a 30–65 Hz comb also fits every partial
    of a higher note (they all lie on its subharmonic combs). A subharmonic
    comb (a cello at 3·f0) only lands on some of its first harmonics, while a
    bass note puts a peak on most of them."""
    df = SR / NFFT
    lo, hi = int(math.ceil(SAL_BAND[0] / df)), int(SAL_BAND[1] / df)
    m_lo = 12 * math.log2(fmin / 440.0) + 69
    m_hi = 12 * math.log2(fmax / 440.0) + 69
    cands = 440.0 * 2 ** ((np.arange(m_lo, m_hi + 1e-9, 0.1) - 69) / 12)
    h = np.arange(1, nlow + 1)
    B = np.rint(cands[:, None] * h[None, :] / df).astype(int)
    odd = (h % 2) == 1

    f0 = np.zeros(nfr)
    dom = np.zeros(nfr)
    npeaks = np.zeros(nfr, int)
    energy = np.zeros(nfr)
    for s in range(0, nfr, chunk):
        fidx = np.arange(s, min(nfr, s + chunk))
        X = spectra(mono, fidx)
        P = X.real ** 2 + X.imag ** 2
        tot = P[:, lo:hi + 1].sum(1) + EPS
        P3 = uniform_filter1d(P, 3, axis=1, mode="constant") * 3.0
        floor = median_filter(P[:, :hi + 32], size=(1, 31), mode="nearest")
        hv = P3[:, B]
        prom = hv > 3.0 * floor[:, B] * 10 ** (prom_db / 10)
        npk = prom.sum(2)
        hs = hv.sum(2)
        d = hs / tot[:, None]
        of = hv[:, :, odd].sum(2) / (hs + EPS)
        # A subharmonic of a bass note (f0/2) catches the same energy on half
        # the comb with the odd half empty: rank by energy × odd presence ×
        # comb completeness.
        score = d * np.clip(of / 0.3, 0.0, 1.0) * npk
        best = score.argmax(1)
        r = np.arange(len(fidx))
        f0[fidx] = cands[best]
        dom[fidx] = d[r, best]
        npeaks[fidx] = npk[r, best]
        energy[fidx] = tot
    return f0, dom, npeaks, energy


def runs(mask):
    """[(start, end_exclusive)] of True runs."""
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


# ---------------------------------------------------------------- pass 2

def lag_peaks(env, lags_ms, window, exclude=None):
    """Peaks of an envelope curve inside |lag| ∈ window, with a robust z."""
    a = np.abs(lags_ms)
    win = (a >= window[0]) & (a <= window[1])
    if exclude is not None:
        win &= ~exclude
    if win.sum() < 10:
        return [], 0.0, 1.0
    vals = env[win]
    med = float(np.median(vals))
    mad = float(np.median(np.abs(vals - med))) * 1.4826 + EPS
    e = np.where(win, env, 0.0)
    pk, _ = find_peaks(e, distance=max(1, int(0.001 * SR)))
    pk = [p for p in pk if win[p]]
    pk.sort(key=lambda p: -e[p])
    out = [{"lag_ms": float(lags_ms[p]), "env": float(env[p]),
            "z": (float(env[p]) - med) / mad} for p in pk[:3]]
    return out, med, mad


def new_spectra():
    z = np.zeros(NFFT // 2 + 1)
    return {"LR": z.astype(complex), "LL": z.copy(), "RR": z.copy(),
            "MM": z.copy(), "n": 0}


def accumulate(S, XL, XR):
    S["LR"] += (np.conj(XL) * XR).sum(0)
    S["LL"] += (np.abs(XL) ** 2).sum(0)
    S["RR"] += (np.abs(XR) ** 2).sum(0)
    S["MM"] += (np.abs(0.5 * (XL + XR)) ** 2).sum(0)
    S["n"] += XL.shape[0]


def gcc_curve(S, taper, maxlag):
    """Coherence-weighted PHAT of an averaged cross-spectrum, over
    −maxlag..+maxlag (+ = R later). Complex: |c| is the envelope."""
    coh = np.abs(S["LR"]) ** 2 / (S["LL"] * S["RR"] + EPS)
    W = S["LR"] / (np.abs(S["LR"]) + EPS) * coh * taper
    W /= np.abs(W).sum() + EPS
    pos, neg = analytic_lags(W, maxlag)
    lags = np.arange(-maxlag, maxlag + 1)
    return np.r_[neg, pos] / WIN_AC[np.abs(lags)]


def ac_curve(S, taper, smooth_bins, maxlag):
    """Autocorrelation of the whitened mono power spectrum, 0..maxlag,
    normalized to 1 at lag 0."""
    P = S["MM"]
    env = uniform_filter1d(P, smooth_bins, mode="nearest") + EPS
    pos, _ = analytic_lags(P / env * taper, maxlag)
    pos = pos / (pos[0].real + EPS)
    return pos / WIN_AC[:maxlag + 1]


def band_stats(S, taper):
    w = np.sqrt(S["LL"] * S["RR"]) * taper
    coh = np.abs(S["LR"]) / (np.sqrt(S["LL"] * S["RR"]) + EPS)
    return {
        "coherence": float((coh * w).sum() / (w.sum() + EPS)),
        "ild_db": float(10 * np.log10(((S["RR"] * taper).sum() + EPS)
                                      / ((S["LL"] * taper).sum() + EPS))),
    }


def pitch_masked_mean(curves, f0s, weights, lags, tol):
    """Average complex lag curves of different notes, skipping for each note
    the lags within tol (samples) of a multiple of its period. A harmonic
    source only has energy on its harmonic lines, so each note's curve
    repeats every period; only a delay fixed by the geometry survives the
    average. Returns (mean, coverage)."""
    per = np.abs(lags)[None, :] * f0s[:, None] / SR
    kk = np.rint(per)
    dist = np.abs(per - kk) * SR / f0s[:, None]
    m = ((dist > tol) | (kk == 0)) * weights[:, None]
    mean = (curves * m).sum(0) / (m.sum(0) + EPS)
    return mean, m.sum(0) / weights.sum()


def peak_rel_to_ref(peaks, lags_ms, ref_env, ref_norm):
    for p in peaks:
        j = int(np.argmin(np.abs(lags_ms - p["lag_ms"])))
        k = slice(max(0, j - 8), j + 9)
        p["ref_rel_db"] = float(20 * np.log10(ref_env[k].max()
                                              / (ref_norm + EPS) + EPS))


def analyze(x, opts, name="input"):
    L, R = x
    n = L.shape[0]
    nfr = 1 + (n - NFFT) // HOP if n >= NFFT else 0
    if nfr < 20:
        raise SystemExit(f"{name}: too short")
    df = SR / NFFT
    mono = 0.5 * (L + R)
    maxlag = int(opts.max_lag / 1000.0 * SR)
    t_frame = (np.arange(nfr) * HOP + NFFT / 2) / SR

    f0, dom, npk, energy = bass_salience(mono, nfr, opts.fmin, opts.fmax)
    gate = energy > np.percentile(energy, 99) * 10 ** (-opts.gate_db / 10)

    if opts.segments:
        bass = np.zeros(nfr, bool)
        for a, b in opts.segments:
            bass |= (t_frame >= a) & (t_frame <= b)
        bass &= gate
    else:
        cand = gate & (dom >= opts.dom) & (npk >= opts.min_peaks)
        cand = median_filter(cand.astype(np.int8), 3).astype(bool)
        bass = np.zeros(nfr, bool)
        for a, b in runs(cand):
            if b - a >= opts.min_frames:
                bass[a:b] = True
    ref = gate & ~bass & (dom < opts.dom * 0.5)

    # Bass frames are pooled per semitone: within a note the source spectrum
    # is stable, across notes the pitch-period artefacts move.
    semi = np.rint(12 * np.log2(f0 / 440.0)).astype(int)
    groups = {}
    Sr = new_spectra()
    sel = np.flatnonzero(bass | ref)
    for s in range(0, len(sel), 256):
        fidx = sel[s:s + 256]
        XL, XR = spectra(L, fidx), spectra(R, fidx)
        mr = ref[fidx]
        if mr.any():
            accumulate(Sr, XL[mr], XR[mr])
        for k in np.unique(semi[fidx][bass[fidx]]):
            m = bass[fidx] & (semi[fidx] == k)
            accumulate(groups.setdefault(int(k), new_spectra()), XL[m], XR[m])

    nb = int(bass.sum())
    res = {
        "file": name,
        "duration_s": n / SR,
        "bass_frames": nb,
        "bass_seconds": nb * HOP / SR,
        "ref_frames": int(ref.sum()),
        "segments": [],
        "timeline": {"t": t_frame.tolist(), "dom": dom.tolist(),
                     "bass": bass.tolist()},
    }
    for a, b in runs(bass):
        res["segments"].append({"start": float(t_frame[a] - NFFT / 2 / SR),
                                "end": float(t_frame[b - 1] + NFFT / 2 / SR),
                                "note": note_name(float(np.median(f0[a:b])))})
    groups = {k: g for k, g in groups.items() if g["n"] >= opts.min_note_frames}
    res["pitch_variety"] = len(groups)
    if nb < opts.min_bass_frames or len(groups) < opts.min_notes:
        res["verdict"] = "insufficient"
        res["verdict_text"] = (
            f"{nb} bass-dominated frames ({res['bass_seconds']:.1f}s) on "
            f"{len(groups)} usable notes: not enough exposed bass with "
            "enough pitch variety; try --segments")
        return res

    tb = band_taper(*BASS_BAND, df)
    tr = band_taper(*REF_BAND, df)
    smooth = int(200.0 / df)
    Sb = new_spectra()
    for g in groups.values():
        for k in ("LR", "LL", "RR", "MM"):
            Sb[k] += g[k]
    res["bass_stats"] = band_stats(Sb, tb)
    if Sr["n"]:
        res["ref_stats"] = band_stats(Sr, tr)

    gf0 = np.array([440.0 * 2 ** (k / 12) for k in groups])
    gw = np.sqrt([g["n"] for g in groups.values()])
    tol = opts.pitch_tol / 1000.0 * SR

    # --- GCC
    lags = np.arange(-maxlag, maxlag + 1)
    lags_ms = lags / SR * 1000.0
    gc = np.array([gcc_curve(g, tb, maxlag) for g in groups.values()])
    near = np.abs(lags_ms) <= MAIN_ITD_MS
    pooled = np.abs((gc * gw[:, None]).sum(0))
    main_i = int(np.argmax(np.where(near, pooled, 0)))
    res["main_itd_ms"] = float(lags_ms[main_i])
    # The main peak repeats at k·T0 ± ITD: widen the mask by the ITD.
    cb, gcover = pitch_masked_mean(gc, gf0, gw, lags,
                                   tol + abs(lags[main_i]))
    envb = np.abs(cb)
    main_env = envb[main_i]
    pk, _, _ = lag_peaks(envb, lags_ms, (opts.min_lag, opts.max_lag),
                         exclude=gcover < opts.min_cover)
    for p in pk:
        p["rel_db"] = 20 * np.log10(p["env"] / (main_env + EPS) + EPS)
    envr = None
    if Sr["n"]:
        envr = np.abs(gcc_curve(Sr, tr, maxlag))
        peak_rel_to_ref(pk, lags_ms, envr, envr[near].max())
    res["gcc"] = {"peaks": pk}

    # --- AC
    alags = np.arange(maxlag + 1)
    ac_ms = alags / SR * 1000.0
    ac = np.array([ac_curve(g, tb, smooth, maxlag) for g in groups.values()])
    A, acover = pitch_masked_mean(ac, gf0, gw, alags, tol)
    envA = np.abs(A)
    pka, _, _ = lag_peaks(envA, ac_ms, (opts.min_lag, opts.max_lag),
                          exclude=acover < opts.min_cover)
    for p in pka:
        p["rel_db"] = 20 * np.log10(p["env"] + EPS)
    envAr = None
    if Sr["n"]:
        envAr = np.abs(ac_curve(Sr, tr, smooth, maxlag))
        peak_rel_to_ref(pka, ac_ms, envAr, 1.0)
    res["ac"] = {"peaks": pka}

    res["curves"] = {"gcc_lags_ms": lags_ms.tolist(),
                     "gcc_bass": envb.tolist(),
                     "gcc_ref": envr.tolist() if envr is not None else None,
                     "gcc_cover": gcover.tolist(),
                     "ac_lags_ms": ac_ms.tolist(),
                     "ac_bass": envA.tolist(),
                     "ac_ref": envAr.tolist() if envAr is not None else None,
                     "ac_cover": acover.tolist()}
    verdict(res, opts)
    return res


def _hit(p, z, min_rel, spec_db):
    if p["z"] < z or p["rel_db"] < min_rel:
        return False
    return p["rel_db"] - p.get("ref_rel_db", -99.0) >= spec_db


def verdict(res, opts):
    """GCC decides; AC, the weaker estimator, can only corroborate (or, on
    its own, raise a 'possible')."""
    g = [p for p in res["gcc"]["peaks"]
         if _hit(p, opts.z, opts.gcc_min_db, opts.specific_db)]
    a_all = res["ac"]["peaks"]
    a = [p for p in a_all if _hit(p, opts.z, opts.ac_min_db, opts.specific_db)]
    if g:
        pg = g[0]
        d = abs(pg["lag_ms"])
        # GCC shows the double arrival at +(Δ+ITD) and −Δ: compare |τ|.
        corr = [p for p in a_all if p["z"] >= opts.ac_corroborate_z
                and abs(p["lag_ms"] - d) <= 1.5]
        res["delay_ms"] = d
        res["verdict"] = "double-arrival" if corr else "likely"
        res["verdict_text"] = (
            f"double arrival at {d:.1f} ms (≈{d * C_SOUND / 1000:.1f} m "
            "path difference): "
            + ("GCC and AC agree" if corr else "GCC only")
            + " — the basses reach the mix through a spot mic before the "
              "main pair: likely an unaligned bass spot")
    elif a:
        d = a[0]["lag_ms"]
        res["delay_ms"] = d
        res["verdict"] = "possible"
        res["verdict_text"] = (
            f"possible double arrival at {d:.1f} ms, AC only (weak "
            "estimator) — listen to the listed segments")
    else:
        res["verdict"] = "none"
        res["verdict_text"] = "no bass-specific double arrival detected"


# ---------------------------------------------------------------- output

def report(res, nseg=12):
    print(f"== {res['file']}")
    print(f"   {fmt_time(res['duration_s'])} long; bass-dominated "
          f"{res['bass_seconds']:.1f}s in {len(res['segments'])} segment(s)"
          + (f", {res['pitch_variety']} distinct notes"
             if "pitch_variety" in res else ""))
    for s in res["segments"][:nseg]:
        print(f"     {fmt_time(s['start']):>8} – {fmt_time(s['end']):<8} "
              f"~{s['note']}")
    if len(res["segments"]) > nseg:
        print(f"     … {len(res['segments']) - nseg} more")
    if res["verdict"] == "insufficient":
        print(f"   VERDICT: {res['verdict_text']}\n")
        return
    b, r = res["bass_stats"], res.get("ref_stats")
    print(f"   bass image: coherence {b['coherence']:.2f}, "
          f"R−L {b['ild_db']:+.1f} dB, main ITD {res['main_itd_ms']:+.2f} ms")
    if r:
        print(f"   orchestra:  coherence {r['coherence']:.2f}, "
              f"R−L {r['ild_db']:+.1f} dB")

    def pl(tag, peaks):
        if not peaks:
            print(f"   {tag}: no peaks in window")
        for p in peaks:
            ref = (f", orch {p['ref_rel_db']:+.1f} dB"
                   if "ref_rel_db" in p else "")
            print(f"   {tag}: {p['lag_ms']:+7.2f} ms  {p['rel_db']:+6.1f} dB"
                  f"  z {p['z']:5.1f}{ref}")
            tag = " " * len(tag)
    pl("GCC", res["gcc"]["peaks"])
    pl("AC ", res["ac"]["peaks"])
    print(f"   VERDICT: {res['verdict_text']}\n")


def plot(res, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(3, 1, figsize=(11, 9))
    tl = res["timeline"]
    t = np.array(tl["t"])
    ax[0].plot(t, tl["dom"], lw=0.6, color="0.4")
    ax[0].fill_between(t, 0, 1, where=np.array(tl["bass"]), color="C1",
                       alpha=0.3, step="mid", label="bass frames")
    ax[0].set_ylim(-0.1, 1)
    ax[0].set_ylabel("low-f0 comb dominance")
    ax[0].set_xlabel("time (s)")
    ax[0].legend(loc="upper right")
    ax[0].set_title(os.path.basename(res["file"]) + " — " + res["verdict"])
    c = res.get("curves")
    if c:
        def db(v):
            v = np.asarray(v)
            return 20 * np.log10(v / v.max() + 1e-6)
        ax[1].plot(c["gcc_lags_ms"], db(c["gcc_bass"]), label="bass")
        if c["gcc_ref"]:
            ax[1].plot(c["gcc_lags_ms"], db(c["gcc_ref"]), alpha=0.6,
                       label="orchestra ref")
        ax[1].set_ylabel("GCC env (dB)")
        ax[1].set_xlabel("lag, + = R later (ms)")
        ax[1].set_ylim(-50, 3)
        ax[1].legend()
        ax[2].plot(c["ac_lags_ms"], db(c["ac_bass"]), label="bass (masked)")
        if c["ac_ref"]:
            ax[2].plot(c["ac_lags_ms"], db(c["ac_ref"]), alpha=0.6,
                       label="orchestra ref")
        ax[2].plot(c["ac_lags_ms"], 20 * np.log10(
            np.asarray(c["ac_cover"]) + 1e-6) - 40, lw=0.5, color="0.6",
            label="pitch-mask coverage (−40 dB = 1)")
        ax[2].set_ylabel("AC env (dB)")
        ax[2].set_xlabel("lag (ms)")
        ax[2].set_ylim(-60, 3)
        ax[2].legend(loc="upper right", fontsize="small")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------- selftest

def synth(case, seed=1, dur=90.0, delta_ms=25.0, bass_drr_db=-4.0,
          upper_gain=2.0):
    """Stage-right basses under a hall, with an upper-string line that comes
    and goes. case: natural | spot | spot-aligned."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t_all = np.arange(n) / SR

    def line(f_lo_midi, f_hi_midi, fmax_h, weak_fund, gaps):
        out = np.zeros(n)
        pos = 0
        while pos < n:
            f0 = 440 * 2 ** ((rng.integers(f_lo_midi, f_hi_midi + 1) - 69)
                             / 12)
            ln = int(rng.uniform(0.25, 0.8) * SR)
            seg = min(ln, n - pos)
            tt = np.arange(seg) / SR
            pizz = rng.random() < 0.5
            env = (np.minimum(1, tt / 0.004) * np.exp(-tt / 0.3) if pizz
                   else np.minimum(1, tt / 0.06)
                   * np.minimum(1, (seg / SR - tt) / 0.05))
            note = np.zeros(seg)
            for h in range(1, int(fmax_h / f0) + 1):
                a = (0.3 if (h == 1 and weak_fund) else 1.0) / h
                note += a * np.sin(2 * np.pi * h * f0 * tt
                                   + rng.uniform(0, 2 * np.pi))
            out[pos:pos + seg] += env * note
            pos += ln + (int(rng.uniform(0, 0.3) * SR) if gaps else 0)
        return out

    bass = line(28, 36, 3000, True, True)
    # Cellos an octave above the basses plus upper strings, in alternate 5 s
    # blocks: tutti (where the basses are a minority) vs exposed basses.
    upper = line(55, 81, 4000, False, False) + 0.7 * line(40, 55, 3000,
                                                          False, False)
    upper *= (np.floor(t_all / 5.0) % 2 == 0)
    upper *= upper_gain

    def reverb(sig, drr_db, rt60=2.0, pre=0.015):
        m = int(2.5 * SR)
        tt = np.arange(m) / SR
        out = []
        for _ in range(2):
            ir = rng.standard_normal(m) * np.exp(-6.91 * tt / rt60)
            ir[:int(pre * SR)] = 0
            ir /= np.sqrt(np.sum(ir ** 2))
            ir *= 10 ** (-drr_db / 20)
            out.append(np.fft.irfft(np.fft.rfft(sig, n + m)
                                    * np.fft.rfft(ir, n + m))[:n])
        return out

    def delay(sig, sec):
        k = int(round(sec * SR))
        return np.r_[np.zeros(k), sig[:n - k]]

    d = delta_ms / 1000.0
    # Main pair: basses at the back right, reverberant; ITD 0.6 ms (R first).
    rb = reverb(bass, bass_drr_db)
    L = 0.7 * delay(bass, d + 0.0006) + delay(rb[0], d)
    R = 0.9 * delay(bass, d) + delay(rb[1], d)
    ru = reverb(upper, 2.0)
    L += 0.9 * delay(upper, 0.010) + delay(ru[0], 0.010)
    R += 0.6 * delay(upper, 0.0103) + delay(ru[1], 0.010)
    if case in ("spot", "spot-aligned"):
        sp = delay(bass, d) if case == "spot-aligned" else bass
        L += 0.35 * sp
        R += 1.0 * sp
    noise = rng.standard_normal((2, n)) * 1e-3
    x = np.vstack([L, R]) + noise
    return x / np.abs(x).max() * 0.5


def selftest(opts):
    ok = True
    expect = {"natural": ("none",),
              "spot": ("double-arrival", "likely"),
              "spot-aligned": ("none",)}
    for case, want in expect.items():
        res = analyze(synth(case), opts, name=f"synthetic:{case}")
        report(res)
        good = res["verdict"] in want
        if case == "spot" and good:
            # GCC sees −(Δ + main-pair delay on L) = 25.6 ms.
            good = abs(res["delay_ms"] - 25.3) < 1.5
        print(f"   selftest {case}: {'PASS' if good else 'FAIL'}\n")
        ok &= good
    return 0 if ok else 1


# ---------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(
        description="Detect a bass spot mic mixed in without delay "
                    "(double arrival) in orchestral recordings.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--segments", help="analyse only these passages, "
                    "e.g. 4:12-4:30,5:02-5:20 (overrides auto-detection)")
    ap.add_argument("--plot", metavar="DIR", help="write a PNG per file here")
    ap.add_argument("--json", metavar="FILE", help="write results as JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="run on synthetic natural / spot / aligned-spot "
                         "stages")
    g = ap.add_argument_group("tuning")
    g.add_argument("--fmin", type=float, default=29.0, help="lowest bass f0")
    g.add_argument("--fmax", type=float, default=66.0,
                   help="highest f0 counted as bass (cello bottom is 65.4)")
    g.add_argument("--dom", type=float, default=0.5,
                   help="share of 25–1500 Hz energy on the first 6 "
                        "harmonics of a bass frame's f0")
    g.add_argument("--min-peaks", type=int, default=5,
                   help="of those 6 harmonics, how many must be peaks")
    g.add_argument("--gate-db", type=float, default=40.0,
                   help="ignore frames this far below the loud passages")
    g.add_argument("--min-frames", type=int, default=3)
    g.add_argument("--min-bass-frames", type=int, default=40)
    g.add_argument("--min-note-frames", type=int, default=4,
                   help="frames needed for a note to join the pitch average")
    g.add_argument("--min-notes", type=int, default=4,
                   help="distinct notes needed (pitch-period masking)")
    g.add_argument("--min-lag", type=float, default=5.0,
                   help="ms; below this the main-array peak's skirt and "
                        "floor bounces dominate (a spot 1.7 m away)")
    g.add_argument("--max-lag", type=float, default=60.0, help="ms")
    g.add_argument("--pitch-tol", type=float, default=1.0,
                   help="ms masked around each pitch-period multiple")
    g.add_argument("--min-cover", type=float, default=0.3,
                   help="AC lags masked in more frames than this are skipped")
    g.add_argument("--z", type=float, default=5.5,
                   help="robust z-score for a peak to count")
    g.add_argument("--gcc-min-db", type=float, default=-20.0)
    g.add_argument("--ac-min-db", type=float, default=-26.0)
    g.add_argument("--ac-corroborate-z", type=float, default=3.0,
                   help="AC peak strength that confirms a GCC detection")
    g.add_argument("--specific-db", type=float, default=6.0,
                   help="how far above the orchestra ref at the same lag")
    return ap


def main():
    ap = build_parser()
    opts = ap.parse_args()

    if opts.segments:
        segs = []
        for part in opts.segments.split(","):
            a, b = part.split("-")
            segs.append((parse_time(a), parse_time(b)))
        opts.segments = segs

    if opts.selftest:
        return selftest(opts)
    if not opts.files:
        ap.error("no input files (or --selftest)")

    results = []
    for f in opts.files:
        try:
            x = load_audio(f)
        except subprocess.CalledProcessError as e:
            print(f"{f}: ffmpeg failed ({e.returncode})", file=sys.stderr)
            continue
        if np.allclose(x[0], x[1]):
            print(f"{f}: mono (L == R): no stereo to analyse", file=sys.stderr)
            continue
        res = analyze(x, opts, name=f)
        report(res)
        if opts.plot:
            os.makedirs(opts.plot, exist_ok=True)
            out = os.path.join(opts.plot, os.path.splitext(
                os.path.basename(f))[0] + ".png")
            plot(res, out)
            print(f"   plot: {out}\n")
        results.append(res)
    if opts.json:
        for r in results:
            r.pop("timeline", None)
            r.pop("curves", None)
        with open(opts.json, "w") as fh:
            json.dump(results, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
