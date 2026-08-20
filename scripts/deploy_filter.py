#!/usr/bin/env python3
"""Builder library behind the single ``new_filter_design.py <dir>`` command.

Every plotted curve is one REW text export, carried into the bundle unchanged.
This module never averages, sums, convolves, interpolates or smooths a response
for display: the graph the web remote draws holds exactly the numbers REW
wrote.  The only DSP left here proves that the deployable impulse WAV really is
the filter whose exported response is plotted, and resamples that WAV into the
per-rate BruteFIR coefficients.

This tool never starts REW and does not parse ``.mdat``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np


def _styled(text: str, code: str) -> str:
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb":
        return f"\033[{code}m{text}\033[0m"
    return text


def progress(step: str, message: str, color: str = "1;36") -> None:
    print(f"{_styled(step, color)} {_styled(message, '1')}", flush=True)


def progress_ok(message: str, color: str = "1;32", label: str = "OK") -> None:
    print(f"  {_styled(label, color)}  {message}", flush=True)


# The engine checkout: helper scripts, CMake, the shipped `flat` set.
ROOT = Path(__file__).resolve().parents[1]
# Where the room-specific data lives — configs/<geometry> and filters/<geometry>.
# It defaults to the engine checkout, so an unconfigured tree behaves exactly as
# it always has.  Point OMDRC_SITE_ROOT (or --site-root) at a second checkout to
# keep personal measurements and filters out of a public engine repository.
SITE_ROOT_ENV = "OMDRC_SITE_ROOT"

# The eight exports that become the eight plotted curves, in legend order, plus
# the two impulse WAVs that become the runtime coefficients.  Every role is
# required: a design with a missing curve is a design the operator cannot read.
TRACE_ROLES = (
    "original_left", "original_right", "original_sum",
    "filter_left", "filter_right",
    "corrected_left", "corrected_right", "corrected_sum",
)
WAV_ROLES = ("filter_left_wav", "filter_right_wav")
ARTIFACT_ROLES = TRACE_ROLES + WAV_ROLES

# id, label (None takes the aggregate wording), colour, legend group, visible
TRACE_SPECS = (
    ("original_left",   "original-left",   "Original L",  "#58a6ff", "Original",  False),
    ("original_right",  "original-right",  "Original R",  "#d29922", "Original",  False),
    ("original_sum",    "original-sum",    None,          "#a371f7", "Original",  True),
    ("filter_left",     "filter-left",     "Filter FLX",  "#2f81f7", "Filter",    False),
    ("filter_right",    "filter-right",    "Filter FRX",  "#e3b341", "Filter",    False),
    ("corrected_left",  "corrected-left",  "Corrected L", "#3fb950", "Corrected", False),
    ("corrected_right", "corrected-right", "Corrected R", "#f85149", "Corrected", False),
    ("corrected_sum",   "corrected-sum",   None,          "#39d353", "Corrected", True),
)

# Two properties every plotted export must have, checked before anything is
# written.  Neither can be repaired afterwards, and neither is visible in the
# graph once the bundle exists.
MAX_MEASUREMENT_RATE_HZ = 48000
MAX_EXPORT_FREQUENCY_HZ = MAX_MEASUREMENT_RATE_HZ / 2.0

DEFAULT_LIMITS = {
    "max_rms_magnitude_db": 0.02,
    "max_rms_phase_deg": 0.2,
    "above_100_hz_max_magnitude_db": 0.1,
    "above_100_hz_max_phase_deg": 1.0,
}


def artifact_roles(recipe: dict) -> tuple[str, ...]:
    artifacts = recipe["source"]["artifacts"]
    missing = [role for role in ARTIFACT_ROLES if role not in artifacts]
    if missing:
        raise AuditError(f"recipe is missing required artifacts: {', '.join(missing)}")
    unknown = sorted(set(artifacts) - set(ARTIFACT_ROLES))
    if unknown:
        raise AuditError(f"recipe has unknown artifact roles: {', '.join(unknown)}")
    return ARTIFACT_ROLES


def aggregate_labels(aggregate: dict) -> tuple[str, str]:
    """Legend wording for the two aggregate curves, from the chosen filenames.

    The aggregate's meaning is carried by the name the operator gave the export
    — ``LR`` is REW's vector average, ``L+R`` is the sum — so nothing has to be
    asserted on a command line, and nothing is recalculated to match it.
    """
    style = aggregate.get("style", "L+R")
    if style == "LR":
        original, corrected = "Original L/R vector average", "Corrected L/R vector average"
    elif style == "L+R":
        original, corrected = "Original L+R", "Corrected L+R"
    else:
        raise AuditError(f"unknown aggregate style: {style!r}")
    if aggregate.get("corrected") == "remeasured":
        corrected += " (re-measured in the room)"
    return original, corrected


class AuditError(RuntimeError):
    pass


def resolve_site_root(explicit: Path | None = None) -> Path:
    """Resolve the root holding configs/<geometry> and filters/<geometry>.

    Precedence: an explicit --site-root, then $OMDRC_SITE_ROOT, then the engine
    checkout itself.  The last case is the historical single-repository layout.
    """
    value = explicit if explicit is not None else os.environ.get(SITE_ROOT_ENV)
    if not value:
        return ROOT
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise AuditError(f"site root is not a directory: {root}")
    return root


def add_site_root_argument(parser: argparse.ArgumentParser) -> None:
    """Register the common --site-root option on a workflow command."""
    parser.add_argument(
        "--site-root", type=Path, default=None, metavar="DIR",
        help="root holding configs/<geometry> and filters/<geometry> "
             f"(default: ${SITE_ROOT_ENV}, else this checkout)")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def bundle_identity_from_manifest(manifest: dict) -> dict:
    """Content identity of a bundle: what it claims, hashed into one ID.

    Schema 2 dropped the Git anchor.  A bundle now stands on the hashes of the
    exports it plots, the coefficients it deploys and the configs that load
    them — no repository, commit or tag participates.
    """
    if manifest.get("schema") != 2:
        raise AuditError(
            f"unsupported bundle schema {manifest.get('schema')!r}; redeploy this "
            "design with new_filter_design.py to publish a schema 2 bundle")
    return {
        "schema": manifest["schema"],
        "geometry": manifest["geometry"],
        "variant": manifest["variant"],
        "design_id": manifest.get("design_id", manifest["variant"]),
        "description": manifest["description"],
        "source_provenance_sha256": canonical_hash(manifest["source"]),
        "source_artifacts": {
            role: item["sha256"] for role, item in manifest["source"]["artifacts"].items()
        },
        "runtime": {
            rate: {
                "config": item["config"],
                "config_sha256": item["config_sha256"],
                "format": item["format"],
                "attenuation_db": item["attenuation_db"],
                "channels": {
                    channel: data["sha256"] for channel, data in item["channels"].items()
                },
            }
            for rate, item in manifest["runtime"]["rates"].items()
        },
        "analysis_sha256": manifest["analysis"]["sha256"],
    }


def run(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if proc.returncode:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise AuditError(f"command failed ({' '.join(args)}): {detail}")
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# Git answers two questions and no others: which project produced a design, and
# how to get any deployed filter set back.  It never takes part in verifying a
# bundle -- the content hashes do that -- so a checkout without Git still
# deploys, it just cannot promise the exports stay retrievable.


def git_toplevel(path: Path) -> Path | None:
    """The work tree `path` belongs to, or None when it is not under Git."""
    start = path if path.is_dir() else path.parent
    proc = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        text=True, capture_output=True)
    if proc.returncode:
        return None
    return Path(proc.stdout.strip()).resolve()


def git_text(repo: Path, *args: str) -> str:
    return run(["git", "-C", str(repo), *args])


def git_raw(repo: Path, *args: str) -> str:
    """Git output kept byte for byte -- NUL-separated formats do not survive strip."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True)
    if proc.returncode:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise AuditError(f"command failed (git {' '.join(args)}): {detail}")
    return proc.stdout


