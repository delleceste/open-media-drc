#!/usr/bin/env python3
"""The app's launcher icon: a simple swirl of notes, coloured like the meters.

An Android adaptive icon (API 26+): three 108 x 108 dp layers the launcher masks
to its own shape (a circle on a Pixel, a squircle or rounded square elsewhere):

  background  opaque, a light warm white, full bleed
  foreground  transparent, the notes only -- all inside the central 66 dp circle,
              the part every mask keeps
  monochrome  the notes in one colour, for Android 13+ themed icons

Writes the app's drawables, SVG copies, the Google Play icon (512 px, full square:
Play masks it) and a sheet previewing the icon under a circle and a squircle at
192, 96 and 48 px, and themed:

  android/omdrc-app/app/src/main/res/drawable/ic_launcher_{background,foreground,monochrome}.xml
  artwork/app-icon/{background,foreground,monochrome,icon}.svg
  artwork/app-icon/playstore-512.png, artwork/app-icon/preview.png

    python3 artwork/draw-app-icon.py      (the PNGs need rsvg-convert and magick)
"""
import math
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app-icon")
RES = os.path.join(HERE, "..", "android", "omdrc-app", "app", "src", "main", "res", "drawable")
DP = 108                         # an adaptive icon layer's side
SAFE_R = 33 - 2                  # the 66 dp safe circle, less a margin

# The level meters' gradient (omdrc-ctrl/src/kiosk/static/widgets/vu.js), quiet at
# the centre of the swirl to loud at its outer end.
METER = ["#1f8f3a", "#3fb950", "#d8c23a", "#e3892b", "#f85149", "#ff2d2d"]
BG = ("#ffffff", "#fbf5ec")      # centre, edge: a light warm white
MONO = "#000000"


def lerp_colour(stops, t):
    t = max(0.0, min(1.0, t)) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    f = t - i
    ca = [int(stops[i][k:k + 2], 16) for k in (1, 3, 5)]
    cb = [int(stops[i + 1][k:k + 2], 16) for k in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(ca, cb))


# One note around its head's centre, head ~1 unit wide, stem up on the right.
HEAD = ("M -0.52 0.08 C -0.52 -0.30 -0.02 -0.44 0.30 -0.34 "
        "C 0.56 -0.26 0.62 0.02 0.44 0.20 C 0.20 0.44 -0.30 0.46 -0.46 0.30 "
        "C -0.51 0.25 -0.53 0.17 -0.52 0.08 Z")
STEM = "M 0.37 0.02 L 0.52 -0.10 L 0.52 -2.35 L 0.40 -2.35 Z"
FLAG = ("M 0.46 -2.35 C 0.62 -1.95 1.18 -1.78 1.12 -1.20 "
        "C 1.10 -0.98 1.02 -0.86 0.94 -0.78 C 1.02 -1.30 0.78 -1.58 0.46 -1.72 Z")
# the extreme points of a note, to keep every note inside the safe circle
OUTLINE = [(-0.53, 0.08), (0.0, 0.42), (0.0, -0.40), (0.62, 0.0), (0.46, -2.35), (1.12, -1.2)]
KINDS = ["eighth", "quarter", "eighth", "eighth", "quarter", "eighth"]


def parts(kind):
    # separate shapes: one path in opposite directions would cancel where they overlap
    return [HEAD, STEM] + ([FLAG] if kind == "eighth" else [])


def layout(count=9, growth=5.0, gap=2.2, follow=0.25, start=0.0, turn=-60):
    """The notes: (x, y, angle, scale, t, kind) in the 108-unit layer, fitted into
    the safe circle.  A nautilus-like spiral (the radius grows `growth` times a
    turn) of about one turn, so no note sits inside an outer one's reach; walking
    out along it, each note is `gap` of its own head-widths past the one before --
    the swirl opens as the notes grow.  The notes stay upright, tilted `follow` of
    the way along the curve; `turn` rotates the whole swirl."""
    b = math.log(growth) / (2 * math.pi)
    psi = math.atan(1 / b)
    size_of = lambda r: 0.24 * (r + 1.2)       # heads grow with the radius; the inner ones still read
    theta, raw = start, []
    for i in range(count):
        r = math.exp(b * theta)
        phi = theta + math.radians(turn)
        tangent = math.degrees(phi + psi)
        wrap = (tangent + 180) % 360 - 180
        angle = follow * (wrap if abs(wrap) <= 90 else wrap - math.copysign(180, wrap))
        size = size_of(r)
        raw.append((r * math.cos(phi), r * math.sin(phi), angle, size, i / (count - 1), KINDS[i % len(KINDS)]))
        theta += gap * size / (r * math.sqrt(1 + b * b))   # arc length = gap * head width

    def points(k, cx, cy):
        for x, y, angle, size, *_ in raw:
            ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            for px, py in OUTLINE:
                yield (cx + k * (x + size * (px * ca - py * sa)), cy + k * (y + size * (px * sa + py * ca)))

    # centre the notes' extent, then scale it to the safe circle
    pts = list(points(1, 0, 0))
    mx = (max(p[0] for p in pts) + min(p[0] for p in pts)) / 2
    my = (max(p[1] for p in pts) + min(p[1] for p in pts)) / 2
    reach = max(math.hypot(px - mx, py - my) for px, py in pts)
    k = SAFE_R / reach
    cx, cy = DP / 2 - k * mx, DP / 2 - k * my
    return [(cx + k * x, cy + k * y, angle, k * size, t, kind) for x, y, angle, size, t, kind in raw]


# ── SVG ──────────────────────────────────────────────────────────────────────

