"""Geometry of a scene: the hall, where each section sits, and the microphones.

Coordinates are metres in the hall's frame: y along the hall (0 at the wall
behind the orchestra), z up from the floor, x across.  The orchestra faces
+y, towards the audience and the main microphones; looking from there
towards the stage (towards −y), +x is on the listener's LEFT.  So the first
violins (the conductor's left, the audience's left) sit at +x, and the left
channel's capsule is the one at higher x.

All positions are plain numpy arrays; the dataclasses only carry them with
names, so every other module can be tested with hand-made geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Hall:
    """A shoebox hall.  `absorption` is the energy absorbed per reflection
    in the image-source part (one value for every wall and every
    frequency); `rt60` maps octave-band centre frequencies to the
    reverberation time of the diffuse tail."""
    length: float = 40.0          # y extent
    width: float = 24.0           # x extent
    height: float = 15.0
    absorption: float = 0.25
    rt60: dict = field(default_factory=lambda: {63: 2.4, 125: 2.2, 250: 2.0,
                                                 500: 1.9, 1000: 1.8, 2000: 1.6,
                                                 4000: 1.3})

    @property
    def dims(self) -> np.ndarray:
        return np.array([self.width, self.length, self.height])

    @property
    def volume(self) -> float:
        return float(np.prod(self.dims))

    def rt60_mid(self) -> float:
        """The 500–1000 Hz average, the usual single-number RT."""
        return 0.5 * (self.rt60.get(500, 2.0) + self.rt60.get(1000, 2.0))


def hall_with_rt(rt_mid: float, base: Hall | None = None) -> Hall:
    """`base` with every band's RT scaled so that the mid-band RT is
    `rt_mid` (the spectral shape of the decay is kept)."""
    base = base or Hall()
    k = rt_mid / base.rt60_mid()
    return Hall(base.length, base.width, base.height, base.absorption,
                {f: t * k for f, t in base.rt60.items()})


@dataclass(frozen=True)
class Mic:
    """One microphone: position, unit axis it points along, and a
    first-order pattern g(θ) = a + (1 − a)·cos θ (a = 1 omni, 0.5 cardioid,
    0 figure-of-eight)."""
    pos: np.ndarray
    axis: np.ndarray
    a: float

    def gain(self, direction: np.ndarray) -> np.ndarray:
        """Pattern gain for arrival directions (unit vectors from the mic
        towards the source, shape (..., 3))."""
        cos = direction @ self.axis
        return self.a + (1.0 - self.a) * cos


PATTERN = {"omni": 1.0, "cardioid": 0.5, "figure8": 0.0}


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def _axis(azimuth_deg: float, elevation_deg: float = 0.0) -> np.ndarray:
    """A unit axis: azimuth 0 points at the orchestra (−y), positive
    azimuth turns it towards +x (the listener's left); positive elevation
    tilts it up."""
    az, el = np.radians(azimuth_deg), np.radians(elevation_deg)
    return _unit([np.sin(az) * np.cos(el), -np.cos(az) * np.cos(el), np.sin(el)])


# The setups a main pair is commonly built with.  For each: spacing between
# the capsules (m), angle of each capsule off the centre line (degrees),
# and the pattern.  Facing the orchestra, the LEFT channel's capsule is the
# one at higher x, turned towards +x.
SETUPS = {
    "AB":       {"spacing": 0.60, "angle": 0.0,  "pattern": "omni"},
    "ORTF":     {"spacing": 0.17, "angle": 55.0, "pattern": "cardioid"},
    "XY":       {"spacing": 0.0,  "angle": 45.0, "pattern": "cardioid"},
    "Blumlein": {"spacing": 0.0,  "angle": 45.0, "pattern": "figure8"},
}


def main_pair(setup: str, centre, tilt_deg: float = -15.0) -> tuple[Mic, Mic]:
    """The left and right microphones of a main pair centred at `centre`,
    aimed at the orchestra and tilted down by `tilt_deg`.  Facing −y, the
    left capsule sits at higher x and turns towards +x."""
    s = SETUPS[setup]
    c = np.asarray(centre, float)
    half = np.array([s["spacing"] / 2.0, 0.0, 0.0])
    a = PATTERN[s["pattern"]]
    # Tilt the complete array rigidly about x; preserve the included angle.
    tilt = np.radians(tilt_deg)
    rotation = np.array([[1, 0, 0], [0, np.cos(tilt), np.sin(tilt)],
                         [0, -np.sin(tilt), np.cos(tilt)]])
    left = Mic(c + half, rotation @ _axis(+s["angle"]), a)
    right = Mic(c - half, rotation @ _axis(-s["angle"]), a)
    return left, right


def spot_mic(section_centre, distance: float = 1.5, height: float = 2.2) -> Mic:
    """A cardioid `distance` in front of a section (towards +y), at
    `height`, pointing at the section's centre at player height."""
    sc = np.asarray(section_centre, float)
    pos = np.array([sc[0], sc[1] + distance, height])
    return Mic(pos, _unit(sc - pos), PATTERN["cardioid"])


# The orchestra: each section a rectangle on stage (x0, x1, y0, y1), x from
# the stage centre line (+x = the audience's left) and y from the back wall;
# players sit at 1.2 m (instrument height).  American seating as the
# audience sees it: first violins left front, seconds behind them, violas
# and cellos right, basses back right.
SECTIONS = {
    "vl1":   (1.0, 5.0, 9.0, 11.0),
    "vl2":   (1.5, 5.0, 6.5, 8.5),
    "vla":   (-4.0, -1.0, 9.0, 11.0),
    "vc":    (-5.5, -2.0, 6.5, 8.8),
    "kb":    (-7.5, -4.0, 3.5, 5.5),
    "winds": (-2.5, 2.5, 5.0, 7.0),
    "brass": (-4.0, 4.0, 2.5, 4.2),
    "perc":  (4.5, 6.5, 2.0, 3.5),
}
PLAYER_HEIGHT = 1.2
STAGE_EDGE = 12.0          # y of the stage front; the conductor stands here


def positions(section: str, count: int, hall: Hall, rng: np.random.Generator,
              shift=(0.0, 0.0)) -> np.ndarray:
    """`count` player positions spread over a section's rectangle (jittered
    grid), moved by `shift` (dx, dy), in hall coordinates."""
    x0, x1, y0, y1 = SECTIONS[section]
    cols = int(np.ceil(np.sqrt(count)))
    rows = int(np.ceil(count / cols))
    pts = []
    for i in range(count):
        r, c = divmod(i, cols)
        fx = (c + 0.5 + rng.uniform(-0.25, 0.25)) / cols
        fy = (r + 0.5 + rng.uniform(-0.25, 0.25)) / rows
        pts.append([hall.width / 2 + x0 + fx * (x1 - x0) + shift[0],
                    y0 + fy * (y1 - y0) + shift[1], PLAYER_HEIGHT])
    return np.array(pts)


def section_centre(section: str, hall: Hall, shift=(0.0, 0.0)) -> np.ndarray:
    x0, x1, y0, y1 = SECTIONS[section]
    return np.array([hall.width / 2 + (x0 + x1) / 2 + shift[0],
                     (y0 + y1) / 2 + shift[1], PLAYER_HEIGHT])
