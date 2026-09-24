#!/usr/bin/env python3
"""The DR meter, and the job that fetches, measures and deletes a record.

The meter was checked sample-for-sample against dr14_tmeter's compute_dr14 on
a real Qobuz track (DR 9, peak -0.13 dB, RMS -10.89 dB from both). These tests
pin what that agreement rests on, with signals whose answer is known:

  - a steady sine has peak == RMS*sqrt(2), so its DR is 0 at any level;
  - one clipped transient cannot raise a track's DR, because the reference
    peak is the second-highest block peak;
  - the loudest 20% of blocks set the RMS, so quiet passages lift DR.

And the job's promise: nothing it downloads outlives it, whatever happens.
"""
import importlib.util
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

import drmeter  # noqa: E402

SPEC = importlib.util.spec_from_file_location("omdrc_drmeter_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)

RATE = 48000


def run(signal, rate=RATE):
    meter = drmeter.Meter(rate, signal.shape[1])
    for start in range(0, len(signal), meter.block):
        meter.feed(signal[start:start + meter.block])
    return meter.result()


def sine(seconds, amplitude=1.0, rate=RATE):
    t = np.arange(int(seconds * rate)) / rate
    wave_ = amplitude * np.sin(2 * np.pi * 997 * t)
    return np.column_stack([wave_, wave_])


class MeterTest(unittest.TestCase):
    def test_a_steady_sine_has_no_dynamic_range(self):
        for amplitude in (1.0, 0.5, 0.1):
            got = run(sine(30, amplitude))
            self.assertEqual(got["dr"], 0)
            self.assertAlmostEqual(got["dr_exact"], 0.0, places=1)
            self.assertAlmostEqual(got["peak_db"], got["rms_db"], places=1)

    def test_one_clipped_transient_does_not_raise_it(self):
        signal = sine(30, 0.5)
        signal[RATE * 10] = 1.0
        self.assertEqual(run(signal)["dr"], 0)

    def test_quiet_passages_with_loud_peaks_give_range(self):
        # 20% of blocks are a full-scale sine and set both rms_upper and the
        # peak; the other 80% are quiet. Two loud blocks -> peak2 = 1.0 and
        # rms_upper = 1.0 -> DR 0 ... unless the loud blocks carry peaks well
        # above their RMS. Give every block one full-scale spike and make the
        # bodies quiet: DR = 20*log10(1 / rms_upper).
        blocks = []
        for i in range(10):
            body = sine(3, 0.1 if i >= 2 else 0.25)
            body[100] = 1.0                      # a full-scale spike per block
            blocks.append(body)
        got = run(np.vstack(blocks))
        expected = 20 * math.log10(1.0 / 0.25)   # ~12.04
        self.assertAlmostEqual(got["dr_exact"], expected, delta=0.1)
        self.assertEqual(got["dr"], 12)

    def test_44_1_khz_uses_the_reference_block_length(self):
        # 3 x 44 160, a quirk of the meter whose logs fill the database.
        self.assertEqual(drmeter.Meter(44100, 2).block, 3 * 44160)
        self.assertEqual(drmeter.Meter(96000, 2).block, 3 * 96000)

    def test_silence_is_not_an_error(self):
        got = run(np.zeros((RATE * 9, 2)))
        self.assertEqual(got["dr"], 0)

    def test_nothing_fed_is_an_error(self):
        with self.assertRaises(drmeter.DrMeterError):
            drmeter.Meter(RATE, 2).result()

    def test_album_value_is_the_rounded_mean_of_track_values(self):
        self.assertEqual(drmeter.album_dr([7, 8, 9, 8, 8]), 8)
        self.assertEqual(drmeter.album_dr([8, 9]), 8)   # 8.5 -> 8, half to even
        self.assertEqual(drmeter.album_dr([9, 10]), 10)  # 9.5 -> 10
        self.assertIsNone(drmeter.album_dr([]))


class RollingEstimateTest(unittest.TestCase):
    def test_matches_the_full_meter_for_the_same_complete_blocks(self):
        audio = sine(12, 0.25)
        for second in (1, 4, 7, 10):
            audio[second * RATE] = 1.0
        estimate = drmeter.RollingEstimate(RATE, 2)
        for start in range(0, len(audio), 2048):
            estimate.feed(audio[start:start + 2048])
        self.assertEqual(estimate.result()["dr"], run(audio)["dr"])
        self.assertEqual(estimate.result()["seconds"], 12)

    def test_waits_for_two_blocks_and_keeps_only_the_recent_minute(self):
        estimate = drmeter.RollingEstimate(100, 2)
        block = sine(3, 0.25, rate=100)
        block[10] = 1.0
        estimate.feed(block)
        self.assertIsNone(estimate.result())
        for _ in range(19):
            estimate.feed(block)
        self.assertGreater(estimate.result()["dr"], 10)
        for _ in range(20):
            estimate.feed(sine(3, 1.0, rate=100))
        self.assertEqual(estimate.result()["dr"], 0)
        self.assertEqual(estimate.result()["seconds"], 60)

    def test_history_retains_twenty_minutes_of_block_statistics(self):
        estimate = drmeter.RollingEstimate(100, 2, seconds=1200)
        block = sine(3, 0.25, rate=100)
        for _ in range(401):
            estimate.feed(block)
        history = estimate.history()
        self.assertEqual(len(history), 400)
        self.assertEqual(len(history[0]), 2)
        self.assertEqual(len(history[0][0]), 2)
        self.assertEqual(estimate.result()["seconds"], 1200)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "ffmpeg not installed")
