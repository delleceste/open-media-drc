"""The DR log's store: album figures from tracks, reports and partial hearings."""
import os
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "omdrc-ctrl", "src"))

import dr_store                                    # noqa: E402
import dr_volumes                                  # noqa: E402
import drmeter                                     # noqa: E402
from dr_store import DrStore, TrackWatch           # noqa: E402


def track(store, key, n, dr, seconds=240.0, complete=True, method="live"):
    return store.record_track(key, f"t{n}", dr=dr, dr_exact=dr + 0.2, seconds=seconds,
                              complete=complete, method=method, number=n)


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DrStore(os.path.join(self.tmp.name, "state", dr_store.DB_FILE),
                             dr_volumes.Volumes(create=False))

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

    def test_recent_tracks_include_unrated_albums_and_only_local_measurements(self):
        self.store.upsert_album("qobuz:1", "qobuz", "1", title="New album", artist="X", image="cover.jpg")
        track(self.store, "qobuz:1", 1, 10, seconds=40, complete=False)
        track(self.store, "qobuz:1", 2, 12, seconds=100, complete=False)
        self.store.upsert_album("qobuz:2", "qobuz", "2", title="Old album")
        track(self.store, "qobuz:2", 1, 8)
        with self.store._lock, self.store._connect() as db:
            db.execute("UPDATE track SET at = ? WHERE album_key = ?", (time.time() - 8 * 86400, "qobuz:2"))
        recent = self.store.recent_tracks(time.time() - 86400)
        self.assertEqual(recent["count"], 2)
        self.assertEqual([row["image"] for row in recent["tracks"]], ["cover.jpg", "cover.jpg"])
        self.assertEqual((recent["summaries"]["qobuz:1"]["dr"], recent["summaries"]["qobuz:1"]["kind"]),
                         (11, "estimate"))
        self.assertNotIn("qobuz:2", recent["summaries"])

    def test_recent_tracks_include_this_boxs_fresh_reports(self):
        now = time.time()
        for key, mtime, origin in (("local:new", now - 60, ""), ("local:old", now - 8 * 86400, ""),
                                   ("local:theirs", now - 60, "bee")):
            self.store.upsert_album(key, "local", key[6:], title=key)
            self.store.set_report(key, 9, 15, mtime)
        with self.store._lock, self.store._connect() as db:
            db.execute("UPDATE album SET report_origin = 'bee' WHERE key = 'local:theirs'")
        recent = self.store.recent_tracks(now - 86400)
        self.assertEqual([a["key"] for a in recent["reports"]], ["local:new"])
        self.assertEqual(recent["summaries"]["local:new"]["basis"], "report")
        self.assertEqual(recent["count"], 0)

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
        with open(report, "w") as f:                  # rewritten, same value: taken
            f.write("Number of tracks:  3\nOfficial DR value: DR12\n")
        os.utime(report, (2, 2))
        self.assertEqual(self.store.import_reports(root)["changed"], 1)
        with open(report, "w") as f:                  # another value: measured again first
            f.write("DR = 9\n")
        os.utime(report, (1, 1))
        result = self.store.import_reports(root)
        self.assertEqual((result["changed"], result["recheck"]), (0, ["Artist/Album"]))
        self.assertEqual(self.store.album("local:Artist/Album")["dr"]["dr"], 12)
        # the scan's own new measurement settles it
        self.assertEqual(self.store.import_folders(root, ["Artist/Album"], measured=True),
                         {"read": 1, "recheck": []})
        self.assertEqual(self.store.album("local:Artist/Album")["dr"]["dr"], 9)
        os.remove(report)
        self.assertEqual(self.store.import_reports(root)["removed"], 1)
        self.assertIsNone(self.store.album("local:Artist/Album"))

    def test_only_the_folders_named_are_read(self):
        root = os.path.join(self.tmp.name, "music")
        for rel, dr in (("Copied In", 11), ("Other", 8)):
            os.makedirs(os.path.join(root, rel))
            with open(os.path.join(root, rel, "dr14.txt"), "w") as f:
                f.write(f"Official DR value: DR{dr}\n")
        self.assertEqual(self.store.import_folders(root, ["Copied In", "Copied In"],
                                                   describe=lambda rel: {})["read"], 1)
        self.assertEqual(self.store.album("local:Copied In")["dr"]["dr"], 11)
        self.assertIsNone(self.store.album("local:Other"))
        self.assertEqual(self.store.import_folders(root, ["Copied In"])["read"], 0)   # unchanged
        self.assertEqual(self.store.import_folders(
            root, ["../music/Other", "/etc", "Missing"])["read"], 0)

    def test_a_disk_linked_into_the_library_is_imported(self):
        disk = os.path.join(self.tmp.name, "disk", "Roxy Music - Avalon")
        os.makedirs(disk)
        with open(os.path.join(disk, "dr14.txt"), "w") as f:
            f.write("Official DR value: DR13\n")
        root = os.path.join(self.tmp.name, "music")
        os.makedirs(root)
        os.symlink(os.path.join(self.tmp.name, "disk"), os.path.join(root, "USBHD2"))
        self.store.import_reports(root, describe=lambda rel: {})
        album = self.store.album("local:USBHD2/Roxy Music - Avalon")   # MPD's path, through the link
        self.assertEqual(album["dr"]["dr"], 13)

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
        rows = dr_store.parse_report_track_rows(
            "DR     Peak       RMS       Duration  Track\n"
            "DR13  -0.00 dB  -18.48 dB  17:08  02-02 - Dogs.flac\n"
            "DR10  -1.20 dB  -15.10 dB  04:31  03-Sheep.wav\n"
            "Official DR value: DR12\n")
        self.assertEqual(rows, [{"number": 1, "title": "Dogs", "dr": 13},
                                {"number": 2, "title": "Sheep", "dr": 10}])


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
        self.assertTrue(w.result(10.3, 198.0)["complete"])
        self.assertFalse(w.result(10.3, 150.0)["complete"])         # 150 of 200 s
        self.assertIsNone(w.result(10.3, 15.0))                     # 15 s: not kept

    def test_a_seek_or_a_late_start_makes_it_partial(self):
        w = TrackWatch(self.SONG, 100.0, 0)
        w.observe({**self.SONG, "elapsed": 1.0}, 100.6, 1)
        self.assertTrue(w.observe({**self.SONG, "elapsed": 60.0}, 101.1, 2))
        self.assertEqual(w.seeks, [2])
        self.assertFalse(w.result(10.0, 200.0)["complete"])
        late = TrackWatch({**self.SONG, "elapsed": 40.0}, 100.0, 0)
        self.assertFalse(late.result(10.0, 200.0)["complete"])

    def test_the_start_is_exact_or_the_median_of_estimates(self):
        self.assertEqual(TrackWatch(self.SONG, 0.0, 0, start_frame=1234).start(500), 1234)
        w = TrackWatch(self.SONG, 0.0, 0)
        self.assertIsNone(w.start())
        for frames, elapsed in ((44100, 0.5), (66150 + 900, 1.0), (88200, 1.5)):
            w.estimate_start(frames, elapsed, 44100)
        self.assertEqual(w.start(latency=100), 22050 - 100)        # the odd one out ignored
        w.seeks.append(5)
        w.estimate_start(10 ** 9, 2.0, 44100)                       # after a seek: not used
        self.assertEqual(len(w.estimates), 3)

    def test_a_start_known_both_ways_measures_the_latency(self):
        w = TrackWatch(self.SONG, 0.0, 0, start_frame=10000)
        for i in range(4):
            w.estimate_start(10000 + 700 + 4410 * i, 0.1 * i, 44100)
        self.assertIsNone(w.latency_sample())                       # too few yet
        w.estimate_start(10000 + 700 + 4410 * 4, 0.4, 44100)
        self.assertEqual(w.latency_sample(), 700)

    def test_a_pause_is_not_a_seek(self):
        w = TrackWatch(self.SONG, 100.0, 0)
        w.observe({**self.SONG, "state": "pause", "elapsed": 10.0}, 110.0, 3)
        self.assertFalse(w.observe({**self.SONG, "elapsed": 10.2}, 500.0, 3))


