"""The stereo recording of a scene, and what is true about it.

Every player is a point source at a position on stage (geometry.positions),
playing one anechoic part (stems).  Each player is convolved with its
impulse responses to the two main microphones (room.pair_responses: one
shared diffuse tail per player, with the pair's coherence) and summed.

An optional bass spot microphone (a cardioid 1.5 m in front of the bass
section) hears every player too, through its own responses (bleed
included).  It is mixed into both channels, panned to where the basses
appear from the main pair, at a level relative to the basses' level in the
main pair, and either

  aligned    delayed by the extra path from the basses to the main pair,
             so the two arrivals coincide (good practice), or
  unaligned  not delayed, so every bass note reaches the mix twice: first
             through the spot, Δ later through the main pair.

The ground truth of a scene is what the detector is tested against: the
basses' mean distance to the main pair, their direct-to-reverberant ratio
at the main pair (from the impulse responses), and the spot's true Δ.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import fftconvolve

from . import geometry, room, stems

# players per section (the recorded parts are repeated to fill a section)
SECTION_PLAYERS = {"vl1": 8, "vl2": 6, "vla": 4, "vc": 4, "kb": 4}
MAIN_CENTRE = (0.0, 14.0, 3.5)     # x from the centre line, y, z
SPOT_DISTANCE = 1.5


def pan_gains(position: float) -> tuple[float, float]:
    """Constant-power pan: position −1 (right) … +1 (left) -> (gL, gR)."""
    a = (np.clip(position, -1, 1) + 1) * math.pi / 4
    return math.sin(a), math.cos(a)


def render_scene(scene, parts: dict[str, list[np.ndarray]], fs: int) -> tuple[np.ndarray, dict]:
    """The stereo recording (2, n) of `scene` (scenes.Scene) made from the
    anechoic `parts` of its piece, and its ground truth."""
    rng = np.random.default_rng(scene.seed)
    hall = geometry.hall_with_rt(scene.rt_mid)
    centre = np.array([hall.width / 2 + MAIN_CENTRE[0], MAIN_CENTRE[1], MAIN_CENTRE[2]])
    left, right = geometry.main_pair(scene.setup, centre)
    shift = (0.0, scene.bass_shift)
    kb_centre = geometry.section_centre("kb", hall, shift)
    spot = geometry.spot_mic(kb_centre, SPOT_DISTANCE) if scene.spot != "none" else None
    n = max(len(x) for xs in parts.values() for x in xs)
    length = int(fs * (1.2 * max(hall.rt60.values()) + 0.3))

    main = np.zeros((2, n + length - 1))
    main_kb = np.zeros((2, n + length - 1))
    spot_all = np.zeros(n + length - 1) if spot else None
    spot_kb = np.zeros(n + length - 1) if spot else None
    kb_dist, kb_drr = [], []
    for section, recorded in sorted(parts.items()):
        count = SECTION_PLAYERS.get(section, len(recorded))
        players = stems.section_players(recorded, count, fs, rng)
        pos = geometry.positions(section, len(players), hall, rng,
                                 shift if section == "kb" else (0.0, 0.0))
        for x, p in zip(players, pos):
            pr = room.pair_responses(p, [left, right], hall, fs, rng, length)
            for ch in (0, 1):
                y = fftconvolve(x, pr["h"][ch])
                main[ch] += y
                if section == "kb":
                    main_kb[ch] += y
            if section == "kb":
                kb_dist.append(float(np.linalg.norm(p - centre)))
                kb_drr.append(10 * math.log10(sum(pr["direct_e"]) / sum(pr["reverb_e"])))
            if spot is not None:
                ys = fftconvolve(x, room.pair_responses(p, [spot], hall, fs, rng, length)["h"][0])
                spot_all += ys
                if section == "kb":
                    spot_kb += ys

    truth = {"bass_distance_m": float(np.mean(kb_dist)), "bass_drr_db": float(np.mean(kb_drr)),
             "bass_x_m": float(kb_centre[0] - centre[0]), "spot": scene.spot, "spot_delta_ms": None}
    out = main
    if spot is not None:
        # level: the spot's basses at `spot_db` relative to the main pair's basses
        e_main = float((main_kb ** 2).sum())
        e_spot = float((spot_kb ** 2).sum())
        g = math.sqrt(10 ** (scene.spot_db / 10) * e_main / e_spot)
        # pan to where the basses appear from the main pair
        az = math.atan2(kb_centre[0] - centre[0], centre[1] - kb_centre[1])
        gl, gr = pan_gains(math.sin(az) / math.sin(math.radians(60)))
        delta = (np.linalg.norm(kb_centre - centre) - np.linalg.norm(kb_centre - spot.pos)) / room.C_SOUND
        truth["spot_delta_ms"] = 1000 * delta
        shift_n = int(round(delta * fs)) if scene.spot == "aligned" else 0
        s = np.concatenate([np.zeros(shift_n), spot_all[:len(spot_all) - shift_n]]) * g
        out = main + np.vstack([gl * s, gr * s])
    out = out / (np.abs(out).max() + 1e-12) * 0.5
    return out, truth
