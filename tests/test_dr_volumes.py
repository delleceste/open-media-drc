"""A drive moved between boxes keeps its albums: keys by volume marker."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "omdrc-ctrl", "src"))

import dr_volumes                                  # noqa: E402
from dr_store import DrStore                       # noqa: E402


class Drives(dr_volumes.Volumes):
    """Volumes whose mount points are the test's own folders, never /tmp's."""

    def __init__(self, *mounts, create=True):
        super().__init__(create=create)
        self.mounts = [os.path.realpath(m) for m in mounts]

    def mount_of(self, path):
        for mount in self.mounts:
            if path == mount or path.startswith(mount + os.sep):
                return mount
        return os.sep


def report(folder, dr, tracks=2):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "dr14.txt"), "w") as f:
        f.write(f"Official DR value: DR{dr}\nNumber of tracks: {tracks}\n")


class DriveKeys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.disk = os.path.join(t, "disk")
        report(os.path.join(self.disk, "musica", "Roxy Music - Avalon"), 13)
        # box A links the drive into its library; box B's library is a folder on it
        self.music_a = os.path.join(t, "a", "music")
        os.makedirs(self.music_a)
        os.symlink(self.disk, os.path.join(self.music_a, "USBHD2"))
        self.music_b = os.path.join(self.disk, "musica")
        self.a = DrStore(os.path.join(t, "a", "dr.sqlite"), Drives(self.disk))
        self.b = DrStore(os.path.join(t, "b", "dr.sqlite"), Drives(self.disk))

    def tearDown(self):
        self.tmp.cleanup()

    def key(self):
        with open(os.path.join(self.disk, dr_volumes.MARKER)) as f:
            return "vol:" + f.read().strip() + ":musica/Roxy Music - Avalon"

    def ranking(self, store):
        return [a["key"] for a in store.ranking()["albums"]]

    def test_the_same_folder_has_the_same_key_through_either_library(self):
        a = dr_volumes.album_key(self.music_a, "USBHD2/musica/Roxy Music - Avalon", self.a.volumes)
        b = dr_volumes.album_key(self.music_b, "Roxy Music - Avalon", self.b.volumes)
        self.assertEqual(a, b)
        self.assertEqual(a, self.key())

    def test_without_a_marker_the_folder_keeps_its_local_key(self):
        volumes = Drives(self.disk, create=False)
        self.assertEqual(dr_volumes.album_key(self.music_b, "Roxy Music - Avalon", volumes),
                         "local:Roxy Music - Avalon")
        self.assertFalse(os.path.exists(os.path.join(self.disk, dr_volumes.MARKER)))
        self.assertIsNone(Drives().key(self.music_b))      # the root filesystem: no marker

    def test_a_moved_drive_is_one_album_on_both_boxes(self):
        self.a.import_reports(self.music_a)
        self.assertEqual(self.ranking(self.a), [self.key()])
        self.b.import_box("a", self.a.export_rows())
        album = self.b.album(self.key())
        self.assertEqual((album["dr"]["dr"], album["report_origin"]), (13, "a"))
        self.assertEqual(self.b.export_rows(), [])         # not B's to pass on
        # the drive is plugged into B: it reads the same report, no second album
        self.b.import_reports(self.music_b)
        self.assertEqual(self.ranking(self.b), [self.key()])
        self.assertEqual(self.b.album(self.key())["report_origin"], "")
        self.b.import_box("a", self.a.export_rows())
        self.assertEqual(self.ranking(self.b), [self.key()])

    def test_listening_on_both_boxes_adds_up(self):
        for store, music, rel, n in ((self.a, self.music_a, "USBHD2/musica/Roxy Music - Avalon", 1),
                                     (self.b, self.music_b, "Roxy Music - Avalon", 2)):
            key = dr_volumes.album_key(music, rel, store.volumes)
            store.upsert_album(key, "local", rel, track_count=2)
            store.record_track(key, f"t{n}", dr=10 + n, dr_exact=10.0 + n, seconds=200.0,
                               complete=True, method="live", number=n)
        self.b.import_box("a", self.a.export_rows())
        figure = self.b.album(self.key())["dr"]
        self.assertEqual((figure["kind"], figure["heard"], figure["origins"]), ("exact", 2, ["", "a"]))

    def test_an_unplugged_drive_keeps_its_reports(self):
        self.a.import_reports(self.music_a)
        os.unlink(os.path.join(self.music_a, "USBHD2"))
        self.a.import_reports(self.music_a)
        self.assertEqual(self.a.album(self.key())["dr"]["dr"], 13)
        self.assertEqual(len(self.a.export_rows()), 1)
        # plugged back with the report deleted: now it has gone
        os.symlink(self.disk, os.path.join(self.music_a, "USBHD2"))
        os.unlink(os.path.join(self.music_b, "Roxy Music - Avalon", "dr14.txt"))
        report(os.path.join(self.music_b, "Other"), 9)
        self.a.import_reports(self.music_a)
        self.assertIsNone(self.a.album(self.key()))

    def test_a_report_the_other_box_dropped_goes(self):
        self.a.import_reports(self.music_a)
        self.b.import_box("a", self.a.export_rows())
        self.b.import_box("a", [])
        self.assertIsNone(self.b.album(self.key()))

    def test_local_rows_are_rekeyed_once_the_drive_is_known(self):
        old = DrStore(self.a.path, Drives(self.disk, create=False))
        old.import_reports(self.music_a)
        local = "local:USBHD2/musica/Roxy Music - Avalon"
        old.record_track(local, "t1", dr=12, dr_exact=12.1, seconds=200.0, complete=True,
                         method="live", number=1)
        self.assertEqual(self.ranking(old), [local])
        result = self.a.import_reports(self.music_a)
        self.assertEqual(result["adopted"], 1)
        self.assertIsNone(self.a.album(local))
        album = self.a.album(self.key())
        self.assertEqual((album["dr"]["dr"], len(album["tracks"]), album["ref"]),
                         (13, 1, "USBHD2/musica/Roxy Music - Avalon"))
        self.assertEqual(self.ranking(self.a), [self.key()])

    def test_the_folder_of_a_drive_album_is_found_here(self):
        self.a.import_reports(self.music_a)
        self.assertEqual(self.a.volumes.folder(self.key()),
                         os.path.join(os.path.realpath(self.disk), "musica", "Roxy Music - Avalon"))
        self.assertIsNone(self.a.volumes.folder("vol:" + "0" * 36 + ":x"))
        self.assertIsNone(self.a.volumes.folder(self.key().rsplit(":", 1)[0] + ":../x"))

    def test_the_signature_changes_when_a_drive_comes(self):
        before = dr_volumes.signature(self.music_a)
        os.unlink(os.path.join(self.music_a, "USBHD2"))
        self.assertNotEqual(dr_volumes.signature(self.music_a), before)


if __name__ == "__main__":
    unittest.main()
