#!/usr/bin/env python3
"""The Logs panel reads configured logs and reports what they say about Qobuz."""

import importlib.util
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import quote, unquote


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
# What a completed sign-in actually logs.  Every one of these names both "qobuz"
# and "oauth" while meaning the opposite of the failure above.
SIGNED_IN = [
    "CMDTALK: qobuz-app.py: 'Qobuz: trackuri: OAuth initialisation'",
    "CMDTALK: qobuz-app.py: 'OAuth: got auth_code: LcoxaT5O'",
    "0$qobuz$: session: init_oauth: auth_code LcoxaT5O",
]


def _config(directory: Path, console: Path, token: str = "") -> Path:
    """The shipped commands.conf with its log and token paths moved into the
    sandbox — the token file has to be sandboxed too, or the rules would be
    answering questions about the machine running the tests."""
    text = (ROOT / "omdrc-ctrl/src/commands.conf.in").read_text(encoding="utf-8")
    text = text.replace("@OMDRC_REPO_DIR@", str(directory))
    text = text.replace("/tmp/upmpdcli-console.log", str(console))
    text = text.replace("/tmp/qconnect2mpd.log", str(directory / "qconnect.log"))
    cache = directory / "qobuz-config"
    cache.write_text(token, encoding="utf-8")
    text = text.replace("cache_config =", f"cache_config = {cache}")
    path = directory / "commands.conf"
    path.write_text(text, encoding="utf-8")
    return path


