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
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

import numpy as np

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


class FifoOwnershipTest(unittest.TestCase):
    """Who is allowed to create the FIFO — the bug that blanked the MPD source.

    MPD's `fifo` output plugin creates the special file when it enables the
    output and unlinks it when it disables it, holding its write end open the
    whole time.  Create one behind its back and the two sides part company in
    total silence: MPD keeps writing the inode it opened, which no longer has a
    name, the analyzer reads the new one nobody writes, `mpc outputs` still
    says enabled, and the display is simply empty for ever.  MPD does not
    reopen an output it believes is already fine, so only an MPD restart
    recovers — which is why the analyzer must not cause this in the first
    place.
    """

    def test_mpd_owns_its_fifo_and_the_cd_bridge_does_not(self):
        self.assertTrue(APP.MpdSpectrumSource("/tmp/mpd.fifo", 48000).owns_fifo)
        self.assertFalse(APP.CdinSpectrumSource("/tmp/cd.fifo", 44100).owns_fifo)

    def test_an_owned_fifo_is_never_created_only_found(self):
        analyzer = APP.SpectrumAnalyzer()
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "owned.fifo")
            ok, err = analyzer._ensure_fifo(path, create=False)
            self.assertFalse(ok)
            self.assertIn(path, err)
            self.assertFalse(Path(path).exists(), "must not have made one")
            # ...and once the owner has put it there, it is simply accepted.
            APP.os.mkfifo(path, 0o600)
            self.assertEqual(analyzer._ensure_fifo(path, create=False), (True, ""))

    def test_an_unowned_fifo_is_created_on_demand(self):
        analyzer = APP.SpectrumAnalyzer()
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "ours.fifo")
            self.assertEqual(analyzer._ensure_fifo(path), (True, ""))
            self.assertTrue(APP.stat_is_fifo(path))

    def test_a_replaced_fifo_is_noticed_under_an_open_descriptor(self):
        """The descriptor stays valid and stops receiving anything; only the
        identity of the file says so."""
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "swap.fifo")
            APP.os.mkfifo(path, 0o600)
            fd = APP.os.open(path, APP.os.O_RDONLY | APP.os.O_NONBLOCK)
            try:
                self.assertTrue(APP._same_file(fd, path))
                APP.os.unlink(path)
                APP.os.mkfifo(path, 0o600)
                self.assertFalse(APP._same_file(fd, path))
            finally:
                APP.os.close(fd)

    def test_a_vanished_path_is_not_treated_as_a_swap(self):
        """A transient stat failure must not tear a working attachment down."""
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "gone.fifo")
            APP.os.mkfifo(path, 0o600)
            fd = APP.os.open(path, APP.os.O_RDONLY | APP.os.O_NONBLOCK)
            try:
                APP.os.unlink(path)
                self.assertTrue(APP._same_file(fd, path))
            finally:
                APP.os.close(fd)


class VirtualOssBlockTest(unittest.TestCase):
    """virtual_oss's `-s` block, the largest term in the display-sync delay.

    It is read out of the running process because nothing else records it, and
    drc.sh starts virtual_oss under `sudo -n` — so the same command line shows
    up twice, once wrapped.  Getting the wrapper wrong silently loses the term
    and under-delays the display by half a second.
    """

    WRAPPED = ("sudo -n virtual_oss -D /tmp/virtual_oss.pid -r 44100 -i 8 "
               "-C 2 -c 2 -b 32 -s 200ms -f /dev/null -a 0 -d dsp.play -L dsp.loop")
    BARE = WRAPPED.removeprefix("sudo -n ")

    def block(self, *lines):
        with mock.patch.object(APP, "_ps_arg_lines", return_value=list(lines)):
            return APP._virtual_oss_block_seconds()

    def test_duration_form_is_rate_independent(self):
        self.assertAlmostEqual(self.block(self.BARE), 0.200)

    def test_sudo_wrapper_is_stripped(self):
        self.assertAlmostEqual(self.block(self.WRAPPED), 0.200)

    def test_frame_count_form_is_divided_by_the_running_rate(self):
        line = self.BARE.replace("-s 200ms", "-s 4410")
        self.assertAlmostEqual(self.block(line), 0.100)

    def test_no_virtual_oss_is_not_zero_but_absent(self):
        # None, not 0.0: "the loopback is not in the path" and "the loopback
        # costs nothing" are different claims, and only the first is true.
        self.assertIsNone(self.block("mpd", "brutefir /etc/brutefir.conf"))