def git_maybe(repo: Path, *args: str) -> str:
    """Run a Git query whose failure is an answer rather than an error."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def git_relative(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo).as_posix()
    except ValueError as exc:
        raise AuditError(f"{path} is outside the work tree {repo}") from exc


def git_uncommitted(repo: Path, relatives: list[str]) -> list[str]:
    """Which of `relatives` differ from HEAD, staged or not, tracked or not.

    Staged-but-uncommitted counts as uncommitted: a commit is what makes the
    bytes retrievable later, and that is the only reason Git is consulted here.
    """
    fields = [item for item in
              git_raw(repo, "status", "--porcelain=1", "-z", "--", *relatives).split("\0")
              if item]
    dirty: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        code, name = entry[:2], entry[3:]
        dirty.append(name)
        if code[0] in ("R", "C") and index < len(fields):
            dirty.append(fields[index])
            index += 1
    return [item for item in relatives if item in set(dirty)]


def git_blobs(repo: Path, relatives: list[str]) -> dict[str, str]:
    """HEAD blob ID of each path, so `git cat-file blob <id>` restores it."""
    blobs: dict[str, str] = {}
    listing = ""
    if git_maybe(repo, "rev-parse", "--verify", "--quiet", "HEAD"):
        listing = git_raw(repo, "ls-tree", "-z", "HEAD", "--", *relatives)
    for line in listing.split("\0"):
        if not line:
            continue
        meta, _, name = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            blobs[name] = parts[2]
    return blobs


def git_project(repo: Path) -> dict:
    """Identity of the repository a design was exported from."""
    return {
        "name": repo.name,
        "repository": str(repo),
        "remote": git_maybe(repo, "config", "--get", "remote.origin.url"),
        "commit": git_maybe(repo, "rev-parse", "HEAD"),
        "committed_at": git_maybe(repo, "log", "-1", "--format=%cI"),
        "subject": git_maybe(repo, "log", "-1", "--format=%s"),
        "branch": git_maybe(repo, "rev-parse", "--abbrev-ref", "HEAD"),
    }


def safe_source(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise AuditError(f"source path must be relative: {relative}")
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AuditError(f"source escapes repository: {relative}") from exc
    if not candidate.is_file() or candidate.is_symlink():
        raise AuditError(f"source is not a regular, non-symlink file: {candidate}")
    return candidate


def parse_rew_txt(path: Path) -> tuple[dict[str, str], np.ndarray, np.ndarray, np.ndarray]:
    headers: dict[str, str] = {}
    rows: list[tuple[float, float, float]] = []
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line in stream:
            text = line.strip()
            if not text:
                continue
            if text.startswith("*"):
                item = text[1:].strip()
                if item.lower().startswith("measurement data measured by rew "):
                    headers["rew_version"] = item[len("Measurement data measured by "):]
                if ":" in item:
                    key, value = item.split(":", 1)
                    headers[key.strip().lower()] = value.strip()
                continue
            parts = text.split()
            if len(parts) < 3:
                continue
            try:
                rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError:
                continue
    if not rows:
        raise AuditError(f"no three-column frequency response rows in {path}")
    data = np.asarray(rows, dtype=np.float64)
    if np.any(np.diff(data[:, 0]) <= 0):
        raise AuditError(f"frequency grid is not strictly increasing: {path}")
    return headers, data[:, 0], data[:, 1], data[:, 2]


def load_filter_wav(path: Path) -> tuple[int, np.ndarray]:
    """Decode a mono filter WAV without depending on Python's limited WAV parser.

    REW commonly writes IEEE-float WAV files (format tag 3), which the stdlib
    ``wave`` module does not accept.  SoX is already the converter used by the
    deployment pipeline, and decoding to f64 here gives PCM S32 and float WAVs
    the same validation path.
    """
    try:
        channels = int(run(["soxi", "-c", str(path)]))
        rate = int(run(["soxi", "-r", str(path)]))
        proc = subprocess.run(
            ["sox", str(path), "-t", "raw", "-e", "floating-point", "-b", "64",
             "-L", "-c", "1", "-"],
            capture_output=True,
        )
    except (OSError, ValueError) as error:
        raise AuditError(f"cannot inspect filter WAV with SoX: {path}: {error}") from error
    if channels != 1:
        raise AuditError(f"filter WAV must be mono, got {channels} channels: {path}")
    if proc.returncode:
        detail = proc.stderr.decode(errors="replace").strip()
        raise AuditError(f"cannot decode filter WAV with SoX: {path}: {detail}")
    samples = np.frombuffer(proc.stdout, dtype="<f8").copy()
    if samples.size < 2 or not np.all(np.isfinite(samples)):
        raise AuditError(f"filter WAV has no usable finite impulse response: {path}")
    return rate, samples


def wrap_phase_deg(value: np.ndarray) -> np.ndarray:
    return (value + 180.0) % 360.0 - 180.0


def db_phase(value: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return 20.0 * np.log10(np.maximum(np.abs(value), 1e-12)), np.degrees(np.angle(value))


def filter_spectrum(ir: np.ndarray, rate: int, delay_samples: int) -> tuple[np.ndarray, np.ndarray]:
    spectrum = np.fft.rfft(ir)
    freqs = np.fft.rfftfreq(ir.size, 1.0 / rate)
    spectrum *= np.exp(1j * 2.0 * np.pi * freqs * delay_samples / rate)
    return freqs, spectrum


def response_metrics(txt_freqs: np.ndarray, txt_mag: np.ndarray, txt_phase: np.ndarray,
                     fft_freqs: np.ndarray, fft_values: np.ndarray,
                     txt_to_wav_gain_db: float = 0.0) -> dict:
    indices = np.clip(np.rint(txt_freqs * (2 * (len(fft_freqs) - 1)) /
                              (fft_freqs[-1] * 2)).astype(int), 0, len(fft_freqs) - 1)
    # The expression above reduces to round(f * N / Fs), while retaining the
    # actual FFT grid as the source of truth.
    selected = fft_values[indices]
    got_mag, got_phase = db_phase(selected)
    mag_error = got_mag - (txt_mag + txt_to_wav_gain_db)
    phase_error = wrap_phase_deg(got_phase - txt_phase)
    # The displayed/audible validation band is 100 Hz--20 kHz.  The export also
    # contains bins almost to Nyquist, where a finite-window endpoint is much
    # more sensitive and is not used by the response page.
    audio = (txt_freqs >= 100.0) & (txt_freqs <= 20_000.0)
    return {
        "rows": int(txt_freqs.size),
        "max_frequency_grid_error_hz": round(float(np.max(np.abs(fft_freqs[indices] - txt_freqs))), 9),
        "rms_magnitude_db": round(float(np.sqrt(np.mean(mag_error ** 2))), 6),
        "rms_phase_deg": round(float(np.sqrt(np.mean(phase_error ** 2))), 6),
        "above_100_hz_max_magnitude_db": round(float(np.max(np.abs(mag_error[audio]))), 6),
        "above_100_hz_max_phase_deg": round(float(np.max(np.abs(phase_error[audio]))), 6),
    }


def detect_filter_alignment(txt_freqs: np.ndarray, txt_mag: np.ndarray,
                            txt_phase: np.ndarray, rate: int,
                            impulse: np.ndarray) -> dict:
    """Find and prove the fixed delay/gain relating a REW TXT to its WAV.

    The search is deliberately narrow around the impulse's dominant sample.
    It cannot bend the response to make unrelated files match: only an integer
    causal delay and one frequency-independent gain are fitted.  The normal
    TXT/WAV residual limits still decide whether the pair is acceptable.
    """
    peak = int(np.argmax(np.abs(impulse)))
    lower = max(0, peak - 16)
    upper = min(impulse.size - 1, peak + 16)
    best: dict | None = None
    for delay in range(lower, upper + 1):
        fft_freqs, fft_values = filter_spectrum(impulse, rate, delay)
        indices = np.clip(
            np.rint(txt_freqs * impulse.size / rate).astype(int),
            0,
            len(fft_freqs) - 1,
        )
        got_mag, _ = db_phase(fft_values[indices])
        audio = (txt_freqs >= 100.0) & (txt_freqs <= 20_000.0)
        if np.count_nonzero(audio) < 3:
            raise AuditError("filter TXT has insufficient data between 100 Hz and 20 kHz")
        gain = float(np.median(got_mag[audio] - txt_mag[audio]))
        metrics = response_metrics(
            txt_freqs, txt_mag, txt_phase, fft_freqs, fft_values, gain)
        candidate = {
            "delay_samples": delay,
            "txt_to_wav_gain_db": gain,
            "impulse_peak_sample": peak,
            "metrics": metrics,
        }
        score = (metrics["rms_phase_deg"], metrics["rms_magnitude_db"])
        if best is None or score < best["score"]:
            best = {**candidate, "score": score}
    assert best is not None
    best.pop("score")
    return best


def parse_config(path: Path, expected_rate: int) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"sampling_rate:\s*(\d+)", text)
    if not match or int(match.group(1)) != expected_rate:
        raise AuditError(f"wrong sampling_rate in {path}")
    coeffs: dict[str, dict] = {}
    for block in re.finditer(r'coeff\s+"([^"]+)"\s*\{([^}]+)\}', text):
        label, body = block.groups()
        filename = re.search(r'filename:\s*"([^"]+)"', body)
        fmt = re.search(r'format:\s*"([^"]+)"', body)
        attenuation = re.search(r"attenuation:\s*([-\d.]+)", body)
        if filename:
            channel = "left" if label.endswith("l") else "right" if label.endswith("r") else label
            coeffs[channel] = {
                "filename": filename.group(1),
                "format": fmt.group(1) if fmt else "FLOAT64_LE",
                "attenuation_db": float(attenuation.group(1)) if attenuation else 0.0,
            }
    if set(coeffs) != {"left", "right"}:
        raise AuditError(f"expected exactly left/right coeff blocks in {path}")
    if coeffs["left"]["attenuation_db"] != coeffs["right"]["attenuation_db"]:
        raise AuditError(f"left/right attenuation differs in {path}")
    return {"sha256": sha256_file(path), "coeffs": coeffs,
            "attenuation_db": coeffs["left"]["attenuation_db"]}


def peak_gain_db(samples: np.ndarray) -> float:
    # Zero-pad to the next power of two. Besides FFT efficiency this samples
    # between the native bins, avoiding an optimistic headroom estimate when a
    # narrow response peak falls between them.
    fft_size = 1 << (max(1, samples.size) - 1).bit_length()
    peak = float(np.max(np.abs(np.fft.rfft(samples, n=fft_size))))
    return 20.0 * math.log10(peak) if peak > 0 else float("-inf")


def required_attenuation(peak_db: float, margin_db: float) -> float:
    return max(0.0, math.ceil((peak_db + margin_db) * 10.0) / 10.0)


def export_defects(parsed: dict) -> list[tuple[str, str]]:
    """(role, reason) for every export that must not be deployed.

    Two properties are required of all eight, and a bundle that lacks either is
    refused rather than annotated.  A smoothed export is a decision REW already
    baked into the numbers: it cannot be undone here, so the page would draw a
    smoothed curve while promising a measurement.  An export reaching past
    24 kHz came from a measurement above 48 kHz, and the deployed filters are
    resampled from one 48 kHz impulse -- a corrected curve drawn beside it would
    describe a system that was never built.
    """
    defects: list[tuple[str, str]] = []
    for role in TRACE_ROLES:
        headers, freqs, _, _ = parsed[role]
        smoothing = headers.get("smoothing", "").strip()
        if not smoothing:
            defects.append((role, "states no smoothing, so it cannot be shown to be "
                                  "unsmoothed; re-export it from REW"))
        elif smoothing.lower() != "none":
            defects.append((role, f"carries REW smoothing '{smoothing}'; re-export it "
                                  "with smoothing set to None"))
        top = float(freqs[-1]) if len(freqs) else 0.0
        if top > MAX_EXPORT_FREQUENCY_HZ:
            defects.append((
                role,
                f"reaches {top:,.1f} Hz, so it was measured at {2.0 * top:,.0f} Hz or "
                f"more; {MAX_MEASUREMENT_RATE_HZ:,} Hz is the highest this pipeline "
                "deploys"))
    return defects


def grid_key(freqs) -> str:
    """Stable identity of one frequency grid, so identical grids are stored once.

    Sharing a grid is purely an encoding choice: the numbers each trace plots
    are the ones its own export contained, and two traces share an entry only
    when their grids are bit-identical.
    """
    return hashlib.sha256(np.ascontiguousarray(freqs, dtype="<f8").tobytes()).hexdigest()[:16]


def build_analysis(recipe: dict, source_paths: dict[str, Path]) -> tuple[dict, dict]:
    """Graph data for one design: eight REW exports, carried through unchanged.

    Nothing here derives a curve from another curve.  The magnitude and phase
    columns of each export become one trace verbatim, so the web remote and REW
    draw the same numbers.  The DSP that remains relates each filter TXT to the
    impulse WAV that is about to become the runtime coefficients — without it
    the plotted filter response would be an unverified claim about the bytes
    BruteFIR actually loads.
    """
    parsed = {role: parse_rew_txt(source_paths[role]) for role in TRACE_ROLES}
    defects = export_defects(parsed)
    if defects:
        raise AuditError(
            "these exports cannot be deployed: "
            + "; ".join(f"{source_paths[role].name} {reason}"
                        for role, reason in defects))

    source_rate = int(recipe["filter"]["sample_rate"])
    delay_samples = int(recipe["filter"]["delay_samples"])
    txt_to_wav_gain = recipe["filter"].get(
        "txt_to_wav_gain_db", {"left": 0.0, "right": 0.0})
    validation: dict[str, dict] = {}
    for channel in ("left", "right"):
        rate, impulse = load_filter_wav(source_paths[f"filter_{channel}_wav"])
        if rate != source_rate:
            raise AuditError(f"{channel} WAV rate {rate} != declared {source_rate}")
        fft_freqs, fft_values = filter_spectrum(impulse, rate, delay_samples)
        _, tfreqs, tmag, tphase = parsed[f"filter_{channel}"]
        metrics = response_metrics(
            tfreqs, tmag, tphase, fft_freqs, fft_values,
            float(txt_to_wav_gain.get(channel, 0.0)))
        limits = recipe["filter"]["txt_wav_limits"]
        metrics["passed"] = (
            metrics["rms_magnitude_db"] <= limits["max_rms_magnitude_db"] and
            metrics["rms_phase_deg"] <= limits["max_rms_phase_deg"] and
            metrics["above_100_hz_max_magnitude_db"] <= limits["above_100_hz_max_magnitude_db"] and
            metrics["above_100_hz_max_phase_deg"] <= limits["above_100_hz_max_phase_deg"]
        )
        if not metrics["passed"]:
            raise AuditError(f"{channel} filter TXT/WAV response check failed: {metrics}")
        validation[channel] = metrics

    aggregate = recipe.get("aggregate", {"style": "L+R", "corrected": "filtered"})
    original_sum_label, corrected_sum_label = aggregate_labels(aggregate)
    grids: dict[str, list[float]] = {}
    traces = []
    for role, identifier, label, color, group, visible in TRACE_SPECS:
        _, freqs, magnitude, phase = parsed[role]
        key = grid_key(freqs)
        if key not in grids:
            grids[key] = np.round(freqs, 6).tolist()
        item = {
            "id": identifier,
            "label": label or (original_sum_label if group == "Original"
                               else corrected_sum_label),
            "color": color,
            "group": group,
            "default_visible": visible,
            "grid": key,
            # REW writes magnitude to 3 decimals and phase to 4.  Round to the
            # precision the export actually carries and no further, and never
            # re-wrap the phase: a value REW wrote as +180.0000 stays +180.0000.
            "magnitude_db": np.round(magnitude, 3).tolist(),
            "phase_deg": np.round(phase, 4).tolist(),
            "source_file": recipe["source"]["artifacts"][role]["path"],
        }
        if identifier == "original-sum":
            item["dash"] = [3, 3]
        traces.append(item)

    analysis = {
        "schema": 2,
        "geometry": recipe["geometry"],
        "variant": recipe["variant"],
        "design_id": recipe.get("design_id", recipe["variant"]),
        "description": recipe.get("description", recipe.get("design_id", recipe["variant"])),
        "frequency_grids": grids,
        "traces": traces,
        "calculation": {
            "note": ("None. Every trace is one REW text export, plotted as exported: "
                     "no average, sum, convolution, interpolation or smoothing is "
                     "applied at any stage between REW and the graph. Every export "
                     "was verified unsmoothed and within the 48 kHz measurement "
                     "limit before publication."),
            "aggregate": aggregate,
            "smoothing_applied": "none",
            "exports_carrying_rew_smoothing": [],
            "highest_exported_frequency_hz": round(
                max(float(parsed[role][1][-1]) for role in TRACE_ROLES), 6),
            "measurement_rate_limit_hz": MAX_MEASUREMENT_RATE_HZ,
            "filter_delay_removed_samples": delay_samples,
        },
        "inputs": {role: recipe["source"]["artifacts"][role]["sha256"]
                   for role in artifact_roles(recipe)},
        "source_headers": {
            role: {key: parsed[role][0].get(key, "") for key in
                   ("rew_version", "source", "format", "dated",
                    "note", "measurement", "smoothing", "frequency step", "start frequency")}
            for role in TRACE_ROLES
        },
        "validation": {"filter_txt_to_wav": validation},
    }
    return analysis, validation


def render_config(geometry: str, rate: int, selector: str,
                  attenuation: float, fmt: str) -> str:
    subdir = f"/{selector}" if selector else ""
    return f'''logic: "cli" {{ port: 3000; }};

sampling_rate: {rate};

coeff "c-l" {{
\tfilename: "@REPO_DIR@/filters/{geometry}/{rate}{subdir}/L.raw";
\tformat: "{fmt}";
\tattenuation: {attenuation:.1f};
}};

coeff "c-r" {{
\tfilename: "@REPO_DIR@/filters/{geometry}/{rate}{subdir}/R.raw";
\tformat: "{fmt}";
\tattenuation: {attenuation:.1f};
}};

input "left_in", "right_in" {{
}};

output "left_out", "right_out" {{
}};

filter "drc_l" {{
\tfrom_inputs: "left_in";
\tto_outputs: "left_out";
\tcoeff: "c-l";
}};

filter "drc_r" {{
\tfrom_inputs: "right_in";
\tto_outputs: "right_out";
\tcoeff: "c-r";
}};
'''


def generate_runtime(recipe: dict, source_paths: dict[str, Path],
                     staging: Path, site_root: Path) -> tuple[dict, dict[str, Path]]:
    tool = ROOT / "scripts/REW2raw.sh"
    runtime: dict[str, dict] = {}
    staged_configs: dict[str, Path] = {}
    margin = float(recipe["runtime"]["safety_margin_db"])
    selector = recipe["runtime"].get("selector", "")
    if selector and not re.fullmatch(r"@[A-Za-z0-9][A-Za-z0-9._-]*", selector):
        raise AuditError("new-design selector must have form @name")
    generated_configs = bool(recipe["runtime"].get("generate_configs", False))
    rate_items = list(recipe["runtime"]["rates"].items())
    conversion_total = len(rate_items) * 2
    conversion_number = 0
    source_rate = int(recipe["filter"]["sample_rate"])
    progress(
        "[RUNTIME]",
        f"Generate {len(rate_items)} sample-rate flavours with SoX "
        f"({conversion_total} channel conversions)",
        "1;35")
    print(
        "  quality: rate -v -L -s; output: FLOAT64_LE; "
        "FIR coefficient scale = source_rate / target_rate; "
        "gain = 20*log10(scale)",
        flush=True,
    )
    for rate_text, config_relative in rate_items:
        rate = int(rate_text)
        fir_scale = source_rate / rate
        fir_gain_db = 20.0 * math.log10(fir_scale)
        config_path = site_root / config_relative
        rate_dir = staging / rate_text / selector if selector else staging / rate_text
        rate_dir.mkdir(parents=True)
        channels: dict[str, dict] = {}
        peak_values: list[float] = []
        for channel in ("left", "right"):
            conversion_number += 1
            output = rate_dir / ("L.raw" if channel == "left" else "R.raw")
            source = source_paths[f"filter_{channel}_wav"]
            progress(
                f"[SoX {conversion_number}/{conversion_total}]",
                f"{channel} {source.name}: {source_rate:,} -> {rate:,} Hz; "
                f"FIR scale {source_rate}/{rate} = {fir_scale:.9f}; "
                f"applied gain {fir_gain_db:+.6f} dB",
                "1;35")
            run([str(tool), "--exact-output", "--no-keep-intermediate",
                 str(source), str(output), "raw", rate_text])
            values = np.fromfile(output, dtype="<f8")
            peak = peak_gain_db(values)
            peak_values.append(peak)
            channels[channel] = {
                "path": f"{rate_text}/{selector + '/' if selector else ''}{output.name}",
                "sha256": sha256_file(output),
                "bytes": output.stat().st_size,
                "samples": int(values.size),
                "peak_gain_db": round(peak, 6),
            }
            progress_ok(
                f"{output.name} {values.size:,} samples, peak {peak:+.3f} dB, "
                f"sha256 {channels[channel]['sha256'][:12]}",
                "1;35", "SoX OK")
        worst_peak = max(peak_values)
        required = required_attenuation(worst_peak, margin)
        progress(
            f"[HEADROOM {rate:,} Hz]",
            f"max(L {peak_values[0]:+.3f}, R {peak_values[1]:+.3f}) dB "
            f"+ {margin:.1f} dB margin = {worst_peak + margin:+.3f} dB; "
            f"ceil to 0.1 dB -> {required:.1f} dB required attenuation",
            "1;33")
        if generated_configs:
            requested = recipe["runtime"].get("attenuation_db", "auto")
            attenuation = required if requested == "auto" else float(requested)
            if attenuation + 1e-9 < required:
                raise AuditError(
                    f"requested attenuation {attenuation} dB is below required {required} dB at {rate} Hz")
            config_stage = staging / "_configs" / config_relative
            config_stage.parent.mkdir(parents=True, exist_ok=True)
            config_stage.write_text(render_config(
                recipe["geometry"], rate, selector, attenuation,
                recipe["runtime"]["format"]), encoding="utf-8")
            config = parse_config(config_stage, rate)
            staged_configs[config_relative] = config_stage
            progress_ok(
                f"baked attenuation: {attenuation:.1f} dB into both BruteFIR "
                f"coeff blocks in staged {config_relative}",
                "1;34", "CONFIG")
        else:
            config = parse_config(config_path, rate)
            progress_ok(
                f"using existing BruteFIR config {config_relative} with "
                f"attenuation {config['attenuation_db']:.1f} dB",
                "1;34", "CONFIG")
        expected_middle = f"/{selector}" if selector else ""
        for channel, output_name in (("left", "L.raw"), ("right", "R.raw")):
            expected_suffix = f"/filters/{recipe['geometry']}/{rate_text}{expected_middle}/{output_name}"
            coeff = config["coeffs"][channel]
            if not coeff["filename"].endswith(expected_suffix):
                raise AuditError(f"config path is not the expected deployed {channel} RAW: {config_path}")
            if coeff["format"] != recipe["runtime"]["format"]:
                raise AuditError(f"config format differs from recipe: {config_path}")
        if config["attenuation_db"] + 1e-9 < required:
            raise AuditError(f"{config_path}: attenuation {config['attenuation_db']} dB is below required {required} dB")
        progress_ok(
            f"config read-back verified {config['attenuation_db']:.1f} dB >= "
            f"{required:.1f} dB; config sha256 {config['sha256'][:12]}",
            "1;34", "CONFIG VERIFIED")
        runtime[rate_text] = {
            "config": config_relative,
            "config_sha256": config["sha256"],
            "format": recipe["runtime"]["format"],
            "attenuation_db": config["attenuation_db"],
            "required_attenuation_db": required,
            "safety_margin_db": margin,
            "channels": channels,
        }
    return runtime, staged_configs


def artifact_record(source_root: Path, recipe: dict, role: str) -> dict:
    item = recipe["source"]["artifacts"][role]
    source = safe_source(source_root, item["path"])
    return {
        "role": role,
        "path": item["path"],
        "bundle_path": item["bundle_path"],
        "sha256": sha256_file(source),
        "bytes": source.stat().st_size,
        **{key: item[key] for key in ("measurement", "smoothing", "git_blob")
           if key in item},
    }


def atomic_copy(source: Path, destination: Path) -> None:
    if (destination.is_file() and not destination.is_symlink() and
            destination.stat().st_size == source.stat().st_size and
            sha256_file(destination) == sha256_file(source)):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(fd)
    temp_path = Path(temporary)
    try:
        shutil.copyfile(source, temp_path)
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def atomic_json(value: object, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent, text=True)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def unreferenced_files(geometry_root: Path, design_id: str,
                       artifacts: dict, runtime: dict) -> list[Path]:
    """Files inside the directories this design owns that it no longer names.

    Only ``source/<design>/`` and ``<rate>/@<design>/`` are considered: those
    belong to one design and nothing else.  A rate directory without a design
    selector is shared with the historical ``default`` set and is never touched.
    """
    owned: dict[Path, set[str]] = {}
    source_dir = geometry_root / "source" / design_id
    owned[source_dir] = {Path(item["bundle_path"]).name for item in artifacts.values()}
    for item in runtime.values():
        for channel in ("left", "right"):
            relative = Path(item["channels"][channel]["path"])
            if len(relative.parts) < 3:
                continue  # <rate>/L.raw: the shared default set
            owned.setdefault(geometry_root / relative.parent, set()).add(relative.name)
    stale: list[Path] = []
    for directory, keep in owned.items():
        if not directory.is_dir():
            continue
        stale.extend(
            path for path in sorted(directory.iterdir())
            if path.is_file() and not path.is_symlink() and path.name not in keep)
    return stale


def publish_bundle(recipe: dict, source_root: Path, site_root: Path,
                   *, apply: bool, replace: bool) -> dict:
    """Verify the selected exports, regenerate every rate, and publish.

    All work happens in private staging first.  Without ``apply`` the audit
    runs to completion and reports what would change, touching nothing.
    """
    design_id = recipe.get("design_id", recipe["variant"])
    geometry_root = site_root / "filters" / recipe["geometry"]
    if site_root != ROOT:
        progress("[SITE]", f"configs and filters resolve under {site_root}", "1;34")

    progress(
        "[DEPLOY 1/4]",
        f"Hash the selected exports for {recipe['geometry']}/{design_id}")
    roles = artifact_roles(recipe)
    source_items = recipe["source"]["artifacts"]
    relatives = [source_items[role]["path"] for role in roles]
    if len(relatives) != len(set(relatives)):
        raise AuditError("one source file is assigned to more than one role")
    source_paths: dict[str, Path] = {}
    artifacts: dict[str, dict] = {}
    for role in roles:
        source_paths[role] = safe_source(source_root, source_items[role]["path"])
        artifacts[role] = artifact_record(source_root, recipe, role)
        if artifacts[role]["sha256"] != source_items[role]["sha256"]:
            raise AuditError(f"{role} changed on disk since it was inspected")
    progress_ok(f"{len(artifacts)} source exports hashed under {source_root}")

    progress(
        "[DEPLOY 2/4]",
        "Read the eight REW exports and relate each filter TXT to its impulse WAV")
    analysis, response_validation = build_analysis(recipe, source_paths)
    analysis_bytes = (json.dumps(analysis, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    analysis_hash = hashlib.sha256(analysis_bytes).hexdigest()
    points = sum(len(item["magnitude_db"]) for item in analysis["traces"])
    progress_ok(
        f"{len(analysis['traces'])} traces carried through unchanged, "
        f"{points:,} plotted points, analysis sha256 {analysis_hash[:12]}")

    with tempfile.TemporaryDirectory(prefix="omdrc-filter-deploy-") as temp_name:
        staging = Path(temp_name)
        progress(
            "[DEPLOY 3/4]",
            "Run SoX conversion for every declared rate/channel in private staging")
        runtime, staged_configs = generate_runtime(recipe, source_paths, staging, site_root)
        progress_ok(f"generated and checked {len(runtime)} complete runtime rate pairs")
        differing_runtime: list[str] = []
        deployed_runtime = 0
        for rate, item in runtime.items():
            for channel in ("left", "right"):
                relative = item["channels"][channel]["path"]
                live = geometry_root / relative
                staged = staging / relative
                if not live.exists():
                    continue
                deployed_runtime += 1
                if sha256_file(live) != sha256_file(staged):
                    differing_runtime.append(relative)
        differing_configs = [relative for relative, staged in staged_configs.items()
                             if (site_root / relative).exists() and
                             sha256_file(site_root / relative) != sha256_file(staged)]
        # A design owns source/<design>/ and every <rate>/@<design>/ outright, so
        # after publication those directories must hold exactly what the new
        # manifest names.  Anything else is a leftover of an earlier deployment
        # under different filenames, and leaving it would put files beside the
        # bundle that nothing references and nobody can account for.
        unreferenced = unreferenced_files(
            geometry_root, design_id, artifacts, runtime)

        progress(
            "[DEPLOY 4/4]",
            "Bind runtime hashes, configs and analysis into the bundle manifest")
        # The source block is hashed whole into the bundle ID, so the project
        # a design came from and the .mdat its exports were taken from are as
        # binding as the exports themselves.
        source_manifest = {
            "directory": recipe["source"]["directory"],
            "project": recipe["source"].get("project", {}),
            "measurements": recipe["source"].get("measurements", {}),
            "artifacts": artifacts,
        }
        identity = {
            "schema": 2,
            "geometry": recipe["geometry"],
            "variant": recipe["variant"],
            "design_id": design_id,
            "description": recipe["description"],
            "source_provenance_sha256": canonical_hash(source_manifest),
            "source_artifacts": {role: item["sha256"] for role, item in artifacts.items()},
            "runtime": {rate: {
                "config": item["config"],
                "config_sha256": item["config_sha256"],
                "format": item["format"],
                "attenuation_db": item["attenuation_db"],
                "channels": {channel: data["sha256"] for channel, data in item["channels"].items()},
            } for rate, item in runtime.items()},
            "analysis_sha256": analysis_hash,
        }
        manifest = {
            "schema": 2,
            "bundle_id": canonical_hash(identity),
            "geometry": recipe["geometry"],
            "variant": recipe["variant"],
            "design_id": design_id,
            "description": recipe["description"],
            "verification": {
                "status": "verified",
                "audited_at": recipe["audited_at"],
                "claims": [
                    "every plotted trace is one REW text export, stored and hashed verbatim",
                    "no average, sum, convolution or smoothing stands between REW and the graph",
                    "filter TXT responses match the canonical WAV responses within declared limits",
                    "all runtime RAWs reproduce from those canonical WAVs",
                    "every BruteFIR config maps to the hashed RAW pair and has sufficient headroom",
                    "graph inputs are content-hash bound to this manifest",
                ],
                "prediction": (
                    "none: the corrected curves are measured REW exports, not a prediction"),
            },
            "source": source_manifest,
            "aggregate": recipe["aggregate"],
            "filter_validation": response_validation,
            "runtime": {"rates": runtime},
            "analysis": {
                "path": f"analysis/{design_id}.json",
                "sha256": analysis_hash,
                "bytes": len(analysis_bytes),
            },
        }

        print(f"PASS: {len(artifacts)} selected exports hashed into the bundle")
        for channel, values in response_validation.items():
            print(f"PASS: {channel} TXT/WAV RMS {values['rms_magnitude_db']:.6f} dB, "
                  f"{values['rms_phase_deg']:.6f} deg")
        for rate, item in runtime.items():
            pair = item["channels"]
            print(f"PASS: {rate} Hz L={pair['left']['sha256'][:12]} R={pair['right']['sha256'][:12]} "
                  f"required={item['required_attenuation_db']:.1f} configured={item['attenuation_db']:.1f} dB")
        print(f"Bundle ID: {manifest['bundle_id']}")

        if not apply:
            if differing_runtime:
                print("DRY RUN: runtime files that would change: " + ", ".join(differing_runtime))
            elif deployed_runtime:
                print(f"DRY RUN: all {deployed_runtime} existing runtime RAWs are "
                      "already byte-identical")
            else:
                print("DRY RUN: no runtime RAW is deployed for this design yet")
            if staged_configs:
                creating = [path for path in staged_configs if not (site_root / path).exists()]
                print("DRY RUN: configs to create: " + (", ".join(creating) if creating else "none"))
            if differing_configs:
                print("DRY RUN: configs that would change: " + ", ".join(differing_configs))
            if unreferenced:
                print(f"DRY RUN: {len(unreferenced)} unreferenced file(s) this design "
                      "owns would be removed: "
                      + ", ".join(sorted(str(item.relative_to(geometry_root))
                                         for item in unreferenced)))
            print("No files written.")
            return manifest

        if (differing_runtime or differing_configs) and not replace:
            raise AuditError(
                "this design ID already has different deployed files; re-run with "
                "--replace-design to overwrite them")

        # Source copies first, analysis next, runtime pair members next, manifest
        # last. The manifest is the commit marker: readers ignore an incomplete
        # deployment until it exists and verifies all preceding hashes.
        for role, item in artifacts.items():
            atomic_copy(source_paths[role], geometry_root / item["bundle_path"])
        atomic_json(analysis, geometry_root / f"analysis/{design_id}.json")
        for rate, item in runtime.items():
            for channel in ("left", "right"):
                relative = item["channels"][channel]["path"]
                atomic_copy(staging / relative, geometry_root / relative)
        for relative, staged in staged_configs.items():
            atomic_copy(staged, site_root / relative)
            progress(
                "[CONFIG PUBLISHED]",
                f"installed verified BruteFIR template {relative}",
                "1;34")
        atomic_json(manifest, geometry_root / f"provenance/{design_id}.json")
        # Last, so a failure here leaves harmless leftovers rather than a bundle
        # whose manifest names a file that was already deleted.
        for stale in unreferenced:
            stale.unlink()
            progress("[PRUNED]", f"removed unreferenced {stale.relative_to(geometry_root)}",
                     "1;33")
        print(f"WROTE: verified bundle under {geometry_root}")
    return manifest


def commit_message(manifest: dict) -> str:
    """One commit subject and body that names everything needed to come back."""
    design_id = manifest.get("design_id", manifest["variant"])
    source = manifest["source"]
    project = source.get("project") or {}
    measurements = source.get("measurements") or {}
    rates = " ".join(sorted(manifest["runtime"]["rates"], key=int))
    lines = [
        f"Deploy {manifest['geometry']} @{design_id} "
        f"(bundle {manifest['bundle_id'][:12]})",
        "",
        f"Geometry:     {manifest['geometry']}",
        f"Design:       {design_id}",
        f"Bundle:       {manifest['bundle_id']}",
        f"Aggregate:    {manifest['aggregate']['style']} "
        f"({manifest['aggregate']['corrected']} after correction)",
        f"Rates:        {rates}",
    ]
    if project:
        lines.append(
            f"Project:      {project.get('name', '?')} "
            f"@ {(project.get('commit') or 'uncommitted')[:12]}"
            + ("" if project.get("clean", True) else "  [UNCOMMITTED SOURCES]"))
        if project.get("path"):
            lines.append(f"Exports:      {project['path']}")
        if project.get("remote"):
            lines.append(f"Remote:       {project['remote']}")
    if measurements:
        lines.append(
            f"Measurements: {measurements.get('file', '?')} "
            f"sha256 {measurements.get('sha256', '')[:16]}")
        if measurements.get("git_blob"):
            where = project.get("remote") or project.get("repository") or "<project>"
            lines.append(f"  restore:    git -C {where} cat-file blob "
                         f"{measurements['git_blob']} > {measurements.get('file', '')}")
    return "\n".join(lines) + "\n"


def git_commit_paths(repo: Path, paths: list[str], message: str) -> dict:
    """Commit exactly `paths`, or say why there is nothing (or no way) to commit."""
    git_text(repo, "add", "-A", "--", *paths)
    born = bool(git_maybe(repo, "rev-parse", "--verify", "--quiet", "HEAD"))
    if born and not git_maybe(repo, "diff", "--cached", "--name-only", "--", *paths):
        return {"status": "unchanged", "repository": str(repo)}
    proc = subprocess.run(
        ["git", "-C", str(repo), "commit", "--only", "-F", "-", "--", *paths],
        input=message, text=True, capture_output=True)
    if proc.returncode:
        return {"status": "failed", "repository": str(repo),
                "error": (proc.stderr.strip() or proc.stdout.strip())}
    return {
        "status": "committed",
        "repository": str(repo),
        "commit": git_maybe(repo, "rev-parse", "HEAD"),
        "subject": message.splitlines()[0],
    }


def commit_site(site_root: Path, manifest: dict) -> dict:
    """Record this deployment as one commit in the room's repository.

    The room repository *is* the deployment history: ``git log`` lists every
    filter set that was ever live and ``git checkout <commit>`` brings any of
    them back byte for byte.  A room that is not a work tree still deploys;
    it simply keeps no history, and the caller says so.
    """
    repo = git_toplevel(site_root)
    if repo is None:
        return {"status": "no-repo", "root": str(site_root)}
    geometry = manifest["geometry"]
    present = [item for item in (f"filters/{geometry}", f"configs/{geometry}")
               if (repo / item).exists()]
    return git_commit_paths(repo, present, commit_message(manifest))
