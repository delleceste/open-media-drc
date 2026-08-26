#!/usr/bin/env python3
"""Test material for the bit-perfect suite: load any track, or find the one a
renderer is streaming right now.

Why this exists
===============
`tests/gen-bitperfect-wav.py` produces the *most sensitive* possible test
signal — a near-silent counter whose every (L,R) pair is unique, so any
altered, dropped or duplicated sample is detectable at any offset.  That is
the right asset for proving the chain.

It is not the only thing an owner wants to verify.  "Is *this* album playing
bit-perfectly?" is a fair question, and answering it needs arbitrary
material: a WAV, a FLAC, or whatever the renderer just buffered off Qobuz.
This module turns any of those into the two things a run needs —

    play_path   what to hand the player (the ORIGINAL file wherever possible,
                because the decoder is part of what is under test)
    ref.raw     the reference byte stream, decoded to PCM and promoted to the
                S32_LE wire container by bitperfect-lib.py's `prep`

— and reports whether the material is safe to align on.

Subcommands
===========
load INPUT --out-dir DIR
    Decode INPUT (WAV / FLAC / anything ffmpeg reads) into DIR, run `prep`,
    check the alignment anchor, print a JSON description.

resolve-live --out-dir DIR [--since EPOCH] [--mpd-port N]
    Find the file the active renderer is streaming and load it the same way.
    Used by the `live` source, where nothing is played by us and the
    reference has to be discovered after the fact.

Alignment and real music
========================
`finalize` locates the reference inside the capture by searching for a 4 KiB
"anchor" taken from the reference.  On the generated asset any window is
unique.  Real music is different: digital silence, a repeated bar or a looped
intro can make a window occur more than once, and aligning on the wrong
occurrence would be a SILENT error — the comparison would then be off by
whole seconds and report corruption that is not there.

`find_probe_offset(ref, unique_in=ref)` in bitperfect-lib.py already skips
ambiguous windows.  This module reports the result up front (`anchor_unique`)
so the page can warn before a run rather than after it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import wave


HERE = Path(__file__).resolve().parent
LIB = HERE / "bitperfect-lib.py"

# Where qobuzconnect2mpd stages what it feeds MPD.  Both literals are present
# in /usr/local/bin/qobuzconnect2mpd, and the two hold DIFFERENT things, so
# each carries how to read it:
#
#   "tracks"   a prefix directory (the uid is appended) holding COMPLETE files
#              one directory down, e.g.
#                  /tmp/qobuzconnect2mpd-1001/cache/track_<id>_<fmt>_<pid>_<n>.flac
#              Each file is a whole track: the reference is one of them, chosen
#              by mtime.  Verified against a live renderer on this box.
#   "segments" one directory holding a single track split into numbered parts,
#              which must be concatenated in NUMERIC order.
#
# Getting this pair the wrong way round would be silently destructive:
# concatenating a "tracks" directory would splice unrelated songs together and
# report corruption that never happened.
#
# These are hints, not assumptions: resolve_live() prefers what MPD actually
# reports, and falls back to open file descriptors, before trusting a path.
BUFFER_HINTS = (("/tmp/qobuzconnect2mpd-", "tracks"),
                ("/tmp/qconnect2mpd-segmented", "segments"))

AUDIO_SUFFIXES = {".wav", ".flac", ".alac", ".m4a", ".mp4", ".aiff", ".aif",
                  ".ape", ".wv", ".dsf", ".mp3", ".ogg", ".opus", ".aac"}
# Formats whose decode is not sample-exact against the stored bytes.  A run on
# one of these can still prove the chain is transparent, but the verdict is
# about the DECODER's output, so the page must not call it "bit-perfect".
LOSSY_SUFFIXES = {".mp3", ".ogg", ".opus", ".aac", ".m4a", ".mp4"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, **kw)


# ── decoding ────────────────────────────────────────────────────────────────

def decode_to_wav(src: Path, dest: Path) -> str:
    """Decode `src` to a PCM WAV at `dest`, returning the decoder used.

    Bit depth and rate are preserved: the point is to reproduce the bytes the
    player will send, not to normalise them.  `flac -d` is preferred for FLAC
    because it is exact by construction and always present where FLAC is;
    ffmpeg covers everything else."""
    if src.suffix.lower() == ".flac" and shutil.which("flac"):
        r = run(["flac", "-d", "-s", "-f", "-o", str(dest), str(src)])
        if r.returncode == 0:
            return "flac -d"
        # fall through to ffmpeg rather than failing on a flac(1) quirk
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            f"cannot decode {src.name}: install flac (for .flac) or ffmpeg")
    # -c:a pcm_s24le etc. would force a width; letting ffmpeg pick the native
    # one keeps 16-bit material 16-bit and 24-bit material 24-bit.
    r = run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(src),
             "-map", "0:a:0", "-c:a", "pcm_s32le", str(dest)])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode {src.name}: "
                           f"{(r.stderr or r.stdout).strip()[:300]}")
    return "ffmpeg"


def concat_segments(directory: Path, dest: Path) -> Path:
    """Join a segmented buffer into one file, ordered NUMERICALLY.

    Lexical order puts part10 before part2 and would silently scramble the
    stream, so the trailing integer in each name is what sorts."""
    def key(p: Path):
        m = re.findall(r"(\d+)", p.name)
        return (int(m[-1]) if m else 0, p.name)

    parts = sorted((p for p in directory.iterdir() if p.is_file()), key=key)
    if not parts:
        raise RuntimeError(f"segmented buffer {directory} is empty")
    with open(dest, "wb") as out:
        for p in parts:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out)
    return dest


# ── loading ─────────────────────────────────────────────────────────────────

def probe_anchor(refraw: Path) -> tuple[int, bool]:
    """Offset of the alignment anchor, and whether it is unambiguous.

    Reuses bitperfect-lib.py's own chooser so the page can never disagree
    with what finalize will actually do."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("bplib", LIB)
    bplib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bplib)
    ref = open(refraw, "rb").read()
    offset = bplib.find_probe_offset(ref, unique_in=ref)
    return offset, ref.count(ref[offset:offset + 4096]) == 1


