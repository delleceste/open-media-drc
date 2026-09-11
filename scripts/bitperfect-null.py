#!/usr/bin/env python3
"""Null two DRC captures against each other — the cross-OS test.

Why this exists
===============
`bitperfect_runner.py --route drc` can prove a chain transparent only while the
convolver is a pass-through, because the source file is its reference. Nobody
listens to a pass-through. The question that matters is whether the chain the
appliance actually runs — a REAL room-correction filter — puts the same samples
on the USB wire under FreeBSD and under Linux.

For that, the reference stops being the source and becomes *the other machine*:

    FreeBSD: run the material through the chain, tap the wire   -> capture A
    Linux:   run the SAME material through the SAME filter      -> capture B
    null A against B

BruteFIR is deterministic. Given the same input samples, the same coefficients
and the same arithmetic, both machines must emit the same output. A null that
comes out at the noise floor says the difference being heard is not in the bytes
and cannot be — every candidate left is downstream of the wire (clocking, the
DAC's own behaviour) or not in the equipment at all (level, expectation). A null
that does *not* come out flat localises a real defect, and this prints where the
samples first diverge so it can be chased.

What is compared
================
The raw wire streams, not the aligned payloads: with a real filter there is no
"aligned payload", since the wire never equals the source. The two captures do
not start at the same sample — priming differs, and so does the convolver's
latency when the partitioning differs — so alignment is by cross-correlation,
then the overlap is subtracted sample by sample.

Reading the result
==================
`max_diff_lsb` is the largest absolute difference in S32 least-significant bits.
`null_depth_db` is the RMS of the difference relative to digital full scale.

    identical             the chains are bit-identical. Nothing to explain.
    within float rounding a handful of +/-1 LSB. Expected when the two runs used
                          different `filter_length` partitionings: the FFT sizes
                          differ, so the last bit of a 64-bit float convolution
                          can land either side. Inaudible by a wide margin --
                          1 LSB of S32 is about -192 dBFS.
    DIFFERENT             a real divergence. Read `first_diff` and the provenance
                          report: the usual cause is that the two runs were not
                          actually taken through the same thing.

Provenance
==========
A null is only as good as the sameness of the two runs, so the JSON each run
writes is compared first and any disagreement is reported before the numbers.
Mismatched filter coefficients, rate, attenuation or input material make the
comparison meaningless, and this says so rather than printing a number that
would be read as an operating-system difference.

Usage
=====
    ./scripts/bitperfect-null.py A B [--rate N] [--channels N] [--json OUT]

A and B are run prefixes (`<prefix>.wire.raw` plus `<prefix>.json`), or the raw
files themselves, in which case --rate and --channels are required.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

S32_FULL_SCALE = 2.0 ** 31


class CannotJudge(SystemExit):
    """Exit 2, the suite's code for "no verdict was possible".

    Distinct from a DIFFERENT verdict (exit 1), which is a real finding. Not
    being able to align two captures is not evidence that the chains differ —
    it usually means they are not captures of the same thing."""

    def __init__(self, message: str):
        print(f"cannot judge: {message}", file=sys.stderr)
        super().__init__(2)

# Provenance keys that must agree for a null to mean anything, and the reason
# each one matters if it does not.
MUST_MATCH = {
    "rate": "a different sample rate is a different filter and a different clock",
    "variant": "a different filter variant is a different correction curve",
    "geometry": "a different geometry is a different filter set entirely",
    "float_bits": "different convolver precision changes the arithmetic",
    "input_sample": "a different input width changes what reached the convolver",
    "output_sample": "a different output width changes what reached the wire",
    "dither": "dither adds noise deliberately; one side dithered will never null",
}
# Differences that are expected and explained rather than disqualifying.
EXPLAINED = {
    "filter_length": "different partitioning: mathematically the same "
                     "convolution, different FFT sizes, so the last bit may "
                     "differ and the latency certainly does",
    "os": "the whole point of the comparison",
    "loopback": "virtual_oss against snd-aloop — the element under test",
    "loopback_args": "per-OS loopback configuration",
    "conf": "the config lives at a different path on each machine",
    "brutefir_version": "different BruteFIR builds; note it, do not assume it "
                        "is harmless",
}


def load_capture(target: str, rate: int | None, channels: int | None) -> dict:
    """Resolve a run prefix or a raw file into samples plus provenance."""
    path = Path(target)
    report: dict = {}
    if path.suffix == ".raw" or path.is_file() and path.suffix not in ("", ".json"):
        raw = path
        sidecar = Path(str(path).replace(".wire.raw", "") + ".json")
    else:
        raw = Path(f"{target}.wire.raw")
        sidecar = Path(f"{target}.json")
    if not raw.is_file():
        raise CannotJudge(f"no wire capture at {raw}")
    if sidecar.is_file():
        try:
            report = json.loads(sidecar.read_text())
        except json.JSONDecodeError:
            report = {}

    ch = channels or report.get("channels") or 2
    # The wire's rate, not the material's: they differ when a resampler was in
    # the path.  Older captures carry no wire_rate; on the DRC route the chain's
    # BruteFIR rate is the wire rate.
    sr = (rate or report.get("wire_rate")
          or (report.get("chain") or {}).get("brutefir_rate")
          or report.get("rate"))
    if not sr:
        raise CannotJudge(f"{target}: no rate in {sidecar.name}; pass --rate")

    data = np.fromfile(raw, dtype="<i4")
    usable = (data.size // ch) * ch
    if usable != data.size:
        # A capture can end mid-frame: usbdump is stopped asynchronously.
        data = data[:usable]
    return {"name": target, "raw": str(raw), "samples": data.reshape(-1, ch),
            "rate": int(sr), "channels": int(ch), "report": report,
            "provenance": (report.get("chain") or {}).get("provenance") or {}}


def _loudest_window(signal: np.ndarray, length: int, block: int = 4096) -> int:
    """Where to take the correlation window from: the loudest stretch.

    NOT a fixed fraction of the capture.  On the DRC route BruteFIR streams to
    the DAC continuously, so it feeds silence whenever MPD is not playing and
    the tap records an arbitrary amount of it either side of the material.
    Measured here: one capture held its signal from 9.50 s to 22.25 s and the
    next, of the same material, from 3.75 s to 16.50 s.  A window taken a
    quarter of the way in landed in pure silence for the first and in signal
    for the second, correlated at r=0.61, and reported two identical chains as
    DIFFERENT at -66.6 dBFS.

    Picking the window by energy makes the alignment independent of how much
    silence each capture happened to catch."""
    usable = signal.size - length
    if usable <= 0:
        return 0
    trimmed = signal[:(signal.size // block) * block]
    if trimmed.size == 0:
        return 0
    envelope = np.abs(trimmed.reshape(-1, block)).max(axis=1)
    # Centre the window on the loudest block, then clamp it inside the signal.
    centre = int(np.argmax(envelope)) * block + block // 2
    return int(np.clip(centre - length // 2, 0, usable))


def find_lag(a: np.ndarray, b: np.ndarray,
             window: int = 1 << 20) -> tuple[int, float]:
    """Samples of b that correspond to the start of a, plus a confidence.

    Correlates one window of `a` over the whole of `b`.  The window is taken a
    quarter of the way in — past priming and lead-in silence — and is capped at
    half of EITHER capture: a window longer than the reference has no valid
    full-overlap lag and the correlation degenerates into noise, which is how
    an earlier version of this returned a lag of 516408 for two 140k-frame
    arrays.

    The confidence returned is the Pearson correlation of the two streams once
    aligned.  It is what separates "these are the same audio, and here is where
    they line up" from "these are unrelated and argmax found a coincidence" —
    without it, two captures of different material report a large null depth,
    which reads as an operating-system difference and is nothing of the kind.
    A level or filter difference leaves the correlation near 1, so this gate
    never suppresses a real finding."""
    if a.size == 0 or b.size == 0:
        raise CannotJudge("one of the captures is empty")
    length = min(window, a.size // 2, b.size // 2)
    if length < 1024:
        raise CannotJudge(f"captures are too short to align "
                          f"({a.size} and {b.size} frames)")
    start = _loudest_window(a, length)
    chunk = a[start:start + length].astype(np.float64)
    if not np.any(chunk):
        raise CannotJudge("the correlation window is pure silence — "
                          "the capture may be misaligned or the pad too long")

    ref = b.astype(np.float64)
    size = 1 << int(np.ceil(np.log2(ref.size + chunk.size)))
    corr = np.fft.irfft(np.fft.rfft(ref, size) *
                        np.conj(np.fft.rfft(chunk, size)), size)
    # Only lags that place the window wholly inside the reference are valid.
    corr = corr[:max(1, ref.size - chunk.size + 1)]
    lag = int(np.argmax(corr)) - start

    aligned = ref[lag + start:lag + start + length]
    if aligned.size != chunk.size:
        raise CannotJudge(f"alignment landed outside the reference (lag {lag})")
    denominator = np.linalg.norm(chunk) * np.linalg.norm(aligned)
    confidence = float(np.dot(chunk, aligned) / denominator) if denominator else 0.0
    return lag, confidence


def null(a: dict, b: dict) -> dict:
    """Align, subtract, and describe what is left."""
    if a["channels"] != b["channels"]:
        raise CannotJudge(f"channel counts differ: {a['channels']} vs {b['channels']}")
    if a["rate"] != b["rate"]:
        raise CannotJudge(f"sample rates differ: {a['rate']} vs {b['rate']}")

    lag, confidence = find_lag(a["samples"][:, 0], b["samples"][:, 0])
    if confidence < 0.5:
        raise CannotJudge(
            f"the two captures do not correlate (r={confidence:.3f} at the "
            f"best lag) — they are almost certainly not recordings of the "
            f"same material, rather than recordings of two chains that "
            f"differ. Check that both runs played the same input.")
    left = a["samples"][max(0, -lag):]
    right = b["samples"][max(0, lag):]
    length = min(left.shape[0], right.shape[0])
    if length < a["rate"]:
        raise CannotJudge(
            f"only {length} frames overlap after alignment (lag {lag}) — "
            "too little to judge. Cross-correlation could not find these two "
            "streams in each other, which normally means they are not "
            "captures of the same material rather than that the chains "
            "differ.")
    left, right = left[:length], right[:length]

    diff = left.astype(np.int64) - right.astype(np.int64)
    differing = int(np.count_nonzero(diff))
    max_lsb = int(np.abs(diff).max())
    rms = float(np.sqrt(np.mean((diff.astype(np.float64) / S32_FULL_SCALE) ** 2)))
    depth = 20 * np.log10(rms) if rms > 0 else float("-inf")

    first = None
    if differing:
        frame = int(np.argmax(np.any(diff != 0, axis=1)))
        first = {"frame": frame,
                 "seconds": round(frame / a["rate"], 6),
                 "a": [int(v) for v in left[frame]],
                 "b": [int(v) for v in right[frame]],
                 "delta": [int(v) for v in diff[frame]]}

    if differing == 0:
        verdict, reading = "IDENTICAL", "the two chains produced the same bytes"
    elif max_lsb <= 2:
        verdict = "EQUIVALENT"
        reading = ("differences are at the last bit or two of S32 — consistent "
                   "with 64-bit float rounding, roughly -190 dBFS, and about "
                   "30 dB below the DAC's own noise floor")
    else:
        verdict = "DIFFERENT"
        reading = "the chains diverge by more than float rounding explains"

    return {"verdict": verdict, "reading": reading, "lag_frames": lag,
            "alignment_confidence": round(confidence, 6),
            "compared_frames": length,
            "compared_seconds": round(length / a["rate"], 3),
            "differing_samples": differing,
            "differing_fraction": differing / (length * a["channels"]),
            "max_diff_lsb": max_lsb, "null_depth_db": depth,
            "first_diff": first}


def compare_provenance(a: dict, b: dict) -> dict:
    """What the two runs disagree about, split into fatal and expected."""
    pa, pb = a["provenance"], b["provenance"]
    blocking, noted = [], []
    if not pa or not pb:
        noted.append("one or both runs carry no provenance (older artifacts, "
                     "or not taken with --reference capture) — the numbers "
                     "below assume the runs were comparable")
    # The coefficients themselves: same file contents, whatever the paths.
    # Establish this FIRST, because it is the direct evidence about the filter
    # and it decides how to read the variant label below.
    ha = [c.get("sha256") for c in pa.get("coeffs", []) if c.get("sha256")]
    hb = [c.get("sha256") for c in pb.get("coeffs", []) if c.get("sha256")]
    coeffs_identical = bool(ha) and bool(hb) and ha == hb

    for key, why in MUST_MATCH.items():
        va, vb = pa.get(key), pb.get(key)
        if va is None or vb is None or va == vb:
            continue
        # `variant` is only ever a proxy for "a different correction curve".
        # When both runs recorded coefficient hashes and those hashes agree,
        # the curve is provably the same and the label is just the directory
        # the identical taps were read from — which is exactly the shape of a
        # deliberate cross-OS run, where one machine's coefficients are staged
        # under a second name rather than overwriting the deployed set.
        if key == "variant" and coeffs_identical:
            noted.append(f"variant: {va!r} vs {vb!r} — different label, but "
                         "the coefficient sha256s are identical, so the two "
                         "runs convolved the same filter")
            continue
        blocking.append(f"{key}: {va!r} vs {vb!r} — {why}")
    for key, why in EXPLAINED.items():
        va, vb = pa.get(key), pb.get(key)
        if va is not None and vb is not None and va != vb:
            noted.append(f"{key}: {va!r} vs {vb!r} — {why}")

    if ha and hb and ha != hb:
        blocking.append(f"filter coefficients differ: {ha} vs {hb} — the two "
                        "machines convolved different filters")
    aa = [c.get("attenuation") for c in pa.get("coeffs", [])]
    ab = [c.get("attenuation") for c in pb.get("coeffs", [])]
    if aa and ab and aa != ab:
        blocking.append(f"attenuation differs: {aa} vs {ab} dB — a level "
                        "difference cannot null and is audible as tone")

    sa = a["report"].get("input_sha256")
    sb = b["report"].get("input_sha256")
    if sa and sb and sa != sb:
        blocking.append("the two runs used different input material")

    for run in (a, b):
        if (run["report"].get("chain") or {}).get("deliberate_resample"):
            blocking.append(
                f"{run['name']}: captured with --allow-resample, i.e. through a "
                "resampler.  Two runs of the same resampler are not guaranteed "
                "to be sample-identical (on Linux, MPD's soxr output was measured "
                "to differ run to run by a sub-sample offset, agreeing only to "
                "~112 dB below the signal), and a whole-sample null cannot "
                "absorb that — it would report DIFFERENT for two perfect "
                "resamples.  Measure each capture with resampler-residual.py and "
                "compare the numbers instead")
        if run["provenance"].get("loopback_resampling"):
            noted.append(f"{run['name']}: virtual_oss was started with -S, so a "
                         "resampler was in the path (quality defaults to "
                         "-Q 2, fastest)")
    return {"blocking": blocking, "noted": noted}


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("a", help="run prefix or .wire.raw from one machine")
    p.add_argument("b", help="run prefix or .wire.raw from the other")
    p.add_argument("--rate", type=int, help="override (needed for bare .raw)")
    p.add_argument("--channels", type=int, help="override (default 2)")
    p.add_argument("--json", help="write the full result here")
    args = p.parse_args()

    a = load_capture(args.a, args.rate, args.channels)
    b = load_capture(args.b, args.rate, args.channels)

    prov = compare_provenance(a, b)
    for line in prov["noted"]:
        print(f"note:  {line}")
    for line in prov["blocking"]:
        print(f"STOP:  {line}")
    if prov["blocking"]:
        print("\nThe two runs were not taken through the same thing, so a null "
              "between them says nothing about the operating systems. Fix the "
              "differences above and re-run.")
        return 2

    result = null(a, b)
    print(f"\n{a['name']}  {a['provenance'].get('os', '?')}  "
          f"{a['samples'].shape[0]} frames")
    print(f"{b['name']}  {b['provenance'].get('os', '?')}  "
          f"{b['samples'].shape[0]} frames")
    print(f"\naligned at lag {result['lag_frames']} frames "
          f"(r={result['alignment_confidence']:.6f}); compared "
          f"{result['compared_frames']} frames "
          f"({result['compared_seconds']} s)")
    print(f"differing samples : {result['differing_samples']} "
          f"({result['differing_fraction']:.3e} of all)")
    print(f"max difference    : {result['max_diff_lsb']} LSB of S32")
    print(f"null depth        : {result['null_depth_db']:.1f} dBFS")
    if result["first_diff"]:
        d = result["first_diff"]
        print(f"first difference  : frame {d['frame']} ({d['seconds']} s) "
              f"{d['a']} vs {d['b']}, delta {d['delta']}")
    print(f"\n{result['verdict']}: {result['reading']}")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"a": {k: a[k] for k in ("name", "raw", "rate", "channels")}
                  | {"provenance": a["provenance"]},
             "b": {k: b[k] for k in ("name", "raw", "rate", "channels")}
                  | {"provenance": b["provenance"]},
             "provenance_diff": prov, "result": result}, indent=2) + "\n")
    return 0 if result["verdict"] in ("IDENTICAL", "EQUIVALENT") else 1


if __name__ == "__main__":
    sys.exit(main())
