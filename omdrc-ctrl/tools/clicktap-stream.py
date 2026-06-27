#!/usr/bin/env python3
"""clicktap-stream.py — STREAMING click/glitch detector for a steady-tone tap.

Reads interleaved raw PCM from a FIFO (or a file) and detects clicks, dropouts
and stuck-sample runs on the fly, in O(1) memory, so it scales to multi-minute
captures of a 192 kHz stream without ever storing the audio.

Method (see detect-clicks.py for the rationale): a cleanly resampled steady sine
obeys  x[n] = 2*cos(w)*x[n-1] - x[n-2]  (w = 2*pi*f0/fs), so the residual
    r[n] = x[n] - 2*cos(w)*x[n-1] + x[n-2]
is ~0 for the tone and SPIKES at any discontinuity.  We flag a click when |r|
exceeds  thresh_rel * tone_peak  (tone_peak learned from the first second).  We
also flag dropouts (near-zero runs) and stuck/identical-sample runs.

Designed to be driven by find-resamp-clicks.sh:
  1. open the FIFO (blocks until MPD's fifo output has a writer -> natural
     back-pressure, so we never outrun the producer and never see false gaps)
  2. touch --ready-file so the orchestrator issues `mpc play`
  3. process until --dur seconds of AUDIO have passed (or EOF), print a summary

numpy only.
"""
import argparse
import os
import select
import sys
import time

import numpy as np

