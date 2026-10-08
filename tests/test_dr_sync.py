"""Two boxes sharing the DR log through one git repository."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "omdrc-ctrl", "src"))

import dr_volumes                                  # noqa: E402
from dr_store import DrStore                       # noqa: E402
from dr_sync import GitSync, SyncError, safe_repo  # noqa: E402


@unittest.skipUnless(shutil.which("git"), "git not installed")
class TwoBoxes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "shared.git")
        subprocess.run(["git", "init", "--quiet", "--bare", self.repo], check=True)
        self.home, self.office = self.box("home"), self.box("office")

    def tearDown(self):
        self.tmp.cleanup()

    def box(self, name):
        store = DrStore(os.path.join(self.tmp.name, name, "dr.sqlite"), dr_volumes.Volumes(create=False))
        sync = GitSync(store, self.repo, name, os.path.join(self.tmp.name, name, "dr-sync"))
        return store, sync

    def hear(self, store, album, track, dr, complete=True, count=2):
        store.upsert_album(album, album.split(":")[0], album.split(":")[1],
                           title=album, track_count=count)
        store.record_track(album, track, dr=dr, dr_exact=dr + .1, seconds=200.0,
                           complete=complete, method="live", number=int(track[-1]))

    def test_each_box_sees_what_the_other_heard(self):
        home, home_sync = self.home
        office, office_sync = self.office
        self.hear(home, "qobuz:1", "t1", 12)
        self.hear(office, "qobuz:1", "t2", 10)
        self.hear(office, "qobuz:2", "t1", 8, complete=False)
        for sync in (home_sync, office_sync, home_sync):
            state = sync.run_once()
            self.assertTrue(state["ok"], state["error"])
        # the album is exact at home: one track heard here, one at the office
        figure = home.album("qobuz:1")["dr"]
        self.assertEqual((figure["dr"], figure["kind"], figure["origins"]), (11, "exact", ["", "office"]))
        self.assertEqual(office.album("qobuz:1")["dr"]["kind"], "exact")
        self.assertIn("qobuz:2", {a["key"] for a in home.ranking()["albums"]})
        self.assertEqual(set(home_sync.status()["boxes"]), {"office"})

    def test_the_best_measurement_wins_and_own_rows_are_kept(self):
        home, home_sync = self.home
        office, office_sync = self.office
        self.hear(home, "qobuz:1", "t1", 9, complete=False)
        self.hear(office, "qobuz:1", "t1", 11, complete=True)
        office_sync.run_once()
        home_sync.run_once()
        tracks = home.album("qobuz:1")["tracks"]
        self.assertEqual(len(tracks), 2)                       # both kept
        self.assertEqual(home.album("qobuz:1")["dr"]["origins"], ["office"])   # the whole one used
        # home exports only its own row, never the office's
        rows = home.export_rows()
        self.assertEqual([r["track"]["dr"] for r in rows if "track" in r], [9])

    def test_another_boxs_local_folders_are_not_ours(self):
        home, home_sync = self.home
        office, office_sync = self.office
        office.upsert_album("local:Rock/X", "local", "Rock/X", title="X", track_count=3)
        office.set_report("local:Rock/X", 13, 3, 1.0)
        office_sync.run_once()
        home_sync.run_once()
        self.assertIsNone(home.album("local:Rock/X"))
        album = home.album("local@office:Rock/X")
        self.assertEqual((album["dr"]["dr"], album["origin"]), (13, "office"))
        # a later export without it removes it
        office.set_report("local:Rock/X", None, None, None)
        office_sync.run_once()
        home_sync.run_once()
        self.assertIsNone(home.album("local@office:Rock/X"))

    def test_unchanged_log_makes_no_commit(self):
        home, home_sync = self.home
        self.hear(home, "qobuz:1", "t1", 12)
        home_sync.run_once()
        count = lambda: subprocess.run(["git", "rev-list", "--count", "main"], cwd=self.repo,
                                       capture_output=True, text=True).stdout.strip()
        before = count()
        home_sync.run_once()
        self.assertEqual(count(), before)

    def test_a_bad_repository_is_reported_not_raised(self):
        store, _ = self.home
        sync = GitSync(store, os.path.join(self.tmp.name, "missing.git"), "home",
                       os.path.join(self.tmp.name, "other", "dr-sync"))
        state = sync.run_once()
        self.assertFalse(state["ok"])
        self.assertIn("git clone", state["error"])


class Names(unittest.TestCase):
    def test_box_names_and_credentials(self):
        with self.assertRaises(SyncError):
            GitSync(None, "x", "bad name", "/tmp/x")
        self.assertEqual(safe_repo("https://user:tok@github.com/a/b.git"), "https://github.com/a/b.git")


if __name__ == "__main__":
    unittest.main()
