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
        is matched — but an underrun is an audible dropout and must reach the
        card's problem list rather than scroll past as an ordinary line."""
        class Nothing:
            spec = ""
            card = None

            def rate(self):
                return None

            def status(self):
                return {}

        stats = CDIN.Stats(Nothing())
        self.assertEqual(CDIN.classify("Playback: xrun detected", stats), "ERR")
        self.assertEqual(stats.starves, 1)
        self.assertEqual(CDIN.classify("Loop thread started", stats), "INF")
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
        with mock.patch.object(CDIN, "process_running", return_value=True):
            device, _ = CDIN.pick_output("hw:Loopback,0,0", "hw:0,0", log)
            self.assertEqual(device, "hw:Loopback,0,0")
        with mock.patch.object(CDIN, "process_running", return_value=False):
            device, _ = CDIN.pick_output("hw:Loopback,0,0", "hw:0,0", log)
            self.assertEqual(device, "hw:0,0")

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
        args = CDIN.parse_args([])
        self.assertEqual(args.sync, "playshift",
                         "the default must not put a resampler in the CD path")

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
    """drc.sh: one seat on hw:Loopback,0,0, and who gets it."""

    def setUp(self):
        self.text = DRC.read_text()

    def test_cdin_mode_leaves_mpd_without_a_loopback_output(self):
        """Enabling DRC-native while alsaloop holds the substream is an EBUSY
        that surfaces as "Failed to open audio output" on the next Play."""
        self.assertTrue(re.search(
            r'if \$IS_LINUX && \[ "\$\{source_mode:-music\}" = "cdin" \]; then'
            r'.{0,600}?mpc_bounded disable "DRC-native"', self.text, re.S),
            "the cdin branch must leave MPD without a loopback output")

    def test_the_bridge_is_stopped_before_the_chain_is_torn_down(self):
        """Both `off/stop` and the rebuild path must free the substream first."""
        self.assertEqual(
            self.text.count("if $IS_LINUX; then stop_cdin_linux || true; else release_cdin; fi"),
            2, "a teardown path no longer releases the loopback")

    def test_stopping_waits_for_the_process_not_the_unit(self):
        """`systemctl stop` returns before the kernel closes the substream."""
        block = self.text.split("stop_cdin_linux() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('pgrep -q -x "$OMDRC_CDIN_PROCESS"', block)
        self.assertIn("OMDRC_CDIN_STOP_POLLS", block)

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