_FMTS = {"s32le": ("<i4", False), "s16le": ("<i2", False), "f32le": ("<f4", True)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="FIFO or raw-PCM file to read")
    ap.add_argument("--rate", type=int, default=192000)
    ap.add_argument("--channels", type=int, default=2)
    ap.add_argument("--fmt", default="s32le", choices=sorted(_FMTS))
    ap.add_argument("--f0", type=float, default=997.0, help="tone frequency Hz")
    ap.add_argument("--thresh-rel", type=float, default=3e-4,
                    help="click if |residual| > thresh_rel * tone_peak (3e-4 ~ -70 dB)")
    ap.add_argument("--dur", type=float, default=0.0, help="seconds of audio to process (0=until EOF)")
    ap.add_argument("--max-wall", type=float, default=0.0, help="hard wall-clock cap (0=auto from --dur)")
    ap.add_argument("--ready-file", default=None)
    ap.add_argument("--label", default="")
    ap.add_argument("--events-file", default=None, help="append one line per event here")
    a = ap.parse_args()

    dt, is_float = _FMTS[fmt] if (fmt := a.fmt) else ("<i4", False)
    scale = 1.0 if is_float else float(np.iinfo(dt).max)
    bytes_per_frame = np.dtype(dt).itemsize * a.channels
    w = 2 * np.pi * a.f0 / a.rate
    coef = 2 * np.cos(w)
    nframes_target = int(a.dur * a.rate) if a.dur > 0 else None

    # Non-blocking open so we can signal readiness BEFORE MPD starts writing the
    # FIFO (otherwise: reader blocks on open -> never touches ready-file -> the
    # orchestrator never issues `mpc play` -> deadlock).  We poll with select().
    fd = os.open(a.source, os.O_RDONLY | os.O_NONBLOCK)
    if a.ready_file:
        open(a.ready_file, "w").close()
    max_wall = a.max_wall if a.max_wall > 0 else (a.dur * 1.5 + 15 if a.dur > 0 else 0.0)

    evf = open(a.events_file, "a") if a.events_file else None

    # per-channel rolling predictor state (last two samples)
    p1 = np.zeros(a.channels)
    p2 = np.zeros(a.channels)
    have_prev = False

    total_frames = 0
    peak = 0.0
    sumsq = np.zeros(a.channels)
    resid_sumsq = np.zeros(a.channels)
    clicks = []           # (t, ch, mag_rel)
    dropouts = []         # (t, len_ms)
    stuck = []            # (t, len)
    learn_frames = a.rate  # learn peak over first second
    thresh = None
    # event de-bounce: don't log within `holdoff` frames of the last click (per ch)
    holdoff = int(0.002 * a.rate)
    last_click = np.full(a.channels, -10 * holdoff, dtype=np.int64)
    # dropout / stuck run trackers per channel
    zero_run = np.zeros(a.channels, dtype=np.int64)
    same_run = np.zeros(a.channels, dtype=np.int64)
    eps = None

    t0 = time.monotonic()
    leftover = b""
    CHUNK = 1 << 18  # 256 KiB
    started = False
    try:
        while True:
            if nframes_target is not None and total_frames >= nframes_target:
                break
            if max_wall and (time.monotonic() - t0) > max_wall:
                break
            r, _, _ = select.select([fd], [], [], 0.2)
            if not r:
                continue
            try:
                data = os.read(fd, CHUNK)
            except (BlockingIOError, OSError):
                continue
            if not data:
                if started:
                    break          # writer (MPD) closed the FIFO -> playback ended
                continue           # not started yet -> keep waiting for the writer
            started = True
            buf = leftover + data
            nfr = len(buf) // bytes_per_frame
            if nfr == 0:
                leftover = buf
                continue
            use = nfr * bytes_per_frame
            leftover = buf[use:]
            block = np.frombuffer(buf[:use], dtype=dt).reshape(nfr, a.channels).astype(np.float64) / scale

            # learn the tone peak (and eps) before arming the detector
            blk_peak = np.abs(block).max()
            if blk_peak > peak:
                peak = blk_peak
            if thresh is None and (total_frames + nfr) >= learn_frames and peak > 0:
                thresh = a.thresh_rel * peak
                eps = peak * 1e-3

            sumsq += (block ** 2).sum(axis=0)

            # vectorised residual within the block (boundary uses p1/p2)
            x = block
            if have_prev:
                xm1 = np.vstack([p1, x[:-1]]) if nfr >= 1 else x
                xm2 = np.vstack([p2, p1, x[:-2]])[:nfr] if nfr >= 2 else np.vstack([p2, p1])[:nfr]
            else:
                xm1 = np.vstack([np.zeros(a.channels), x[:-1]])
                xm2 = np.vstack([np.zeros((2, a.channels)), x[:-2]])[:nfr] if nfr >= 2 else np.zeros_like(x)
            r = x - coef * xm1 + xm2
            if not have_prev:
                r[0:2] = 0.0  # ignore the very first samples (no valid history)
            resid_sumsq += (r ** 2).sum(axis=0)

            if thresh is not None:
                ar = np.abs(r)
                for ch in range(a.channels):
                    hot = np.nonzero(ar[:, ch] > thresh)[0]
                    for i in hot:
                        gi = total_frames + i
                        if gi - last_click[ch] < holdoff:
                            continue
                        last_click[ch] = gi
                        mag_rel = ar[i, ch] / max(peak, 1e-12)
                        t = gi / a.rate
                        clicks.append((t, ch, mag_rel))
                        if evf:
                            evf.write(f"click t={t:.5f} ch={ch} mag_db={20*np.log10(max(mag_rel,1e-12)):+.1f} label={a.label}\n")
                # dropout (near-zero) and stuck (identical) run accounting
                if eps is not None:
                    for ch in range(a.channels):
                        col = x[:, ch]
                        isz = np.abs(col) < eps
                        # zero runs
                        run = zero_run[ch]
                        for k in range(nfr):
                            if isz[k]:
                                run += 1
                            else:
                                if run >= max(int(0.0005 * a.rate), 16):
                                    t = (total_frames + k - run) / a.rate
                                    dropouts.append((t, run / a.rate * 1e3))
                                    if evf:
                                        evf.write(f"dropout t={t:.5f} ch={ch} len_ms={run/a.rate*1e3:.2f} label={a.label}\n")
                                run = 0
                        zero_run[ch] = run
                        # stuck runs (consecutive identical)
                        d0 = np.diff(col)
                        srun = same_run[ch]
                        prev = p1[ch] if have_prev else col[0]
                        first_same = (col[0] == prev)
                        flags = np.concatenate([[first_same], d0 == 0])
                        for k in range(nfr):
                            if flags[k]:
                                srun += 1
                            else:
                                if srun >= 8:
                                    t = (total_frames + k - srun) / a.rate
                                    stuck.append((t, int(srun)))
                                srun = 0
                        same_run[ch] = srun

            # update predictor history for the next block
            if nfr >= 2:
                p2 = x[-2].copy()
                p1 = x[-1].copy()
            elif nfr == 1:
                p2 = p1.copy()
                p1 = x[-1].copy()
            have_prev = True
            total_frames += nfr
    finally:
        os.close(fd)
        if evf:
            evf.close()

    dur_audio = total_frames / a.rate
    wall = time.monotonic() - t0
    rms = np.sqrt(sumsq / max(total_frames, 1))
    resid_rms = np.sqrt(resid_sumsq / max(total_frames, 1))
    lbl = f"[{a.label}] " if a.label else ""
    print(f"{lbl}source={a.source}")
    print(f"  audio processed : {dur_audio:.2f}s  ({total_frames} frames)  wall={wall:.1f}s  ratio={dur_audio/max(wall,1e-9):.2f}x")
    print(f"  tone peak       : {peak:.4f}   rms(ch)={np.array2string(rms, precision=4)}")
    if peak > 0:
        rfdb = 20 * np.log10(np.maximum(resid_rms, 1e-12) / peak)
        print(f"  residual floor  : {np.array2string(resid_rms, precision=2)}  ({np.array2string(rfdb, precision=1)} dB below peak)")
    print(f"  click threshold : {thresh if thresh is not None else float('nan'):.3e}  (rel={a.thresh_rel:g})")
    print(f"  CLICKS          : {len(clicks)}")
    for (t, ch, m) in clicks[:50]:
        print(f"     t={t:9.4f}s  ch{ch}  mag={20*np.log10(max(m,1e-12)):+.1f} dB")
    if len(clicks) > 50:
        print(f"     ... and {len(clicks)-50} more (see events file)")
    if len(clicks) >= 2:
        ts = np.array([c[0] for c in clicks])
        d = np.diff(ts)
        kind = "PERIODIC -> buffer boundary?" if d.size and d.std() < 0.2 * d.mean() else "irregular -> sporadic"
        print(f"  inter-click gap : mean={d.mean():.3f}s min={d.min():.3f}s max={d.max():.3f}s  ({kind})")
    print(f"  DROPOUTS        : {len(dropouts)}")
    for (t, ms) in dropouts[:50]:
        print(f"     t={t:9.4f}s  len={ms:.2f}ms")
    print(f"  STUCK runs      : {len(stuck)}")
    for (t, ln) in stuck[:20]:
        print(f"     t={t:9.4f}s  len={ln}smp")
    print(f"RESULT label={a.label} clicks={len(clicks)} dropouts={len(dropouts)} stuck={len(stuck)} audio_s={dur_audio:.1f}")
    return 0 if (len(clicks) == 0 and len(dropouts) == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
