#!/usr/bin/env python3
"""The Linux CD input: the supervisor, the roles it reads, and the arbitration.

Three things are pinned here, chosen because each of them fails silently:

* the supervisor emits the log grammar the web panel's CD input card parses.
  Nothing crashes when it does not — the card just shows a bridge that is
  running and has nothing to say, which is indistinguishable from a healthy
  one that has not spoken yet.
* the capture role survives the round trip from a USB identity to an ALSA card
  number.  A wrong number opens the DAC's own capture side and records
  silence; there is no error anywhere in that path.
* `drc.sh` never leaves both MPD and the bridge pointed at hw:Loopback,0,0.
  One seat, and the loser gets EBUSY at the moment someone presses Play.
"""

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "scripts/omdrc-cdin-alsaloop"
HELPER = ROOT / "scripts/omdrc-config-helper.py"
DRC = ROOT / "drc.sh"


def load(path: Path, name: str):
    # The bridge ships without a .py suffix (it is a command, not a module), so
    # the loader has to be named rather than inferred from the extension.
    spec = importlib.util.spec_from_file_location(
        name, path, loader=importlib.machinery.SourceFileLoader(name, str(path)))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


# The panel's parser, imported from the panel rather than restated here: a test
# that carried its own copy of the regexes would keep passing while the two
# drifted apart, which is the exact failure it exists to catch.
APP = load(ROOT / "omdrc-ctrl/src/app.py", "omdrc_linux_app")
CDIN = load(BRIDGE, "omdrc_cdin_alsaloop")


