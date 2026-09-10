#!/usr/bin/env python3
"""The cross-OS null test must not report an operating-system difference that
is really an experimental one.

Nulling two captures is only meaningful if both were taken through the same
material, the same filter, the same rate and the same arithmetic. Every way
that can silently not be true is a way for this tool to print a number that
gets read as "FreeBSD sounds different", so each gets a test — alongside the
numeric behaviour itself: exact equality, last-bit rounding, and a real
divergence.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bpnull",
                                              ROOT / "scripts/bitperfect-null.py")
NULL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(NULL)

RATE, CHANNELS = 44100, 2

FREEBSD = {"os": "freebsd15/15.1-RELEASE-p2", "rate": RATE, "variant": "multi.pt",
           "geometry": "120.blue", "float_bits": "64", "input_sample": "S32_LE",
           "output_sample": "S32_LE", "dither": "false",
           "filter_length": "8192,64", "loopback": "virtual_oss",
           "loopback_resampling": False,
           "coeffs": [{"sha256": "L", "attenuation": 1.5},
                      {"sha256": "R", "attenuation": 1.5}]}
LINUX = dict(FREEBSD, os="linux/7.1.5", filter_length="32768,16",
             loopback="snd-aloop")


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        rng = np.random.default_rng(11)
        self.base = (rng.standard_normal((RATE * 3, CHANNELS))
                     * 2 ** 26).astype(np.int32)
        self.rng = rng

    def write(self, name, samples, lead, provenance, **report):
        pad = np.zeros((lead, CHANNELS), dtype=np.int32)
        np.concatenate([pad, samples]).tofile(self.dir / f"{name}.wire.raw")
        body = {"rate": RATE, "channels": CHANNELS, "input_sha256": "same",
                "chain": {"provenance": provenance}}
        body.update(report)
        (self.dir / f"{name}.json").write_text(json.dumps(body))
        return str(self.dir / name)

    def run_null(self, a, b):
        return NULL.null(NULL.load_capture(a, None, None),
                         NULL.load_capture(b, None, None))

    def run_provenance(self, a, b):
        return NULL.compare_provenance(NULL.load_capture(a, None, None),
                                       NULL.load_capture(b, None, None))


class Alignment(Fixtures):
    """The two captures never start at the same sample."""

    def test_different_priming_still_nulls_exactly(self):
        a = self.write("a", self.base, 733, FREEBSD)
        b = self.write("b", self.base, 5119, LINUX)
        result = self.run_null(a, b)
        self.assertEqual(result["lag_frames"], 5119 - 733)
        self.assertEqual(result["verdict"], "IDENTICAL")
        self.assertEqual(result["differing_samples"], 0)

    def test_lag_in_the_other_direction(self):
        a = self.write("a", self.base, 8000, FREEBSD)
        b = self.write("b", self.base, 120, LINUX)
        result = self.run_null(a, b)
        self.assertEqual(result["lag_frames"], 120 - 8000)
        self.assertEqual(result["verdict"], "IDENTICAL")

    def test_unrelated_material_refuses_rather_than_reporting_a_difference(self):
        """Two unrelated captures cannot be aligned, and "cannot align" must
        not be reported as "the chains differ" — that is the whole failure
        mode this comparison exists to avoid."""
        other = (self.rng.standard_normal(self.base.shape)
                 * 2 ** 26).astype(np.int32)
        a = self.write("a", self.base, 400, FREEBSD)
        b = self.write("b", other, 400, LINUX)
        with self.assertRaises(NULL.CannotJudge) as caught:
            self.run_null(a, b)
        self.assertEqual(caught.exception.code, 2)


class SilencePadding(Fixtures):
    """Captures carry arbitrary silence, because BruteFIR streams continuously.

    On the DRC route the convolver feeds the DAC whether or not MPD is
    playing, so the tap records however much silence it happened to catch
    either side of the material. Two real captures of the same 10 s file held
    their signal at 9.50-22.25 s and 3.75-16.50 s. Choosing the correlation
    window by position instead of by energy put it in silence for one of them,
    correlated at r=0.61, and reported two byte-identical chains as DIFFERENT
    at -66.6 dBFS. This is that case."""

    def signal_with_silence(self, lead_s: float, tail_s: float):
        lead = np.zeros((int(RATE * lead_s), CHANNELS), dtype=np.int32)
        tail = np.zeros((int(RATE * tail_s), CHANNELS), dtype=np.int32)
        return np.concatenate([lead, self.base, tail])

    def test_very_different_silence_padding_still_nulls(self):
        a = self.write("a", self.signal_with_silence(9.5, 4.6), 0, FREEBSD)
        b = self.write("b", self.signal_with_silence(3.75, 4.6), 0, LINUX)
        result = self.run_null(a, b)
        self.assertEqual(result["verdict"], "IDENTICAL")
        self.assertGreater(result["alignment_confidence"], 0.999)

    def test_window_is_taken_from_the_loudest_part(self):
        """Not from a fixed fraction: a quarter in would be silence here."""
        padded = self.signal_with_silence(9.5, 4.6)[:, 0]
        length = 1 << 15
        start = NULL._loudest_window(padded, length)
        self.assertTrue(np.any(padded[start:start + length]),
                        "the chosen window is silent")

    def test_leading_silence_longer_than_the_signal(self):
        a = self.write("a", self.signal_with_silence(20.0, 1.0), 0, FREEBSD)
        b = self.write("b", self.signal_with_silence(0.5, 1.0), 0, LINUX)
        self.assertEqual(self.run_null(a, b)["verdict"], "IDENTICAL")


class Verdicts(Fixtures):
    def test_last_bit_rounding_reads_as_equivalent(self):
        """Different partitionings round differently; that is not an OS."""
        jittered = self.base.copy()
        mask = self.rng.random(self.base.shape) < 0.03
        jittered[mask] += self.rng.choice(
            [-1, 1], size=int(mask.sum())).astype(np.int32)
        a = self.write("a", self.base, 400, FREEBSD)
        b = self.write("b", jittered, 400, LINUX)
        result = self.run_null(a, b)
        self.assertEqual(result["verdict"], "EQUIVALENT")
        self.assertEqual(result["max_diff_lsb"], 1)
        self.assertLess(result["null_depth_db"], -180)

    def test_a_small_level_difference_is_caught(self):
        """0.3 dB — inaudible as loudness, obvious as 'less air'."""
        louder = (self.base * 10 ** (0.3 / 20)).astype(np.int32)
        a = self.write("a", self.base, 400, FREEBSD)
        b = self.write("b", louder, 400, LINUX)
        result = self.run_null(a, b)
        self.assertEqual(result["verdict"], "DIFFERENT")
        self.assertGreater(result["null_depth_db"], -80)

    def test_first_difference_is_located(self):
        changed = self.base.copy()
        changed[12345, 1] += 5000
        a = self.write("a", self.base, 0, FREEBSD)
        b = self.write("b", changed, 0, LINUX)
        result = self.run_null(a, b)
        self.assertEqual(result["first_diff"]["frame"], 12345)
        self.assertEqual(result["first_diff"]["delta"], [0, -5000])


class Provenance(Fixtures):
    """Refusing is the feature: a number here would be read as an OS verdict."""

    def test_different_coefficients_block(self):
        a = self.write("a", self.base, 0, FREEBSD)
        b = self.write("b", self.base, 0,
                       dict(LINUX, coeffs=[{"sha256": "X", "attenuation": 1.5},
                                           {"sha256": "Y", "attenuation": 1.5}]))
        blocking = self.run_provenance(a, b)["blocking"]
        self.assertTrue(any("coefficients differ" in x for x in blocking), blocking)

    def test_different_attenuation_blocks(self):
        a = self.write("a", self.base, 0, FREEBSD)
        b = self.write("b", self.base, 0,
                       dict(LINUX, coeffs=[{"sha256": "L", "attenuation": 3.0},
                                           {"sha256": "R", "attenuation": 3.0}]))
        blocking = self.run_provenance(a, b)["blocking"]
        self.assertTrue(any("attenuation differs" in x for x in blocking), blocking)

    def test_different_rate_blocks(self):
        a = self.write("a", self.base, 0, FREEBSD)
        b = self.write("b", self.base, 0, dict(LINUX, rate=96000))
        self.assertTrue(self.run_provenance(a, b)["blocking"])

    def test_dither_on_one_side_blocks(self):
        a = self.write("a", self.base, 0, FREEBSD)
        b = self.write("b", self.base, 0, dict(LINUX, dither="true"))
        blocking = self.run_provenance(a, b)["blocking"]
        self.assertTrue(any("dither" in x for x in blocking), blocking)

    def test_different_input_material_blocks(self):
        a = self.write("a", self.base, 0, FREEBSD, input_sha256="one")
        b = self.write("b", self.base, 0, LINUX, input_sha256="two")
        blocking = self.run_provenance(a, b)["blocking"]
        self.assertTrue(any("different input material" in x for x in blocking))

    def test_os_and_loopback_are_expected_not_blocking(self):
        """The differences under test must never disqualify the test."""
        a = self.write("a", self.base, 0, FREEBSD)
        b = self.write("b", self.base, 0, LINUX)
        prov = self.run_provenance(a, b)
        self.assertEqual(prov["blocking"], [])
        joined = " ".join(prov["noted"])
        self.assertIn("virtual_oss", joined)
        self.assertIn("filter_length", joined)

    def test_virtual_oss_dash_S_is_surfaced(self):
        """-S would put virtual_oss's own -Q 2 resampler in the path."""
        a = self.write("a", self.base, 0, dict(FREEBSD, loopback_resampling=True))
        b = self.write("b", self.base, 0, LINUX)
        noted = " ".join(self.run_provenance(a, b)["noted"])
        self.assertIn("-S", noted)


