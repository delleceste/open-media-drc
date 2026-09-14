#!/usr/bin/env python3
"""Generate a 44.1 kHz / 24-bit multitone high-frequency-response WAV.

Equal-amplitude tones at 1, 4, 8, 12, 16 and 19 kHz play simultaneously for
15 seconds. That duration matters: the deployed 524288-tap BruteFIR filter is
about 11.9 seconds long at 44.1 kHz. The analyser discards the first 12 seconds
and measures only the settled response.

The ordinary bit-perfect counter has no useful acoustic spectrum. This signal
does, and matches the format of the music that motivated the test. It is
audible: turn the amplifier down before playing it through loudspeakers.
"""
import argparse
import hashlib
import math
import wave

import numpy as np

DEFAULT_FREQUENCIES = (1000, 4000, 8000, 12000, 16000, 19000)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav")
    ap.add_argument("--rate", type=int, default=44100)
    ap.add_argument("--bits", type=int, choices=(16, 24, 32), default=24)
    ap.add_argument("--seconds", type=float, default=15.0,
                    help="active duration (default: 15 s, enough for 524288 taps)")
    ap.add_argument("--level", type=float, default=-30.0,
                    help="peak dBFS of each individual tone")
    ap.add_argument("--lead-seconds", type=float, default=0.5)
    ap.add_argument("--frequencies", type=float, nargs="+", default=DEFAULT_FREQUENCIES)
    a = ap.parse_args()

    if a.rate <= 0 or a.seconds <= 0 or a.lead_seconds < 0:
        ap.error("rate/seconds must be positive and lead-seconds non-negative")
    if not -120.0 <= a.level <= -3.0:
        ap.error("--level must be between -120 and -3 dBFS")
    if any(f <= 0 or f >= a.rate / 2 for f in a.frequencies):
        ap.error("every frequency must be above 0 and below Nyquist")
    combined_peak = len(a.frequencies) * 10 ** (a.level / 20.0)
    if combined_peak >= 1:
        ap.error("the worst-case sum of the tones would clip; lower --level")

    width = a.bits // 8
    full_scale = 2**(a.bits - 1) - 1
    amplitude = full_scale * 10 ** (a.level / 20.0)
    lead_frames = round(a.lead_seconds * a.rate)
    active_frames = round(a.seconds * a.rate)
    fade_frames = min(round(0.02 * a.rate), active_frames // 4)
    # Schroeder phases lower the multitone crest factor without randomness.
    count = len(a.frequencies)
    phases = [math.pi * index * (index - 1) / count for index in range(count)]
    frame_index = np.arange(active_frames, dtype=np.float64)
    values = np.zeros(active_frames, dtype=np.float64)
    for frequency, phase in zip(a.frequencies, phases):
        values += np.sin(2 * np.pi * frequency * frame_index / a.rate + phase)
    if fade_frames:
        fade = 0.5 - 0.5 * np.cos(np.pi * np.arange(fade_frames) / fade_frames)
        values[:fade_frames] *= fade
        values[-fade_frames:] *= fade[::-1]
    samples = np.rint(amplitude * values).astype("<i4")
    stereo = np.repeat(samples[:, None], 2, axis=1).reshape(-1)
    if width == 4:
        active_bytes = stereo.tobytes()
    elif width == 2:
        active_bytes = stereo.astype("<i2").tobytes()
    else:
        # WAV 24-bit PCM is the low three bytes of each little-endian int32.
        active_bytes = (stereo.view(np.uint8).reshape(-1, 4)[:, :3]
                        .reshape(-1).tobytes())
    silence = b"\0" * (2 * width * lead_frames)
    buf = bytearray(silence + active_bytes + silence)

    with wave.open(a.wav, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(width)
        output.setframerate(a.rate)
        output.writeframes(buf)

    digest = hashlib.sha256(buf).hexdigest()
    print(f"{a.wav}: S{a.bits}_LE stereo @ {a.rate} Hz, "
          f"{len(buf) // (2 * width) / a.rate:.2f} s")
    print("simultaneous tones " + ", ".join(f"{f:g} Hz" for f in a.frequencies)
          + f" at {a.level:g} dBFS each")
    print(f"PCM sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
