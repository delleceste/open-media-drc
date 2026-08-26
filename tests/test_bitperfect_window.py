#!/usr/bin/env python3
"""The byte view must agree with the verdict, exactly.

The page shows a user which bytes were compared and where they diverged. If
`window`/`scan` disagreed with `finalize` — a different offset, a different
notion of "identical" — the page would be lying in the most damaging possible
way: it would look like proof. These tests pin the two together on synthetic
captures whose faults are known to the byte.
"""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts/bitperfect-lib.py"

SPEC = importlib.util.spec_from_file_location("bplib", LIB)
BP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(BP)

# The lead-in every real capture carries: the isochronous ring is zero-filled
# and transmits before the first write() propagates (16 ms at any rate).
PRIMING = 5648
PAD = 4000


def counter_wav(path: Path, frames: int = 20000, rate: int = 44100,
                bits: int = 32) -> None:
    """The generated asset's signal: unique (L,R) per frame, ~-90 dBFS."""
    width = bits // 8
    buf = bytearray()
    for i in range(frames):
        left = i & 0xFFFF
        right = (i * 40503 + (i >> 16)) & 0xFFFF
        buf += struct.pack("<i", left)[:width] + struct.pack("<i", right)[:width]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(buf))


def run_lib(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(LIB), *map(str, args)],
                          capture_output=True, text=True)


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bpwin."))
        self.wav = self.tmp / "src.wav"
        counter_wav(self.wav)
        self.ref = self.tmp / "ref.raw"
        run_lib("prep", self.wav, self.ref)
        self.refbytes = self.ref.read_bytes()

    def finalize(self, capture: bytes, prefix: str = "out") -> dict:
        cap = self.tmp / f"{prefix}.cap"
        cap.write_bytes(capture)
        pre = self.tmp / prefix
        run_lib("finalize", self.ref, cap, 44100, 2, pre, "test/0", self.wav)
        return json.loads((self.tmp / f"{prefix}.json").read_text())

    def window(self, prefix: str, offset: int, frames: int) -> dict:
        r = run_lib("window", self.tmp / f"{prefix}.ref.raw",
                    self.tmp / f"{prefix}.wav", offset, frames, 2)
        return json.loads(r.stdout)

    def scan(self, prefix: str, buckets: int) -> dict:
        r = run_lib("scan", self.tmp / f"{prefix}.ref.raw",
                    self.tmp / f"{prefix}.wav", buckets, 2)
        return json.loads(r.stdout)


class CleanRunTest(Harness):
    def test_bit_perfect_run_shows_every_byte_identical(self):
        meta = self.finalize(b"\0" * PRIMING + self.refbytes + b"\0" * PAD)
        self.assertEqual(meta["kind"], "BIT-PERFECT")
        self.assertEqual(meta["exit"], 0)
        self.assertIsNone(meta["first_mismatch"])
        self.assertEqual(meta["start"], PRIMING)

        view = self.window("out", 0, 64)
        self.assertTrue(all(row["eq"] for row in view["rows"]))
        self.assertTrue(all(row["d"] == [] for row in view["rows"]))

        cells = self.scan("out", 64)["cells"]
        self.assertTrue(all(c["s"] == "equal" for c in cells))

    def test_decoded_samples_match_the_counter_signal(self):
        """The decoded integers are what a person actually reads on the page."""
        self.finalize(b"\0" * PRIMING + self.refbytes + b"\0" * PAD)
        rows = self.window("out", 0, 4)["rows"]
        for row in rows:
            i = row["i"]
            self.assertEqual(row["ref_s"],
                             [i & 0xFFFF, (i * 40503 + (i >> 16)) & 0xFFFF])
            self.assertEqual(row["wire_s"], row["ref_s"])


class CorruptionTest(Harness):
    def setUp(self):
        super().setUp()
        self.flip = 80000                       # byte 0 of frame 10000
        bad = bytearray(self.refbytes)
        bad[self.flip] ^= 0xFF
        self.meta = self.finalize(b"\0" * PRIMING + bytes(bad) + b"\0" * PAD,
                                  "bad")

    def test_verdict_names_the_exact_offset(self):
        self.assertEqual(self.meta["kind"], "VALUE CORRUPTION")
        self.assertEqual(self.meta["exit"], 1)
        self.assertEqual(self.meta["first_mismatch"], self.flip)

    def test_window_highlights_that_byte_and_only_that_byte(self):
        rows = self.window("bad", self.flip - 8, 3)["rows"]
        by_offset = {r["o"]: r for r in rows}
        self.assertTrue(by_offset[self.flip - 8]["eq"])
        self.assertTrue(by_offset[self.flip + 8]["eq"])
        hit = by_offset[self.flip]
        self.assertFalse(hit["eq"])
        self.assertEqual(hit["d"], [0])         # first byte of the frame
        self.assertNotEqual(hit["ref_s"][0], hit["wire_s"][0])
        self.assertEqual(hit["ref_s"][1], hit["wire_s"][1])   # right untouched

    def test_map_marks_the_containing_bucket_and_no_other(self):
        result = self.scan("bad", 20)
        states = [c["s"] for c in result["cells"]]
        self.assertEqual(states.count("diff"), 1)
        hit = states.index("diff")
        cell = result["cells"][hit]
        self.assertLessEqual(cell["o"], self.flip)
        self.assertLess(self.flip, cell["o"] + cell["n"])


class TruncatedCaptureTest(Harness):
    def test_short_capture_reads_as_incomplete_not_as_corruption(self):
        """A capture that stops early is an OBSERVER fault, not a chain fault.

        The distinction matters: the DAC received those bytes either way.
        """
        lost = 5960
        meta = self.finalize(
            b"\0" * PRIMING + self.refbytes[:-lost], "short")
        self.assertEqual(meta["kind"], "INCOMPLETE")
        self.assertEqual(meta["first_mismatch"], len(self.refbytes) - lost)
        cells = self.scan("short", 40)["cells"]
        self.assertEqual(cells[0]["s"], "equal")
        self.assertIn(cells[-1]["s"], {"uncovered", "short"})


class AnchorTest(unittest.TestCase):
    def test_a_repeated_window_is_never_chosen_as_the_anchor(self):
        """Aligning on an ambiguous anchor would be a silent, total error."""
        block = b"".join(struct.pack("<ii", (i % 1000) * 70000, (i % 1000) * 90000)
                         for i in range(2048))
        unique = b"".join(struct.pack("<ii", 500000 + i * 7, 900000 - i * 11)
                          for i in range(20000))
        ref = block + block + unique
        offset = BP.find_probe_offset(ref, unique_in=ref)
        self.assertEqual(ref.count(ref[offset:offset + 4096]), 1)
        self.assertGreaterEqual(offset, len(block) * 2 - 4096)

    def test_the_generated_asset_anchors_at_the_very_start(self):
        """Its whole point: every window is unique, so nothing is skipped."""
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "a.wav"
            counter_wav(wav)
            ref = Path(d) / "a.raw"
            run_lib("prep", wav, ref)
            data = ref.read_bytes()
            self.assertEqual(BP.find_probe_offset(data, unique_in=data), 0)


if __name__ == "__main__":
    unittest.main()
