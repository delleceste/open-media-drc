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
lag-domain feature (even GCC's main peak) at k·T0. Each group also masks its
octave (T0/2) and the periods of other bass notes ringing into its frames;
peak scores are taken against the noise floor of however many notes remain
at each lag.

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

Bass contexts (reported per file): frames where the basses play alone,
with other sections, under a loud tutti, and pizzicato notes (16 ms comb
envelope: sharp rise, immediate decay), each with depth cues measured over
those frames: L/R coherence of the bass harmonics against the orchestra at
rest, and the fundamental against harmonics 2–6. Those cues combine into a
0–10 depth score with a green / yellow / red band (see depth_score), per file
and, with several files, for all of them together.

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

# Bumped whenever results change: the box re-analyses tracks stored under an
# older version, and calibration only compares results of one version.
VERSION = "1.5"

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
    out = [{"i": int(p), "lag_ms": float(lags_ms[p]), "env": float(env[p]),
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


def group_pitches(S, f0, fmin, fmax, df, rel_db):
    """Pitches whose periods repeat in a note group's lag curves: the note
    itself and, from the rest of the bass range, every note whose own
    harmonics (those not shared with f0's) stand within rel_db of f0's in the
    group's spectrum — a sustained or reverberating previous note bleeds into
    the frames of the next one. Each comes with its octave, whose period
    (T0/2) a strong second harmonic also imprints."""
    P = S["MM"]

    def harmonics(f, excl=None):
        out = []
        for h in range(1, 7):
            b = h * f / df
            if excl is not None:
                r = b / (excl / df)
                # Hann main lobe: ±2 bins of f0's own harmonic are f0's.
                if round(r) >= 1 and abs(r - round(r)) * excl / df <= 3.0:
                    continue
            i = int(round(b))
            out.append(P[max(i - 1, 0):i + 2].max())
        return out

    own = np.mean(harmonics(f0))
    pitches = [f0]
    m_lo = math.ceil(12 * math.log2(fmin / 440.0) + 69)
    m_hi = math.floor(12 * math.log2(fmax / 440.0) + 69)
    for m in range(m_lo, m_hi + 1):
        g = 440.0 * 2 ** ((m - 69) / 12)
        if abs(g / f0 - 1) < 0.02:
            continue
        h = harmonics(g, excl=f0)
        if len(h) >= 2 and np.mean(h) >= own * 10 ** (-rel_db / 10):
            pitches.append(g)
    return pitches + [2 * p for p in pitches]


def pitch_masked_mean(curves, pitches, weights, lags, tol):
    """Average complex lag curves of different notes, skipping for each note
    the lags within tol (samples) of a multiple of any period in its pitch
    list. A harmonic source only has energy on its harmonic lines, so each
    note's curve repeats every period; only a delay fixed by the geometry
    survives the average.

    Where notes are skipped, fewer curves are averaged and the noise floor
    rises; peak scores must be taken on mean / noise. Returns (mean,
    coverage, noise), noise relative to the unmasked average."""
    m = np.empty(curves.shape)
    for i, (ps, w) in enumerate(zip(pitches, weights)):
        keep = np.ones(len(lags), bool)
        for f in ps:
            per = np.abs(lags) * f / SR
            kk = np.rint(per)
            keep &= (np.abs(per - kk) * SR / f > tol) | (kk == 0)
        m[i] = keep * w
    s = m.sum(0)
    mean = (curves * m).sum(0) / (s + EPS)
    noise = np.where(s > 0, np.sqrt((m ** 2).sum(0)) / (s + EPS), np.inf)
    noise /= np.sqrt((weights ** 2).sum()) / weights.sum()
    return mean, s / weights.sum(), noise


def peak_rel_to_ref(peaks, lags_ms, ref_env, ref_norm):
    for p in peaks:
        j = int(np.argmin(np.abs(lags_ms - p["lag_ms"])))
        k = slice(max(0, j - 8), j + 9)
        p["ref_rel_db"] = float(20 * np.log10(ref_env[k].max()
                                              / (ref_norm + EPS) + EPS))


# ---------------------------------------------------------------- contexts

CTX_BAND = (80.0, 700.0)   # band of the context coherence cue
CONTEXTS = ("solo", "with-sections", "tutti", "pizzicato")


PZ_NFFT, PZ_HOP = 1024, 256   # 64 ms window, 16 ms hop for attacks


def comb_envelope(mono, f0, present):
    """Bass comb energy (harmonics 2–6 of each coarse frame's f0) on a
    16 ms grid, in dB; NaN where the basses are not playing."""
    nf = 1 + (len(mono) - PZ_NFFT) // PZ_HOP
    df = SR / PZ_NFFT
    w = np.hanning(PZ_NFFT)
    centre = (np.arange(nf) * PZ_HOP + PZ_NFFT / 2) / SR
    ci = np.clip(((centre * SR - NFFT / 2) / HOP).round().astype(int),
                 0, len(f0) - 1)
    # A coarse frame straddling an onset is rarely 'present' itself: take
    # the note from the first present frame up to 3 frames (192 ms) later.
    src = np.full(len(f0), -1)
    for d in (3, 2, 1, 0):
        sh = np.full(len(f0), False)
        sh[:len(f0) - d] = present[d:]
        src[sh] = (np.arange(len(f0)) + d)[sh]
    fi = src[ci]
    env = np.full(nf, np.nan)
    on = np.flatnonzero(fi >= 0)
    for s in range(0, len(on), 2048):
        k = on[s:s + 2048]
        idx = k[:, None] * PZ_HOP + np.arange(PZ_NFFT)[None, :]
        P = np.abs(np.fft.rfft(mono[idx] * w, axis=1)) ** 2
        b = np.rint(f0[fi[k]][:, None] * np.arange(2, 7)[None, :] / df)
        b = b.astype(int)
        env[k] = 10 * np.log10(P[np.arange(len(k))[:, None], b].sum(1) + EPS)
    return env, ci


def classify_contexts(mono, f0, dom, npk, energy, gate, opts, pizz_comb=None):
    """Per frame: are the basses playing, and in which setting?

      solo           the bass comb carries ≥ --solo-dom of 25–1500 Hz
      tutti          basses audible under a loud orchestra (comb may miss
                     some harmonics: --tutti-min-peaks)
      with-sections  basses audible with other instruments, not tutti
      pizzicato      overlay: on the 16 ms comb envelope, a rise of
                     --pizz-rise within 64 ms, then a decay that starts at
                     once (−1.5 dB by 80 ms: no bowed plateau or swell),
                     never re-rises by 2 dB, and reaches --pizz-drop by
                     320 ms (the hall holds a low pizzicato up).  Searched
                     up to --pizz-fmax (G2) on its own comb (`pizz_comb`:
                     f0, dom, npk): plucked bass lines sit higher than
                     bowed ones (Mahler 2/II, Rattle: the plucks at 19 and
                     23 s are E♭2), so they may include cellos above C2.

    Thresholds are first guesses; the report lists passages per context so
    they can be checked by ear."""
    nfr = len(f0)
    loud = energy >= np.percentile(energy[gate], 70) if gate.any() else gate
    # Under a loud orchestra some bass harmonics drown: accept a sparser comb.
    present = gate & (dom >= opts.present_dom) & (
        (npk >= opts.min_peaks) | (loud & (npk >= opts.tutti_min_peaks)))
    present = median_filter(present.astype(np.int8), 3).astype(bool)
    solo = present & (dom >= opts.solo_dom)
    tutti = present & ~solo & (dom < opts.dom) & loud
    with_s = present & ~solo & ~tutti

    pf0, pdom, pnpk = pizz_comb if pizz_comb is not None else (f0, dom, npk)
    ppresent = gate & (pdom >= opts.present_dom) & (
        (pnpk >= opts.min_peaks) | (loud & (pnpk >= opts.tutti_min_peaks)))
    ppresent = median_filter(ppresent.astype(np.int8), 3).astype(bool)
    env, ci = comb_envelope(mono, pf0, ppresent)
    pizz = np.zeros(nfr, bool)
    onsets = []
    i, n = 4, len(env)
    while i < n - 21:
        pk = i + int(np.nanargmax(env[i:i + 4])) if np.isfinite(
            env[i:i + 4]).any() else i
        base = env[i - 4]
        e = env[pk:pk + 21]
        if (np.isfinite(base) and np.isfinite(e).all()
                and env[pk] - base >= opts.pizz_rise
                and e[0] - e[5] >= 1.5
                and e[0] - e[20] >= opts.pizz_drop
                and np.all(np.diff(e) <= 2.0)):
            onsets.append(pk)
            pizz[ci[pk]:ci[min(pk + 20, n - 1)] + 1] = True
            i = pk + 20
        else:
            i += 1
    return ({"solo": solo, "with-sections": with_s, "tutti": tutti,
             "pizzicato": pizz}, onsets, env)


def _coh(acc):
    w = np.sqrt(acc["ll"] * acc["rr"])
    return float(np.abs(acc["lr"]).sum() / (w.sum() + EPS))


def _acc_new():
    nb = NFFT // 2 + 1
    return {"lr": np.zeros(nb, complex), "ll": np.zeros(nb), "rr": np.zeros(nb)}


def _equal_weight(XL, XR, band):
    """Scale each frame to unit band power, so that coherence is an average
    over time rather than over energy: otherwise a few loud seconds outweigh
    minutes of quieter playing (Nelsons Sym 2 Ia: 5 loud seconds at 0.76
    against 30 s at 0.50–0.56 made the whole track read ×1.6)."""
    p = np.sqrt(np.sqrt((np.abs(XL[:, band]) ** 2).sum(1)
                        * (np.abs(XR[:, band]) ** 2).sum(1)))[:, None] + EPS
    return XL / p, XR / p


def reference_coherence(L, R, frames, df):
    """L/R coherence of the orchestra where the basses do not play, in the
    band the bass harmonics are judged in."""
    acc = _acc_new()
    band = np.zeros(NFFT // 2 + 1, bool)
    band[int(CTX_BAND[0] / df):int(CTX_BAND[1] / df) + 1] = True
    for s in range(0, len(frames), 256):
        fi = frames[s:s + 256]
        XL, XR = _equal_weight(spectra(L, fi), spectra(R, fi), band)
        acc["lr"][band] += (np.conj(XL) * XR).sum(0)[band]
        acc["ll"][band] += (np.abs(XL) ** 2).sum(0)[band]
        acc["rr"][band] += (np.abs(XR) ** 2).sum(0)[band]
    return _coh(acc)


REF_DRAWS = 8          # orchestra draws per matched reference
REF_PIECE = 64         # longest run of consecutive frames one draw piece takes


class MatchedReference:
    """The orchestra's coherence where the basses rest, measured over frames
    shaped like the ones it is compared with: the same lengths of runs of
    consecutive frames.  Coherence over few frames reads high whatever the
    source (Mahler 2/II: the orchestra over 6-frame runs ×2.47 its long-run
    value, bowed bass ×2.65), so a short passage judged against the long-run
    value always looked forward."""

    def __init__(self, L, R, rest, df, seed=0):
        self.L, self.R, self.df = L, R, df
        self.runs = [(a, b) for a, b in runs(np.isin(np.arange(len(L) // HOP + 2), rest))]
        self.lens = np.array([b - a for a, b in self.runs])
        self.rng = np.random.default_rng(seed)
        self.whole = reference_coherence(L, R, rest, df) if len(rest) else None

    def _piece(self, n):
        ok = np.flatnonzero(self.lens >= n)
        if not len(ok):
            ok = np.array([int(np.argmax(self.lens))])
            n = int(self.lens[ok[0]])
        w = self.lens[ok] - n + 1
        a, b = self.runs[int(self.rng.choice(ok, p=w / w.sum()))]
        s0 = a + int(self.rng.integers(0, b - a - n + 1))
        return list(range(s0, s0 + n)), n

    def __call__(self, frames):
        if self.whole is None or not len(self.lens):
            return None
        m = np.zeros(int(frames.max()) + 2, bool)
        m[frames] = True
        need = [b - a for a, b in runs(m)]
        vals = []
        for _ in range(REF_DRAWS):
            pick = []
            for n in need:
                while n > 0:
                    got, k = self._piece(min(n, REF_PIECE))
                    pick += got
                    n -= k
            vals.append(reference_coherence(self.L, self.R, np.array(pick), self.df))
        return float(np.median(vals))


def context_cues(L, R, f0, frames, df, coh_ref):
    """Depth cues over a set of frames:

      coh_ratio  L/R coherence on the bass harmonics (80–700 Hz) over the
                 orchestra's where the basses rest, every frame weighted
                 equally; `coh_ref` is a number or a MatchedReference,
                 which measures the orchestra over frames of the same shape.
                 A bass picked up more directly than the rest is more
                 coherent (×>1).
      balance_db right minus left level on the same harmonics, every frame
                 weighted equally: where the basses sit (position, not
                 depth).  A level ratio, so short passages are not biased
                 the way coherence is.  The harmonics may include cellos
                 doubling an octave up (Mahler 2/II, Rattle: from bar 13
                 the octave leans left 7–11 dB while the fundamental, the
                 basses alone, leans right 5–9 dB).
      balance_fund_db  the same on the fundamental: the basses alone.
      fund_db    fundamental vs the mean of harmonics 2–6 (mono). Distance
                 and hall favour the fundamental; a close mic the upper
                 harmonics. Register dependent: compare like with like."""
    acc = _acc_new()
    fund = []
    fl = fr = 0.0
    bins = np.arange(NFFT // 2 + 1)
    band = (bins >= int(CTX_BAND[0] / df)) & (bins <= int(CTX_BAND[1] / df))
    for s in range(0, len(frames), 256):
        fi = frames[s:s + 256]
        XL, XR = _equal_weight(spectra(L, fi), spectra(R, fi), band)
        for j, i in enumerate(fi):
            b0 = f0[i] / df
            r = bins / b0
            comb = (band & (np.abs(r - np.rint(r)) * b0 <= 1.0)
                    & (np.rint(r) >= 2))
            xl, xr = XL[j], XR[j]
            acc["lr"][comb] += (np.conj(xl) * xr)[comb]
            acc["ll"][comb] += (np.abs(xl) ** 2)[comb]
            acc["rr"][comb] += (np.abs(xr) ** 2)[comb]
            k0 = int(round(b0))
            fl += float((np.abs(xl[max(k0 - 1, 0):k0 + 2]) ** 2).sum())
            fr += float((np.abs(xr[max(k0 - 1, 0):k0 + 2]) ** 2).sum())
            pm = np.abs(0.5 * (xl + xr)) ** 2
            h = [pm[max(int(round(k * b0)) - 1, 0):int(round(k * b0)) + 2].max()
                 for k in range(1, 7)]
            fund.append(10 * np.log10(h[0] / (np.mean(h[1:]) + EPS) + EPS))
    cc = _coh(acc)
    if callable(coh_ref):
        coh_ref = coh_ref(np.asarray(frames))
    return {"coh_comb": cc, "coh_ref": coh_ref,
            "coh_ratio": cc / (coh_ref + EPS) if coh_ref else None,
            "fund_db": float(np.median(fund)) if fund else None,
            "balance_db": float(10 * np.log10((acc["rr"].sum() + EPS) / (acc["ll"].sum() + EPS)))
            if acc["ll"].sum() > 0 else None,
            "balance_fund_db": float(10 * np.log10((fr + EPS) / (fl + EPS))) if fl > 0 else None}


def pizz_clarity(env, onsets):
    """Per isolated pizzicato: comb energy in the first 80 ms vs 96–600 ms
    (a per-note C80). The hall and a distant pickup fill the tail."""
    out = []
    e = 10 ** (env / 10)
    for n, i in enumerate(onsets):
        nxt = onsets[n + 1] if n + 1 < len(onsets) else len(env)
        if nxt < i + 38 or i + 38 > len(env) or not np.isfinite(
                env[i:i + 38]).all():
            continue
        out.append(10 * np.log10(e[i:i + 5].sum() / (e[i + 6:i + 38].sum()
                                                    + EPS)))
    return out


BLOCK_GAP_S = 2.0       # runs of one context closer than this form one block
BLOCK_MIN_FRAMES = 12   # frames a block needs for cues of its own (0.8 s)
PIZZ_BLOCK_MIN_FRAMES = 5   # one plucked note (0.3 s) is scored on its own


def context_blocks(L, R, t_frame, f0s, masks, coh_ref):
    """The passages of each context, with their own cues and score: the
    timeline the box shows, and what a listener's feedback is matched to."""
    df = SR / NFFT
    blocks = []
    for name in CONTEXTS:
        m = masks[name]
        merged = []
        for a, b in runs(m):
            if merged and t_frame[a] - t_frame[merged[-1][1] - 1] < BLOCK_GAP_S:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        for a, b in merged:
            fr = np.flatnonzero(m[a:b]) + a
            blk = {"context": name,
                   "start": round(float(t_frame[a] - NFFT / 2 / SR), 2),
                   "end": round(float(t_frame[b - 1] + NFFT / 2 / SR), 2),
                   "seconds": round(float(len(fr) * HOP / SR), 2)}
            least = PIZZ_BLOCK_MIN_FRAMES if name == "pizzicato" else BLOCK_MIN_FRAMES
            if len(fr) >= least and coh_ref:
                c = context_cues(L, R, f0s[name], fr, df, coh_ref)
                d = depth_score([{"solo": {"seconds": blk["seconds"], **c}}])
                blk.update(coh_ratio=round(c["coh_ratio"], 3),
                           fund_db=round(c["fund_db"], 2),
                           balance_db=round(c["balance_db"], 2),
                           balance_fund_db=round(c["balance_fund_db"], 2),
                           score=round(d["score"], 2), band=d["band"])
            blocks.append(blk)
    blocks.sort(key=lambda x: (x["start"], CONTEXTS.index(x["context"])))
    return blocks


def analyze_contexts(L, R, t_frame, f0, dom, npk, energy, gate, opts, pizz_comb=None):
    df = SR / NFFT
    masks, onsets, env = classify_contexts(0.5 * (L + R), f0, dom, npk,
                                           energy, gate, opts, pizz_comb)
    # each context's cues on the comb it was found with
    f0s = {name: f0 for name in CONTEXTS}
    if pizz_comb is not None:
        f0s["pizzicato"] = pizz_comb[0]
    rest = np.flatnonzero(gate & ~masks["solo"] & ~masks["with-sections"]
                          & ~masks["tutti"])
    coh_ref = MatchedReference(L, R, rest, df) if len(rest) >= 20 else None
    out = {}
    for name in CONTEXTS:
        m = masks[name]
        c = {"seconds": float(m.sum() * HOP / SR), "passages": []}
        for a, b in runs(m):
            if (b - a) * HOP / SR >= opts.ctx_min_s:
                c["passages"].append((float(t_frame[a] - NFFT / 2 / SR),
                                      float(t_frame[b - 1] + NFFT / 2 / SR)))
        if m.sum() >= opts.ctx_min_frames:
            c.update(context_cues(L, R, f0s[name], np.flatnonzero(m), df, coh_ref))
        if name == "pizzicato":
            cl = pizz_clarity(env, onsets)
            c["notes"] = len(onsets)
            if cl:
                c["clarity_db"] = float(np.median(cl))
                c["clarity_notes"] = len(cl)
        out[name] = c
    out["blocks"] = context_blocks(L, R, t_frame, f0s, masks, coh_ref)
    return out, masks


# ---------------------------------------------------------------- score

# Depth score 0–10 (10 = basses at the back, in the hall) from the cues that
# ordered the labelled recordings correctly: solo and with-sections ×coh, and
# solo fund_db. Each maps linearly between two anchors, clipped. First
# calibration (3 recordings): recalibrate as labelled material grows.
SCORE_COH = (1.0, 2.2)      # ×coh scoring 10 and 0
SCORE_FUND = (-15.0, 10.0)  # solo fund_db scoring 0 and 10
SCORE_W_FUND = 1 / 3        # weight of the fundamental cue (coherence 2/3)
SCORE_BANDS = ((7.0, "green", "back / natural"),
               (4.0, "yellow", "in between"),
               (-1.0, "red", "forward"))
SCORE_MIN_S = 10.0          # less solo + with-sections material: low confidence


def depth_score(ctxs):
    """Score one or more files' contexts together, weighting by seconds."""
    coh, fund = [], []
    for c in ctxs:
        for k in ("solo", "with-sections"):
            x = c.get(k, {})
            if x.get("coh_ratio"):
                coh.append((x["seconds"], x["coh_ratio"]))
        x = c.get("solo", {})
        if x.get("fund_db") is not None:
            fund.append((x["seconds"], x["fund_db"]))
    if not coh:
        return None
    ratio = np.average([v for _, v in coh], weights=[s for s, _ in coh])
    s_coh = 10 * (SCORE_COH[1] - ratio) / (SCORE_COH[1] - SCORE_COH[0])
    out = {"coh_ratio": float(ratio), "seconds": float(sum(s for s, _ in coh)),
           "coh_score": float(np.clip(s_coh, 0, 10))}
    score = out["coh_score"]
    if fund:
        fd = np.average([v for _, v in fund], weights=[s for s, _ in fund])
        s_f = 10 * (fd - SCORE_FUND[0]) / (SCORE_FUND[1] - SCORE_FUND[0])
        out["fund_db"] = float(fd)
        out["fund_score"] = float(np.clip(s_f, 0, 10))
        score = ((1 - SCORE_W_FUND) * score
                 + SCORE_W_FUND * out["fund_score"])
    out["score"] = float(score)
    out["band"], out["meaning"] = next((b, m) for t, b, m in SCORE_BANDS
                                       if score >= t)
    out["low_material"] = out["seconds"] < SCORE_MIN_S
    return out


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
    pizz_comb = bass_salience(mono, nfr, opts.fmin, opts.pizz_fmax)[:3]
    res["contexts"], ctx_masks = analyze_contexts(
        L, R, t_frame, f0, dom, npk, energy, gate, opts, pizz_comb)
    res["blocks"] = res["contexts"].pop("blocks")
    res["version"] = VERSION
    res["timeline"]["contexts"] = {k: v.tolist() for k, v in ctx_masks.items()}
    res["depth"] = depth_score([res["contexts"]])
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

    gw = np.sqrt([g["n"] for g in groups.values()])
    gp = [group_pitches(g, 440.0 * 2 ** (k / 12), opts.fmin, opts.fmax, df,
                        opts.leak_db) for k, g in groups.items()]
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
    cb, gcover, gnoise = pitch_masked_mean(gc, gp, gw, lags,
                                           tol + abs(lags[main_i]))
    envb = np.abs(cb)
    main_env = envb[main_i]
    pk, _, _ = lag_peaks(envb / gnoise, lags_ms, (opts.min_lag, opts.max_lag),
                         exclude=gcover < opts.min_cover)
    for p in pk:
        p["env"] = float(envb[p["i"]])
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
    A, acover, anoise = pitch_masked_mean(ac, gp, gw, alags, tol)
    envA = np.abs(A)
    pka, _, _ = lag_peaks(envA / anoise, ac_ms, (opts.min_lag, opts.max_lag),
                          exclude=acover < opts.min_cover)
    for p in pka:
        p["env"] = float(envA[p["i"]])
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

ANSI = {"green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m"}


def fmt_depth(d):
    """'8.8/10 ● green (back / natural)', coloured on a terminal."""
    if not d:
        return "no score (no bass context with enough frames)"
    dot = "●"
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        dot = f"{ANSI[d['band']]}●\033[0m"
    s = (f"{d['score']:.1f}/10 {dot} {d['band']} ({d['meaning']}) — "
         f"×coh {d['coh_ratio']:.2f}")
    if "fund_db" in d:
        s += f", solo fund {d['fund_db']:+.1f} dB"
    s += f", {d['seconds']:.0f}s"
    if d["low_material"]:
        s += " — LOW MATERIAL, provisional"
    return s


def report_contexts(ctx, npass=4):
    if not ctx:
        return
    print("   bass contexts (depth cues: L/R coherence of the bass harmonics vs "
          "the orchestra at rest; fundamental vs harmonics 2–6):")
    for name in CONTEXTS:
        c = ctx[name]
        line = f"     {name:<14}{c['seconds']:6.1f}s"
        if name == "pizzicato":
            line += f" {c['notes']:4d} notes"
        if c.get("coh_ratio"):
            line += (f"  coh {c['coh_comb']:.2f}/{c['coh_ref']:.2f} "
                     f"(×{c['coh_ratio']:.2f})  fund {c['fund_db']:+5.1f} dB"
                     f"  R−L {c['balance_db']:+5.1f} dB"
                     f" (fundamental {c['balance_fund_db']:+5.1f})")
        if "clarity_db" in c:
            line += (f"  clarity {c['clarity_db']:+5.1f} dB "
                     f"({c['clarity_notes']} isolated)")
        print(line)
        ps = c["passages"]
        if ps:
            print("       " + ", ".join(f"{fmt_time(a)}–{fmt_time(b)}"
                                        for a, b in ps[:npass])
                  + (f" … +{len(ps) - npass}" if len(ps) > npass else ""))


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
    report_contexts(res.get("contexts"))
    if "depth" in res:
        print(f"   DEPTH: {fmt_depth(res['depth'])}")
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
    g.add_argument("--present-dom", type=float, default=0.1,
                   help="share of 25–1500 Hz on a complete bass comb for "
                        "the basses to count as playing")
    g.add_argument("--tutti-min-peaks", type=int, default=4,
                   help="comb peaks enough for basses under a loud orchestra")
    g.add_argument("--solo-dom", type=float, default=0.7,
                   help="share above which the basses play alone")
    g.add_argument("--pizz-fmax", type=float, default=100.0,
                   help="highest f0 searched for pizzicato (G2 is 98; above C2 "
                        "cellos may be included)")
    g.add_argument("--pizz-rise", type=float, default=6.0,
                   help="dB rise of the bass comb within 48 ms (pizzicato)")
    g.add_argument("--pizz-drop", type=float, default=4.0,
                   help="dB fall of the bass comb within 320 ms")
    g.add_argument("--ctx-min-frames", type=int, default=20,
                   help="frames a context needs for depth cues")
    g.add_argument("--ctx-min-s", type=float, default=1.0,
                   help="shortest passage listed per context")
    g.add_argument("--leak-db", type=float, default=10.0,
                   help="another bass note this close to a group's own note "
                        "(harmonic level) has its periods masked too")
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
    if len(results) > 1:
        print(f"== all {len(results)} files together\n   DEPTH: "
              + fmt_depth(depth_score([r["contexts"] for r in results
                                       if "contexts" in r])))
    if opts.json:
        for r in results:
            r.pop("timeline", None)
            r.pop("curves", None)
        with open(opts.json, "w") as fh:
            json.dump(results, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
