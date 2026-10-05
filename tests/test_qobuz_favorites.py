"""Folder mapping and Qobuz playlist mutations without account writes."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omdrc-ctrl/src"))
import qobuz_favorites as fav
from qobuz_search import QobuzError


class FakeCatalog:
    def __init__(self):
        self.calls = []
        self.playlists = [{"id": 7, "name": "Blow Up/2026/June", "owner": {"id": 10}}]
        self.tracks = [{"id": 1, "playlist_track_id": 91,
                        "album": {"id": "abc", "title": "First", "artist": {"name": "A"}}},
                       {"id": 2, "playlist_track_id": 92,
                        "album": {"id": "abc", "title": "First", "artist": {"name": "A"}}}]

    def _call(self, endpoint, params):
        self.calls.append((endpoint, params))
        if endpoint == "playlist/getUserPlaylists":
            return {"playlists": {"items": self.playlists, "total": len(self.playlists)}}
        if endpoint == "playlist/get":
            return {"tracks": {"items": self.tracks, "total": len(self.tracks)}}
        if endpoint == "playlist/create":
            self.playlists.append({"id": 8, "name": params["name"], "owner": {"id": 10}})
            return {"id": 8}
        if endpoint == "playlist/addTracks":
            self.tracks.append({"id": int(params["track_ids"]), "playlist_track_id": 93,
                                "album": {"id": "xyz", "title": "New"}})
        return {}

    def album(self, album_id):
        return {"track_list": [{"id": 101, "streamable": True}]}


class FolderTest(unittest.TestCase):
    def test_existing_names_map_to_reversible_paths(self):
        self.assertEqual(fav.legacy_path("BUJUL-AUG26"), "Blow Up/2026/JUL-AUG")
        self.assertEqual(fav.legacy_path("BUJUN26"), "Blow Up/2026/June")
        self.assertEqual(fav.legacy_path("BUDEC25"), "Blow Up/2025/December")
        self.assertEqual(fav.legacy_path("blow up May 2025"), "Blow Up/2025/May")
        self.assertEqual(fav.legacy_path("gramophone awards 2024"),
                         "Classical/Gramophone/Awards/2024")

    def test_group_tracks_and_remove_all_album_entries(self):
        cat = FakeCatalog()
        albums = fav.playlist_albums(cat, "7")
        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0]["playlist_track_ids"], ["91", "92"])
        fav.remove(cat, "10", "abc", "7")
        self.assertEqual(cat.calls[-1], ("playlist/deleteTracks",
                                         {"playlist_id": "7", "playlist_track_ids": "91,92"}))

    def test_rejects_foreign_playlist_and_reserved_path(self):
        cat = FakeCatalog()
        with self.assertRaises(QobuzError):
            fav.remove(cat, "10", "abc", "999")
        with self.assertRaises(QobuzError):
            fav.valid_path("Qobuz/Other")

    def test_add_reuses_folder_and_does_not_duplicate_album(self):
        cat = FakeCatalog()
        result = fav.add(cat, "10", "abc", "Blow Up/2026/June")
        self.assertTrue(result["already_present"])
        self.assertFalse(any(endpoint == "playlist/addTracks" for endpoint, _ in cat.calls))
        fav.add(cat, "10", "xyz", "Blow Up/2026/June")
        self.assertEqual(cat.calls[-1], ("playlist/addTracks",
                                         {"playlist_id": "7", "track_ids": "101"}))


if __name__ == "__main__":
    unittest.main()
