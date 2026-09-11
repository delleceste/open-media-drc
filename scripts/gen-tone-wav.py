#!/usr/bin/env python3
"""Generate a deterministic, near-silent two-tone test WAV for resampler checks.

    L = 997 Hz sine, R = 1499 Hz sine, both at --level dBFS (default -90)

Why these choices
=================
* **Tones, not the counter.** The counter in gen-bitperfect-wav.py is ideal for
  proving bytes unchanged, but once a resampler is in the path the output is
  *supposed* to differ from the input, so "did it change?" stops being the
  question.  "How cleanly did it change?" is — and a pure tone answers that
  directly: fit the sine back out of the capture and whatever remains is the
  resampler's error (aliasing, imaging, noise), in dB below the tone.
* **997 Hz and 1499 Hz** are prime, so neither is harmonically related to any
  of the standard sample rates or to each other; error products do not land on
  top of the tones they are measured against.  Different per channel so a swap
  or a channel copy is visible.
* **-90 dBFS** keeps the file inaudible through an amplifier, like the
  counter, while still leaving roughly 95 dB between the tone and the 32-bit
  floor — enough to separate a very-high-quality resampler (error at the floor)
  from a fast one (error tens of dB up).
* **32-bit source**, so the source's own quantisation (~-186 dBFS) sits far
  below anything being measured.

Determinism: on one machine the output is fully deterministic and its SHA-256
is printed.  Across operating systems it is *expected* but not guaranteed to
match: math.sin goes through the platform's libm (glibc on Linux, msun on
FreeBSD), and the two may differ in the last bit of a double, which could in
principle flip the rounding of a sample.  That does not matter for what the
file is for — resampler-residual.py fits the tone by frequency and level, it
never compares bytes.  (The counter in gen-bitperfect-wav.py is integer-only
and IS byte-identical everywhere; use it wherever bytes are compared.)
"""
import argparse
import hashlib
import math
import struct
import wave

p = argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("wav")
p.add_argument("--rate", type=int, default=44100)
p.add_argument("--seconds", type=float, default=10.0)
p.add_argument("--level", type=float, default=-90.0, help="dBFS per channel")
p.add_argument("--freq-l", type=float, default=997.0)
p.add_argument("--freq-r", type=float, default=1499.0)
a = p.parse_args()

amp = (2**31 - 1) * 10 ** (a.level / 20.0)
n = int(round(a.rate * a.seconds))
wl = 2 * math.pi * a.freq_l / a.rate
wr = 2 * math.pi * a.freq_r / a.rate
buf = bytearray()
for i in range(n):
    buf += struct.pack("<ii", int(round(amp * math.sin(wl * i))),
                       int(round(amp * math.sin(wr * i))))
with wave.open(a.wav, "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(4)
    w.setframerate(a.rate)
    w.writeframes(bytes(buf))
sha = hashlib.sha256(open(a.wav, "rb").read()).hexdigest()
print(f"{a.wav}: {n} frames, S32_LE stereo @ {a.rate} Hz, "
      f"L {a.freq_l:g} Hz / R {a.freq_r:g} Hz at {a.level:g} dBFS")
print(f"sha256 {sha}")
