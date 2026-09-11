"""Regression tests for the rate-mismatch tooling added 2026-09-11.

Each test pins a behaviour that, when wrong, produced a confident wrong answer
during the investigation rather than an error:

* resampler-residual.py and bitperfect-null.py must read the WIRE's rate, not
  the material's — analysed at the material rate, a 997 Hz tone resampled to
  192 kHz "appeared" at 229 Hz and the analyser reported garbage.
* bitperfect-null.py must refuse captures taken through a resampler: two runs
  of MPD's soxr were measured to differ by a sub-sample offset, so a
  whole-sample null reports DIFFERENT for two perfect resamples.
* bitperfect-null.py treats a variant LABEL difference as a note when the
  coefficient hashes prove the filters identical, and blocks otherwise.
* bitperfect_runner.py must not guess which USB card to tap when there are
  several, and accepts a rate mismatch only when asked (--allow-resample) and
  only against a capture.
"""
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


BP = _load("bprun_ratetest", ROOT / "scripts/bitperfect_runner.py")
NULL = _load("bpnull_ratetest", ROOT / "scripts/bitperfect-null.py")
RESIDUAL = ROOT / "scripts/resampler-residual.py"
FLAT_192K = ROOT / "configs/flat/brutefir-192000.conf"

AMP = (2**31 - 1) * 10 ** (-90 / 20)


