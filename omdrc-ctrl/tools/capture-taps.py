#!/usr/bin/env python3
"""Capture two live PCM taps on one clock and report the delay between them.

This is the measurement core for the DRC delay: it opens the analyzer's input
(MPD spectrum FIFO) and the post-BruteFIR DAC tap (a virtual_oss loopback)
*in one process*, time-stamps the first sample read from each against a single
monotonic clock, then aligns both streams on that common time origin before
cross-correlating.  That shared origin is the whole point: the FIFO only starts
flowing when MPD begins playback, while the DAC loopback streams continuously,
so the two captures do NOT share t=0 — we must align them by wall-clock, not by
file offset.

Flow (driven by measure-drc-delay.sh):
  1. open both taps non-blocking, start the read loop
  2. touch --ready-file  ->  the shell sees it and issues `mpc play`
  3. read both taps for --dur seconds, recording each tap's first-sample time
  4. resample to a common rate, front-pad each by (t_first - t0), cross-correlate
  5. print the lag = delay the analyzer must apply

numpy only.
"""

import argparse
import os
import select
import threading
import time

import numpy as np

_FMTS = {"s32le": ("<i4", False), "s16le": ("<i2", False), "f32le": ("<f4", True)}


def _to_mono(chunks, fmt, channels):
    dt, is_float = _FMTS[fmt]
    x = np.frombuffer(b"".join(chunks), dtype=dt)
    n = x.size // channels
    if n == 0:
        return np.zeros(0)
    x = x[: n * channels].reshape(n, channels).astype(np.float64).mean(axis=1)
    if not is_float:
        x /= float(np.iinfo(dt).max)
    return x


def _resample(x, src, dst):
    if src == dst or x.size == 0:
        return x
    n = int(round(x.size * dst / src))
    if n <= 0:
        return x[:0]
    X = np.fft.rfft(x)
    nf = n // 2 + 1
    Xr = X[:nf] if nf <= X.size else np.concatenate([X, np.zeros(nf - X.size, X.dtype)])
    return np.fft.irfft(Xr, n=n) * (n / x.size)


def _xcorr_lag(ref, sig, rate):
    ref = ref - ref.mean()
    sig = sig - sig.mean()
    nfft = 1 << int(ref.size + sig.size - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(sig, nfft) * np.conj(np.fft.rfft(ref, nfft)), nfft)
    corr = np.concatenate([corr[-(ref.size - 1):], corr[: sig.size]])
    lags = np.arange(-(ref.size - 1), sig.size)
    mag = np.abs(corr)
    k = int(np.argmax(mag))
    floor = float(np.median(mag)) or 1e-30
    return lags[k] / float(rate), float(mag[k] / floor)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fifo", required=True)
    ap.add_argument("--fifo-rate", type=int, required=True)
    ap.add_argument("--tap", required=True)
    ap.add_argument("--tap-rate", type=int, required=True)
    ap.add_argument("--channels", type=int, default=2)
    ap.add_argument("--fmt", default="s32le", choices=sorted(_FMTS))
    ap.add_argument("--dur", type=float, default=8.0)
    ap.add_argument("--ready-file", default=None)
    ap.add_argument("--subtract-ms", type=float, default=0.0)
    a = ap.parse_args()

    fd_f = os.open(a.fifo, os.O_RDONLY | os.O_NONBLOCK)
    fd_t = os.open(a.tap, os.O_RDONLY | os.O_NONBLOCK)
    bufs = {fd_f: [], fd_t: []}
    t_first = {fd_f: None, fd_t: None}
    stop = threading.Event()

    def reader(fd):
        # One thread per tap: the high-rate DAC tap (~1.5 MB/s @192k) must not
        # starve the FIFO reads — if the FIFO pipe fills, MPD's fifo write fails
        # and MPD closes the output (EOF), truncating the reference capture.
        while not stop.is_set():
            r, _, _ = select.select([fd], [], [], 0.2)
            if not r:
                continue
            try:
                d = os.read(fd, 1 << 18)   # 256 KiB
            except (BlockingIOError, OSError):
                continue
            if not d:
                continue
            if t_first[fd] is None:
                t_first[fd] = time.monotonic()
            bufs[fd].append(d)

    threads = [threading.Thread(target=reader, args=(fd,), daemon=True) for fd in (fd_f, fd_t)]
    for th in threads:
        th.start()

    if a.ready_file:
        open(a.ready_file, "w").close()   # tell the shell to start playback

    time.sleep(a.dur)
    stop.set()
    for th in threads:
        th.join(timeout=2)
    os.close(fd_f)
    os.close(fd_t)

    if t_first[fd_f] is None:
        raise SystemExit("FIFO tap: no data — was MPD playing with 'OMDRC Spectrum' enabled?")
    if t_first[fd_t] is None:
        raise SystemExit("DAC tap: no data — is virtual_oss #2 / the loopback alive?")

    fifo = _resample(_to_mono(bufs[fd_f], a.fmt, a.channels), a.fifo_rate, min(a.fifo_rate, a.tap_rate))
    tap = _resample(_to_mono(bufs[fd_t], a.fmt, a.channels), a.tap_rate, min(a.fifo_rate, a.tap_rate))
    common = min(a.fifo_rate, a.tap_rate)

    # Align both streams to a common monotonic origin t0 (THIS removes the
    # FIFO-vs-DAC start-time asymmetry that a naive file-offset compare gets wrong).
    t0 = min(t_first[fd_f], t_first[fd_t])
    fifo = np.concatenate([np.zeros(int(round((t_first[fd_f] - t0) * common))), fifo])
    tap = np.concatenate([np.zeros(int(round((t_first[fd_t] - t0) * common))), tap])

    lag, conf = _xcorr_lag(fifo, tap, common)
    corrected = lag - a.subtract_ms / 1000.0

    print(f"common rate     : {common} Hz")
    print(f"fifo capture    : {fifo.size/common:6.3f} s   first-sample +{(t_first[fd_f]-t0)*1000:.0f} ms")
    print(f"dac  capture    : {tap.size/common:6.3f} s   first-sample +{(t_first[fd_t]-t0)*1000:.0f} ms")
    print(f"raw lag (delay) : {lag*1000:8.1f} ms  ({lag:.3f} s)")
    if a.subtract_ms:
        print(f"  - tap overhead: {a.subtract_ms:8.1f} ms")
        print(f"corrected delay : {corrected*1000:8.1f} ms  ({corrected:.3f} s)")
    print(f"peak/floor      : {conf:8.1f}  ({'strong' if conf > 8 else 'WEAK — check signal/levels'})")
    if lag < 0:
        print("WARNING: negative lag — taps may be swapped or capture too short")
    print(f"RESULT delay_ms={corrected*1000:.1f} raw_ms={lag*1000:.1f} conf={conf:.2f}")


if __name__ == "__main__":
    main()
