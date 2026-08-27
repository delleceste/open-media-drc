#!/usr/bin/env python3
"""Which producer the spectrum analyzer reads, and at what rate.

The analyzer is a FIFO reader that FFTs whatever arrives, so pointing it at the
CD bridge instead of MPD is a matter of choosing a FIFO and a sample rate. Both
choices fail *silently* when they are wrong, which is why they are pinned here:

  * pick MPD while a disc is playing and the display is simply blank, because
    CD audio never passes through MPD;
  * carry the wrong rate and every frequency bin is mislabelled — 44.1 kHz read
    as 48 kHz puts a 1 kHz tone at 1088 Hz — with nothing anywhere reporting an
    error.

The `[stats]` assertions cover the other half of the loop: the FreeBSD bridge
reports the tap only while a reader is attached, and the card must read that
back without disturbing the fields it already parses.
"""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "omdrc_spectrum_app", ROOT / "omdrc-ctrl/src/app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


class SourceSelectionTest(unittest.TestCase):
    """`auto` has to ask the chain, not a config file."""

    def setUp(self):
        # The CD-active probe is cached for a couple of seconds so that page
        # renders do not each tail the daemon's log; clear it so one test's
        # answer cannot leak into the next.
        APP._CDIN_ACTIVE_CACHE = (0.0, False)

    def resolve(self, source, cd_active, cdin_enabled=True):
        APP._CDIN_ACTIVE_CACHE = (0.0, False)
        with mock.patch.object(APP, "SPECTRUM_SOURCE", source), \
             mock.patch.object(APP, "CDIN_ENABLED", cdin_enabled), \
             mock.patch.object(APP, "_cdin_status",
                               return_value={"active": cd_active}):
            return APP._spectrum_resolve_source()

    def test_the_probe_is_cached_but_not_forever(self):
        """Cheap enough for every page render, fresh enough to follow a disc."""
        calls = []

        def status():
            calls.append(1)
            return {"active": True}

        APP._CDIN_ACTIVE_CACHE = (0.0, False)
        with mock.patch.object(APP, "CDIN_ENABLED", True), \
             mock.patch.object(APP, "_cdin_status", side_effect=status):
            for _ in range(20):
                self.assertTrue(APP._cdin_source_active())
            self.assertEqual(len(calls), 1, "should read the log once, not 20 times")
            # Expire it and the next caller must go back to the log.
            APP._CDIN_ACTIVE_CACHE = (APP.time.monotonic() - 60.0, True)
            APP._cdin_source_active()
            self.assertEqual(len(calls), 2)

    def test_auto_follows_the_disc(self):
        self.assertEqual(self.resolve("auto", cd_active=True).name, "cdin")
        self.assertEqual(self.resolve("auto", cd_active=False).name, "mpd")

    def test_auto_ignores_the_cd_when_the_bridge_is_disabled(self):
        """No bridge means no CD FIFO will ever be written; MPD is the only
        source that can produce anything at all."""
        self.assertEqual(
            self.resolve("auto", cd_active=True, cdin_enabled=False).name, "mpd")

    def test_an_explicit_choice_is_not_second_guessed(self):
        self.assertEqual(self.resolve("cdin", cd_active=False).name, "cdin")
        self.assertEqual(self.resolve("mpd", cd_active=True).name, "mpd")

    def test_a_broken_cdin_status_does_not_break_the_analyzer(self):
        """The CD log is read off disk and can be missing or malformed; that
        must degrade to 'no disc', not take the spectrum card down with it."""
        with mock.patch.object(APP, "SPECTRUM_SOURCE", "auto"), \
             mock.patch.object(APP, "CDIN_ENABLED", True), \
             mock.patch.object(APP, "_cdin_status", side_effect=OSError("no log")):
            self.assertEqual(APP._spectrum_resolve_source().name, "mpd")


class SourceRateTest(unittest.TestCase):
    """The rate travels with the source, because the two differ."""

    def setUp(self):
        APP._CDIN_ACTIVE_CACHE = (0.0, False)

    def test_each_source_carries_its_own_rate_and_fifo(self):
        with mock.patch.object(APP, "SPECTRUM_SOURCE", "cdin"), \
             mock.patch.object(APP, "SPECTRUM_CDIN_RATE", 44100), \
             mock.patch.object(APP, "SPECTRUM_CDIN_FIFO", "/tmp/cd.fifo"), \
             mock.patch.object(APP, "SPECTRUM_RATE", 48000), \
             mock.patch.object(APP, "SPECTRUM_FIFO", "/tmp/mpd.fifo"):
            cd = APP._spectrum_resolve_source()
            self.assertEqual((cd.rate, cd.fifo), (44100, "/tmp/cd.fifo"))
        with mock.patch.object(APP, "SPECTRUM_SOURCE", "mpd"), \
             mock.patch.object(APP, "SPECTRUM_RATE", 48000), \
             mock.patch.object(APP, "SPECTRUM_FIFO", "/tmp/mpd.fifo"):
            mpd = APP._spectrum_resolve_source()
            self.assertEqual((mpd.rate, mpd.fifo), (48000, "/tmp/mpd.fifo"))


