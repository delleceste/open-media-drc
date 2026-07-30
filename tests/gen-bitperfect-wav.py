#!/usr/bin/env python3
"""Generate the deterministic bit-perfect test WAV (S32_LE, stereo).

Signal: a near-silent (~ -90 dBFS) per-sample counter with DISTINCT L/R
channels, and — unlike the original 100000-frame asset — every (L,R) pair
is UNIQUE over the whole file:

    L = i & 0xFFFF
    R = (i*40503 + (i >> 16)) & 0xFFFF

The `i >> 16` term folds the 65536-frame block index into R, so the pair
sequence never repeats (up to 2^32 frames).  This keeps USB-capture
alignment unambiguous for arbitrarily long files and makes any dropped,
duplicated, swapped or altered sample detectable at any offset, while the
amplitude stays inaudible.

Output is byte-deterministic: the same command produces the identical file
on any OS / Python version — verify with the printed SHA-256.

Canonical cross-OS asset (referenced by scripts/bitperfect-usbtap.sh):

    ./gen-bitperfect-wav.py bitperfect-test-44100-s32-stereo-30s.wav
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
w.setsampwidth(4)
w.setframerate(a.rate)
w.writeframes(buf)
w.close()

digest = hashlib.sha256(open(a.wav, "rb").read()).hexdigest()
print(f"{a.wav}: {a.frames} frames, S32_LE stereo @ {a.rate} Hz "
      f"({a.frames / a.rate:.2f} s, {44 + len(buf)} bytes)")
print(f"sha256 {digest}")