def load(src: Path, out_dir: Path, *, play_original: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._+-]", "_", src.stem)[:80] or "material"
    lossy = src.suffix.lower() in LOSSY_SUFFIXES

    if src.suffix.lower() == ".wav":
        wav, decoder = src, "none (already PCM WAV)"
    else:
        wav = out_dir / f"{stem}.decoded.wav"
        decoder = decode_to_wav(src, wav)

    with wave.open(str(wav), "rb") as w:
        rate, ch, sw, frames = (w.getframerate(), w.getnchannels(),
                                w.getsampwidth(), w.getnframes())

    refraw = out_dir / f"{stem}.ref.raw"
    r = run([sys.executable, str(LIB), "prep", str(wav), str(refraw)])
    if r.returncode != 0:
        raise RuntimeError(f"prep failed: {(r.stderr or r.stdout).strip()}")

    anchor, unique = probe_anchor(refraw)
    return {
        "ok": True,
        "name": src.name,
        "source": str(src),
        # MPD and upmpdcli decode for themselves — that decode is part of what
        # is under test, so they get the original.  aplay/the OSS writer take
        # raw PCM only, and the runner uses ref.raw there instead.
        "play_path": str(src if play_original else wav),
        "wav": str(wav),
        "ref_raw": str(refraw),
        "rate": rate, "channels": ch, "bits": sw * 8, "frames": frames,
        "seconds": round(frames / rate, 3) if rate else 0,
        "ref_bytes": refraw.stat().st_size,
        "sha256": sha256_file(src),
        "decoder": decoder,
        "lossy": lossy,
        "anchor_offset": anchor,
        "anchor_unique": unique,
        "warning": ("decoded from a lossy container — a clean run proves the "
                    "chain carried the DECODER's output unchanged, which is "
                    "transparency, not bit-perfection of the file"
                    if lossy else
                    "the alignment anchor is not unique in this material; "
                    "alignment may be ambiguous" if not unique else ""),
    }


# ── live: what is the renderer actually streaming? ──────────────────────────

def mpd_current(port: str | None = None) -> str | None:
    """The URI MPD reports for the playing song, if it is a local file."""
    client = shutil.which("mpc") or shutil.which("musicpc")
    if not client:
        return None
    argv = [client]
    if port:
        argv += ["-p", str(port)]
    r = run(argv + ["--format", "%file%", "current"], timeout=5)
    uri = (r.stdout or "").strip()
    if not uri:
        return None
    if uri.startswith("file://"):
        return uri[len("file://"):]
    return uri if uri.startswith("/") else None


def open_media_fds() -> list[str]:
    """Media files currently held open by MPD or a renderer.

    The catch-all: it needs no knowledge of any renderer's buffering scheme
    and works on both OSes.  Linux reads /proc/PID/fd; FreeBSD has no such
    tree for other processes, so fstat(1) is the equivalent."""
    names = ("mpd", "musicpd", "qobuzconnect2mpd", "upmpdcli")
    found: list[str] = []
    if Path("/proc/self/fd").is_dir():                      # Linux
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                comm = (proc / "comm").read_text().strip()
            except OSError:
                continue
            if comm not in names:
                continue
            try:
                entries = list((proc / "fd").iterdir())
            except OSError:
                continue
            for fd in entries:
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if Path(target).suffix.lower() in AUDIO_SUFFIXES:
                    found.append(target)
    elif shutil.which("fstat"):                             # FreeBSD
        r = run(["fstat"], timeout=10)
        for line in (r.stdout or "").splitlines():
            fields = line.split()
            if len(fields) < 2 or fields[1] not in names:
                continue
            m = re.search(r"(/\S+)", line)
            if m and Path(m.group(1)).suffix.lower() in AUDIO_SUFFIXES:
                found.append(m.group(1))
    # de-duplicate, keep order
    return list(dict.fromkeys(found))