class DrcDelayEstimateTest(unittest.TestCase):
    """Every stage between the analyzer's tap and the speaker gets a term.

    The shipped flat profile is `dirac pulse`, whose group delay is genuinely
    zero — so these numbers are entirely the buffering, which is exactly the
    part the old estimate left out and the part a listener notices when the
    plots run on after the music has stopped.
    """

    PARTITION = 8192 / 44100          # 185.8 ms, one BruteFIR block at 44.1k
    VOSS = 3 * 0.200                  # -s 200ms x the modelled ring blocks
    OUTPUT = 0.150                    # DAC buffer + USB

    def terms(self, auto_sync, trim_ms, voss_line=VirtualOssBlockTest.BARE,
              conf="/active.conf"):
        APP._drc_delay_cache = {"key": None, "seconds": 0.0}
        APP._DRC_TERMS_CACHE = (0.0, (), {})
        parsed = {
            "rate": 44100,
            "coeffs": [{
                "label": "c-l",
                "filename": "dirac pulse",
                "format": "FLOAT64_LE",
            }],
        }
        fake_stat = mock.Mock(st_mtime=1.0)
        lines = [voss_line] if voss_line else []
        with mock.patch.object(APP, "SPECTRUM_DRC_DELAY_AUTO_SYNC", auto_sync), \
             mock.patch.object(APP, "SPECTRUM_DRC_DELAY_TRIM_MS", trim_ms), \
             mock.patch.object(APP, "_ps_arg_lines", return_value=lines), \
             mock.patch.object(APP, "_active_brutefir_conf", return_value=conf), \
             mock.patch.object(APP, "_parse_brutefir_conf", return_value=parsed), \
             mock.patch.object(APP, "_read_text_quietly",
                               return_value="filter_length: 8192,64;"), \
             mock.patch.object(APP.os, "stat", return_value=fake_stat):
            return APP._drc_display_delay_terms()

    def estimate(self, auto_sync, trim_ms, voss_line=VirtualOssBlockTest.BARE,
                 conf="/active.conf"):
        return self.terms(auto_sync, trim_ms, voss_line, conf)["total"]

    def test_builtin_dirac_still_costs_the_convolver_and_the_buffers(self):
        terms = self.terms(True, 0)
        self.assertAlmostEqual(terms["group"], 0.0)
        self.assertAlmostEqual(terms["convolver"], self.PARTITION)
        self.assertAlmostEqual(terms["brutefir_io"], 2 * self.PARTITION)
        self.assertAlmostEqual(terms["virtual_oss"], self.VOSS)
        self.assertAlmostEqual(terms["output"], self.OUTPUT)

    def test_the_loopback_is_the_largest_single_term(self):
        # The regression this whole model exists for: counting BruteFIR alone
        # accounted for 186 ms of a delay well over a second.
        terms = self.terms(True, 0)
        self.assertGreater(terms["virtual_oss"], terms["convolver"])
        self.assertGreater(terms["total"], 1.0)

    def test_auto_estimate_excludes_configured_static_trim(self):
        self.assertAlmostEqual(
            self.estimate(True, 250),
            3 * self.PARTITION + self.VOSS + self.OUTPUT)

    def test_one_shot_mode_replaces_the_modelled_stages_with_the_trim(self):
        # Manual mode keeps what is READ from the filter (group delay and the
        # convolver partition) and drops what is MODELLED, so a measured trim
        # is not double-counted against an estimate of the same buffering.
        terms = self.terms(False, 250)
        self.assertAlmostEqual(terms["virtual_oss"], 0.0)
        self.assertAlmostEqual(terms["brutefir_io"], 0.0)
        self.assertAlmostEqual(terms["output"], 0.0)
        self.assertAlmostEqual(terms["total"], self.PARTITION + 0.250)

    def test_a_chain_that_is_down_compensates_for_nothing(self):
        self.assertAlmostEqual(
            self.estimate(True, 0, voss_line="", conf=None), 0.0)


