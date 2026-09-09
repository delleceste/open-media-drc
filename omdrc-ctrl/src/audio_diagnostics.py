"""Persistent FreeBSD uaudio diagnostics for the omdrcctrl web service.

The kernel counters are monotonic for one USB attachment, but disappear when
the device is unplugged or the machine reboots.  This monitor samples them at a
low rate and writes sparse JSONL records: stream transitions, anomaly deltas,
and one summary per minute.  It never logs every feedback packet.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import threading
import time
from typing import Callable


DIAGNOSTIC_COUNTERS = (
    "feedback_updates",
    "feedback_bad",
    "feedback_errors",
    "feedback_stale",
    "play_short_transfers",
    "play_short_bytes",
    "play_errors",
)

ANOMALY_COUNTERS = (
    "feedback_bad",
    "feedback_errors",
    "feedback_stale",
    "play_short_transfers",
    "play_short_bytes",
    "play_errors",
)

CONTROL_OIDS = {
    "feedback_mode": "hw.usb.uaudio.feedback_mode",
    "prefer_feedback": "hw.usb.uaudio.prefer_feedback",
}
FREEBSD_SUDO = "/usr/local/bin/sudo"


def parse_diagnostics(text: str) -> dict[str, int | str]:
    """Parse the kernel's stable ``key=value`` diagnostics ABI."""
    result: dict[str, int | str] = {}
    for field in text.split():
        if "=" not in field:
            continue
        key, value = field.split("=", 1)
        try:
            result[key] = int(value, 0)
        except ValueError:
            result[key] = value
    return result


