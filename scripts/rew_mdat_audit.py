#!/usr/bin/env python3
"""Audit selected REW project traces without opening audio hardware or a GUI.

Example:
  python3 scripts/rew_mdat_audit.py \
    --project ../DRC/DRC-120.blue/120-blue-with-inversion.mdat \
    --trace measurement_left=L.120.Blue \
    --trace measurement_right=R.120.Blue \
    --trace measurement_sum=L+R.120.Blue \
    --trace filter_left=FLX-trimmed \
    --trace filter_right=FRX-trimmed \
    --export measurement_left=../DRC/DRC-120.blue/txt/L.120.Blue.txt \
    --wav filter_left=../DRC/DRC-120.blue/FLX-trimmed-48k.wav

The report records the complete trace inventory and exact selected UUIDs. TXT
responses are compared numerically with REW's API values; final WAV impulses
are compared sample-by-sample with the selected project traces.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import numpy as np

from deploy_filter import AuditError, atomic_json, parse_rew_txt, sha256_file, wrap_phase_deg


LOCK_PATH = Path(os.environ.get("OMDRC_REW_AUDIT_LOCK", "/tmp/omdrc-rew-mdat-audit.lock"))


def assignments(values: list[str], option: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        role, separator, value = raw.partition("=")
        role, value = role.strip(), value.strip()
        if not separator or not role or not value:
            raise AuditError(f"{option} expects ROLE=VALUE: {raw}")
        if role in result:
            raise AuditError(f"duplicate {option} role: {role}")
        result[role] = value
    return result


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def rew_java_pids() -> set[int]:
    """REW JVMs currently alive, identified without matching this Python CLI."""
    result = subprocess.run(
        ["ps", "ax", "-o", "pid=", "-o", "args="], text=True,
        capture_output=True, check=True,
    )
    pids = set()
    for line in result.stdout.splitlines():
        pid_text, separator, command = line.strip().partition(" ")
        if separator and "RoomEQ_Wizard_obf.jar" in command:
            try:
                pids.add(int(pid_text))
            except ValueError:
                pass
    return pids


def api_listener_pids(port: int) -> set[int]:
    """PID owning the private loopback API port (FreeBSD/Linux fallbacks)."""
    if shutil.which("lsof"):
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True, capture_output=True,
        )
        if result.returncode == 0:
            return {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}
    if shutil.which("sockstat"):
        result = subprocess.run(["sockstat", "-4", "-l"], text=True, capture_output=True)
        pids = set()
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 6 and fields[2].isdigit() and fields[5].endswith(f":{port}"):
                pids.add(int(fields[2]))
        if pids:
            return pids
    if shutil.which("ss"):
        result = subprocess.run(["ss", "-ltnp"], text=True, capture_output=True)
        pids = set()
        for line in result.stdout.splitlines():
            if re.search(rf"(?:\]|:){port}\s", line):
                pids.update(int(value) for value in re.findall(r"pid=(\d+)", line))
        return pids
    return set()


def terminate_process_group(process: subprocess.Popen) -> None:
    """Terminate only the private session created for this audit."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as error:
        raise AuditError(
            f"REW process group {process.pid} did not terminate; manual cleanup is required") from error


@contextmanager
def exclusive_rew_audit():
    """Prevent concurrent auditors and refuse coexistence with GUI/other REW."""
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(LOCK_PATH, flags, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AuditError(
                f"another REW audit holds {LOCK_PATH}; concurrent audits are forbidden") from error
        existing = rew_java_pids()
        if existing:
            raise AuditError(
                "REW is already running (PID(s) " +
                ", ".join(str(pid) for pid in sorted(existing)) +
                "). Close it before a headless audit; the auditor never joins an existing instance.")
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class RewApi:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def request(self, path: str, value: object | None = None) -> object:
        data = None
        headers = {}
        if value is not None:
            data = json.dumps(value).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)


def start_rew(command: str, port: int, prefs: Path) -> tuple[subprocess.Popen, RewApi, str, set[int]]:
    args = [command, "-nogui", "-noaudio", "-api", "-host", "localhost",
            "-port", str(port), "-prefs", str(prefs)]
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    api = RewApi(f"http://127.0.0.1:{port}")
    deadline = time.monotonic() + 60.0
    last_error = ""
    while time.monotonic() < deadline:
        try:
            version = api.request("/version")
            message = str(version.get("message", "")) if isinstance(version, dict) else str(version)
            all_rew = rew_java_pids()
            children = api_listener_pids(port)
            if len(children) != 1 or children != all_rew:
                try:
                    api.request("/application/command", {"command": "Shutdown"})
                except Exception:
                    pass
                # An identified loopback-port owner is this API instance. Do
                # not touch any other REW PID that appeared concurrently.
                for pid in children & all_rew:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    terminate_process_group(process)
                raise AuditError(
                    "REW isolation failed: expected the private API listener to be the only JVM; "
                    f"listener={sorted(children)}, all={sorted(all_rew)}")
            return process, api, message, children
        except (OSError, urllib.error.URLError) as error:
            last_error = str(error)
            time.sleep(0.25)
    terminate_process_group(process)
    raise AuditError(f"REW API did not start within 60 seconds: {last_error}")