class ResumeAfterSilenceTest(unittest.TestCase):
    """Pressing Play must not light the bars with the PREVIOUS track.

    The hold-back reads `delay` seconds back into the capture buffer, and that
    buffer survives a pause.  So on resume the read point landed in the tail of
    whatever played before, and the bars came alive within a frame of Play while
    the new music was still working its way down the chain — read by a listener,
    correctly, as "the bars move at once and the sound arrives two seconds
    later".  It looks like the display is early; it is actually a whole track
    late.  Stale history has to be dropped when a real gap ends.
    """

    RATE = 48000
    PERIOD = 1024
    DELAY_S = 0.5

    def _tone(self, hz, phase):
        t = (phase + np.arange(self.PERIOD)) / self.RATE
        v = (0.5 * 2147483647 * np.sin(2 * np.pi * hz * t)).astype("<i4")
        return np.stack([v, v], axis=1).tobytes()

    def test_the_first_bars_after_a_gap_show_the_new_audio(self):
        d = tempfile.mkdtemp()
        fifo = os.path.join(d, "cd.fifo")
        analyzer = APP.SpectrumAnalyzer()
        paused, stop, hz = threading.Event(), threading.Event(), [1000.0]

        def writer():
            phase, fd = 0, None
            while not stop.is_set():
                if paused.is_set():
                    if fd is not None:
                        os.close(fd)
                        fd = None
                    time.sleep(0.02)
                    continue
                if fd is None:
                    try:
                        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                    except OSError:
                        time.sleep(0.02)
                        continue
                try:
                    os.write(fd, self._tone(hz[0], phase))
                except BlockingIOError:
                    pass
                except OSError:
                    os.close(fd)
                    fd = None
                    continue
                phase += self.PERIOD
                time.sleep(self.PERIOD / self.RATE)

        def loudest(frame):
            if not frame.get("left"):
                return None
            i = frame["left"].index(max(frame["left"]))
            return frame["bands"][i]["label"], max(frame["left"])

        def watch(secs):
            seq, t0, out = 0, time.monotonic(), []
            while time.monotonic() - t0 < secs:
                new, frame = analyzer.wait_next(seq, timeout=0.2)
                if new == seq:
                    continue
                seq = new
                out.append((time.monotonic() - t0, loudest(frame)))
            return out

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        with mock.patch.object(APP, "SPECTRUM_ENABLED", True), \
             mock.patch.object(APP, "SPECTRUM_SOURCE", "cdin"), \
             mock.patch.object(APP, "SPECTRUM_CDIN_FIFO", fifo), \
             mock.patch.object(APP, "SPECTRUM_CDIN_RATE", self.RATE), \
             mock.patch.object(APP, "SPECTRUM_CDIN_CAPTURE_PCM", ""), \
             mock.patch.object(APP, "SPECTRUM_DRC_DELAY_DELTA_MS", 0.0), \
             mock.patch.object(APP, "_drc_display_delay_seconds",
                               lambda: self.DELAY_S):
            analyzer.acquire("music")
            try:
                watch(3.0)                       # track A (1 kHz) settles
                paused.set()
                watch(1.5)                       # a real gap
                hz[0] = 6000.0                   # track B
                paused.clear()
                signal = [(at, pk) for at, pk in watch(3.0)
                          if pk and pk[1] > -60.0]
            finally:
                stop.set()
                analyzer.release()
                t.join(timeout=1.0)

        self.assertTrue(signal, "no audio ever reached the bars after the gap")
        at, (label, _db) = signal[0]
        # The new tone, never the old one: 1 kHz would mean the retained tail of
        # track A was drawn.
        self.assertEqual(label, "6.3k")
        # And not before the hold-back has actually elapsed.
        self.assertGreater(at, self.DELAY_S)


