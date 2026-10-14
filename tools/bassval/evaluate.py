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
import hashlib
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
    **{f"{c}.coh_comb": -1 for c in CONTEXTS},
    "pizzicato.clarity_db": -1,
    "fixed.bass.coh_comb": -1,
    "fixed.mix.coh_comb": -1,
    "fixed.bass.fund_db": +1,
    "fixed.mix.fund_db": +1,
}


def source_selection(dry: np.ndarray):
    """Frames/pitches selected on the unchanged input bass performance.

    This is a diagnostic independent of mixture detection, not annotated
    pitch ground truth. Keep sustained, exposed source frames and exclude
    128 ms at each selected boundary to tolerate propagation delays.
    """
    nfr = max(0, 1 + (len(dry) - bass_depth.NFFT) // bass_depth.HOP)
    if nfr < 5:
        return np.zeros(nfr), np.array([], dtype=int)
    opts = bass_depth.build_parser().parse_args([])
    f0, dom, peaks, energy = bass_depth.bass_salience(dry, nfr, opts.fmin, opts.fmax)
    selected = ((energy > np.percentile(energy, 99) * 10 ** (-opts.gate_db / 10))
                & (dom >= opts.dom) & (peaks >= opts.min_peaks))
    stable = np.abs(np.diff(np.log2(f0), prepend=np.log2(f0[0]))) < 1 / 24
    selected &= stable
    selected = np.convolve(selected.astype(int), np.ones(5), mode="same") == 5
    return f0, np.flatnonzero(selected)


def fixed_cues(components: dict) -> dict:
    f0, frames = source_selection(components["dry_bass"])
    identity = np.column_stack((frames, f0[frames])).astype("<f8").tobytes()
    out = {"fixed.frames": len(frames), "fixed.selection_sha256": hashlib.sha256(identity).hexdigest()}
    if len(frames) < 20:
        return out
    for label, key in (("bass", "bass_main"), ("mix", "main")):
        c = bass_depth.context_cues(*components[key], f0, frames, bass_depth.SR / bass_depth.NFFT, None)
        for cue in ("coh_comb", "fund_db", "balance_db", "balance_fund_db"):
            out[f"fixed.{label}.{cue}"] = c[cue]
    return out


def detector_cues(x: np.ndarray, name: str, components: dict | None = None) -> dict:
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
        for k in ("seconds", "coh_comb", "coh_ref", "coh_ratio", "fund_db", "balance_db",
                  "balance_fund_db", "clarity_db", "clarity_notes"):
            out[f"{c}.{k}"] = ctx.get(k)
    gcc = (res.get("gcc") or {}).get("peaks") or []
    out["gcc_best"] = [{k: p.get(k) for k in ("lag_ms", "z", "rel_db")} for p in gcc[:3]]
    if components is not None:
        out.update(fixed_cues(components))
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
        groups[(s["piece"], s["setup"], s["rt_mid"], s.get("seed", 1),
                s.get("section_mode", "copies"))].append(r)
    groups = {k: v for k, v in groups.items() if len({r["scene"]["bass_shift"] for r in v}) >= 3}
    lines = [f"Distance: {len(groups)} groups (piece × main pair × hall × seed × section mode), "
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
    groups = defaultdict(list)
    for r in rows:
        s = r["scene"]
        groups[(s["piece"], s.get("seed", 1), s.get("section_mode", "copies"))].append(r)
    if not any(len({r["scene"]["rt_mid"] for r in v}) >= 3 for v in groups.values()):
        return []
    lines = ["Hall vs distance: spread of each cue across halls (fixed distance) "
             "over its spread across the same distances in every hall (paired by seed)"]
    for cue in CUES:
        by_hall, by_dist = [], []
        for pr in groups.values():
            rts = sorted({r["scene"]["rt_mid"] for r in pr})
            if len(rts) < 3:
                continue
            values = {(r["scene"]["rt_mid"], r["scene"]["bass_shift"]): r["cues"].get(cue) for r in pr}
            shifts = sorted(set.intersection(*[
                {shift for (rt0, shift), value in values.items() if rt0 == rt and value is not None}
                for rt in rts]))
            if len(shifts) < 2:
                continue
            matrix = np.array([[values[rt, shift] for shift in shifts] for rt in rts])
            by_hall.extend(np.ptp(matrix, axis=0))
            by_dist.extend(np.ptp(matrix, axis=1))
        if by_hall and by_dist and np.mean(by_dist) > 0:
            lines.append(f"  {cue:24} hall/distance spread {np.mean(by_hall) / np.mean(by_dist):5.2f}")
    return lines


def report(rows: list[dict]) -> str:
    # Grid files share controls. Never count duplicate observations twice.
    unique = {}
    for r in rows:
        key = r["scene"]["key"]
        if key in unique and unique[key] != r:
            raise ValueError(f"Conflicting results for {key}; use separate result files")
        unique[key] = r
    rows = list(unique.values())
    signatures = {json.dumps(r.get("provenance", {}).get("algorithm"), sort_keys=True) for r in rows}
    if len(signatures) > 1:
        raise ValueError("Cannot compare results from different algorithm versions")
    by_piece = defaultdict(set)
    for r in rows:
        by_piece[r["scene"]["piece"]].add(json.dumps(r.get("provenance"), sort_keys=True))
    if any(len(v) > 1 for v in by_piece.values()):
        raise ValueError("Cannot compare different input recordings/excerpts for the same piece")
    out = ["Experimental cues; these measurements do not validate perceived depth."]
    if any(r["truth"].get("bass_independent_parts", 0) < 2
           or r["scene"].get("section_mode", "copies") == "copies" for r in rows):
        out.append("Coherence limitation: single recorded bass part and/or duplicated performances present.")
    for part in (report_distance(rows), report_spot(rows), report_hall(rows)):
        if part:
            out += part + [""]
    return "\n".join(out)
