#!/usr/bin/env python3
"""Loading arbitrary material must not change what "bit-perfect" means.

The generated counter asset is safe by construction. Real tracks are not: they
arrive in containers, at widths the DAC does not take on the wire, and with
passages that repeat. Each of those is a way for a verdict to become wrong
without looking wrong, so each gets a test.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
MATERIAL = ROOT / "scripts/bitperfect_material.py"

SPEC = importlib.util.spec_from_file_location("bpmat", MATERIAL)
MAT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MAT)

HAVE_FLAC = shutil.which("flac") is not None


def counter_wav(path: Path, frames: int = 20000, rate: int = 44100,
                bits: int = 32) -> None:
    width = bits // 8
    buf = bytearray()
    for i in range(frames):
        buf += struct.pack("<i", i & 0xFFFF)[:width]
        buf += struct.pack("<i", (i * 40503 + (i >> 16)) & 0xFFFF)[:width]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(buf))


def load(path: Path, out: Path) -> dict:
    r = subprocess.run([sys.executable, str(MATERIAL), "load", str(path),
                        "--out-dir", str(out)], capture_output=True, text=True)
    return json.loads(r.stdout)


class LosslessRoundTripTest(unittest.TestCase):
    """A FLAC and the WAV it came from must yield the SAME reference bytes.

    If they did not, a FLAC verdict would be comparing against the wrong
    stream — and since FLAC is lossless, any difference is a bug in the
    loader, never in the material.
    """

    @unittest.skipUnless(HAVE_FLAC, "flac(1) not installed")
    def test_flac_and_wav_produce_identical_references(self):
        for bits in (16, 24):
            with self.subTest(bits=bits), tempfile.TemporaryDirectory() as d:
                d = Path(d)
                wav = d / f"m{bits}.wav"
                counter_wav(wav, bits=bits)
                flac = d / f"m{bits}.flac"
                subprocess.run(["flac", "-s", "-f", "-o", str(flac), str(wav)],
                               check=True, capture_output=True)
                from_wav = load(wav, d / "w")
                from_flac = load(flac, d / "f")
                self.assertTrue(from_wav["ok"] and from_flac["ok"])
                self.assertEqual(Path(from_wav["ref_raw"]).read_bytes(),
                                 Path(from_flac["ref_raw"]).read_bytes())
                # the format is read from the file, never assumed
                self.assertEqual(from_flac["bits"], bits)
                self.assertEqual(from_flac["rate"], 44100)
                self.assertEqual(from_flac["frames"], 20000)

    @unittest.skipUnless(HAVE_FLAC, "flac(1) not installed")
    def test_the_original_is_what_gets_played(self):
        """MPD's own decode is part of what is under test, so it must see the
        FLAC — not a WAV we decoded for it."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            wav = d / "m.wav"
            counter_wav(wav)
            flac = d / "m.flac"
            subprocess.run(["flac", "-s", "-f", "-o", str(flac), str(wav)],
                           check=True, capture_output=True)
            info = load(flac, d / "out")
            self.assertEqual(Path(info["play_path"]), flac)


class PromotionTest(unittest.TestCase):
    def test_narrow_material_is_promoted_into_the_32_bit_wire_container(self):
        """The DAC takes only 4-byte containers, so any bit-perfect player has
        to widen. The reference must model that, or every narrow-format run
        would mismatch on every sample."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            sizes = {}
            for bits in (16, 24, 32):
                wav = d / f"m{bits}.wav"
                counter_wav(wav, frames=1000, bits=bits)
                info = load(wav, d / f"o{bits}")
                sizes[bits] = info["ref_bytes"]
            self.assertEqual(set(sizes.values()), {1000 * 2 * 4})

    def test_promotion_is_a_left_shift_not_a_reinterpretation(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            wav = d / "m16.wav"
            counter_wav(wav, frames=8, bits=16)
            info = load(wav, d / "o")
            ref = Path(info["ref_raw"]).read_bytes()
            # frame 3, left channel: value 3 stored 16-bit -> 3 << 16
            left = struct.unpack_from("<i", ref, 3 * 8)[0]
            self.assertEqual(left, 3 << 16)


class AnchorReportingTest(unittest.TestCase):
    def test_repetitive_material_still_reports_a_unique_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            wav = d / "rep.wav"
            block = [struct.pack("<ii", (i % 1000) * 70000, (i % 1000) * 90000)
                     for i in range(2048)]
            tail = [struct.pack("<ii", 500000 + i * 7, 900000 - i * 11)
                    for i in range(20000)]
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(4)
                w.setframerate(44100)
                w.writeframes(b"".join(block * 2 + tail))
            info = load(wav, d / "o")
            self.assertTrue(info["anchor_unique"])
            self.assertGreater(info["anchor_offset"], 0)
            self.assertEqual(info["warning"], "")


class SegmentOrderTest(unittest.TestCase):
    def test_segments_join_numerically_not_lexically(self):
        """part10 must not land between part1 and part2: lexical order would
        scramble the stream and produce a fault that never happened."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            segments = d / "seg"
            segments.mkdir()
            for i in range(1, 13):
                (segments / f"part{i}").write_bytes(bytes([i]) * 4)
            out = d / "joined.bin"
            MAT.concat_segments(segments, out)
            self.assertEqual(out.read_bytes(),
                             b"".join(bytes([i]) * 4 for i in range(1, 13)))