class DecodeTest(unittest.TestCase):
    """measure_file end to end: a WAV decoded by ffmpeg at its native rate."""

    def test_a_file_measures_as_its_samples_do(self):
        signal = sine(12, 0.5)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tone.wav")
            with wave.open(path, "wb") as out:
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(RATE)
                out.writeframes((signal * 32767).astype("<i2").tobytes())
            got = drmeter.measure_file(path)
        self.assertEqual((got["rate"], got["channels"]), (RATE, 2))
        self.assertEqual(got["dr"], 0)
        self.assertAlmostEqual(got["peak_db"], -6.02, delta=0.05)
        self.assertAlmostEqual(got["seconds"], 12.0, delta=0.1)

    def test_a_file_that_is_not_audio_is_refused(self):
        with tempfile.NamedTemporaryFile(suffix=".flac") as bogus:
            bogus.write(b"not audio at all")
            bogus.flush()
            with self.assertRaises(drmeter.DrMeterError):
                drmeter.measure_file(bogus.name)


class JobTest(unittest.TestCase):
    """Nothing the job downloads outlives it: each track is deleted once
    measured, and the work directory goes whatever happens."""

    TRACKS = [{"url": f"http://cdn.invalid/{i}.flac", "title": f"Song {i}",
               "track": i, "disc": None} for i in (1, 2, 3)]

    def setUp(self):
        self.parent = tempfile.mkdtemp()
        self.seen = []

    def tearDown(self):
        shutil.rmtree(self.parent, ignore_errors=True)

    def download(self, url, dest, progress, cancelled):
        with open(dest, "wb") as out:
            out.write(b"x" * 1000)
        progress(0.5)
        progress(1.0)
        # Every earlier track must already be gone: one on disk at a time.
        self.seen.append(sorted(os.listdir(os.path.dirname(dest))))

    def job(self, measure, tracks=None):
        return drmeter.AlbumMeasurement("Record", "", tracks or self.TRACKS,
                                        download=self.download, measure=measure,
                                        workdir_parent=self.parent)

    def test_every_track_is_measured_and_the_album_averaged(self):
        values = iter([{"dr": 8, "dr_exact": 8.1, "peak_db": 0, "rms_db": -9},
                       {"dr": 9, "dr_exact": 9.2, "peak_db": 0, "rms_db": -10},
                       {"dr": 9, "dr_exact": 8.9, "peak_db": 0, "rms_db": -10}])
        job = self.job(lambda path: next(values))
        job.run()
        state = job.state()
        self.assertEqual(state["status"], "done")
        self.assertEqual([t["dr"] for t in state["tracks"]], [8, 9, 9])
        self.assertEqual(state["album_dr"], 9)
        self.assertEqual(state["fraction"], 1.0)

    def test_the_album_value_builds_as_tracks_come_in(self):
        """The card shows "album so far" while the job runs: it must hold a
        value from the first measured track on, not only at the end."""
        seen = []
        values = iter([{"dr": 9}, {"dr": 7}, {"dr": 8}])

        def measure(path):
            seen.append(job.state()["album_dr"])
            return next(values)

        job = self.job(measure)
        job.run()
        # Read before each track is measured: nothing yet, then 9, then 8.
        self.assertEqual(seen, [None, 9, 8])

    def test_one_track_on_disk_at_a_time_and_nothing_left_after(self):
        job = self.job(lambda path: {"dr": 8})
        job.run()
        self.assertEqual(self.seen, [["track-001"], ["track-002"], ["track-003"]])
        self.assertEqual(os.listdir(self.parent), [])

    def test_a_track_that_fails_does_not_sink_the_rest(self):
        def measure(path):
            if path.endswith("track-002"):
                raise RuntimeError("could not decode")
            return {"dr": 10}

        job = self.job(measure)
        job.run()
        state = job.state()
        self.assertEqual([t["status"] for t in state["tracks"]],
                         ["done", "failed", "done"])
        self.assertEqual(state["album_dr"], 10)
        self.assertIn("1 failed", state["message"])
        self.assertEqual(os.listdir(self.parent), [])

    def test_cancelling_stops_and_still_cleans_up(self):
        job = self.job(lambda path: job.cancel() or {"dr": 8})
        job.run()
        state = job.state()
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual([t["status"] for t in state["tracks"]],
                         ["done", "waiting", "waiting"])
        self.assertEqual(os.listdir(self.parent), [])

    def test_a_crash_mid_job_still_removes_the_work_directory(self):
        def download(url, dest, progress, cancelled):
            with open(dest, "wb") as out:
                out.write(b"partial")
            raise OSError("connection reset")

        job = drmeter.AlbumMeasurement("Record", "", self.TRACKS[:1],
                                       download=download,
                                       measure=lambda path: {"dr": 8},
                                       workdir_parent=self.parent)
        job.run()
        self.assertEqual(os.listdir(self.parent), [])
        self.assertEqual(job.state()["tracks"][0]["status"], "failed")

    def test_the_state_carries_no_url(self):
        job = self.job(lambda path: {"dr": 8})
        job.run()
        self.assertNotIn("cdn.invalid", str(job.state()))

    def test_discs_of_a_set_get_their_own_value(self):
        tracks = [{"url": f"http://cdn.invalid/{i}", "title": "", "track": 1,
                   "disc": disc} for i, disc in enumerate((1, 1, 2))]
        values = iter([{"dr": 13}, {"dr": 13}, {"dr": 14}])
        job = self.job(lambda path: next(values), tracks)
        job.run()
        self.assertEqual(job.state()["discs"], {"1": 13, "2": 14})