def _tone_capture(path: Path, rate: int, *, linear_from: int | None = None,
                  seconds: float = 4.0) -> None:
    """Write a padded stereo 997/1499 Hz capture; optionally a crude
    linear-interpolation resample from `linear_from` Hz, as a bad resampler."""
    n = np.arange(int(rate * seconds))
    if linear_from is None:
        sig = np.column_stack([AMP * np.sin(2 * np.pi * 997 * n / rate),
                               AMP * np.sin(2 * np.pi * 1499 * n / rate)])
    else:
        m = np.arange(int(linear_from * seconds) + 2)
        t = n * linear_from / rate
        sig = np.column_stack([
            np.interp(t, m, AMP * np.sin(2 * np.pi * 997 * m / linear_from)),
            np.interp(t, m, AMP * np.sin(2 * np.pi * 1499 * m / linear_from))])
    pad = np.zeros((rate // 2, 2))
    np.rint(np.concatenate([pad, sig, pad])).astype("<i4").tofile(path)


def _residual(target: str, *extra: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "r.json"
        subprocess.run([sys.executable, str(RESIDUAL), target, "--seconds", "1",
                        "--json", str(out), *extra],
                       check=True, capture_output=True, text=True)
        return json.loads(out.read_text())


class ResidualAnalyser(unittest.TestCase):
    def test_a_perfect_tone_lands_on_the_32_bit_floor(self):
        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "ideal.wire.raw"
            _tone_capture(raw, 192000)
            r = _residual(str(raw), "--rate", "192000")
        for ch in "LR":
            self.assertLess(abs(r["channels"][ch]["above_floor_db"]), 1.0)

    def test_a_poor_resampler_is_tens_of_db_above_the_floor(self):
        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "linear.wire.raw"
            _tone_capture(raw, 192000, linear_from=44100)
            r = _residual(str(raw), "--rate", "192000")
        for ch in "LR":
            self.assertGreater(r["channels"][ch]["above_floor_db"], 30.0)

    def test_the_wire_rate_wins_over_the_material_rate(self):
        """Sidecar says material 44100, chain 192000: analyse at 192000."""
        with tempfile.TemporaryDirectory() as d:
            prefix = Path(d) / "cap"
            _tone_capture(Path(f"{prefix}.wire.raw"), 192000)
            Path(f"{prefix}.json").write_text(json.dumps(
                {"rate": 44100, "chain": {"brutefir_rate": 192000}}))
            r = _residual(str(prefix))
        self.assertEqual(r["rate"], 192000)
        self.assertAlmostEqual(r["channels"]["L"]["tone_hz"], 997, delta=0.05)


class NullRate(unittest.TestCase):
    def _capture(self, d: str, sidecar: dict) -> str:
        prefix = str(Path(d) / "cap")
        np.zeros(64, dtype="<i4").tofile(f"{prefix}.wire.raw")
        Path(f"{prefix}.json").write_text(json.dumps(sidecar))
        return prefix

    def test_wire_rate_field_is_preferred(self):
        with tempfile.TemporaryDirectory() as d:
            cap = NULL.load_capture(self._capture(d, {"rate": 44100, "wire_rate": 192000}),
                                    None, None)
        self.assertEqual(cap["rate"], 192000)

    def test_older_captures_fall_back_to_the_chain_rate(self):
        with tempfile.TemporaryDirectory() as d:
            cap = NULL.load_capture(self._capture(
                d, {"rate": 44100, "chain": {"brutefir_rate": 192000}}), None, None)
        self.assertEqual(cap["rate"], 192000)


def _run(name: str, prov: dict, chain: dict | None = None) -> dict:
    return {"name": name, "provenance": prov,
            "report": {"chain": chain or {}, "input_sha256": "same"}}


COEFFS_A = [{"sha256": "aa", "attenuation": 1.5}, {"sha256": "bb", "attenuation": 1.5}]
COEFFS_B = [{"sha256": "cc", "attenuation": 1.5}, {"sha256": "dd", "attenuation": 1.5}]


class NullProvenance(unittest.TestCase):
    def test_a_resampled_capture_is_refused(self):
        verdict = NULL.compare_provenance(
            _run("a", {}, {"deliberate_resample": True}), _run("b", {}))
        self.assertTrue(any("--allow-resample" in b for b in verdict["blocking"]))

    def test_variant_label_is_a_note_when_the_coefficients_match(self):
        verdict = NULL.compare_provenance(
            _run("a", {"variant": "multi.pt", "coeffs": COEFFS_A}),
            _run("b", {"variant": "fbsdcoef", "coeffs": COEFFS_A}))
        self.assertEqual(verdict["blocking"], [])
        self.assertTrue(any(n.startswith("variant:") for n in verdict["noted"]))

    def test_variant_label_still_blocks_when_the_coefficients_differ(self):
        verdict = NULL.compare_provenance(
            _run("a", {"variant": "multi.pt", "coeffs": COEFFS_A}),
            _run("b", {"variant": "fbsdcoef", "coeffs": COEFFS_B}))
        self.assertTrue(any(b.startswith("variant:") for b in verdict["blocking"]))
        self.assertTrue(any("coefficients differ" in b for b in verdict["blocking"]))


class RunnerCardDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for card, usb, name in (("0", "001/003", "U24XL"), ("1", None, "Loopback"),
                                ("2", "001/004", "DAC8STEREO")):
            (root / f"card{card}").mkdir()
            (root / f"card{card}" / "id").write_text(name + "\n")
            if usb:
                (root / f"card{card}" / "usbbus").write_text(usb + "\n")
        real = Path
        self.patch = unittest.mock.patch.object(
            BP, "Path", lambda p: real(root) if str(p) == "/proc/asound" else real(p))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_two_usb_cards_and_no_choice_is_refused(self):
        with self.assertRaises(RuntimeError) as cm:
            BP.discover_linux()
        self.assertIn("--card", str(cm.exception))

    def test_an_explicit_card_is_honoured(self):
        dac = BP.discover_linux("2")
        self.assertEqual((dac["devnum"], dac["alsa"], dac["name"]), (4, "hw:2,0", "DAC8STEREO"))

    def test_a_non_usb_card_is_rejected(self):
        with self.assertRaises(RuntimeError):
            BP.discover_linux("1")


class RunnerAllowResample(unittest.TestCase):
    def _route(self, **kw):
        chain = {"script": "/usr/local/bin/omdrc", "geometry": "flat", "sink": "ALSA",
                 "sink_rate": 192000, "brutefir_rate": 192000}
        with unittest.mock.patch.object(BP, "chain_state", lambda: dict(chain)), \
             unittest.mock.patch.object(BP, "running_brutefir_conf", lambda: FLAT_192K), \
             unittest.mock.patch.object(BP, "chain_provenance", lambda: {}), \
             unittest.mock.patch.object(BP, "say", lambda *_: None):
            return BP.assert_drc_route({"rate": 44100}, **kw)

    def test_a_mismatch_is_refused_by_default(self):
        with self.assertRaises(SystemExit):
            self._route(reference="capture")

    def test_allow_resample_records_the_mismatch_instead(self):
        chain = self._route(reference="capture", allow_resample=True)
        self.assertTrue(chain["deliberate_resample"])
        self.assertTrue(chain["rate_verdict"].startswith("MISMATCH (deliberate)"))

    def test_allow_resample_does_not_apply_to_a_source_verdict(self):
        with self.assertRaises(SystemExit):
            self._route(reference="source", allow_resample=True)

    def test_the_output_selector_is_offered(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts/bitperfect_runner.py"),
                              "--help"], capture_output=True, text=True).stdout
        self.assertIn("--drc-output", out)
        self.assertIn("--allow-resample", out)


if __name__ == "__main__":
    unittest.main()


class StatusRateLabel(unittest.TestCase):
    """`drc.sh status` with stand-in ps/mpc: MPD decoding 44100 Hz, BruteFIR at
    192000 Hz.  In resamp mode the difference is deliberate and must say so;
    otherwise it is a bare [MISMATCH].  Both must keep the word MISMATCH, which
    bitperfect_runner.py keys on."""

    def _status(self, last_arg: str) -> str:
        import os
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            site, state, shims = root / "site", root / "state", root / "bin"
            (site / "configs/flat").mkdir(parents=True)
            (site / "configs/flat/brutefir-192000.conf").write_text("sampling_rate: 192000;\n")
            state.mkdir(); shims.mkdir()
            (state / "last_arg").write_text(last_arg + "\n")
            (state / "last_power").write_text("on\n")
            (shims / "ps").write_text(
                "#!/bin/sh\necho 'brutefir /x/configs/flat/brutefir-192000.conf -daemon'\n")
            (shims / "mpc").write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *audioformat*) echo 44100:32:2 ;;\n"
                "  current*) echo test.wav ;;\n"
                "  status*) printf 'test.wav\\n[playing] #1/1   0:01/0:10 (10%%)\\n' ;;\n"
                "esac\n")
            for f in ("ps", "mpc"):
                (shims / f).chmod(0o755)
            conf = root / "omdrc.conf"
            conf.write_text(f"GEOMETRY=flat\nOMDRC_SITE_DIR={site}\nOMDRC_STATE_DIR={state}\n")
            env = dict(os.environ, OMDRC_CONF=str(conf),
                       PATH=f"{shims}:{os.environ['PATH']}")
            out = subprocess.run([str(ROOT / "drc.sh"), "status"], env=env,
                                 capture_output=True, text=True, timeout=10).stdout
        return next((l for l in out.splitlines() if l.startswith("Rate:")), "")

    def test_native_mode_is_a_bare_mismatch(self):
        line = self._status("192000")
        self.assertIn("MPD 44100 Hz != brutefir 192000 Hz", line)
        self.assertIn("[MISMATCH]", line)
        self.assertNotIn("deliberate", line)

    def test_resamp_mode_says_the_resample_is_deliberate(self):
        line = self._status("resamp")
        self.assertIn("MISMATCH", line)
        self.assertIn("deliberate", line)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux status branch")
    def test_the_runner_still_reads_both_as_a_mismatch(self):
        for arg in ("192000", "resamp"):
            value = self._status(arg).split(":", 1)[1]
            self.assertIn("MISMATCH", value)