class LossyLabellingTest(unittest.TestCase):
    def test_a_lossy_container_is_flagged_rather_than_called_bit_perfect(self):
        """The DAC legitimately receives the decoder's output, not the file's
        bytes, so the page must not claim bit-perfection of the file."""
        self.assertIn(".mp3", MAT.LOSSY_SUFFIXES)
        self.assertIn(".m4a", MAT.LOSSY_SUFFIXES)
        self.assertNotIn(".flac", MAT.LOSSY_SUFFIXES)
        self.assertNotIn(".wav", MAT.LOSSY_SUFFIXES)


class BufferHintTest(unittest.TestCase):
    """The live source's reference comes from the renderer's own buffer.

    The layout below is not invented: it is what a running qobuzconnect2mpd
    (pid 85852) actually had on this box —
        /tmp/qobuzconnect2mpd-1001/cache/track_101472988_7_85852_10.flac
    — a COMPLETE FLAC per track, one directory below the hint root.
    """

    def test_the_known_qobuz_buffer_paths_are_carried_with_their_kind(self):
        kinds = dict(MAT.BUFFER_HINTS)
        self.assertEqual(kinds["/tmp/qobuzconnect2mpd-"], "tracks")
        self.assertEqual(kinds["/tmp/qconnect2mpd-segmented"], "segments")

    def _fake_cache(self, root: Path, name: str, age: float) -> Path:
        import os
        import time
        cache = root / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        track = cache / name
        track.write_bytes(b"x")
        os.utime(track, (time.time() - age, time.time() - age))
        return track

    def test_a_track_nested_under_cache_is_found(self):
        """The real files sit in cache/, not in the hint root — listing only
        the root (which holds nothing but directories) would find nothing."""
        import time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "qobuzconnect2mpd-1001"
            fresh = self._fake_cache(root, "track_101472988_7_85852_10.flac", 5)
            original = MAT.BUFFER_HINTS
            try:
                MAT.BUFFER_HINTS = ((str(Path(d) / "qobuzconnect2mpd-"), "tracks"),)
                found = MAT.scan_buffer_hints(time.time() - 60)
            finally:
                MAT.BUFFER_HINTS = original
            self.assertEqual(found, [(str(fresh), "tracks")])

    def test_a_stale_buffer_is_not_offered_as_a_reference(self):
        """This cache really did hold two completed tracks hours old.
        Comparing a capture against one of those would report a fault that
        never happened."""
        import time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "qobuzconnect2mpd-1001"
            stale = self._fake_cache(root, "track_101472987_7_85852_5.flac", 7200)
            original = MAT.BUFFER_HINTS
            try:
                MAT.BUFFER_HINTS = ((str(Path(d) / "qobuzconnect2mpd-"), "tracks"),)
                self.assertEqual(MAT.scan_buffer_hints(time.time() - 60), [])
                self.assertEqual(MAT.scan_buffer_hints(time.time() - 10800),
                                 [(str(stale), "tracks")])
            finally:
                MAT.BUFFER_HINTS = original

    def test_the_newest_track_wins_when_several_are_cached(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "qobuzconnect2mpd-1001"
            self._fake_cache(root, "track_1_7_1_1.flac", 300)
            newest = self._fake_cache(root, "track_2_7_1_2.flac", 5)
            original = MAT.BUFFER_HINTS
            try:
                MAT.BUFFER_HINTS = ((str(Path(d) / "qobuzconnect2mpd-"), "tracks"),)
                found = MAT.scan_buffer_hints(time.time() - 600)
            finally:
                MAT.BUFFER_HINTS = original
            self.assertEqual(found[0], (str(newest), "tracks"))

    def test_a_tracks_directory_is_never_concatenated(self):
        """Splicing two unrelated songs together would manufacture corruption.
        Only the explicitly segmented hint is ever joined."""
        import time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "qobuzconnect2mpd-1001"
            self._fake_cache(root, "track_1_7_1_1.flac", 5)
            self._fake_cache(root, "track_2_7_1_2.flac", 5)
            original = MAT.BUFFER_HINTS
            try:
                MAT.BUFFER_HINTS = ((str(Path(d) / "qobuzconnect2mpd-"), "tracks"),)
                found = MAT.scan_buffer_hints(time.time() - 60)
            finally:
                MAT.BUFFER_HINTS = original
            self.assertTrue(found)
            for path, kind in found:
                self.assertEqual(kind, "tracks")
                self.assertTrue(Path(path).is_file())


if __name__ == "__main__":
    unittest.main()