def _hint_roots(prefix: str) -> list[Path]:
    """Directories matching a hint prefix (the renderer appends the uid)."""
    base = Path(prefix)
    if base.is_dir():
        return [base]
    if not base.parent.is_dir():
        return []
    try:
        return [p for p in base.parent.iterdir()
                if p.name.startswith(base.name) and p.is_dir()]
    except OSError:
        return []


def scan_buffer_hints(since: float) -> list[tuple[str, str]]:
    """Renderer buffers that changed during the tap, newest first.

    Returns (path, kind) pairs so the caller knows whether it found a whole
    track or a directory of segments to join.

    `since` is what makes this safe: a stale buffer from an earlier track sits
    in /tmp indefinitely — the cache here held two completed tracks hours old —
    and comparing this capture against one of those would report a fault that
    never happened."""
    hits: list[tuple[float, str, str]] = []
    for prefix, kind in BUFFER_HINTS:
        for root in _hint_roots(prefix):
            try:
                if kind == "segments":
                    parts = [p for p in root.iterdir() if p.is_file()]
                    if parts:
                        newest = max(p.stat().st_mtime for p in parts)
                        if newest >= since:
                            hits.append((newest, str(root), kind))
                    continue
                # "tracks": complete files, one directory down (cache/), so
                # walk rather than listing the root — which holds only dirs.
                for path in root.rglob("*"):
                    if not path.is_file():
                        continue
                    if path.suffix.lower() not in AUDIO_SUFFIXES:
                        continue
                    mtime = path.stat().st_mtime
                    if mtime >= since:
                        hits.append((mtime, str(path), kind))
            except OSError:
                continue
    hits.sort(reverse=True)
    return [(path, kind) for _, path, kind in hits]


def resolve_live(out_dir: Path, since: float, port: str | None = None) -> dict:
    """Find, then load, whatever the renderer streamed during the tap window."""
    tried: list[str] = []

    current = mpd_current(port)
    if current and Path(current).is_file():
        tried.append(f"mpc current -> {current}")
        result = load(Path(current), out_dir)
        result["resolved_by"] = "mpc current"
        result["tried"] = tried
        return result
    tried.append(f"mpc current -> {current or '(not a local file)'}")

    for candidate in open_media_fds():
        tried.append(f"open fd -> {candidate}")
        if Path(candidate).is_file():
            result = load(Path(candidate), out_dir)
            result["resolved_by"] = "open file descriptor"
            result["tried"] = tried
            return result

    for candidate, kind in scan_buffer_hints(since):
        tried.append(f"buffer hint ({kind}) -> {candidate}")
        path = Path(candidate)
        if kind == "segments":
            joined = out_dir / "segments.bin"
            concat_segments(path, joined)
            # A concatenated segment stream has no reliable suffix; ffmpeg
            # sniffs the container, so give it a neutral name.
            result = load(joined, out_dir, play_original=False)
            result["resolved_by"] = f"segmented buffer {candidate}"
            result["tried"] = tried
            return result
        result = load(path, out_dir)
        result["resolved_by"] = f"renderer buffer {candidate}"
        result["tried"] = tried
        return result

    raise RuntimeError(
        "could not find what the renderer streamed. Tried: "
        + "; ".join(tried)
        + ". Play a track and keep it playing for the whole tap window.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("load", help="load a WAV/FLAC/decodable file")
    a.add_argument("input")
    a.add_argument("--out-dir", required=True)
    a.add_argument("--decoded-for-play", action="store_true",
                   help="play the decoded PCM instead of the original "
                        "(aplay / OSS writer sources, which take raw PCM)")

    b = sub.add_parser("resolve-live", help="find what a renderer is streaming")
    b.add_argument("--out-dir", required=True)
    b.add_argument("--since", type=float, default=0.0)
    b.add_argument("--mpd-port", default=None)

    args = p.parse_args()
    try:
        if args.cmd == "load":
            info = load(Path(args.input).resolve(), Path(args.out_dir),
                        play_original=not args.decoded_for_play)
        else:
            info = resolve_live(Path(args.out_dir), args.since or time.time() - 3600,
                                args.mpd_port)
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 2
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