def stop_rew(process: subprocess.Popen, api: RewApi, rew_pids: set[int]) -> None:
    try:
        api.request("/application/command", {"command": "Shutdown"})
    except Exception:
        pass
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        alive = rew_java_pids() & rew_pids
        if not alive:
            break
        time.sleep(0.25)
    else:
        # These PIDs were resolved from this audit's unique loopback API port.
        # Never touch an unrelated REW instance that appeared later.
        for pid in sorted(rew_java_pids() & rew_pids):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and (rew_java_pids() & rew_pids):
            time.sleep(0.25)
        alive = rew_java_pids() & rew_pids
        if alive:
            raise AuditError(
                f"isolated REW JVM(s) did not stop: {sorted(alive)}; refusing another audit")
    if process.poll() is None:
        terminate_process_group(process)


def floats(value: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(value), dtype=">f4").astype(np.float64)


def response_frequencies(response: dict, count: int) -> np.ndarray:
    start = float(response["startFreq"])
    if "ppo" in response:
        return start * np.power(2.0, np.arange(count, dtype=np.float64) / float(response["ppo"]))
    if "freqStep" in response:
        return start + np.arange(count, dtype=np.float64) * float(response["freqStep"])
    raise AuditError("REW frequency response has neither ppo nor freqStep")


def compare_export(api: RewApi, trace_id: str, path: Path) -> dict:
    headers, text_freqs, text_mag, text_phase = parse_rew_txt(path)
    response = api.request(f"/measurements/{trace_id}/frequency-response")
    magnitude = floats(response["magnitude"])
    phase = floats(response["phase"])
    if magnitude.size != phase.size or magnitude.size != text_freqs.size:
        raise AuditError(
            f"TXT/API row count differs for {path}: {text_freqs.size} vs {magnitude.size}")
    frequencies = response_frequencies(response, magnitude.size)
    mag_error = magnitude - text_mag
    phase_error = wrap_phase_deg(phase - text_phase)
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "rows": int(text_freqs.size),
        "measurement": headers.get("measurement", ""),
        "smoothing": headers.get("smoothing", ""),
        "max_frequency_error_hz": round(float(np.max(np.abs(frequencies - text_freqs))), 7),
        "rms_magnitude_db": round(float(np.sqrt(np.mean(mag_error ** 2))), 6),
        "max_magnitude_db": round(float(np.max(np.abs(mag_error))), 6),
        "rms_phase_deg": round(float(np.sqrt(np.mean(phase_error ** 2))), 6),
        "max_phase_deg": round(float(np.max(np.abs(phase_error))), 6),
    }