class DownloadRetryTest(unittest.TestCase):
    """The first live run lost its last track to one stalled CDN connection.
    A stall is retried, and the retry resumes rather than starting over."""

    BODY = bytes(range(256)) * 400          # 102 400 bytes

    class Response:
        def __init__(self, data, status=200, total=None, stall_after=None):
            self.status = status
            self.headers = {"Content-Length": str(len(data))}
            self._data, self._pos, self._stall = data, 0, stall_after

        def read(self, n):
            if self._stall is not None and self._pos >= self._stall:
                raise TimeoutError("timed out")
            chunk = self._data[self._pos:self._pos + n]
            self._pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fetch(self, responses):
        requests = []

        def urlopen(request, timeout=None):
            requests.append(dict(request.header_items()))
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "t")
            with patch("urllib.request.urlopen", urlopen), \
                 patch.object(APP.time, "sleep", lambda s: None), \
                 patch.object(APP, "_DRMETER_READ", 4096):
                try:
                    APP._drmeter_download("http://cdn.invalid/t.flac", dest,
                                          lambda f: None, lambda: False)
                    with open(dest, "rb") as got:
                        return got.read(), requests, None
                except RuntimeError as error:
                    return None, requests, error

    def test_a_stall_is_resumed_with_a_range_request(self):
        first = self.Response(self.BODY, stall_after=40960)
        rest = self.Response(self.BODY[40960:], status=206)
        data, requests, error = self.fetch([first, rest])
        self.assertIsNone(error)
        self.assertEqual(data, self.BODY)
        self.assertNotIn("Range", requests[0])
        self.assertEqual(requests[1]["Range"], "bytes=40960-")

    def test_a_server_that_ignores_the_range_restarts_the_track(self):
        first = self.Response(self.BODY, stall_after=40960)
        whole = self.Response(self.BODY, status=200)
        data, _requests, error = self.fetch([first, whole])
        self.assertIsNone(error)
        self.assertEqual(data, self.BODY)       # not the first 40 KB twice

    def test_it_gives_up_after_the_last_attempt(self):
        stalls = [self.Response(self.BODY, stall_after=0)
                  for _ in range(APP.DRMETER_ATTEMPTS)]
        data, requests, error = self.fetch(stalls)
        self.assertIsNone(data)
        self.assertIn("after", str(error))
        self.assertEqual(len(requests), APP.DRMETER_ATTEMPTS)