class AudioDiagnosticsMonitor:
    """Poll, persist, and expose diagnostics for the DAC role."""

    def __init__(
        self,
        state_dir: Path,
        unit_getter: Callable[[], str],
        *,
        interval: float = 2.0,
        summary_interval: float = 60.0,
        system_name: Callable[[], str] = platform.system,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        wall_clock: Callable[[], float] = time.time,
        mono_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "audio-diagnostics.jsonl"
        self.unit_getter = unit_getter
        self.interval = max(0.25, float(interval))
        self.summary_interval = max(self.interval, float(summary_interval))
        self.system_name = system_name
        self.runner = runner
        self.wall_clock = wall_clock
        self.mono_clock = mono_clock
        self._lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: dict = {}
        self._previous: dict | None = None
        self._last_recorded = 0.0

    @staticmethod
    def _timestamp(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat()

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess:
        return self.runner(
            argv, capture_output=True, text=True, timeout=5,
        )

    def _sysctl(self, oid: str, *, optional: bool = False) -> str:
        result = self._run(["/sbin/sysctl", "-n", oid])
        value = result.stdout.strip()
        if result.returncode != 0:
            if optional:
                return ""
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or f"cannot read {oid}")
        return value

    def _sysctls(self, oids: list[str]) -> list[str]:
        """Read related OIDs in one process to keep the monitor audio-cheap."""
        result = self._run(["/sbin/sysctl", "-n", *oids])
        values = result.stdout.splitlines()
        if result.returncode != 0 or len(values) != len(oids):
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or "cannot read uaudio diagnostics")
        return [value.strip() for value in values]

    @staticmethod
    def _device_oid_prefix(nameunit: str) -> str:
        index = len(nameunit)
        while index > 0 and nameunit[index - 1].isdigit():
            index -= 1
        if index == len(nameunit):
            return ""
        return f"dev.{nameunit[:index]}.{nameunit[index:]}"

    @staticmethod
    def _int_or_text(value: str) -> int | str:
        try:
            return int(value, 0)
        except ValueError:
            return value

    def _snapshot(self) -> dict:
        if self.system_name() != "FreeBSD":
            return {
                "ok": False,
                "supported": False,
                "error": "FreeBSD uaudio diagnostics are not available on this OS",
            }

        unit = str(self.unit_getter())
        prefix = f"dev.pcm.{unit}"
        oids = [
            f"{prefix}.uaudio_diagnostics",
            f"{prefix}.%parent",
            f"{prefix}.%desc",
            "hw.usb.uaudio.feedback_mode",
            "hw.usb.uaudio.prefer_feedback",
            f"{prefix}.bitperfect",
            f"{prefix}.play.vchans",
        ]
        (
            diag_text,
            parent,
            description,
            feedback_mode,
            prefer_feedback,
            bitperfect,
            play_vchans,
        ) = self._sysctls(oids)
        diag = parse_diagnostics(diag_text)
        parent_prefix = self._device_oid_prefix(parent)
        parent_info = ""
        location = ""
        if parent_prefix:
            try:
                parent_info, location = self._sysctls([
                    f"{parent_prefix}.%pnpinfo", f"{parent_prefix}.%location",
                ])
            except RuntimeError:
                pass

        controls = {
            "feedback_mode": self._int_or_text(feedback_mode),
            "prefer_feedback": self._int_or_text(prefer_feedback),
        }
        audio_path = {
            "bitperfect": self._int_or_text(bitperfect),
            "play_vchans": self._int_or_text(play_vchans),
        }

        return {
            "ok": True,
            "supported": True,
            "collected_at": self._timestamp(self.wall_clock()),
            "pcm_unit": unit,
            "pcm": f"pcm{unit}",
            "description": description,
            "parent": parent,
            "parent_info": parent_info,
            "location": location,
            "controls": controls,
            "audio_path": audio_path,
            "diagnostics": diag,
        }

    @staticmethod
    def _same_counter_epoch(previous: dict, current: dict) -> bool:
        if previous.get("parent") != current.get("parent"):
            return False
        if previous.get("parent_info") != current.get("parent_info"):
            return False
        old = previous.get("diagnostics", {})
        new = current.get("diagnostics", {})
        return all(
            not isinstance(old.get(key), int)
            or not isinstance(new.get(key), int)
            or new[key] >= old[key]
            for key in DIAGNOSTIC_COUNTERS
        )

    @classmethod
    def _deltas(cls, previous: dict | None, current: dict) -> dict[str, int]:
        if not previous or not cls._same_counter_epoch(previous, current):
            return {}
        old = previous.get("diagnostics", {})
        new = current.get("diagnostics", {})
        return {
            key: new[key] - old[key]
            for key in DIAGNOSTIC_COUNTERS
            if isinstance(old.get(key), int)
            and isinstance(new.get(key), int)
            and new[key] != old[key]
        }

    def _append(self, record: dict) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > 5_000_000:
                os.replace(self.path, self.path.with_suffix(".jsonl.1"))
            with self.path.open("a", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
        except OSError:
            # Diagnostics must never take down playback or the web service.
            pass

    def poll_once(self, *, force_record: bool = False) -> dict:
        with self._poll_lock:
            try:
                current = self._snapshot()
            except Exception as error:
                current = {
                    "ok": False,
                    "supported": self.system_name() == "FreeBSD",
                    "collected_at": self._timestamp(self.wall_clock()),
                    "error": str(error),
                }

            now = self.mono_clock()
            with self._lock:
                previous = self._previous
                deltas = self._deltas(previous, current) if current.get("ok") else {}
                current["deltas"] = deltas
                current["log_path"] = str(self.path)
                self._latest = copy.deepcopy(current)

                reason = ""
                if force_record:
                    reason = "requested"
                elif current.get("ok") and not previous:
                    reason = "attach"
                elif current.get("ok") and previous and (
                    current.get("parent") != previous.get("parent")
                    or current.get("parent_info") != previous.get("parent_info")
                ):
                    reason = "attach"
                elif current.get("ok") and previous:
                    old_diag = previous.get("diagnostics", {})
                    new_diag = current.get("diagnostics", {})
                    if (old_diag.get("generation"), old_diag.get("running")) != (
                        new_diag.get("generation"), new_diag.get("running")
                    ):
                        reason = "stream"
                if current.get("ok") and any(deltas.get(key, 0) for key in ANOMALY_COUNTERS):
                    reason = "anomaly"
                elif not reason and now - self._last_recorded >= self.summary_interval:
                    reason = "summary" if current.get("ok") else "unavailable"

                if reason:
                    self._append({"reason": reason, "snapshot": current})
                    self._last_recorded = now
                self._previous = copy.deepcopy(current) if current.get("ok") else previous
                return copy.deepcopy(current)

    def state(self) -> dict:
        with self._lock:
            latest = copy.deepcopy(self._latest)
        return latest or self.poll_once(force_record=True)

    def history(self, limit: int = 30) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        try:
            with self.path.open(encoding="utf-8") as stream:
                lines = stream.readlines()[-limit:]
        except OSError:
            return []
        result = []
        for line in lines:
            try:
                result.append(json.loads(line))
            except (TypeError, ValueError):
                continue
        return result

    def mark(self, note: str = "") -> dict:
        snapshot = self.state()
        record = {
            "reason": "listener-marker",
            "note": str(note).strip()[:200],
            "marked_at": self._timestamp(self.wall_clock()),
            "snapshot": snapshot,
        }
        self._append(record)
        return record

    def set_control(self, name: str, value: int) -> dict:
        if self.system_name() != "FreeBSD":
            raise RuntimeError("FreeBSD only")
        if name not in CONTROL_OIDS or value not in (0, 1):
            raise ValueError("unsupported uaudio control")
        before = self._snapshot()
        if name == "prefer_feedback" and before.get("diagnostics", {}).get("running"):
            raise RuntimeError(
                "stop playback before changing the uaudio clock-source policy"
            )
        oid = CONTROL_OIDS[name]
        argv = ["/sbin/sysctl", f"{oid}={value}"]
        if os.geteuid() != 0:
            argv = [FREEBSD_SUDO, "-n", *argv]
        try:
            result = self._run(argv)
        except OSError as error:
            raise RuntimeError(
                f"cannot run {argv[0]}: {error.strerror or error}") from None
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or f"cannot set {oid}")
        actual = self._sysctl(oid)
        if actual != str(value):
            raise RuntimeError(f"{oid} read back {actual}, expected {value}")
        return self.poll_once(force_record=True)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="uaudio-diagnostics", daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join(timeout=max(1.0, self.interval * 2))

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)
