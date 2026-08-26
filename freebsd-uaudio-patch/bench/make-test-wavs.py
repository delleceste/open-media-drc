#!/usr/bin/env python3
"""
Generate one test WAV per sample rate for the DAC lock bench.

Each rate gets its OWN tone frequency, chosen to be a non-harmonic of 50/60 Hz
mains.  That buys three things from a single analog recording of the DAC output:

  * presence      -- is the DAC routing audio at all (the silent-open bug)?
  * identity      -- which file is playing (so a mis-sequenced run is obvious)
  * clock truth   -- measured_hz / expected_hz is the ratio of the DAC's actual
                     sample clock to the one it was asked for.  A DAC left on
                     the 48 kHz crystal while fed 44.1 kHz data reproduces the
                     tone 8.84% sharp, which is unmistakable.

32-bit PCM stereo, matching the format uaudio(4) fixes at attach on this host,
so the player can hand the frames to /dev/dspN untouched.
"""

import argparse
import json
import os
import struct
import wave

import numpy as np

# Non-harmonic of 50 Hz and 60 Hz, spread far enough apart that an 8.8%
# pitch error can never be confused with a neighbouring rate's tone.
TONES = {
    44100: 997.0,
    48000: 1319.0,
    88200: 1741.0,
    96000: 2273.0,
    176400: 2971.0,
    192000: 3821.0,
    352800: 4877.0,
    384000: 6113.0,
}


def make(path, rate, tone_hz, seconds, lead_s, level_dbfs, fade_ms=5.0):
    amp = 10.0 ** (level_dbfs / 20.0)
    lead = np.zeros(int(rate * lead_s), dtype=np.float64)
    n = int(rate * seconds)
    t = np.arange(n, dtype=np.float64) / rate
    tone = amp * np.sin(2.0 * np.pi * tone_hz * t)

    # Short raised-cosine fades: a hard edge would smear across the spectrum
    # and blunt the peak the analyser looks for.
    f = max(1, int(rate * fade_ms / 1000.0))
    ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, f)))
    tone[:f] *= ramp
    tone[-f:] *= ramp[::-1]

    mono = np.concatenate([lead, tone, lead])
    stereo = np.repeat(mono[:, None], 2, axis=1)
    pcm = np.clip(np.rint(stereo * 2147483647.0), -2147483648, 2147483647).astype("<i4")

    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(4)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return len(mono)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--outdir", default=os.path.join(os.path.dirname(__file__), "wavs"))
    ap.add_argument("--rates", default=",".join(str(r) for r in sorted(TONES)),
                    help="comma-separated sample rates")
    ap.add_argument("--seconds", type=float, default=2.5, help="tone length")
    ap.add_argument("--lead", type=float, default=0.25,
                    help="silence before and after the tone (noise-floor reference)")
    ap.add_argument("--level", type=float, default=-20.0,
                    help="tone level in dBFS; keep well below 0 so the DAC's "
                         "analog output does not overload the capture input")
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    manifest = {"level_dbfs": a.level, "seconds": a.seconds, "lead": a.lead, "files": {}}
    for r in [int(x) for x in a.rates.split(",") if x.strip()]:
        if r not in TONES:
            raise SystemExit("no tone assigned for rate %d (edit TONES)" % r)
        name = "tone-%d.wav" % r
        frames = make(os.path.join(a.outdir, name), r, TONES[r], a.seconds, a.lead, a.level)
        manifest["files"][str(r)] = {"file": name, "tone_hz": TONES[r],
                                     "frames": frames, "duration_s": frames / r}
        print("%-16s %6d Hz  tone %7.1f Hz  %5.2f s  %8d frames"
              % (name, r, TONES[r], frames / r, frames))

    mp = os.path.join(a.outdir, "manifest.json")
    with open(mp, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    print("\nmanifest -> %s" % mp)


if __name__ == "__main__":
    main()