class LogGrammarTest(unittest.TestCase):
    """Every line the supervisor writes must mean something to the card."""

    def lines(self, calls) -> list[str]:
        out = []
        for level, message in calls:
            now = "2026-08-26 10:00:00.001"
            out.append(f"{now} [{level}] {message}")
        return out

    def test_startup_lines_parse_as_the_card_expects(self):
        recorded = []
        log = lambda level, message: recorded.append((level, message))

        # The shapes the supervisor emits at startup, replayed through the
        # panel's own regexes.
        log("INF", "capture hw:2,0: available")
        log("INF", "playback hw:Loopback,0,0: available — the DRC chain is up")
        log("INF", "omdrc-cdin alsaloop starting: in=hw:2,0 "
                   "out=hw:Loopback,0,0 44100 Hz")
        log("INF", "playback hw:Loopback,0,0: acquired")
        log("INF", "state playing: capturing from hw:2,0")

        parsed = [APP._CDIN_LINE.match(line) for line in self.lines(recorded)]
        self.assertTrue(all(parsed), "a supervisor line is not in the log grammar")
        messages = [m.group("msg") for m in parsed]

        start = APP._CDIN_START.match(messages[2])
        self.assertIsNotNone(start, "the starting line must carry both ends")
        self.assertEqual(start.group("inpath"), "hw:2,0")
        self.assertEqual(start.group("outpath"), "hw:Loopback,0,0")

        device = APP._CDIN_DEVICE.match(messages[0])
        self.assertEqual((device.group("dev"), device.group("what")),
                         ("capture", "available"))
        held = APP._CDIN_DEVICE.match(messages[3])
        self.assertEqual((held.group("dev"), held.group("what")),
                         ("playback", "acquired"))

        state = APP._CDIN_STATE.match(messages[4])
        self.assertEqual(state.group("state"), "playing")

    def test_states_are_the_three_the_card_renders(self):
        """A fourth state would render as a bare "running" with no explanation."""
        source = BRIDGE.read_text()
        emitted = set(re.findall(r'set_state\("([a-z-]+)"', source))
        self.assertTrue(emitted <= {"playing", "idle", "no-carrier"},
                        f"unrenderable states: {emitted - {'playing', 'idle', 'no-carrier'}}")

    def test_stats_line_reduces_to_chips(self):
        class FakeDevice:
            spec = "hw:Loopback,0,0"
            card = 1

            def rate(self):
                return 44100.0

            def status(self):
                return {"delay": "8820"}      # 200 ms at 44.1k

        stats = CDIN.Stats(FakeDevice())
        stats.numid = "7"
        with mock.patch.object(CDIN, "rate_shift_ppm", return_value=-3.0):
            line = stats.line(FakeDevice())

        self.assertIsNotNone(APP._CDIN_STATS.match(line))
        fields = APP._cdin_stats_fields(APP._CDIN_STATS.match(line).group("body"))
        self.assertEqual(fields["lead_ms"], 200)
        self.assertEqual(fields["in_hz"], 44100.0)
        self.assertEqual(fields["out_hz"], 44100.0)
        self.assertEqual(fields["drift_ppm"], -3.0)
        self.assertEqual(fields["starves"], 0)

    def test_the_lead_is_published_with_the_target_it_is_held_at(self):
        """200 ms is what the Linux bridge ASKS for, so 187 ms is nominal and
        must not read as a drained buffer.  The card cannot know that without
        the target, and applying the FreeBSD daemon's absolute floor to it
        warned on every healthy run — a warning that is always on is one nobody
        reads when it finally means something."""
        class FakeDevice:
            spec = "hw:Loopback,0,0"
            card = 1

            def rate(self):
                return 44100.0

            def status(self):
                return {"delay": "8247"}      # 187 ms at 44.1k

        stats = CDIN.Stats(FakeDevice(), target_ms=200)
        stats.numid = "7"
        with mock.patch.object(CDIN, "rate_shift_ppm", return_value=0.0):
            line = stats.line(FakeDevice())
        body = APP._CDIN_STATS.match(line).group("body")
        fields = APP._cdin_stats_fields(body)
        self.assertEqual(fields["lead_ms"], 187)
        self.assertEqual(fields["lead_target_ms"], 200)

    def test_the_card_can_tell_a_corrected_disc_from_an_uncorrected_one(self):
        """"playing" reads the same either way, and which of the two you are
        hearing is decided by whether BruteFIR happened to be up when the
        bridge started — the one difference a listener can hear and cannot
        see.  It has to survive the round trip into the panel's own parser."""
        class FakeDevice:
            spec = "hw:Loopback,0,0"
            card = 1

            def rate(self):
                return 44100.0

            def status(self):
                return {"delay": "8820"}

        for path, summary in (("drc", "playing through DRC — audio on the wire"),
                              ("direct", "playing straight to the DAC — "
                                         "no room correction")):
            stats = CDIN.Stats(FakeDevice(), target_ms=200, path=path)
            stats.numid = "7"
            with mock.patch.object(CDIN, "rate_shift_ppm", return_value=0.0):
                line = stats.line(FakeDevice())
            fields = APP._cdin_stats_fields(APP._CDIN_STATS.match(line).group("body"))
            self.assertEqual(fields["path"], path)
            verdict = APP._cdin_verdict(
                {"running": True, "state": "playing", "stats_fields": fields,
                 "capture": {"available": True, "error": "", "label": "capture"},
                 "output": {"available": True, "error": "", "label": "output"}})
            self.assertEqual(verdict, ("green", summary))

    def test_the_digital_input_is_selected_rather_than_assumed(self):
        """An S/PDIF interface is usually also an analog one and boots on the
        analog input: the U24 XL's selector comes up on 'Line'.  Capturing that
        with nothing plugged in is a stream that is healthy in every measurable
        way and about -76 dBFS of converter noise — right rate, right format,
        no xruns, "audio on the wire", no error anywhere."""
        contents = ("numid=8,iface=MIXER,name='PCM Capture Source'\n"
                    "  ; type=ENUMERATED,access=rw------,values=1,items=2\n"
                    "  ; Item #0 'Line'\n"
                    "  ; Item #1 'IEC958 In'\n"
                    "  : values=0\n")
        with mock.patch.object(CDIN.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, contents, "")):
            controls = CDIN.mixer_enum_controls(2)
        self.assertEqual(controls[0]["items"], {0: "Line", 1: "IEC958 In"})
        control, index = CDIN.pick_capture_source(controls, "auto")
        self.assertEqual((control["numid"], index), ("8", 1))
        # Already on the digital input, or asked to keep hands off: nothing.
        controls[0]["value"] = 1
        self.assertIsNone(CDIN.pick_capture_source(controls, "auto"))
        # A card with no selector at all — the majority — is untouched.
        self.assertIsNone(CDIN.pick_capture_source(
            [{"numid": "1", "name": "Mic Boost", "items": {0: "a"}, "value": 0}],
            "auto"))

    def test_the_missing_realtime_priority_is_said_once_not_filed_as_an_error(self):
        """alsaloop prints this on every start when RLIMIT_RTPRIO is 0 — the
        default for an ordinary user — and it matches "FAILED", so the card
        collected one more red event per restart for a condition that costs no
        audio at all."""
        line = "!!!Scheduler set to Round Robin with priority 99 FAILED!"
        self.assertIsNotNone(CDIN._NO_REALTIME.search(line))
        # Without the interception it is an error, which is what made it noise.
        class FakeDevice:
            card = 1
        self.assertEqual(CDIN.classify(line, CDIN.Stats(FakeDevice())), "ERR")
        source = BRIDGE.read_text()
        self.assertIn("warned_no_realtime", source)

    def test_a_stopped_transport_is_not_audio_on_the_wire(self):
        """The ESI U24 XL slaves its clock to the S/PDIF carrier, so a stopped
        CD player does not stop the stream — it keeps it open and dribbles ~1%
        of the frame rate.  "Is the device streaming?" says yes to that, which
        is how the card came to report "playing — audio on the wire" over a
        stopped transport while its starve counter climbed.  hw_ptr against the
        wall clock is the only thing here that measures arrival."""
        log = lambda *args: None
        bridge = CDIN.Bridge(CDIN.parse_args([]), log)
        self.assertEqual(bridge.args.carrier_min, 50.0)

        class FakeCapture:
            spec = "hw:0,0"

            def __init__(self):
                self.ptr = 0

            def hw_ptr(self):
                return self.ptr

        capture = FakeCapture()
        seen: dict = {}
        with mock.patch.object(CDIN.time, "monotonic", side_effect=[0.0, 1.0, 2.0]):
            self.assertIsNone(bridge.carrier(capture, seen))   # first sample
            capture.ptr = 441                                  # 1% of nominal
            self.assertIs(bridge.carrier(capture, seen), False)
            capture.ptr = 441 + 44100                          # full rate
            self.assertIs(bridge.carrier(capture, seen), True)

    def test_the_carrier_check_can_be_turned_off(self):
        """0 disables it, the same knob FreeBSD spells omdrc_cdin_carrier_min."""
        log = lambda *args: None
        bridge = CDIN.Bridge(CDIN.parse_args(["--carrier-min", "0"]), log)
        self.assertIsNone(bridge.carrier(object(), {}))

    def test_carrier_loss_ends_alsaloop_instead_of_repeating_its_tail(self):
        """snd-aloop can circulate its queued tail after S/PDIF stops.  Once
        measured delivery falls below the carrier threshold, the current
        alsaloop process must release the playback side."""
        recorded = []
        bridge = CDIN.Bridge(CDIN.parse_args([]),
                             lambda level, message: recorded.append(message))
        bridge.carrier = mock.Mock(return_value=False)

        class Capture:
            spec = "hw:2,0"

            def hw_params(self):
                return {"rate": "44100"}

        child = mock.Mock()
        child.poll.return_value = None
        stats = mock.Mock()
        bridge.report(Capture(), object(), stats, CDIN.threading.Event(), child)
        child.terminate.assert_called_once_with()
        self.assertTrue(any("transport is not clocking" in line
                            for line in recorded))

    def test_no_lead_is_absent_rather_than_zero(self):
        """A closed output has no lead; reporting 0 ms would show a red buffer
        warning for a bridge that is merely starting up."""
        class Closed:
            spec = "hw:Loopback,0,0"
            card = 1

            def rate(self):
                return None

            def status(self):
                return {}

        stats = CDIN.Stats(Closed())
        body = APP._CDIN_STATS.match(stats.line(Closed())).group("body")
        self.assertNotIn("lead_ms", APP._cdin_stats_fields(body))

    def test_an_xrun_is_counted_as_a_starve(self):
        """alsaloop's own wording is not a stable interface, so only the sense
        is matched.  alsaloop cannot tell whether the samples are silent, so
        preserve the event as a warning rather than claiming it was audible."""
        class Nothing:
            spec = ""
            card = None

            def rate(self):
                return None

            def status(self):
                return {}

        stats = CDIN.Stats(Nothing())
        self.assertEqual(CDIN.classify("Playback: xrun detected", stats), "WRN")
        self.assertEqual(stats.starves, 1)
        self.assertEqual(CDIN.classify("Loop thread started", stats), "INF")
        self.assertEqual(stats.starves, 1)

    def test_an_xrun_while_alsaloop_primes_is_not_a_dropout(self):
        """The playback side can underrun while alsaloop fills and locks its
        buffers.  There was no established stream to interrupt, so the raw
        diagnostic stays informational and the persistent counter stays zero.
        """
        class Nothing:
            spec = ""
            card = None

            def rate(self):
                return None

            def status(self):
                return {}

        with mock.patch.object(CDIN.time, "monotonic", return_value=100.0):
            stats = CDIN.Stats(Nothing(), xrun_grace=10)
        with mock.patch.object(CDIN.time, "monotonic", return_value=109.9):
            self.assertEqual(CDIN.classify("underrun for playback", stats), "INF")
        self.assertEqual(stats.starves, 0)
        with mock.patch.object(CDIN.time, "monotonic", return_value=110.0):
            self.assertEqual(CDIN.classify("underrun for playback", stats), "WRN")
        self.assertEqual(stats.starves, 1)


