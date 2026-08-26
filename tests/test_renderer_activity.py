#!/usr/bin/env python3
"""The renderer card's activity feedback: what qobuzconnect2mpd is doing in the
gap between the phone pressing play and the first sound.

The daemon reports it in the status file as an `state=<phase>` line plus a ring
of timestamped entries; /qconnect/status turns that into `state` + `events`.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

SPEC = importlib.util.spec_from_file_location("omdrc_activity_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


def parse(text):
    return APP._parse_qconnect_status(text.splitlines())


class StatusParseTest(unittest.TestCase):
    def test_phase_and_ring_are_split_out(self):
        got = parse(
            "[playing] Artist - Title  [0:12 / 4:02]\n"
            "24 bit / 176.4 kHz / stereo\n"
            "state=LOADING SEGMENT\n"
            "11:24:03 queue received: 14 tracks, starting at item 0\n"
            "11:24:07 segment 7/52 (13%)\n"
        )
        self.assertEqual(got["line1"], "[playing] Artist - Title  [0:12 / 4:02]")
        self.assertEqual(got["line2"], "24 bit / 176.4 kHz / stereo")
        self.assertEqual(got["state"], "LOADING SEGMENT")
        self.assertEqual(got["events"], [
            "11:24:03 queue received: 14 tracks, starting at item 0",
            "11:24:07 segment 7/52 (13%)",
        ])
        # Legacy single-line consumers get the newest entry.
        self.assertEqual(got["line3"], "11:24:07 segment 7/52 (13%)")

    def test_bare_state_tag_is_not_a_track(self):
        # The controller replaced the queue: the daemon drops the title, and
        # naming the old track here would read as a stalled renderer.
        got = parse("[paused] \n\nstate=NEW PLAYLIST RECEIVED\n11:24:03 queue received: 3 tracks\n")
        self.assertEqual(got["line1"], "")
        self.assertEqual(got["state"], "NEW PLAYLIST RECEIVED")

    def test_older_daemon_without_activity_lines(self):
        got = parse("[playing] Artist - Title  [0:12 / 4:02]\n24 bit / 176.4 kHz / stereo\n")
        self.assertEqual(got["state"], "")
        self.assertEqual(got["events"], [])
        self.assertEqual(got["line3"], "")

    def test_empty_phase_line_means_nothing_in_progress(self):
        got = parse("[playing] Artist - Title  [0:12 / 4:02]\n\nstate=\n")
        self.assertEqual(got["state"], "")
        self.assertEqual(got["line2"], "")
        self.assertEqual(got["events"], [])


class StatusRouteTest(unittest.TestCase):
    def _get(self, contents):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(contents)
            path = f.name
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        APP.QCONNECT_STATUS_FILE = path
        client = APP.app.test_client()
        return json.loads(client.get("/qconnect/status").data)

    def test_route_reports_phase_and_events(self):
        body = self._get("[stopped] \n\nstate=RESOLVING STREAM\n11:24:04 resolving stream URL 1/14 (7%)\n")
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"], "RESOLVING STREAM")
        self.assertEqual(body["events"], ["11:24:04 resolving stream URL 1/14 (7%)"])

    def test_missing_status_file_is_reported_empty(self):
        APP.QCONNECT_STATUS_FILE = "/nonexistent/qconnect-status"
        body = json.loads(APP.app.test_client().get("/qconnect/status").data)
        self.assertFalse(body["ok"])
        self.assertEqual(body["events"], [])
        self.assertEqual(body["state"], "")


class PanelMarkupTest(unittest.TestCase):
    """The panel is one template; these keep the wiring from silently rotting."""

    def setUp(self):
        self.html = (SRC / "templates/index.html").read_text(encoding="utf-8")

    def test_activity_toggle_button_is_next_to_the_log_button(self):
        self.assertIn('id="btn-act-lines"', self.html)
        self.assertIn("toggleActivityLines()", self.html)

    def test_phase_takes_over_the_big_line(self):
        self.assertIn("QC_PHASE_PLAYING", self.html)
        self.assertIn("#qc-line1.phase", self.html)

    def test_ring_depth_is_remembered(self):
        self.assertIn("omdrcctrl.qc.activityLines", self.html)


if __name__ == "__main__":
    unittest.main()
