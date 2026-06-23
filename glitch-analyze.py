#!/usr/bin/env python3
"""glitch-analyze.py — classify the events in glitch.log.

Reads the unified log written by glitch-monitor.sh / glitch-usbtap.sh and
answers the question the ear can't: are the glitches *periodic* (a buffer/clock
cycle), *random* (CPU/scheduling contention), or *bursty* (something waking up)?

Method: per stage and overall, take the inter-event intervals and look at their
coefficient of variation (CV = stddev/mean):

    CV ≈ 0        perfectly periodic     → a fixed cycle; the mean IS the period
    CV < 0.5      semi-regular
    CV ≈ 1        random / Poisson       → contention, scheduling, thermal
    CV > 1.5      bursty / clustered     → a process waking (cron, rebuffer)

It also runs a small autocorrelation over a binned event-count series to surface
a dominant period even when buried in noise, and (when drc.log is present) flags
how many glitches landed next to a DRC rate switch — i.e. the known crystal-
switch family rather than a steady-state fault.

Usage: glitch-analyze.py [glitch.log] [--since EPOCH] [--stage NAME] [--bin SECS]
"""
import sys
import os
import re
import math
import argparse
from collections import defaultdict, Counter

LINE_RE = re.compile(r'\bepoch=(\d+)\s+stage=(\S+)\s+kind=(\S+)')


def parse(path, since=None, stage=None):
    events = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = LINE_RE.search(line)
                if not m:
                    continue
                ep, st, kind = int(m.group(1)), m.group(2), m.group(3)
                if since and ep < since:
                    continue
                if stage and st != stage:
                    continue
                events.append((ep, st, kind))
    except FileNotFoundError:
        sys.exit(f"no such log: {path}")
    events.sort(key=lambda e: e[0])
    return events


def stats(xs):
    n = len(xs)
    if n == 0:
        return None
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n if n > 1 else 0.0
    sd = math.sqrt(var)
    cv = sd / mean if mean else 0.0
    return {"n": n, "mean": mean, "sd": sd, "cv": cv,
            "min": min(xs), "max": max(xs)}


def verdict(cv, n):
    if n < 4:
        return "too few events to classify"
    if cv < 0.15:
        return "PERIODIC (fixed cycle — mean interval is the period)"
    if cv < 0.5:
        return "SEMI-REGULAR (a loose cycle)"
    if cv < 1.5:
        return "RANDOM / Poisson (contention, scheduling, thermal)"
    return "BURSTY / CLUSTERED (something wakes up periodically)"


def autocorr_period(epochs, bin_s):
    """Bin events into a 0/1+ count series and find the lag (in bins) with the
    strongest autocorrelation — a candidate period.  Returns (period_s, score)."""
    if len(epochs) < 8:
        return None
    t0, t1 = epochs[0], epochs[-1]
    nbins = int((t1 - t0) // bin_s) + 1
    if nbins < 8:
        return None
    series = [0] * nbins
    for e in epochs:
        series[int((e - t0) // bin_s)] += 1
    mean = sum(series) / nbins
    dev = [s - mean for s in series]
    denom = sum(d * d for d in dev) or 1.0
    best_lag, best = 0, 0.0
    for lag in range(1, nbins // 2):
        num = sum(dev[i] * dev[i + lag] for i in range(nbins - lag))
        score = num / denom
        if score > best:
            best, best_lag = score, lag
    if best_lag == 0:
        return None
    return best_lag * bin_s, best


def drc_correlation(epochs, log_path, window=3):
    """Fraction of glitches within `window` seconds of a drc.sh run event."""
    drc_log = os.path.join(os.path.dirname(os.path.abspath(log_path)), "drc.log")
    if not os.path.isfile(drc_log):
        return None
    switch_ts = []
    ts_re = re.compile(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})')
    import calendar
    try:
        with open(drc_log, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = ts_re.match(line)
                if m:
                    switch_ts.append(calendar.timegm(
                        tuple(int(x) for x in m.groups()) + (0, 0, 0)))
    except OSError:
        return None
    if not switch_ts:
        return None
    # NOTE: drc.log times are local; glitch epochs are UTC.  We compare deltas
    # only loosely (within `window`), so we widen the window to absorb any tz
    # offset bucketing by also accepting hour-multiple-aligned matches.
    near = 0
    for e in epochs:
        if any(abs(e - s) <= window or abs(((e - s) % 3600)) <= window
               for s in switch_ts):
            near += 1
    return near, len(switch_ts)


def fmt_dur(s):
    if s < 90:
        return f"{s:.1f}s"
    if s < 5400:
        return f"{s/60:.1f}m"
    return f"{s/3600:.2f}h"


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("log", nargs="?", default=os.path.join(here, "glitch.log"))
    ap.add_argument("--since", type=int, default=None, help="only epochs >= this")
    ap.add_argument("--stage", default=None, help="restrict to one stage")
    ap.add_argument("--bin", type=float, default=1.0, help="autocorr bin (s)")
    args = ap.parse_args()

    events = parse(args.log, args.since, args.stage)
    # exclude the monitor's own start/stop bookkeeping from the statistics
    events = [e for e in events if e[1] != "monitor"]

    print(f"log: {args.log}")
    if not events:
        print("no glitch events recorded.")
        return
    span = events[-1][0] - events[0][0]
    print(f"events: {len(events)}   span: {fmt_dur(span) if span else '0s'}"
          + (f"   rate: {len(events)/span*3600:.1f}/h" if span else ""))

    by_stage = Counter(e[1] for e in events)
    by_kind = Counter(f"{e[1]}/{e[2]}" for e in events)
    print("\nby stage:  " + "  ".join(f"{k}={v}" for k, v in by_stage.most_common()))
    print("by kind:   " + "  ".join(f"{k}={v}" for k, v in by_kind.most_common()))

    # overall + per-stage interval analysis
    groups = {"OVERALL": [e[0] for e in events]}
    for st in by_stage:
        groups[st] = [e[0] for e in events if e[1] == st]

    print("\ninterval analysis (CV = stddev/mean):")
    for name, eps in groups.items():
        if len(eps) < 2:
            continue
        intervals = [b - a for a, b in zip(eps, eps[1:])]
        s = stats(intervals)
        print(f"  {name:9s} n={s['n']:<5d} mean={fmt_dur(s['mean']):>7s} "
              f"cv={s['cv']:.2f}  [{fmt_dur(s['min'])}..{fmt_dur(s['max'])}]  "
              f"→ {verdict(s['cv'], len(eps))}")
        ac = autocorr_period(eps, args.bin)
        if ac and ac[1] > 0.3:
            print(f"            ↳ autocorrelation hints a ~{fmt_dur(ac[0])} "
                  f"period (score {ac[1]:.2f})")

    corr = drc_correlation([e[0] for e in events], args.log)
    if corr:
        near, nsw = corr
        pct = 100.0 * near / len(events)
        print(f"\nDRC correlation: {near}/{len(events)} glitches ({pct:.0f}%) "
              f"near one of {nsw} drc.sh events")
        if pct > 50:
            print("  → likely tied to rate/crystal switches, not steady-state.")


if __name__ == "__main__":
    main()