class TrackSelectionTest(unittest.TestCase):
    """The record is the unbroken run of queue entries carrying exactly the
    playing track's album tag -- not a cleaned-up version of it."""

    def queue(self, albums):
        return [{"file": f"http://host/{i}.flac", "title": f"T{i}",
                 "album": album, "track": i} for i, album in enumerate(albums)]

    def test_the_run_around_the_playing_track(self):
        entries = self.queue(["A", "B", "B", "B", "C"])
        got = APP._drmeter_tracks(entries, 2)
        self.assertEqual([t["title"] for t in got], ["T1", "T2", "T3"])

    def test_two_editions_of_one_record_are_not_mixed(self):
        # The live case: both hold a "Carol (Live)", and one measures DR 8,
        # the other DR 9.
        entries = self.queue(["Get Yer Ya-Ya's Out!"] * 2
                             + ["Get Yer Ya-Ya's Out! (40th Anniversary Deluxe Edition)"] * 2)
        got = APP._drmeter_tracks(entries, 0)
        self.assertEqual(len(got), 2)

    def test_disc_numbers_are_split_back_apart(self):
        entries = self.queue(["Set"] * 2)
        entries[0]["track"], entries[1]["track"] = 1005, 2003
        got = APP._drmeter_tracks(entries, 0)
        self.assertEqual([(t["disc"], t["track"]) for t in got], [(1, 5), (2, 3)])

    def test_nothing_playing_selects_nothing(self):
        self.assertEqual(APP._drmeter_tracks(self.queue(["A"]), None), [])

    def test_the_payload_never_carries_a_track_url(self):
        job = drmeter.AlbumMeasurement(
            "A", "", APP._drmeter_tracks(self.queue(["A", "A"]), 0),
            download=lambda *a: None, measure=lambda p: {"dr": 8})
        with patch.object(APP, "_DR_JOB", job):
            body = APP.app.test_client().get("/dr/measure").get_data(as_text=True)
        self.assertNotIn("http://host/", body)


if __name__ == "__main__":
    unittest.main()
