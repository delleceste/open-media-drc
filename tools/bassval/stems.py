"""Anechoic orchestra parts, grouped into sections.

The Aalto recordings (Pätynen, Pulkki & Lokki, Acta Acustica united with
Acustica 94(6), 2008) give every part of four orchestral excerpts as a
separate anechoic recording, one player per part, picked up by several
microphones around the player; `<piece>_<part>_<mic>.mp3`, mic 6 being the
frontal one.  Only mic 6 is used here: one mono signal per player.

A real section has several players; the recordings have one (two for some
divided strings).  A section of N players is made from the parts available
by repeating them with small, random timing offsets (5–30 ms, the spread of
an ensemble) and level differences (±1.5 dB).  Copies of one performance
are more alike than different players would be, so the section is more
coherent than a real one; the spread of positions and of room responses
decorrelates them in the mix.  This is a limitation of the test material.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess

import numpy as np

# part name (without the divisi letter) -> section
PART_SECTION = {
    "vl1": "vl1", "vl2": "vl2", "vla": "vla", "vc": "vc", "kb": "kb",
    "fl": "winds", "ob": "winds", "cl": "winds", "bsn": "winds", "picc": "winds",
    "corno": "brass", "tr": "brass", "trb": "brass", "tuba": "brass",
    "timp": "perc", "perc": "perc",
}
FRONT_MIC = "6"

# "vl1", "vl2" first: violins I and II carry their number in the part name
_PARTS = sorted(["vl1", "vl2", *PART_SECTION], key=len, reverse=True)
_REST = re.compile(r"^\d*[ab]?$")       # player number and divisi letter


def parse_name(filename: str) -> tuple[str, str] | None:
    """(section, mic) of `<piece>_<part><n><a|b>_<mic>.mp3`, or None.  The
    part is the longest known name the token starts with, so "vla" (viola)
    is not "vl" + divisi "a", nor "kb" "k" + "b"."""
    stem, ext = os.path.splitext(filename)
    bits = stem.split("_")
    if ext != ".mp3" or len(bits) != 3 or not bits[2].isdigit():
        return None
    token = bits[1]
    for part in _PARTS:
        if token.startswith(part) and _REST.match(token[len(part):]):
            section = PART_SECTION.get(part) or part
            return section, bits[2]
    return None


def decode(path: str, fs: int) -> np.ndarray:
    """A file as mono float64 at `fs` (ffmpeg on PATH)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(fs),
                          "-f", "f32le", "-"], stdout=subprocess.PIPE, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").astype(np.float64)


def part_files(folder: str) -> dict[str, list[str]]:
    """The frontal-microphone files of one piece, by section."""
    out: dict[str, list[str]] = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.mp3"))):
        parsed = parse_name(os.path.basename(path))
        if parsed and parsed[1] == FRONT_MIC:
            out.setdefault(parsed[0], []).append(path)
    return out


def load_piece(folder: str, fs: int, seconds: float | None = None) -> dict[str, list[np.ndarray]]:
    """Every frontal part of a piece, decoded, padded to one length and
    optionally cut to the first `seconds`: {section: [signals]}."""
    files = part_files(folder)
    parts = {sec: [decode(p, fs) for p in paths] for sec, paths in files.items()}
    n = max(len(x) for xs in parts.values() for x in xs)
    if seconds:
        n = min(n, int(seconds * fs))
    for sec, xs in parts.items():
        parts[sec] = [np.pad(x, (0, max(0, n - len(x))))[:n] for x in xs]
    return parts


def section_players(parts: list[np.ndarray], count: int, fs: int,
                    rng: np.random.Generator) -> list[np.ndarray]:
    """`count` players from the recorded parts of one section: the parts in
    turn, each copy offset by 5–30 ms and scaled by ±1.5 dB (the first copy
    of each part unchanged)."""
    out = []
    for i in range(count):
        x = parts[i % len(parts)]
        if i >= len(parts):
            shift = int(rng.uniform(0.005, 0.030) * fs)
            x = np.concatenate([np.zeros(shift), x[:len(x) - shift]])
            x = x * 10 ** (rng.uniform(-1.5, 1.5) / 20)
        out.append(x)
    return out
