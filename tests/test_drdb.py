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

    def test_the_album_appended_to_the_status_line_is_read_back(self):
        got = self.tags(self.mpd(state="play"),
                        "[playing] Pink Floyd - Learning to Fly \u00b7 "
                        "A Momentary Lapse of Reason  [5:16 / 5:27]\n"
                        "16 bit / 44.1 kHz / stereo\nstate=PLAYING\n")
        self.assertEqual(got["artist"], "Pink Floyd")
        self.assertEqual(got["title"], "Learning to Fly")
        self.assertEqual(got["album"], "A Momentary Lapse of Reason")

    def test_a_title_holding_a_middle_dot_keeps_it(self):
        # The album is what the daemon appended last, so the split takes the
        # final separator, not the first.
        got = self.tags(self.mpd(),
                        "[playing] Squarepusher - Iambic \u00b7 9 Poetry \u00b7 "
                        "Ultravisitor\n")
        self.assertEqual(got["title"], "Iambic \u00b7 9 Poetry")
        self.assertEqual(got["album"], "Ultravisitor")

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


class MetadataCleanupTest(unittest.TestCase):
    """What a renderer puts in a tag is not always what the database is
    indexed by.  These are the two shapes that cost a lookup its answer."""

    # Verbatim from MusicPD while upmpdcli played an Alice In Chains track:
    # 29 credits, each with its role, in the Artist tag.
    CREDITS = (
        "Alice In Chains, M. Inez (Composer), Alex Coletti, Producer  - "
        "Alice In Chains, Performer  - Alice In Chains, Producer  - "
        "Brian Kingman, 2nd Engineer  - Don C. Tyler, Edited By  - "
        "J. Cantrell, Composer  - Jerry Cantrell, Guitar  - "
        "Layne Staley, Vocal  - Toby Wright, Recording Engineer  (Performer)")

    def test_the_performer_is_picked_out_of_a_credits_dump(self):
        self.assertEqual(APP._drdb_main_artist(self.CREDITS), "Alice In Chains")

    def test_credits_without_an_explicit_performer_fall_back_to_the_first(self):
        self.assertEqual(
            APP._drdb_main_artist(
                "Miles Davis, Trumpet  - Bill Evans, Piano  - "
                "Paul Chambers, Bass"),
            "Miles Davis")

    def test_an_ordinary_artist_tag_is_left_alone(self):
        for artist in ("Pink Floyd", "Crosby, Stills, Nash & Young",
                       "Dinosaur Jr.", "Simon & Garfunkel", ""):
            self.assertEqual(APP._drdb_main_artist(artist), artist)

    def test_edition_annotations_come_off_a_title(self):
        self.assertEqual(
            APP._drdb_plain("Nutshell (Live at the Majestic Theatre, "
                            "Brooklyn, NY - April 1996) (Album Version)"),
            "Nutshell")
        self.assertEqual(APP._drdb_plain("The Dark Side of the Moon "
                                         "[Remastered 2011]"),
                         "The Dark Side of the Moon")

    def test_a_name_that_only_looks_like_an_annotation_survives(self):
        # Leading, so not an annotation -- and stripping one that is the
        # whole name would leave nothing to search for.
        self.assertEqual(APP._drdb_plain("(Don't Fear) The Reaper"),
                         "(Don't Fear) The Reaper")
        self.assertEqual(APP._drdb_plain("(Untitled)"), "(Untitled)")

    def test_now_playing_is_cleaned_before_it_reaches_the_lookup(self):
        mpd = {"title": "Nutshell (Live at the Majestic Theatre, Brooklyn, "
                        "NY - April 1996) (Album Version)",
               "album": "Unplugged", "artist": self.CREDITS, "state": "play",
               "elapsed": None, "duration": None, "audio": ""}
        with patch.object(APP, "_mpd_now_playing_via_protocol", return_value=mpd), \
             patch.object(APP, "_resolve_mpd_port", return_value="6600"):
            got = APP._drdb_now_playing()
        # The fingerprint fields travel alongside; these four are the lookup.
        self.assertEqual(
            {key: got[key] for key in ("artist", "album", "title", "state")},
            {"artist": "Alice In Chains", "album": "Unplugged",
             "title": "Nutshell", "state": "play"})


