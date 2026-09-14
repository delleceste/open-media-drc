"""Regression tests for the high-frequency response tooling.

The investigation of 2026-09-08..11 excluded every digital candidate it tested,
but the complaint it was chasing — a loss of air and detail — is about the top
octave, and nothing measured the top octave.  Worse, the two instruments in use
*cannot*: a null needs identical bytes, so it stops working the moment a
resampler is in the path, and resampler-residual.py fits amplitude freely, so
an attenuated band is absorbed into the fit and still reports a deep residual.

These tests pin the properties that make the replacement trustworthy.  Each one
fails in the direction that produced a confident wrong answer before:

* The analyser must RECOVER a known response, not merely notice that something
  changed — a 1 dB dip at 18 kHz must read as 1 dB at 18 kHz.
* It must do so through a resampler, where a null cannot run at all.
* Its phase must survive a large delay.  Unwrapping a sparse tone set across
  BruteFIR's latency returns nonsense that looks like a measurement; the first
  version of this tool reported 349 degrees of "deviation" for a bit-perfect
  identity chain.
* Its empty bins must be genuinely empty, so distortion lands where it can be
  seen.  Tone bins are all odd, and no third-order product may fall on a tone.
* It must count clamped samples, the only signature of the level-dependent
  failure that every previous test ran too quietly to provoke.
* A flat, rate-matched chain must PASS, or the tool is useless as a gate.
"""
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "scripts/gen-multitone-wav.py"
HF = ROOT / "scripts/hf-response.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


GENMOD = _load("genmultitone", GEN)


def generate(tmp: Path, **kw):
    argv = [sys.executable, str(GEN), str(tmp / "m.wav")]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    subprocess.run(argv, check=True, capture_output=True)
    man = json.loads((tmp / "m.tones.json").read_text())
    with wave.open(str(tmp / "m.wav")) as w:
        raw = w.readframes(w.getnframes())
        width = w.getsampwidth()
    if width == 4:
        a = np.frombuffer(raw, dtype="<i4").reshape(-1, 2).astype(np.float64) / 2 ** 31
    else:
        a = np.frombuffer(raw, dtype="<i2").reshape(-1, 2).astype(np.float64) / 2 ** 15
    return man, a


def wire(tmp: Path, x: np.ndarray, name="w.raw", pad=0.5, rate=44100):
    """Write a float signal as an S32_LE stereo capture with silence around it."""
    q = np.clip(np.rint(x * 2 ** 31), -2 ** 31, 2 ** 31 - 1).astype("<i4")
    silence = np.zeros((int(pad * rate), 2), dtype="<i4")
    p = tmp / name
    np.concatenate([silence, q, silence]).tofile(p)
    return p


def shape(x, rate, curve):
    """Apply an exact gain curve g(f) in the frequency domain, both channels."""
    out = np.empty_like(x)
    f = np.fft.rfftfreq(len(x), 1 / rate)
    g = curve(f)
    for c in range(x.shape[1]):
        out[:, c] = np.fft.irfft(np.fft.rfft(x[:, c]) * g, len(x))
    return out


def run_hf(capture: Path, manifest: Path, rate: int, extra=()):
    r = subprocess.run(
        [sys.executable, str(HF), str(capture), "--tones", str(manifest),
         "--rate", str(rate), "--json", str(capture.with_suffix(".report.json")),
         *extra],
        capture_output=True, text=True)
    report = json.loads(capture.with_suffix(".report.json").read_text())
    return r, report["result"]