def load_audio(path: Path) -> tuple[int, np.ndarray]:
    try:
        rate = int(subprocess.check_output(["soxi", "-r", str(path)], text=True).strip())
        result = subprocess.run(
            ["sox", str(path), "-t", "raw", "-e", "floating-point", "-b", "64",
             "-L", "-c", "1", "-"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        raise AuditError(f"cannot decode WAV with SoX: {path}: {error}") from error
    return rate, np.frombuffer(result.stdout, dtype="<f8")


def compare_wav(api: RewApi, trace_id: str, path: Path) -> dict:
    rate, wav = load_audio(path)
    response = api.request(f"/measurements/{trace_id}/impulse-response?normalised=false")
    impulse = floats(response["data"])
    if response.get("unit") == "percent":
        impulse *= 0.01
    api_rate = int(round(float(response["sampleRate"])))
    if rate != api_rate or wav.size != impulse.size:
        raise AuditError(
            f"WAV/API shape differs for {path}: {rate}/{wav.size} vs {api_rate}/{impulse.size}")
    error = wav - impulse
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "sample_rate": rate,
        "samples": int(wav.size),
        "max_abs_error": float(np.max(np.abs(error))),
        "rms_error": float(np.sqrt(np.mean(error ** 2))),
        "correlation": float(np.corrcoef(wav, impulse)[0, 1]),
    }


def audit_project(project: Path, trace_titles: dict[str, str],
                  exports: dict[str, Path], wavs: dict[str, Path],
                  rew_command: str) -> dict:
    project = project.resolve()
    if not project.is_file():
        raise AuditError(f"REW project not found: {project}")
    for role in set(exports) | set(wavs):
        if role not in trace_titles:
            raise AuditError(f"{role} has an export/WAV but no --trace declaration")
    original_hash = sha256_file(project)
    original_stat = project.stat()
    with exclusive_rew_audit():
      port = free_port()
      with tempfile.TemporaryDirectory(prefix="rew-audit-isolated-") as private_name:
        private_root = Path(private_name)
        private_project = private_root / "project-copy.mdat"
        private_prefs = private_root / "prefs"
        private_prefs.mkdir(mode=0o700)
        shutil.copyfile(project, private_project)
        os.chmod(private_project, 0o600)
        process, api, version, rew_pids = start_rew(
            rew_command, port, private_prefs)
        try:
            if rew_java_pids() != rew_pids:
                raise AuditError("REW process set changed before project load; aborting")
            api.request("/application/blocking", True)
            api.request("/measurements/command", {
                # Load a private disposable copy. REW never receives the path of
                # the original Git artifact, even if it auto-saves on shutdown.
                "command": "Load", "parameters": [str(private_project)],
            })
            errors = api.request("/application/errors")
            warnings = api.request("/application/warnings")
            inventory_raw = api.request("/measurements")
            if not isinstance(inventory_raw, dict):
                raise AuditError("unexpected REW measurement inventory")
            title_index: dict[str, list[tuple[str, dict]]] = {}
            inventory = []
            for trace_id, item in inventory_raw.items():
                title_index.setdefault(str(item.get("title", "")), []).append((str(trace_id), item))
                inventory.append({
                    "id": int(trace_id),
                    **{key: item.get(key) for key in
                       ("uuid", "title", "date", "notes", "sampleRate", "rewVersion",
                        "timingReference", "delay")},
                })
            selected: dict[str, dict] = {}
            selected_ids: dict[str, str] = {}
            for role, title in trace_titles.items():
                matches = title_index.get(title, [])
                if len(matches) != 1:
                    raise AuditError(
                        f"trace title must resolve uniquely: {role}={title!r} ({len(matches)} matches)")
                trace_id, item = matches[0]
                selected_ids[role] = trace_id
                selected[role] = {"id": int(trace_id), **item}
            export_results = {
                role: compare_export(api, selected_ids[role], path.resolve())
                for role, path in exports.items()
            }
            wav_results = {
                role: compare_wav(api, selected_ids[role], path.resolve())
                for role, path in wavs.items()
            }
            for role, result in export_results.items():
                if result["measurement"] != selected[role]["title"]:
                    raise AuditError(
                        f"TXT Measurement header differs for {role}: {result['measurement']!r}")
            if rew_java_pids() != rew_pids:
                raise AuditError(
                    "REW process set changed during audit; refusing potentially concurrent result")
            return {
                "schema": 1,
                "project": {
                    "path": str(project),
                    "sha256": original_hash,
                    "bytes": project.stat().st_size,
                    "loaded_from_private_copy": True,
                },
                "rew": {
                    "version": version,
                    "launch_flags": ["-nogui", "-noaudio", "-api", "-host", "localhost"],
                    "audio_disabled": True,
                    "gui_disabled": True,
                },
                "errors": errors,
                "warnings": warnings,
                "trace_count": len(inventory),
                "selected_traces": selected,
                "exports": export_results,
                "wav_impulses": wav_results,
                "inventory": sorted(inventory, key=lambda item: item["id"]),
            }
        finally:
            stop_rew(process, api, rew_pids)
            current_stat = project.stat()
            if (sha256_file(project) != original_hash or
                    current_stat.st_size != original_stat.st_size or
                    current_stat.st_mtime_ns != original_stat.st_mtime_ns):
                raise AuditError(
                    "original REW project changed during isolated audit; refusing result")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--trace", action="append", default=[], metavar="ROLE=TITLE")
    parser.add_argument("--export", action="append", default=[], metavar="ROLE=TXT")
    parser.add_argument("--wav", action="append", default=[], metavar="ROLE=WAV")
    parser.add_argument("--rew-command", default=os.environ.get("REW_COMMAND", "roomeqwizard"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    traces = assignments(args.trace, "--trace")
    if not traces:
        parser.error("at least one --trace ROLE=TITLE is required")
    exports = {role: Path(path) for role, path in assignments(args.export, "--export").items()}
    wavs = {role: Path(path) for role, path in assignments(args.wav, "--wav").items()}
    report = audit_project(args.project, traces, exports, wavs, args.rew_command)
    if args.output:
        atomic_json(report, args.output.resolve())
        print(f"WROTE: {args.output.resolve()}")
    print(f"PASS: {report['trace_count']} traces in {Path(report['project']['path']).name}")
    for role, item in report["selected_traces"].items():
        print(f"PASS: {role} -> {item['title']} [{item['uuid']}]")
    for role, item in report["exports"].items():
        print(f"PASS: {role} TXT/API RMS {item['rms_magnitude_db']:.6f} dB, "
              f"{item['rms_phase_deg']:.6f} deg")
    for role, item in report["wav_impulses"].items():
        print(f"PASS: {role} WAV/API max={item['max_abs_error']:.3g}, corr={item['correlation']:.12f}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
