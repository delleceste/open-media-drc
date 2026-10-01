#!/usr/bin/env python3
"""The Qobuz album search behind the panel's search page.

Qobuz's search has no label or date facet, so qobuz_search.py fetches the
plain answer and filters it.  These tests pin what that filtering must get
right with album objects shaped like the ones catalog/search and album/get
return: labels spelled several ways, dates from either field, a merge of the
plain query with the per-label queries that keeps each album's best position,
and performer credits that leave engineers and producers out.
"""
import datetime as dt
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

import qobuz_search as qs  # noqa: E402
import qobuz_web  # noqa: E402

SPEC = importlib.util.spec_from_file_location("omdrc_qobuz_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)

TODAY = dt.date(2026, 9, 27)


def album(album_id, title, label, date=None, released_at=None, streamable=True,
          artist="Some Orchestra", composer="Anton Bruckner"):
    item = {"id": album_id, "title": title, "label": {"id": 1, "name": label},
            "artist": {"name": artist}, "composer": {"name": composer},
            "image": {"small": f"https://img/{album_id}_230.jpg",
                      "large": f"https://img/{album_id}_600.jpg"},
            "tracks_count": 4, "duration": 3900, "streamable": streamable,
            "maximum_bit_depth": 24, "maximum_sampling_rate": 96.0,
            "genre": {"name": "Symphonies"}}
    if date:
        item["release_date_original"] = date
    if released_at is not None:
        item["released_at"] = released_at
    return item


def fake_qobuz(searches, albums=None):
    """A fetch() answering catalog/search from {query: [items]} (paged the way
    Qobuz pages) and album/get from {album_id: object}."""
    calls = []

    def fetch(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "catalog/search":
            items = searches.get(params["query"], [])
            start = params["offset"]
            return {"albums": {"items": items[start:start + params["limit"]],
                               "total": len(items), "offset": start,
                               "limit": params["limit"]}}
        if endpoint == "album/get":
            return (albums or {})[params["album_id"]]
        raise AssertionError(endpoint)
    return fetch, calls


def catalog(searches, albums=None, **settings):
    fetch, calls = fake_qobuz(searches, albums)
    return qs.QobuzCatalog(qs.Settings(**settings), fetch=fetch,
                           today=lambda: TODAY), calls


class SingleTrackRankingTest(unittest.TestCase):
    def test_single_tracks_remain_available_after_albums_in_both_orders(self):
        items = [album(str(i), "Debussy Preludes", "Decca", date=date)
                 for i, date in enumerate(["2026-01-01", "2024-01-01", "2025-01-01", "2023-01-01"])]
        items[0]["tracks_count"] = 1
        items[2]["tracks_count"] = 1
        items[3].pop("tracks_count")  # Unknown counts must not be penalized.
        for sort, expected in [("relevance", ["1", "3", "0", "2"]),
                               ("date", ["1", "3", "0", "2"])]:
            with self.subTest(sort=sort):
                cat, _ = catalog({"Debussy Preludes": items})
                seen = []
                with patch.object(qs, "PROGRESS_INTERVAL", 0):
                    answer = cat.search("Debussy Preludes", sort=sort, enrich=False,
                                        progress=seen.append)
                self.assertEqual([c["id"] for c in answer["results"]], expected)
                self.assertEqual(answer["count"], 4)
                self.assertEqual(answer["lowered"], 0)
                self.assertTrue(seen)
                self.assertEqual([c["id"] for c in seen[-1]["results"]], expected)

    def test_enrichment_prioritizes_albums_over_single_tracks(self):
        single = album("single", "Feux d'artifice", "Decca")
        single["tracks_count"] = 1
        full = album("full", "Preludes", "Decca")
        cat, calls = catalog({"Debussy": [single, full]}, {"full": full}, max_enrich=1)
        answer = cat.search("Debussy")
        self.assertEqual([c["id"] for c in answer["results"]], ["full", "single"])
        self.assertEqual([p["album_id"] for e, p in calls if e == "album/get"], ["full"])


class LabelTest(unittest.TestCase):
    def test_one_group_matches_every_spelling(self):
        decca = qs.parse_labels("Decca")[0]
        for name in ("Decca Music Group Ltd.", "Decca (UMO)", "DECCA Classics"):
            self.assertTrue(decca.matches(name), name)
        self.assertFalse(decca.matches("Deccan Records"))

    def test_patterns_match_whole_words_only(self):
        bis = qs.parse_labels("BIS: bis records, bis")[0]
        self.assertTrue(bis.matches("BIS"))
        self.assertTrue(bis.matches("BIS Records"))
        self.assertFalse(bis.matches("Brisbane Music"))
        self.assertFalse(bis.matches("Bissonnet"))

    def test_group_lines(self):
        groups = qs.parse_labels("""
            Pentatone
            Warner Classics: warner classics, erato   # comment
        """)
        self.assertEqual([g.name for g in groups], ["Pentatone", "Warner Classics"])
        self.assertEqual(groups[0].patterns, ("pentatone",))
        self.assertEqual(groups[1].patterns, ("warner classics", "erato"))

    def test_an_unconfigured_label_is_its_own_group(self):
        cat, _ = catalog({})
        group = cat.label_groups(["Challenge Classics"])[0]
        self.assertTrue(group.matches("Challenge Classics"))


class MetadataTest(unittest.TestCase):
    def test_date_prefers_release_date_original(self):
        self.assertEqual(qs.album_date({"release_date_original": "2024-03-01",
                                        "released_at": 0}), "2024-03-01")

    def test_date_falls_back_to_released_at(self):
        ts = int(dt.datetime(2022, 5, 6, 12, tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(qs.album_date({"released_at": ts}), "2022-05-06")

    def test_no_date(self):
        self.assertEqual(qs.album_date({}), "")

    def test_card(self):
        card = qs.album_card(album("abc1", "Symphony No. 7", "PENTATONE", "2025-01-10"))
        self.assertEqual(card["label"], "PENTATONE")
        self.assertEqual(card["year"], 2025)
        self.assertEqual(card["composer"], "Anton Bruckner")
        self.assertEqual(card["image"], "https://img/abc1_230.jpg")

    def test_performers_leave_the_studio_out(self):
        credits = ("Jakub Hrusa, Conductor, MainArtist - Bamberger Symphoniker, "
                   "Orchestra, MainArtist - Erdo Groot, Producer, Balance Engineer - "
                   "Anton Bruckner, Composer")
        self.assertEqual(qs.parse_performers(credits), [
            ("Jakub Hrusa", ["Conductor"]),
            ("Bamberger Symphoniker", ["Orchestra"])])

    def test_album_performers_most_present_first(self):
        tracks = [{"performers": "Soloist A, Piano - Orchestra B, Orchestra"},
                  {"performers": "Orchestra B, Orchestra"},
                  {"performers": "Orchestra B, Orchestra - Soloist A, Piano"}]
        self.assertEqual(qs.album_performers(tracks), [
            {"name": "Orchestra B", "roles": ["Orchestra"]},
            {"name": "Soloist A", "roles": ["Piano"]}])


class DateWindowTest(unittest.TestCase):
    def test_last_years_is_rolling(self):
        self.assertEqual(qs.date_window(last_years=2, today=TODAY), ("2024-09-27", ""))

    def test_calendar_years_are_inclusive(self):
        self.assertEqual(qs.date_window(from_year=2021, to_year=2023, today=TODAY),
                         ("2021-01-01", "2023-12-31"))

    def test_the_narrower_lower_bound_wins(self):
        self.assertEqual(qs.date_window(last_years=10, from_year=2021, today=TODAY)[0],
                         "2021-01-01")


class SearchTest(unittest.TestCase):
    SEARCHES = {
        "bruckner 7": [
            album("old", "Symphony No. 7 (1998)", "Decca Music Group Ltd.", "1998-02-01"),
            album("dg", "Symphony No. 7", "Deutsche Grammophon (DG)", "2025-06-01"),
            album("penta1", "Symphony No. 7", "PENTATONE", "2024-11-15"),
            album("nodate", "Symphony No. 7", "Decca (UMO)"),
            album("gone", "Symphony No. 7", "PENTATONE", "1999-05-01", streamable=False),
        ],
        # The label query reaches an album the plain one ranked out of reach,
        # and repeats one the plain query already had lower down.
        "bruckner 7 Pentatone": [
            album("penta2", "Symphony No. 7", "Pentatone Music", "2026-03-20"),
            album("penta1", "Symphony No. 7", "PENTATONE", "2024-11-15"),
        ],
        "bruckner 7 Decca": [
            album("decca", "Symphony No. 7", "Decca (UMO)", "2025-09-01"),
        ],
    }

    def search(self, **kw):
        cat, calls = catalog(self.SEARCHES, max_enrich=0)
        return cat.search("bruckner 7", **kw), calls

    def ids(self, answer):
        return [c["id"] for c in answer["results"]]

    def test_label_and_date_filters_combine(self):
        answer, calls = self.search(labels=["Pentatone"], last_years=2)
        self.assertEqual(self.ids(answer), ["penta2", "penta1"])
        self.assertEqual([p["query"] for _, p in calls],
                         ["bruckner 7", "bruckner 7 Pentatone"])

    def test_progress_reports_partial_results_in_the_final_order(self):
        seen = []
        with patch.object(qs, "PROGRESS_INTERVAL", 0):
            answer, _ = self.search(labels=["Pentatone", "Decca"], from_year=2021, sort="date",
                                    progress=seen.append)
        self.assertTrue(seen)
        self.assertTrue(all(p["partial"] for p in seen))
        self.assertNotIn("partial", answer)
        last = [c["id"] for c in seen[-1]["results"]]
        self.assertEqual(last, self.ids(answer))

    def test_several_labels(self):
        answer, _ = self.search(labels=["Pentatone", "Decca"], from_year=2021)
        self.assertEqual(self.ids(answer), ["penta2", "decca", "penta1"])

    def test_no_label_keeps_every_label(self):
        answer, _ = self.search(from_year=2021)
        self.assertEqual(self.ids(answer), ["dg", "penta1"])

    def test_undated_albums_drop_out_only_under_a_date_filter(self):
        self.assertIn("nodate", self.ids(self.search()[0]))
        self.assertNotIn("nodate", self.ids(self.search(from_year=1990)[0]))

    def test_unstreamable_albums_are_kept_marked_and_counted(self):
        """As in Qobuz's own list: shown, but with nothing to play."""
        answer, _ = self.search(labels=["Pentatone"])
        cards = {c["id"]: c for c in answer["results"]}
        self.assertFalse(cards["gone"]["streamable"])
        self.assertTrue(cards["penta1"]["streamable"])
        self.assertEqual(answer["unstreamable"], 1)

    def test_relevance_keeps_each_albums_best_position(self):
        answer, _ = self.search(labels=["Pentatone"], sort="relevance")
        # penta2 is first of its query; penta1 is 2nd of the label query,
        # better than 3rd of the plain one.
        self.assertEqual([(c["id"], c["rank"]) for c in answer["results"]],
                         [("penta2", 0), ("penta1", 1), ("gone", 4)])

    def test_no_filter_is_qobuzs_own_list(self):
        """Nothing ticked, no dates: Qobuz's releases for the text, in its
        order, one page to start with -- the filters only ever work on that."""
        many = [album(f"a{i}", "T", "L", f"20{i % 25:02d}-01-01") for i in range(120)]
        cat, calls = catalog({"x": many}, max_enrich=0)
        answer = cat.search("x")
        self.assertEqual(self.ids(answer), [f"a{i}" for i in range(50)])
        self.assertEqual([p["offset"] for _, p in calls], [0])
        self.assertTrue(answer["more"])
        self.assertEqual(answer["next_scan"], 100)

    def test_played_albums_join_only_a_filtered_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = qs.PlayedAlbums(tmp + "/p.json")
            store.record(album("mine", "Symphony No. 7", "PENTATONE", "2025-05-05"), [])
            cat, _ = catalog(self.SEARCHES, max_enrich=0)
            cat.played = store
            plain = cat.search("bruckner 7")
            filtered = cat.search("bruckner 7", labels=["Pentatone"])
        self.assertNotIn("mine", self.ids(plain))
        self.assertIn("mine", self.ids(filtered))

    def test_hidden_from_recent_stays_played_and_a_new_play_brings_it_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = qs.PlayedAlbums(tmp + "/p.json")
            store.record(album("a", "One", "PENTATONE", "2025-05-05"), [])
            store.record(album("b", "Two", "PENTATONE", "2025-05-05"), [])
            self.assertTrue(store.hide("a"))
            self.assertEqual([c["id"] for c in store.recent()], ["b"])
            self.assertEqual(store.counts()["a"], 1)             # still played
            self.assertIn("a", [i["id"] for i in store.matching("one")])
            again = qs.PlayedAlbums(tmp + "/p.json")               # kept in the file
            self.assertEqual([c["id"] for c in again.recent()], ["b"])
            self.assertTrue(again.hide("a", False))
            self.assertEqual([c["id"] for c in again.recent()], ["b", "a"])
            again.hide("a")
            again.record(album("a", "One", "PENTATONE", "2025-05-05"), [])
            self.assertEqual([c["id"] for c in again.recent()], ["a", "b"])
            self.assertEqual(again.counts()["a"], 2)
            self.assertFalse(again.hide("nope"))

    def test_labels_seen_are_reported_as_qobuz_spells_them(self):
        answer, _ = self.search(from_year=2021)
        self.assertEqual(answer["labels_seen"], [
            {"name": "Deutsche Grammophon (DG)", "count": 1,
             "groups": ["Deutsche Grammophon"]},
            {"name": "PENTATONE", "count": 1, "groups": ["Pentatone"]}])

    def test_label_only_search(self):
        cat, calls = catalog({"Pentatone": [album("p", "X", "PENTATONE", "2026-01-01")]},
                             max_enrich=0)
        answer = cat.search("", labels=["Pentatone"])
        self.assertEqual(self.ids(answer), ["p"])
        self.assertEqual([p["query"] for _, p in calls], ["Pentatone"])

    def test_nothing_to_search_for_is_refused_before_any_request(self):
        cat, calls = catalog({})
        with self.assertRaises(qs.QobuzError):
            cat.search("  ")
        self.assertEqual(calls, [])

    def test_unknown_sort_is_refused(self):
        cat, _ = catalog({})
        with self.assertRaises(qs.QobuzError):
            cat.search("x", sort="price")

    def test_paging_stops_at_the_total(self):
        many = [album(f"a{i}", "T", "L", "2020-01-01") for i in range(120)]
        cat, calls = catalog({"x": many}, max_enrich=0)
        answer = cat.search("x", from_year=2000)       # a filter reads deeper at once
        self.assertEqual(answer["considered"], 120)
        self.assertEqual(sorted(p["offset"] for _, p in calls), [0, 50, 100])
        self.assertFalse(answer["more"])

    def test_enough_matches_stop_the_reading(self):
        many = [album(f"a{i}", "T", "L", "2020-01-01") for i in range(3000)]
        cat, calls = catalog({"x": many}, max_enrich=0, scan=100, want=20)
        answer = cat.search("x", from_year=2000)
        self.assertEqual(len(calls), 2)
        self.assertTrue(answer["more"])
        self.assertEqual(answer["next_scan"], 200)

    def test_a_rare_match_is_read_for_deeper(self):
        # The only Pentatone album sits at position 700 of the plain query;
        # the label query finds nothing.  Reading doubles 100 -> 200 -> 400
        # -> 800 and stops there, having found it.
        many = [album(f"a{i}", "T", "Other", "2020-01-01") for i in range(3000)]
        many[700] = album("deep", "T", "PENTATONE", "2025-01-01")
        cat, calls = catalog({"x": many}, max_enrich=0, scan=100, want=1, auto_scan=1000)
        answer = cat.search("x", labels=["Pentatone"])
        self.assertEqual([c["id"] for c in answer["results"]], ["deep"])
        self.assertEqual(answer["scan"], 800)
        self.assertEqual(answer["queries"][0]["fetched"], 800)

    def test_auto_reading_stops_at_auto_scan_and_offers_more(self):
        many = [album(f"a{i}", "T", "Other", "2020-01-01") for i in range(3000)]
        cat, calls = catalog({"x": many}, max_enrich=0, scan=100, auto_scan=300)
        answer = cat.search("x", labels=["Pentatone"])
        self.assertEqual(answer["queries"][0]["fetched"], 300)
        self.assertTrue(answer["more"])
        self.assertEqual(answer["next_scan"], 600)

    def test_load_more_reads_only_the_new_pages(self):
        many = [album(f"a{i}", "T", "Other", "2020-01-01") for i in range(3000)]
        cat, calls = catalog({"x": many}, max_enrich=0, scan=100, auto_scan=100)
        cat.search("x")                                 # the first page
        calls.clear()
        answer = cat.search("x", scan=300)
        self.assertEqual(sorted(p["offset"] for _, p in calls), [50, 100, 150, 200, 250])
        self.assertEqual(answer["considered"], 300)

    def test_a_failing_later_page_ends_only_its_query(self):
        many = [album(f"a{i}", "T", "L", "2020-01-01") for i in range(200)]
        fetch, _ = fake_qobuz({"x": many})

        def flaky(endpoint, params):
            if params.get("offset") == 100:
                raise qs.QobuzError("HTTP 500")
            return fetch(endpoint, params)

        cat = qs.QobuzCatalog(qs.Settings(max_enrich=0), fetch=flaky, today=lambda: TODAY)
        answer = cat.search("x", from_year=2000)
        self.assertEqual(answer["considered"], 100)
        self.assertEqual(answer["queries"][0]["error"], "HTTP 500")

    def test_a_failing_first_page_fails_the_search(self):
        def refuse(endpoint, params):
            raise qs.QobuzAuthError("HTTP 401")
        cat = qs.QobuzCatalog(qs.Settings(), fetch=refuse, today=lambda: TODAY)
        with self.assertRaises(qs.QobuzAuthError):
            cat.search("x")

    def test_searches_are_cached(self):
        cat, calls = catalog(self.SEARCHES, max_enrich=0)
        cat.search("bruckner 7")
        cat.search("Bruckner  7")
        self.assertEqual(len(calls), 1)

    def test_enrichment_adds_performers_and_survives_a_failing_album(self):
        albums = {"penta1": {**album("penta1", "S7", "PENTATONE", "2024-11-15"),
                             "tracks": {"items": [
                                 {"performers": "Jakub Hrusa, Conductor"}]}}}

        fetch, _ = fake_qobuz(self.SEARCHES, albums)

        def flaky(endpoint, params):
            if endpoint == "album/get" and params["album_id"] == "penta2":
                raise qs.QobuzError("HTTP 500")
            return fetch(endpoint, params)

        cat = qs.QobuzCatalog(qs.Settings(), fetch=flaky, today=lambda: TODAY)
        answer = cat.search("bruckner 7", labels=["Pentatone"], last_years=2)
        cards = {c["id"]: c for c in answer["results"]}
        self.assertNotIn("performers", cards["penta2"])
        self.assertEqual(cards["penta1"]["performers"],
                         [{"name": "Jakub Hrusa", "roles": ["Conductor"]}])
        self.assertEqual(answer["enriched"], 1)


class LoweredTest(unittest.TestCase):
    """The − on a result: an album, a label or an artist moved to the end of
    every list, never dropped, and listed to restore or clear."""

    SEARCHES = {"x": [
        album("a", "One", "Decca Music Group Ltd.", "2024-01-01"),
        album("b", "Two", "PENTATONE", "2024-01-02", artist="Some Conductor"),
        album("c", "Three", "PENTATONE", "2024-01-03"),
        album("d", "Four", "BIS", "2024-01-04"),
    ]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = qs.LoweredList(self.tmp.name + "/sub/lowered.json")

    def tearDown(self):
        self.tmp.cleanup()

    def search(self, albums=None, **kw):
        cat, _ = catalog(self.SEARCHES, albums, max_enrich=0)
        cat.lowered = self.store
        return cat.search("x", **kw)

    def order(self, answer):
        return [(c["id"], c.get("lowered", {}).get("kind")) for c in answer["results"]]

    def test_an_album_goes_to_the_end_marked(self):
        self.store.add("album", "b", "Two")
        answer = self.search()
        self.assertEqual(self.order(answer),
                         [("a", None), ("c", None), ("d", None), ("b", "album")])
        self.assertEqual(answer["lowered"], 1)

    def test_a_label_lowers_every_spelling_of_it(self):
        self.store.add("label", "Decca", "Decca")
        self.store.add("label", "pentatone", "Pentatone")
        self.assertEqual([i for i, _ in self.order(self.search())], ["d", "a", "b", "c"])

    def test_an_artist_matches_the_album_artist_or_a_performer(self):
        self.store.add("artist", "Some Conductor")
        self.store.add("artist", "Jakub Hrusa")
        albums = {i: {**a, "tracks": {"items": [{"performers": "Jakub Hrusa, Conductor"}]}}
                  for i, a in ((a["id"], a) for a in self.SEARCHES["x"]) if i == "d"}
        albums.update({i: {**a, "tracks": {"items": []}} for i, a in
                       ((a["id"], a) for a in self.SEARCHES["x"]) if i != "d"})
        cat, calls = catalog(self.SEARCHES, albums, max_enrich=10)
        cat.lowered = self.store
        answer = cat.search("x")
        self.assertEqual(self.order(answer),
                         [("a", None), ("c", None), ("b", "artist"), ("d", "artist")])
        fetched = [p["album_id"] for e, p in calls if e == "album/get"]
        self.assertNotIn("b", fetched, "no performers are fetched for a lowered album")

    def test_lowering_is_kept_restored_and_cleared(self):
        self.store.add("label", "Decca Classics", "Decca Classics")
        self.store.add("album", "b", "Two")
        again = qs.LoweredList(self.store.path)
        self.assertEqual([(e["kind"], e["key"]) for e in again.entries()],
                         [("album", "b"), ("label", "decca classics")])
        self.assertTrue(again.remove("label", "DECCA classics"))
        self.assertEqual([e["key"] for e in again.entries()], ["b"])
        again.clear()
        self.assertEqual(qs.LoweredList(self.store.path).entries(), [])

    def test_adding_twice_keeps_one_newest_entry(self):
        self.store.add("album", "b", "Two")
        self.store.add("label", "BIS")
        self.store.add("album", "b", "Two")
        self.assertEqual([e["key"] for e in self.store.entries()], ["b", "bis"])

    def test_nonsense_is_refused(self):
        with self.assertRaises(qs.QobuzError):
            self.store.add("composer", "Berio")
        with self.assertRaises(qs.QobuzError):
            self.store.add("label", "  ")


class AlbumTest(unittest.TestCase):
    def test_album_details(self):
        raw = {**album("a1", "Symphony No. 7", "PENTATONE", "2024-11-15"),
               "description": "<p>A <b>great</b> record&amp;more</p>",
               "tracks": {"items": [
                   {"id": 11, "title": "I. Allegro moderato", "track_number": 1,
                    "media_number": 1, "duration": 1200, "work": "Symphony No. 7",
                    "composer": {"name": "Anton Bruckner"},
                    "performer": {"name": "Bamberger Symphoniker"},
                    "performers": "Jakub Hrusa, Conductor - X, Producer"}]}}
        cat, _ = catalog({}, {"a1": raw})
        out = cat.album("a1")
        self.assertEqual(out["description"], "A great record&more")
        self.assertEqual(out["performers"], [{"name": "Jakub Hrusa", "roles": ["Conductor"]}])
        self.assertEqual(out["track_list"][0]["id"], "11")
        self.assertEqual(out["track_list"][0]["work"], "Symphony No. 7")
        self.assertEqual(out["groups"], ["Pentatone"])

    def test_album_booklets_and_awards(self):
        raw = {**album("a1", "Symphony No. 2", "Halle", "2025-10-01"),
               "goodies": [{"name": "Livret numérique", "description": "Mahler 2",
                            "url": "https://static.qobuz.com/goodies/97/000215979.pdf"},
                           {"name": "not a link", "url": "javascript:alert(1)"}],
               "awards": [{"name": "Gramophone: Editor's Choice", "publication_name": "Gramophone",
                           "awarded_at": 1764543600}],
               "upc": "0123", "maximum_technical_specifications": "24 bits / 48.0 kHz - Stereo"}
        cat, _ = catalog({}, {"a1": raw})
        out = cat.album("a1")
        self.assertEqual(out["booklets"], [{"name": "Livret numérique", "description": "Mahler 2",
                                            "url": "https://static.qobuz.com/goodies/97/000215979.pdf"}])
        self.assertEqual(out["awards"][0]["publication"], "Gramophone")
        self.assertEqual(out["awards"][0]["date"], "2025-11-30")
        self.assertEqual(out["upc"], "0123")
        self.assertEqual(out["technical"], "24 bits / 48.0 kHz - Stereo")

    def test_awards_come_with_enrichment_and_on_request(self):
        prize = {"name": "Diapason d'or", "publication_name": "Diapason", "awarded_at": 1764543600}
        raw = {**album("a1", "Symphony No. 2", "Halle", "2025-10-01"), "awards": [prize],
               "tracks": {"items": []}}
        plain = {**album("a2", "Symphony No. 2", "Halle", "2025-10-01"), "tracks": {"items": []}}
        cat, _ = catalog({"mahler 2": [album("a1", "Symphony No. 2", "Halle", "2025-10-01"),
                                       album("a2", "Symphony No. 2", "Halle", "2025-10-01")]},
                         {"a1": raw, "a2": plain})
        answer = cat.search("mahler 2")
        by_id = {c["id"]: c for c in answer["results"]}
        self.assertEqual(by_id["a1"]["awards"][0]["name"], "Diapason d'or")
        self.assertEqual(by_id["a2"]["awards"], [])
        got = cat.awards(["a1", "a2", "../bad", "a1"])
        self.assertEqual(sorted(got), ["a1", "a2"])
        self.assertEqual(got["a1"][0]["publication"], "Diapason")

    def test_awarded_list_notes_qobuz_awards_and_keeps_the_users(self):
        prize = {"name": "Gramophone: Editor's Choice", "publication_name": "Gramophone",
                 "awarded_at": 1764543600}
        raw1 = {**album("a1", "Symphony No. 2", "Halle", "2025-10-01"), "awards": [prize],
                "tracks": {"items": []}}
        raw2 = {**album("a2", "Symphony No. 2", "Channel", "2006-01-01"), "tracks": {"items": []}}
        with tempfile.TemporaryDirectory() as tmp:
            store = qs.AwardedAlbums(tmp + "/aw.json")
            cat, _ = catalog({}, {"a1": raw1, "a2": raw2})
            cat.awarded = store
            cat.awards(["a1", "a2"])                       # met: a1 with an award, a2 without
            self.assertEqual([a["id"] for a in store.albums()], ["a1"])
            got = store.mark(qs.album_card(raw2), "Diapason d'Or", "Diapason")
            self.assertEqual(got, [{"name": "Diapason d'Or", "publication": "Diapason", "date": "", "mine": True}])
            again = qs.AwardedAlbums(tmp + "/aw.json")      # kept in the file
            cat.awarded = again
            self.assertEqual(cat.awards(["a2"])["a2"][0]["name"], "Diapason d'Or")
            self.assertEqual([a["id"] for a in again.albums()], ["a2", "a1"])
            self.assertEqual(again.unmark("a2", "diapason d'or"), [])
            self.assertEqual([a["id"] for a in again.albums()], ["a1"])
            with self.assertRaises(qs.QobuzError):
                again.mark(qs.album_card(raw2), "", "")

    def test_a_rating_alone_keeps_the_album_listed_and_rides_with_its_awards(self):
        raw = {**album("r1", "Symphony No. 3", "BIS", "2020-01-01"), "tracks": {"items": []}}
        with tempfile.TemporaryDirectory() as tmp:
            store = qs.AwardedAlbums(tmp + "/aw.json")
            cat, _ = catalog({"mahler 3": [album("r1", "Symphony No. 3", "BIS", "2020-01-01")]}, {"r1": raw})
            cat.awarded = store
            self.assertEqual(store.rate(qs.album_card(raw), 5), 3)            # clamped
            self.assertEqual([(a["id"], a["rating"], a["awards"]) for a in store.albums()], [("r1", 3, [])])
            card = cat.search("mahler 3")["results"][0]
            self.assertEqual(card["rating"], 3)
            self.assertEqual(cat.ratings(["r1", "x"]), {"r1": 3})
            self.assertEqual(qs.AwardedAlbums(tmp + "/aw.json").rating("r1"), 3)
            self.assertEqual(store.rate(qs.album_card(raw), 0), 0)
            self.assertEqual(store.albums(), [])

    def test_awarded_filter_combines_with_label_and_release_date(self):
        items = [album("prize", "Symphony", "Pentatone", "2025-04-01"),
                 album("rated", "Symphony", "Decca", "2025-05-01"),
                 album("old", "Symphony", "Pentatone", "2020-01-01"),
                 album("plain", "Symphony", "Pentatone", "2025-06-01")]
        with tempfile.TemporaryDirectory() as tmp:
            store = qs.AwardedAlbums(tmp + "/aw.json")
            store.seen(qs.album_card(items[0]), [{"name": "Gramophone", "publication": "Gramophone"}])
            store.rate(qs.album_card(items[1]), 2)
            store.mark(qs.album_card(items[2]), "Diapason", "Diapason")
            cat, _ = catalog({"symphony": items, "symphony Pentatone": items}, max_enrich=0)
            cat.awarded = store
            answer = cat.search("symphony", awarded_only=True, labels=["Pentatone"], from_year=2024)
            self.assertEqual([c["id"] for c in answer["results"]], ["prize"])
            self.assertTrue(answer["awarded_only"])
            self.assertEqual([c["id"] for c in cat.search("symphony", awarded_only=True)["results"]],
                             ["prize", "rated", "old"])
            self.assertEqual([c["id"] for c in cat.search("symphony")["results"]],
                             ["prize", "rated", "old", "plain"])

    def test_track_brings_its_album_card(self):
        calls = []

        def fetch(endpoint, params):
            calls.append((endpoint, params))
            return {"id": 5, "title": "II. Adagio", "duration": 1400,
                    "performer": {"name": "Some Orchestra"},
                    "album": album("a1", "Symphony No. 7", "PENTATONE", "2024-11-15")}
        cat = qs.QobuzCatalog(qs.Settings(), fetch=fetch)
        out = cat.track("5")
        self.assertEqual((out["title"], out["album"]["title"], out["album"]["image_large"]),
                         ("II. Adagio", "Symphony No. 7", "https://img/a1_600.jpg"))
        cat.track("5")
        self.assertEqual(calls, [("track/get", {"track_id": "5"})], "cached")
        with self.assertRaises(qs.QobuzError):
            cat.track("5/../../user")

    def test_bad_album_id_is_refused(self):
        cat, calls = catalog({})
        with self.assertRaises(qs.QobuzError):
            cat.album("../user/login")
        self.assertEqual(calls, [])


class PanelTest(unittest.TestCase):
    def setUp(self):
        self.client = APP.app.test_client()
        qobuz_web._renderer = (0.0, False)

    def running(self, value=True):
        return patch.object(qobuz_web, "_renderer_running", lambda: value)

    def test_search_route(self):
        cat, _ = catalog(SearchTest.SEARCHES, max_enrich=0)
        with patch.object(qobuz_web, "catalog", return_value=cat), self.running():
            data = self.client.get("/qobuz/search?q=bruckner+7&label=Pentatone,Decca"
                                   "&from=2021").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual([c["id"] for c in data["results"]], ["penta2", "decca", "penta1"])

    def test_search_route_passes_awarded_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat, _ = catalog(SearchTest.SEARCHES, max_enrich=0)
            cat.awarded = qs.AwardedAlbums(tmp + "/aw.json")
            cat.awarded.rate(qs.album_card(SearchTest.SEARCHES["bruckner 7"][2]), 1)
            with patch.object(qobuz_web, "catalog", return_value=cat), self.running():
                data = self.client.get("/qobuz/search?q=bruckner+7&awarded=1").get_json()
            self.assertEqual([c["id"] for c in data["results"]], ["penta1"])

    def test_search_stream_route(self):
        import json
        cat, _ = catalog(SearchTest.SEARCHES, max_enrich=0)
        with patch.object(qobuz_web, "catalog", return_value=cat), self.running(), \
                patch.object(qs, "PROGRESS_INTERVAL", 0):
            response = self.client.get("/qobuz/search/stream?q=bruckner+7&label=Pentatone,Decca"
                                       "&from=2021")
            body = response.get_data(as_text=True)
        self.assertEqual(response.mimetype, "text/event-stream")
        frames = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        self.assertGreater(len(frames), 1)
        self.assertTrue(all(f["ok"] and f.get("partial") for f in frames[:-1]))
        self.assertNotIn("partial", frames[-1])
        self.assertEqual([c["id"] for c in frames[-1]["results"]], ["penta2", "decca", "penta1"])

    def test_search_stream_says_why_it_cannot(self):
        cat, _ = catalog(SearchTest.SEARCHES)
        with patch.object(qobuz_web, "catalog", return_value=cat), self.running(False):
            response = self.client.get("/qobuz/search/stream?q=bruckner+7")
        self.assertEqual(response.status_code, 200)
        self.assertIn("upmpdcli", response.get_data(as_text=True))

    def test_no_search_without_upmpdcli(self):
        cat, calls = catalog(SearchTest.SEARCHES)
        with patch.object(qobuz_web, "catalog", return_value=cat), self.running(False):
            response = self.client.get("/qobuz/search?q=bruckner+7")
            status = self.client.get("/qobuz/status").get_json()
        self.assertEqual(response.status_code, 409)
        self.assertIn("upmpdcli", response.get_json()["error"])
        self.assertEqual(calls, [])
        self.assertFalse(status["renderer"])

    def test_a_bad_number_is_an_error_not_a_crash(self):
        cat, _ = catalog({})
        with patch.object(qobuz_web, "catalog", return_value=cat), self.running():
            response = self.client.get("/qobuz/search?q=x&last=two")
        self.assertEqual(response.status_code, 502)
        self.assertIn("last", response.get_json()["error"])

    def test_no_token_says_what_to_do(self):
        with tempfile.NamedTemporaryFile("w", suffix=".config") as token_file:
            token_file.write("user_id = 42\n")
            token_file.flush()
            with patch.object(APP, "QOBUZ_CACHE_CONFIG", token_file.name), self.running():
                data = self.client.get("/qobuz/search?q=x").get_json()
                status = self.client.get("/qobuz/status").get_json()
        self.assertFalse(data["ok"])
        self.assertIn("sign-in", data["error"])
        self.assertFalse(status["token"])

    def test_lowered_route_adds_restores_and_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = qs.LoweredList(tmp + "/l.json")
            with patch.object(qobuz_web, "lowered", return_value=store):
                post = lambda **b: self.client.post("/qobuz/lowered", json=b)
                self.assertEqual(post(action="add", kind="label", key="Decca", name="Decca")
                                 .get_json()["entries"][0]["key"], "decca")
                post(action="add", kind="album", key="a1", name="Some album")
                self.assertEqual(len(self.client.get("/qobuz/lowered").get_json()["entries"]), 2)
                self.assertEqual([e["key"] for e in post(action="remove", kind="album", key="a1")
                                  .get_json()["entries"]], ["decca"])
                self.assertEqual(post(action="clear").get_json()["entries"], [])
                self.assertEqual(post(action="add", kind="composer", key="x").status_code, 400)
                self.assertEqual(post(action="explode").status_code, 400)

    def test_labels_come_from_the_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf") as conf:
            conf.write("[qobuz_search]\nlabels =\n    Pentatone\n"
                       "    Warner Classics: warner classics, erato\n")
            conf.flush()
            saved = APP.QOBUZ_SEARCH
            # Another test module may have loaded app.py again, pointing the
            # shared qobuz_web at its own copy: read this one's settings.
            try:
                APP.load_config(conf.name)
                with patch.object(qobuz_web, "_settings", lambda: APP.QOBUZ_SEARCH):
                    data = self.client.get("/qobuz/labels").get_json()
            finally:
                APP.QOBUZ_SEARCH = saved
        self.assertEqual(data["labels"], [
            {"name": "Pentatone", "patterns": ["pentatone"]},
            {"name": "Warner Classics", "patterns": ["warner classics", "erato"]}])

    def test_the_shipped_config_parses(self):
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(SRC / "commands.conf.in")
        groups = qs.parse_labels(cfg["qobuz_search"]["labels"])
        self.assertIn("Pentatone", [g.name for g in groups])
        self.assertEqual(len(groups), len(qs.parse_labels(qs.DEFAULT_LABELS)))


class DiscoverTests(unittest.TestCase):
    def test_featured_genre_paging_and_cache(self):
        calls = []
        def fetch(endpoint, params):
            calls.append((endpoint, params))
            return {"albums": {"items": [album("new", "New", "Label", "2026-09-30"),
                                        album("blocked", "Blocked", "Label", streamable=False)],
                               "total": 55}}
        catalog = qs.QobuzCatalog(fetch=fetch)
        result = catalog.discover("80", 50)
        self.assertEqual([a["id"] for a in result["albums"]], ["new"])
        self.assertEqual(result["next_offset"], 52)
        self.assertTrue(result["more"])
        self.assertEqual(calls[0], ("album/getFeatured", {
            "type": "new-releases", "genre_ids": "80:", "offset": 50, "limit": qs.PAGE_SIZE}))
        self.assertEqual(catalog.discover("80", 50), result)
        self.assertEqual(len(calls), 1)
        catalog.discover()
        self.assertNotIn("genre_ids", calls[-1][1])

    def test_genres_and_invalid_arguments(self):
        catalog = qs.QobuzCatalog(fetch=lambda endpoint, params: {
            "genres": {"items": [{"id": 80, "name": "Jazz"}, {"id": 10, "name": "Classical"}]}})
        self.assertEqual(catalog.genres(), [{"id": "80", "name": "Jazz"},
                                           {"id": "10", "name": "Classical"}])
        with self.assertRaises(qs.QobuzError):
            catalog.discover("invalid")
        with self.assertRaises(qs.QobuzError):
            catalog.discover(offset=-1)


if __name__ == "__main__":
    unittest.main()
