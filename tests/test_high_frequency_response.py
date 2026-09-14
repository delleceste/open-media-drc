"""Regression tests for the high-frequency response probe and analyser."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/gen-high-frequency-wav.py"
ANALYSER = ROOT / "scripts/high-frequency-response.py"


def generate(path: Path, *extra: str) -> None:
    subprocess.run([sys.executable, str(GENERATOR), str(path), *extra], check=True,
                   capture_output=True, text=True)


def analyse(*captures: Path, filter_taps: int = 0) -> tuple[int, dict]:
    output = captures[0].parent / "result.json"
    result = subprocess.run([sys.executable, str(ANALYSER),
                             *(str(path) for path in captures),
                             "--filter-taps", str(filter_taps),
                             "--json", str(output)], capture_output=True, text=True)
    return result.returncode, json.loads(output.read_text())


def combine(low: Path, high: Path, output: Path, high_gain: float) -> None:
    with wave.open(str(low), "rb") as source:
        params = source.getparams()
        low_samples = np.frombuffer(source.readframes(source.getnframes()),
                                    dtype="<i4").astype(np.float64)
    with wave.open(str(high), "rb") as source:
        high_samples = np.frombuffer(source.readframes(source.getnframes()),
                                     dtype="<i4").astype(np.float64)
    samples = np.rint(low_samples + high_gain * high_samples).astype("<i4")
    with wave.open(str(output), "wb") as destination:
        destination.setparams(params)
        destination.writeframes(samples.tobytes())


class HighFrequencyResponse(unittest.TestCase):
    def test_default_material_is_44100_hz_24_bit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.wav"
            generate(path)
            with wave.open(str(path), "rb") as source:
                self.assertEqual((source.getframerate(), source.getsampwidth(),
                                  source.getnchannels()), (44100, 3, 2))

    def test_generated_material_measures_flat(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat.wav"
            generate(path)
            status, report = analyse(path)
            self.assertEqual(status, 0)
            for tone in report["captures"][0]["tones"]:
                for level in tone["relative_db"]:
                    self.assertAlmostEqual(level, 0.0, delta=0.002)

    def test_runner_prefix_artifacts_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav, prefix = root / "source.wav", root / "capture"
            generate(wav, "--bits", "32")
            with wave.open(str(wav), "rb") as source:
                Path(str(prefix) + ".wire.raw").write_bytes(
                    source.readframes(source.getnframes()))
            Path(str(prefix) + ".json").write_text(json.dumps(
                {"wire_rate": 44100, "channels": 2}))
            status, report = analyse(prefix)
            self.assertEqual(status, 0)
            self.assertEqual(report["captures"][0]["rate"], 44100)

    def test_a_six_db_treble_loss_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            low, high, flat, rolled = (root / name for name in
                                       ("low.wav", "high.wav", "flat.wav", "rolled.wav"))
            generate(low, "--bits", "32", "--frequencies", "1000", "4000", "8000")
            generate(high, "--bits", "32", "--frequencies", "12000", "16000", "19000")
            combine(low, high, flat, 1.0)
            combine(low, high, rolled, 0.5)
            status, report = analyse(flat, rolled)
            self.assertEqual(status, 1)
            self.assertFalse(report["comparison"]["passed"])
            for tone in report["comparison"]["second_minus_first"][3:]:
                for delta in tone["delta_db"]:
                    self.assertAlmostEqual(delta, -6.0206, delta=0.003)

    def test_default_settling_covers_the_deployed_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.wav"
            generate(path)
            status, report = analyse(path, filter_taps=524288)
            self.assertEqual(status, 0)
            self.assertGreater(report["captures"][0]["settling_seconds"], 11.8)


if __name__ == "__main__":
    unittest.main()
