#!/usr/bin/env python3
"""Switching renderer must fail loudly.

Both renderers start through daemon(8), which forks and reports success before
the binary has done anything: one that dies immediately — a missing shared
library, a config it will not accept — used to look exactly like a successful
switch, with the toggle quietly flipping back and no way to see why.  The card
also always showed qobuzconnect2mpd's log, so the log that explained the
failure was not the one on screen.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

SPEC = importlib.util.spec_from_file_location("omdrc_switch_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)

DIED = ('ld-elf.so.1: Shared object "libnpupnp.so.13" not found, '
        'required by "libupnpp.so.17"')


class RendererLogSourceTest(unittest.TestCase):
    def test_each_renderer_gets_its_own_log(self):
        self.assertEqual(APP._renderer_log_source("qobuzconnect2mpd")["id"],
                         "qobuzconnect2mpd")
        # The console log first: a renderer that dies on startup says why on
        # the stderr its service script captures, not in its own log file.
        self.assertEqual(APP._renderer_log_source("upmpdcli")["id"],
                         "upmpdcli-console")

    def test_unknown_renderer_falls_back_to_the_historical_log(self):
        self.assertEqual(APP._renderer_log_source("nonesuch")["path"],
                         APP.QCONNECT_LOG_FILE)


class LogRouteTest(unittest.TestCase):
    def setUp(self):
        self.log = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        self.log.write(DIED + "\n")
        self.log.close()
        self.addCleanup(lambda: Path(self.log.name).unlink(missing_ok=True))
        self.sources = list(APP.LOG_SOURCES)
        APP.LOG_SOURCES = [{"id": "upmpdcli-console", "label": "upmpdcli (plugins)",
                            "path": self.log.name}]
        self.addCleanup(lambda: setattr(APP, "LOG_SOURCES", self.sources))

    def test_log_follows_the_requested_renderer(self):
        body = json.loads(APP.app.test_client()
                          .get("/qconnect/log?renderer=upmpdcli").data)
        self.assertTrue(body["ok"])
        self.assertIn("libnpupnp", body["content"])
        self.assertEqual(body["renderer"], "upmpdcli")
        self.assertEqual(body["path"], self.log.name)
        self.assertEqual(body["label"], "upmpdcli (plugins)")

    def test_unknown_renderer_argument_falls_back_to_the_running_one(self):
        with mock.patch.object(APP, "_service_running",
                               side_effect=lambda n: n == "upmpdcli"):
            body = json.loads(APP.app.test_client()
                              .get("/qconnect/log?renderer=bogus").data)
        self.assertEqual(body["renderer"], "upmpdcli")


class ActivateRendererTest(unittest.TestCase):
    def test_a_renderer_that_never_turns_up_is_a_failure(self):
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(APP, "_service_action", return_value=done), \
             mock.patch.object(APP, "_service_running", return_value=False), \
             mock.patch.object(APP, "_mpc_quiesce"), \
             mock.patch.object(APP, "_write_state_str") as remember, \
             mock.patch.object(APP, "RENDERER_START_TIMEOUT", 0.0):
            ok, error = APP._activate_renderer("upmpdcli")
        self.assertFalse(ok)
        self.assertIn("would not stay running", error)
        # A switch that did not happen must not arm the boot service with it.
        remember.assert_not_called()

    def test_a_renderer_that_only_flickers_up_is_a_failure(self):
        """The regression that reported a dead renderer as a switch that
        worked: a unit whose ExecStart names a binary that is not there is
        `active` for the few milliseconds before the service manager reaps it,
        and Restart= brings it back for another such flicker every few seconds.
        Sampling once caught one of those windows and called it a success."""
        done = mock.Mock(returncode=0, stdout="", stderr="")
        polls = {"upmpdcli": 0}
        def service_running(name):
            if name != "upmpdcli":
                return False
            polls["upmpdcli"] += 1
            return polls["upmpdcli"] == 1   # up once, dead from then on
        with mock.patch.object(APP, "_service_action", return_value=done), \
             mock.patch.object(APP, "_service_running", side_effect=service_running), \
             mock.patch.object(APP, "_mpc_quiesce"), \
             mock.patch.object(APP, "_write_state_str") as remember, \
             mock.patch.object(APP, "RENDERER_START_TIMEOUT", 0.5), \
             mock.patch.object(APP, "RENDERER_SETTLE", 0.5):
            ok, error = APP._activate_renderer("upmpdcli")
        self.assertFalse(ok)
        self.assertIn("would not stay running", error)
        remember.assert_not_called()

    def test_a_renderer_that_dies_late_in_startup_is_a_failure(self):
        """Not every failure is immediate: qobuzconnect2mpd connects to MPD and
        then to Qobuz before it knows whether it can run, and exits about four
        seconds in when it has no cached token.  It is genuinely up for all of
        that, so the settle window has to outlast the renderer's own verdict —
        a short one reported the dead renderer as a switch that worked."""
        done = mock.Mock(returncode=0, stdout="", stderr="")
        polls = {"upmpdcli": 0}
        def service_running(name):
            if name != "upmpdcli":
                return False
            polls["upmpdcli"] += 1
            return polls["upmpdcli"] <= 4     # up for a while, then gone
        with mock.patch.object(APP, "_service_action", return_value=done), \
             mock.patch.object(APP, "_service_running", side_effect=service_running), \
             mock.patch.object(APP, "_mpc_quiesce"), \
             mock.patch.object(APP, "_write_state_str") as remember, \
             mock.patch.object(APP, "RENDERER_START_TIMEOUT", 0.5), \
             mock.patch.object(APP, "RENDERER_SETTLE", 1.5):
            ok, error = APP._activate_renderer("upmpdcli")
        self.assertFalse(ok)
        self.assertIn("would not stay running", error)
        remember.assert_not_called()

    def test_a_renderer_that_comes_up_and_stays_up_succeeds(self):
        done = mock.Mock(returncode=0, stdout="", stderr="")
        running = {"upmpdcli": False}
        def service_running(name):
            if name == "upmpdcli":
                was, running["upmpdcli"] = running["upmpdcli"], True
                return was            # absent on the first poll, up on the next
            return False
        with mock.patch.object(APP, "_service_action", return_value=done), \
             mock.patch.object(APP, "_service_running", side_effect=service_running), \
             mock.patch.object(APP, "_mpc_quiesce"), \
             mock.patch.object(APP, "_write_state_str") as remember, \
             mock.patch.object(APP, "RENDERER_SETTLE", 0.5):
            ok, error = APP._activate_renderer("upmpdcli")
        self.assertTrue(ok, error)
        remember.assert_called_once()


class SwitchRouteTest(unittest.TestCase):
    FAILED = (False, "upmpdcli would not stay running")

    def _switch(self):
        return json.loads(APP.app.test_client().post(
            "/qconnect/switch", json={"target": "upmpdcli"}).data)

    def test_failure_carries_the_renderer_log_as_evidence(self):
        with mock.patch.object(APP, "_activate_renderer", return_value=self.FAILED), \
             mock.patch.object(APP, "_renderer_log_tail", return_value=DIED), \
             mock.patch.object(APP, "_service_failure_report", return_value=""), \
             mock.patch.object(APP, "_qconnect_oauth_active", return_value=False):
            body = self._switch()
        self.assertFalse(body["ok"])
        self.assertIn("would not stay running", body["error"])
        self.assertIn("libnpupnp", body["detail"])
        self.assertEqual(body["renderer"], "upmpdcli")
        # One source, so the card can name it under the text.
        self.assertTrue(body["log_path"])

    def test_a_start_that_fails_before_the_binary_runs_still_says_why(self):
        """An ExecStart naming a binary that is not there (upmpdcli moved from
        /usr/local/bin to /usr/bin when it came from the distro package) never
        reaches the renderer's own log — only the service manager saw it."""
        report = ("upmpdcli.service: Unable to locate executable "
                  "'/usr/local/bin/upmpdcli': No such file or directory")
        with mock.patch.object(APP, "_activate_renderer", return_value=self.FAILED), \
             mock.patch.object(APP, "_renderer_log_tail", return_value=""), \
             mock.patch.object(APP, "_service_failure_report", return_value=report), \
             mock.patch.object(APP, "_qconnect_oauth_active", return_value=False):
            body = self._switch()
        self.assertFalse(body["ok"])
        self.assertIn("Unable to locate executable", body["detail"])
        # Nothing came from a log, so the card must not claim one.
        self.assertEqual(body["log_path"], "")

    def test_both_sources_are_shown_and_neither_is_claimed_as_the_only_one(self):
        with mock.patch.object(APP, "_activate_renderer", return_value=self.FAILED), \
             mock.patch.object(APP, "_renderer_log_tail", return_value=DIED), \
             mock.patch.object(APP, "_service_failure_report",
                              return_value="status=203/EXEC"), \
             mock.patch.object(APP, "_qconnect_oauth_active", return_value=False):
            body = self._switch()
        self.assertIn("203/EXEC", body["detail"])
        self.assertIn("libnpupnp", body["detail"])
        self.assertEqual(body["log_path"], "")


class PanelMarkupTest(unittest.TestCase):
    def setUp(self):
        self.html = (SRC / "templates/index.html").read_text(encoding="utf-8")

    def test_the_failure_has_somewhere_to_stay(self):
        self.assertIn('id="renderer-error"', self.html)
        self.assertIn("showRendererError(", self.html)
        self.assertIn("clearRendererError()", self.html)

    def test_the_log_button_follows_the_renderer(self):
        self.assertIn("qcLogRenderer", self.html)
        self.assertIn("/qconnect/log${query}", self.html)


if __name__ == "__main__":
    unittest.main()