class GeneratorProperties(unittest.TestCase):
    """The claims the analysis rests on, checked rather than assumed."""

    def test_all_tone_bins_are_odd(self):
        with tempfile.TemporaryDirectory() as d:
            man, _ = generate(Path(d), rate=44100, tones=96)
        self.assertTrue(all(k % 2 for k in man["bins"]),
                        "even bins let even-order distortion hide under a tone")

    def test_no_third_order_product_lands_on_a_tone(self):
        with tempfile.TemporaryDirectory() as d:
            man, _ = generate(Path(d), rate=44100, tones=96)
        bins = set(man["bins"])
        collisions = [(a, b) for a in bins for b in bins
                      if a != b and (2 * a - b) in bins]
        self.assertEqual(collisions, [],
                         "3rd-order products on tone bins make the empty-bin "
                         "floor report clean while the chain distorts")

    def test_period_is_bin_exact_at_the_chain_rate(self):
        for chain in (44100, 48000, 88200, 96000, 192000):
            with tempfile.TemporaryDirectory() as d:
                man, _ = generate(Path(d), rate=44100, chain_rate=chain)
            m = man["period_samples"] * chain / man["rate"]
            self.assertEqual(m, int(m),
                             f"period not a whole number of samples at {chain}")

    def test_overs_mode_has_intersample_overshoot(self):
        with tempfile.TemporaryDirectory() as d:
            man, _ = generate(Path(d), mode="overs", peak=-0.1)
        self.assertLess(man["sample_peak_dbfs"], 0.0)
        self.assertGreater(man["true_peak_dbfs"], 2.5,
                           "the point of the signal is that it overshoots")


class RecoversAKnownResponse(unittest.TestCase):
    """A measurement, not a change detector."""

    def _measure(self, curve, chain_rate=44100, tones=96):
        d = Path(self.tmp.name)
        man, x = generate(d, rate=44100, chain_rate=chain_rate, tones=tones)
        y = shape(x, 44100, curve) if curve else x
        cap = wire(d, y, rate=44100)
        _, res = run_hf(cap, d / "m.tones.json", 44100)
        freqs = np.array(man["freqs_hz"])
        rel = np.array(res["channels"]["left"]["rel_db"])
        return freqs, rel, res

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_identity_chain_is_flat_and_passes(self):
        freqs, rel, res = self._measure(None)
        self.assertLess(np.abs(rel).max(), 0.001)
        self.assertTrue(res["pass"])
        self.assertEqual(res["clipped_samples"], 0)

    def test_one_dB_dip_at_18k_reads_as_one_dB_at_18k(self):
        # the exact failure resampler-residual.py absorbs into its fit
        def curve(f):
            return 1.0 - 0.109 * np.exp(-((f - 18000) / 1500.0) ** 2)
        freqs, rel, res = self._measure(curve)
        near18k = int(np.argmin(np.abs(freqs - 18000)))
        self.assertAlmostEqual(rel[near18k], -1.0, delta=0.15)
        low = rel[freqs < 8000]
        self.assertLess(np.abs(low).max(), 0.05, "the dip must stay local")
        self.assertFalse(res["pass"], "a 1 dB HF dip must not pass")

    def test_gentle_top_octave_rolloff_is_caught(self):
        # -0.3 dB at 20 kHz: below anything a listener would name, above the gate
        def curve(f):
            return 1.0 - 0.034 * np.clip((f - 12000) / 8000.0, 0, 1)
        freqs, rel, res = self._measure(curve)
        self.assertLess(rel[np.argmin(np.abs(freqs - 20000))], -0.15)
        self.assertFalse(res["pass"])

    def test_phase_survives_a_large_delay(self):
        d = Path(self.tmp.name)
        man, x = generate(d, rate=44100)
        y = np.roll(x, 4096, axis=0)          # far more than a period of 20 kHz
        cap = wire(d, y, rate=44100)
        _, res = run_hf(cap, d / "m.tones.json", 44100)
        dev = np.abs(np.array(res["channels"]["left"]["phase_dev_deg"]))
        self.assertLess(dev.max(), 0.5,
                        "a pure delay is not a phase distortion; unwrapping "
                        "a sparse tone set across it reports that it is")