class DeviceResolutionTest(unittest.TestCase):
    def test_device_names_split_into_card_device_subdevice(self):
        with mock.patch.object(CDIN, "card_number", return_value=1):
            for spec, want in (("hw:Loopback,0,0", (1, 0, 0)),
                               ("hw:1,0", (1, 0, 0)),
                               ("hw:2,1,3", (1, 1, 3)),
                               ("hw:Loopback", (1, 0, 0))):
                device = CDIN.Device(spec, "p")
                self.assertEqual((device.card, device.device, device.sub), want, spec)

    def test_proc_path_follows_the_direction(self):
        with mock.patch.object(CDIN, "card_number", return_value=1):
            self.assertEqual(str(CDIN.Device("hw:1,0", "c").proc),
                             "/proc/asound/card1/pcm0c/sub0")
            self.assertEqual(str(CDIN.Device("hw:1,0", "p").proc),
                             "/proc/asound/card1/pcm0p/sub0")

    def test_output_follows_the_chain(self):
        """The loopback while BruteFIR is up, the DAC straight when it is not —
        the same preference cdin/src/outsel.h applies, for the same reason:
        writing into a loopback nothing reads is silence."""
        log = lambda *args: None
        with mock.patch.object(CDIN, "brutefir_running", return_value=True):
            device, _ = CDIN.pick_output("hw:Loopback,0,0", "hw:0,0", log)
            self.assertEqual(device, "hw:Loopback,0,0")
        with mock.patch.object(CDIN, "brutefir_running", return_value=False):
            device, _ = CDIN.pick_output("hw:Loopback,0,0", "hw:0,0", log)
            self.assertEqual(device, "hw:0,0")

    def test_brutefir_is_found_by_command_line_not_by_comm(self):
        """BruteFIR renames its main thread to "input" as soon as it starts
        convolving, so a comm match (`pgrep -x brutefir`) reports the engine as
        down while it holds the DAC.  The bridge then picks the DAC as its
        output and every alsaloop start dies of EBUSY — silence with a green
        service.  Pin the cmdline match, and pin that it is the same pattern
        drc.sh uses, so the two sides cannot drift into disagreeing about
        whether the chain is up."""
        seen = []

        def fake_run(argv, **kwargs):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 1)

        with mock.patch.object(CDIN.subprocess, "run", fake_run):
            CDIN.brutefir_running()
        self.assertEqual(seen, [["pgrep", "-f", CDIN.BRUTEFIR_PATTERN]])

        drc = DRC.read_text()
        self.assertIn(f"bf_pattern='{CDIN.BRUTEFIR_PATTERN}'", drc)

    def test_roles_file_supplies_the_capture_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.roles"
            path.write_text("dac_unit=0\ndac_desc=DAC8\ndac_id=0x1:0x2\n"
                            "capture_unit=2\ncapture_desc=ESI U24XL\n"
                            "capture_id=0x0a92:0x0053\n")
            roles = CDIN.read_roles(str(path))
        self.assertEqual(roles["capture_unit"], "2")
        self.assertEqual(roles["capture_desc"], "ESI U24XL")

    def test_missing_roles_file_is_empty_not_an_error(self):
        self.assertEqual(CDIN.read_roles("/nonexistent/audio.roles"), {})

    def test_sync_default_keeps_the_path_bit_perfect(self):
        """The default follows the output, and neither answer resamples."""
        args = CDIN.parse_args([])
        loop, _ = CDIN.pick_sync(args.sync, "hw:Loopback,0,0", "hw:Loopback,0,0")
        dac, _ = CDIN.pick_sync(args.sync, "hw:0,0", "hw:Loopback,0,0")
        self.assertEqual(loop, "playshift")
        self.assertEqual(dac, "simple")
        self.assertNotIn("samplerate", (loop, dac),
                         "the default must not put a resampler in the CD path")

    def test_the_dac_straight_path_still_corrects_the_drift(self):
        """Only snd-aloop has the rate-shift control playshift steers.  Leaving
        playshift on the DAC-straight path means NO correction at all: "drift
        settling" forever while the crystals walk apart, until the loop starves
        or overruns — a slow failure that looks healthy for the length of a
        disc.  An explicit choice still wins on either path."""
        self.assertEqual(CDIN.pick_sync("", "hw:0,0", "hw:Loopback,0,0")[0], "simple")
        self.assertEqual(
            CDIN.pick_sync("samplerate", "hw:0,0", "hw:Loopback,0,0")[0], "samplerate")
        self.assertEqual(
            CDIN.pick_sync("simple", "hw:Loopback,0,0", "hw:Loopback,0,0")[0], "simple")

    def test_only_the_capture_end_goes_through_plug(self):
        """alsaloop takes one -f for both ends and the ends disagree: BruteFIR
        reads the loopback as S32_LE, the ESI U24 XL captures only S16_LE /
        S24_3LE.  A raw `hw:` capture is "Sample format not available" at every
        start — silence with a service that looks healthy.  The loopback end
        must stay raw, so nothing can slip a converter into the DRC input."""
        log = lambda *args: None
        with mock.patch.object(CDIN, "card_number", return_value=2):
            capture = CDIN.Device("hw:2,0", "c")
        with mock.patch.object(CDIN, "card_number", return_value=1):
            output = CDIN.Device("hw:Loopback,0,0", "p")
            bridge = CDIN.Bridge(CDIN.parse_args([]), log)
        argv = bridge.alsaloop_argv(capture, output)
        self.assertEqual(argv[argv.index("-C") + 1], "plughw:2,0")
        self.assertEqual(argv[argv.index("-P") + 1], "hw:Loopback,0,0")

    def test_samplerate_is_the_only_mode_that_passes_a_converter(self):
        log = lambda *args: None
        with mock.patch.object(CDIN, "card_number", return_value=1):
            capture = CDIN.Device("hw:2,0", "c")
            output = CDIN.Device("hw:Loopback,0,0", "p")
            shift = CDIN.Bridge(CDIN.parse_args([]), log)
            resamp = CDIN.Bridge(CDIN.parse_args(["--sync", "samplerate"]), log)
        self.assertNotIn("-A", shift.alsaloop_argv(capture, output))
        self.assertIn("-A", resamp.alsaloop_argv(capture, output))


