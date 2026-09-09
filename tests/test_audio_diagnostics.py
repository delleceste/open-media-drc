#!/usr/bin/env python3
"""FreeBSD uaudio long-session diagnostics and UI contracts."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))
import audio_diagnostics


DIAG = (
    "version=1 running={running} generation={generation} rate=44100 source=1 "
    "feedback_valid=1 feedback_rate=44101 feedback_raw=361276 "
    "feedback_q16=361276 feedback_q16_min=361270 feedback_q16_max=361280 "
    "feedback_age_ms=1 feedback_updates={updates} feedback_bad={bad} "
    "feedback_errors=0 feedback_stale={stale} play_short_transfers={short} "
    "play_short_bytes={short_bytes} play_errors=0"
)


class FakeSysctl:
    def __init__(self):
        self.controls = {
            "hw.usb.uaudio.feedback_mode": "1",
            "hw.usb.uaudio.prefer_feedback": "1",
        }
        self.diag = DIAG.format(
            running=1, generation=1, updates=10, bad=0, stale=0,
            short=0, short_bytes=0,
        )

    def __call__(self, argv, **_kwargs):
        args = list(argv)
        if args[:2] == [audio_diagnostics.FREEBSD_SUDO, "-n"]:
            args = args[2:]
        if args[:2] != ["/sbin/sysctl", "-n"]:
            assignment = args[-1]
            oid, value = assignment.split("=", 1)
            self.controls[oid] = value
            return subprocess.CompletedProcess(argv, 0, f"{oid}: {value}\n", "")
        values = {
            "dev.pcm.0.uaudio_diagnostics": self.diag,
            "dev.pcm.0.%parent": "uaudio0",
            "dev.pcm.0.%desc": "Test USB DAC",
            "dev.pcm.0.bitperfect": "1",
            "dev.pcm.0.play.vchans": "0",
            "dev.uaudio.0.%pnpinfo": "vendor=0x1234 product=0xabcd serial=TEST",
            "dev.uaudio.0.%location": "bus=0 hubaddr=1 port=2",
            **self.controls,
        }
        requested = args[2:]
        try:
            stdout = "\n".join(values[oid] for oid in requested) + "\n"
        except KeyError as error:
            return subprocess.CompletedProcess(argv, 1, "", f"unknown oid {error}")
        return subprocess.CompletedProcess(argv, 0, stdout, "")


class MonitorTest(unittest.TestCase):
    def test_anomaly_delta_is_persisted_across_a_long_session(self):
        fake = FakeSysctl()
        mono = [10.0]
        with tempfile.TemporaryDirectory() as directory:
            monitor = audio_diagnostics.AudioDiagnosticsMonitor(
                Path(directory), lambda: "0", runner=fake,
                system_name=lambda: "FreeBSD", mono_clock=lambda: mono[0],
                wall_clock=lambda: 1_700_000_000.0,
            )
            first = monitor.poll_once()
            self.assertTrue(first["ok"])
            self.assertEqual(first["controls"]["feedback_mode"], 1)
            self.assertEqual(first["audio_path"], {"bitperfect": 1, "play_vchans": 0})
            self.assertEqual(first["parent_info"],
                             "vendor=0x1234 product=0xabcd serial=TEST")

            fake.diag = DIAG.format(
                running=1, generation=1, updates=20, bad=1, stale=0,
                short=1, short_bytes=8,
            )
            mono[0] = 12.0
            second = monitor.poll_once()
            self.assertEqual(second["deltas"]["feedback_bad"], 1)
            self.assertEqual(second["deltas"]["play_short_bytes"], 8)
            history = monitor.history(10)
            self.assertEqual([entry["reason"] for entry in history],
                             ["attach", "anomaly"])

    def test_scheduler_hot_switches_but_source_policy_requires_idle(self):
        fake = FakeSysctl()
        with tempfile.TemporaryDirectory() as directory:
            monitor = audio_diagnostics.AudioDiagnosticsMonitor(
                Path(directory), lambda: "0", runner=fake,
                system_name=lambda: "FreeBSD",
            )
            state = monitor.set_control("feedback_mode", 0)
            self.assertEqual(state["controls"]["feedback_mode"], 0)

            with self.assertRaisesRegex(RuntimeError, "stop playback"):
                monitor.set_control("prefer_feedback", 0)
            fake.diag = DIAG.format(
                running=0, generation=1, updates=10, bad=0, stale=0,
                short=0, short_bytes=0,
            )
            state = monitor.set_control("prefer_feedback", 0)
            self.assertEqual(state["controls"]["prefer_feedback"], 0)

    def test_parser_ignores_future_non_numeric_fields(self):
        parsed = audio_diagnostics.parse_diagnostics("version=1 running=1 future=text")
        self.assertEqual(parsed, {"version": 1, "running": 1, "future": "text"})


SPEC = importlib.util.spec_from_file_location("omdrc_uaudio_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


class WebVisibilityTest(unittest.TestCase):
    def test_card_is_visible_on_freebsd(self):
        with mock.patch.object(APP.platform, "system", return_value="FreeBSD"):
            body = APP.app.test_client().get("/").get_data(as_text=True)
        self.assertIn('id="uaudio-card"', body)
        self.assertIn("Linux-style Q16.16", body)
        self.assertIn("const schedulerDisabled = uaudioControlBusy", body)
        self.assertIn("const sourceDisabled = running || uaudioControlBusy", body)
        self.assertIn("responseText = await response.text()", body)

    def test_card_is_invisible_on_linux(self):
        with mock.patch.object(APP.platform, "system", return_value="Linux"):
            body = APP.app.test_client().get("/").get_data(as_text=True)
        self.assertNotIn('id="uaudio-card"', body)
        self.assertIn("const UAUDIO_ENABLED = false", body)

    def test_control_route_rejects_missing_csrf(self):
        response = APP.app.test_client().post(
            "/audio/diagnostics/control",
            data=json.dumps({"name": "feedback_mode", "value": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
