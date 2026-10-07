"""The DR log's store: album figures from tracks, reports and partial hearings."""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "omdrc-ctrl", "src"))

import dr_store                                    # noqa: E402
import drmeter                                     # noqa: E402
from dr_store import DrStore, TrackWatch           # noqa: E402


def track(store, key, n, dr, seconds=240.0, complete=True, method="live"):
    return store.record_track(key, f"t{n}", dr=dr, dr_exact=dr + 0.2, seconds=seconds,
                              complete=complete, method=method, number=n)


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DrStore(os.path.join(self.tmp.name, "state", dr_store.DB_FILE))

    def tearDown(self):
        self.tmp.cleanup()

    def test_partial_hearing_is_an_estimate_and_a_whole_one_exact(self):
        self.store.upsert_album("qobuz:1", "qobuz", "1", title="A", artist="X", track_count=3)
        track(self.store, "qobuz:1", 1, 9, seconds=60, complete=False)
        self.assertIsNone(self.store.album("qobuz:1")["dr"]["dr"])     # under two minutes
        track(self.store, "qobuz:1", 2, 11)
        figure = self.store.album("qobuz:1")["dr"]
        self.assertEqual((figure["dr"], figure["kind"]), (10, "estimate"))
        track(self.store, "qobuz:1", 1, 9)             # now heard whole
        track(self.store, "qobuz:1", 3, 12)
        figure = self.store.album("qobuz:1")["dr"]
        self.assertEqual((figure["dr"], figure["kind"], figure["basis"]), (11, "exact", "listened"))

    def test_a_better_measurement_is_never_replaced_by_a_worse_one(self):
        self.store.upsert_album("qobuz:1", "qobuz", "1", track_count=1)
        self.assertTrue(track(self.store, "qobuz:1", 1, 8, method="measured"))
        self.assertFalse(track(self.store, "qobuz:1", 1, 12, complete=False))
        self.assertFalse(track(self.store, "qobuz:1", 1, 12, method="live"))
        self.assertEqual(self.store.album("qobuz:1")["tracks"][0]["dr"], 8)
        self.assertTrue(track(self.store, "qobuz:1", 1, 9, method="measured"))
        self.assertEqual(self.store.album("qobuz:1")["dr"]["basis"], "measured")

    def test_unknown_track_count_never_makes_an_album_exact(self):
        self.store.upsert_album("tags:x\x1fy", "stream", title="y", artist="x")
        for n in range(5):
            track(self.store, "tags:x\x1fy", n, 10)
        self.assertEqual(self.store.album("tags:x\x1fy")["dr"]["kind"], "estimate")

    def test_empty_values_never_overwrite_known_ones(self):
        self.store.upsert_album("qobuz:1", "qobuz", "1", title="Known", track_count=4)
        self.store.upsert_album("qobuz:1", "qobuz", "1", title="", track_count=None)
        album = self.store.album("qobuz:1")
        self.assertEqual((album["title"], album["track_count"]), ("Known", 4))

    def test_ranking_orders_by_dr_and_filters(self):
        for key, source, dr in (("qobuz:1", "qobuz", 8), ("qobuz:2", "qobuz", 13),
                                ("local:a", "local", 11)):
            self.store.upsert_album(key, source, key.split(":")[1], title=key, track_count=1)
            track(self.store, key, 1, dr)
        self.store.upsert_album("qobuz:3", "qobuz", "3", title="partial", track_count=9)
        track(self.store, "qobuz:3", 1, 14, seconds=300, complete=False)
        ranked = self.store.ranking()
        self.assertEqual([a["key"] for a in ranked["albums"]],
                         ["qobuz:3", "qobuz:2", "local:a", "qobuz:1"])
        self.assertEqual([a["key"] for a in self.store.ranking(exact_only=True)["albums"]],
                         ["qobuz:2", "local:a", "qobuz:1"])
        self.assertEqual([a["key"] for a in self.store.ranking(source="local")["albums"]],
                         ["local:a"])
        self.assertEqual(self.store.ranking(text="partial")["count"], 1)
        self.assertEqual(set(self.store.lookup(["qobuz:1", "qobuz:9", ""])), {"qobuz:1"})

    def test_reports_are_imported_updated_and_dropped(self):
        root = os.path.join(self.tmp.name, "music")
        folder = os.path.join(root, "Artist", "Album")
        os.makedirs(folder)
        for n in range(3):
            open(os.path.join(folder, f"{n}.flac"), "w").close()
        report = os.path.join(folder, "dr14.txt")
        with open(report, "w") as f:
            f.write("Number of tracks:  3\nOfficial DR value: DR12\n")
        result = self.store.import_reports(root, describe=lambda rel: {"title": None, "artist": ""})
        self.assertEqual((result["added"], result["reports"]), (1, 1))
        album = self.store.album("local:Artist/Album")
        # no tags: the folder gives the title, and a parent folder is no artist
        self.assertEqual((album["title"], album["artist"], album["track_count"]),
                         ("Album", "", 3))
        self.assertEqual((album["dr"]["dr"], album["dr"]["basis"]), (12, "report"))
        self.assertEqual(self.store.import_reports(root)["changed"], 0)   # unchanged mtime
        with open(report, "w") as f:
            f.write("DR = 9\n")
        os.utime(report, (1, 1))
        self.assertEqual(self.store.import_reports(root)["changed"], 1)
        self.assertEqual(self.store.album("local:Artist/Album")["dr"]["dr"], 9)
        os.remove(report)
        self.assertEqual(self.store.import_reports(root)["removed"], 1)
        self.assertIsNone(self.store.album("local:Artist/Album"))

    def test_an_untagged_rip_is_named_from_its_folder(self):
        folder = os.path.join(self.tmp.name, "music", "Rock", "The Band - Stage Fright (1970)")
        os.makedirs(folder)
        with open(os.path.join(folder, "dr14.txt"), "w") as f:
            f.write("Official DR value: DR11\n")
        self.store.import_reports(os.path.join(self.tmp.name, "music"), describe=lambda rel: {})
        album = self.store.album("local:Rock/The Band - Stage Fright (1970)")
        self.assertEqual((album["artist"], album["title"]), ("The Band", "Stage Fright (1970)"))

    def test_tags_win_over_the_folder_name(self):
        folder = os.path.join(self.tmp.name, "music", "Rock", "The Band - Stage Fright (1970)")
        os.makedirs(folder)
        with open(os.path.join(folder, "dr14.txt"), "w") as f:
            f.write("Official DR value: DR11\n")
        self.store.import_reports(os.path.join(self.tmp.name, "music"), describe=lambda rel: {
            "title": "Stage Fright", "artist": "The Band", "year": 1970, "track_count": 10})
        album = self.store.album("local:Rock/The Band - Stage Fright (1970)")
        self.assertEqual((album["artist"], album["title"], album["year"], album["track_count"]),
                         ("The Band", "Stage Fright", 1970, 10))

    def test_report_parsing(self):
        self.assertEqual(dr_store.parse_report("Official DR value: DR7\nNumber of tracks: 10"),
                         {"dr": 7, "tracks": 10})
        self.assertEqual(dr_store.parse_report("DR = 14"), {"dr": 14, "tracks": None})
        self.assertIsNone(dr_store.parse_report("nothing"))