class CaptureRoleTest(unittest.TestCase):
    """The USB identity -> ALSA card number round trip on Linux."""

    CARDS = [{"number": "0", "identity": "0x2fc6:0x0001", "serial": "okto1",
              "name": "DAC8 STEREO"},
             {"number": "2", "identity": "0x0a92:0x0053", "serial": "",
              "name": "ESI U24XL"}]

    def setUp(self):
        self.helper = load(HELPER, "omdrc_config_helper_linux")

    def test_apply_publishes_both_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "brutefir").mkdir()
            defaults = root / "brutefir/brutefir_defaults.conf"
            defaults.write_text('output {\n  device: "alsa" {\n'
                                '    device: "hw:9,0"; # omdrc-managed-dac\n'
                                "  };\n};\n")
            roles_conf = root / "etc/open-media-drc/audio-roles.conf"
            state = root / "run/audio.roles"

            captured = {}

            # Recorded rather than written: linux_apply publishes to the real
            # /run/omdrc, which a test must not touch (and cannot create).
            def fake_atomic(path, text, mode=0o644, owner=None):
                captured[Path(path).name] = text

            with mock.patch.object(self.helper, "linux_usb_cards", return_value=self.CARDS), \
                 mock.patch.object(self.helper, "installed_conf",
                                   return_value={"AUDIO_USER": "tester",
                                                 "AUDIO_HOME": str(root)}), \
                 mock.patch.object(self.helper.pwd, "getpwnam",
                                   return_value=mock.Mock(pw_dir=str(root),
                                                          pw_uid=1000, pw_gid=1000)), \
                 mock.patch.object(self.helper, "atomic_text", side_effect=fake_atomic), \
                 mock.patch.object(self.helper, "linux_aloop_timer"), \
                 mock.patch.dict(os.environ, {"PREFIX": str(root)}), \
                 mock.patch.object(self.helper, "Path", Path):
                # ~/.config/BruteFIR is where linux_apply looks; point it there.
                (root / ".config/BruteFIR").mkdir(parents=True)
                (root / ".config/BruteFIR/brutefir_defaults.conf").write_text(
                    defaults.read_text())
                self.helper.linux_apply("0x2fc6:0x0001:okto1", 5, restart=False,
                                        capture="0x0a92:0x0053")

        published = captured["audio.roles"]
        self.assertIn("dac_unit=0\n", published)
        self.assertIn("capture_unit=2\n", published)
        self.assertIn("capture_desc=ESI U24XL\n", published)
        self.assertIn("capture_id=0x0a92:0x0053\n", published)
        # The identities are what survives a reboot; the numbers are not.
        self.assertIn('OMDRC_AUDIO_CAPTURE="0x0a92:0x0053"',
                      captured["audio-roles.conf"])

    def _apply_with_defaults(self, root, defaults_text):
        """Run linux_apply against a hand-written defaults file."""
        (root / ".config/BruteFIR").mkdir(parents=True)
        (root / ".config/BruteFIR/brutefir_defaults.conf").write_text(defaults_text)
        with mock.patch.object(self.helper, "linux_usb_cards", return_value=self.CARDS), \
             mock.patch.object(self.helper, "installed_conf",
                               return_value={"AUDIO_USER": "tester",
                                             "AUDIO_HOME": str(root)}), \
             mock.patch.object(self.helper.pwd, "getpwnam",
                               return_value=mock.Mock(pw_dir=str(root),
                                                      pw_uid=1000, pw_gid=1000)), \
             mock.patch.object(self.helper, "atomic_text"), \
             mock.patch.object(self.helper, "linux_aloop_timer"), \
             mock.patch.dict(os.environ, {"PREFIX": str(root)}):
            self.helper.linux_apply("0x2fc6:0x0001:okto1", 5, restart=False)

    def test_a_defaults_file_without_the_marker_names_the_fix(self):
        # What every box installed before the marker existed still has: the
        # file is hand-edited, so no install overwrites it.  The error has to
        # carry the repair, or the web UI's DAC picker just keeps failing.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as error:
                self._apply_with_defaults(
                    Path(tmp), 'output {\n  device: "alsa" {\n'
                    '    device: "hw:0,0";   # the USB DAC\n  };\n};\n')
        message = str(error.exception)
        self.assertIn("omdrc-managed-dac", message)
        self.assertIn("user-install", message)
        self.assertIn('device: "hw:0,0"; # omdrc-managed-dac', message)

    def test_two_marked_device_lines_are_refused_by_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as error:
                self._apply_with_defaults(
                    Path(tmp), 'output {\n  device: "alsa" {\n'
                    '    device: "hw:0,0"; # omdrc-managed-dac\n'
                    '    device: "hw:1,0"; # omdrc-managed-dac\n  };\n};\n')
        self.assertIn("2 device lines", str(error.exception))

    def test_an_unresolvable_capture_is_named_as_such(self):
        with mock.patch.object(self.helper, "linux_usb_cards", return_value=self.CARDS):
            with self.assertRaises(RuntimeError) as error:
                self.helper.linux_resolve("0xdead:0xbeef", "capture")
        self.assertIn("capture", str(error.exception))

    def test_aloop_timer_follows_the_selected_dac(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "omdrc-snd-aloop.conf"
            path.write_text('options snd-aloop index=1 id=Loopback '
                            'pcm_substreams=2 timer_source="hw:0,0,0"'
                            '  # omdrc-managed-aloop-timer\n')
            with mock.patch.object(self.helper, "ALOOP_MODPROBE", str(path)):
                self.helper.linux_aloop_timer("3")
            self.assertIn('timer_source="hw:3,0,0"', path.read_text())
            # Everything else on the line, marker included, is preserved.
            self.assertIn("index=1 id=Loopback pcm_substreams=2", path.read_text())
            self.assertIn("# omdrc-managed-aloop-timer", path.read_text())

    def test_a_missing_modprobe_file_is_a_notice_not_a_failure(self):
        with mock.patch.object(self.helper, "ALOOP_MODPROBE", "/nonexistent/x.conf"):
            self.helper.linux_aloop_timer("0")   # must not raise


class ExclusiveSourceTest(unittest.TestCase):
    """drc.sh: a capture input gates MPD outputs on both supported platforms."""

    def setUp(self):
        self.text = DRC.read_text()

    def test_every_capture_source_is_exclusive(self):
        """`cdin` and `linein` are one bridge on one loopback seat.

        The exclusivity is a property of that seat, not of the CD, so every
        branch that used to name "cdin" has to ask the set instead — a source
        added to the table and forgotten in one `case` is a bridge that plays
        while MPD is also enabled, which on Linux is an EBUSY and on FreeBSD is
        two programs mixed together.
        """
        self.assertIn("""valid_source() {
  case "$1" in music|cdin|linein) return 0 ;; esac""", self.text)
        self.assertIn("""is_capture_source() {
  case "$1" in cdin|linein) return 0 ;; esac""", self.text)
        # No branch may test the token directly any more.
        self.assertNotRegex(self.text, r'\[ "\$\w*source\w*" = "cdin" \]')

    def test_cdin_mode_leaves_mpd_without_a_loopback_output(self):
        """Enabling DRC-native while alsaloop holds the substream is an EBUSY
        that surfaces as "Failed to open audio output" on the next Play."""
        self.assertTrue(re.search(
            r'if is_capture_source "\$\{source_mode:-music\}"; then'
            r'.{0,600}?mpc_bounded disable "DRC-native"', self.text, re.S),
            "the capture branch must leave MPD without a loopback output")

    def test_cdin_mode_remembers_the_output_for_the_web_stop_action(self):
        self.assertIn('CDIN_MPD_OUTPUT_FILE="$STATE_DIR/cdin-mpd-output"', self.text)
        self.assertIn('> "$CDIN_MPD_OUTPUT_FILE"', self.text)

    def test_the_bridge_is_stopped_before_the_chain_is_torn_down(self):
        """Both `off/stop` and the rebuild path must free the substream first."""
        self.assertEqual(self.text.count("release_cdin_or_restore_mpd\n"), 2,
                         "a teardown path no longer releases the loopback")
        self.assertIn('mpc_bounded enable only "OKTO-DAC"', self.text)
        self.assertIn("MPD direct output restored to OKTO-DAC; requested audio change was not applied",
                      self.text)

    def test_freebsd_bridge_release_uses_process_lifecycle(self):
        self.assertIn('service omdrc_cdin onestop', self.text)
        self.assertIn('source=process_exit', self.text)
        self.assertIn('CDIN_RESTART_NEEDED=1', self.text)
        self.assertNotIn('cannot verify omdrc-cdin release; log is unreadable',
                         self.text)

    def test_stopping_waits_for_the_process_not_the_unit(self):
        """`systemctl stop` returns before the kernel closes the substream."""
        block = self.text.split("stop_cdin_linux() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('pgrep_x "$OMDRC_CDIN_PROCESS"', block)
        self.assertIn("OMDRC_CDIN_STOP_POLLS", block)

    def test_a_stopped_loop_still_stops_the_unit(self):
        """The supervisor picks its output once, at startup, and retries
        alsaloop on a backoff — so "no alsaloop" does not mean "no bridge".
        Skipping the stop there leaves the unit up with its old answer, and the
        `start` that follows is a no-op: pressing CD input again changes
        nothing."""
        block = self.text.split("stop_cdin_linux() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('systemctl_user is-active --quiet "$OMDRC_CDIN_UNIT"', block)

    def test_process_predicates_are_not_spelled_the_freebsd_way(self):
        """`pgrep -q` is FreeBSD's; procps-ng exits 2 on it, so on Linux every
        such test reads as "not running" — the bridge is neither waited for nor
        ever seen to start.  The Linux paths go through pgrep_x."""
        self.assertIn('pgrep_x() { pgrep -x "$1" > /dev/null 2>&1; }', self.text)
        for line in self.text.splitlines():
            if "pgrep -q" in line:
                self.assertNotIn("OMDRC_CDIN_PROCESS", line,
                                 "a Linux-reached predicate still uses pgrep -q")

    def test_no_drc_keeps_the_disc_playing_straight_to_the_dac(self):
        """FreeBSD has always done this: `off` tears virtual_oss down and the
        bridge is restarted, re-picking /dev/dsp.dac, so turning the correction
        off leaves the CD audible.  Linux stopped it instead, which made "no
        DRC" mean "no CD" — the divergence this pins shut."""
        block = self.text.split('if [ "$mode" = "off" ] || [ "$mode" = "stop" ]; then',
                                1)[1].split("\nfi\n", 1)[0]
        # The saved source decides, and `off` is the only verb that keeps it:
        # `stop` is the transient teardown and must still hand the DAC back.
        self.assertIn('[ "$mode" = "off" ] && is_capture_source "$off_source"', block)
        self.assertIn("keep_cdin=true", block)
        # MPD must NOT be given the DAC first: it is single-open, and doing so
        # is the EBUSY that would read as the bridge failing to start.
        self.assertTrue(re.search(
            r'if \$keep_cdin; then.{0,400}?elif mpc_bounded enable only "OKTO-DAC"',
            block, re.S),
            "the direct-DAC output must be skipped when the CD input keeps it")
        # And the bridge has to be told to start again after being stopped.
        self.assertTrue(re.search(
            r'if \$keep_cdin; then\s+source_mode="\$off_source"\s+'
            r'export OMDRC_START_CDIN=1',
            block), "restart_cdin needs the source and the start permission")

    def test_reconcile_does_not_evict_a_disc_that_owns_the_dac(self):
        """reconcile is level-triggered and runs repeatedly.  Handing the DAC
        to MPD in its already-off branch would evict the disc on the next tick,
        and from the listener's side the music would just stop by itself."""
        block = self.text.split('if [ "$desired_power" = "off" ]; then',
                                1)[1].split("\n  fi\n", 1)[0]
        self.assertTrue(re.search(
            r'if \$IS_LINUX && is_capture_source "\$desired_source"; then'
            r'.{0,900}?exit 0.{0,80}?fi\s+mpc_bounded enable only "OKTO-DAC"',
            block, re.S),
            "the capture guard must come before the direct-DAC handover")
        # Started only when absent: restarting a healthy bridge every tick
        # would chop the music up on its own.
        self.assertIn('if ! pgrep_x "$OMDRC_CDIN_PROCESS"; then', block)

    def test_music_source_always_stops_the_bridge(self):
        block = self.text.split("restart_cdin() {", 1)[1].split("\n}", 1)[0]
        linux = block.split("if $IS_LINUX; then", 1)[1].split("return 0", 1)[0]
        self.assertIn("else\n      stop_cdin_linux", linux,
                      "selecting music must hand the loopback back to MPD")

    def test_only_the_explicit_action_may_start_a_stopped_bridge(self):
        block = self.text.split("restart_cdin() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('"${OMDRC_START_CDIN:-0}" = 1', block)

    def test_shell_still_parses(self):
        self.assertEqual(
            subprocess.run(["bash", "-n", str(DRC)], capture_output=True).returncode, 0)


class PanelDefaultsTest(unittest.TestCase):
    def test_the_watched_process_is_the_one_holding_the_device(self):
        """On Linux the supervisor can be alive with alsaloop dead, and a green
        light over silence is the one reading the card must never give."""
        source = (ROOT / "omdrc-ctrl/src/app.py").read_text()
        self.assertIn('CDIN_PROCESS = "alsaloop" if _IS_LINUX else "omdrc-cdin"', source)
        self.assertIn('CDIN_SERVICE = "omdrc-cdin" if _IS_LINUX else "omdrc_cdin"', source)

    def test_chain_capture_role_follows_the_configured_interface(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.roles"
            path.write_text("dac_unit=0\ncapture_unit=2\n")
            with mock.patch.object(APP, "_AUDIO_ROLES_FILE", str(path)):
                self.assertEqual(APP._linux_capture_role(), "hw:2,0")
            path.write_text("dac_unit=0\ncapture_unit=\n")
            with mock.patch.object(APP, "_AUDIO_ROLES_FILE", str(path)):
                self.assertEqual(APP._linux_capture_role(), "")

    def test_capture_selection_is_no_longer_refused_on_linux(self):
        source = (ROOT / "omdrc-ctrl/src/configuration.py").read_text()
        self.assertNotIn("capture selection is not operational on Linux", source)
        page = (ROOT / "omdrc-ctrl/src/templates/configuration.html").read_text()
        self.assertNotIn("Linux capture routing is not operational yet", page)


if __name__ == "__main__":
    unittest.main()
