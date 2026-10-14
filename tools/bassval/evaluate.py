"""The detector on rendered scenes, and every cue against the truth.

For each scene the detector (tools/bass_depth.py, imported, defaults) runs
on the rendered stereo signal; the cues it reports are kept next to the
scene's ground truth, one JSON line per scene, so runs can be resumed and
re-reported.

The report asks, for each cue, the question that matters:

  distance   holding piece, main pair and hall fixed, does the cue move
             monotonically with the basses' distance, and in the direction
             the detector assumes?  Spearman's ρ per group (4 distances),
             the share of groups where it is ±1 the right way, and ρ over
             all scenes against distance and against the true direct-to-
             reverberant ratio (comparability across setups).
  spot       does the spot-mic check say "likely"/"double-arrival" for an
             unaligned spot only, and at the right delay?
  hall       does a change of hall move the cue as much as a change of
             distance?  (The ratio of the two spreads.)
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import numpy as np
from scipy.stats import spearmanr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bass_depth  # noqa: E402  (tools/bass_depth.py)

CONTEXTS = ("solo", "with-sections", "tutti", "pizzicato")
# the cues and the sign of their expected relation to distance (further -> ?)
CUES = {
    "score": +1,                       # higher = further back
    "coh_ratio": -1,                   # the track's ×coh (solo + with)
    "fund_db": +1,                     # the track's solo fundamental
    **{f"{c}.coh_ratio": -1 for c in CONTEXTS},
    **{f"{c}.fund_db": +1 for c in CONTEXTS},
}


def detector_cues(x: np.ndarray, name: str) -> dict:
    """Run the detector on a (2, n) signal at its own rate and flatten what
    it reports into {cue: value}."""
    opts = bass_depth.build_parser().parse_args([])
    opts.segments = None
    res = bass_depth.analyze(x.astype(np.float64), opts, name=name)
    d = res.get("depth") or {}
    out = {"score": d.get("score"), "coh_ratio": d.get("coh_ratio"), "fund_db": d.get("fund_db"),
           "bass_seconds": res.get("bass_seconds"), "verdict": res.get("verdict"),
           "delay_ms": res.get("delay_ms"), "version": res.get("version")}
    for c in CONTEXTS:
        ctx = (res.get("contexts") or {}).get(c, {})
        for k in ("seconds", "coh_ratio", "fund_db", "balance_db", "balance_fund_db"):
            out[f"{c}.{k}"] = ctx.get(k)
    gcc = (res.get("gcc") or {}).get("peaks") or []
    out["gcc_best"] = [{k: p.get(k) for k in ("lag_ms", "z", "rel_db")} for p in gcc[:3]]
    return out


def load(path: str) -> list[dict]:
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    return rows


def _rho(x, y):
    pts = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(pts) < 3 or len({a for a, _ in pts}) < 2 or len({b for _, b in pts}) < 2:
        return None
    return float(spearmanr([a for a, _ in pts], [b for _, b in pts]).statistic)


def report_distance(rows: list[dict]) -> list[str]:
    rows = [r for r in rows if r["scene"]["spot"] == "none"]
    groups = defaultdict(list)
    for r in rows:
        s = r["scene"]
        groups[(s["piece"], s["setup"], s["rt_mid"])].append(r)
    groups = {k: v for k, v in groups.items() if len({r["scene"]["bass_shift"] for r in v}) >= 3}
    lines = [f"Distance: {len(groups)} groups (piece × main pair × hall), "
             f"{sum(len(v) for v in groups.values())} scenes",
             f"{'cue':24} {'expect':>6} {'mean ρ':>7} {'right way':>9} "
             f"{'ρ vs dist (all)':>16} {'ρ vs DRR (all)':>15}"]
    for cue, sign in CUES.items():
        rhos = []
        for v in groups.values():
            r = _rho([x["truth"]["bass_distance_m"] for x in v], [x["cues"].get(cue) for x in v])
            if r is not None:
                rhos.append(r)
        if not rhos:
            lines.append(f"{cue:24} {'+' if sign > 0 else '−':>6} {'no data':>7}")
            continue
        right = sum(1 for r in rhos if r * sign >= 0.99)
        all_d = _rho([x["truth"]["bass_distance_m"] for x in rows], [x["cues"].get(cue) for x in rows])
        all_r = _rho([x["truth"]["bass_drr_db"] for x in rows], [x["cues"].get(cue) for x in rows])
        fmt = lambda v: f"{v:+.2f}" if v is not None else "–"     # noqa: E731
        lines.append(f"{cue:24} {'+' if sign > 0 else '−':>6} {np.mean(rhos):+7.2f} "
                     f"{right:>4}/{len(rhos):<4} {fmt(all_d):>16} {fmt(all_r):>15}")
    lines.append("expect: the sign of ρ if the cue works (further -> score up, ×coh down, "
                 "fundamental up); right way: groups with ρ = ±1 in that direction")
    return lines


def report_spot(rows: list[dict]) -> list[str]:
    rows = [r for r in rows if r["scene"]["bass_shift"] == 0.0 and r["scene"]["rt_mid"] == 1.9]
    by = defaultdict(list)
    for r in rows:
        by[r["scene"]["spot"]].append(r)
    if len(by) < 2:
        return []
    lines = ["Spot microphone: verdicts per condition"]
    for spot in ("none", "aligned", "unaligned"):
        v = by.get(spot, [])
        if not v:
            continue
        counts = defaultdict(int)
        for r in v:
            counts[r["cues"].get("verdict")] += 1
        lines.append(f"  {spot:10} " + ", ".join(f"{k}: {n}" for k, n in sorted(counts.items())))
        if spot == "unaligned":
            for r in v:
                true = r["truth"].get("spot_delta_ms")
                got = r["cues"].get("delay_ms")
                lines.append(f"    {r['scene']['key']}: true Δ {true:.1f} ms, found "
                             + (f"{got:+.1f} ms" if got is not None else "none")
                             + "  best GCC peaks " + ", ".join(
                                 f"{p['lag_ms']:+.1f} ms z {p['z']:.1f}" for p in r["cues"].get("gcc_best", [])))
    return lines


def report_hall(rows: list[dict]) -> list[str]:
    rows = [r for r in rows if r["scene"]["spot"] == "none" and r["scene"]["setup"] == "ORTF"]
    rts = sorted({r["scene"]["rt_mid"] for r in rows})
    if len(rts) < 3:
        return []
    lines = ["Hall vs distance: spread of each cue across halls (fixed distance) "
             "over its spread across distances (fixed hall)"]
    for cue in CUES:
        by_hall, by_dist = [], []
        for piece in {r["scene"]["piece"] for r in rows}:
            pr = [r for r in rows if r["scene"]["piece"] == piece]
            for shift in {r["scene"]["bass_shift"] for r in pr}:
                v = [r["cues"].get(cue) for r in pr if r["scene"]["bass_shift"] == shift]
                v = [a for a in v if a is not None]
                if len(v) >= 2:
                    by_hall.append(np.ptp(v))
            for rt in rts:
                v = [r["cues"].get(cue) for r in pr if r["scene"]["rt_mid"] == rt]
                v = [a for a in v if a is not None]
                if len(v) >= 2:
                    by_dist.append(np.ptp(v))
        if by_hall and by_dist and np.mean(by_dist) > 0:
            lines.append(f"  {cue:24} hall/distance spread {np.mean(by_hall) / np.mean(by_dist):5.2f}")
    return lines


def report(rows: list[dict]) -> str:
    out = []
    for part in (report_distance(rows), report_spot(rows), report_hall(rows)):
        if part:
            out += part + [""]
    return "\n".join(out)
