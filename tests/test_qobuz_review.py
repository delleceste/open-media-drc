"""Review later: a ring of albums that ages by days of use, not calendar days."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omdrc-ctrl/src"))
from qobuz_review import ReviewList


class Clock:
    day = 1

    def __call__(self):
        return f"2026-01-{self.day:02d}"


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.store = ReviewList(str(Path(self.dir.name) / "r.json"), self.clock)

    def tearDown(self):
        self.dir.cleanup()

    def test_days_away_do_not_count(self):
        self.store.add({"id": "a", "title": "A"})
        self.clock.day = 2
        self.assertEqual(self.store.view()["items"][0]["left"], 9)
        self.store.view()                       # same day again: no change
        self.assertEqual(self.store.view()["items"][0]["left"], 9)
        self.clock.day = 30                     # four weeks away: one more day only
        self.assertEqual(self.store.view()["items"][0]["left"], 8)

    def test_expires_after_lifetime_days_of_use(self):
        self.store.add({"id": "a", "title": "A"})
        for day in range(2, 11):
            self.clock.day = day
            self.assertEqual(len(self.store.view()["items"]), 1)
        self.clock.day = 11
        self.assertEqual(self.store.view()["items"], [])

    def test_extend_note_remove(self):
        self.store.add({"id": "a", "title": "A"})
        self.clock.day = 5
        self.store.view()
        self.assertTrue(self.store.update("a", "extend"))
        self.assertTrue(self.store.update("a", "note", "for the organ"))
        item = self.store.view()["items"][0]
        self.assertEqual((item["left"], item["note"]), (19, "for the organ"))
        self.assertTrue(self.store.update("a", "remove"))
        self.assertFalse(self.store.update("a", "remove"))

    def test_ring_drops_oldest_and_resize(self):
        self.store.configure({"size": 3})
        for n in range(5):
            self.store.add({"id": f"x{n}"})
        self.assertEqual([i["id"] for i in self.store.view()["items"]], ["x2", "x3", "x4"])
        self.store.configure({"size": 1})
        self.assertEqual([i["id"] for i in self.store.view()["items"]], ["x4"])

    def test_readding_keeps_note_and_refreshes(self):
        self.store.add({"id": "a"}, "why")
        self.assertTrue(self.store.add({"id": "a"})["already"])
        self.assertEqual(self.store.view()["items"][0]["note"], "why")

    def test_bad_settings_rejected(self):
        with self.assertRaises(ValueError):
            self.store.configure({"size": 0})
        with self.assertRaises(ValueError):
            self.store.configure({"lifetime": "ten"})


if __name__ == "__main__":
    unittest.main()
