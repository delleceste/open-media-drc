#!/usr/bin/env python3
"""The DRC route must refuse every way it could return a meaningless verdict.

The direct route is safe by construction: it opens the raw device with the
chain torn down, so the only thing between the reference bytes and the wire is
the kernel.  The DRC route deliberately puts the loopback and the convolver
back in the path, and each of those is a way for a byte comparison to stop
meaning what it says:

  * a real room filter changes every sample by design, so a mismatch would be
    the filter working, not a fault;
  * a dirac pulse with attenuation is still a scale factor, and that one looks
    like a pass-through until you read the attenuation line;
  * a chain started at the wrong rate makes something resample, and the run
    then measures the resampler.

So each gets a test, plus the parse of `drc.sh status` that the guards read
their facts from.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/bitperfect_runner.py"

SPEC = importlib.util.spec_from_file_location("bprun", RUNNER)
BP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(BP)


# `drc.sh status` as it is actually printed — "%-17s" padded labels, the rate
# comparison line last.  Copied from drc.sh's status block rather than
# invented, because the parser's whole job is to read that exact shape.
STATUS_MATCHED = """\
Geometry:         flat
Active config:    44.1 kHz
Saved source:     music
virtual_oss:      running  44100 Hz
brutefir:         running  44100 Hz

MPD:              playing
Song:             Miles Davis - So What
Output audio:     44100:24:2
Bitrate:          1411

Rate:             MPD 44100 Hz = virtual_oss 44100 Hz  [match]
"""

STATUS_RESAMPLING = STATUS_MATCHED.replace(
    "Rate:             MPD 44100 Hz = virtual_oss 44100 Hz  [match]",
    "Rate:             MPD 96000 Hz != virtual_oss 44100 Hz  [MISMATCH]",
).replace("Output audio:     44100:24:2", "Output audio:     96000:24:2")

STATUS_LINUX = """\
Geometry:         flat
Active config:    44.1 kHz
Saved source:     music
ALSA:             running  44100 Hz
brutefir:         running  44100 Hz

MPD:              playing
Output audio:     44100:24:2

Rate:             MPD 44100 Hz = brutefir 44100 Hz  [match]  [ALSA: 44100 Hz]
"""

STATUS_CHAIN_DOWN = """\
Geometry:         flat
Active config:    off
Saved source:     music
virtual_oss:      not running
brutefir:         not running

