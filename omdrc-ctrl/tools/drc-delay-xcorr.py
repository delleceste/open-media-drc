#!/usr/bin/env python3
"""Measure the lag between two PCM captures by cross-correlation.

Used by measure-drc-delay.sh to find the real MPD-FIFO -> DAC delay of the
DRC chain.  Both inputs are raw interleaved PCM (no header); their format and
sample rate are given on the command line because the two taps run at
different rates (the spectrum FIFO is fixed 48 kHz, the DAC tap runs at the
BruteFIR/playback rate).

The played test signal is a train of short log sweeps in silence.  A sweep
cross-correlates sharply, so the global correlation peak gives the bulk delay
between the two streams.  We collapse each capture to mono, resample both to a
common rate, FFT-cross-correlate, and report the peak lag in seconds.

numpy only (no scipy): correlation is done with rfft/irfft.
"""

import argparse
import sys

import numpy as np

# Raw sample formats we support -> (numpy dtype, is_float).
_FMTS = {
    "s32le": ("<i4", False),
    "s16le": ("<i2", False),
    "f32le": ("<f4", True),
}


def _read_raw(path: str, fmt: str, channels: int) -> np.ndarray:
    dt, is_float = _FMTS[fmt]
    data = np.fromfile(path, dtype=dt)
    if data.size == 0:
        raise SystemExit(f"{path}: no samples read (empty capture?)")
    # Drop a trailing partial frame, then collapse to a mono float track.
    frames = data.size // channels
    data = data[: frames * channels].reshape(frames, channels).astype(np.float64)
    mono = data.mean(axis=1)
    if not is_float:
        mono /= float(np.iinfo(dt).max)
    return mono


def _resample(x: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Fourier resample x from src_rate to dst_rate (good enough for a sweep)."""
    if src_rate == dst_rate or x.size == 0:
        return x
    n_dst = int(round(x.size * dst_rate / src_rate))
    if n_dst <= 0:
        return x[:0]
    # np.fft-based resample (resample by zero-pad/truncate in frequency).
    X = np.fft.rfft(x)
    n_freq_dst = n_dst // 2 + 1
    if n_freq_dst <= X.size:
        Xr = X[:n_freq_dst]
    else:
        Xr = np.concatenate([X, np.zeros(n_freq_dst - X.size, dtype=X.dtype)])
    y = np.fft.irfft(Xr, n=n_dst)
    return y * (n_dst / x.size)


def _xcorr_lag(ref: np.ndarray, sig: np.ndarray, rate: int):
    """Lag (seconds) by which `sig` is delayed relative to `ref`, via FFT
    cross-correlation.  Positive => sig is later than ref (the expected case:
    the DAC tap lags the FIFO tap).  Returns (lag_s, peak, second_peak)."""
    ref = ref - ref.mean()
    sig = sig - sig.mean()
    n = ref.size + sig.size - 1
    nfft = 1 << (int(n - 1).bit_length())
    R = np.fft.rfft(ref, nfft)
    S = np.fft.rfft(sig, nfft)
    corr = np.fft.irfft(S * np.conj(R), nfft)  # corr[k] = sum sig[n] ref[n-k]
    # Lags 0..len(sig)-1 are positive (sig later); wrap the tail for negatives.
    corr = np.concatenate([corr[-(ref.size - 1):], corr[: sig.size]])
    lags = np.arange(-(ref.size - 1), sig.size)
    mag = np.abs(corr)
    k = int(np.argmax(mag))
    peak = mag[k]
    # Confidence = peak vs the broadband correlation floor.  A repeating sweep
    # train deliberately puts strong secondary peaks at the gap spacing, so we
    # measure against the median (the floor) rather than the 2nd-highest peak.
    floor = float(np.median(mag)) or 1e-30
    return lags[k] / float(rate), float(peak), floor


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in-file", required=True, help="FIFO-tap capture (reference)")
    ap.add_argument("--in-rate", type=int, required=True)
    ap.add_argument("--out-file", required=True, help="DAC-tap capture (delayed)")
    ap.add_argument("--out-rate", type=int, required=True)
    ap.add_argument("--channels", type=int, default=2)
    ap.add_argument("--fmt", default="s32le", choices=sorted(_FMTS))
    ap.add_argument("--subtract-ms", type=float, default=0.0,
                    help="known harness overhead to subtract (e.g. interposed "
                         "virtual_oss self-latency)")
    args = ap.parse_args()

    ref = _read_raw(args.in_file, args.fmt, args.channels)
    sig = _read_raw(args.out_file, args.fmt, args.channels)

    common = min(args.in_rate, args.out_rate)
    ref = _resample(ref, args.in_rate, common)
    sig = _resample(sig, args.out_rate, common)

    lag_s, peak, floor = _xcorr_lag(ref, sig, common)
    corrected = lag_s - args.subtract_ms / 1000.0
    conf = peak / floor if floor > 0 else float("inf")

    print(f"common rate     : {common} Hz")
    print(f"ref samples     : {ref.size}  ({ref.size/common:.3f} s)")
    print(f"sig samples     : {sig.size}  ({sig.size/common:.3f} s)")
    print(f"raw lag         : {lag_s*1000:8.1f} ms  ({lag_s:.3f} s)")
    if args.subtract_ms:
        print(f"  - overhead    : {args.subtract_ms:8.1f} ms")
        print(f"corrected delay : {corrected*1000:8.1f} ms  ({corrected:.3f} s)")
    print(f"peak/floor      : {conf:6.1f}  ({'strong' if conf > 8 else 'WEAK — check signal'})")
    if lag_s < 0:
        print("WARNING: negative lag — taps may be swapped or capture too short", file=sys.stderr)
    # Machine-readable last line.
    print(f"RESULT delay_ms={corrected*1000:.1f} raw_ms={lag_s*1000:.1f} conf={conf:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