class ThroughAResampler(unittest.TestCase):
    """Where a null test cannot go."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _resample(self, x, up, down):
        from scipy.signal import resample_poly
        return np.stack([resample_poly(x[:, c], up, down, window=("kaiser", 14.0))
                         for c in range(x.shape[1])], axis=1)

    def test_good_resample_is_flat_below_16k(self):
        d = Path(self.tmp.name)
        man, x = generate(d, rate=44100, chain_rate=192000)
        y = self._resample(x, 640, 147)
        cap = wire(d, y, rate=192000)
        _, res = run_hf(cap, d / "m.tones.json", 192000)
        freqs = np.array(man["freqs_hz"])
        rel = np.array(res["channels"]["left"]["rel_db"])
        below = np.abs(rel[freqs < 16000]).max()
        self.assertLess(below, 0.05,
                        "a good resampler must not tilt the audible band")

    def test_resampler_images_are_reported(self):
        d = Path(self.tmp.name)
        man, x = generate(d, rate=44100, chain_rate=88200)
        # a deliberately poor 2x upsample: zero-stuff, no image rejection
        y = np.zeros((len(x) * 2, 2))
        y[::2] = x
        cap = wire(d, y, rate=88200)
        _, res = run_hf(cap, d / "m.tones.json", 88200)
        self.assertGreater(res["channels"]["left"]["above_nyquist_db"], -20.0,
                           "unrejected images must show above the source Nyquist")
        self.assertFalse(res["pass"])


class LevelDependentFailure(unittest.TestCase):
    """The class of defect every earlier test ran too quietly to provoke."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_clamped_samples_are_counted(self):
        d = Path(self.tmp.name)
        # the generator refuses a level that would already clip the file, so
        # the clamp has to come from gain applied downstream, as a chain does
        man, x = generate(d, rate=44100, level=-20.0)
        y = np.clip(x * 16.0, -1.0, 1.0)
        cap = wire(d, y, rate=44100)
        _, res = run_hf(cap, d / "m.tones.json", 44100)
        self.assertGreater(res["clipped_samples"], 0)
        self.assertFalse(res["pass"])

    def test_clipping_shows_in_the_empty_bins_above_10k(self):
        d = Path(self.tmp.name)
        man, x = generate(d, rate=44100, level=-20.0)
        clean = wire(d, x, "clean.raw", rate=44100)
        hot = wire(d, np.clip(x * 16.0, -1.0, 1.0), "hot.raw", rate=44100)
        _, a = run_hf(clean, d / "m.tones.json", 44100)
        _, b = run_hf(hot, d / "m.tones.json", 44100)
        self.assertGreater(b["channels"]["left"]["empty_above_10k_db"],
                           a["channels"]["left"]["empty_above_10k_db"] + 40,
                           "clipping intermodulation belongs in the empty bins")

    def test_overs_mode_reports_the_clamp(self):
        d = Path(self.tmp.name)
        man, x = generate(d, mode="overs", peak=-0.1, chain_rate=44100)
        # emulate a resample that turns the overshoot into real samples
        y = np.clip(x * 10 ** (2.0 / 20), -1.0, 1.0)
        cap = wire(d, y, rate=44100)
        r, res = run_hf(cap, d / "m.tones.json", 44100)
        self.assertGreater(res["clipped_samples"], 0)
        self.assertFalse(res["pass"])
        self.assertIn("clamped intersample overshoot", r.stdout)


class ChainIntegrity(unittest.TestCase):
    def test_a_dropped_sample_shows_as_period_drift(self):
        with tempfile.TemporaryDirectory() as dd:
            d = Path(dd)
            man, x = generate(d, rate=44100)
            n = man["period_samples"]
            y = np.delete(x, 2 * n + 17, axis=0)     # lose one frame mid-capture
            cap = wire(d, y, rate=44100)
            _, res = run_hf(cap, d / "m.tones.json", 44100, extra=("--windows", "4"))
            self.assertGreater(res["channels"]["left"]["period_drift"], 1e-6,
                               "two periods of a periodic signal must agree "
                               "unless the chain lost or inserted samples")


if __name__ == "__main__":
    unittest.main()
