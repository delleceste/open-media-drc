#!/usr/bin/env python3
"""Generate the deterministic bit-perfect test WAV (S32_LE, stereo).

Signal design
=============
A near-silent (~ -90 dBFS) per-sample counter with DISTINCT L/R channels:

    L = i & 0xFFFF                       (i = frame index)
    R = (i*40503 + (i >> 16)) & 0xFFFF

Why these formulas:

* **Low 16 bits only** — the sample values never exceed 0xFFFF in a 32-bit
  container, i.e. peak ~ -90 dBFS: inaudible through an amplifier, yet
  maximally sensitive, since truncation, dithering, non-unity volume or
  resampling all corrupt low bits deterministically.
* **Distinct L and R** (40503 is odd, so i -> i*40503 mod 2^16 is a
  bijection and R visits all 65536 values in a scrambled order) — catches
  a channel swap or channel duplication, which identical channels would
  miss.
* **Every (L,R) pair is unique over the whole file** — this is what the
  `i >> 16` term adds compared to the original 100000-frame asset
  (`tests/bitperfect-test-44100-s32-stereo.wav`), whose pattern repeats
  every 65536 frames (~1.5 s @ 44100).  Proof: if frames i and j collide,
  L forces i ≡ j (mod 2^16), so j = i + 2^16·k; then
  R_j − R_i ≡ 2^16·k·40503 + (j>>16) − (i>>16) ≡ 0 + k (mod 2^16),
  which is non-zero for 0 < k < 2^16.  Hence pairs are unique for files up
  to 2^32 frames.  Uniqueness makes USB-capture alignment unambiguous at
  any file length and makes every dropped, duplicated, swapped or altered
  sample detectable at any offset.

Determinism
===========
The output depends only on the arguments: the same command produces the
byte-identical file on any OS / architecture / Python version (the wave
module always writes a canonical 44-byte RIFF header for these
parameters).  That is why the canonical 30 s asset need not be committed —
regenerate it anywhere and check the printed SHA-256 against
tests/README.md:

    ./gen-bitperfect-wav.py bitperfect-test-44100-s32-stereo-30s.wav

The canonical asset is the common input for the cross-OS USB tap suite
(scripts/bitperfect-tap-linux.sh / -freebsd.sh / bitperfect-compare.py).
"""
import argparse
import hashlib
import struct
import wave

p = argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("wav", help="output WAV path")
p.add_argument("--frames", type=int, default=1323000, help="frame count (default 30 s @ 44100)")
p.add_argument("--rate", type=int, default=44100, help="sample rate (default 44100)")
a = p.parse_args()

buf = bytearray()
for i in range(a.frames):
    buf += struct.pack("<ii", i & 0xFFFF, (i * 40503 + (i >> 16)) & 0xFFFF)

w = wave.open(a.wav, "wb")
w.setnchannels(2)
w.setsampwidth(4)       # 4 bytes -> S32_LE (the wire container of both DACs)
w.setframerate(a.rate)
w.writeframes(buf)
w.close()

digest = hashlib.sha256(open(a.wav, "rb").read()).hexdigest()
print(f"{a.wav}: {a.frames} frames, S32_LE stereo @ {a.rate} Hz "
      f"({a.frames / a.rate:.2f} s, {44 + len(buf)} bytes)")
print(f"sha256 {digest}")
