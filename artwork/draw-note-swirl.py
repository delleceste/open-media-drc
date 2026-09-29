#!/usr/bin/env python3
"""App icon studies: a swirl of small notes, laid on a nautilus (golden) spiral.

No lines, notes only: each note sits on the spiral, a little larger the further
out it is, turned partly along the curve, and coloured from a gradient that runs
from the centre outwards.  Writes, for each palette, an SVG and a 512 px PNG
preview, and one contact sheet with all of them:

    artwork/note-swirl-<palette>.svg / .png
    artwork/note-swirl-sheet.png

    python3 artwork/draw-note-swirl.py        (needs rsvg-convert and magick for the PNGs)
"""
import math
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE = 512                      # the icon's side, in SVG units

# Colour stops, centre -> outside, and the background (light, a hint of the hue).
PALETTES = {
    "blue":    {"stops": ["#8ec5ff", "#4b9dff", "#1f6feb", "#0b3f9c"], "bg": ("#ffffff", "#eef5ff")},
    "green":   {"stops": ["#9be7a9", "#4cc26a", "#1f9d49", "#0d6630"], "bg": ("#ffffff", "#eefaf1")},
    # "fantasy": dawn over the sea -- teal to violet to a warm coral
    "fantasy": {"stops": ["#5fd4c4", "#4f8ff0", "#8b5cf6", "#f0708a"], "bg": ("#ffffff", "#f5f1ff")},
}


def lerp_colour(stops, t):
    t = max(0.0, min(1.0, t)) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    f = t - i
    a, b = stops[i], stops[i + 1]
    ca = [int(a[k:k + 2], 16) for k in (1, 3, 5)]
    cb = [int(b[k:k + 2], 16) for k in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(ca, cb))


# One note, drawn around its head's centre at (0, 0), head width ~ 1 unit.
# kind: "quarter", "eighth", "sixteenth"; the stem goes up on the right.
def note_path(kind: str) -> str:
    head = ("M -0.52 0.08 C -0.52 -0.30 -0.02 -0.44 0.30 -0.34 "
            "C 0.56 -0.26 0.62 0.02 0.44 0.20 C 0.20 0.44 -0.30 0.46 -0.46 0.30 "
            "C -0.51 0.25 -0.53 0.17 -0.52 0.08 Z")
    stem = "M 0.37 0.02 L 0.52 -0.10 L 0.52 -2.35 L 0.40 -2.35 Z"
    flag1 = ("M 0.46 -2.35 C 0.62 -1.95 1.18 -1.78 1.12 -1.20 "
             "C 1.10 -0.98 1.02 -0.86 0.94 -0.78 C 1.02 -1.30 0.78 -1.58 0.46 -1.72 Z")
    flag2 = ("M 0.46 -1.92 C 0.62 -1.55 1.14 -1.40 1.08 -0.86 "
             "C 1.06 -0.66 0.99 -0.55 0.91 -0.48 C 0.99 -0.96 0.76 -1.22 0.46 -1.34 Z")
    parts = [head, stem]
    if kind in ("eighth", "sixteenth"):
        parts.append(flag1)
    if kind == "sixteenth":
        parts.append(flag2)
    # separate shapes, not one path: drawn in opposite directions, overlapping parts
    # of one path would cancel out and leave a white seam where the stem meets the head
    return "".join(f'<path d="{d}"/>' for d in parts)


def swirl(palette: dict, count: int = 21, turns: float = 1.85, growth: float = 3.0,
          follow: float = 0.35) -> str:
    """The icon's SVG.  A nautilus-like spiral (the radius grows `growth` times a
    turn), `count` notes at even angles on it -- so their spacing grows with them --
    each tilted `follow` of the way along the curve (0 upright, 1 fully along it)."""
    b = math.log(growth) / (2 * math.pi)
    psi = math.atan(1 / b)                    # a log spiral's constant tangent-to-radius angle
    theta_end = turns * 2 * math.pi
    a = 1 / math.exp(b * theta_end)           # outermost radius 1, scaled below
    kinds = ["eighth", "quarter", "sixteenth", "eighth", "quarter", "eighth", "sixteenth"]
    pts = []
    for i in range(count):
        t = i / (count - 1)
        theta = theta_end * (0.12 + 0.88 * t)
        r = a * math.exp(b * theta)
        phi = theta - math.radians(40)        # turn the whole swirl so it sits balanced
        tangent = math.degrees(phi + psi)     # direction of travel, outwards
        pts.append((r, phi, tangent, t, kinds[i % len(kinds)]))
    # the notes' heads grow with the radius; fit the swirl, heads and stems, in the square
    head = lambda r: 0.045 + 0.13 * r
    xs = [r * math.cos(p) for r, p, *_ in pts]
    ys = [r * math.sin(p) for r, p, *_ in pts]
    pad = max(head(r) for r, *_ in pts) * 1.6
    span = max(max(xs) - min(xs), max(ys) - min(ys)) + 2 * pad
    k = SIZE * 0.78 / span
    cx = SIZE / 2 - k * (max(xs) + min(xs)) / 2
    cy = SIZE / 2 - k * (max(ys) + min(ys)) / 2 + SIZE * 0.03
    notes = []
    for (r, phi, tangent, t, kind), x, y in zip(pts, xs, ys):
        wrap = (tangent + 180) % 360 - 180
        angle = follow * (wrap if abs(wrap) <= 90 else wrap - math.copysign(180, wrap))
        colour = lerp_colour(palette["stops"], t)
        notes.append(f'<g fill="{colour}" '
                     f'transform="translate({cx + k * x:.1f} {cy + k * y:.1f}) rotate({angle:.1f}) '
                     f'scale({k * head(r):.2f})">{note_path(kind)}</g>')
    bg0, bg1 = palette["bg"]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}">
  <defs>
    <radialGradient id="bg" cx="50%" cy="45%" r="70%">
      <stop offset="0" stop-color="{bg0}"/>
      <stop offset="1" stop-color="{bg1}"/>
    </radialGradient>
  </defs>
  <rect width="{SIZE}" height="{SIZE}" rx="{SIZE * 0.22:.0f}" fill="url(#bg)"/>
  {chr(10).join("  " + n for n in notes).strip()}
</svg>
'''


def main():
    pngs = []
    for name, palette in PALETTES.items():
        svg_path = os.path.join(HERE, f"note-swirl-{name}.svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(swirl(palette))
        png_path = svg_path[:-4] + ".png"
        subprocess.run(["rsvg-convert", "-w", "512", "-h", "512", svg_path, "-o", png_path], check=True)
        pngs.append(png_path)
        print("wrote", os.path.relpath(svg_path, HERE), "and", os.path.relpath(png_path, HERE))
    # the three at full size, and under them at launcher sizes (96 and 48 px): an icon
    # has to read small
    sheet = os.path.join(HERE, "note-swirl-sheet.png")
    small = []
    for png in pngs:
        small += ["(", png, "-resize", "96x96", ")", "(", png, "-resize", "48x48", ")"]
    subprocess.run(["magick", "(", *pngs, "-background", "#d9dde3", "-splice", "24x24", "+append", ")",
                    "(", *small, "-background", "#d9dde3", "-gravity", "center", "-extent", "180x120", "+append", ")",
                    "-background", "#d9dde3", "-gravity", "west", "-append", "-gravity", "southeast",
                    "-splice", "24x24", sheet], check=True)
    print("wrote", os.path.relpath(sheet, HERE))


if __name__ == "__main__":
    main()
