"""The opt-in CUE conversion must preserve decoded audio before deletion."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "omdrc-cue-split.py"
SPEC = importlib.util.spec_from_file_location("omdrc_cue_split", SCRIPT)
SPLIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SPLIT)


@unittest.skipUnless(all(shutil.which(command) for command in
                         ("cuebreakpoints", "cueprint", "cuetag.sh", "shnsplit", "shnhash", "flac", "metaflac", "ffmpeg")),
                     "CUE conversion tools are not installed")
class CueSplitTest(unittest.TestCase):
    def test_split_creates_tagged_tracks_and_removes_verified_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rate = 44100
            samples = (12000 * np.sin(2 * np.pi * 440 * np.arange(rate * 8) / rate)).astype("<i2")
            with wave.open(str(folder / "album.wav"), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(rate)
                output.writeframes(np.column_stack((samples, samples)).astype("<i2").tobytes())
            subprocess.run(["flac", "-s", "-o", str(folder / "album.flac"), str(folder / "album.wav")], check=True)
            (folder / "album.wav").unlink()
            (folder / "album.cue").write_text(
                'PERFORMER "Test Artist"\nTITLE "Test Album"\nFILE "album.flac" WAVE\n'
                '  TRACK 01 AUDIO\n    TITLE "First"\n    INDEX 01 00:00:00\n'
                '  TRACK 02 AUDIO\n    TITLE "Second"\n    INDEX 01 00:04:00\n')
            (folder / "dr14.txt").write_text("old report\n")
            with patch.object(SPLIT, "fingerprint", side_effect=["0" * 32, "1" * 32]):
                with self.assertRaisesRegex(SPLIT.SplitError, "audio differs"):
                    SPLIT.split(folder)
            self.assertTrue((folder / "album.flac").exists())
            self.assertEqual((folder / "dr14.txt").read_text(), "old report\n")
            self.assertFalse(list(folder.glob(".omdrc-cue-split-*")))
            self.assertEqual(SPLIT.split(folder, verify_only=True), (0, 2, None))
            self.assertTrue((folder / "album.flac").exists())
            self.assertEqual(SPLIT.split(folder), (0, 2, None))
            self.assertFalse((folder / "album.flac").exists())
            self.assertEqual(sorted(p.name for p in folder.glob("*.flac")),
                             ["01 - First.flac", "02 - Second.flac"])
            self.assertEqual((folder / "dr14.whole-flac.txt").read_text(), "old report\n")
            self.assertTrue((folder / "album.cue.original").exists())
            self.assertIn("Number of tracks:  2", (folder / "dr14.txt").read_text())
            tags = subprocess.run(["metaflac", "--export-tags-to=-", str(folder / "01 - First.flac")],
                                  check=True, capture_output=True, text=True).stdout
            self.assertIn("TITLE=First", tags)
            self.assertIn("ALBUMARTIST=Test Artist", tags)

    def test_mismatched_cue_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "album.flac").write_bytes(b"original")
            (folder / "album.cue").write_text('FILE "elsewhere.flac" WAVE\n')
            with self.assertRaises(SPLIT.SplitError):
                SPLIT.split(folder)
            self.assertEqual((folder / "album.flac").read_bytes(), b"original")
