"""The Local database page's backend: the scan
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


class Scan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_status_reads_the_scripts_line(self):
        def write(text):
            with open(os.path.join(self.state, local_db.SCAN_STATUS_FILE), "w") as f:
                f.write(text)
        self.assertEqual(local_db.scan_status(self.state)["state"], "never")
        now = int(time.time())
        write(f"running {now}\n")
        self.assertEqual(local_db.scan_status(self.state), {"state": "running", "since": now})
        write(f"running {now} 3 10\n")
        self.assertEqual(local_db.scan_status(self.state),
                         {"state": "running", "since": now, "done": 3, "total": 10})
        write(f"done {now} 7 2\n")
        self.assertEqual(local_db.scan_status(self.state),
                         {"state": "done", "at": now, "calculated": 7, "failed": 2})
        write(f"done {now} 7\n")
        self.assertEqual(local_db.scan_status(self.state)["failed"], 0)
        write(f"running {now - local_db.STALE_SCAN - 5}\n")
        self.assertEqual(local_db.scan_status(self.state)["state"], "interrupted")

    def test_log_tail(self):
        self.assertEqual(local_db.scan_log(self.state), [])
        with open(os.path.join(self.state, local_db.SCAN_LOG_FILE), "w") as f:
            f.write("".join(f"line {i}\n" for i in range(300)))
        got = local_db.scan_log(self.state)
        self.assertEqual((len(got), got[-1]), (local_db.LOG_LINES, "line 299"))

    def test_count_folders_with_audio_and_reports(self):
        for sub, files in (("a", ["x.FLAC", "dr14.txt"]), ("b", ["y.mp3"]), ("c", ["notes.txt"])):
            os.makedirs(os.path.join(self.state, sub))
            for name in files:
                open(os.path.join(self.state, sub, name), "w").close()
        got = local_db._count(self.state)
        self.assertEqual((got["folders"], got["reports"]), (2, 1))

    def test_a_disk_linked_into_the_library_is_counted(self):
        disk = tempfile.TemporaryDirectory()
        self.addCleanup(disk.cleanup)
        os.makedirs(os.path.join(disk.name, "Album"))
        for name in ("x.flac", "dr14.txt"):
            open(os.path.join(disk.name, "Album", name), "w").close()
        os.symlink(disk.name, os.path.join(self.state, "USBHD2"))
        got = local_db._count(self.state)
        self.assertEqual((got["folders"], got["reports"]), (1, 1))


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

    def test_status_reports_the_mpd_music_directory(self):
        status = self.client.get("/qobuz/local/status").get_json()
        self.assertEqual((status["path"], status["exists"]), ("/from/mpd", False))
        self.assertEqual(self.client.post("/qobuz/local/config", json={"path": "/x"}).status_code, 404)

    def test_refresh_passes_only_the_status_file(self):
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(self.client.post("/qobuz/local/refresh").get_json()["ok"])
        env = run.call_args.kwargs["env"]
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
