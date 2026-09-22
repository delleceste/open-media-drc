#!/usr/bin/env python3
"""The Dynamic Range database lookups behind the renderer card's "DR ↗" button.

dr.loudness-war.info has no API, so the panel parses two of its pages.  These
tests pin the parsers to the markup the site actually serves (trimmed here to
the structure that matters), and pin the two things the parsing must survive:
a column order read from the table header rather than assumed, and a DR badge
whose CSS class is only a colour bucket -- every value at or below 7 wears
`badge-dr-07`, so the class can never be the number.
"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

import drdb  # noqa: E402

SPEC = importlib.util.spec_from_file_location("omdrc_drdb_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


def badge(value):
    # The site pads to two digits and buckets the colour at 7 and 14.
    bucket = min(14, max(7, value))
    return f'<span class="badge-dr badge-dr-{bucket:02d}">{value:02d}</span>'


def search_page(rows, headers=("Artist", "Album", "Year", "DR",
                               "min DR", "max DR", "Codec", "Source")):
    head = "".join(f'<th scope="col">{h}</th>' for h in headers)
    body = ""
    for row in rows:
        cells = {
            "Artist": f'<td><a href="/?artist=X">{row["artist"]}</a></td>',
            "Album": f'<td><a href="/album/view/{row["id"]}">{row["album"]}</a></td>',
            "Year": f'<td>{row["year"]}</td>',
            "DR": f'<td>{badge(row["dr"])}</td>',
            "min DR": f'<td>{badge(row["dr_min"])}</td>',
            "max DR": f'<td>{badge(row["dr_max"])}</td>',
            "Codec": f'<td>{row["codec"]}</td>',
            "Source": f'<td>{row["source"]}</td>',
        }
        body += "<tr>" + "".join(cells[h] for h in headers) + "</tr>"
    # The legend above the table is badges outside any table: it must not be
    # mistaken for a result.
    return ('<div class="badge-wrapper">' + badge(7) + badge(14) + "</div>"
            '<table class="table"><thead><tr>' + head + "</tr></thead>"
            "<tbody>" + body + "</tbody></table>")


ALBUM_PAGE = """
<h1>Album details</h1>
<table class="table">
<tr><th scope="row">Artist</th><td>Dinosaur Jr.</td></tr>
<tr><th scope="row">Album</th><td>Sweep It Into Space</td></tr>
<tr><th scope="row">Year</th><td>2021</td></tr>
<tr><th scope="row">Album DR</th><td>%s</td></tr>
<tr><th scope="row">Min. track DR</th><td>%s</td></tr>
<tr><th scope="row">Max. track DR</th><td>%s</td></tr>
<tr><th scope="row">Track DR</th><td><div class="badge-wrapper">%s%s%s</div></td></tr>
<tr><th scope="row">Codec</th><td>Lossless</td></tr>
<tr><th scope="row">Source</th><td>Download</td></tr>
<tr><th scope="row">Label</th><td>Jagjaguwar</td></tr>
<tr><th scope="row">Catalog number</th><td></td></tr>
<tr><th scope="row">Bar code</th><td></td></tr>
<tr><th scope="row">Country</th><td></td></tr>
<tr><th scope="row">Link</th><td><a href="https://example.invalid/x">discogs</a></td></tr>
<tr><th scope="row">Comment</th><td>The production isn&#039;t the greatest.</td></tr>
<tr><th scope="row">Log file</th><td><pre class="log">foobar2000 1.5.5 / Dynamic Range Meter 1.1.1

DR         Peak         RMS     Duration Track
--------------------------------------------------------------------------------
DR7        0.00 dB    -8.34 dB      4:12 01-I Ain&#039;t
DR8       -0.08 dB    -8.80 dB      3:45 02-I Met The Stones
DR9       -0.09 dB    -9.95 dB      4:12 03-To Be Waiting
--------------------------------------------------------------------------------

