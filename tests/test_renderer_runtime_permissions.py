#!/usr/bin/env python3
"""Installer contracts for MPD and renderer writable-state ownership."""

import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/prepare-renderer-runtime.sh"


class RendererRuntimePermissionTests(unittest.TestCase):
    def test_helper_converges_all_writable_paths(self):
        account = pwd.getpwuid(os.getuid())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home" / account.pw_name
            qconnect = root / "var" / "db" / "qobuzconnect2mpd"
            runtime = root / "tmp"
            runtime.mkdir(parents=True)

            # Existing nested state must survive migration, not merely have a
            # correctly owned parent directory created around it.
            token = qconnect / "cache" / "token"
            token.parent.mkdir(parents=True)
            token.write_text("keep-me", encoding="utf-8")
            token.chmod(0o600)
            for name in (
                "upmpdcli.log",
                "upmpdcli-console.log",
                "qconnect2mpd.log",
                "qconnect2mpd-status.txt",
            ):
                (runtime / name).write_text(name, encoding="utf-8")

            result = subprocess.run(
                [
                    "/bin/sh",
                    str(HELPER),
                    account.pw_name,
                    str(home),
                    str(qconnect),
                    str(runtime),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(token.read_text(encoding="utf-8"), "keep-me")
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)

            expected = (
                home / ".local/share/mpd",
                home / ".cache/mpd",
                home / ".cache/upmpdcli",
                qconnect,
                token,
                *(runtime.iterdir()),
            )
            for path in expected:
                with self.subTest(path=path):
                    self.assertEqual(path.stat().st_uid, os.getuid())
                    self.assertEqual(path.stat().st_gid, account.pw_gid)
            self.assertEqual(qconnect.stat().st_mode & 0o777, 0o700)

    def test_helper_rejects_runtime_symlinks(self):
        account = pwd.getpwuid(os.getuid())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home" / account.pw_name
            qconnect = root / "state" / "qobuzconnect2mpd"
            runtime = root / "tmp"
            runtime.mkdir()
            target = root / "must-not-be-chowned"
            target.write_text("safe", encoding="utf-8")
            (runtime / "qconnect2mpd.log").symlink_to(target)

            result = subprocess.run(
                [
                    "/bin/sh",
                    str(HELPER),
                    account.pw_name,
                    str(home),
                    str(qconnect),
                    str(runtime),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 73)
            self.assertIn("refusing to chown runtime symlink", result.stderr)

    def test_cmake_install_runs_helper_but_destdir_skips_host_mutation(self):
        renderers = (ROOT / "cmake/renderers.cmake").read_text()
        installer = (ROOT / "cmake/install-renderer-runtime.cmake.in").read_text()
        self.assertIn("install-renderer-runtime.cmake", renderers)
        self.assertIn("prepare-renderer-runtime.sh", renderers)
        self.assertIn("install(SCRIPT", renderers)
        self.assertIn('$ENV{DESTDIR}', installer)
        self.assertIn("prepare-renderer-runtime.sh", installer)
        self.assertIn("/var/db/qobuzconnect2mpd", installer)


if __name__ == "__main__":
    unittest.main()
