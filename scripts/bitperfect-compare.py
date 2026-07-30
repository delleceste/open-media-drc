#!/usr/bin/env python3
"""Compare two USB-wire tap captures — did both systems send the DAC the
same bytes?

Usage:
    bitperfect-compare.py A B
    # e.g. full payloads on one machine:
    bitperfect-compare.py bp-results/song-linux.wav bp-results/song-freebsd.wav
    # or across machines via git, transferring only the tiny .txt reports:
    bitperfect-compare.py bp-results/song-linux.wav bp-results/song-freebsd.txt
    bitperfect-compare.py bp-results/song-linux.txt bp-results/song-freebsd.txt

A and B are artifacts of bitperfect-tap-linux.sh / bitperfect-tap-freebsd.sh,
in any combination of three forms:

  PREFIX.wav       the source-aligned tap.  The 44-byte WAV header is
                   STRIPPED before comparing — only PCM payload bytes are
                   judged, so a header difference could never mask or fake
                   a payload difference.
  PREFIX.txt       the run's report, which records the tap payload's exact
                   length and sha256.  Comparison is then done BY HASH:
                   sha256 equality on equal-length payloads proves
                   byte-identity just as strongly as cmp(1) — this is what
                   lets the 10 MB streams stay out of git while only the
                   ~600-byte reports travel between machines.  (On a
                   hash MISMATCH the differing offset cannot be shown from
                   a report alone; transfer the .wav for forensics.)
  PREFIX.wire.raw  the full untrimmed wire stream.  Starts at an arbitrary
                   point (capture lead-in, priming bytes), so it is
                   auto-aligned against the other side before comparing.
                   Not combinable with a .txt report: an untrimmed stream
                   cannot be checked against a hash of the aligned region.

Verdicts (colour-coded, machine-readable via exit status):
  exit 0  MATCH     — payloads byte-for-byte identical (directly, or
                      proven via equal sha256 of equal-length payloads)
  exit 1  MISMATCH  — payloads differ; when both payloads are present the
                      first differing offset is printed with hex context
  exit 2  usage / unreadable input / uncomparable combination

Both inputs must use the same wire container.  The tap scripts always
produce S32_LE-container streams (both DACs only accept 4-byte
containers), so taps of the same input WAV are directly comparable.  If a
future DAC negotiated a 3-byte-packed container (S24_3LE), its capture
would carry the same audio bits WITHOUT the zero pad byte per sample, and
a byte comparison against a 4-byte-container capture would report a
mismatch on every sample; such a capture would need the pad bytes
stripped/inserted (normalization) first — deliberately NOT implemented,
as no such DAC is in use.
"""
import hashlib
import re
import sys
import wave


def load(path):
    """Load a tap artifact into a uniform dict:

    {desc:  human-readable format note,
     n:     payload length in bytes,
     sha:   sha256 of the payload,
     data:  the payload bytes, or None for a .txt report (hash only),
     raw:   True when the payload is an untrimmed wire stream}
    """
    if path.endswith(".txt"):
        # Parse the lines finalize() writes:
        #   tap wav    : PREFIX.wav  (10584000 PCM bytes, sha256 <64 hex>)
        #   verdict    : BIT-PERFECT — ...
        txt = open(path).read()
        m = re.search(r"tap wav\s*:\s*.*\((\d+) PCM bytes, sha256 ([0-9a-f]{64})\)", txt)
        if not m:
            sys.exit(f"{path}: no 'tap wav ... sha256' line — not a tap report "
                     "(or the run failed before writing the tap wav)")
        v = re.search(r"verdict\s*:\s*(.+)", txt)
        osl = re.search(r"os\s*:\s*(.+)", txt)
        desc = "report" + (f", {osl.group(1).strip()}" if osl else "")
        if v:
            desc += f" — verdict: {v.group(1).strip()}"
        return {"desc": desc, "n": int(m.group(1)), "sha": m.group(2),
                "data": None, "raw": False}
    if path.endswith(".wav"):
        w = wave.open(path, "rb")
        pcm = w.readframes(w.getnframes())
        desc = f"{w.getnchannels()}ch S{8*w.getsampwidth()}_LE {w.getframerate()} Hz"
        w.close()
        return {"desc": desc, "n": len(pcm),
                "sha": hashlib.sha256(pcm).hexdigest(), "data": pcm, "raw": False}
    b = open(path, "rb").read()
    return {"desc": "raw wire stream (untrimmed)", "n": len(b),
            "sha": hashlib.sha256(b).hexdigest(), "data": b, "raw": True}