Number of tracks:  3
Official DR value: DR8</pre></td></tr>
</table>
""" % (badge(8), badge(7), badge(9), badge(7), badge(8), badge(9))


class SearchParseTest(unittest.TestCase):
    def rows(self, **kwargs):
        base = {"id": 186383, "artist": "Dinosaur Jr.", "album": "Sweep It Into Space",
                "year": "2021", "dr": 8, "dr_min": 7, "dr_max": 9,
                "codec": "Lossless", "source": "Download"}
        base.update(kwargs)
        return base

    def test_one_result_row(self):
        got = drdb.parse_search(search_page([self.rows()]))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0], {
            "id": 186383, "artist": "Dinosaur Jr.", "album": "Sweep It Into Space",
            "year": "2021", "dr": 8, "dr_min": 7, "dr_max": 9,
            "codec": "Lossless", "source": "Download",
            "url": "https://dr.loudness-war.info/album/view/186383",
        })

    def test_columns_are_read_from_the_header_not_counted(self):
        # A column inserted or moved upstream must not silently turn the
        # minimum DR into the album DR.
        html = search_page([self.rows()],
                           headers=("Album", "Artist", "max DR", "min DR",
                                    "DR", "Year", "Source", "Codec"))
        got = drdb.parse_search(html)[0]
        self.assertEqual((got["dr"], got["dr_min"], got["dr_max"]), (8, 7, 9))
        self.assertEqual(got["album"], "Sweep It Into Space")
        self.assertEqual(got["source"], "Download")

    def test_low_dr_reads_its_number_not_its_colour_class(self):
        # DR3 is served as badge-dr-07 -- the class is the colour, flat red
        # for everything at or below 7.
        got = drdb.parse_search(search_page([self.rows(dr=3, dr_min=2, dr_max=5)]))[0]
        self.assertEqual((got["dr"], got["dr_min"], got["dr_max"]), (3, 2, 5))

    def test_legend_badges_are_not_results(self):
        self.assertEqual(drdb.parse_search(search_page([])), [])


class AlbumParseTest(unittest.TestCase):
    def setUp(self):
        self.album = drdb.parse_album(ALBUM_PAGE, 186383)

    def test_fields(self):
        self.assertEqual(self.album["artist"], "Dinosaur Jr.")
        self.assertEqual(self.album["album"], "Sweep It Into Space")
        self.assertEqual(self.album["year"], "2021")
        self.assertEqual((self.album["dr"], self.album["dr_min"], self.album["dr_max"]),
                         (8, 7, 9))
        self.assertEqual(self.album["label"], "Jagjaguwar")
        self.assertEqual(self.album["catalog_number"], "")
        self.assertEqual(self.album["comment"], "The production isn't the greatest.")
        self.assertEqual(self.album["link_url"], "https://example.invalid/x")
        self.assertEqual(self.album["url"],
                         "https://dr.loudness-war.info/album/view/186383")

    def test_per_track_badges(self):
        self.assertEqual(self.album["track_dr"], [7, 8, 9])

    def test_track_list_comes_out_of_the_uploaded_log(self):
        # The page has no structured track list; the log is the only place the
        # track names appear, and they are what places a playing track in a
        # version.
        titles = [track["title"] for track in self.album["tracks"]]
        self.assertEqual(titles, ["01-I Ain't", "02-I Met The Stones",
                                  "03-To Be Waiting"])
        self.assertEqual(self.album["tracks"][2],
                         {"dr": 9, "peak": "-0.09 dB", "rms": "-9.95 dB",
                          "duration": "4:12", "title": "03-To Be Waiting"})

    def test_log_prose_is_not_a_track(self):
        self.assertEqual(len(self.album["tracks"]), 3)

    def test_album_with_no_log_still_parses(self):
        album = drdb.parse_album(
            '<table><tr><th scope="row">Album</th><td>Bare</td></tr></table>')
        self.assertEqual(album["album"], "Bare")
        self.assertEqual(album["tracks"], [])
        self.assertEqual(album["log"], "")


class ClientTest(unittest.TestCase):
    def client(self, pages, **settings):
        self.asked = []

        def fetch(path):
            self.asked.append(path)
            if path not in pages:
                raise AssertionError(f"unexpected page {path}")
            return pages[path]

        return drdb.DrDb(drdb.Settings(**settings), fetch=fetch)

    def row(self, album_id, dr):
        return {"id": album_id, "artist": "A", "album": f"Album {album_id}",
                "year": "1991", "dr": dr, "dr_min": dr, "dr_max": dr,
                "codec": "Lossless", "source": "CD"}

    def test_search_sorts_by_album_dr_descending(self):
        page = search_page([self.row(1, 8), self.row(2, 13), self.row(3, 11)])
        db = self.client({"/album/list/1/dr/desc?artist=A": page})
        got = db.search("A")
        self.assertEqual([row["dr"] for row in got["rows"]], [13, 11, 8])
        self.assertFalse(got["truncated"])

    def test_unrated_versions_sort_last(self):
        html = search_page([self.row(1, 8)]).replace(
            '<td><span class="badge-dr badge-dr-08">08</span></td>', "<td></td>", 1)
        db = self.client({"/album/list/1/dr/desc?artist=A": html})
        rows = db.search("A")["rows"]
        self.assertIsNone(rows[0]["dr"])

    def test_paging_stops_on_a_short_page(self):
        full = search_page([self.row(i, 10) for i in range(drdb.PAGE_SIZE)])
        short = search_page([self.row(100, 12)])
        db = self.client({"/album/list/1/dr/desc?artist=A": full,
                          "/album/list/2/dr/desc?artist=A": short})
        got = db.search("A")
        self.assertEqual(len(got["rows"]), drdb.PAGE_SIZE + 1)
        self.assertEqual(self.asked, ["/album/list/1/dr/desc?artist=A",
                                      "/album/list/2/dr/desc?artist=A"])
        self.assertFalse(got["truncated"])

    def test_max_pages_reports_truncation(self):
        full = search_page([self.row(i, 10) for i in range(drdb.PAGE_SIZE)])
        db = self.client({"/album/list/1/dr/desc?artist=A": full}, max_pages=1)
        self.assertTrue(db.search("A")["truncated"])

    def test_album_and_artist_are_both_sent(self):
        page = search_page([self.row(1, 9)])
        db = self.client({"/album/list/1/dr/desc?artist=A&album=B": page})
        db.search("A", "B")
        self.assertEqual(self.asked, ["/album/list/1/dr/desc?artist=A&album=B"])

    def test_an_empty_query_is_refused_before_any_request(self):
        db = self.client({})
        with self.assertRaises(drdb.DrDbError):
            db.search("  ", "")
        self.assertEqual(self.asked, [])

    def test_pages_are_cached(self):
        page = search_page([self.row(1, 9)])
        db = self.client({"/album/list/1/dr/desc?artist=A": page})
        db.search("A")
        db.search("A")
        self.assertEqual(len(self.asked), 1)

    def test_a_stale_cache_entry_is_refetched(self):
        page = search_page([self.row(1, 9)])
        db = self.client({"/album/list/1/dr/desc?artist=A": page}, cache_ttl=0)
        db.search("A")
        db.search("A")
        self.assertEqual(len(self.asked), 2)


class NowPlayingTest(unittest.TestCase):
    """What the lookup is seeded with.  upmpdcli tags MusicPD's queue;
    qobuzconnect2mpd in direct mode queues a bare redirect token, and its
    status file's "Artist - Title" line is then the only metadata there is."""

    def tags(self, mpd, status_line=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.txt"
            if status_line is not None:
                path.write_text(status_line, encoding="utf-8")
            with patch.object(APP, "_mpd_now_playing_via_protocol", return_value=mpd), \
                 patch.object(APP, "_resolve_mpd_port", return_value="6600"), \
                 patch.object(APP, "QCONNECT_STATUS_FILE", str(path)):
                return APP._drdb_now_playing()

    def mpd(self, **kwargs):
        base = {"title": "", "album": "", "artist": "", "state": "",
                "elapsed": None, "duration": None, "audio": ""}
        base.update(kwargs)
        return base

    def test_mpd_tags_win_when_the_renderer_publishes_them(self):
        got = self.tags(self.mpd(artist="Low", album="Things We Lost In The Fire",
                                 title="Sunflower", state="play"))
        self.assertEqual((got["artist"], got["album"], got["title"]),
                         ("Low", "Things We Lost In The Fire", "Sunflower"))

    def test_untagged_queue_falls_back_to_the_status_line(self):
        got = self.tags(self.mpd(state="play"),
                        "[playing] Dinosaur Jr. - No Friends  [2:15 / 3:48]\n"
                        "24 bit / 96 kHz / stereo\nstate=PLAYING\n")
        self.assertEqual(got["artist"], "Dinosaur Jr.")
        self.assertEqual(got["title"], "No Friends")
        # No album tag exists anywhere in that path: the lookup has to cope.
        self.assertEqual(got["album"], "")

    def test_a_title_holding_a_dash_keeps_its_tail(self):
        got = self.tags(self.mpd(), "[playing] Neu! - Hallogallo - remaster\n")
        self.assertEqual(got["artist"], "Neu!")
        self.assertEqual(got["title"], "Hallogallo - remaster")

    def test_nothing_playing(self):
        got = self.tags(self.mpd(state="stop"), "[stopped]\n")
        self.assertEqual((got["artist"], got["album"], got["title"]), ("", "", ""))

    def test_missing_status_file_is_not_an_error(self):
        got = self.tags(self.mpd())
        self.assertEqual(got["title"], "")


class EndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = APP.app.test_client()

    def test_disabled_hides_page_and_api(self):
        with patch.object(APP, "DRDB", drdb.Settings(enabled=False)):
            self.assertEqual(self.client.get("/dr-alternatives").status_code, 404)
            self.assertEqual(self.client.get("/drdb/search?artist=A").status_code, 404)
            self.assertEqual(self.client.get("/drdb/now").status_code, 404)

    def test_a_database_that_cannot_be_reached_is_reported_not_raised(self):
        class Broken:
            def search(self, *a, **k):
                raise drdb.DrDbError("cannot reach the database: timed out")

        with patch.object(APP, "_drdb", return_value=Broken()):
            response = self.client.get("/drdb/search?artist=A")
        self.assertEqual(response.status_code, 502)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("timed out", payload["error"])

    def test_search_falls_back_to_what_is_playing(self):
        seen = {}

        class Recorder:
            def search(self, artist, album):
                seen.update(artist=artist, album=album)
                return {"artist": artist, "album": album, "rows": [],
                        "truncated": False, "search_url": ""}

        with patch.object(APP, "_drdb", return_value=Recorder()), \
             patch.object(APP, "_drdb_now_playing",
                          return_value={"artist": "Low", "album": "Hey What",
                                        "title": "White Horses", "state": "play"}):
            response = self.client.get("/drdb/search")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen, {"artist": "Low", "album": "Hey What"})


if __name__ == "__main__":
    unittest.main()
