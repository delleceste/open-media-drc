"""Exercise browser role changes and CMake installation in disposable homes."""
import importlib.util
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "browser_alsa_helper", ROOT / "scripts/omdrc-config-helper.py")
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


class BrowserRolesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefix = Path(self.tmp.name)
        template = self.prefix / "share/open-media-drc/asoundrc.linux.conf.in"
        template.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "browser-nodrc/asoundrc.linux.conf.in", template)
        self.config = self.prefix / "etc/open-media-drc/browser-alsa.conf"

    def test_switch_dac_and_reorder_indexes_preserves_selected_identity(self):
        first = {"number": "2", "alsa_id": "DAC8STEREO"}
        second = {"number": "0", "alsa_id": "OtherDAC"}
        capture = {"number": "3", "alsa_id": "U24XL"}
        with mock.patch.object(HELPER, "linux_alsa_card_id",
                               side_effect=lambda card: card["alsa_id"]):
            HELPER.linux_browser_alsa(str(self.prefix), first, capture)
            self.assertIn("hw:CARD=DAC8STEREO,DEV=0", self.config.read_text())
            HELPER.linux_browser_alsa(str(self.prefix), second, capture)
            changed = self.config.read_text()
            self.assertIn("hw:CARD=OtherDAC,DEV=0", changed)
            self.assertIn("hw:CARD=U24XL,DEV=0", changed)
            self.assertNotIn("DAC8STEREO", changed)
            second["number"], capture["number"] = "4", "0"
            HELPER.linux_browser_alsa(str(self.prefix), second, capture)
            self.assertEqual(changed, self.config.read_text())

    def test_unconfigured_output_never_falls_back_to_card_zero(self):
        HELPER.linux_browser_alsa(str(self.prefix), None, None)
        text = self.config.read_text()
        self.assertIn('pcm "hw:CARD=OMDRCNoDAC,DEV=0"', text)
        self.assertIn('slave.pcm "null"', text)
        self.assertNotIn("@_browser_", text)

    def test_install_refresh_uses_saved_usb_selection_without_restarting(self):
        self.config.parent.mkdir(parents=True)
        (self.config.parent / "audio-roles.conf").write_text(
            'OMDRC_AUDIO_DAC="0x1234:0xabcd"\n'
            'OMDRC_AUDIO_CAPTURE=""\n')
        card = {"number": "7", "identity": "0x1234:0xabcd", "serial": ""}
        with mock.patch.dict(os.environ, {"PREFIX": str(self.prefix)}), \
             mock.patch.object(HELPER, "linux_usb_cards", return_value=[card]), \
             mock.patch.object(HELPER, "linux_alsa_card_id", return_value="ChosenDAC"), \
             mock.patch.object(HELPER, "run") as run:
            HELPER.linux_browser_alsa_refresh()
        self.assertIn("hw:CARD=ChosenDAC,DEV=0", self.config.read_text())
        run.assert_not_called()

    def test_apply_updates_browser_route_alongside_drc(self):
        home = self.prefix / "home"
        defaults = home / ".config/BruteFIR/brutefir_defaults.conf"
        defaults.parent.mkdir(parents=True)
        defaults.write_text('device: "hw:9,0"; # omdrc-managed-dac\n')
        card = {"number": "3", "identity": "0x1234:0xabcd",
                "serial": "", "name": "Selected DAC"}
        real_atomic = HELPER.atomic_text

        def write(path, text, mode=0o644, owner=None):
            if str(path).startswith("/run/"):
                return
            real_atomic(path, text, mode)

        with mock.patch.dict(os.environ, {"PREFIX": str(self.prefix)}), \
             mock.patch.object(HELPER, "linux_usb_cards", return_value=[card]), \
             mock.patch.object(HELPER, "linux_alsa_card_id", return_value="SelectedDAC"), \
             mock.patch.object(HELPER, "installed_conf", return_value={
                 "AUDIO_USER": pwd.getpwuid(os.getuid()).pw_name,
                 "AUDIO_HOME": str(home)}), \
             mock.patch.object(HELPER, "linux_aloop_timer", return_value=False), \
             mock.patch.object(HELPER, "atomic_text", side_effect=write):
            HELPER.linux_apply("0x1234:0xabcd", 5, restart=False)
        self.assertIn("hw:CARD=SelectedDAC,DEV=0", self.config.read_text())
        self.assertIn('"hw:3,0"', defaults.read_text())


@unittest.skipUnless(shutil.which("cmake"), "CMake required")
class CMakeBrowserInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "audio home"
        self.home.mkdir()
        self.prefix = self.root / "prefix"
        self.build = self.root / "build"
        source = self.root / "src"
        source.mkdir()
        for directory in ("cmake", "browser-nodrc", "scripts"):
            (source / directory).symlink_to(ROOT / directory, target_is_directory=True)
        (source / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.16)\n'
            'project(browser_install_test LANGUAGES NONE)\n'
            'include(cmake/browser-alsa-linux.cmake)\n')
        self.command("cmake", "-S", str(source), "-B", str(self.build),
                     f"-DCMAKE_INSTALL_PREFIX={self.prefix}",
                     f"-DAUDIO_HOME={self.home}",
                     f"-DAUDIO_USER={pwd.getpwuid(os.getuid()).pw_name}",
                     f"-DPYTHON3={sys.executable}")

    def command(self, *args, env=None, check=True):
        result = subprocess.run(args, text=True, capture_output=True, env=env)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def install(self, **kwargs):
        return self.command("cmake", "--install", str(self.build), **kwargs)

    def test_preserves_original_and_is_idempotent(self):
        path = self.home / ".asoundrc"
        original = '# Custom settings\ndefaults.pcm.card 0\npcm.custom { type null }\n'
        path.write_text(original)
        self.install()
        first = path.read_text()
        self.assertTrue(first.startswith(original))
        backup = Path(str(path) + ".omdrc-before-browser-alsa")
        self.assertEqual(backup.read_text(), original)
        self.assertIn(str(self.prefix / "etc/open-media-drc/browser-alsa.conf"), first)
        self.install()
        self.assertEqual(path.read_text(), first)
        self.assertEqual(backup.read_text(), original)
        self.assertTrue((self.prefix / "etc/open-media-drc/browser-alsa.conf").is_file())

    def test_existing_xdg_file_gets_last_override(self):
        old = self.home / ".asoundrc"
        old.write_text('defaults.pcm.card 0\n')
        xdg = self.home / ".config/alsa/asoundrc"
        xdg.parent.mkdir(parents=True)
        xdg.write_text('defaults.pcm.card 1\n')
        self.install()
        self.assertEqual(old.read_text(), 'defaults.pcm.card 0\n')
        self.assertIn("# BEGIN open-media-drc browser ALSA", xdg.read_text())

    def test_destdir_stages_template_without_touching_home(self):
        path = self.home / ".asoundrc"
        path.write_text("original\n")
        stage = self.root / "stage"
        self.install(env=dict(os.environ, DESTDIR=str(stage)))
        self.assertEqual(path.read_text(), "original\n")
        self.assertFalse(self.prefix.exists())
        self.assertTrue((stage / self.prefix.relative_to("/") /
                         "share/open-media-drc/asoundrc.linux.conf.in").is_file())

    def test_incomplete_managed_block_is_preserved(self):
        path = self.home / ".asoundrc"
        original = "# BEGIN open-media-drc browser ALSA\n<custom>\n"
        path.write_text(original)
        result = self.install(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
    unittest.main()
