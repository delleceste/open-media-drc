#!/usr/bin/env python3
"""The app's launcher icon: "omdrc" in an elegant italic, its "d" a green note.

"om" white, the d an eighth note in green -- its head in the d's bowl, its stem
rising well above the letters, slanted with the italic -- "r" yellow and "c" red,
the level meters' colours, on a near-black background.

An Android adaptive icon (API 26+): three 108 x 108 dp layers the launcher masks
to its own shape (a circle on a Pixel, a squircle or rounded square elsewhere):

  background  opaque: a dark radial gradient, full bleed
  foreground  transparent: the word and its note, all inside the central 66 dp
              circle, the part every mask keeps
  monochrome  the same in one colour, for Android 13+ themed icons

Writes the app's drawables, SVG copies, the Google Play icon (512 px, full square:
Play masks it) and a sheet previewing the icon under a circle and a squircle at
192, 96 and 48 px, and themed:

  android/omdrc-app/app/src/main/res/drawable/ic_launcher_{background,foreground,monochrome}.xml
  artwork/app-icon/{background,foreground,monochrome,icon}.svg
  artwork/app-icon/playstore-512.png, artwork/app-icon/preview.png

    python3 artwork/draw-app-icon.py     (needs fontTools -- pip install fonttools --
                                          and, for the PNGs, rsvg-convert and magick)
"""
import math
import os
import subprocess

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app-icon")
RES = os.path.join(HERE, "..", "android", "omdrc-app", "app", "src", "main", "res", "drawable")
DP = 108                         # an adaptive icon layer's side
SAFE_R = 33 - 2                  # the 66 dp safe circle, less a margin

BG = ("#1b2028", "#07090c")      # centre, edge
WORD = "omdrc"
WORD_FONT = "/usr/share/fonts/noto/NotoSerifDisplay-LightItalic.ttf"
WORD_WIDTH = 60                  # of the 108 units, before fitting in the safe circle
# the level meters' colours (omdrc-ctrl/src/kiosk/static/widgets/vu.js), plain
COLOUR = {"o": "#f4f1ea", "m": "#f4f1ea", "d": "#22c55e", "r": "#f5b700", "c": "#ff3b30"}
MONO = "#000000"
SLANT = math.tan(math.radians(12))   # the italic's angle


def note_d():
    """The note that stands for the d, in the d's font units (y up): a head in the
    d's bowl, a stem rising well above the letters, an eighth-note flag -- slanted
    with the italic.  Each shape a closed polygon."""
    cx, cy, rx, ry, tilt = 232, 150, 232, 160, math.radians(22)
    head = []
    for i in range(64):
        a = 2 * math.pi * i / 64
        x, y = rx * math.cos(a), ry * math.sin(a)
        head.append((cx + x * math.cos(tilt) - y * math.sin(tilt), cy + x * math.sin(tilt) + y * math.cos(tilt)))
    top, sx0, sx1 = 1180, 418, 470
    stem = [(sx0, 190), (sx1, 225), (sx1, top), (sx0, top)]
    # the flag, full enough for the tall stem: out and down along an outer curve,
    # back up along an inner one, the body between them thick at its middle
    flag = [(sx0, top)]
    for i in range(1, 17):
        t = i / 16
        flag.append((sx1 + 262 * math.sin(t * math.pi * 0.9) * (1 - 0.3 * t), top - 565 * t))
    for i in range(16, -1, -1):
        t = i / 16
        flag.append((sx1 + 160 * math.sin(t * math.pi * 0.9) * (1 - 0.3 * t), top - 115 - 400 * t))
    return [[(x + y * SLANT, y) for x, y in shape] for shape in (head, stem, flag)]


