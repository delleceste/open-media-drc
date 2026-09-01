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
            env = os.environ.copy()
            env["OMDRC_CONF"] = str(config)
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
                })


if __name__ == "__main__":
    unittest.main()
