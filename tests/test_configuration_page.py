#!/usr/bin/env python3
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from werkzeug.datastructures import FileStorage

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))
import configuration

SPEC = importlib.util.spec_from_file_location("omdrc_configuration_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


class CardIdentityTest(unittest.TestCase):
    def test_serial_is_added_only_for_duplicate_vid_pid(self):
        rows = configuration._disambiguate([
            {"vid": "0x1234", "pid": "0xabcd", "serial": "one"},
            {"vid": "0x1234", "pid": "0xabcd", "serial": "two"},
            {"vid": "0x9999", "pid": "0x0001", "serial": "irrelevant"},
        ])
        self.assertEqual(rows[0]["identity"], "0x1234:0xabcd:one")
        self.assertEqual(rows[1]["identity"], "0x1234:0xabcd:two")
        self.assertEqual(rows[2]["identity"], "0x9999:0x0001")

    def test_freebsd_status_exposes_dsp_mapping(self):
        output = "/dev/dsp1   dac      Example DAC (uaudio1, play/rec, 0x1234:0xabcd)\n"
        done = mock.Mock(stdout=output)
        with mock.patch.object(configuration.subprocess, "run", return_value=done):
            cards = configuration.freebsd_cards({})
        self.assertEqual(cards[0]["device"], "/dev/dsp1")
        self.assertEqual(cards[0]["identity"], "0x1234:0xabcd")


class UploadTest(unittest.TestCase):
    def manager_and_job(self, root: str):
        settings = configuration.Settings(state_root=Path(root))
        manager = configuration.ConfigurationManager(settings, lambda: {})
        return manager, manager._new_job("upload")

    def test_upload_keeps_only_basenames_inside_private_job(self):
        with tempfile.TemporaryDirectory() as name:
            manager, job = self.manager_and_job(name)
            files = [FileStorage(stream=io.BytesIO(b"left"),
                                 filename="120.blue.test.txts/L.txt")]
            mdat = FileStorage(stream=io.BytesIO(b"session"), filename="120.blue.test.mdat")
            directory = manager.save_uploads(job, files, mdat)
            self.assertEqual((directory / "L.txt").read_bytes(), b"left")
            self.assertEqual((directory / "120.blue.test.mdat").read_bytes(), b"session")

    def test_upload_rejects_mdat_from_another_session(self):
        with tempfile.TemporaryDirectory() as name:
            manager, job = self.manager_and_job(name)
            files = [FileStorage(stream=io.BytesIO(b"left"),
                                 filename="abc.txts/L.txt")]
            mdat = FileStorage(stream=io.BytesIO(b"session"), filename="def.mdat")
            with self.assertRaisesRegex(ValueError, "must be abc.mdat"):
                manager.save_uploads(job, files, mdat)

    def test_upload_rejects_multiple_export_folders(self):
        with tempfile.TemporaryDirectory() as name:
            manager, job = self.manager_and_job(name)
            files = [
                FileStorage(stream=io.BytesIO(b"left"), filename="abc.txts/L.txt"),
                FileStorage(stream=io.BytesIO(b"right"), filename="def.txts/R.txt"),
            ]
            mdat = FileStorage(stream=io.BytesIO(b"session"), filename="abc.mdat")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                manager.save_uploads(job, files, mdat)


class RoutesTest(unittest.TestCase):
    def setUp(self):
        self.client = APP.app.test_client()

    def test_page_renders_instructions_and_csrf_token(self):
        response = self.client.get("/configuration")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Export all measurements as text", response.data)
        self.assertIn(b"FLX-trimmed-48k.wav", response.data)

    def test_form_data_is_captured_before_inputs_are_disabled(self):
        text = (SRC / "templates/configuration.html").read_text()
        handler = text[text.index("q('install-form').onsubmit"):]
        self.assertLess(handler.index("new FormData(e.target)"),
                        handler.index("freeze(true)"))

    def test_browser_enforces_and_auto_pairs_matching_session(self):
        text = (SRC / "templates/configuration.html").read_text()
        self.assertIn("actual.toLowerCase()!==expected.toLowerCase()", text)
        self.assertIn("if(sibling)setMdatFile(sibling)", text)
        self.assertIn("data.append('mdat',selectedMdat,selectedMdat.name)", text)

    def test_mutation_requires_token(self):
        response = self.client.delete("/configuration/api/filters/room/design")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