def mark():
    """[(SVG path data, colour)] in the 108-unit layer: the letters and the note,
    centred and scaled to stay inside the safe circle."""
    font = TTFont(WORD_FONT)
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    placed, x = [], 0
    for ch in WORD:
        name = cmap[ord(ch)]
        placed.append((ch, name, x))
        x += font["hmtx"][name][0]
    bounds = BoundsPen(glyphs)
    for ch, name, gx in placed:
        glyphs[name].draw(TransformPen(bounds, (1, 0, 0, 1, gx, 0)))
    x0, _, x1, _ = bounds.bounds
    k = WORD_WIDTH / (x1 - x0)
    d_at = next(gx for ch, _, gx in placed if ch == "d")
    note = note_d()

    # the whole mark's extent (font units, y up), for centring and fitting
    pts = [(d_at + px, py) for shape in note for px, py in shape]
    for ch, name, gx in placed:
        if ch != "d":
            b = BoundsPen(glyphs)
            glyphs[name].draw(b)
            pts += [(gx + b.bounds[0], b.bounds[1]), (gx + b.bounds[2], b.bounds[3])]
    mx0, mx1 = min(p[0] for p in pts), max(p[0] for p in pts)
    my0, my1 = min(p[1] for p in pts), max(p[1] for p in pts)
    reach = math.hypot(mx1 - mx0, my1 - my0) / 2 * k
    s = k * min(1.0, (SAFE_R + 1) / reach)
    # font units -> layer: scaled by s, y flipped, the extent's centre on the layer's
    ox, oy = DP / 2 - s * (mx0 + mx1) / 2, DP / 2 + s * (my0 + my1) / 2

    out = []
    for ch, name, gx in placed:
        if ch == "d":
            path = " ".join("M " + " L ".join(f"{ox + s * (d_at + px):.2f} {oy - s * py:.2f}" for px, py in shape) + " Z"
                            for shape in note)
        else:
            pen = SVGPathPen(glyphs)
            glyphs[name].draw(TransformPen(pen, (s, 0, 0, -s, ox + s * gx, oy)))
            path = pen.getCommands()
        out.append((path, COLOUR[ch]))
    return out


# ── SVG ──────────────────────────────────────────────────────────────────────

