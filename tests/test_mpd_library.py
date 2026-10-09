"""Local-library search: tagged tracks match on their tags, untagged ones
(raw .ac3 rips, bare .wav) on their path, named after their folders."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omdrc-ctrl" / "src"))
import mpd_library  # noqa: E402

UNTAGGED = "USBHD2/Pop Rock/Nirvana/MTV Unplugged (DVD Audio)/Nirvana/Stereo Tracks"
TRACKS = [
    {"file": "Rock/Alanis/Unplugged/01.flac", "Artist": "Alanis Morissette",
     "Album": "MTV Unplugged", "Title": "You Learn", "duration": "250.0"},
    {"file": UNTAGGED + "/01 - About a Girl.ac3", "duration": "261.4"},
    {"file": UNTAGGED + "/02 - Come as You Are.ac3", "duration": "274.1"},
]


def fake_command(sock, command):
    """`search any` matches tags only; the filter form matches untagged paths."""
    if command.startswith("search any "):
        word = command[len("search any "):].strip('"').casefold()
        return [t for t in TRACKS if any(word in v.casefold()
                for k, v in t.items() if k not in ("file", "duration"))]
    word = command.split("contains ")[1].split(")")[0].strip('\\"').casefold()
    return [t for t in TRACKS if "Album" not in t and word in t["file"].casefold()]


class SearchTest(unittest.TestCase):
    def search(self, text):
        with patch.object(mpd_library, "_connect"), \
                patch.object(mpd_library, "_command", side_effect=fake_command), \
                patch.object(mpd_library, "_dr14_average", return_value=None):
            return mpd_library.search(text)

    def test_untagged_album_is_found_by_its_path(self):
        cards = self.search("Nirvana MTV")
        self.assertEqual([(c["artist"], c["title"], c["track_count"]) for c in cards],
                         [("Nirvana", "MTV Unplugged (DVD Audio)", 2)])
        self.assertEqual([t["title"] for t in cards[0]["tracks"]],
                         ["About a Girl", "Come as You Are"])

    def test_tagged_and_untagged_matches_are_combined(self):
        self.assertEqual(sorted(c["title"] for c in self.search("mtv")),
                         ["MTV Unplugged", "MTV Unplugged (DVD Audio)"])

    def test_tagged_tracks_are_not_matched_by_path(self):
        self.assertEqual(self.search("Alanis Rock"), [])


class FolderNamesTest(unittest.TestCase):
    def test_skips_stream_disc_and_repeated_folders(self):
        self.assertEqual(mpd_library._folder_names(UNTAGGED), ("Nirvana", "MTV Unplugged (DVD Audio)"))
        self.assertEqual(mpd_library._folder_names("Rock/Artist/Album/CD 2"), ("Artist", "Album"))
        self.assertEqual(mpd_library._folder_names("Rock/Artist/Album/Disc1"), ("Artist", "Album"))

    def test_keeps_album_names_that_start_like_disc_words(self):
        self.assertEqual(mpd_library._folder_names("Pop/Dean Martin/Volare"), ("Dean Martin", "Volare"))
        self.assertEqual(mpd_library._folder_names("Pop/Bee Gees/Disco"), ("Bee Gees", "Disco"))

    def test_strips_the_artist_prefix(self):
        self.assertEqual(mpd_library._folder_names("Rock/Nirvana/Nirvana - In Utero (1993)"),
                         ("Nirvana", "In Utero (1993)"))

    def test_a_top_level_folder_has_no_artist(self):
        self.assertEqual(mpd_library._folder_names("Album"), ("", "Album"))


if __name__ == "__main__":
    unittest.main()