def svg_notes(notes, mono=None):
    return "\n".join(
        f'  <g fill="{mono or lerp_colour(METER, t)}" transform="translate({x:.2f} {y:.2f}) '
        f'rotate({angle:.1f}) scale({s:.3f})">' + "".join(f'<path d="{d}"/>' for d in parts(kind)) + "</g>"
        for x, y, angle, s, t, kind in notes)


def svg_background():
    return f'''  <defs><radialGradient id="bg" cx="50%" cy="45%" r="70%">
    <stop offset="0" stop-color="{BG[0]}"/><stop offset="1" stop-color="{BG[1]}"/>
  </radialGradient></defs>
  <rect width="{DP}" height="{DP}" fill="url(#bg)"/>'''


def svg(body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {DP} {DP}" width="{DP * 4}" height="{DP * 4}">\n{body}\n</svg>\n'


# ── Android vector drawables ─────────────────────────────────────────────────

HEADER = """<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by artwork/draw-app-icon.py: edit that, not this. -->
"""


def vector(body, extra_ns=""):
    return (HEADER + f'<vector xmlns:android="http://schemas.android.com/apk/res/android"{extra_ns}\n'
            f'    android:width="{DP}dp" android:height="{DP}dp"\n'
            f'    android:viewportWidth="{DP}" android:viewportHeight="{DP}">\n{body}</vector>\n')


def vector_notes(notes, mono=None):
    out = []
    for x, y, angle, s, t, kind in notes:
        colour = mono or lerp_colour(METER, t)
        # a group scales, then rotates, then translates: SVG's translate rotate scale
        out.append(f'    <group android:translateX="{x:.2f}" android:translateY="{y:.2f}" '
                   f'android:rotation="{angle:.1f}" android:scaleX="{s:.3f}" android:scaleY="{s:.3f}">\n'
                   + "".join(f'        <path android:fillColor="{colour}" android:pathData="{d}"/>\n' for d in parts(kind))
                   + "    </group>\n")
    return "".join(out)


def vector_background():
    return (f'    <path android:pathData="M0,0 L{DP},0 L{DP},{DP} L0,{DP} Z">\n'
            '        <aapt:attr name="android:fillColor">\n'
            f'            <gradient android:type="radial" android:centerX="{DP / 2}" android:centerY="{DP * 0.45}"\n'
            f'                android:gradientRadius="{DP * 0.7}" android:startColor="{BG[0]}" android:endColor="{BG[1]}"/>\n'
            '        </aapt:attr>\n    </path>\n')


def main():
    os.makedirs(OUT, exist_ok=True)
    notes = layout()
    files = {
        "background.svg": svg(svg_background()),
        "foreground.svg": svg(svg_notes(notes)),
        "monochrome.svg": svg(svg_notes(notes, MONO)),
        "icon.svg": svg(svg_background() + "\n" + svg_notes(notes)),
    }
    for name, text in files.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(text)
    drawables = {
        "ic_launcher_background.xml": vector(vector_background(), '\n    xmlns:aapt="http://schemas.android.com/aapt"'),
        "ic_launcher_foreground.xml": vector(vector_notes(notes)),
        "ic_launcher_monochrome.xml": vector(vector_notes(notes, MONO)),
    }
    for name, text in drawables.items():
        with open(os.path.join(RES, name), "w", encoding="utf-8") as f:
            f.write(text)
    print("wrote", ", ".join(files), "and", ", ".join(drawables))

    # Google Play: 512 px, the full square (Play applies its own mask)
    icon_svg = os.path.join(OUT, "icon.svg")
    play = os.path.join(OUT, "playstore-512.png")
    subprocess.run(["rsvg-convert", "-w", "512", "-h", "512", icon_svg, "-o", play], check=True)

    # Previews: the launcher shows the middle 72 of the 108 units, masked
    tmp = os.path.join(OUT, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    big = os.path.join(tmp, "big.png")
    subprocess.run(["rsvg-convert", "-w", "648", "-h", "648", icon_svg, "-o", big], check=True)
    mono_big = os.path.join(tmp, "mono.png")
    subprocess.run(["rsvg-convert", "-w", "648", "-h", "648", os.path.join(OUT, "monochrome.svg"), "-o", mono_big], check=True)
    crop = ["-gravity", "center", "-crop", "432x432+0+0", "+repage"]
    tiles = []
    for mask, draw in (("circle", "circle 216,216 216,0"), ("squircle", "roundrectangle 0,0 431,431 130,130")):
        for px in (192, 96, 48):
            out = os.path.join(tmp, f"{mask}-{px}.png")
            subprocess.run(["magick", big, "-alpha", "set", *crop, "(", "-size", "432x432", "xc:none", "-fill", "white", "-draw", draw, ")",
                            "-compose", "DstIn", "-composite", "-resize", f"{px}x{px}", out], check=True)
            tiles.append(out)
    # themed (Android 13+): the monochrome layer tinted, on a tinted disc
    themed = os.path.join(tmp, "themed.png")
    subprocess.run(["magick", "-size", "432x432", "xc:none", "-fill", "#d6e3ff", "-draw", "circle 216,216 216,0",
                    "(", mono_big, *crop, "-fill", "#1b3a73", "-colorize", "100", ")", "-compose", "over", "-composite",
                    "-resize", "96x96", themed], check=True)
    sheet = os.path.join(OUT, "preview.png")
    row = lambda items: ["(", *sum([["(", f, "-background", "none", "-gravity", "center", "-extent", "220x220", ")"] for f in items], []), "+append", ")"]
    subprocess.run(["magick", *row(tiles[:3] + [themed]), *row(tiles[3:]),
                    "-append", "-background", "#3c4450", "-alpha", "remove", sheet], check=True)
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    print("wrote app-icon/playstore-512.png and app-icon/preview.png")


if __name__ == "__main__":
    main()
