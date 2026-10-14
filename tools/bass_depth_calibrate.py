#!/usr/bin/env python3
"""bass_depth_calibrate.py — compare the listener's taps with the detector.

The boxes keep, for every tap ("the basses sound forward / in between / back
here"), the moment heard and the track; and for every track played, the
detector's analysis with its timeline of bass passages (bass_live.py).  Both
travel through the DR log's git repository, one file per box in `bass/`.
This script reads them and reports, for one detector version:

  - how many taps, per verdict and per context, and how many fell where the
    detector found no bass at all (passages it missed, or not the basses);
  - per context, how the passage's score and its cues (×coh, fundamental)
    follow the verdict (Spearman rank correlation), and how often the
    green / yellow / red band agrees with back / in between / forward;
  - per track, the mean verdict against the track's score;
  - per context with enough taps, a least-squares fit of the verdict on the
    cues: a proposal for new anchors and weights, to be reviewed, never
    applied by itself.

Usage:
  bass_depth_calibrate.py REPO_DIR [--version 1.2] [--box arki] [--min-taps 10]

REPO_DIR is a clone of the DR log repository (git pull it first).
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

VALUE = {"forward": 0.0, "between": 5.0, "back": 10.0}
BAND_OF = {"forward": "red", "between": "yellow", "back": "green"}
CONTEXTS = ("solo", "with-sections", "tutti", "pizzicato")


def block_at(blocks, pos):
    """The passage a tap refers to (as bass_live.block_at): the longest one
    containing the position, else the latest ending < 3 s before it."""
    inside = [b for b in blocks if b["start"] <= pos <= b["end"]]
    if inside:
        return max(inside, key=lambda b: b.get("seconds", 0))
    before = [b for b in blocks if 0 <= pos - b["end"] < 3.0]
    return max(before, key=lambda b: b["end"]) if before else None


def load(repo, only_box=None):
    analyses, taps = {}, []
    for path in sorted(glob.glob(os.path.join(repo, "bass", "*.jsonl"))):
        box = os.path.splitext(os.path.basename(path))[0]
        if only_box and box != only_box:
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if "bass" in row:
                    a = row["bass"]
                    analyses[(box, a["track_id"])] = a
                elif "tap" in row:
                    taps.append(dict(row["tap"], box=box))
    return analyses, taps


def spearman(x, y):
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    from scipy.stats import spearmanr
    return float(spearmanr(x, y).statistic)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", help="clone of the DR log repository")
    ap.add_argument("--version", help="detector version to compare (default: the newest with taps)")
    ap.add_argument("--box", help="only this box's taps")
    ap.add_argument("--min-taps", type=int, default=10,
                    help="scored taps a context needs before a fit is proposed")
    opts = ap.parse_args()

    analyses, taps = load(opts.repo, opts.box)
    if not taps:
        print("No taps yet.")
        return 0
    joined = []
    for t in taps:
        a = analyses.get((t["box"], t["track_id"]))
        if a is None:
            joined.append((t, None, None))
            continue
        res = a.get("result") or {}
        joined.append((t, a, block_at(res.get("blocks", []), t["pos"])))
    versions = Counter(a["version"] for _, a, _ in joined if a)
    version = opts.version or (max(versions, key=lambda v: [int(p) for p in v.split(".")])
                               if versions else None)
    print(f"{len(taps)} taps from {len({t['box'] for t in taps})} box(es); "
          f"analyses by version: {dict(versions)}; comparing version {version}\n")

    use = [(t, a, b) for t, a, b in joined if a and a["version"] == version]
    missing = len(joined) - len(use)
    print(f"Taps on tracks with a v{version} analysis: {len(use)}"
          + (f" ({missing} on tracks not analysed by it)" if missing else ""))
    print("Verdicts:", dict(Counter(t["verdict"] for t, _, _ in use)))
    nobass = [(t, a) for t, a, b in use if b is None]
    print(f"Taps where the detector found no bass passage: {len(nobass)}")
    for t, a in nobass[:10]:
        print(f"   {t['box']}: {a['artist']} — {a['title']} at {t['pos']:.0f} s, "
              f"{t['verdict']}{': ' + t['note'] if t['note'] else ''}")

    by_ctx = defaultdict(list)
    for t, a, b in use:
        if b is not None:
            by_ctx[b["context"]].append((t, b))
    print("\nPer context (the passage under each tap):")
    print(f"   {'context':14} {'taps':>4} {'scored':>6} {'ρ score':>8} {'ρ ×coh':>7} "
          f"{'ρ fund':>7} {'band agrees':>11}")
    fits = {}
    for ctx in CONTEXTS:
        rows = by_ctx.get(ctx, [])
        scored = [(t, b) for t, b in rows if "score" in b]
        v = [VALUE[t["verdict"]] for t, _ in scored]
        rs = spearman(v, [b["score"] for _, b in scored])
        rc = spearman(v, [-b["coh_ratio"] for _, b in scored])    # lower ×coh = further back
        rf = spearman(v, [b["fund_db"] for _, b in scored])
        agree = sum(BAND_OF[t["verdict"]] == b["band"] for t, b in scored)
        fmt = lambda r: f"{r:+.2f}" if r is not None else "–"      # noqa: E731
        print(f"   {ctx:14} {len(rows):4d} {len(scored):6d} {fmt(rs):>8} {fmt(rc):>7} {fmt(rf):>7} "
              + (f"{agree:4d}/{len(scored)}" if scored else "–").rjust(11))
        if len(scored) >= opts.min_taps:
            X = np.array([[1.0, b["coh_ratio"], b["fund_db"]] for _, b in scored])
            coef, *_ = np.linalg.lstsq(X, np.array(v), rcond=None)
            pred = X @ coef
            r2 = 1 - np.sum((v - pred) ** 2) / max(np.sum((v - np.mean(v)) ** 2), 1e-9)
            fits[ctx] = (coef, r2, len(scored))
    print("   (ρ: Spearman rank correlation with the verdict, forward 0 … back 10; "
          "×coh is negated, so + means the cue points the right way)")

    print("\nPer track (mean verdict vs the track's score):")
    per_track = defaultdict(list)
    for t, a, _ in use:
        per_track[(t["box"], t["track_id"])].append(VALUE[t["verdict"]])
    for key, vals in sorted(per_track.items(), key=lambda kv: -len(kv[1])):
        a = analyses[key]
        d = (a.get("result") or {}).get("depth") or {}
        sc = f"{d['score']:.1f} {d['band']}" if d else "no score"
        print(f"   {np.mean(vals):4.1f} from {len(vals):2d} taps  vs  {sc:12}  "
              f"{a['artist'][:30]} — {a['title'][:40]}")

    if fits:
        print("\nProposed per-context fits (verdict ≈ a + b·×coh + c·fund), for review:")
        for ctx, (coef, r2, n) in fits.items():
            a0, b, c = coef
            print(f"   {ctx:14} n={n:3d}  R²={r2:.2f}  a={a0:+.2f} b={b:+.2f}/× c={c:+.3f}/dB"
                  + (f"  → score 10 at ×{(10 - a0) / b:.2f}, 0 at ×{-a0 / b:.2f} (fund 0 dB)"
                     if abs(b) > 1e-6 else ""))
    else:
        print(f"\nNo fit proposed yet: a context needs {opts.min_taps} scored taps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