MPD:              stopped
"""


class FakeStatus:
    """Stand in for `drc.sh status` without needing a chain to be running."""

    def __init__(self, text: str):
        self.text = text

    def __call__(self, argv, **kw):
        return subprocess.CompletedProcess(argv, 0, self.text, "")


def with_status(text: str):
    """Point chain_state() at canned status output."""
    return unittest.mock.patch.object(BP.subprocess, "run", FakeStatus(text))


class ChainStateParse(unittest.TestCase):
    def setUp(self):
        self.script = unittest.mock.patch.object(
            BP, "drc_script", lambda: "/usr/local/bin/omdrc")
        self.script.start()
        self.addCleanup(self.script.stop)

    def test_freebsd_matched_chain(self):
        with with_status(STATUS_MATCHED):
            state = BP.chain_state()
        self.assertEqual(state["geometry"], "flat")
        self.assertEqual(state["sink"], "virtual_oss")
        self.assertEqual(state["sink_rate"], 44100)
        self.assertEqual(state["brutefir_rate"], 44100)
        self.assertEqual(state["mpd_rate"], 44100)
        self.assertEqual(state["mpd_format"], "44100:24:2")
        self.assertEqual(state["rate_verdict"], "match")

    def test_resampling_chain_is_reported_as_mismatch(self):
        """The one state that makes virtual_oss resample: rates out of step."""
        with with_status(STATUS_RESAMPLING):
            state = BP.chain_state()
        self.assertEqual(state["mpd_rate"], 96000)
        self.assertEqual(state["sink_rate"], 44100)
        self.assertEqual(state["rate_verdict"], "mismatch")

    def test_linux_sink_is_alsa_not_virtual_oss(self):
        with with_status(STATUS_LINUX):
            state = BP.chain_state()
        self.assertEqual(state["sink"], "ALSA")
        self.assertEqual(state["sink_rate"], 44100)
        self.assertEqual(state["rate_verdict"], "match")

    def test_chain_down_reports_no_rates(self):
        with with_status(STATUS_CHAIN_DOWN):
            state = BP.chain_state()
        self.assertIsNone(state["brutefir_rate"])
        self.assertIsNone(state["sink_rate"])
        self.assertIsNone(state["rate_verdict"])

    def test_no_drc_script_is_not_an_exception(self):
        with unittest.mock.patch.object(BP, "drc_script", lambda: None):
            state = BP.chain_state()
        self.assertIsNone(state["brutefir_rate"])


class IdentityGuard(unittest.TestCase):
    """Only a dirac pulse at 0 dB leaves the samples alone."""

    def conf(self, text: str) -> Path:
        path = Path(tempfile.mkdtemp()) / "brutefir-44100.conf"
        path.write_text(text)
        return path

    def test_shipped_flat_geometry_is_a_pass_through(self):
        for rate in (44100, 48000, 88200, 96000, 192000):
            conf = ROOT / f"configs/flat/brutefir-{rate}.conf"
            with self.subTest(rate=rate):
                ok, why = BP.conf_is_identity(conf)
                self.assertTrue(ok, why)

    def test_room_filter_is_refused(self):
        ok, why = BP.conf_is_identity(self.conf('''
            coeff "c-l" { filename: "/f/room/44100/L.raw"; attenuation: 0.0; };
            coeff "c-r" { filename: "/f/room/44100/R.raw"; attenuation: 0.0; };
        '''))
        self.assertFalse(ok)
        self.assertTrue(any("not \"dirac pulse\"" in r for r in why), why)

    def test_attenuated_dirac_is_refused(self):
        """Looks like a pass-through; is a 3 dB scale factor on every sample."""
        ok, why = BP.conf_is_identity(self.conf(
            'coeff "c-l" { filename: "dirac pulse"; attenuation: 3.0; };'))
        self.assertFalse(ok)
        self.assertTrue(any("attenuation" in r for r in why), why)

    def test_config_without_coeff_blocks_is_refused(self):
        ok, why = BP.conf_is_identity(self.conf("sampling_rate: 44100;"))
        self.assertFalse(ok)

    def test_unreadable_config_is_refused_not_raised(self):
        ok, why = BP.conf_is_identity(Path("/nonexistent/brutefir-44100.conf"))
        self.assertFalse(ok)
        self.assertTrue(why)


class RouteGuard(unittest.TestCase):
    """assert_drc_route refuses; it never lets a bad run reach a verdict."""

    def setUp(self):
        self.flat = ROOT / "configs/flat/brutefir-44100.conf"
        unittest.mock.patch.object(
            BP, "drc_script", lambda: "/usr/local/bin/omdrc").start()
        self.addCleanup(unittest.mock.patch.stopall)

    def route(self, status: str, conf: Path | None, material: dict | None):
        with with_status(status), \
             unittest.mock.patch.object(BP, "running_brutefir_conf",
                                        lambda: conf):
            return BP.assert_drc_route(material)

    def test_healthy_chain_passes_and_reports_what_it_checked(self):
        chain = self.route(STATUS_MATCHED, self.flat,
                           {"rate": 44100, "channels": 2})
        self.assertTrue(chain["identity"])
        self.assertEqual(chain["brutefir_rate"], 44100)
        self.assertEqual(chain["material_rate"], 44100)
        self.assertEqual(chain["conf"], str(self.flat))

    def test_chain_down_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self.route(STATUS_CHAIN_DOWN, None, {"rate": 44100})
        self.assertIn("not running", str(caught.exception))

    def test_room_filter_is_refused_with_the_way_out(self):
        room = Path(tempfile.mkdtemp()) / "brutefir-44100.conf"
        room.write_text('coeff "c" { filename: "L.raw"; attenuation: 0.0; };')
        with self.assertRaises(SystemExit) as caught:
            self.route(STATUS_MATCHED, room, {"rate": 44100})
        self.assertIn("geometry flat", str(caught.exception))

    def test_material_rate_below_the_chain_rate_is_refused(self):
        """44.1 kHz material into a 96 kHz chain: MPD or the loopback resamples."""
        with self.assertRaises(SystemExit) as caught:
            self.route(STATUS_MATCHED.replace("44100 Hz", "96000 Hz"),
                       self.flat, {"rate": 44100})
        self.assertIn("resample", str(caught.exception))

    def test_no_material_skips_the_rate_check(self):
        """The precondition pass runs before the material is loaded."""
        chain = self.route(STATUS_MATCHED, self.flat, None)
        self.assertNotIn("material_rate", chain)


class GeometryProvenance(unittest.TestCase):
    """The loaded geometry comes from the running process, never a state file.

    On this box `drc.sh status` (run from the checkout) reported "flat" while
    brutefir was actually convolving 120.blue @multi.pt at 192 kHz, because
    the checkout's state files are not the ones the installed chain updates.
    A verdict that trusted the state file would have named the wrong filter
    set in its report."""

    def test_geometry_is_read_from_the_config_path(self):
        conf = Path("/usr/local/etc/open-media-drc/configs/120.blue/"
                    "brutefir-192000@multi.pt.conf")
        self.assertEqual(BP.geometry_from_conf(conf), "120.blue")

    def test_repo_layout_geometry(self):
        self.assertEqual(
            BP.geometry_from_conf(ROOT / "configs/flat/brutefir-44100.conf"),
            "flat")

    def test_disagreement_is_reported_but_not_fatal(self):
        """status says flat, the process says 120.blue -> warn, keep going."""
        flat = ROOT / "configs/flat/brutefir-44100.conf"
        said = []
        with with_status(STATUS_MATCHED), \
             unittest.mock.patch.object(BP, "drc_script",
                                        lambda: "/usr/local/bin/omdrc"), \
             unittest.mock.patch.object(BP, "running_brutefir_conf",
                                        lambda: flat), \
             unittest.mock.patch.object(BP, "say", said.append):
            chain = BP.assert_drc_route({"rate": 44100})
        self.assertEqual(chain["geometry_running"], "flat")
        self.assertEqual(said, [])          # they agree here: nothing to say

    def test_installed_wrapper_wins_over_the_checkout(self):
        with unittest.mock.patch.object(BP.shutil, "which",
                                        lambda n: "/usr/local/bin/omdrc"):
            self.assertEqual(BP.drc_script(), "/usr/local/bin/omdrc")


class ReportShape(unittest.TestCase):
    def test_route_choices_and_output_names_match_mpd_conf(self):
        """DRC_OUTPUT must name an output that actually exists in mpd.conf."""
        self.assertEqual(BP.ROUTES, ("direct", "drc"))
        for conf in ("mpd/mpd.conf.in", "mpd/musicpd.conf.in"):
            text = (ROOT / conf).read_text()
            with self.subTest(conf=conf):
                self.assertIn(f'name "{BP.DRC_OUTPUT}"', text)
                self.assertIn(f'name "{BP.DIRECT_OUTPUT}"', text)


class ManagerPlumbing(unittest.TestCase):
    """The panel and the CLI must agree about what the route means."""

    def setUp(self):
        # bitperfect.py imports its siblings, so the panel's src directory has
        # to be importable as a package root — same as test_bitperfect_page.
        import sys
        src = str(ROOT / "omdrc-ctrl/src")
        if src not in sys.path:
            sys.path.insert(0, src)
        import bitperfect
        self.mgr_mod = bitperfect

    def manager(self):
        settings = self.mgr_mod.Settings()
        return self.mgr_mod.BitPerfectManager(settings, dict)

    def test_aplay_cannot_be_routed_through_the_chain(self):
        with self.assertRaises(ValueError) as caught:
            self.manager().run_test(None, "aplay", "x.wav", 30.0, False, "drc")
        self.assertIn("raw DAC node", str(caught.exception))

    def test_unknown_route_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            self.manager().run_test(None, "mpd", "x.wav", 30.0, False, "sideways")
        self.assertIn("unknown route", str(caught.exception))

    def test_route_defaults_to_direct(self):
        """Existing callers keep the behaviour they had."""
        import inspect
        sig = inspect.signature(self.mgr_mod.BitPerfectManager.run_test)
        self.assertEqual(sig.parameters["route"].default, "direct")


if __name__ == "__main__":
    unittest.main()