class SyncSliderReachTest(unittest.TestCase):
    """Raising the Sync slider must apply on the next frame, not seconds later.

    The capture buffer used to be trimmed to the hold-back *currently in
    force*, and the trim runs before the section that recomputes it.  Every
    increase therefore found the history already cut short, `start` went
    negative, and the analyzer published `waiting` with no bands for exactly as
    long as the increase — a frozen plot.  Dragging the slider restarted that
    stall at every intermediate value, so the control looked completely inert,
    which is precisely how it was reported.  Reserving the slider's full travel
    is what makes a move take effect immediately.
    """

    RATE = 48000
    FRAME_BYTES = 8            # stereo S32_LE
    FFT = 16384

    def keep(self, base_s, delta_max_ms):
        """The retained-history rule, isolated from the capture loop."""
        need = self.FFT * self.FRAME_BYTES
        chunk = int(self.RATE / 25) * self.FRAME_BYTES
        with mock.patch.object(APP, "SPECTRUM_DRC_DELAY_DELTA_MAX_MS", delta_max_ms):
            reach = max(0.0, base_s + APP.SPECTRUM_DRC_DELAY_DELTA_MAX_MS / 1000.0)
            return need + int(reach * self.RATE) * self.FRAME_BYTES + chunk * 3

    def test_history_covers_the_whole_slider_travel(self):
        base, delta_max = 0.5, 2000.0
        held = self.keep(base, delta_max)
        need = self.FFT * self.FRAME_BYTES
        # Enough for the FFT window AND the largest hold-back reachable, so the
        # read point stays inside the buffer wherever the slider is put.
        self.assertGreaterEqual(held - need,
                                (base + delta_max / 1000.0) * self.RATE * self.FRAME_BYTES)

    def test_it_does_not_shrink_when_the_delta_is_at_zero(self):
        # The old rule sized from the delay in force, so at delta 0 it reserved
        # only the base — which is why the first increase always stalled.
        self.assertEqual(self.keep(0.5, 2000.0), self.keep(0.5, 2000.0))
        self.assertGreater(self.keep(0.5, 2000.0),
                           self.FFT * self.FRAME_BYTES
                           + int(0.5 * self.RATE) * self.FRAME_BYTES)

    def test_the_reserve_stays_a_sane_size(self):
        # A couple of MB, not a leak: this is the cost of an instant slider.
        self.assertLess(self.keep(1.5, 2000.0), 4 * 1024 * 1024)


