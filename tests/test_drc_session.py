#!/usr/bin/env python3

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DrcSessionTest(unittest.TestCase):
    def test_boot_service_restores_the_complete_session(self):
        # The installed hotplug/boot unit must delegate to `drc.sh restore`,
        # which honours power, source, geometry and the rate/design tuple —
        # not parse last_arg itself.  (This guard used to point at the --user
        # drc.service, deleted as a duplicate of this unit.)
        service = (ROOT / "etc/systemd/system/drc-usb-audio.service.in").read_text(
            encoding="utf-8")
        self.assertIn("ExecStart=@REPO_DIR@/drc.sh restore", service)
        self.assertNotIn("last_arg", service)

    def test_every_mpd_start_reconciles_the_saved_audio_route(self):
        dropin = (ROOT / "etc/systemd/system/mpd.service.d/open-media-drc.conf.in").read_text(
            encoding="utf-8")
        service = (ROOT / "etc/systemd/system/omdrc-mpd-reconcile.service.in").read_text(
            encoding="utf-8")

        self.assertIn("Wants=omdrc-mpd-reconcile.service", dropin)
        self.assertIn("After=mpd.service", service)
        self.assertIn("ExecStart=@REPO_DIR@/drc.sh reconcile", service)
        self.assertNotIn("RemainAfterExit=yes", service)

        cmake = (ROOT / "cmake/renderers.cmake").read_text(encoding="utf-8")
        self.assertIn("DESTINATION lib/systemd/system/mpd.service.d", cmake)
        self.assertNotIn("DESTINATION share/omdrc/mpd.service.d", cmake)

    @staticmethod
    def _chain_bin(root: Path) -> Path:
        """A bin dir with stub brutefir/virtual_oss so `session` reports the
        chain as installed regardless of what the test host actually ships;
        the restore tuple must read the same on a dev box without brutefir."""
        chain = root / "chainbin"
        chain.mkdir()
        for name in ("brutefir", "virtual_oss"):
            stub = chain / name
            stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
        return chain

    def test_session_reports_the_exact_persistent_restore_tuple(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "site"
            state = root / "state"
            (site / "configs/120.blue").mkdir(parents=True)
            state.mkdir()
            (site / "configs/120.blue/brutefir-192000@rscreen.v2.conf").write_text(
                "sampling_rate: 192000;\n", encoding="utf-8")
            (state / "last_arg").write_text("resamp @rscreen.v2\n", encoding="utf-8")
            (state / "last_power").write_text("on\n", encoding="utf-8")
            (state / "last_source").write_text("cdin\n", encoding="utf-8")
            (state / "last_geometry").write_text("120.blue\n", encoding="utf-8")
            config = root / "omdrc.conf"
            config.write_text(
                f"GEOMETRY=flat\nOMDRC_SITE_DIR={site}\nOMDRC_STATE_DIR={state}\n",
                encoding="utf-8",
            )
            chain = self._chain_bin(root)
            env = os.environ.copy()
            env["OMDRC_CONF"] = str(config)
            env["PATH"] = f"{chain}{os.pathsep}{env['PATH']}"
            result = subprocess.run(
                [str(ROOT / "drc.sh"), "session"], env=env,
                capture_output=True, text=True, timeout=5, check=True,
            )
            self.assertEqual(dict(
                line.split("=", 1) for line in result.stdout.splitlines()), {
                    "geometry": "120.blue",
                    "power": "on",
                    "source": "cdin",
                    "mode": "resamp",
                    "rate": "192000",
                    "design": "@rscreen.v2",
                    "label": "rscreen.v2 auto-resample",
                    # With the chain installed the panel keeps its DRC controls.
                    "drc_available": "yes",
                    "missing": "",
                })

    @staticmethod
    def _path_without_chain(root: Path) -> str:
        """A PATH that keeps bash and the coreutils drc.sh needs but is
        guaranteed to contain no brutefir/virtual_oss, so the absence is
        hermetic even on a host (FreeBSD) that installs them beside bash.

        Filtering whole directories out of PATH cannot do this — bash and
        brutefir share /usr/local/bin there — so symlink every executable on
        the inherited PATH except the two chain binaries into one clean dir."""
        farm = root / "pathfarm"
        farm.mkdir()
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory or not os.path.isdir(directory):
                continue
            for name in os.listdir(directory):
                if name in ("brutefir", "virtual_oss"):
                    continue
                link = farm / name
                src = os.path.join(directory, name)
                if not link.exists() and os.access(src, os.X_OK):
                    try:
                        link.symlink_to(src)
                    except OSError:
                        pass
        return str(farm)

    def test_session_flags_a_missing_chain_for_the_panel(self):
        """A control box with no brutefir: `session` must say so, so the panel
        hides the DRC controls rather than offering buttons that cannot work.
        The rest of the restore tuple is reported unchanged."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site, state = root / "site", root / "state"
            (site / "configs/flat").mkdir(parents=True)
            state.mkdir()
            (site / "configs/flat/brutefir-192000.conf").write_text(
                "sampling_rate: 192000;\n", encoding="utf-8")
            (state / "last_arg").write_text("192000\n", encoding="utf-8")
            (state / "last_power").write_text("on\n", encoding="utf-8")
            config = root / "omdrc.conf"
            config.write_text(
                f"GEOMETRY=flat\nOMDRC_SITE_DIR={site}\nOMDRC_STATE_DIR={state}\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["OMDRC_CONF"] = str(config)
            # No chain by any route: a PATH with no brutefir/virtual_oss and
            # empty override sweeps.
            env["PATH"] = self._path_without_chain(root)
            env["OMDRC_BRUTEFIR_PATHS"] = ""
            env["OMDRC_VIRTUAL_OSS_PATHS"] = ""
            result = subprocess.run(
                [str(ROOT / "drc.sh"), "session"], env=env,
                capture_output=True, text=True, timeout=5, check=True,
            )
            fields = dict(
                line.split("=", 1) for line in result.stdout.splitlines())
            self.assertEqual(fields["drc_available"], "no")
            # brutefir is missing on every platform; virtual_oss only on FreeBSD.
            self.assertIn("brutefir", fields["missing"].split(","))
            self.assertEqual(fields["rate"], "192000")


    def test_the_session_reports_a_capture_source_as_itself(self):
        """`source` is the one part of the session the rate cannot reveal.

        44,100 Hz reads identically whether it is a CD-quality file or the CD
        input, so a session that flattened `cdin` back to `music` would
        restore the chain and leave the bridge down — silence, from a state
        that looked correct.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site, state = root / "site", root / "state"
            (site / "configs/flat").mkdir(parents=True)
            state.mkdir()
            (site / "configs/flat/brutefir-44100.conf").write_text(
                "sampling_rate: 44100;\n", encoding="utf-8")
            (state / "last_arg").write_text("44100\n", encoding="utf-8")
            (state / "last_power").write_text("on\n", encoding="utf-8")
            (state / "last_source").write_text("cdin\n", encoding="utf-8")
            config = root / "omdrc.conf"
            config.write_text(
                f"GEOMETRY=flat\nOMDRC_SITE_DIR={site}\nOMDRC_STATE_DIR={state}\n",
                encoding="utf-8")
            env = os.environ.copy()
            env["OMDRC_CONF"] = str(config)
            result = subprocess.run(
                [str(ROOT / "drc.sh"), "session"], env=env,
                capture_output=True, text=True, timeout=5, check=True,
            )
            session = dict(line.split("=", 1)
                           for line in result.stdout.splitlines())
            self.assertEqual(session["source"], "cdin")
            self.assertEqual(session["rate"], "44100")


if __name__ == "__main__":
    unittest.main()
