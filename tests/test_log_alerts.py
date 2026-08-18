#!/usr/bin/env python3
"""The Logs panel reads configured logs and reports what they say about Qobuz."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "omdrc_logs_app", ROOT / "omdrc-ctrl/src/app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)

# The line upmpdcli's Qobuz plugin prints when it has no OAuth token, and the
# one it prints on startup, just before the (silent on success) login.
FAILURE = "0$qobuz$: Qobuz login: oauth initialisation not done"
STARTUP = "CMDTALK: qobuz-app.py: 'Qobuz running'"


def _config(directory: Path, console: Path) -> Path:
    """The shipped commands.conf with its log paths moved into the sandbox."""
    text = (ROOT / "omdrc-ctrl/src/commands.conf.in").read_text(encoding="utf-8")
    text = text.replace("@OMDRC_REPO_DIR@", str(directory))
    text = text.replace("/tmp/upmpdcli-console.log", str(console))
    path = directory / "commands.conf"
    path.write_text(text, encoding="utf-8")
    return path


def _alerts(console_text: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        console = root / "console.log"
        console.write_text(console_text, encoding="utf-8")
        APP.load_config(str(_config(root, console)))
        response = APP.app.test_client().get("/logs/alerts")
    return response.get_json()["alerts"]


class LogAlertTest(unittest.TestCase):
    def test_the_qobuz_oauth_line_raises_a_warning_with_what_to_do(self):
        alerts = _alerts(f"{STARTUP}\n{FAILURE}\n")
        self.assertEqual([(a["id"], a["severity"]) for a in alerts],
                         [("qobuz_oauth", "warn")])
        self.assertEqual(alerts[0]["line"], FAILURE)
        self.assertEqual(alerts[0]["source"], "upmpdcli-console")
        self.assertIn("qobuz-init-oauth.py", alerts[0]["hint"])

    def test_a_reworded_oauth_failure_still_matches(self):
        for line in ("0$qobuz$: oauth not done",
                     "qobuz: oauth initialization missing",
                     "0$qobuz$: /user/login returns None",
                     "PlgWithSlave::maybeStartCmd: tried login but failed for qobuz"):
            with self.subTest(line=line):
                alerts = _alerts(f"{STARTUP}\n{line}\n")
                self.assertEqual([a["id"] for a in alerts], ["qobuz_oauth"])

    def test_a_startup_with_no_later_failure_reports_the_connection_as_good(self):
        alerts = _alerts(f"{STARTUP}\n{FAILURE}\n{STARTUP}\n")
        self.assertEqual([(a["id"], a["severity"]) for a in alerts],
                         [("qobuz_ok", "ok")])
        self.assertEqual(alerts[0]["message"], "Qobuz plugin connected")

    def test_a_failure_after_the_last_startup_wins(self):
        alerts = _alerts(f"{STARTUP}\n{STARTUP}\n{FAILURE}\n")
        self.assertEqual([a["id"] for a in alerts], ["qobuz_oauth"])

    def test_a_log_with_nothing_to_say_raises_nothing(self):
        self.assertEqual(_alerts("mpd_run_idle_mask returned 0\n"), [])

    def test_the_dismissal_key_changes_when_the_failure_recurs(self):
        once = _alerts(f"{STARTUP}\n{FAILURE}\n")[0]
        twice = _alerts(f"{STARTUP}\n{FAILURE}\n{FAILURE}\n")[0]
        self.assertEqual((once["count"], twice["count"]), (1, 2))
        self.assertNotEqual(once["key"], twice["key"])


class LogViewerTest(unittest.TestCase):
    def test_the_tail_marks_the_lines_that_tripped_a_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            console = root / "console.log"
            console.write_text(f"{STARTUP}\nnoise\n{FAILURE}\n", encoding="utf-8")
            APP.load_config(str(_config(root, console)))
            response = APP.app.test_client().get("/logs/tail?source=upmpdcli-console")
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["matches"], [2])
        self.assertFalse(data["truncated"])
        self.assertTrue(data["exists"])

    def test_a_byte_bounded_tail_drops_the_half_line_it_starts_on(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big.log"
            path.write_text("first line\nsecond line\nthird line\n", encoding="utf-8")
            text, truncated = APP._tail_text(str(path), 20)
        self.assertTrue(truncated)
        self.assertEqual(text, "third line\n")

    def test_a_source_that_does_not_exist_yet_reads_back_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            APP.load_config(str(_config(root, root / "console.log")))
            response = APP.app.test_client().get("/logs/tail?source=upmpdcli-console")
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["exists"])
        self.assertEqual(data["content"], "")

    def test_an_unknown_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            APP.load_config(str(_config(root, root / "console.log")))
            response = APP.app.test_client().get("/logs/tail?source=/etc/passwd")
        self.assertEqual(response.status_code, 404)


class LogConfigTest(unittest.TestCase):
    def test_alert_sections_are_rules_not_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            APP.load_config(str(_config(root, root / "console.log")))
            ids = [c["id"] for c in APP.COMMANDS]
        self.assertNotIn("logs", ids)
        self.assertFalse([i for i in ids if i.startswith("alert:")])
        self.assertEqual([r["id"] for r in APP.LOG_ALERTS],
                         ["qobuz_oauth", "qobuz_ok"])

    def test_a_config_without_a_logs_section_still_gets_the_renderer_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.conf"
            path.write_text("[reboot]\nwhat = Reboot\ngroup = system\n"
                            "type = WRITE\nbutton = Reboot\ncmd = true\n",
                            encoding="utf-8")
            APP.load_config(str(path))
        self.assertIn("upmpdcli-console", [s["id"] for s in APP.LOG_SOURCES])
        self.assertEqual([r["id"] for r in APP.LOG_ALERTS],
                         ["qobuz_oauth", "qobuz_ok"])

    def test_a_rule_without_a_pattern_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.conf"
            path.write_text("[alert:broken]\nmessage = nothing to match\n",
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                APP.load_config(str(path))


if __name__ == "__main__":
    unittest.main()
