#!/usr/bin/env python3
"""Offline self-test for the bench analyser -- no hardware touched.

Run this before trusting a bench result, and after any change to analyse().
It feeds synthetic signals covering every verdict the bench can emit,
including the two that matter most: a DAC routing silence, and a DAC left on
the wrong master crystal (which reproduces the tone 8.84% sharp).
"""
import importlib.util
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("bench", os.path.join(HERE, "dac-bench.py"))
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

R, EXP = 44100, 997.0


def sig(hz, amp, noise, secs=3.0, silent_head=0.0):
    n = int(R * secs)
    t = np.arange(n) / R
    x = amp * np.sin(2 * np.pi * hz * t) if hz else np.zeros(n)
    if silent_head:
        x[: int(R * silent_head)] = 0.0
    x = x + noise * np.random.default_rng(1).standard_normal(n)
    return np.repeat(x[:, None], 2, axis=1).astype(np.float32)


CASES = [
    ("clean tone, correct pitch", sig(997.0, 0.10, 1e-4), "OK"),
    ("quiet but present (-46 dBFS)", sig(997.0, 0.005, 1e-5), "OK"),
    ("DAC silent (noise floor only)", sig(None, 0.0, 1e-4), "SILENT"),
    ("clock stuck on 48k family", sig(997.0 * 48000 / 44100, 0.10, 1e-4), "WRONG-RATE"),
    ("clock stuck on 44.1k while 48k", sig(997.0 * 44100 / 48000, 0.10, 1e-4), "WRONG-RATE"),
    ("late start (1.2 s of silence)", sig(997.0, 0.10, 1e-4, silent_head=1.2), "OK"),
    ("hum only, no tone", sig(50.0, 0.05, 1e-4), "SILENT"),
]


def main():
    ok = True
    print("%-34s %-12s %-12s %s" % ("case", "expected", "verdict", "detail"))
    for name, x, want in CASES:
        r = bench.analyse(x, R, EXP)
        good = r["verdict"] == want
        ok &= good
        print("%-34s %-12s %-12s peak=%8.2f ratio=%.4f snr=%5.1f onset=%-7s %s"
              % (name, want, r["verdict"], r.get("peak_hz", 0), r.get("ratio", 0),
                 r.get("snr_db", 0), r.get("onset_ms"), "PASS" if good else "*** FAIL"))
    print("\nanalyser self-test:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