def _alerts_with_token(console_text: str, token: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        console = root / "console.log"
        console.write_text(console_text, encoding="utf-8")
        APP.load_config(str(_config(root, console, token)))
        with mock.patch.object(APP, "_service_running", return_value=True):
            response = APP.app.test_client().get("/logs/alerts")
    return response.get_json()["alerts"]


def _alerts(console_text: str, running: bool = True) -> list[dict]:
    """Rules are scoped to a service, so the answer depends on whether that
    service runs — stubbed here, or the tests would be reporting on whatever
    the machine running them happens to have up."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        console = root / "console.log"
        console.write_text(console_text, encoding="utf-8")
        APP.load_config(str(_config(root, console)))
        with mock.patch.object(APP, "_service_running", return_value=running):
            response = APP.app.test_client().get("/logs/alerts")
    return response.get_json()["alerts"]


def _qconnect_alerts(log_text: str, running: bool = True) -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        console = root / "console.log"
        console.write_text("", encoding="utf-8")
        (root / "qconnect.log").write_text(log_text, encoding="utf-8")
        APP.load_config(str(_config(root, console)))
        service_state = lambda name: running if name == "qobuzconnect2mpd" else False
        with (mock.patch.object(APP, "_service_running", side_effect=service_state),
              mock.patch.object(APP, "_qconnect_oauth_active", return_value=False)):
            response = APP.app.test_client().get("/logs/alerts")
    return response.get_json()["alerts"]


class LogAlertTest(unittest.TestCase):
    def test_the_qobuz_oauth_line_raises_a_warning_with_what_to_do(self):
        alerts = _alerts(f"{STARTUP}\n{FAILURE}\n")
        self.assertEqual([(a["id"], a["severity"]) for a in alerts],
                         [("qobuz_oauth", "warn")])
        self.assertEqual(alerts[0]["line"], FAILURE)
        self.assertEqual(alerts[0]["source"], "upmpdcli-console")
        self.assertIn("Qobuz sign-in", alerts[0]["hint"])

    def test_a_reworded_oauth_failure_still_matches(self):
        for line in ("0$qobuz$: oauth not done",
                     "qobuz: oauth initialization missing",
                     "0$qobuz$: Qobuz login: OAuth initialisation NOT DONE"):
            with self.subTest(line=line):
                alerts = _alerts(f"{STARTUP}\n{line}\n")
                self.assertEqual([a["id"] for a in alerts], ["qobuz_oauth"])

    def test_a_reworded_login_refusal_still_matches(self):
        for line in ("0$qobuz$: /user/login returns None",
                     "PlgWithSlave::maybeStartCmd: tried login but failed for qobuz",
                     "0$qobuz$: Qobuz login failed"):
            with self.subTest(line=line):
                alerts = _alerts(f"{STARTUP}\n{line}\n")
                self.assertEqual([a["id"] for a in alerts], ["qobuz_login"])

    def test_a_startup_with_no_later_failure_reports_the_connection_as_good(self):
        alerts = _alerts(f"{STARTUP}\n{FAILURE}\n{STARTUP}\n")  # noqa: E501
        self.assertEqual([(a["id"], a["severity"]) for a in alerts],
                         [("qobuz_ok", "ok")])
        self.assertEqual(alerts[0]["message"], "upmpdcli: Qobuz plugin connected")

    def test_a_completed_sign_in_clears_the_warning(self):
        """The bug this guards: the success lines name Qobuz and OAuth, and a
        looser pattern matched them — leaving the warning up for a working
        login, with the very line that proved it worked quoted underneath."""
        alerts = _alerts("\n".join([STARTUP, FAILURE] + SIGNED_IN) + "\n")
        self.assertEqual([(a["id"], a["severity"]) for a in alerts],
                         [("qobuz_ok", "ok")])

    def test_no_sign_in_line_is_ever_read_as_a_failure(self):
        for line in SIGNED_IN:
            with self.subTest(line=line):
                self.assertEqual(_alerts(f"{STARTUP}\n{line}\n"),
                                 _alerts(f"{STARTUP}\n{line}\n"))
                ids = [a["id"] for a in _alerts(f"{FAILURE}\n{line}\n")]
                self.assertEqual(ids, ["qobuz_ok"])

    def test_a_refused_token_is_its_own_warning(self):
        alerts = _alerts(f"{STARTUP}\n0$qobuz$: /user/login returns None\n")
        self.assertEqual([(a["id"], a["severity"]) for a in alerts],
                         [("qobuz_login", "warn")])
        self.assertIn("sign in again", alerts[0]["hint"])

    def test_a_failure_after_the_last_startup_wins(self):
        alerts = _alerts(f"{STARTUP}\n{STARTUP}\n{FAILURE}\n")
        self.assertEqual([a["id"] for a in alerts], ["qobuz_oauth"])

    def test_a_stored_token_settles_the_warning_whatever_the_log_says(self):
        """A tail that still ends on the failure line, but the token file — the
        thing that line is *about* — has one."""
        stale = f"{STARTUP}\n{FAILURE}\n"
        self.assertEqual([a["id"] for a in _alerts_with_token(stale, "")],
                         ["qobuz_oauth"])
        self.assertEqual(
            _alerts_with_token(stale, "user_auth_token = abc\nuser_id = 42\n"), [])

    def test_a_refused_token_is_not_settled_by_the_token_file(self):
        refused = f"{STARTUP}\n0$qobuz$: /user/login returns None\n"
        alerts = _alerts_with_token(refused, "user_auth_token = abc\nuser_id = 42\n")
        self.assertEqual([a["id"] for a in alerts], ["qobuz_login"])

    def test_a_stopped_renderer_is_reported_but_demands_nothing(self):
        """The complaint this guards: upmpdcli's stale log nagged — and offered
        a renderer switch — while qobuzconnect2mpd was the one actually
        playing."""
        stale = f"{STARTUP}\n{FAILURE}\n"
        self.assertEqual([(a["id"], a["severity"]) for a in _alerts(stale)],
                         [("qobuz_oauth", "warn")])
        idle = _alerts(stale, running=False)
        self.assertEqual([(a["id"], a["severity"]) for a in idle],
                         [("qobuz_oauth", "info")])
        self.assertFalse(idle[0]["service_running"])
        self.assertEqual(idle[0]["service"], "upmpdcli")

    def test_a_stopped_renderer_reports_no_connection(self):
        """Nothing is connected while nothing is running."""
        self.assertEqual(_alerts(f"{FAILURE}\n{STARTUP}\n", running=False), [])

    def test_each_renderer_speaks_for_its_own_login(self):
        """upmpdcli's rules name upmpdcli; qobuzconnect2mpd's name its own —
        and each renderer's warning starts its own, separate OAuth flow."""
        for rule in APP.LOG_ALERTS:
            with self.subTest(rule=rule["id"]):
                self.assertIn(rule["service"], ("upmpdcli", "qobuzconnect2mpd"))
                if rule["service"] == "qobuzconnect2mpd":
                    self.assertFalse(rule.get("clears_file"))
        actions = {r["id"]: r.get("action") for r in APP.LOG_ALERTS}
        self.assertEqual(actions["qobuz_oauth"], "qobuz-oauth")
        self.assertEqual(actions["qconnect_auth"], "qconnect-oauth")

    def test_qconnect_missing_oauth_has_its_own_sign_in_action(self):
        alerts = _qconnect_alerts(
            "QcManager: not authenticated and no cached Qobuz token\n"
            "This service cannot stream until it is authenticated.\n")
        self.assertEqual([a["id"] for a in alerts], ["qconnect_auth"])
        self.assertEqual(alerts[0]["action"], "qconnect-oauth")

    def test_qconnect_boot_start_reports_the_plugin_connected(self):
        """MPD connected OK is emitted after the cached-token gate even at the
        default error log level, so it is the reliable restored-at-boot signal."""
        alerts = _qconnect_alerts("qconnect2mpd: MPD connected OK (localhost:6600)\n")
        self.assertEqual([(a["id"], a["severity"]) for a in alerts],
                         [("qconnect_ok", "ok")])
        self.assertEqual(alerts[0]["message"],
                         "qobuzconnect2mpd: Qobuz plugin connected")

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
                         ["qobuz_oauth", "qobuz_login", "qobuz_ok",
                          "qconnect_auth", "qconnect_ok"])

    def test_a_config_without_a_logs_section_still_gets_the_renderer_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.conf"
            path.write_text("[reboot]\nwhat = Reboot\ngroup = system\n"
                            "type = WRITE\nbutton = Reboot\ncmd = true\n",
                            encoding="utf-8")
            APP.load_config(str(path))
        self.assertIn("upmpdcli-console", [s["id"] for s in APP.LOG_SOURCES])
        self.assertEqual([r["id"] for r in APP.LOG_ALERTS],
                         ["qobuz_oauth", "qobuz_login", "qobuz_ok",
                          "qconnect_auth", "qconnect_ok"])

    def test_a_rule_without_a_pattern_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.conf"
            path.write_text("[alert:broken]\nmessage = nothing to match\n",
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                APP.load_config(str(path))



class QobuzOauthTest(unittest.TestCase):
    """The panel drives upmpdcli's qobuz-init-oauth.py, which only prints the
    sign-in URLs — upmpdcli itself catches the redirect and stores the token."""

    SCRIPT_OUTPUT = (
        "This script must run on the same machine as upmpdcli\n"
        "- If upmpdcli and the script run on the same machine as the WEB browser, use:\n"
        "https://www.qobuz.com/signin/oauth?ext_app_id=798273057"
        "&redirect_url=http://localhost:49149/qobuz/oauth/\n"
        "- If the upmpdcli and the script run on a different machine, use:\n"
        "https://www.qobuz.com/signin/oauth?ext_app_id=798273057"
        "&redirect_url=http://172.19.180.123:49149/qobuz/oauth/\n"
    )

    def test_the_reachable_network_url_is_offered_first(self):
        urls = APP._oauth_candidates(self.SCRIPT_OUTPUT, "172.19.180.123")
        primary = next(u for u in urls if u["primary"])
        self.assertEqual(primary["host"], "172.19.180.123")
        self.assertEqual(primary["port"], 49149)
        self.assertFalse(any(u["primary"] for u in urls if u["local"]))

    def test_the_address_the_browser_used_is_added_and_preferred(self):
        urls = APP._oauth_candidates(self.SCRIPT_OUTPUT, "omdrc.local")
        self.assertTrue(urls[0]["primary"])
        self.assertEqual(urls[0]["host"], "omdrc.local")
        self.assertIn("redirect_url=http://omdrc.local:49149/qobuz/oauth/", urls[0]["url"])
        # the script's own two URLs are kept as fallbacks
        self.assertEqual([u["host"] for u in urls[1:]], ["localhost", "172.19.180.123"])

    def test_output_without_a_url_yields_no_candidates(self):
        self.assertEqual(APP._oauth_candidates("config file not found\n", "host"), [])

    def _status(self, directory: Path, cache_text: str | None) -> dict:
        cache = directory / "qobuz-config"
        if cache_text is not None:
            cache.write_text(cache_text, encoding="utf-8")
        config = directory / "commands.conf"
        config.write_text(
            f"[qobuz_oauth]\nscript = {directory}/init.py\n"
            f"cache_config = {cache}\ntimeout = 9\n", encoding="utf-8")
        APP.load_config(str(config))
        return APP.app.test_client().get("/qobuz/oauth/status").get_json()

    def test_status_reports_a_stored_token(self):
        with tempfile.TemporaryDirectory() as directory:
            data = self._status(Path(directory),
                                "user_auth_token = abc123\nuser_id = 42\n")
        self.assertTrue(data["token"])
        self.assertEqual(data["user_id"], "42")

    def test_status_reports_the_empty_token_file_of_a_fresh_install(self):
        with tempfile.TemporaryDirectory() as directory:
            data = self._status(Path(directory), "")
        self.assertFalse(data["token"])
        self.assertFalse(data["script_present"])

    def test_a_token_needs_both_the_id_and_the_auth_token(self):
        with tempfile.TemporaryDirectory() as directory:
            data = self._status(Path(directory), "user_auth_token = abc123\n")
        self.assertFalse(data["token"])

    def test_the_qobuz_oauth_settings_are_read_from_the_config(self):
        with tempfile.TemporaryDirectory() as directory:
            self._status(Path(directory), "")
        self.assertEqual(APP.QOBUZ_OAUTH_TIMEOUT, 9)
        self.assertTrue(APP.QOBUZ_OAUTH_SCRIPT.endswith("init.py"))

    def test_the_alert_rule_carries_its_ui_action(self):
        alerts = _alerts(f"{STARTUP}\n{FAILURE}\n")
        self.assertEqual(alerts[0]["action"], "qobuz-oauth")

    def test_restarting_an_unknown_renderer_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            APP.load_config(str(_config(root, root / "console.log")))
            response = APP.app.test_client().post(
                "/renderer/restart", json={"target": "rm -rf"})
        self.assertEqual(response.status_code, 400)


class QconnectOauthTest(unittest.TestCase):
    """qobuzconnect2mpd's -L process owns the callback and stays alive until
    the remote browser returns, unlike upmpdcli's short URL-printing helper."""

    def setUp(self):
        with APP._QCONNECT_OAUTH_LOCK:
            APP._QCONNECT_OAUTH_PROCESS = None
            APP._QCONNECT_OAUTH_SESSION = {
                "phase": "idle", "output": "", "error": "",
                "returncode": None, "started_at": None,
            }

    def test_encoded_redirect_and_ansi_output_are_rewritten_for_the_browser(self):
        redirect = "http://172.19.180.123:9093/oauth/callback/abc123"
        login = ("https://www.qobuz.com/signin/oauth?ext_app_id=798273057"
                 f"&redirect_url={quote(redirect, safe='')}")
        urls = APP._oauth_candidates(f"\x1b[1;33m{login}\x1b[0m\n", "omdrc.local")
        self.assertTrue(urls[0]["primary"])
        self.assertEqual(urls[0]["host"], "omdrc.local")
        self.assertIn("redirect_url=http://omdrc.local:9093/oauth/callback/abc123",
                      unquote(urls[0]["url"]))
        self.assertNotIn("\x1b", urls[0]["url"])

    def test_nonstandard_distinct_user_command_is_noninteractive(self):
        passwd = mock.Mock(pw_name="omdrcctrl")
        with (mock.patch.object(APP, "QCONNECT_OAUTH_USER", "qobuzconnect2mpd"),
              mock.patch.object(APP.pwd, "getpwuid", return_value=passwd)):
            command = APP._qconnect_oauth_command(
                "/usr/local/bin/qobuzconnect2mpd",
                "/usr/local/etc/qobuzconnect2mpd.conf")
        self.assertEqual(command[:4], ["sudo", "-n", "-u", "qobuzconnect2mpd"])
        self.assertEqual(command[-4:], ["/usr/local/bin/qobuzconnect2mpd", "-c",
                                        "/usr/local/etc/qobuzconnect2mpd.conf", "-L"])

    def test_successful_bootstrap_activates_the_normal_renderer(self):
        redirect = "http://172.19.180.123:9093/oauth/callback/abc123"
        login = ("https://www.qobuz.com/signin/oauth?ext_app_id=798273057"
                 f"&redirect_url={quote(redirect, safe='')}")

        class FakeProcess:
            def __init__(self):
                self.stdout = iter([login + "\n",
                                    "qobuzconnect2mpd: authenticated — token cached\n"])
                self.returncode = None

            def poll(self):
                return self.returncode

            def wait(self):
                self.returncode = 0
                return 0

            def terminate(self):
                self.returncode = -15

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "qobuzconnect2mpd"
            config = root / "qobuzconnect2mpd.conf"
            binary.write_text("fake", encoding="utf-8")
            config.write_text("qconnectstatedir = /tmp/fake\n", encoding="utf-8")
            settings = root / "commands.conf"
            settings.write_text(
                "[qconnect_oauth]\n"
                f"binary = {binary}\nconfig = {config}\nrun_user =\nurl_timeout = 5\n",
                encoding="utf-8")
            APP.load_config(str(settings))
            fake = FakeProcess()
            with (mock.patch.object(APP.subprocess, "Popen", return_value=fake) as popen,
                  mock.patch.object(APP, "_service_action"),
                  mock.patch.object(APP, "_service_running", return_value=False),
                  mock.patch.object(APP, "_activate_renderer",
                                    return_value=(True, "")) as activate):
                response = APP.app.test_client().post("/qconnect/oauth/start")
                data = response.get_json()
                deadline = time.monotonic() + 1
                while data["phase"] != "connected" and time.monotonic() < deadline:
                    time.sleep(0.01)
                    data = APP.app.test_client().get(
                        "/qconnect/oauth/status").get_json()

        self.assertTrue(data["connected"])
        self.assertEqual(data["phase"], "connected")
        self.assertEqual(data["urls"][0]["port"], 9093)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0],
                         [str(binary), "-c", str(config), "-L"])
        activate.assert_called_once_with("qobuzconnect2mpd")


if __name__ == "__main__":
    unittest.main()
