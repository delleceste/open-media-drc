#!/usr/bin/env python3
"""The renderer card's activity feedback: what qobuzconnect2mpd is doing in the
gap between the phone pressing play and the first sound.

The daemon reports it in the status file as an `state=<phase>` line plus a ring
of timestamped entries; /qconnect/status turns that into `state` + `events`.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

SPEC = importlib.util.spec_from_file_location("omdrc_activity_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


def parse(text):
    return APP._parse_qconnect_status(text.splitlines())


class StatusParseTest(unittest.TestCase):
    def test_phase_and_ring_are_split_out(self):
        got = parse(
            "[playing] Artist - Title  [0:12 / 4:02]\n"
            "24 bit / 176.4 kHz / stereo\n"
            "state=LOADING SEGMENT\n"
            "11:24:03 queue received: 14 tracks, starting at item 0\n"
            "11:24:07 segment 7/52 (13%)\n"
        )
        self.assertEqual(got["line1"], "[playing] Artist - Title")
        self.assertEqual(got["elapsed"], 12.0)
        self.assertEqual(got["duration"], 242.0)
        self.assertEqual(got["line2"], "24 bit / 176.4 kHz / stereo")
        self.assertEqual(got["state"], "LOADING SEGMENT")
        self.assertEqual(got["events"], [
            "11:24:03 queue received: 14 tracks, starting at item 0",
            "11:24:07 segment 7/52 (13%)",
        ])
        # Legacy single-line consumers get the newest entry.
        self.assertEqual(got["line3"], "11:24:07 segment 7/52 (13%)")

    def test_bare_state_tag_is_not_a_track(self):
        # The controller replaced the queue: the daemon drops the title, and
        # naming the old track here would read as a stalled renderer.
        got = parse("[paused] \n\nstate=NEW PLAYLIST RECEIVED\n11:24:03 queue received: 3 tracks\n")
        self.assertEqual(got["line1"], "")
        self.assertEqual(got["state"], "NEW PLAYLIST RECEIVED")

    def test_older_daemon_without_activity_lines(self):
        got = parse("[playing] Artist - Title  [0:12 / 4:02]\n24 bit / 176.4 kHz / stereo\n")
        self.assertEqual(got["state"], "")
        self.assertEqual(got["events"], [])
        self.assertEqual(got["line3"], "")

    def test_empty_phase_line_means_nothing_in_progress(self):
        got = parse("[playing] Artist - Title  [0:12 / 4:02]\n\nstate=\n")
        self.assertEqual(got["state"], "")
        self.assertEqual(got["line2"], "")
        self.assertEqual(got["events"], [])


class StatusRouteTest(unittest.TestCase):
    def _get(self, contents):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(contents)
            path = f.name
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        APP.QCONNECT_STATUS_FILE = path
        client = APP.app.test_client()
        with patch.object(APP, "_current_renderer", return_value=APP.QCONNECT_SERVICE):
            return json.loads(client.get("/qconnect/status").data)

    def test_route_reports_phase_and_events(self):
        body = self._get("[stopped] \n\nstate=RESOLVING STREAM\n11:24:04 resolving stream URL 1/14 (7%)\n")
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"], "RESOLVING STREAM")
        self.assertEqual(body["events"], ["11:24:04 resolving stream URL 1/14 (7%)"])

    def test_missing_status_file_is_reported_empty(self):
        APP.QCONNECT_STATUS_FILE = "/nonexistent/qconnect-status"
        with patch.object(APP, "_current_renderer", return_value=APP.QCONNECT_SERVICE):
            body = json.loads(APP.app.test_client().get("/qconnect/status").data)
        self.assertFalse(body["ok"])
        self.assertEqual(body["events"], [])
        self.assertEqual(body["state"], "")


class UpmpdcliStatusTest(unittest.TestCase):
    def test_now_playing_omits_artist_credit_dump(self):
        now = {
            "title": "Nutshell (Live)",
            "album": "Unplugged",
            "artist": "Alice In Chains, Producer Name, Producer - Lyricist Name, Lyricist",
            "state": "play",
            "elapsed": 170.0,
            "duration": 297.0,
            "audio": "44100:24:2",
        }
        with patch.object(APP, "_mpd_now_playing_via_protocol", return_value=now):
            got = APP._upmpdcli_qconnect_status()
        self.assertEqual(got["line1"], "[playing] Nutshell (Live) · Unplugged")
        self.assertNotIn("Producer", got["line1"])
        self.assertNotIn("Lyricist", got["line1"])


class PanelMarkupTest(unittest.TestCase):
    """The panel is one template; these keep the wiring from silently rotting."""

    def setUp(self):
        self.html = (SRC / "templates/index.html").read_text(encoding="utf-8")

    def test_activity_toggle_button_is_next_to_the_log_button(self):
        self.assertIn('id="btn-act-lines"', self.html)
        self.assertIn("toggleActivityLines()", self.html)

    def test_phase_takes_over_the_big_line(self):
        self.assertIn("QC_PHASE_PLAYING", self.html)
        self.assertIn("#qc-line1.phase", self.html)

    def test_ring_depth_is_remembered(self):
        self.assertIn("omdrcctrl.qc.activityLines", self.html)


class NowPlayingExtrasTest(unittest.TestCase):
    """The cover and the edition line on the renderer card.

    Art is not an MPD tag and MusicPD's albumart command reads files in its
    library, which a stream is not -- so the only copy of a cover URL for a
    UPnP-queued track is the DIDL upmpdcli cached. Label and year travel the
    other way, as queue tags, and either source may be missing.
    """

    DIDL = ('<DIDL-Lite><item><dc:title>Sorrow</dc:title>'
            '<upnp:album>Delicate Sound of Thunder</upnp:album>'
            '<dc:publisher>Pink Floyd Records</dc:publisher>'
            '<dc:date>1988-11-22</dc:date>'
            '<upnp:albumArtURI>http://example.invalid/cover_600.jpg'
            '</upnp:albumArtURI></item></DIDL-Lite>')

    def metacache(self, uri, didl=None):
        directory = tempfile.mkdtemp()
        path = Path(directory) / "metacache"
        # upmpdcli writes one "<uri>=<didl>" line per track, with the '=' of
        # the XML attributes percent-encoded inside the value.
        body = (didl if didl is not None else self.DIDL).replace("=", "%3D")
        path.write_text(f"{uri}={body}\n", encoding="utf-8")
        return path

    def meta(self, uri, cached_uri=None, didl=None):
        path = self.metacache(cached_uri or uri, didl)
        with patch.object(APP, "UPMPDCLI_METACACHE", str(path)), \
             patch.dict(APP._METACACHE_SEEN,
                        {"uri": None, "mtime": None, "meta": {}}):
            return APP._upmpdcli_didl_meta(uri)

    def test_the_cover_and_edition_are_read_from_the_cached_didl(self):
        got = self.meta("http://host/proxy/qobuz/AB.flac")
        self.assertEqual(got["art"], "http://example.invalid/cover_600.jpg")
        self.assertEqual(got["label"], "Pink Floyd Records")
        self.assertEqual(got["year"], "1988-11-22")

    def test_a_track_the_cache_does_not_hold_yields_nothing(self):
        self.assertEqual(self.meta("http://host/other.flac",
                                   cached_uri="http://host/proxy/x.flac"), {})

    def test_a_missing_cache_is_not_an_error(self):
        with patch.object(APP, "UPMPDCLI_METACACHE", "/nonexistent/metacache"):
            self.assertEqual(APP._upmpdcli_didl_meta("http://host/x.flac"), {})

    def test_an_entry_without_art_still_gives_the_edition(self):
        didl = self.DIDL.replace(
            '<upnp:albumArtURI>http://example.invalid/cover_600.jpg'
            '</upnp:albumArtURI>', '')
        got = self.meta("http://host/x.flac", didl=didl)
        self.assertNotIn("art", got)
        self.assertEqual(got["label"], "Pink Floyd Records")

    def test_the_edition_line_shows_the_year_not_the_release_date(self):
        # One line's worth of room, and the day a record came out is not what
        # anyone reads it for.
        self.assertEqual(APP._edition_line("Columbia", "1988-11-22"),
                         "Columbia · 1988")
        self.assertEqual(APP._edition_line("", "1988-11-22"), "1988")
        self.assertEqual(APP._edition_line("Columbia", ""), "Columbia")
        self.assertEqual(APP._edition_line("", ""), "")

    def test_the_card_payload_carries_both_fields_even_when_empty(self):
        # The browser paints from these keys on every poll; a renderer that
        # has neither must still answer the same shape.
        empty = APP._QC_STATUS_EMPTY
        self.assertEqual((empty["art"], empty["edition"]), ("", ""))
        parsed = parse("[playing] Artist - Title  [0:12 / 4:02]\n24 bit\n")
        self.assertEqual((parsed["art"], parsed["edition"]), ("", ""))



class CoverProxyTest(unittest.TestCase):
    """The panel fetches covers itself.

    The box is on the internet by definition -- it is streaming -- while
    whatever is looking at the panel may be on a phone, a guest network or
    behind a DNS blocker. An <img> pointed at a service's CDN shows nothing
    there, which is what a first version of this did.
    """

    def setUp(self):
        APP._ART_CACHE.clear()

    def tearDown(self):
        APP._ART_CACHE.clear()

    class Response:
        def __init__(self, data=b"\xff\xd8\xff-jpeg-bytes",
                     content_type="image/jpeg"):
            self.headers = {"Content-Type": content_type}
            self._data = data

        def read(self, _limit=None):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def test_a_cover_is_fetched_and_cached(self):
        calls = []

        def urlopen(request, timeout=None):
            calls.append(request.full_url)
            return self.Response()

        with patch("urllib.request.urlopen", urlopen):
            first = APP._fetch_art("http://cdn.invalid/cover.jpg")
            second = APP._fetch_art("http://cdn.invalid/cover.jpg")
        self.assertEqual(first, ("image/jpeg", b"\xff\xd8\xff-jpeg-bytes"))
        self.assertEqual(second, first)
        self.assertEqual(len(calls), 1)          # the second came from cache

    def test_only_http_urls_are_fetched(self):
        # The URL comes from upmpdcli's cache rather than from a request, but
        # a panel that opened whatever it was handed would be a way into
        # whatever the box can reach.
        for url in ("file:///etc/passwd", "ftp://host/x.jpg", "", "/etc/passwd"):
            with patch("urllib.request.urlopen",
                       lambda *a, **k: self.fail("must not fetch " + url)):
                self.assertIsNone(APP._fetch_art(url))

    def test_something_that_is_not_an_image_is_refused(self):
        with patch("urllib.request.urlopen",
                   lambda *a, **k: self.Response(b"<html>", "text/html")):
            self.assertIsNone(APP._fetch_art("http://cdn.invalid/oops"))

    def test_a_fetch_that_fails_is_not_cached(self):
        def boom(*_a, **_k):
            raise OSError("no route to host")

        with patch("urllib.request.urlopen", boom):
            self.assertIsNone(APP._fetch_art("http://cdn.invalid/cover.jpg"))
        self.assertEqual(APP._ART_CACHE, {})

    def test_the_endpoint_answers_404_when_there_is_no_cover(self):
        with patch.object(APP, "_mpd_now_playing_via_protocol",
                          return_value={"file": "http://host/x.flac"}), \
             patch.object(APP, "_resolve_mpd_port", return_value="6600"), \
             patch.object(APP, "_upmpdcli_didl_meta", return_value={}):
            self.assertEqual(APP.app.test_client().get("/qconnect/art").status_code, 404)

    def test_the_endpoint_serves_the_bytes_with_their_type(self):
        with patch.object(APP, "_mpd_now_playing_via_protocol",
                          return_value={"file": "http://host/x.flac"}), \
             patch.object(APP, "_resolve_mpd_port", return_value="6600"), \
             patch.object(APP, "_upmpdcli_didl_meta",
                          return_value={"art": "http://cdn.invalid/cover.jpg"}), \
             patch("urllib.request.urlopen", lambda *a, **k: self.Response()):
            response = APP.app.test_client().get("/qconnect/art")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/jpeg")
        self.assertEqual(response.data, b"\xff\xd8\xff-jpeg-bytes")

    def test_the_card_points_at_this_panel_not_the_cdn(self):
        playing = {"title": "Sorrow", "album": "Delicate Sound of Thunder",
                   "artist": "Pink Floyd", "state": "play", "elapsed": 1.0,
                   "duration": 2.0, "audio": "44100:16:2",
                   "file": "http://host/x.flac", "label": "", "date": ""}
        with patch.object(APP, "_mpd_now_playing_via_protocol", return_value=playing), \
             patch.object(APP, "_resolve_mpd_port", return_value="6600"), \
             patch.object(APP, "_upmpdcli_didl_meta",
                          return_value={"art": "http://cdn.invalid/cover.jpg"}):
            got = APP._upmpdcli_qconnect_status()
        self.assertTrue(got["art"].startswith("/qconnect/art?v="))
        self.assertNotIn("cdn.invalid", got["art"])



class QobuzCoverLineTest(unittest.TestCase):
    """qobuzconnect2mpd writes the cover URL as an art= line in its status
    file. It is for fetching, never for display: it must not reach the
    activity ring, the format line, or the payload a browser receives."""

    STATUS = ("[playing] Pink Floyd - Sorrow \u00b7 Delicate Sound of Thunder  [0:12 / 9:28]\n"
              "16 bit / 44.1 kHz / stereo\n"
              "state=PLAYING\n"
              "art=https://static.qobuz.invalid/covers/xx_600.jpg\n"
              "11:24:03 queue received: 3 tracks\n")

    def test_the_url_is_parsed_but_not_shown(self):
        got = parse(self.STATUS)
        self.assertEqual(got["art_url"], "https://static.qobuz.invalid/covers/xx_600.jpg")
        self.assertEqual(got["line2"], "16 bit / 44.1 kHz / stereo")
        self.assertEqual(got["events"], ["11:24:03 queue received: 3 tracks"])
        self.assertFalse(any("qobuz.invalid" in e for e in got["events"]))

    def test_an_art_line_before_the_format_line_does_not_displace_it(self):
        got = parse("[playing] A - B\nart=https://x.invalid/c.jpg\n24 bit / 96 kHz / stereo\n")
        self.assertEqual(got["line2"], "24 bit / 96 kHz / stereo")

    def test_the_route_sends_a_panel_path_and_never_the_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.txt"
            path.write_text(self.STATUS, encoding="utf-8")
            with patch.object(APP, "QCONNECT_STATUS_FILE", str(path)), \
                 patch.object(APP, "_current_renderer", return_value="qobuzconnect2mpd"), \
                 patch.object(APP, "_mpd_now_playing_via_protocol", return_value={}), \
                 patch.object(APP, "_resolve_mpd_port", return_value="6600"):
                body = APP.app.test_client().get("/qconnect/status").get_data(as_text=True)
        self.assertNotIn("qobuz.invalid", body)
        self.assertIn("/qconnect/art?v=", body)

    def test_the_art_endpoint_reads_the_status_file_for_this_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.txt"
            path.write_text(self.STATUS, encoding="utf-8")
            seen = []
            with patch.object(APP, "QCONNECT_STATUS_FILE", str(path)), \
                 patch.object(APP, "_current_renderer", return_value="qobuzconnect2mpd"), \
                 patch.object(APP, "_fetch_art",
                              lambda url: seen.append(url) or ("image/jpeg", b"jpg")):
                response = APP.app.test_client().get("/qconnect/art")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen, ["https://static.qobuz.invalid/covers/xx_600.jpg"])


if __name__ == "__main__":
    unittest.main()
