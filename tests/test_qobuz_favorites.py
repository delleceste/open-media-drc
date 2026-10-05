"""Folder mapping and Qobuz playlist mutations without account writes."""
import sys
from pathlib import Path
import tempfile
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
        if endpoint == "playlist/deleteTracks":
            ids = set(params["playlist_track_ids"].split(","))
            self.tracks = [t for t in self.tracks if str(t["playlist_track_id"]) not in ids]
        if endpoint == "playlist/delete":
            self.playlists = [p for p in self.playlists if str(p["id"]) != params["playlist_id"]]
        if endpoint == "playlist/update":
            next(p for p in self.playlists if str(p["id"]) == params["playlist_id"])["name"] = params["name"]
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
        self.assertEqual(fav.legacy_path("* Classica *"), "Classical/**")
        self.assertEqual(fav.legacy_path("* Classical"), "Classical/**")
        self.assertEqual(fav.legacy_path("Discover BIS"), "Classical/BIS")
        self.assertEqual(fav.legacy_path("label: aparte"), "Classical/Aparté")
        self.assertEqual(fav.legacy_path("Discover DG"), "Classical/Deutsche Grammophon")

    def test_group_tracks_and_remove_all_album_entries(self):
        cat = FakeCatalog()
        albums = fav.playlist_albums(cat, "7")
        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0]["playlist_track_ids"], ["91", "92"])
        self.assertTrue(fav.remove(cat, "10", "abc", "7"))
        self.assertIn(("playlist/deleteTracks",
                       {"playlist_id": "7", "playlist_track_ids": "91,92"}), cat.calls)
        self.assertEqual(cat.calls[-1], ("playlist/delete", {"playlist_id": "7"}))

    def test_folder_stays_when_another_album_remains(self):
        cat = FakeCatalog()
        cat.tracks.append({"id": 3, "playlist_track_id": 93,
                           "album": {"id": "xyz", "title": "Other"}})
        self.assertFalse(fav.remove(cat, "10", "abc", "7"))
        self.assertEqual(len(cat.playlists), 1)

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

    def test_same_named_playlists_choose_the_one_with_more_tracks(self):
        cat = FakeCatalog()
        cat.playlists = [
            {"id": 7, "name": "Classical/**", "owner": {"id": 10}, "tracks_count": 1},
            {"id": 8, "name": "Classical/**", "owner": {"id": 10}, "tracks_count": 19},
        ]
        cat.tracks = []
        fav.add(cat, "10", "xyz", "Classical/**")
        self.assertEqual(cat.calls[-1][1]["playlist_id"], "8")

    def test_cover_samples_and_shared_order_store(self):
        cat = FakeCatalog()
        for track in cat.tracks:
            track["album"]["image"] = {"small": "https://example.test/cover.jpg"}
        cards = [{"id": "unfiled", "image": "https://example.test/unfiled.jpg"}]
        unfiled, covers = fav.library_snapshot(cat, "10", cat.playlists, cards)
        self.assertEqual(unfiled, cards)
        self.assertEqual(len(covers["Blow Up"]), 1)
        self.assertEqual(covers["Qobuz"], [cards[0]["image"]])
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "order.json")
            store = fav.LibraryOrder(path)
            self.assertEqual(store.all(), {})
            store.save("Classical", ["f:Classical/BIS", "a:abc"])
            self.assertEqual(fav.LibraryOrder(path).all()["Classical"],
                             ["f:Classical/BIS", "a:abc"])
            with self.assertRaises(QobuzError):
                store.save("Classical", ["a:abc", "a:abc"])

    def test_rename_and_delete_folder_subtrees_and_saved_positions(self):
        cat = FakeCatalog()
        cat.playlists.append({"id": 8, "name": "Blow Up/2026/July", "owner": {"id": 10}})
        cat.playlists.append({"id": 9, "name": "Other", "owner": {"id": 10}})
        with tempfile.TemporaryDirectory() as directory:
            store = fav.LibraryOrder(str(Path(directory) / "order.json"))
            store.save("", ["f:Other", "f:Blow Up"])
            store.save("Blow Up", ["f:Blow Up/2026"])
            store.save("Blow Up/2026", ["f:Blow Up/2026/July", "f:Blow Up/2026/June"])
            result = fav.folder_action(cat, "10", "Blow Up", "rename", store, "Magazine")
            self.assertEqual(result["path"], "Magazine")
            self.assertEqual([p["name"] for p in cat.playlists[:2]],
                             ["Magazine/2026/June", "Magazine/2026/July"])
            self.assertEqual(store.all()[""], ["f:Other", "f:Magazine"])
            self.assertEqual(store.all()["Magazine/2026"],
                             ["f:Magazine/2026/July", "f:Magazine/2026/June"])
            with self.assertRaises(QobuzError):
                fav.folder_action(cat, "10", "Magazine", "rename", store, "Other")
            fav.folder_action(cat, "10", "Magazine", "delete", store)
            self.assertEqual([p["name"] for p in cat.playlists], ["Other"])
            self.assertEqual(store.all(), {"": ["f:Other"]})

    def test_move_folder_into_another_and_keep_child_order(self):
        cat = FakeCatalog()
        cat.playlists.extend([
            {"id": 8, "name": "Blow Up/2026/July", "owner": {"id": 10}},
            {"id": 9, "name": "Other", "owner": {"id": 10}},
        ])
        with tempfile.TemporaryDirectory() as directory:
            store = fav.LibraryOrder(str(Path(directory) / "order.json"))
            store.save("", ["f:Other", "f:Blow Up"])
            store.save("Blow Up", ["f:Blow Up/2026"])
            store.save("Blow Up/2026", ["f:Blow Up/2026/July", "f:Blow Up/2026/June"])
            with self.assertRaises(QobuzError):
                fav.folder_action(cat, "10", "Blow Up", "move", store, target="Blow Up/2026")
            result = fav.folder_action(cat, "10", "Blow Up/2026", "move", store, target="Other")
            self.assertEqual(result["path"], "Other/2026")
            self.assertEqual([p["name"] for p in cat.playlists[:2]],
                             ["Other/2026/June", "Other/2026/July"])
            self.assertEqual(store.all()["Blow Up"], [])
            self.assertEqual(store.all()["Other"], ["f:Other/2026"])
            self.assertEqual(store.all()["Other/2026"],
                             ["f:Other/2026/July", "f:Other/2026/June"])


if __name__ == "__main__":
    unittest.main()
