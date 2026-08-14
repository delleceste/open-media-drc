#!/usr/bin/env python3
"""Build and verify an auditable room-correction filter bundle offline.

The source recipe pins a committed declaration and/or legacy provenance plus
the selected exported files. This tool never starts REW. It verifies those
inputs, regenerates every declared BruteFIR rate in a staging directory,
calculates graph data, and writes only after all checks pass.

Run without --write first.  Existing runtime coefficients may be replaced only
with the additional --replace-runtime acknowledgement.
"""

from __future__ import annotations

import argparse
import datetime as dt
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
REQUIRED_ROLES = (
    "measurement_left", "measurement_right", "measurement_sum",
    "filter_left_txt", "filter_right_txt", "filter_left_wav", "filter_right_wav",
)
OPTIONAL_PREDICTION_ROLES = (
    "corrected_left_txt", "corrected_right_txt", "corrected_sum_txt",
)
DEFAULT_LIMITS = {
    "max_rms_magnitude_db": 0.02,
    "max_rms_phase_deg": 0.2,
    "above_100_hz_max_magnitude_db": 0.1,
    "above_100_hz_max_phase_deg": 1.0,
}


def artifact_roles(recipe: dict) -> tuple[str, ...]:
    artifacts = recipe["source"]["artifacts"]
    missing = [role for role in REQUIRED_ROLES if role not in artifacts]
    if missing:
        raise AuditError(f"recipe is missing required artifacts: {', '.join(missing)}")
    return REQUIRED_ROLES + tuple(role for role in OPTIONAL_PREDICTION_ROLES if role in artifacts)


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
    identity = {
        "schema": manifest["schema"],
        "geometry": manifest["geometry"],
        "variant": manifest["variant"],
        "design_id": manifest.get("design_id", manifest["variant"]),
        "source_repository": manifest["source"]["repository"],
        "source_commit": manifest["source"]["repository_head"],
        "source_release": manifest["source"].get("release", {}),
        "source_provenance_sha256": canonical_hash(manifest["source"]),
        "project_sha256": manifest["source"].get("project", {}).get("sha256", ""),
        "source_declaration_sha256": manifest["source"].get("declaration", {}).get("sha256", ""),
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
    # Added for new bundles without changing the identity of already deployed
    # manifests that predate the human-readable description field.
    if "description" in manifest:
        identity["description"] = manifest["description"]
    return identity


def run(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if proc.returncode:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise AuditError(f"command failed ({' '.join(args)}): {detail}")
    return proc.stdout.strip()


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


def verify_git_sources(source_root: Path, paths: list[str]) -> dict:
    top = Path(run(["git", "rev-parse", "--show-toplevel"], source_root)).resolve()
    if top != source_root.resolve():
        raise AuditError(f"source root is not the Git top level: {source_root} (top is {top})")
    for relative in paths:
        run(["git", "ls-files", "--error-unmatch", "--", relative], source_root)
    dirty = run(["git", "status", "--porcelain", "--", *paths], source_root)
    if dirty:
        raise AuditError(f"selected source files are modified or untracked:\n{dirty}")
    remote = run(["git", "config", "--get", "remote.origin.url"], source_root)
    return {
        "repository": remote,
        "head": run(["git", "rev-parse", "HEAD"], source_root),
    }


def git_blob(source_root: Path, relative: str) -> str:
    return run(["git", "rev-parse", f"HEAD:{relative}"], source_root)


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


def complex_from_db_phase(magnitude: np.ndarray, phase: np.ndarray) -> np.ndarray:
    return np.power(10.0, magnitude / 20.0) * np.exp(1j * np.radians(phase))


def db_phase(value: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return 20.0 * np.log10(np.maximum(np.abs(value), 1e-12)), np.degrees(np.angle(value))


def filter_spectrum(ir: np.ndarray, rate: int, delay_samples: int) -> tuple[np.ndarray, np.ndarray]:
    spectrum = np.fft.rfft(ir)
    freqs = np.fft.rfftfreq(ir.size, 1.0 / rate)
    spectrum *= np.exp(1j * 2.0 * np.pi * freqs * delay_samples / rate)
    return freqs, spectrum


def interpolate_complex(freqs: np.ndarray, source_freqs: np.ndarray,
                        values: np.ndarray) -> np.ndarray:
    real = np.interp(freqs, source_freqs, values.real)
    imag = np.interp(freqs, source_freqs, values.imag)
    return real + 1j * imag


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


def complex_response_metrics(expected_freqs: np.ndarray, expected: np.ndarray,
                             actual_freqs: np.ndarray, actual_mag: np.ndarray,
                             actual_phase: np.ndarray) -> dict:
    within = ((actual_freqs >= expected_freqs[0]) &
              (actual_freqs <= min(expected_freqs[-1], 20_000.0)))
    if np.count_nonzero(within) < 3:
        raise AuditError("independent corrected export has insufficient overlapping data")
    actual_freqs = actual_freqs[within]
    actual = complex_from_db_phase(actual_mag[within], actual_phase[within])
    predicted = interpolate_complex(actual_freqs, expected_freqs, expected)
    predicted_mag, predicted_phase = db_phase(predicted)
    actual_db, actual_degrees = db_phase(actual)
    mag_error = predicted_mag - actual_db
    phase_error = wrap_phase_deg(predicted_phase - actual_degrees)
    return {
        "rows": int(actual_freqs.size),
        "rms_magnitude_db": round(float(np.sqrt(np.mean(mag_error ** 2))), 6),
        "max_magnitude_db": round(float(np.max(np.abs(mag_error))), 6),
        "rms_phase_deg": round(float(np.sqrt(np.mean(phase_error ** 2))), 6),
        "max_phase_deg": round(float(np.max(np.abs(phase_error))), 6),
    }


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


def trace(identifier: str, label: str, color: str, group: str,
          values: np.ndarray, visible: bool = False, dash: list[int] | None = None) -> dict:
    magnitude, phase = db_phase(values)
    result = {
        "id": identifier,
        "label": label,
        "color": color,
        "group": group,
        "default_visible": visible,
        "magnitude_db": np.round(magnitude, 3).tolist(),
        "phase_deg": np.round(phase, 3).tolist(),
    }
    if dash:
        result["dash"] = dash
    return result


def trace_from_arrays(identifier: str, label: str, color: str, group: str,
                      magnitude: np.ndarray, phase: np.ndarray,
                      visible: bool = False, dash: list[int] | None = None) -> dict:
    result = {
        "id": identifier,
        "label": label,
        "color": color,
        "group": group,
        "default_visible": visible,
        "magnitude_db": np.round(magnitude, 3).tolist(),
        "phase_deg": np.round(wrap_phase_deg(phase), 3).tolist(),
    }
    if dash:
        result["dash"] = dash
    return result


def build_analysis(recipe: dict, source_paths: dict[str, Path]) -> tuple[dict, dict]:
    parsed = {role: parse_rew_txt(path) for role, path in source_paths.items()
              if role.endswith("_txt") or role.startswith("measurement_")}
    lh, freqs, lmag, lphase = parsed["measurement_left"]
    rh, rfreqs, rmag, rphase = parsed["measurement_right"]
    sh, sfreqs, smag, sphase = parsed["measurement_sum"]
    if not np.array_equal(freqs, rfreqs) or not np.array_equal(freqs, sfreqs):
        raise AuditError("L, R and L+R response exports must have the same frequency grid")
    sum_mode = recipe.get("prediction", {}).get("sum_mode", "independent")
    if sum_mode not in {"independent", "coherent_sum", "vector_average"}:
        raise AuditError(f"unknown prediction sum_mode: {sum_mode!r}")
    if sum_mode == "vector_average":
        aggregate_roles = ["measurement_sum"]
        if "corrected_sum_txt" in parsed:
            aggregate_roles.append("corrected_sum_txt")
        for role in aggregate_roles:
            headers = parsed[role][0]
            kind = (headers.get("source", "") + " " + headers.get("format", "")).lower()
            if "vector average" not in kind:
                raise AuditError(
                    f"sum_mode vector_average contradicts {role} TXT headers")
    required_notes = recipe.get("measurement", {}).get("required_note_fragments", [])
    timed_measurements = [("left", lh), ("right", rh)]
    if sum_mode == "independent":
        timed_measurements.append(("sum", sh))
    for role, headers in timed_measurements:
        if "acoustic timing reference" not in headers.get("format", "").lower():
            raise AuditError(f"{role} measurement has no acoustic timing reference")
        note = headers.get("note", "")
        missing_notes = [fragment for fragment in required_notes if fragment not in note]
        if missing_notes:
            raise AuditError(f"{role} measurement note is missing: {', '.join(missing_notes)}")

    source_rate = int(recipe["filter"]["sample_rate"])
    delay_samples = int(recipe["filter"]["delay_samples"])
    txt_to_wav_gain = recipe["filter"].get(
        "txt_to_wav_gain_db", {"left": 0.0, "right": 0.0})
    filter_values: dict[str, np.ndarray] = {}
    validation: dict[str, dict] = {}
    for channel in ("left", "right"):
        rate, impulse = load_filter_wav(source_paths[f"filter_{channel}_wav"])
        if rate != source_rate:
            raise AuditError(f"{channel} WAV rate {rate} != declared {source_rate}")
        fft_freqs, fft_values = filter_spectrum(impulse, rate, delay_samples)
        _, tfreqs, tmag, tphase = parsed[f"filter_{channel}_txt"]
        metrics = response_metrics(
            tfreqs, tmag, tphase, fft_freqs, fft_values,
            float(txt_to_wav_gain.get(channel, 0.0)))
        limits = recipe["filter"]["txt_wav_limits"]
        checks = (
            metrics["rms_magnitude_db"] <= limits["max_rms_magnitude_db"],
            metrics["rms_phase_deg"] <= limits["max_rms_phase_deg"],
            metrics["above_100_hz_max_magnitude_db"] <= limits["above_100_hz_max_magnitude_db"],
            metrics["above_100_hz_max_phase_deg"] <= limits["above_100_hz_max_phase_deg"],
        )
        metrics["passed"] = all(checks)
        if not metrics["passed"]:
            raise AuditError(f"{channel} filter TXT/WAV response check failed: {metrics}")
        validation[channel] = metrics
        filter_values[channel] = interpolate_complex(freqs, fft_freqs, fft_values)

    measured_l = complex_from_db_phase(lmag, lphase)
    measured_r = complex_from_db_phase(rmag, rphase)
    measured_sum = complex_from_db_phase(smag, sphase)
    sum_scale = 0.5 if sum_mode == "vector_average" else 1.0
    calculated_sum = (measured_l + measured_r) * sum_scale
    corrected_l = measured_l * filter_values["left"]
    corrected_r = measured_r * filter_values["right"]
    corrected_sum = (corrected_l + corrected_r) * sum_scale
    calc_sum_mag, calc_sum_phase = db_phase(calculated_sum)
    sum_mag_error = calc_sum_mag - smag
    sum_phase_error = wrap_phase_deg(calc_sum_phase - sphase)

    cutoff = freqs <= 20_000.0
    freqs = freqs[cutoff]
    prediction_validation: dict[str, dict] = {}
    if abs(float(txt_to_wav_gain.get("left", 0.0)) -
           float(txt_to_wav_gain.get("right", 0.0))) > 0.01 and \
            "corrected_sum_txt" in parsed:
        raise AuditError(
            "cannot gain-adjust a corrected aggregate export when left/right "
            "TXT-to-WAV gains differ by more than 0.01 dB")
    prediction_values = {
        "corrected_left_txt": (
            corrected_l[cutoff], float(txt_to_wav_gain.get("left", 0.0))),
        "corrected_right_txt": (
            corrected_r[cutoff], float(txt_to_wav_gain.get("right", 0.0))),
        "corrected_sum_txt": (
            corrected_sum[cutoff],
            (float(txt_to_wav_gain.get("left", 0.0)) +
             float(txt_to_wav_gain.get("right", 0.0))) / 2.0),
    }
    prediction_limits = recipe.get("prediction", {}).get("limits", {
        "max_rms_magnitude_db": 0.1,
        "max_rms_phase_deg": 1.0,
    })
    for role, (expected, source_gain_adjustment) in prediction_values.items():
        if role not in parsed:
            continue
        _, pfreqs, pmag, pphase = parsed[role]
        metrics = complex_response_metrics(
            freqs, expected, pfreqs, pmag + source_gain_adjustment, pphase)
        metrics["passed"] = (
            metrics["rms_magnitude_db"] <= prediction_limits["max_rms_magnitude_db"] and
            metrics["rms_phase_deg"] <= prediction_limits["max_rms_phase_deg"]
        )
        if not metrics["passed"]:
            raise AuditError(f"independent {role} cross-check failed: {metrics}")
        prediction_validation[role] = metrics

    sum_description = (
        "L/R vector average" if sum_mode == "vector_average" else "L+R")
    traces = [
        trace_from_arrays("original-left", "Original L", "#58a6ff", "Original",
                          lmag[cutoff], lphase[cutoff]),
        trace_from_arrays("original-right", "Original R", "#d29922", "Original",
                          rmag[cutoff], rphase[cutoff]),
        trace_from_arrays("original-sum-measured", f"Original {sum_description} (exported)", "#a371f7", "Original",
                          smag[cutoff], sphase[cutoff], visible=True, dash=[3, 3]),
        trace("original-sum-calculated", f"Original {sum_description} (calculated)", "#f0f6fc", "Original",
              calculated_sum[cutoff]),
        trace("filter-left", "Filter FLX", "#2f81f7", "Filter",
              filter_values["left"][cutoff]),
        trace("filter-right", "Filter FRX", "#e3b341", "Filter",
              filter_values["right"][cutoff]),
        trace("corrected-left", "Corrected L", "#3fb950", "Predicted",
              corrected_l[cutoff]),
        trace("corrected-right", "Corrected R", "#f85149", "Predicted",
              corrected_r[cutoff]),
        trace("corrected-sum", f"Corrected {sum_description}", "#39d353", "Predicted",
              corrected_sum[cutoff], visible=True),
    ]
    inputs = {role: recipe["source"]["artifacts"][role]["sha256"]
              for role in artifact_roles(recipe)}
    analysis = {
        "schema": 1,
        "geometry": recipe["geometry"],
        "variant": recipe["variant"],
        "design_id": recipe.get("design_id", recipe["variant"]),
        "description": recipe.get("description", recipe.get("design_id", recipe["variant"])),
        "frequencies_hz": np.round(freqs, 6).tolist(),
        "traces": traces,
        "calculation": {
            "formula": (
                "corrected_sum = (L * FLX + R * FRX) / 2"
                if sum_mode == "vector_average"
                else "corrected_sum = L * FLX + R * FRX"),
            "sum_mode": sum_mode,
            "txt_to_wav_gain_db": txt_to_wav_gain,
            "filter_delay_removed_samples": delay_samples,
            "runtime_attenuation_applied_by_web_endpoint": True,
            "original_sum_default": (
                f"exported {sum_description}; calculated {sum_description} is selectable"),
            "measured_sum_is_independent": sum_mode == "independent",
        },
        "inputs": inputs,
        "source_headers": {
            role: {key: parsed[role][0].get(key, "") for key in
                   ("rew_version", "source", "format", "dated",
                    "note", "measurement", "smoothing", "frequency step", "start frequency")}
            for role in ("measurement_left", "measurement_right", "measurement_sum")
        },
        "validation": {
            "filter_txt_to_wav": validation,
            "calculated_vs_exported_aggregate": {
                "rms_magnitude_db": round(float(np.sqrt(np.mean(sum_mag_error ** 2))), 4),
                "rms_wrapped_phase_deg": round(float(np.sqrt(np.mean(sum_phase_error ** 2))), 4),
                "note": f"Informational comparison using sum mode {sum_mode}."
            },
            "independent_corrected_exports": prediction_validation,
        }
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
        "original_path": item["path"],
        "bundle_path": item["bundle_path"],
        "sha256": sha256_file(source),
        "git_blob": git_blob(source_root, item["path"]),
        "bytes": source.stat().st_size,
        **{key: item[key] for key in ("measurement", "smoothing") if key in item},
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "This is the lower-level builder. Artifact role options such as "
            "--measurement-left belong to declare_filter_design.py and are stored "
            "in the recipe consumed here. For a new tagged design, normally run "
            "new_filter_design.py instead."),
    )
    required = parser.add_argument_group(
        "required inputs (artifact roles are contained in the recipe)")
    required.add_argument(
        "--recipe", type=Path, required=True,
        help="source recipe JSON produced by the declaration/tag workflow")
    required.add_argument(
        "--source-root", type=Path, required=True,
        help="clean Git checkout containing the pinned declaration and exports")
    parser.add_argument("--write", action="store_true", help="publish the verified bundle")
    parser.add_argument("--replace-runtime", action="store_true",
                        help="allow --write to replace runtime RAW bytes that differ")
    add_site_root_argument(parser)
    args = parser.parse_args()
    recipe_path = args.recipe.resolve()
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    design_id = recipe.get("design_id", recipe["variant"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", design_id):
        raise AuditError("design_id must contain only letters, numbers, dot, underscore and hyphen")
    source_root = args.source_root.resolve()
    site_root = resolve_site_root(args.site_root)
    geometry_root = site_root / "filters" / recipe["geometry"]
    if site_root != ROOT:
        progress("[SITE]", f"configs and filters resolve under {site_root}", "1;34")

    progress(
        "[DEPLOY 1/4]",
        f"Verify pinned source checkout and artifacts for "
        f"{recipe['geometry']}/{design_id}")
    roles = artifact_roles(recipe)
    source_items = recipe["source"]["artifacts"]
    source_recipe = recipe["source"]
    all_source_relatives = [source_items[role]["path"] for role in roles]
    if source_recipe.get("project"):
        all_source_relatives.append(source_recipe["project"]["path"])
    if source_recipe.get("declaration"):
        all_source_relatives.append(source_recipe["declaration"]["path"])
    if len(all_source_relatives) != len(set(all_source_relatives)):
        raise AuditError("one source path is assigned to multiple provenance roles")
    git_info = verify_git_sources(source_root, all_source_relatives)
    expected_repo = source_recipe["repository"]
    if git_info["repository"].removesuffix(".git") != expected_repo.removesuffix(".git"):
        raise AuditError(f"wrong source remote: {git_info['repository']}")
    expected_head = source_recipe.get("repository_head")
    if expected_head and git_info["head"] != expected_head:
        raise AuditError(
            f"source checkout HEAD {git_info['head']} differs from recipe {expected_head}")
    release = source_recipe.get("release")
    if release:
        if run(["git", "rev-parse", f"{source_recipe['source_ref']}^{{commit}}"], source_root) != git_info["head"]:
            raise AuditError("source release ref no longer resolves to the checked-out commit")
        if release.get("kind") == "annotated_tag":
            tag_ref = f"refs/tags/{release['name']}"
            actual_tag_object = run(["git", "rev-parse", f"{tag_ref}^{{tag}}"], source_root)
            if actual_tag_object != release.get("tag_object"):
                raise AuditError("annotated source tag object differs from the pinned recipe")
        if release.get("commit") != git_info["head"]:
            raise AuditError("source release commit differs from the checked-out commit")

    project_path: Path | None = None
    project_hash = ""
    if source_recipe.get("project"):
        project_item = source_recipe["project"]
        project_path = safe_source(source_root, project_item["path"])
        project_hash = sha256_file(project_path)
        if project_hash != project_item["sha256"]:
            raise AuditError("optional archived project hash differs from recipe")
        project_last_commit = run(
            ["git", "log", "-1", "--format=%H", "--", project_item["path"]], source_root)
        if project_last_commit != project_item["last_commit"]:
            raise AuditError("optional archived project last commit differs from recipe")

    declaration_record = None
    if source_recipe.get("declaration"):
        declaration_item = source_recipe["declaration"]
        declaration_path = safe_source(source_root, declaration_item["path"])
        if sha256_file(declaration_path) != declaration_item["sha256"]:
            raise AuditError("source declaration hash differs from recipe")
        if git_blob(source_root, declaration_item["path"]) != declaration_item["git_blob"]:
            raise AuditError("source declaration Git blob differs from recipe")
        declaration_last_commit = run(
            ["git", "log", "-1", "--format=%H", "--", declaration_item["path"]], source_root)
        if declaration_last_commit != declaration_item["last_commit"]:
            raise AuditError("source declaration last commit differs from recipe")
        declaration_record = {
            **declaration_item,
            "bytes": declaration_path.stat().st_size,
        }

    source_paths: dict[str, Path] = {}
    artifacts: dict[str, dict] = {}
    for role in roles:
        source_paths[role] = safe_source(source_root, source_items[role]["path"])
        artifacts[role] = artifact_record(source_root, recipe, role)
        if artifacts[role]["sha256"] != source_items[role]["sha256"]:
            raise AuditError(f"{role} hash differs from audited recipe")
    progress_ok(
        f"{len(artifacts)} source artifacts match commit {git_info['head'][:12]}")

    progress(
        "[DEPLOY 2/4]",
        "Calculate graph traces and validate TXT/WAV plus corrected exports")
    analysis, response_validation = build_analysis(recipe, source_paths)
    analysis_bytes = (json.dumps(analysis, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    analysis_hash = hashlib.sha256(analysis_bytes).hexdigest()
    progress_ok(f"analysis JSON sha256 {analysis_hash[:12]}")

    with tempfile.TemporaryDirectory(prefix="omdrc-filter-deploy-") as temp_name:
        staging = Path(temp_name)
        progress(
            "[DEPLOY 3/4]",
            "Run SoX conversion for every declared rate/channel in private staging")
        runtime, staged_configs = generate_runtime(recipe, source_paths, staging, site_root)
        progress_ok(f"generated and checked {len(runtime)} complete runtime rate pairs")
        differing_runtime: list[str] = []
        for rate, item in runtime.items():
            for channel in ("left", "right"):
                relative = item["channels"][channel]["path"]
                live = geometry_root / relative
                staged = staging / relative
                if live.exists() and sha256_file(live) != sha256_file(staged):
                    differing_runtime.append(relative)
        differing_configs = [relative for relative, staged in staged_configs.items()
                             if (site_root / relative).exists() and
                             sha256_file(site_root / relative) != sha256_file(staged)]

        progress(
            "[DEPLOY 4/4]",
            "Bind runtime hashes, configs, analysis and source release into the bundle")
        source_manifest = {
            "repository": source_recipe["repository"],
            "repository_head": git_info["head"],
            "artifacts": artifacts,
            "traces": source_recipe.get("traces", {}),
            "lineage": source_recipe.get("lineage", []),
            "attestation": source_recipe.get("attestation", {}),
        }
        for key in ("source_ref", "release"):
            if key in source_recipe:
                source_manifest[key] = source_recipe[key]
        if declaration_record is not None:
            source_manifest["declaration"] = declaration_record
        if project_path is not None:
            source_manifest["project"] = {
                **source_recipe["project"],
                "bytes": project_path.stat().st_size,
                "git_blob": git_blob(source_root, source_recipe["project"]["path"]),
            }
        if source_recipe.get("rew_audit"):
            source_manifest["rew_audit"] = source_recipe["rew_audit"]
        identity = {
            "schema": 1,
            "geometry": recipe["geometry"],
            "variant": recipe["variant"],
            "design_id": design_id,
            "source_repository": source_manifest["repository"],
            "source_commit": source_manifest["repository_head"],
            "source_release": source_manifest.get("release", {}),
            "source_provenance_sha256": canonical_hash(source_manifest),
            "project_sha256": project_hash,
            "source_declaration_sha256": (
                declaration_record["sha256"] if declaration_record is not None else ""),
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
        if "description" in recipe:
            identity["description"] = recipe["description"]
        claims = [
            "selected sources are tracked and clean at the recorded Git commit",
            "filter TXT responses match the canonical WAV responses within declared limits",
            "all runtime RAWs reproduce from the canonical WAVs",
            "every BruteFIR config maps to the hashed RAW pair and has sufficient headroom",
            "graph inputs and calculations are content-hash bound to this manifest",
        ]
        if declaration_record is not None:
            claims.insert(1, "semantic source roles are bound by the committed, hashed declaration")
        if release and release.get("kind") == "annotated_tag":
            claims.insert(1, "source commit is anchored by the recorded annotated Git tag object")
        if project_path is not None:
            claims.append("optional design-project archive is recorded by path, commit and SHA-256")
        if source_recipe.get("rew_audit"):
            claims.append("historical REW trace audit evidence is retained as optional provenance")

        manifest = {
            "schema": 1,
            "bundle_id": canonical_hash(identity),
            "geometry": recipe["geometry"],
            "variant": recipe["variant"],
            "design_id": design_id,
            "verification": {
                "status": "verified",
                "audited_at": recipe["audited_at"],
                "claims": claims,
                "prediction": (
                    "deterministic complex multiplication; independent corrected export(s) passed"
                    if analysis["validation"]["independent_corrected_exports"] else
                    "deterministic complex multiplication; no independent corrected export supplied"
                ),
            },
            "source": source_manifest,
            "filter_validation": response_validation,
            "runtime": {"rates": runtime},
            "analysis": {
                "path": f"analysis/{design_id}.json",
                "sha256": analysis_hash,
                "bytes": len(analysis_bytes),
            },
        }
        if "description" in recipe:
            manifest["description"] = recipe["description"]

        if project_path is not None:
            print(f"PASS: optional source project {project_path.name} {project_hash}")
        if declaration_record is not None:
            print(f"PASS: source declaration {declaration_record['sha256']}")
        if release:
            print(f"PASS: source {release['kind']} {release['name']} -> {release['commit']}")
        print(f"PASS: {len(artifacts)} pinned source exports are tracked, clean and hash-identical")
        for channel, values in response_validation.items():
            print(f"PASS: {channel} TXT/WAV RMS {values['rms_magnitude_db']:.6f} dB, "
                  f"{values['rms_phase_deg']:.6f} deg")
        for rate, item in runtime.items():
            pair = item["channels"]
            print(f"PASS: {rate} Hz L={pair['left']['sha256'][:12]} R={pair['right']['sha256'][:12]} "
                  f"required={item['required_attenuation_db']:.1f} configured={item['attenuation_db']:.1f} dB")
        print(f"Bundle ID: {manifest['bundle_id']}")

        if not args.write:
            if differing_runtime:
                print("DRY RUN: runtime files that would change: " + ", ".join(differing_runtime))
            else:
                print("DRY RUN: all existing runtime RAWs are already byte-identical")
            if staged_configs:
                creating = [path for path in staged_configs if not (site_root / path).exists()]
                print("DRY RUN: configs to create: " + (", ".join(creating) if creating else "none"))
            if differing_configs:
                print("DRY RUN: configs that would change: " + ", ".join(differing_configs))
            print("No files written. Re-run with --write after reviewing this audit.")
            return 0

        if (differing_runtime or differing_configs) and not args.replace_runtime:
            raise AuditError("design files differ; inspect a dry run, then add --replace-runtime explicitly")

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
        print(f"WROTE: verified bundle under {geometry_root}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as error:
        print(f"FAIL: {error}", file=os.sys.stderr)
        raise SystemExit(1)
