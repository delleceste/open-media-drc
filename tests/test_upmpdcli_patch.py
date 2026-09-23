#!/usr/bin/env python3
"""The upmpdcli patch that lets a renderer name the release it is playing.

upmpdcli drops everything but artist/album/title/track on the way from the
control point to MusicPD, so a queue entry names a recording and never the
issue it came from (see upmpdcli/patches/README.md).  The patch here keeps
the year and the label, and cmake/dependencies.cmake warns when the upmpdcli
on the host was built without it.

What these tests pin is the join between those two: the check greps the
binary for a string the patch introduces, and nothing else connects them.
Rename it on one side only and detection fails silently -- every host would
be reported as patched, which is the one wrong answer that never complains.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH_DIR = ROOT / "upmpdcli/patches"
PATCH = PATCH_DIR / "0001-carry-date-genre-and-publisher-tags.patch"
DEPENDENCIES = ROOT / "cmake/dependencies.cmake"
RENDERERS = ROOT / "cmake/renderers.cmake"

# The DIDL property name the patch introduces and a stock upmpdcli never
# mentions.  Functional, not a marker: an upstream release that merges the
# patch answers yes to the same test.
ANCHOR = "upnp:publisher"


class PatchFileTest(unittest.TestCase):
    def setUp(self):
        self.text = PATCH.read_text(encoding="utf-8")

    def test_the_patch_ships_with_its_instructions(self):
        self.assertTrue(PATCH.is_file())
        self.assertTrue((PATCH_DIR / "README.md").is_file())

    def test_it_touches_exactly_the_three_files_it_documents(self):
        touched = set(re.findall(r"^\+\+\+ b/(.+)$", self.text, re.M))
        self.assertEqual(touched, {"src/mpdcli.cxx", "src/upmpdutils.cxx",
                                   "src/upmpdutils.hxx"})

    def test_it_carries_the_anchor_the_build_check_looks_for(self):
        added = [line for line in self.text.splitlines()
                 if line.startswith("+") and not line.startswith("+++")]
        self.assertTrue(any(ANCHOR in line for line in added),
                        f"the patch must introduce {ANCHOR!r}")

    def test_the_new_tags_are_all_there(self):
        added = "\n".join(line for line in self.text.splitlines()
                          if line.startswith("+"))
        for tag in ("MPD_TAG_DATE", "MPD_TAG_GENRE", "MPD_TAG_LABEL"):
            self.assertIn(tag, added)
        # MPD_TAG_LABEL needs libmpdclient 2.17; sending it to an older one
        # would cost the tags after it.
        self.assertIn("LIBMPDCLIENT_CHECK_VERSION(2,17,0)", added)

    def test_empty_values_are_never_sent(self):
        added = "\n".join(line for line in self.text.splitlines()
                          if line.startswith("+"))
        for field in ("meta.dcdate", "meta.genre", "meta.publisher"):
            self.assertIn(f"!{field}.empty()", added)

    def test_it_is_a_git_am_able_export(self):
        # The same commit goes upstream as a Framagit merge request, so the
        # export has to keep its author and message.
        self.assertTrue(self.text.startswith("From "))
        self.assertIn("Subject: [PATCH]", self.text)


class BuildCheckTest(unittest.TestCase):
    def setUp(self):
        self.cmake = DEPENDENCIES.read_text(encoding="utf-8")

    def test_the_check_greps_for_that_same_anchor(self):
        self.assertIn(f'grep -a -c "{ANCHOR}"', self.cmake)

    def test_the_warning_names_the_patch_that_fixes_it(self):
        self.assertIn(PATCH.name, self.cmake)

    def test_it_can_be_silenced_and_never_fails_the_configure(self):
        self.assertIn("OMDRC_WARN_UNPATCHED_UPMPDCLI", self.cmake)
        check = self.cmake[self.cmake.index("function(omdrc_check_upmpdcli"):]
        check = check[:check.index("endfunction()")]
        self.assertNotIn("FATAL_ERROR", check)

    def test_the_patch_is_installed_where_the_warning_points(self):
        # The printed procedure must name a path that exists on the host, not
        # one inside a checkout that may be gone by then.
        renderers = RENDERERS.read_text(encoding="utf-8")
        self.assertIn("install(DIRECTORY upmpdcli/patches", renderers)


if __name__ == "__main__":
    unittest.main()
