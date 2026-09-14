#!/usr/bin/env python3
"""Measure the settled multitone made by gen-high-frequency-wav.py.

One WAV reports all tones relative to 1 kHz. Two WAVs compare their normalized
responses, removing constant recording-gain differences. The default settling
time covers a 524288-tap BruteFIR filter at the capture's sample rate.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import wave

import numpy as np

DEFAULT_FREQUENCIES = (1000, 4000, 8000, 12000, 16000, 19000)


def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as source:
        channels, width, rate = (source.getnchannels(), source.getsampwidth(),
                                 source.getframerate())
        frames = source.readframes(source.getnframes())
    if channels not in (1, 2) or width not in (2, 3, 4):
        raise ValueError(f"{path}: need mono/stereo 16-, 24- or 32-bit PCM WAV")
    if width == 2:
        values, scale = np.frombuffer(frames, dtype="<i2").astype(np.float64), 2.0**15
    elif width == 4:
        values, scale = np.frombuffer(frames, dtype="<i4").astype(np.float64), 2.0**31
    else:
        packed = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        unsigned = (packed[:, 0].astype(np.int32)
                    | packed[:, 1].astype(np.int32) << 8
                    | packed[:, 2].astype(np.int32) << 16)
        values = ((unsigned ^ 0x800000) - 0x800000).astype(np.float64)
        scale = 2.0**23
    return values.reshape(-1, channels) / scale, rate


def read_capture(target: str) -> tuple[np.ndarray, int]:
    """Read a PCM WAV or a bitperfect_runner PREFIX(.wire.raw) artifact."""
    path = Path(target)
    if path.suffix.lower() == ".wav":
        return read_wav(str(path))
    if path.name.endswith(".wire.raw"):
        prefix = Path(str(path)[:-len(".wire.raw")])
        raw = path
    else:
        prefix = path
        raw = Path(str(path) + ".wire.raw")
    sidecar = Path(str(prefix) + ".json")
    if not raw.is_file() or not sidecar.is_file():
        raise ValueError(f"{target}: need a WAV or PREFIX.wire.raw + PREFIX.json")
    info = json.loads(sidecar.read_text())
    rate = info.get("wire_rate") or (info.get("chain") or {}).get("brutefir_rate")
    channels = int(info.get("channels", 2))
    if not rate or channels not in (1, 2):
        raise ValueError(f"{target}: sidecar lacks a usable wire rate/channels")
    values = np.fromfile(raw, dtype="<i4")
    usable = len(values) // channels * channels
    return values[:usable].reshape(-1, channels) / 2.0**31, int(rate)

def first_signal(samples: np.ndarray, rate: int) -> int:
    """Locate signal onset by 10 ms block RMS, tolerating analogue noise."""
    mono = np.max(np.abs(samples), axis=1)
    block = max(1, round(rate * 0.01))
    count = len(mono) // block
    if count < 10:
        raise ValueError("capture is too short")
    rms = np.sqrt(np.mean(mono[:count * block].reshape(count, block) ** 2, axis=1))
    peak = float(np.max(rms))
    if peak <= 0:
        raise ValueError("capture is silent")
    active = np.flatnonzero(rms >= peak * 10 ** (-30 / 20))
    if not len(active):
        raise ValueError("could not locate the multitone")
    return int(active[0] * block)


def actual_frequency(samples: np.ndarray, expected: float, rate: int) -> float:
    """Estimate ADC clock offset from the 1 kHz tone with a zero-padded FFT."""
    size = 1 << (max(2, len(samples) * 4) - 1).bit_length()
    spectrum = np.abs(np.fft.rfft((samples - np.mean(samples))
                                  * np.hanning(len(samples)), n=size))
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    candidates = np.flatnonzero(np.abs(frequencies - expected) <= 5)
    peak = int(candidates[np.argmax(spectrum[candidates])])
    if peak <= 0 or peak >= len(spectrum) - 1:
        return expected
    y = np.log(np.maximum(spectrum[peak - 1:peak + 2], 1e-300))
    denominator = y[0] - 2 * y[1] + y[2]
    offset = 0.5 * (y[0] - y[2]) / denominator if denominator else 0.0
    return float((peak + offset) * rate / size)


def sine_level(samples: np.ndarray, frequency: float, rate: int) -> float:
    n = np.arange(len(samples), dtype=np.float64)
    omega = 2 * np.pi * frequency / rate
    cosine, sine = np.cos(omega * n), np.sin(omega * n)
    # Solve the three-parameter least-squares fit from its tiny normal matrix.
    # Building an 88k x 3 matrix and sending it through a general SVD made each
    # six-tone analysis unnecessarily slow on the appliance.
    design_gram = np.array(((cosine @ cosine, cosine @ sine, cosine.sum()),
                            (cosine @ sine, sine @ sine, sine.sum()),
                            (cosine.sum(), sine.sum(), len(samples))))
    target = np.array((cosine @ samples, sine @ samples, samples.sum()))
    coefficients = np.linalg.solve(design_gram, target)
    return float(math.hypot(coefficients[0], coefficients[1]))


def measure(path: str, frequencies: tuple[float, ...], filter_taps: int,
            analysis_seconds: float) -> dict:
    samples, rate = read_capture(path)
    if any(f >= rate / 2 for f in frequencies):
        raise ValueError(f"{path}: a requested tone is at or above Nyquist")
    start = first_signal(samples, rate)
    # One filter length plus 250 ms keeps convolution start-up out of the fit.
    left = start + filter_taps + round(0.25 * rate)
    right = left + round(analysis_seconds * rate)
    if right > len(samples):
        needed = (right - start) / rate
        raise ValueError(f"{path}: need {needed:.2f} s of multitone after onset "
                         f"for {filter_taps} filter taps")
    segment = samples[left:right]
    measured_reference = actual_frequency(segment[:, 0], frequencies[0], rate)
    clock_ratio = measured_reference / frequencies[0]
    levels = np.asarray([
        [sine_level(segment[:, channel], frequency * clock_ratio, rate)
         for channel in range(samples.shape[1])]
        for frequency in frequencies
    ])
    if np.any(levels[0] <= 0):
        raise ValueError(f"{path}: 1 kHz reference tone is missing")
    relative = 20 * np.log10(np.maximum(levels, 1e-30) / levels[0])
    return {"path": path, "rate": rate, "channels": samples.shape[1],
            "filter_taps": filter_taps,
            "settling_seconds": round(filter_taps / rate + 0.25, 6),
            "analysis_seconds": analysis_seconds,
            "estimated_clock_ppm": round((clock_ratio - 1) * 1e6, 3),
            "tones": [{"hz": frequency,
                        "relative_db": [round(float(v), 4) for v in relative[i]]}
                       for i, frequency in enumerate(frequencies)]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("captures", nargs="+", metavar="WAV")
    ap.add_argument("--frequencies", type=float, nargs="+", default=DEFAULT_FREQUENCIES)
    ap.add_argument("--filter-taps", type=int, default=524288)
    ap.add_argument("--analysis-seconds", type=float, default=2.0)
    ap.add_argument("--max-delta-db", type=float, default=0.25)
    ap.add_argument("--json")
    a = ap.parse_args()
    if len(a.captures) > 2:
        ap.error("provide one capture to inspect or two captures to compare")
    if a.filter_taps < 0 or a.analysis_seconds <= 0:
        ap.error("filter-taps must be non-negative and analysis-seconds positive")
    try:
        results = [measure(path, tuple(a.frequencies), a.filter_taps,
                           a.analysis_seconds) for path in a.captures]
    except (OSError, EOFError, ValueError, wave.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    report = {"reference_hz": a.frequencies[0], "captures": results}
    for result in results:
        print(f"{result['path']} ({result['rate']} Hz, settled "
              f"{result['settling_seconds']:.3f} s, clock "
              f"{result['estimated_clock_ppm']:+.1f} ppm; relative to 1 kHz)")
        labels = "LR" if result["channels"] == 2 else "M"
        for tone in result["tones"]:
            values = "  ".join(f"{labels[i]} {value:+.3f} dB"
                               for i, value in enumerate(tone["relative_db"]))
            print(f"  {tone['hz']:7g} Hz  {values}")

    status = 0
    if len(results) == 2:
        channels = min(results[0]["channels"], results[1]["channels"])
        comparison, worst = [], 0.0
        print(f"delta: {results[1]['path']} minus {results[0]['path']}")
        for first, second in zip(results[0]["tones"], results[1]["tones"]):
            delta = [second["relative_db"][i] - first["relative_db"][i]
                     for i in range(channels)]
            worst = max(worst, *(abs(value) for value in delta))
            comparison.append({"hz": first["hz"],
                               "delta_db": [round(value, 4) for value in delta]})
            labels = "LR" if channels == 2 else "M"
            print(f"  {first['hz']:7g} Hz  " + "  ".join(
                f"{labels[i]} {value:+.3f} dB" for i, value in enumerate(delta)))
        passed = worst <= a.max_delta_db
        report["comparison"] = {"second_minus_first": comparison,
                                "worst_abs_delta_db": round(worst, 4),
                                "limit_db": a.max_delta_db, "passed": passed}
        print(f"{'PASS' if passed else 'FAIL'}: worst normalized response delta "
              f"{worst:.3f} dB (limit {a.max_delta_db:.3f} dB)")
        status = 0 if passed else 1
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2) + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