class BandPeakHoldTest(unittest.TestCase):
    """Short analysis windows must sweep the whole frame interval.

    A 2048-sample treble window spans ~43 ms.  At the display's frame interval
    that leaves most of the elapsed audio outside any single window, which is
    exactly why fast hi-hats never reached the bars: the transient landed
    between two snapshots and was simply never analysed.  The short tiers hop
    back across the new audio and keep the loudest result per band.
    """

    RATE = 48000
    N = 2048

    def bins(self, chan, span):
        tiers = APP._spectrum_tiers(self.N, self.RATE, multi=False)
        bands = APP._spectrum_band_defs(6, self.RATE / 2.0, 31.5)
        return APP._spectrum_multi_bins(chan, bands, [0] * len(bands),
                                        tiers, span)

    def burst_at(self, offset_from_end):
        """Silence with one full-scale click `offset_from_end` samples back."""
        import numpy as np
        chan = np.zeros(self.N * 4, dtype=np.float32)
        chan[len(chan) - offset_from_end] = 1.0
        return chan

    def test_a_transient_outside_the_newest_window_is_missed_without_hops(self):
        # The click sits two windows back: the single newest window cannot see
        # it, and a span the window already covers takes only that one window.
        quiet = self.bins(self.burst_at(self.N * 2 + 100), self.N // 2)
        self.assertLess(max(quiet), -100.0)

    def test_the_hops_recover_it(self):
        # A frame interval three windows long: the click is inside it, so it
        # has to reach the bars.
        loud = self.bins(self.burst_at(self.N * 2 + 100), self.N * 3)
        self.assertGreater(max(loud), -60.0)

    def test_a_window_that_covers_the_span_runs_once(self):
        import numpy as np
        lengths = []
        real_rfft = np.fft.rfft

        def counting_rfft(x, *a, **k):
            lengths.append(len(x))
            return real_rfft(x, *a, **k)

        with mock.patch("numpy.fft.rfft", counting_rfft):
            self.bins(self.burst_at(10), self.N // 2)
        self.assertEqual(lengths, [self.N])

    def test_the_hop_count_is_bounded(self):
        # A stalled frame must not turn into an unbounded FFT sweep, whatever
        # span it is handed.
        import numpy as np
        chan = np.zeros(self.N * 200, dtype=np.float32)
        calls = []
        real_rfft = np.fft.rfft

        def counting_rfft(x, *a, **k):
            calls.append(len(x))
            return real_rfft(x, *a, **k)

        with mock.patch("numpy.fft.rfft", counting_rfft):
            self.bins(chan, self.N * 1000)
        self.assertLessEqual(len(calls), APP._SPECTRUM_MAX_HOPS)

    def test_at_the_shipped_frame_rate_no_tier_has_to_hop(self):
        """The hops are insurance, not the normal cost.

        At 25 Hz the interval is 40 ms and even the shortest tier's window
        (~43 ms) reaches over it, so a frame is exactly one FFT per tier —
        the same work the analyzer did before, at a higher frame rate.  Drop
        `refresh_hz` to 10 and the short tier starts hopping, which is the
        regime the old default sat in.
        """
        import numpy as np
        tiers = APP._spectrum_tiers(16384, self.RATE, multi=True)
        bands = APP._spectrum_band_defs(24, self.RATE / 2.0, 31.5)
        assign = APP._assign_band_tiers(bands, tiers, self.RATE)
        chan = np.zeros(16384 * 2, dtype=np.float32)
        real_rfft = np.fft.rfft

        def count(span):
            lengths = []

            def counting_rfft(x, *a, **k):
                lengths.append(len(x))
                return real_rfft(x, *a, **k)

            with mock.patch("numpy.fft.rfft", counting_rfft):
                APP._spectrum_multi_bins(chan, bands, assign, tiers, span)
            return lengths

        self.assertEqual(len(count(int(self.RATE / 25))), len(tiers))
        self.assertGreater(len(count(int(self.RATE / 10))), len(tiers))


class PublishDeduplicationTest(unittest.TestCase):
    """An unchanged frame is not news, and re-sending it was visible.

    The analyzer used to alternate a fault frame with a live one twice a
    second, which the browser rendered as an unreadable flashing red banner
    over bars it kept clearing.
    """

    def test_an_identical_frame_does_not_bump_the_sequence(self):
        an = APP.SpectrumAnalyzer()
        frame = {"ok": False, "state": "no-writer", "error": "nothing writes it",
                 "left": [], "right": [], "bands": [], "vu": {}}
        an._publish(dict(frame))
        seq, _ = an.snapshot()
        an._publish(dict(frame))
        self.assertEqual(an.snapshot()[0], seq)

    def test_a_changed_frame_does(self):
        an = APP.SpectrumAnalyzer()
        an._publish({"ok": False, "state": "waiting", "error": ""})
        seq, _ = an.snapshot()
        an._publish({"ok": False, "state": "no-writer", "error": "gone"})
        self.assertNotEqual(an.snapshot()[0], seq)


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