class PlatformDefaults(unittest.TestCase):
    """The two platform defaults must agree on the partitioning.

    They are separate files because the audio backend differs, and it would be
    easy to tune one and not the other. If they drift apart, every cross-OS
    null silently acquires a last-bit difference and a latency difference that
    belong to the config, not to the operating system."""

    def filter_length(self, name: str) -> str:
        import re
        text = (ROOT / name).read_text()
        m = re.search(r"^filter_length:\s*([^;]+);", text, re.M)
        self.assertIsNotNone(m, f"{name} has no filter_length")
        return m.group(1).strip()

    def test_freebsd_and_linux_defaults_match(self):
        self.assertEqual(self.filter_length("brutefir_defaults.conf"),
                         self.filter_length("brutefir_defaults.linux.conf"))

    def test_partitioning_fits_the_virtual_oss_buffer_cap(self):
        """8192 frames at 192 kHz is ~43 ms; virtual_oss is started with
        -s 200ms and caps around 250 ms. A partition close to that cap leaves
        the convolver no ring-buffer slack."""
        partition = int(self.filter_length("brutefir_defaults.conf").split(",")[0])
        block_ms = partition / 192000 * 1000
        self.assertLess(block_ms, 100,
                        f"{partition}-frame partitions are {block_ms:.0f} ms "
                        "at 192 kHz — too close to virtual_oss's buffer cap")


if __name__ == "__main__":
    unittest.main()
