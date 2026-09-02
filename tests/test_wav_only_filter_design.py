"""WAV-only filter publication keeps runtime assurance without inventing a graph."""

import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import wave


ROOT = Path(__file__).resolve().parents[1]


def impulse(path: Path, rate: int = 48000) -> None:
    samples = [16384] + [0] * 127
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack("<" + "h" * len(samples), *samples))


class WavOnlyFilterDesignTest(unittest.TestCase):
    def test_publishes_verified_runtime_but_no_response(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-wav-only-") as name:
            site = Path(name)
            upload = site / "upload"
            upload.mkdir()
            impulse(upload / "L.wav")
            impulse(upload / "R.wav")
            command = [
                sys.executable, str(ROOT / "scripts/new_wav_filter_design.py"),
                str(upload), "--site-root", str(site), "--geometry", "headphones",
                "--design", "downloaded", "--rates", "48000", "--no-commit",
                "--upload-provenance", "--yes", "--no-next",
            ]
            result = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest_path = site / "filters/headphones/provenance/downloaded.json"
            manifest = json.loads(manifest_path.read_text())
            analysis = json.loads(
                (site / "filters/headphones/analysis/downloaded.json").read_text())
            self.assertFalse(manifest["response_available"])
            self.assertFalse(analysis["response_available"])
            self.assertEqual(analysis["traces"], [])
            self.assertEqual(set(manifest["source"]["artifacts"]),
                             {"filter_left_wav", "filter_right_wav"})
            verified = subprocess.run([
                sys.executable, str(ROOT / "scripts/verify_filter_bundle.py"),
                "--site-root", str(site), "--require-sources", "--no-next",
                str(manifest_path),
            ], text=True, capture_output=True)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertIn("FILTER RESPONSE unavailable", verified.stdout)


if __name__ == "__main__":
    unittest.main()
