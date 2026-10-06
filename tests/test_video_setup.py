"""Video setup changes persist safely and uploaded keys replace only valid files."""
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "video/webremote/src"
sys.path.insert(0, str(SOURCE))
SPEC = importlib.util.spec_from_file_location("video_remote_under_test", SOURCE / "app.py")
VIDEO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VIDEO)


class VideoSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.saved_roots = VIDEO.ROOTS
        self.addCleanup(lambda: setattr(VIDEO, "ROOTS", self.saved_roots))
        settings = mock.patch.object(VIDEO.media_settings, "settings_path",
                                     return_value=self.base / "settings/media-roots.json")
        settings.start()
        self.addCleanup(settings.stop)
        target = mock.patch.object(VIDEO.keydb_install, "target_path",
                                   return_value=self.base / "aacs/KEYDB.cfg")
        target.start()
        self.addCleanup(target.stop)
        self.client = VIDEO.app.test_client()

    def test_folder_picker_and_roots_save_update_the_active_whitelist(self):
        media = self.base / "media"
        media.mkdir()
        (media / "Movies").mkdir()
        (media / "private.txt").write_text("not listed")
        listing = self.client.get("/api/server-folders", query_string={"path": str(media)}).get_json()
        self.assertEqual([item["name"] for item in listing["folders"]], ["Movies"])
        chosen = str(media / "Movies")
        response = self.client.put("/api/media-roots", json={"roots": [chosen]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VIDEO.ROOTS, [chosen])
        self.assertEqual([item["path"] for item in self.client.get("/api/roots").get_json()["roots"]], [chosen])
        VIDEO.ROOTS = []
        VIDEO.load_config(None)
        self.assertEqual(VIDEO.ROOTS, [chosen])
        bad = self.client.put("/api/media-roots", json={"roots": ["/"]})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(VIDEO.ROOTS, [chosen])

    def test_keydb_upload_keeps_previous_file_and_rejects_bad_replacement(self):
        target = self.base / "aacs/KEYDB.cfg"
        target.parent.mkdir()
        target.write_bytes(b"previous title | previous key\n")
        response = self.client.post("/api/keydb", data={"file": (io.BytesIO(b"current title | current key\n"), "KEYDB.cfg")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(target.read_bytes(), b"current title | current key\n")
        self.assertEqual((target.parent / "KEYDB.cfg.previous").read_bytes(), b"previous title | previous key\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        bad = self.client.post("/api/keydb", data={"file": (io.BytesIO(b"<html>wrong file</html>"), "KEYDB.cfg")},
                               content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(target.read_bytes(), b"current title | current key\n")

    def test_keydb_zip_install_extracts_only_the_named_member(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("nested/KEYDB.cfg", "movie title | key material\n")
            zip_file.writestr("other.txt", "ignored")
        archive.seek(0)
        response = self.client.post("/api/keydb", data={"file": (archive, "keydb_eng.zip")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual((self.base / "aacs/KEYDB.cfg").read_text(), "movie title | key material\n")
        self.assertIsNone(response.get_json()["backup"])