class IdentifyTest(unittest.TestCase):
    """Which pressing is on the wire.

    Only the track playing (or paused, or last played) is evidence: the rest
    of the queue is a listener's doing. One track is enough, because that is
    where two masters differ -- modelled on the real case, Alice In Chains
    Unplugged, whose "Down In A Hole" runs 5:46 on the CD and 6:06 on the DVD
    rip, which keeps the dialogue between songs.
    """

    TRACKS = ["Nutshell", "Brother", "No Excuses", "Sludge Factory",
              "Down In A Hole", "Angry Chair", "Rooster"]

    def playing(self, **kwargs):
        base = {"artist": "Alice In Chains", "album": "Unplugged",
                "title": "Down In A Hole", "track_no": 5, "disc": None,
                "duration": 346.0, "rate": 44100, "bits": 16, "channels": 2,
                "year": "", "label": ""}
        base.update(kwargs)
        return base

    def version(self, name, source, durations, log=True, **extra):
        titles = [f"{i:02d}-{t}" for i, t in enumerate(self.TRACKS, 1)]
        tracks = [{"dr": 8, "peak": "0.00 dB", "rms": "-9.00 dB",
                   "duration": durations.get(i, "3:30"), "title": title}
                  for i, title in enumerate(titles, 1)]
        version = {"album": name, "source": source, "codec": "Lossless",
                   "track_dr": [8] * len(titles),
                   "tracks": tracks if log else []}
        version.update(extra)
        return version

    def test_the_right_length_at_the_right_position_calls_it(self):
        match = drdb.identify(self.playing(),
                              self.version("MTV Unplugged", "CD", {5: "5:46"}))
        self.assertEqual(match["verdict"], "likely")
        self.assertEqual(match["matched_track"], 4)
        self.assertTrue(any("5:46" in reason for reason in match["for"]))

    def test_a_longer_cut_of_the_same_track_is_ruled_out(self):
        dvd = drdb.identify(
            self.playing(),
            self.version("MTV Unplugged [5.1 Dolby Digital DVD]", "Unknown",
                         {5: "6:26"}))
        self.assertEqual(dvd["verdict"], "unlikely")
        self.assertTrue(any("6:26" in reason for reason in dvd["against"]))
        # ...and a surround transfer cannot be a 44.1 kHz stereo stream.
        self.assertTrue(any("surround" in reason for reason in dvd["against"]))

    def test_a_version_that_does_not_list_the_track_is_rejected(self):
        match = drdb.identify(
            self.playing(),
            {"album": "Dirt", "source": "CD", "track_dr": [6] * 7,
             "tracks": [{"dr": 6, "duration": "5:00", "peak": "", "rms": "",
                         "title": f"{i:02d}-Something Else {i}"}
                        for i in range(1, 8)]})
        self.assertEqual(match["verdict"], "unlikely")
        self.assertTrue(any("does not name" in r for r in match["against"]))

    def test_label_and_year_can_name_an_edition_on_their_own(self):
        # An entry uploaded without a DR-meter log has no track list and no
        # track times; the tags that name the edition are all there is.
        match = drdb.identify(
            self.playing(label="Columbia/Legacy", year="1996"),
            self.version("MTV Unplugged", "CD", {}, log=False,
                         label="Columbia", year="1996"))
        self.assertTrue(any("same label (Columbia)" in r for r in match["for"]))
        self.assertTrue(any("same year (1996)" in r for r in match["for"]))
        self.assertIn(match["verdict"], ("possible", "likely"))

    def test_a_different_year_only_counts_against_a_little(self):
        # A streaming service dates a record by the issue it licensed, so a
        # mismatch here is weak evidence, not a refutation.
        match = drdb.identify(
            self.playing(year="2006"),
            self.version("MTV Unplugged", "CD", {5: "5:46"}, year="1996"))
        self.assertEqual(match["verdict"], "likely")
        self.assertTrue(any("1996" in r for r in match["against"]))

    def test_a_few_seconds_out_does_not_outweigh_a_matching_year(self):
        """The live case that exposed this: Delicate Sound of Thunder, where
        the 1995 reissue lists the playing track at 6:21 (the stream's length
        to the second) and the 1988 original at 6:17, while the stream's Date
        tag says 1988. Both fit; neither should be able to bury the other."""
        stream = self.playing(title="Yet Another Movie", track_no=3,
                              duration=381.2, year="1988-11-22")
        reissue = self.version("Delicate Sound of Thunder [Disc 1]", "CD",
                               {3: "6:21"}, year="1995")
        original = self.version("Delicate Sound Of Thunder (Disc 1)", "CD",
                                {3: "6:17"}, year="1988")
        # Titles differ from the default fixture; give both the same list.
        for version in (reissue, original):
            version["tracks"][2]["title"] = "03-Yet Another Movie"
        near = drdb.identify(stream, reissue)
        far = drdb.identify(stream, original)
        self.assertGreater(near["score"], far["score"])   # the exact time wins
        # ...but by less than the margin the page calls a tie, so both are
        # named instead of one being buried.
        self.assertLess(near["score"] - far["score"], 12)
        self.assertIn(far["verdict"], ("likely", "possible"))
        self.assertTrue(any("same year (1988)" in r for r in far["for"]))
        self.assertTrue(any("6:21" in r for r in near["for"]))

    def test_a_cd_cannot_be_carrying_a_hi_res_stream(self):
        # A CD is 44.1 kHz/16 bit by definition, so a stream above that is not
        # a rip of one. The exclusion holds even when the track times agree to
        # the second, which they will when the hi-res issue shares the CD's
        # master -- and the reason says exactly that.
        match = drdb.identify(
            self.playing(rate=96000, bits=24),
            self.version("MTV Unplugged", "CD", {5: "5:46"}))
        self.assertNotEqual(match["verdict"], "likely")
        self.assertTrue(any("holds 44.1 kHz/16 bit" in r for r in match["against"]))
        self.assertTrue(any("would measure much the same" in r
                            for r in match["against"]))

    def test_a_cd_rate_stream_still_suits_a_cd_entry(self):
        match = drdb.identify(self.playing(),
                              self.version("MTV Unplugged", "CD", {5: "5:46"}))
        self.assertEqual(match["verdict"], "likely")
        self.assertTrue(any("44.1 kHz/16 bit, as the stream is" in r
                            for r in match["for"]))

    def test_a_hi_res_stream_suits_a_download_entry(self):
        match = drdb.identify(
            self.playing(rate=96000, bits=24),
            self.version("MTV Unplugged", "Download", {5: "5:46"}))
        self.assertTrue(any("as a 96 kHz/24 bit stream would be" in r
                            for r in match["for"]))

    def test_a_transfer_that_names_its_own_format_is_compared_to_the_stream(self):
        off = drdb.identify(
            self.playing(),
            self.version("MTV Unplugged [2.0 LPCM 16/48 DVD]", "Unknown",
                         {5: "5:46"}))
        self.assertTrue(any("transferred at 48 kHz/16 bit" in r
                            for r in off["against"]))

    def test_a_bitrate_in_a_title_is_not_a_sample_rate(self):
        # "448 Kbps" is Dolby Digital's bitrate; reading it as 448 kHz would
        # rule the entry out for the wrong reason.
        self.assertEqual(
            drdb.stated_format("MTV Unplugged [5.1 Dolby Digital 448 Kbps DVD]"),
            (None, None))
        self.assertEqual(drdb.stated_format("MTV Unplugged (DVD 5.1 Mix) 48kHz-16bit"),
                         (48000, 16))
        self.assertEqual(drdb.stated_format("Aja (24/96 HDtracks)"), (96000, 24))
        self.assertEqual(drdb.stated_format("Kind of Blue 192kHz"), (192000, None))
        self.assertEqual(drdb.stated_format("MTV Unplugged"), (None, None))
        self.assertEqual(drdb.format_text(96000, 24), "96 kHz/24 bit")
        self.assertEqual(drdb.format_text(44100, None), "44.1 kHz")

    def test_the_wrong_disc_of_a_set_is_ruled_out(self):
        # A double album is filed one disc per entry and they do not measure
        # alike: Delicate Sound of Thunder is DR13 on disc 1, DR14 on disc 2.
        wrong = drdb.identify(
            self.playing(disc=1),
            self.version("Delicate Sound of Thunder [CD 2]", "CD", {5: "5:46"}))
        right = drdb.identify(
            self.playing(disc=1),
            self.version("Delicate Sound Of Thunder (Disc 1)", "CD", {5: "5:46"}))
        self.assertTrue(any("disc 2 of the set" in r for r in wrong["against"]))
        self.assertTrue(any("disc 1 of the set" in r for r in right["for"]))
        self.assertGreater(right["score"], wrong["score"])

    def test_an_entry_naming_no_disc_is_not_held_against(self):
        match = drdb.identify(self.playing(disc=1),
                              self.version("MTV Unplugged", "CD", {5: "5:46"}))
        self.assertFalse(any("disc" in r for r in match["against"]))
        self.assertEqual(match["verdict"], "likely")

    def test_a_comparison_nobody_could_make_is_said_out_loud(self):
        """A signal absent from the list reads as a signal that agreed. When
        one side has a label or a year and the other has none, the entry says
        so instead of leaving a silence to interpret."""
        # The renderer publishes no label (an unpatched upmpdcli, or a queue
        # loaded before it was patched), but the entry names one.
        no_wire_label = drdb.identify(
            self.playing(),
            self.version("MTV Unplugged", "CD", {5: "5:46"}, label="Columbia"))
        self.assertTrue(any("released by Columbia" in r and "no label" in r
                            for r in no_wire_label["unchecked"]))
        self.assertFalse(any("Columbia" in r for r in no_wire_label["against"]))

        # ...and the other way round.
        no_entry_label = drdb.identify(
            self.playing(label="Columbia/Legacy"),
            self.version("MTV Unplugged", "CD", {5: "5:46"}))
        self.assertTrue(any("names no label" in r
                            for r in no_entry_label["unchecked"]))

    def test_an_entry_with_no_track_list_says_so(self):
        match = drdb.identify(
            self.playing(),
            self.version("MTV Unplugged", "Vinyl", {}, log=False))
        self.assertTrue(any("without a track list" in r
                            for r in match["unchecked"]))

    def test_a_year_that_could_not_be_compared_is_flagged_too(self):
        match = drdb.identify(self.playing(year="1996"),
                              self.version("MTV Unplugged", "CD", {5: "5:46"}))
        self.assertTrue(any("names no year" in r for r in match["unchecked"]))

    def test_everything_comparable_leaves_nothing_unchecked(self):
        match = drdb.identify(
            self.playing(year="1996", label="Columbia"),
            self.version("MTV Unplugged", "CD", {5: "5:46"},
                         year="1996", label="Columbia"))
        self.assertEqual(match["unchecked"], [])

    def test_nothing_to_compare_is_reported_as_unknown(self):
        match = drdb.identify(
            {"title": "", "album": "", "duration": None, "rate": None,
             "bits": None, "channels": None, "track_no": None,
             "year": "", "label": ""},
            {"album": "MTV Unplugged", "source": "CD", "track_dr": [],
             "tracks": []})
        self.assertEqual(match["verdict"], "unknown")
        self.assertEqual(match["score"], 0)

    def test_title_duration_label_and_year_helpers(self):
        self.assertEqual(drdb.title_key("05-Down In A Hole (MTV Unplugged)"),
                         "down in a hole")
        self.assertEqual(drdb.title_key("The Killer Is Me"), "killer is me")
        self.assertEqual(drdb.duration_seconds("4:37"), 277)
        self.assertEqual(drdb.duration_seconds("1:02:11"), 3731)
        self.assertIsNone(drdb.duration_seconds("later"))
        self.assertEqual(drdb.label_names("Columbia/Legacy"),
                         {"columbia", "legacy"})
        self.assertTrue(drdb.label_names("Columbia/Legacy")
                        & drdb.label_names("Columbia Records"))
        self.assertEqual(drdb.release_year("1996-04-30"), 1996)
        self.assertIsNone(drdb.release_year("no idea"))


