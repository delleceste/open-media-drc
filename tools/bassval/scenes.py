"""Scenes to render: one hall, one main pair, the basses somewhere, a spot
microphone or not.  A grid varies one thing at a time around a reference,
so that every cue can be checked against a known change.

`bass_shift` moves the bass section along the stage (m; negative = towards
the back wall, further from the main pair).  The section's default centre
is 4.5 m from the back wall, about 10 m from the main pair.
"""
from __future__ import annotations

import itertools
import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Scene:
    piece: str
    setup: str = "ORTF"          # geometry.SETUPS
    rt_mid: float = 1.9          # s, 500–1000 Hz
    bass_shift: float = 0.0      # m along the stage, negative = further back
    spot: str = "none"           # none | aligned | unaligned
    spot_db: float = -6.0        # spot's basses relative to the main pair's
    seed: int = 1
    section_mode: str = "recorded"  # independent recorded parts; copies is a diagnostic

    @property
    def key(self) -> str:
        identity = asdict(self)
        if self.spot == "none":
            identity.pop("spot_db")
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
        return f"{self.piece}-{self.setup}-{self.spot}-s{self.seed}-{digest}"

    def as_dict(self) -> dict:
        return asdict(self) | {"key": self.key}


GRIDS = {
    # the first question: does any cue follow the basses' distance?
    "distance": dict(setup=["AB", "ORTF", "XY"], rt_mid=[1.9], bass_shift=[-2.0, 0.0, 2.0, 4.0],
                     spot=["none"]),
    # does the spot-mic check find a planted spot, and only that?
    "spot": dict(setup=["AB", "ORTF", "XY"], rt_mid=[1.9], bass_shift=[0.0],
                 spot=["none", "aligned", "unaligned"]),
    # does the hall change the cues as much as distance does?
    "hall": dict(setup=["ORTF"], rt_mid=[1.4, 1.9, 2.6], bass_shift=[-2.0, 2.0], spot=["none"]),
    # a quick end-to-end check
    "smoke": dict(setup=["ORTF"], rt_mid=[1.9], bass_shift=[-2.0, 4.0], spot=["none"]),
}


def grid(name: str, pieces: list[str], seed: int = 1, section_mode: str = "recorded") -> list[Scene]:
    """All scenes of a named grid for the given pieces."""
    g = GRIDS[name]
    return [Scene(piece, setup, rt, shift, spot, seed=seed, section_mode=section_mode)
            for piece, setup, rt, shift, spot in itertools.product(
                pieces, g["setup"], g["rt_mid"], g["bass_shift"], g["spot"])]
