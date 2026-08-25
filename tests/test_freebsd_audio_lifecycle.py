"""Static contract tests for the FreeBSD rc.d/devd lifecycle integration."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUN_FROM_REPO = ROOT / "etc/rc.d/omdrc_audio.in"
PORT_TEMPLATE = ROOT / "freebsd/audio/open-media-drc/files/omdrc_audio.in"
MPD_HOOK = ROOT / "etc/rc.conf.d/musicpd/omdrc_audio"


class FreeBSDAudioLifecycleTests(unittest.TestCase):
    def test_musicpd_hook_is_one_way_bounded_reconcile(self):
        text = MPD_HOOK.read_text()
        self.assertIn('start_postcmd="omdrc_musicpd_poststart"', text)
        self.assertIn("checkyesno omdrc_audio_enable", text)
        self.assertEqual(text.count("/usr/sbin/service omdrc_audio reconcile"), 1)
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertIsNone(
            re.search(r"(?:^|\s)(?:sleep|daemon|lockf|flock)(?:\s|$)", code)
        )
        self.assertNotIn("service musicpd", text)

    def test_both_audio_templates_verify_dynamic_default_unit(self):
        for path in (RUN_FROM_REPO, PORT_TEMPLATE):
            with self.subTest(path=path):
                text = path.read_text()
                self.assertIn('SYSCTL="/sbin/sysctl"', text)
                self.assertIn('"hw.snd.default_unit=${DAC_UNIT}"', text)
                self.assertIn("hw.snd.default_unit readback", text)
                self.assertIn("default unit mismatch -- DAC role is pcm", text)
                self.assertNotRegex(text, r"hw\.snd\.default_unit=[0-9]+")

    def test_port_packages_musicpd_hook(self):
        makefile = (ROOT / "Makefile").read_text()
        cmake = (ROOT / "cmake/hotplug.cmake").read_text()
        plist = (ROOT / "freebsd/audio/open-media-drc/pkg-plist").read_text()
        self.assertIn("etc/rc.conf.d/musicpd/omdrc_audio", makefile)
        self.assertIn("etc/rc.conf.d/musicpd/omdrc_audio", cmake)
        self.assertIn("install-musicpd-hook.cmake", cmake)
        self.assertIn("etc/rc.conf.d/musicpd/omdrc_audio", plist)

    def test_musicpd_file_layout_is_migrated_without_losing_settings(self):
        helper = ROOT / "scripts/prepare-musicpd-rc-conf-dir.sh"
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "etc/rc.conf.d/musicpd"
            target.parent.mkdir(parents=True)
            original = 'musicpd_flags="--verbose"\nmusicpd_user="media"\n'
            target.write_text(original, encoding="utf-8")
            target.chmod(0o640)

            result = subprocess.run(
                [str(helper), str(target)], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.is_dir())
            preserved = target / "00-local.conf"
            self.assertEqual(preserved.read_text(encoding="utf-8"), original)
            self.assertEqual(preserved.stat().st_mode & 0o777, 0o640)

            # Preparing an already-converted install is an idempotent no-op.
            second = subprocess.run(
                [str(helper), str(target)], capture_output=True, text=True
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(preserved.read_text(encoding="utf-8"), original)

    def test_port_preinstall_handles_musicpd_file_layout(self):
        port_makefile = (
            ROOT / "freebsd/audio/open-media-drc/Makefile"
        ).read_text()
        pkg_install_template = (
            ROOT / "freebsd/audio/open-media-drc/files/pkg-install.in"
        ).read_text()
        self.assertIn("SUB_FILES=\tpkg-message pkg-install", port_makefile)
        self.assertIn("PRE-INSTALL", pkg_install_template)
        self.assertIn("00-local.conf", pkg_install_template)

        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "usr/local"
            target = prefix / "etc/rc.conf.d/musicpd"
            target.parent.mkdir(parents=True)
            target.write_text('musicpd_flags="--no-daemon"\n', encoding="utf-8")
            rendered = Path(temporary) / "pkg-install"
            rendered.write_text(
                pkg_install_template.replace("%%PREFIX%%", str(prefix)),
                encoding="utf-8",
            )
            result = subprocess.run(
                ["/bin/sh", str(rendered), "open-media-drc", "PRE-INSTALL"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (target / "00-local.conf").read_text(encoding="utf-8"),
                'musicpd_flags="--no-daemon"\n',
            )

    def test_service_and_configuration_separator_convention(self):
        hook = MPD_HOOK.read_text()
        self.assertRegex(hook, r"\bomdrc_audio\b")
        self.assertNotIn("omdrc-audio", hook)
        devd = (ROOT / "etc/devd/omdrc-audio.conf").read_text()
        self.assertIn("service omdrc_audio reconcile", devd)
        self.assertFalse(re.search(r"service\s+omdrc-audio", devd))

    def test_detached_devd_workers_keep_both_transaction_locks(self):
        devd = (ROOT / "etc/devd/omdrc-audio.conf").read_text()
        service = RUN_FROM_REPO.read_text()
        drc = (ROOT / "drc.sh").read_text()
        self.assertEqual(
            devd.count(
                'action "/usr/sbin/daemon -f /usr/sbin/service '
                'omdrc_audio reconcile"'
            ),
            2,
        )
        self.assertIn('/usr/bin/lockf -k -s -t 15 "${omdrc_audio_lockfile}"', service)
        self.assertIn('LOCK_FILE="${OMDRC_LOCK_FILE:-$STATE_DIR/drc.lock}"', drc)
        self.assertIn('lockf -k -s -t 30 "$LOCK_FILE"', drc)


if __name__ == "__main__":
    unittest.main()
