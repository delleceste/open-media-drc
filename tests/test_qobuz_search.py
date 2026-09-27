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
            album("gone", "Symphony No. 7", "PENTATONE", "2026-01-01", streamable=False),
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

    def test_several_labels(self):
        answer, _ = self.search(labels=["Pentatone", "Decca"], from_year=2021)
        self.assertEqual(self.ids(answer), ["penta2", "decca", "penta1"])

    def test_no_label_keeps_every_label(self):
        answer, _ = self.search(from_year=2021)
        self.assertEqual(self.ids(answer), ["dg", "penta1"])

    def test_undated_albums_drop_out_only_under_a_date_filter(self):
        self.assertIn("nodate", self.ids(self.search()[0]))
        self.assertNotIn("nodate", self.ids(self.search(from_year=1990)[0]))

    def test_unstreamable_albums_are_dropped_and_counted(self):
        answer, _ = self.search(labels=["Pentatone"])
        self.assertNotIn("gone", self.ids(answer))
        self.assertEqual(answer["unstreamable"], 1)

    def test_relevance_keeps_each_albums_best_position(self):
        answer, _ = self.search(labels=["Pentatone"], sort="relevance")
        # penta2 is first of its query; penta1 is 2nd of the label query,
        # better than 3rd of the plain one.
        self.assertEqual([(c["id"], c["rank"]) for c in answer["results"]],
                         [("penta2", 0), ("penta1", 1)])

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
        answer = cat.search("x")
        self.assertEqual(answer["considered"], 120)
        self.assertEqual(sorted(p["offset"] for _, p in calls), [0, 50, 100])
        self.assertFalse(answer["more"])

    def test_enough_matches_stop_the_reading(self):
        many = [album(f"a{i}", "T", "L", "2020-01-01") for i in range(3000)]
        cat, calls = catalog({"x": many}, max_enrich=0, scan=100, want=20)
        answer = cat.search("x")
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
        cat.search("x")
        calls.clear()
        answer = cat.search("x", scan=300)
        self.assertEqual(sorted(p["offset"] for _, p in calls), [100, 150, 200, 250])
        self.assertEqual(answer["considered"], 300)

    def test_a_failing_later_page_ends_only_its_query(self):
        many = [album(f"a{i}", "T", "L", "2020-01-01") for i in range(200)]
        fetch, _ = fake_qobuz({"x": many})

        def flaky(endpoint, params):
            if params.get("offset") == 100:
                raise qs.QobuzError("HTTP 500")
            return fetch(endpoint, params)

        cat = qs.QobuzCatalog(qs.Settings(max_enrich=0), fetch=flaky, today=lambda: TODAY)
        answer = cat.search("x")
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


if __name__ == "__main__":
    unittest.main()
