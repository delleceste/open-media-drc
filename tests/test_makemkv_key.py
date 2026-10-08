"""MakeMKV beta key: page parsing, expiry bookkeeping, settings.conf edits, the API."""
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "video/webremote/src"
sys.path.insert(0, str(SOURCE))
SPEC = importlib.util.spec_from_file_location("video_remote_key_under_test", SOURCE / "app.py")
VIDEO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VIDEO)
KEY = VIDEO.makemkv_key

OLD_KEY = "T-" + "A" * 66
NEW_KEY = "T-" + "B" * 30 + "@x_" + "C" * 33

# The forum post's shape as of 2026-10: the key in a code box, then the sentence.
PAGE = """<div class="content">As stated on a main page all features of MakeMKV are free while
program is in beta. The current beta key is <div class="codebox"><p>Code: <a href="#">Select all</a></p>
<pre><code>{key}</code></pre></div> and is valid until end of {month}.<br>
Please check back for updated key on this page.</div>"""


class ParseTests(unittest.TestCase):
    def test_forum_post_gives_key_and_last_day_of_month(self):
        found = KEY.parse_page(PAGE.format(key=OLD_KEY, month="October 2026"))
        self.assertEqual(found["key"], OLD_KEY)
        self.assertEqual(found["expires"], "2026-10-31")
        self.assertEqual(found["expiry_text"], "valid until end of October 2026")

    def test_other_phrasings_of_the_date(self):
        cases = {"end of February 2028": "2028-02-29", "October 31, 2026": "2026-10-31",
                 "31st of Oct 2026": "2026-10-31", "the end of Sept 2026": "2026-09-30",
                 "2026-11-15": "2026-11-15", "November 2026": "2026-11-30"}
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(KEY.parse_expiry(phrase).isoformat(), expected)

    def test_unreadable_expiry_keeps_the_key_and_reports_the_phrase(self):
        found = KEY.parse_page(PAGE.format(key=OLD_KEY, month="the beta"))
        self.assertEqual(found["key"], OLD_KEY)
        self.assertIsNone(found["expires"])
        self.assertEqual(found["expiry_text"], "valid until end of the beta")

    def test_page_without_a_key_is_an_error(self):
        with self.assertRaises(KEY.KeyLookupError):
            KEY.parse_page("<html>Forum maintenance</html>")


class StateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.settings = self.base / "home/.MakeMKV/settings.conf"
        patcher = mock.patch.object(KEY, "settings_path", return_value=self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        linux = mock.patch.object(KEY, "supported", return_value=True)
        linux.start()
        self.addCleanup(linux.stop)
        mmbd = mock.patch.object(KEY, "libmmbd", return_value="/usr/lib/libmmbd.so.0")
        mmbd.start()
        self.addCleanup(mmbd.stop)
        uses = mock.patch.object(KEY, "players_use_libmmbd", return_value=True)
        uses.start()
        self.addCleanup(uses.stop)
        self.state = str(self.base / "cache/makemkv-key.json")

    def install(self, key):
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(f'app_DestinationDir = "/tmp"\napp_Key = "{key}"\nsdf_Stop = ""\n')

    def test_no_key_installed_offers_the_online_one(self):
        KEY.record(self.state, {"key": OLD_KEY, "expires": "2026-10-31", "expiry_text": "x"})
        status = KEY.status(self.state, today=date(2026, 10, 8))
        self.assertEqual(status["level"], "error")
        self.assertTrue(status["offer_update"])
        self.assertTrue(status["notify"])

    def test_installed_key_takes_its_expiry_from_the_recorded_lookup(self):
        self.install(OLD_KEY)
        KEY.record(self.state, {"key": OLD_KEY, "expires": "2026-10-31", "expiry_text": "x"})
        status = KEY.status(self.state, today=date(2026, 10, 8))
        self.assertEqual((status["level"], status["days_left"]), ("ok", 23))
        self.assertFalse(status["offer_update"])
        self.assertFalse(status["notify"])
        self.assertTrue(KEY.status(self.state, today=date(2026, 10, 24))["notify"])     # one week
        late = KEY.status(self.state, today=date(2026, 10, 30))                       # a day left
        self.assertEqual(late["level"], "warning")
        self.assertTrue(late["offer_update"])
        expired = KEY.status(self.state, today=date(2026, 11, 1))
        self.assertEqual(expired["level"], "error")
        self.assertIn("expired", expired["summary"])

    def test_a_later_key_online_is_an_update_and_shifts_the_expiry_once_applied(self):
        self.install(OLD_KEY)
        KEY.record(self.state, {"key": OLD_KEY, "expires": "2026-10-31", "expiry_text": "x"})
        KEY.record(self.state, {"key": NEW_KEY, "expires": "2026-12-31", "expiry_text": "y"})
        status = KEY.status(self.state, today=date(2026, 10, 8))
        self.assertTrue(status["update_available"])
        self.assertTrue(status["offer_update"])
        self.assertEqual(status["installed_expires"], "2026-10-31")   # the old key's date is kept
        KEY.apply(NEW_KEY)
        status = KEY.status(self.state, today=date(2026, 10, 8))
        self.assertFalse(status["update_available"])
        self.assertEqual(status["installed_expires"], "2026-12-31")

    def test_apply_replaces_only_app_key_and_keeps_a_backup(self):
        self.install(OLD_KEY)
        result = KEY.apply(NEW_KEY)
        text = self.settings.read_text()
        self.assertIn(f'app_Key = "{NEW_KEY}"', text)
        self.assertIn('app_DestinationDir = "/tmp"', text)
        self.assertIn('sdf_Stop = ""', text)
        self.assertNotIn(OLD_KEY, text)
        self.assertIn(OLD_KEY, Path(result["backup"]).read_text())
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            KEY.apply('T-short"\nmalicious = "1')

    def test_without_makemkv_the_key_is_reported_but_not_pushed(self):
        KEY.record(self.state, {"key": OLD_KEY, "expires": "2026-10-31", "expiry_text": "x"})
        with mock.patch.object(KEY, "libmmbd", return_value=None), \
                mock.patch.object(KEY, "players_use_libmmbd", return_value=False):
            status = KEY.status(self.state, today=date(2026, 10, 8))
        self.assertEqual(status["level"], "warning")
        self.assertFalse(status["offer_update"])
        self.assertFalse(status["notify"])

    def test_apply_creates_settings_when_missing(self):
        KEY.apply(NEW_KEY)
        self.assertEqual(self.settings.read_text(), f'app_Key = "{NEW_KEY}"\n')


class ApiTests(StateTests):
    def setUp(self):
        super().setUp()
        cache = mock.patch.object(VIDEO, "CACHE_DIR", str(self.base / "cache"))
        cache.start()
        self.addCleanup(cache.stop)
        no_probe = mock.patch.object(KEY, "registration_problem", return_value=None)
        no_probe.start()
        self.addCleanup(no_probe.stop)
        self.client = VIDEO.app.test_client()

    def test_check_records_without_installing_and_apply_installs(self):
        found = {"key": NEW_KEY, "expires": "2026-12-31", "expiry_text": "valid until end of December 2026"}
        with mock.patch.object(KEY, "fetch", return_value=found):
            checked = self.client.post("/api/makemkv-key", json={"op": "check"}).get_json()
            self.assertTrue(checked["ok"])
            self.assertEqual(checked["online_expires"], "2026-12-31")
            self.assertFalse(self.settings.exists())
            applied = self.client.post("/api/makemkv-key", json={"op": "apply"}).get_json()
        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["installed_expires"], "2026-12-31")
        self.assertIn(NEW_KEY, self.settings.read_text())
        stored = self.client.get("/api/makemkv-key").get_json()
        self.assertEqual(stored["installed_expires"], "2026-12-31")

    def test_failed_lookup_is_reported_and_remembered(self):
        with mock.patch.object(KEY, "fetch", side_effect=KEY.KeyLookupError("offline")):
            response = self.client.post("/api/makemkv-key", json={"op": "apply"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["last_error"]["message"], "offline")
        self.assertFalse(self.settings.exists())


class FreeBSDTests(unittest.TestCase):
    """FreeBSD decrypts with libaacs + KEYDB.cfg: no key lookup, no install, no banner."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        for target, value in ((VIDEO, ("CACHE_DIR", str(self.base / "cache"))),):
            patcher = mock.patch.object(target, *value)
            patcher.start()
            self.addCleanup(patcher.stop)
        system = mock.patch.object(KEY.platform, "system", return_value="FreeBSD")
        system.start()
        self.addCleanup(system.stop)
        self.client = VIDEO.app.test_client()

    def test_key_operations_are_refused_and_nothing_is_fetched(self):
        with mock.patch.object(KEY, "fetch") as fetch, mock.patch.object(KEY, "apply") as apply:
            for op in ("check", "apply"):
                response = self.client.post("/api/makemkv-key", json={"op": op})
                self.assertEqual(response.status_code, 400)
                self.assertIn("KEYDB.cfg", response.get_json()["error"])
        fetch.assert_not_called()
        apply.assert_not_called()
        status = self.client.get("/api/makemkv-key").get_json()
        self.assertFalse(status["supported"])
        self.assertFalse(status["notify"])

    def test_even_a_present_libmmbd_is_not_used(self):
        with mock.patch.object(KEY, "libmmbd", return_value="/usr/local/lib/libmmbd.so.0"):
            self.assertFalse(KEY.players_use_libmmbd())

    def test_makemkv_env_is_linux_only(self):
        env = (ROOT / "video/lib/makemkv-env.sh").read_text()
        self.assertIn('if [ "$(uname)" = "Linux" ]', env)


class ShellBranchTests(unittest.TestCase):
    """Source the real launcher libraries under a faked uname: the Linux-only
    mpv options never reach FreeBSD, and FreeBSD never switches to libmmbd."""

    def source(self, system):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "uname"
            fake.write_text(f"#!/bin/sh\necho {system}\n")
            fake.chmod(0o755)
            script = ('HERE="$1"; DRC_SKIP_RESAMP=1; DRC_VIDEO_DELAY=0.5; '
                      'unset LIBAACS_PATH LIBBDPLUS_PATH; . "$HERE/drc-audio.sh" >/dev/null; '
                      'printf "%s|%s|%s" "$AO" "$AO_OPTS" "${LIBAACS_PATH:-unset}"')
            env = {"PATH": f"{temp}:/usr/bin:/bin:/usr/local/bin"}
            out = subprocess.run(["sh", "-c", script, "sh", str(ROOT / "video/lib")],
                                 capture_output=True, text=True, env=env, timeout=30)
        return out.stdout.split("|")

    def test_freebsd_gets_oss_no_linux_options_and_keydb(self):
        ao, options, aacs = self.source("FreeBSD")
        self.assertEqual((ao, options, aacs), ("oss", "", "unset"))

    def test_linux_gets_alsa_and_the_loopback_options(self):
        ao, options, _ = self.source("Linux")
        self.assertEqual(ao, "alsa")
        self.assertIn("--alsa-periods=8", options)
        self.assertIn("--autosync=30", options)


class WiringTests(unittest.TestCase):
    def test_kodi_button_runs_the_libmmbd_wrapper(self):
        conf = (ROOT / "omdrc-ctrl/src/commands.conf.in").read_text()
        self.assertIn("cmd    = @OMDRC_KODI@", conf)
        self.assertIn("@OMDRC_KODI@", (ROOT / "omdrc-ctrl/CMakeLists.txt").read_text())
        wrapper = (ROOT / "video/kodi.sh").read_text()
        self.assertIn('. "$HERE/makemkv-env.sh"', wrapper)
        self.assertIn("../kodi.sh", (ROOT / "video/webremote/CMakeLists.txt").read_text())


if __name__ == "__main__":
    unittest.main()