class Migration(unittest.TestCase):
    def test_a_database_from_before_sharing_keeps_its_rows(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.sqlite")
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE album (key TEXT PRIMARY KEY, source TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '', artist TEXT NOT NULL DEFAULT '', year INTEGER,
                    label TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '',
                    track_count INTEGER, report_dr INTEGER, report_tracks INTEGER, report_mtime REAL,
                    updated REAL NOT NULL);
                CREATE TABLE track (album_key TEXT NOT NULL, track_key TEXT NOT NULL, number INTEGER,
                    title TEXT NOT NULL DEFAULT '', dr INTEGER NOT NULL, dr_exact REAL NOT NULL,
                    seconds REAL NOT NULL, duration REAL, complete INTEGER NOT NULL, method TEXT NOT NULL,
                    at REAL NOT NULL, PRIMARY KEY (album_key, track_key));
                CREATE INDEX track_album ON track (album_key);
                INSERT INTO album (key, source, track_count, updated) VALUES ('qobuz:1', 'qobuz', 1, 0);
                INSERT INTO track VALUES ('qobuz:1', 't1', 1, 'A', 9, 9.2, 200, 200, 1, 'live', 0);
            """)
            db.commit()
            db.close()
            store = DrStore(path)
            album = store.album("qobuz:1")
            self.assertEqual((album["dr"]["dr"], album["tracks"][0]["origin"]), (9, ""))
            self.assertTrue(track(store, "qobuz:1", 2, 11))


class Watch(unittest.TestCase):
    SONG = {"file": "a.flac", "state": "play", "elapsed": 0.4, "duration": 200.0}

    def test_whole_track_heard_from_the_start_is_complete(self):
        w = TrackWatch(self.SONG, 100.0, 0)
        for i in range(1, 400):
            self.assertFalse(w.observe({**self.SONG, "elapsed": 0.4 + i * 0.5}, 100.0 + i * 0.5, i))
        self.assertTrue(w.result(10.3, 66, 3.0)["complete"])
        self.assertFalse(w.result(10.3, 50, 3.0)["complete"])      # 150 of 200 s
        self.assertIsNone(w.result(10.3, 5, 3.0))                  # 15 s: not kept

    def test_a_seek_or_a_late_start_makes_it_partial(self):
        w = TrackWatch(self.SONG, 100.0, 0)
        w.observe({**self.SONG, "elapsed": 1.0}, 100.6, 1)
        self.assertTrue(w.observe({**self.SONG, "elapsed": 60.0}, 101.1, 2))
        self.assertEqual(w.seeks, [2])
        self.assertFalse(w.result(10.0, 66, 3.0)["complete"])
        late = TrackWatch({**self.SONG, "elapsed": 40.0}, 100.0, 0)
        self.assertFalse(late.result(10.0, 66, 3.0)["complete"])

    def test_a_pause_is_not_a_seek(self):
        w = TrackWatch(self.SONG, 100.0, 0)
        w.observe({**self.SONG, "state": "pause", "elapsed": 10.0}, 110.0, 3)
        self.assertFalse(w.observe({**self.SONG, "elapsed": 10.2}, 500.0, 3))


class TrackBlocks(unittest.TestCase):
    def test_gap_slots_stay_out_of_the_track(self):
        estimate = drmeter.RollingEstimate(100, 2)
        tone = np.full((300, 2), 0.25, dtype=np.float32)
        tone[::50] = 0.5
        estimate.feed(tone)
        estimate.add_gap()
        estimate.feed(tone)
        self.assertEqual(len(estimate.blocks), 3)
        blocks = estimate.take_track()
        self.assertEqual(len(blocks), 2)
        self.assertEqual(estimate.track, [])
        self.assertAlmostEqual(drmeter.blocks_dr(blocks),
                               20 * np.log10(0.5 / np.sqrt(2 * np.mean(tone[:, 0] ** 2))),
                               places=4)
        self.assertIsNone(drmeter.blocks_dr(blocks[:1]))



class CueFolders(unittest.TestCase):
    def test_a_cue_track_belongs_to_the_folder_holding_the_sheet(self):
        import mpd_library
        self.assertEqual(mpd_library.album_folder("A/B/Album.cue/track0003"), "A/B")
        self.assertEqual(mpd_library.album_folder("A/B/01.flac"), "A/B")
        self.assertTrue(mpd_library.is_cue_track("A/x.CUE/track0001"))
        self.assertFalse(mpd_library.is_cue_track("A/01.flac"))


if __name__ == "__main__":
    unittest.main()