def align(a, b):
    """Align raw wire streams that start at different points.

    Picks a 4 KiB probe from A at the first offset whose window holds at
    least 256 non-zero bytes (skipping leading silence/priming zeros — a
    mostly-zero probe would false-match inside any zero run), finds it in
    B, and trims the stream that starts earlier so both begin at the same
    stream position.  Returns (a', b') or None when no alignment exists —
    i.e. the streams share no such 4 KiB run at all.
    """
    po = 0
    while po + 8192 < len(a):
        if sum(1 for x in a[po:po + 4096] if x) >= 256:
            break
        po += 4096
    pos = b.find(a[po:po + 4096])
    if pos < 0:
        return None
    start = pos - po        # b-offset of a's byte 0
    if start >= 0:
        return a, b[start:]     # b starts earlier: drop its lead-in
    return a[-start:], b        # a starts earlier: drop its lead-in


GRN, RED, OFF = "\033[32m", "\033[31m", "\033[0m"


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    pa, pb = sys.argv[1], sys.argv[2]
    A, B = load(pa), load(pb)
    print(f"A: {pa}  ({A['n']} bytes, {A['desc']})  sha256 {A['sha']}")
    print(f"B: {pb}  ({B['n']} bytes, {B['desc']})  sha256 {B['sha']}")

    # ── hash-proxy path: at least one side is a .txt report ──────────────────
    if A["data"] is None or B["data"] is None:
        other = B if A["data"] is None else A
        if other["raw"]:
            print(f"{RED}cannot compare an untrimmed .wire.raw against a "
                  f"report hash — use the aligned PREFIX.wav{OFF}")
            return 2
        if A["n"] == B["n"] and A["sha"] == B["sha"]:
            print(f"{GRN}MATCH: payload sha256 identical over {A['n']} bytes "
                  f"— byte-by-byte identity proven by hash{OFF}")
            return 0
        why = ("lengths differ" if A["n"] != B["n"] else "sha256 differs")
        print(f"{RED}MISMATCH: {why} (A: {A['n']} bytes, B: {B['n']} bytes). "
              f"Transfer the .wav files to locate the first differing "
              f"offset.{OFF}")
        return 1

    # ── full-payload paths ───────────────────────────────────────────────────
    a, b = A["data"], B["data"]
    # Fast path: two source-aligned taps of the same input are simply equal.
    if a == b:
        print(f"{GRN}MATCH: files are byte-by-byte identical "
              f"({len(a)} bytes){OFF}")
        return 0

    # Slow path: at least one side is an untrimmed wire stream — align,
    # then compare the overlap byte by byte.
    r = align(a, b)
    if r is None:
        print(f"{RED}MISMATCH: no common alignment — the streams do not "
              f"share even a 4 KiB run of identical bytes{OFF}")
        return 1
    a2, b2 = r
    n = min(len(a2), len(b2))
    i = 0
    while i < n and a2[i] == b2[i]:
        i += 1
    if i == n:
        # Every overlapping byte matched; the lengths differ only because
        # the two captures started/stopped at different wire positions.
        print(f"{GRN}MATCH: {n} overlapping bytes identical{OFF} "
              f"(lengths differ only by capture extent: A' {len(a2)}, B' {len(b2)})")
        return 0
    hx = lambda buf, o: " ".join(f"{x:02x}" for x in buf[o:o + 16])
    print(f"{RED}MISMATCH: first difference at aligned offset {i} of {n}{OFF}")
    print(f"  A: {hx(a2, i)}")
    print(f"  B: {hx(b2, i)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
