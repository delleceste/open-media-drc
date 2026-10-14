"""Room impulse responses for a shoebox hall.

An impulse response from a source to a microphone is built in two parts:

  early   the direct sound and the specular reflections up to `T_MIX`
          after it, by the image-source method (Allen & Berkley 1979):
          every wall mirrors the source, each mirror image arrives with
          delay r/c, amplitude β^n / r (β = √(1 − α) per bounce, n bounces)
          and the microphone's pattern gain for its direction.

  late    a statistical diffuse tail from `T_MIX` on: Gaussian noise split
          into octave bands, each decaying at its band's RT60, at the level
          Sabine's diffuse-field theory gives for the hall (critical
          distance r_c = 0.057·√(V/RT)).  The tails of the two microphones
          of a pair are not independent: they get the coherence a diffuse
          field has between those two capsules (patterns, angles and
          spacing; Cook et al. 1955 for spaced omnis, sin(kd)/kd).

Units: the direct sound of a source at 1 m into an omni has amplitude 1.
Responses are sampled at `fs` (the detector's 16 kHz is enough: it looks at
nothing above 4 kHz).  Everything here is deterministic given the `rng`.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import butter, sosfilt

from .geometry import Hall, Mic

C_SOUND = 343.0
T_MIX = 0.080          # s after the direct sound: image sources before, tail after
FADE = 0.010           # s: the tail fades in over this
SINC_TAPS = 32         # fractional-delay kernel length


def critical_distance(hall: Hall) -> float:
    """Distance at which direct and diffuse energy are equal (Sabine)."""
    return 0.057 * math.sqrt(hall.volume / hall.rt60_mid())


def diffuse_energy_factor(mic: Mic) -> float:
    """Mean of g² over the sphere: the share of diffuse energy a pattern
    picks up relative to an omni (1 omni, 1/3 cardioid and figure-8)."""
    a = mic.a
    return a * a + (1.0 - a) ** 2 / 3.0


# ---------------------------------------------------------------- early part

def _axis_images(coord: float, size: float, nmax: int):
    """Image coordinates along one axis and their bounce counts: for parity
    q ∈ {0, 1} and order n, coord' = (1 − 2q)·coord + 2·n·size after
    |2n − q| reflections."""
    out = []
    for n in range(-nmax, nmax + 1):
        for q in (0, 1):
            out.append(((1 - 2 * q) * coord + 2 * n * size, abs(2 * n - q)))
    return np.array(out)


def image_sources(src: np.ndarray, hall: Hall, max_dist: float):
    """All image sources of `src` that can lie within `max_dist` of any
    point in the hall: positions (M, 3) and bounce counts (M,)."""
    dims = hall.dims
    per_axis = [_axis_images(src[a], dims[a], int(math.ceil(max_dist / dims[a])) + 1)
                for a in range(3)]
    gx, gy, gz = np.meshgrid(np.arange(len(per_axis[0])), np.arange(len(per_axis[1])),
                             np.arange(len(per_axis[2])), indexing="ij")
    gx, gy, gz = gx.ravel(), gy.ravel(), gz.ravel()
    pos = np.stack([per_axis[0][gx, 0], per_axis[1][gy, 0], per_axis[2][gz, 0]], axis=1)
    bounces = (per_axis[0][gx, 1] + per_axis[1][gy, 1] + per_axis[2][gz, 1]).astype(int)
    return pos, bounces


def _place(h: np.ndarray, delays_s: np.ndarray, amps: np.ndarray, fs: int) -> None:
    """Add impulses at fractional delays into `h` with a Hann-windowed
    sinc kernel (band-limited, so the sub-sample timing survives)."""
    half = SINC_TAPS // 2
    pos = delays_s * fs
    base = np.floor(pos).astype(int)
    frac = pos - base
    k = np.arange(-half + 1, half + 1)
    t = k[None, :] - frac[:, None]
    kern = np.sinc(t) * (0.5 + 0.5 * np.cos(np.pi * t / (half + 1)))
    idx = base[:, None] + k[None, :]
    ok = (idx >= 0) & (idx < len(h))
    np.add.at(h, idx[ok], (amps[:, None] * kern)[ok])


def early_response(src: np.ndarray, mic: Mic, hall: Hall, fs: int, length: int):
    """Direct sound and specular reflections arriving up to T_MIX after it.
    Returns (h, direct delay in s, direct energy, early-reflection energy)."""
    r0 = float(np.linalg.norm(src - mic.pos))
    t0 = r0 / C_SOUND
    imgs, bounces = image_sources(src, hall, r0 + C_SOUND * T_MIX)
    vec = imgs - mic.pos
    dist = np.linalg.norm(vec, axis=1)
    keep = dist <= r0 + C_SOUND * T_MIX + 1e-9
    vec, dist, bounces = vec[keep], dist[keep], bounces[keep]
    beta = math.sqrt(1.0 - hall.absorption)
    amps = beta ** bounces / np.maximum(dist, 0.1) * mic.gain(vec / dist[:, None])
    h = np.zeros(length)
    _place(h, dist / C_SOUND, amps, fs)
    direct = bounces == 0
    return h, t0, float((amps[direct] ** 2).sum()), float((amps[~direct] ** 2).sum())


# ---------------------------------------------------------------- late part

def diffuse_coherence(m1: Mic, m2: Mic, freqs: np.ndarray, n_dirs: int = 2000) -> np.ndarray:
    """Complex coherence of a diffuse field between two microphones, per
    frequency: ∫ g1(u)·g2(u)·e^{−j k u·(p2 − p1)} dΩ / √(∫g1² ∫g2²), over
    plane waves from every direction u (a Fibonacci sphere).  For two omnis
    `d` apart this is sin(kd)/kd."""
    i = np.arange(n_dirs) + 0.5
    phi = np.arccos(1 - 2 * i / n_dirs)
    theta = np.pi * (1 + 5 ** 0.5) * i
    u = np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], 1)
    g1, g2 = m1.gain(u), m2.gain(u)
    d = m2.pos - m1.pos
    k = 2 * np.pi * np.asarray(freqs) / C_SOUND
    phase = np.exp(-1j * np.outer(k, u @ d))
    return (phase * (g1 * g2)[None, :]).sum(1) / math.sqrt((g1 ** 2).sum() * (g2 ** 2).sum())


def _band_filters(centres, fs):
    """Octave bands covering 0–fs/2: the lowest a low-pass, the highest a
    high-pass, the rest band-passes (2nd-order Butterworth sections)."""
    centres = sorted(centres)
    out = []
    for i, fc in enumerate(centres):
        lo, hi = fc / math.sqrt(2), min(fc * math.sqrt(2), fs / 2 * 0.95)
        if i == 0:
            sos = butter(2, hi, btype="low", fs=fs, output="sos")
        elif i == len(centres) - 1 or hi >= fs / 2 * 0.95:
            sos = butter(2, lo, btype="high", fs=fs, output="sos")
        else:
            sos = butter(2, [lo, hi], btype="band", fs=fs, output="sos")
        out.append((fc, sos))
        if hi >= fs / 2 * 0.95:
            break
    return out


def late_tails(mics: list[Mic], hall: Hall, fs: int, length: int, start: int,
               rng: np.random.Generator) -> list[np.ndarray]:
    """Diffuse tails for the microphones of one pair (1 or 2 mics), from
    sample `start` on, each normalised to unit energy, decaying per octave
    band at the hall's RT60 and, for a pair, with the diffuse-field
    coherence between the two capsules."""
    n = length - start
    t = np.arange(n) / fs
    bands = _band_filters(hall.rt60.keys(), fs)
    # Fixed seed per capsule: changing the requested length must not change
    # the other capsule's random excitation. Generate relative to tail onset.
    seeds = rng.integers(0, 2**63, size=len(mics), dtype=np.int64)
    noises = [np.random.default_rng(int(seed)).standard_normal(n) for seed in seeds]
    shaped = []
    for x in noises:
        y = np.zeros(n)
        for fc, sos in bands:
            env = np.exp(-6.91 * t / hall.rt60[fc])
            y += sosfilt(sos, x) * env
        shaped.append(y)
    if len(mics) == 2:
        # mix in the frequency domain: Y2 = γ·Y1 + √(1 − |γ|²)·N2
        nf = 1 << int(math.ceil(math.log2(n)))
        f = np.fft.rfftfreq(nf, 1 / fs)
        grid = np.geomspace(20, fs / 2, 96)
        g = diffuse_coherence(mics[0], mics[1], grid)
        gamma = np.interp(f, grid, g.real) + 1j * np.interp(f, grid, g.imag)
        gamma[f < 20] = gamma[f >= 20][0]
        Y1, N2 = np.fft.rfft(shaped[0], nf), np.fft.rfft(shaped[1], nf)
        Y2 = gamma * Y1 + np.sqrt(np.clip(1 - np.abs(gamma) ** 2, 0, 1)) * N2
        shaped[1] = np.fft.irfft(Y2, nf)[:n]
    fade = np.minimum(1.0, t / FADE)
    out = []
    for y in shaped:
        y = y * fade
        full = np.zeros(length)
        full[start:] = y / math.sqrt((y ** 2).sum() + 1e-30)
        out.append(full)
    return out


# ---------------------------------------------------------------- together

def tail_energy(hall: Hall, mic: Mic, t_from_direct: float) -> float:
    """Energy the diffuse tail of one response carries: Sabine's
    reverberant energy 1/r_c² (times the pattern's diffuse share), the part
    still left `t_from_direct` after the direct sound, e^(−13.8·t/RT).  The
    reflections before that come from the image sources."""
    total = diffuse_energy_factor(mic) / critical_distance(hall) ** 2
    return total * math.exp(-13.8 * t_from_direct / hall.rt60_mid())


def pair_responses(src: np.ndarray, mics: list[Mic], hall: Hall, fs: int,
                   rng: np.random.Generator, length: int | None = None) -> dict:
    """Impulse responses from `src` to each microphone of one pair (or a
    single microphone), sharing one diffuse tail realisation shaped to the
    pair's coherence.  Returns {"h": [arrays], "direct_s": [delays],
    "direct_e": [energies], "reverb_e": [energies]}."""
    if length is None:
        length = int(fs * (1.2 * max(hall.rt60.values()) + 0.3))
    early = [early_response(src, m, hall, fs, length) for m in mics]
    t0 = min(e[1] for e in early)
    start = int((t0 + T_MIX) * fs)
    tails = late_tails(mics, hall, fs, length, start, rng)
    out = {"h": [], "direct_s": [], "direct_e": [], "reverb_e": []}
    for m, (h, td, ed, er), tail in zip(mics, early, tails):
        e_tail = tail_energy(hall, m, T_MIX)
        h = h + tail * math.sqrt(e_tail)
        out["h"].append(h)
        out["direct_s"].append(td)
        out["direct_e"].append(ed)
        out["reverb_e"].append(er + e_tail)
    return out