class SubBlockTiming(unittest.TestCase):
    """A track measured inside a stream, from its own first sample, reads
    what the meter reads on the file."""
    RATE = 6000          # 3-second blocks of 18 000 frames, sub-blocks of 300

    def music(self, seconds, seed):
        rng = np.random.default_rng(seed)
        envelope = np.repeat(rng.uniform(0.05, 0.6, size=seconds * 4), self.RATE // 4)
        return (rng.standard_normal((len(envelope), 2)) * envelope[:, None] * 0.3).clip(-1, 1)

    def meter(self, x):
        m = drmeter.Meter(self.RATE, 2)
        block = m.block
        for i in range(0, len(x), block):
            m.feed(x[i:i + block])
        return m.result()["dr_exact"]

    def stream(self, *parts):
        subs = drmeter.SubBlocks(self.RATE, 2)
        for part in parts:
            for i in range(0, len(part), 977):        # arbitrary chunking
                subs.feed(part[i:i + 977])
        return subs

    def test_from_the_first_sample_it_is_the_files_value(self):
        track = self.music(47, 1)                     # ends in a partial block
        subs = self.stream(track)
        exact, seconds = subs.dr_between(0, len(track))
        self.assertAlmostEqual(round(exact, 2), self.meter(track), places=2)
        self.assertAlmostEqual(seconds, 47.0)

    def test_inside_a_stream_it_counts_from_the_tracks_start(self):
        # the previous track, then 1300 frames of silence: not on a sub-block edge
        before = np.vstack([self.music(20, 2), np.zeros((1300, 2))])
        track, after = self.music(47, 3), self.music(10, 4)
        start = len(before)
        subs = self.stream(before, track, after)
        exact, seconds = subs.dr_between(start, start + len(track))
        self.assertAlmostEqual(exact, self.meter(track), delta=0.05)
        self.assertAlmostEqual(seconds, 47.0, delta=0.1)

    def test_blocks_are_the_sources_length(self):
        subs = drmeter.SubBlocks(44100, 2)
        self.assertEqual(subs.block_for(44100), 132480)        # the reference's quirk
        self.assertEqual(subs.block_for(None), 132480)
        self.assertAlmostEqual(subs.block_for(96000), 132300)  # exactly 3 s
        self.assertEqual(TrackWatch({"audio": "96000:24:2"}, 0, 0).source_rate, 96000)
        self.assertIsNone(TrackWatch({"audio": ""}, 0, 0).source_rate)

    def test_a_longer_block_moves_the_cuts(self):
        track = self.music(47, 6)
        subs = self.stream(track)
        own, _ = subs.dr_between(0, len(track))
        longer, seconds = subs.dr_between(0, len(track), subs.block * 1.5)
        self.assertNotEqual(round(own, 3), round(longer, 3))
        self.assertAlmostEqual(seconds, 47.0)

    def test_trim_forgets_only_what_is_behind(self):
        subs = self.stream(self.music(10, 5))
        subs.trim(subs.sub * 5 + 1)
        self.assertEqual(subs.subs[0][0], subs.sub * 5)



class CueFolders(unittest.TestCase):
    def test_a_cue_track_belongs_to_the_folder_holding_the_sheet(self):
        import mpd_library
        self.assertEqual(mpd_library.album_folder("A/B/Album.cue/track0003"), "A/B")
        self.assertEqual(mpd_library.album_folder("A/B/01.flac"), "A/B")
        self.assertTrue(mpd_library.is_cue_track("A/x.CUE/track0001"))
        self.assertFalse(mpd_library.is_cue_track("A/01.flac"))


if __name__ == "__main__":
    unittest.main()