class CdinSourceStartTest(unittest.TestCase):
    def test_without_a_capture_pcm_nothing_is_spawned(self):
        """FreeBSD, where omdrc-cdin tees the FIFO itself: opening the read end
        is the entire start protocol, so there is nothing for the panel to do."""
        src = APP.CdinSpectrumSource("/tmp/cd.fifo", 44100)
        with mock.patch.object(APP, "SPECTRUM_CDIN_CAPTURE_PCM", ""), \
             mock.patch.object(APP.subprocess, "Popen") as popen:
            ok, err = src.start()
        self.assertTrue(ok, err)
        popen.assert_not_called()
        src.stop()          # must be safe with nothing running

    def test_with_a_capture_pcm_arecord_feeds_the_fifo(self):
        """Linux, where alsaloop is opaque: the panel reads the capture a second
        time instead of teeing alsaloop's output."""
        src = APP.CdinSpectrumSource("/tmp/cd.fifo", 44100)
        proc = mock.Mock()
        proc.poll.return_value = None
        with mock.patch.object(APP, "SPECTRUM_CDIN_CAPTURE_PCM", "dsnoop_cdin"), \
             mock.patch.object(APP, "SPECTRUM_CHANNELS", 2), \
             mock.patch.object(APP.subprocess, "Popen", return_value=proc) as popen, \
             mock.patch.object(APP.time, "sleep"):
            ok, err = src.start()
        self.assertTrue(ok, err)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[:3], ["arecord", "-D", "dsnoop_cdin"])
        self.assertIn("S32_LE", argv)
        self.assertIn("44100", argv)
        self.assertEqual(argv[-1], "/tmp/cd.fifo")

    def test_a_bad_pcm_name_is_reported_not_left_on_waiting(self):
        """arecord dies immediately on an unknown PCM. Without this the page
        would sit on 'waiting' forever with no clue why."""
        src = APP.CdinSpectrumSource("/tmp/cd.fifo", 44100)
        proc = mock.Mock()
        proc.poll.return_value = 1
        proc.stderr.read.return_value = b"arecord: unknown PCM dsnoop_typo"
        with mock.patch.object(APP, "SPECTRUM_CDIN_CAPTURE_PCM", "dsnoop_typo"), \
             mock.patch.object(APP.subprocess, "Popen", return_value=proc), \
             mock.patch.object(APP.time, "sleep"):
            ok, err = src.start()
        self.assertFalse(ok)
        self.assertIn("dsnoop_typo", err)
        self.assertIn("unknown PCM", err)


class StatsFieldTest(unittest.TestCase):
    BASE = ("lead 1962 ms (min 1955, max 1972)  in 44100.206 Hz  "
            "out 44100.152 Hz  frames 4054016/3980288  drops 0 B  "
            "starves 0  silence 0%  up 125 s")

    def test_the_tap_is_absent_when_no_one_is_reading(self):
        f = APP._cdin_stats_fields(self.BASE)
        self.assertNotIn("spectrum_attached", f)
        self.assertEqual(f["starves"], 0)

    def test_the_tap_is_read_back_when_present(self):
        f = APP._cdin_stats_fields(
            self.BASE + "  spectrum 1411200 B dropped 4096 B")
        self.assertTrue(f["spectrum_attached"])
        self.assertEqual(f["spectrum_bytes"], 1411200)
        self.assertEqual(f["spectrum_dropped_bytes"], 4096)

    def test_the_new_field_does_not_disturb_the_old_ones(self):
        """`drops N B` and `dropped N B` are one word apart, and both end in
        ' B' — the tap's counter must not be read as the ring's."""
        f = APP._cdin_stats_fields(
            self.BASE + "  spectrum 1411200 B dropped 4096 B")
        self.assertEqual(f["drops_bytes"], 0)
        self.assertEqual(f["starves"], 0)
        self.assertEqual(f["lead_ms"], 1962)
        self.assertEqual(f["up_s"], 125)
        self.assertEqual(f["in_hz"], 44100.206)


if __name__ == "__main__":
    unittest.main()
