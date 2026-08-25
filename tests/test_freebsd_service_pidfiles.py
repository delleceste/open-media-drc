#!/usr/bin/env python3
"""FreeBSD system rc services must not change identity with the caller UID."""

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class FreeBSDServicePidfileTests(unittest.TestCase):
    def test_controller_templates_use_one_canonical_pidfile(self):
        for relpath in (
            "omdrc-ctrl/rc.d/omdrcctrl.in",
            "freebsd/audio/open-media-drc/files/omdrcctrl.in",
        ):
            with self.subTest(template=relpath):
                text = (ROOT / relpath).read_text()
                self.assertIn('/var/run/${name}/${name}.pid', text)
                self.assertIn("-M 0644", text)
                self.assertNotIn("${TMPDIR:-/tmp}", text)
                self.assertNotIn("unset omdrcctrl_user", text)

    def test_video_template_uses_one_canonical_pidfile(self):
        text = (ROOT / "video/webremote/rc.d/omdrcvideo.in").read_text()
        self.assertIn('/var/run/${name}/${name}.pid', text)
        self.assertIn("-M 0644", text)
        self.assertNotIn("${TMPDIR:-/tmp}", text)
        self.assertNotIn("unset omdrcvideo_user", text)

    def test_pidfile_selection_is_not_conditioned_on_invoking_uid(self):
        for relpath in (
            "omdrc-ctrl/rc.d/omdrcctrl.in",
            "video/webremote/rc.d/omdrcvideo.in",
        ):
            with self.subTest(template=relpath):
                text = (ROOT / relpath).read_text()
                identity_block = text[text.index('command="/usr/sbin/daemon"') :]
                self.assertNotIn('if [ "$(id -u)"', identity_block)
                self.assertNotIn("$(id -un)", identity_block)


if __name__ == "__main__":
    unittest.main()
