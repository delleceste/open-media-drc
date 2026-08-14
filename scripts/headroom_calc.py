#!/usr/bin/env python3
"""
headroom_calc.py
----------------
Calculates the minimum attenuation (headroom) required in brutefir.conf
for each FIR filter file, to prevent clipping and maximise dynamics.

Principle
---------
brutefir convolves the input audio (max amplitude ±1.0 in float) with the
filter's impulse response h[n].  The worst-case output amplitude at any
given frequency f is:

    |output(f)| = |input(f)| × |H(f)|

where H(f) is the filter's complex frequency response.  For a full-scale
sine at the frequency of maximum gain the output would clip if |H(f)| > 1.

For a requested safety margin, the required BruteFIR attenuation is therefore:

    attenuation_dB = max(0, peak_gain_dB + safety_margin_dB)

We obtain H(f) by taking the FFT of the impulse response:
the FFT output at each bin IS H(f) evaluated at that bin's frequency,
so we just take the magnitude and find the maximum.

A practical safety margin of +1 dB is added on top.  The suggested
brutefir `attenuation:` value is then rounded up to one decimal place.
"""

import argparse
import json
from pathlib import Path
import re

import numpy as np

# ── Configuration ────────────────────────────────────────────────────────────

SAFETY_MARGIN_DB = 1.0   # extra dB added on top of the theoretical minimum