def svg(body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {DP} {DP}" width="{DP * 4}" height="{DP * 4}">\n{body}\n</svg>\n'


def svg_background():
    return f'''  <defs><radialGradient id="bg" cx="50%" cy="45%" r="70%">
    <stop offset="0" stop-color="{BG[0]}"/><stop offset="1" stop-color="{BG[1]}"/>
  </radialGradient></defs>
  <rect width="{DP}" height="{DP}" fill="url(#bg)"/>'''


def svg_mark(shapes, mono=None):
    return "\n".join(f'  <path d="{d}" fill="{mono or colour}"/>' for d, colour in shapes)


# ── Android vector drawables ─────────────────────────────────────────────────

HEADER = """<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by artwork/draw-app-icon.py: edit that, not this. -->
"""


def vector(body, extra_ns=""):
    return (HEADER + f'<vector xmlns:android="http://schemas.android.com/apk/res/android"{extra_ns}\n'
            f'    android:width="{DP}dp" android:height="{DP}dp"\n'
            f'    android:viewportWidth="{DP}" android:viewportHeight="{DP}">\n{body}</vector>\n')


def vector_background():
    return (f'    <path android:pathData="M0,0 L{DP},0 L{DP},{DP} L0,{DP} Z">\n'
            '        <aapt:attr name="android:fillColor">\n'
            f'            <gradient android:type="radial" android:centerX="{DP / 2}" android:centerY="{DP * 0.45}"\n'
            f'                android:gradientRadius="{DP * 0.7}" android:startColor="{BG[0]}" android:endColor="{BG[1]}"/>\n'
            '        </aapt:attr>\n    </path>\n')


def vector_mark(shapes, mono=None):
    return "".join(f'    <path android:fillColor="{mono or colour}" android:pathData="{d}"/>\n' for d, colour in shapes)


def main():
    os.makedirs(OUT, exist_ok=True)
    shapes = mark()
    files = {
        "background.svg": svg(svg_background()),
        "foreground.svg": svg(svg_mark(shapes)),
        "monochrome.svg": svg(svg_mark(shapes, MONO)),
        "icon.svg": svg(svg_background() + "\n" + svg_mark(shapes)),
    }
    for name, text in files.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(text)
    drawables = {
        "ic_launcher_background.xml": vector(vector_background(), '\n    xmlns:aapt="http://schemas.android.com/aapt"'),
        "ic_launcher_foreground.xml": vector(vector_mark(shapes)),
        "ic_launcher_monochrome.xml": vector(vector_mark(shapes, MONO)),
    }
    for name, text in drawables.items():
        with open(os.path.join(RES, name), "w", encoding="utf-8") as f:
            f.write(text)
    print("wrote", ", ".join(files), "and", ", ".join(drawables))

    # Google Play: 512 px, the full square (Play applies its own mask)
    icon_svg = os.path.join(OUT, "icon.svg")
    subprocess.run(["rsvg-convert", "-w", "512", "-h", "512", icon_svg, "-o", os.path.join(OUT, "playstore-512.png")], check=True)

    # Previews: the launcher shows the middle 72 of the 108 units, masked
    tmp = os.path.join(OUT, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    big, mono_big = os.path.join(tmp, "big.png"), os.path.join(tmp, "mono.png")
    subprocess.run(["rsvg-convert", "-w", "648", "-h", "648", icon_svg, "-o", big], check=True)
    subprocess.run(["rsvg-convert", "-w", "648", "-h", "648", os.path.join(OUT, "monochrome.svg"), "-o", mono_big], check=True)
    crop = ["-gravity", "center", "-crop", "432x432+0+0", "+repage"]
    tiles = []
    for mask, draw in (("circle", "circle 216,216 216,0"), ("squircle", "roundrectangle 0,0 431,431 130,130")):
        masked = os.path.join(tmp, f"{mask}.png")
        subprocess.run(["magick", big, "-alpha", "set", *crop, "(", "-size", "432x432", "xc:none", "-fill", "white",
                        "-draw", draw, ")", "-compose", "DstIn", "-composite", masked], check=True)
        for px in (192, 96, 48):
            out = os.path.join(tmp, f"{mask}-{px}.png")
            subprocess.run(["magick", masked, "-resize", f"{px}x{px}", out], check=True)
            tiles.append(out)
    # themed (Android 13+): the monochrome layer tinted, on a tinted disc
    themed = os.path.join(tmp, "themed.png")
    mono_tint = os.path.join(tmp, "mono-tint.png")
    subprocess.run(["magick", mono_big, *crop, "-fill", "#1b3a73", "-colorize", "100", mono_tint], check=True)
    subprocess.run(["magick", "-size", "432x432", "xc:none", "-fill", "#d6e3ff", "-draw", "circle 216,216 216,0",
                    mono_tint, "-compose", "over", "-composite", "-resize", "96x96", themed], check=True)
    cells = []
    for i, f in enumerate(tiles[:3] + [themed] + tiles[3:]):
        cell = os.path.join(tmp, f"cell-{i}.png")
        subprocess.run(["magick", f, "-background", "#5a6472", "-alpha", "remove", "-gravity", "center",
                        "-extent", "220x220", cell], check=True)
        cells.append(cell)
    row1, row2 = os.path.join(tmp, "row1.png"), os.path.join(tmp, "row2.png")
    subprocess.run(["magick", *cells[:4], "+append", row1], check=True)
    subprocess.run(["magick", *cells[4:], "+append", row2], check=True)
    subprocess.run(["magick", row1, row2, "-background", "#5a6472", "-append", os.path.join(OUT, "preview.png")], check=True)
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    print("wrote app-icon/playstore-512.png and app-icon/preview.png")


if __name__ == "__main__":
    main()
