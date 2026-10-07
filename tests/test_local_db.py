"""The Local database page's backend: the per-host music directory, the scan
status the script leaves, and the routes that use them."""
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "omdrc-ctrl", "src"))

import local_db                                    # noqa: E402
import qobuz_web                                   # noqa: E402
from flask import Flask                            # noqa: E402


class PerHostPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_host_keeps_its_own_path(self):
        local_db.set_path(self.state, "/srv/music", "bee")
        local_db.set_path(self.state, "/home/me/Music", "dal")
        self.assertEqual(local_db.configured_path(self.state, "bee"), "/srv/music")
        self.assertEqual(local_db.configured_path(self.state, "dal"), "/home/me/Music")
        self.assertEqual(local_db.configured_path(self.state, "other"), "")

    def test_empty_path_forgets_and_falls_back_to_mpd(self):
        with patch.object(local_db, "host", return_value="dal"):
            local_db.set_path(self.state, "/a")
            self.assertEqual(local_db.music_directory(self.state, lambda: "/mpd"), "/a")
            local_db.set_path(self.state, "")
            self.assertEqual(local_db.music_directory(self.state, lambda: "/mpd"), "/mpd")

    def test_validate_wants_an_existing_absolute_directory(self):
        self.assertEqual(local_db.validate("  "), "")
        self.assertEqual(local_db.validate(self.state), os.path.normpath(self.state))
        for bad in ("relative/dir", os.path.join(self.state, "missing"), "/x\0y"):
            with self.assertRaises(ValueError):
                local_db.validate(bad)

    def test_scan_status_reads_the_scripts_line(self):
        def write(text):
            with open(os.path.join(self.state, local_db.SCAN_STATUS_FILE), "w") as f:
                f.write(text)
        self.assertEqual(local_db.scan_status(self.state)["state"], "never")
        now = int(time.time())
        write(f"running {now}\n")
        self.assertEqual(local_db.scan_status(self.state), {"state": "running", "since": now})
        write(f"done {now} 7\n")
        self.assertEqual(local_db.scan_status(self.state), {"state": "done", "at": now, "calculated": 7})
        write(f"running {now - local_db.STALE_SCAN - 5}\n")
        self.assertEqual(local_db.scan_status(self.state)["state"], "interrupted")

    def test_count_folders_with_audio_and_reports(self):
        for sub, files in (("a", ["x.FLAC", "dr14.txt"]), ("b", ["y.mp3"]), ("c", ["notes.txt"])):
            os.makedirs(os.path.join(self.state, sub))
            for name in files:
                open(os.path.join(self.state, sub, name), "w").close()
        got = local_db._count(self.state)
        self.assertEqual((got["folders"], got["reports"]), (2, 1))


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.music = tempfile.TemporaryDirectory()
        app = Flask(__name__)
        qobuz_web._state_dir = lambda: self.tmp.name
        qobuz_web._music_directory = lambda: "/from/mpd"
        app.register_blueprint(qobuz_web.bp)
        self.client = app.test_client()

    def tearDown(self):
        self.tmp.cleanup()
        self.music.cleanup()
        qobuz_web._music_directory = lambda: None

    def test_config_round_trip_and_bad_path(self):
        bad = self.client.post("/qobuz/local/config", json={"path": "nope"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/qobuz/local/config", json={"path": self.music.name})
        self.assertTrue(ok.get_json()["ok"])
        status = self.client.get("/qobuz/local/status").get_json()
        self.assertEqual((status["path"], status["source"]), (os.path.normpath(self.music.name), "configured"))
        self.assertEqual(status["mpd_path"], "/from/mpd")
        self.client.post("/qobuz/local/config", json={"path": ""})
        status = self.client.get("/qobuz/local/status").get_json()
        self.assertEqual((status["path"], status["source"]), ("/from/mpd", "mpd"))

    def test_refresh_hands_the_script_this_hosts_path_and_status_file(self):
        self.client.post("/qobuz/local/config", json={"path": self.music.name})
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(self.client.post("/qobuz/local/refresh").get_json()["ok"])
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["OMDRC_MUSIC_DIRECTORY"], os.path.normpath(self.music.name))
        self.assertEqual(env["OMDRC_SCAN_STATUS"], os.path.join(self.tmp.name, local_db.SCAN_STATUS_FILE))

    def test_refresh_does_not_start_a_second_scan(self):
        with open(os.path.join(self.tmp.name, local_db.SCAN_STATUS_FILE), "w") as f:
            f.write(f"running {int(time.time())}\n")
        with patch("subprocess.run") as run:
            answer = self.client.post("/qobuz/local/refresh").get_json()
        run.assert_not_called()
        self.assertIn("already running", answer["message"])


if __name__ == "__main__":
    unittest.main()
