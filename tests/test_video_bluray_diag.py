"""Blu-ray diagnostic reports missing keys and newer database releases."""
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "video/webremote/src/lib/bluray_diag.py"
SPEC = importlib.util.spec_from_file_location("bluray_diag_under_test", SOURCE)
DIAG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAG)


class BluRayDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.now = datetime(2026, 10, 6, tzinfo=timezone.utc)

    def test_missing_keys_identify_the_expected_filename_and_path(self):
        report = DIAG.diagnose(True, "cd0", False, self.now)
        row = next(r for r in report["checks"] if r["label"] == "AACS keys")
        self.assertEqual(row["status"], "error")
        self.assertIn("aacs/KEYDB.cfg", row["detail"])
        self.assertEqual(report["status"], "needs attention")

    def test_newer_online_archive_warns_even_when_the_file_exists(self):
        file = Path(self.temp.name) / "aacs/KEYDB.cfg"
        file.parent.mkdir()
        file.write_text("sample | key database")
        old = datetime(2026, 6, 19, tzinfo=timezone.utc).timestamp()
        os.utime(file, (old, old))

        class Reply:
            headers = {"Last-Modified": "Tue, 06 Oct 2026 13:34:39 GMT"}
            def __enter__(self): return self
            def __exit__(self, *_): return False

        with mock.patch.object(DIAG.urllib.request, "urlopen", return_value=Reply()) as head:
            report = DIAG.diagnose(True, "cd0", True, self.now)
        row = next(r for r in report["checks"] if r["label"] == "AACS keys")
        self.assertEqual(row["status"], "warning")
        self.assertEqual(report["keydb"]["remote_updated"], "2026-10-06")
        self.assertIn("newer database", row["fix"].lower())
        self.assertEqual(head.call_args.args[0].get_method(), "HEAD")

    def test_linux_checks_its_device_without_freebsd_cache_requirements(self):
        with mock.patch.object(DIAG.platform, "system", return_value="Linux"):
            report = DIAG.diagnose(True, "sr0", False, self.now)
        labels = [row["label"] for row in report["checks"]]
        self.assertIn("Optical drive", labels)
        self.assertNotIn("gcache", labels)
        self.assertNotIn("kldload", labels)
        self.assertFalse(any(label.startswith("sudo ") for label in labels))
        self.assertTrue(any("/dev/sr0" in row["detail"] for row in report["checks"]))

    def test_service_home_comes_from_account_when_env_home_is_wrong(self):
        with mock.patch.dict(os.environ, {"HOME": "/"}, clear=True):
            with mock.patch.object(DIAG.pwd, "getpwuid", return_value=mock.Mock(pw_dir="/home/video-user")):
                report = DIAG.diagnose(True, "sr0", False, self.now)
        self.assertEqual(report["keydb"]["path"], "/home/video-user/.config/aacs/KEYDB.cfg")