# Map each .raw file to its sample format.
# FLOAT64_LE → numpy dtype '<f8'  (64-bit little-endian float, as written by sox)
# S32_LE     → numpy dtype '<i4'  (32-bit little-endian signed int, as written by REW)
#
# Filters are grouped into L/R pairs: brutefir uses a single `attenuation:` value
# per coeff block, and both channels must share the same value to preserve balance.
# The pair's attenuation is therefore driven by whichever channel needs more headroom.
FORMAT_DTYPES = {
    'FLOAT64_LE': '<f8', 'FLOAT64_BE': '>f8',
    'FLOAT32_LE': '<f4', 'FLOAT32_BE': '>f4',
    'FLOAT_LE': '<f4', 'FLOAT_BE': '>f4',
    'S32_LE': '<i4', 'S32_BE': '>i4',
    'S16_LE': '<i2', 'S16_BE': '>i2',
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_filter(path: str, dtype: str) -> np.ndarray:
    """Load raw samples and return them as a normalised float64 array."""

    with open(path, 'rb') as fh:
        raw = fh.read()

    # Parse the bytes into the declared sample type
    samples = np.frombuffer(raw, dtype=dtype)

    if np.issubdtype(samples.dtype, np.integer):
        # Signed integer PCM: divide by 2^(bits-1), matching the [-1, 1) scale.
        samples = samples.astype(np.float64) / (2 ** (samples.dtype.itemsize * 8 - 1))

    # dtype '<f8' is already in ±1 range (by construction from sox)
    return samples.astype(np.float64)


def peak_gain_db(h: np.ndarray) -> float:
    """
    Return the peak gain of the FIR impulse response h[n] in dB.

    Steps:
      1. FFT of h[n] → H[k]  (complex spectrum, one bin per frequency)
      2. Magnitude |H[k]|    (how much the filter amplifies a sine at that freq)
      3. max over all bins   (worst-case frequency for clipping)
      4. convert to dB       (20×log10 because we're talking amplitude, not power)
    """

    # Use the next power-of-two length for FFT efficiency; zero-padding does
    # not change the result, it just interpolates between existing bins.
    n_fft = 1
    while n_fft < len(h):
        n_fft <<= 1          # shift left = multiply by 2

    # Compute the real FFT (h is real, so we only need the positive half)
    H = np.fft.rfft(h, n=n_fft)

    # Magnitude spectrum: |H[k]| for each frequency bin
    magnitude = np.abs(H)

    # Peak gain across all frequencies (linear scale)
    peak_linear = magnitude.max()

    # Convert to dB.  If peak < 1 the filter actually attenuates everywhere
    # and no correction is needed (return 0); log10 of values ≤ 0 is undefined.
    if peak_linear <= 0:
        return float('-inf')

    return 20.0 * np.log10(peak_linear)


def suggested_attenuation(peak_db: float, margin_db: float) -> float:
    """
    Return the brutefir `attenuation:` value to use.

    brutefir's attenuation is a non-negative number of dB of reduction.
    Existing attenuation in the filter contributes to the requested safety
    margin.  We round up to one decimal place to keep the conf file tidy.
    """
    raw = peak_db + margin_db
    # Ceiling to one decimal place, with no runtime gain above unity.
    return max(0.0, round(np.ceil(raw * 10) / 10, 1))


# ── Main ─────────────────────────────────────────────────────────────────────

def config_attenuation(config: Path) -> float | None:
    if not config.is_file():
        return None
    values = [float(value) for value in re.findall(
        r'attenuation:\s*([-\d.]+)', config.read_text(encoding='utf-8'))]
    if not values:
        return None
    if any(value != values[0] for value in values[1:]):
        raise ValueError(f'left/right attenuation differs in {config}')
    return values[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[1] / 'filters/120.blue'
    parser.add_argument('filter_root', nargs='?', type=Path, default=default_root,
                        help='geometry filter root (default: filters/120.blue)')
    parser.add_argument('--variant', default='',
                        help='subdirectory below each rate, for example +2dB')
    parser.add_argument('--format', default='FLOAT64_LE', choices=sorted(FORMAT_DTYPES))
    parser.add_argument('--margin', type=float, default=SAFETY_MARGIN_DB)
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args()
    root = args.filter_root.resolve()
    dtype = FORMAT_DTYPES[args.format]
    rate_dirs = sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name.isdigit()),
        key=lambda path: int(path.name),
    ) if root.is_dir() else []
    if not rate_dirs:
        parser.error(f'no numeric sample-rate directories under {root}')

    repo_root = Path(__file__).resolve().parents[1]
    geometry = root.name
    results = []
    failed = False
    for rate_dir in rate_dirs:
        pair_dir = rate_dir / args.variant if args.variant else rate_dir
        left, right = pair_dir / 'L.raw', pair_dir / 'R.raw'
        if not left.is_file() and not right.is_file():
            continue
        if not left.is_file() or not right.is_file():
            raise FileNotFoundError(f'incomplete filter pair in {pair_dir}')
        peaks = {
            'left': peak_gain_db(load_filter(str(left), dtype)),
            'right': peak_gain_db(load_filter(str(right), dtype)),
        }
        limiting = max(peaks, key=peaks.get)
        required = suggested_attenuation(peaks[limiting], args.margin)
        suffix = args.variant if args.variant else ''
        config = repo_root / 'configs' / geometry / f'brutefir-{rate_dir.name}{suffix}.conf.in'
        configured = config_attenuation(config)
        passed = configured is None or configured >= required
        failed |= not passed
        results.append({
            'rate': int(rate_dir.name), 'variant': args.variant or 'default',
            'format': args.format, 'left_peak_db': round(peaks['left'], 6),
            'right_peak_db': round(peaks['right'], 6), 'limiting_channel': limiting,
            'safety_margin_db': args.margin, 'required_attenuation_db': required,
            'configured_attenuation_db': configured, 'passed': bool(passed),
        })

    if not results:
        parser.error(f'no complete L.raw/R.raw pairs for variant {args.variant or "default"}')
    if args.as_json:
        print(json.dumps(results, indent=2))
        return 1 if failed else 0

    col_pair  = 20
    col_ch    = 48
    col_num   = 10

    header = (f"{'Pair':<{col_pair}} {'Channel file':<{col_ch}}"
              f" {'Peak gain':>{col_num}} {'Limiting ch':>{col_num}} {'Suggested':>{col_num}}")
    print(header)
    print(f"{'':─<{col_pair}} {'':─<{col_ch}} {'(dB)':>{col_num}} {'':>{col_num}} {'atten (dB)':>{col_num}}")

    for item in results:
        label = f"{item['rate']} {item['variant']}"
        limiting = item['limiting_channel']
        print(f"{label:<{col_pair}} {'L.raw':<{col_ch}} {item['left_peak_db']:>+{col_num}.3f}"
              f" {'← limits' if limiting == 'left' else '':>{col_num}} {item['required_attenuation_db']:>{col_num}.1f}")
        print(f"{'': <{col_pair}} {'R.raw':<{col_ch}} {item['right_peak_db']:>+{col_num}.3f}"
              f" {'← limits' if limiting == 'right' else '':>{col_num}}")
        configured = item['configured_attenuation_db']
        if configured is not None:
            verdict = 'PASS' if item['passed'] else 'FAIL'
            print(f"{'': <{col_pair}} {'configured':<{col_ch}} {configured:>{col_num}.1f}"
                  f" {verdict:>{col_num}}")
        print()

    print(f"Safety margin applied: {args.margin} dB")
    print("'Suggested atten (dB)' → use this for BOTH channels in brutefir.conf `attenuation:`")
    print("Note: attenuation in brutefir is a gain reduction applied before convolution output;"
          "\n      it is lossless in float64 — only clipping prevention matters, not level optimisation.")
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