class FingerprintTest(unittest.TestCase):
    """The fingerprint is the playing track, and nothing around it."""

    def fingerprint(self, **info):
        base = {"title": "Down In A Hole", "album": "Unplugged", "artist": "",
                "state": "play", "elapsed": None, "duration": 346.0,
                "audio": "44100:16:2", "date": "1996-04-30",
                "label": "Columbia/Legacy", "track_no": 5}
        base.update(info)
        return APP._drdb_fingerprint(base)

    def test_the_playing_track_is_the_whole_fingerprint(self):
        got = self.fingerprint()
        self.assertEqual(got, {"year": "1996-04-30", "label": "Columbia/Legacy",
                               "disc": None, "track_no": 5, "duration": 346.0,
                               "rate": 44100, "bits": 16, "channels": 2})

    def test_a_multi_disc_track_number_is_split_back_apart(self):
        # upmpdcli's Qobuz plugin queues Delicate Sound of Thunder as
        # 1001..1005 for disc 1: disc*1000 + track.
        got = self.fingerprint(track_no=1005)
        self.assertEqual((got["disc"], got["track_no"]), (1, 5))
        self.assertEqual(self.fingerprint(track_no=2011)["disc"], 2)
        self.assertEqual(self.fingerprint(track_no=2011)["track_no"], 11)

    def test_a_plain_track_number_names_no_disc(self):
        got = self.fingerprint(track_no=7)
        self.assertEqual((got["disc"], got["track_no"]), (None, 7))

    def test_a_renderer_that_publishes_no_edition_tags_still_fingerprints(self):
        got = self.fingerprint(date="", label="")
        self.assertEqual((got["year"], got["label"]), ("", ""))
        self.assertEqual((got["rate"], got["bits"]), (44100, 16))

    def test_no_stream_format_is_not_an_error(self):
        got = self.fingerprint(audio="")
        self.assertIsNone(got["rate"])
        self.assertEqual(got["track_no"], 5)


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
