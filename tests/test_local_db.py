"""The Local database page's backend: the scan
status the script leaves, and the routes that use them."""
import os
import signal
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
        write(f"running {now} cue 2 11\n")
        self.assertEqual(local_db.scan_status(self.state),
                         {"state": "running", "since": now, "phase": "cue", "done": 2, "total": 11})
        write(f"done {now} 7 2\n")
        self.assertEqual(local_db.scan_status(self.state),
                         {"state": "done", "at": now, "calculated": 7, "failed": 2})
        write(f"done {now} 7\n")
        self.assertEqual(local_db.scan_status(self.state)["failed"], 0)
        write(f"running {now - local_db.STALE_SCAN - 5}\n")
        self.assertEqual(local_db.scan_status(self.state)["state"], "interrupted")
        write(f"stopped {now} 4 10\n")
        self.assertEqual(local_db.scan_status(self.state),
                         {"state": "stopped", "at": now, "done": 4, "total": 10})

    def test_stop_terminates_scan_tree_and_releases_rescan(self):
        with open(os.path.join(self.state, local_db.SCAN_STATUS_FILE), "w") as f:
            f.write(f"running {int(time.time()) - 60} 4 10\n")
        processes = [{"pid": 10, "parent": 1, "kind": "Scanner"},
                     {"pid": 11, "parent": 10, "kind": "DR meter"},
                     {"pid": 12, "parent": 11, "kind": "Decoder"}]
        with patch.object(local_db, "scan_activity", side_effect=[processes, [], [], [], []]), \
             patch.object(local_db.os, "kill") as kill:
            result = local_db.stop_scan(self.state, "/music")
        self.assertTrue(result["ok"])
        self.assertEqual(kill.call_args_list, [
            unittest.mock.call(10, signal.SIGSTOP),
            unittest.mock.call(12, signal.SIGTERM),
            unittest.mock.call(11, signal.SIGTERM),
            unittest.mock.call(10, signal.SIGTERM),
            unittest.mock.call(10, signal.SIGCONT),
        ])
        self.assertEqual(local_db.scan_status(self.state)["state"], "stopped")

    def test_activity_follows_only_the_current_scan_process_tree(self):
        output = ("PID PPID %CPU ELAPSED COMMAND\n"
                  "10 1 0.0 10:00 /bin/sh /usr/local/libexec/omdrc/scripts/omdrc-mpd-update-dr14.sh --calculate /music\n"
                  "11 10 4.2 02:00 python3 /usr/local/lib/omdrcctrl/drmeter.py --album /music/Album\n"
                  "12 11 23.0 00:45 ffmpeg -i /music/Album/track.flac -f f32le -\n"
                  "13 1 90.0 00:10 ffmpeg -i /unrelated.flac -\n")
        with patch.object(local_db.subprocess, "run") as run:
            run.return_value.stdout = output
            activity = local_db.scan_activity("/music")
            self.assertEqual([item["pid"] for item in activity], [10, 11, 12])
            self.assertEqual(activity[-1]["cpu"], "23.0")
            self.assertEqual(local_db.scan_activity("/other"), [])

    def test_status_shows_missing_process_during_a_running_scan(self):
        with open(os.path.join(self.state, local_db.SCAN_STATUS_FILE), "w") as f:
            f.write(f"running {int(time.time())} 100 297\n")
        with patch.object(local_db, "scan_activity", return_value=[]) as activity:
            result = local_db.status(self.state, lambda: "/missing")
        self.assertEqual(result["activity"], [])
        activity.assert_called_once_with("/missing")

    def test_log_tail(self):
        self.assertEqual(local_db.scan_log(self.state), [])
        with open(os.path.join(self.state, local_db.SCAN_LOG_FILE), "w") as f:
            f.write("".join(f"line {i}\n" for i in range(300)))
        got = local_db.scan_log(self.state)
        self.assertEqual((len(got), got[-1]), (local_db.LOG_LINES, "line 299"))

    def test_cue_log_keeps_each_album_result(self):
        with open(os.path.join(self.state, local_db.SCAN_LOG_FILE), "w") as f:
            f.write("[cue] Album A: split into 8 verified track FLACs\n"
                    "[cue] skipped Album B: unsupported CUE\n")
        self.assertEqual(local_db.scan_log(self.state),
                         ["[cue] Album A: split into 8 verified track FLACs",
                          "[cue] skipped Album B: unsupported CUE"])

    def test_the_log_shows_each_folders_last_line_by_name(self):
        with open(os.path.join(self.state, local_db.SCAN_LOG_FILE), "w") as f:
            f.write("Rescan started\n"
                    "[listing] 100 folders with audio so far, 3 without a report\n"
                    "[listing] 120 folders with audio, 4 without a report\n"
                    "[1/4] measuring Rock/Sparklehorse - 1998 - Good Morning Spider (FLAC)\n"
                    "[1/4] DR9  Rock/Sparklehorse - 1998 - Good Morning Spider (FLAC)\tSparklehorse\tGood Morning Spider\n"
                    "    skipped 03.flac: cannot probe 03.flac\n"
                    "[2/4] measuring Organ/Norrlands Orgel/CD2\n"
                    "[2/4] FAILED  Organ/Norrlands Orgel/CD2\tno audio file could be measured\n"
                    "[3/4] measuring Jazz/Some Artist/1959 - An Album\n")
        self.assertEqual(local_db.scan_log(self.state, "/music"), [
            "Rescan started",
            "[listing] 120 folders with audio, 4 without a report",
            "[1/4] DR9  Sparklehorse — Good Morning Spider",
            "    skipped 03.flac: cannot probe 03.flac",
            "[2/4] FAILED  Norrlands Orgel · CD2: no audio file could be measured",
            "[3/4] measuring Some Artist — An Album",
        ])

    def test_names_are_cut_to_a_readable_length(self):
        label = local_db.album_label("x/y", "A" * 60, "B" * 90)
        artist, album = label.split(" — ")
        self.assertEqual((len(artist), len(album)), (local_db.ARTIST_CHARS, local_db.ALBUM_CHARS))
        self.assertTrue(artist.endswith("…") and album.endswith("…"))
        self.assertEqual(local_db.album_label("Classica/!Luigi Nono - La fabbrica illuminata"),
                         "Luigi Nono — La fabbrica illuminata")

    def test_count_folders_with_audio_and_reports(self):
        for sub, files in (("a", ["x.FLAC", "dr14.txt"]), ("b", ["y.mp3"]), ("c", ["notes.txt"])):
            os.makedirs(os.path.join(self.state, sub))
            for name in files:
                open(os.path.join(self.state, sub, name), "w").close()
        got = local_db._count(self.state)
        self.assertEqual((got["folders"], got["reports"]), (2, 1))

    def test_a_finished_scan_makes_the_counts_fresh(self):
        os.makedirs(os.path.join(self.state, "a"))
        open(os.path.join(self.state, "a", "x.flac"), "w").close()
        local_db._counts[self.state] = {"at": time.time() - 10, "folders": 9, "reports": 0}
        self.addCleanup(local_db._counts.pop, self.state, None)
        self.assertEqual(local_db.counts(self.state)["folders"], 9)       # still recent
        # a scan done after the count: counted again, the old figure meanwhile
        with patch.object(local_db, "_count", return_value={"at": time.time(), "folders": 1, "reports": 1}), \
                patch.object(local_db.threading, "Thread") as thread:
            self.assertEqual(local_db.counts(self.state, since=time.time() - 5)["folders"], 9)
            thread.call_args.kwargs["target"]()
        self.assertEqual(local_db.counts(self.state)["reports"], 1)

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
        self.assertEqual(env["OMDRC_SPLIT_CUE"], "0")

    def test_cue_split_requires_tools_and_is_passed_to_the_scan(self):
        with patch.object(qobuz_web, "_dr14_tools", return_value={"cue_split_available": False}):
            self.assertEqual(self.client.post("/qobuz/local/refresh", json={"split_cue": True}).status_code, 400)
        with patch.object(qobuz_web, "_dr14_tools", return_value={"cue_split_available": True}), \
             patch("subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(self.client.post("/qobuz/local/refresh", json={"split_cue": True}).get_json()["ok"])
        self.assertEqual(run.call_args.kwargs["env"]["OMDRC_SPLIT_CUE"], "1")

    def test_refresh_counts_the_folders_again(self):
        qobuz_web._music_directory = lambda: self.music.name
        with patch("subprocess.run") as run, patch.object(local_db, "counts") as counts:
            run.return_value.returncode = 0
            self.client.post("/qobuz/local/refresh")
        counts.assert_called_once_with(self.music.name, fresh=True)

    def test_refresh_does_not_start_a_second_scan(self):
        with open(os.path.join(self.tmp.name, local_db.SCAN_STATUS_FILE), "w") as f:
            f.write(f"running {int(time.time())}\n")
        with patch("subprocess.run") as run:
            answer = self.client.post("/qobuz/local/refresh").get_json()
        run.assert_not_called()
        self.assertIn("already running", answer["message"])

    def test_stop_route_targets_the_configured_music_root(self):
        with patch.object(local_db, "stop_scan", return_value={"ok": True}) as stop:
            self.assertTrue(self.client.post("/qobuz/local/stop").get_json()["ok"])
        stop.assert_called_once_with(self.tmp.name, "/from/mpd")


if __name__ == "__main__":
    unittest.main()
