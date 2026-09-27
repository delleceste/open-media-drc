#!/usr/bin/env python3
"""Playing a Qobuz search result through upmpdcli's OpenHome playlist.

The panel queues albums the way a control point does: SOAP calls on
upmpdcli's Playlist service (openhome.py), with the plugin's own track URLs
and a DIDL per track.  A fake Playlist service stands in for upmpdcli here;
it can fail an action the way upmpdcli does after MPD dropped its idle
connection ("Action Failed" once, fine on the next call), which is what the
retry rules must survive without ever inserting a track twice.
"""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape, unescape

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

import openhome  # noqa: E402
import qobuz_search as qs  # noqa: E402
import qobuz_web  # noqa: E402

SPEC = importlib.util.spec_from_file_location("omdrc_qobuz_play_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


class FakePlaylist:
    """upmpdcli's Playlist service, as far as the panel uses it."""

    def __init__(self):
        self.tracks = []            # [(id, uri, metadata)]
        self.next_id = 1
        self.sought = None
        self.fail = set()           # actions whose next call fails
        self.fail_after_insert = False  # Insert fails, but the track went in
        self.calls = []

    def handle(self, action, args):
        self.calls.append(action)
        if action in self.fail:
            self.fail.discard(action)
            return None
        if action == "IdArray":
            ids = struct.pack(f">{len(self.tracks)}I", *[t[0] for t in self.tracks])
            return {"Token": "1", "Array": base64.b64encode(ids).decode()}
        if action == "DeleteAll":
            self.tracks = []
            return {}
        if action == "Insert":
            after = int(args["AfterId"])
            ids = [t[0] for t in self.tracks]
            pos = ids.index(after) + 1 if after else 0
            self.tracks.insert(pos, (self.next_id, args["Uri"], args["Metadata"]))
            self.next_id += 1
            if self.fail_after_insert:
                self.fail_after_insert = False
                return None
            return {"NewId": str(self.next_id - 1)}
        if action == "Read":
            track = next(t for t in self.tracks if t[0] == int(args["Id"]))
            return {"Uri": track[1], "Metadata": track[2]}
        if action == "SeekId":
            self.sought = int(args["Value"])
            return {}
        raise AssertionError(action)


def serve(fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            action = re.search(r"<u:(\w+) ", body).group(1)
            args = {m.group(1): unescape(m.group(2))
                    for m in re.finditer(r"<(\w+)>(.*?)</\1>", body, re.S)}
            answer = fake.handle(action, args)
            if answer is None:
                reply = (b'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                         b'<s:Body><s:Fault><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
                         b"<errorCode>501</errorCode><errorDescription>Action Failed</errorDescription>"
                         b"</UPnPError></detail></s:Fault></s:Body></s:Envelope>")
                self.send_response(500)
            else:
                out = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in answer.items())
                reply = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                         f'<s:Body><u:{action}Response xmlns:u="{openhome.PLAYLIST}">{out}'
                         f"</u:{action}Response></s:Body></s:Envelope>").encode()
                self.send_response(200)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02},
                     daemon=True).start()
    return server


class OpenHomeCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakePlaylist()
        self.server = serve(self.fake)
        self.url = f"http://127.0.0.1:{self.server.server_port}/ctl-Playlist"
        self.playlist = openhome.Playlist(self.url)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()


class PlaylistTest(OpenHomeCase):
    def test_insert_and_ids(self):
        a = self.playlist.insert(0, "http://x/1", "<DIDL-Lite/>")
        b = self.playlist.insert(a, "http://x/2", "<DIDL-Lite/>")
        self.assertEqual(self.playlist.ids(), [a, b])
        self.assertEqual(self.fake.tracks[0][2], "<DIDL-Lite/>")

    def test_a_repeatable_action_is_retried_once(self):
        self.fake.fail.add("DeleteAll")
        self.playlist.delete_all()
        self.assertEqual(self.fake.calls, ["DeleteAll", "DeleteAll"])

    def test_a_failed_insert_that_did_not_go_in_is_retried(self):
        self.fake.fail.add("Insert")
        new = self.playlist.insert(0, "http://x/1", "m")
        self.assertEqual([t[1] for t in self.fake.tracks], ["http://x/1"])
        self.assertEqual(new, self.fake.tracks[0][0])

    def test_a_failed_insert_that_went_in_is_not_repeated(self):
        first = self.playlist.insert(0, "http://x/1", "m")
        self.fake.fail_after_insert = True
        new = self.playlist.insert(first, "http://x/2", "m")
        self.assertEqual([t[1] for t in self.fake.tracks], ["http://x/1", "http://x/2"])
        self.assertEqual(new, self.fake.tracks[1][0])

    def test_a_refusal_names_the_action_and_upnp_error(self):
        self.fake.fail.add("Insert")
        self.fake.fail.add("IdArray")
        with patch.object(self.fake, "handle", side_effect=lambda a, k: None):
            with self.assertRaisesRegex(openhome.OpenHomeError, "501 Action Failed"):
                self.playlist.insert(0, "http://x/1", "m")

    def test_unreachable(self):
        with self.assertRaisesRegex(openhome.OpenHomeError, "cannot reach upmpdcli"):
            openhome.Playlist("http://127.0.0.1:9/ctl", timeout=1).ids()


class DiscoveryTest(unittest.TestCase):
    def renderer(self, location, name):
        return openhome.Renderer(location, name, location + "/ctl")

    def find(self, locations, local_hosts, name=""):
        return openhome.find_local_renderer(
            name, search=lambda st, t: locations,
            describe=lambda loc: self.renderer(loc, "box" if "10.0.0.5" in loc else "other"),
            local=lambda host: host in local_hosts)

    def test_a_renderer_on_another_machine_is_never_used(self):
        with self.assertRaises(openhome.OpenHomeError):
            self.find(["http://10.0.0.9:49152/d.xml"], {"10.0.0.5"})

    def test_the_local_one_is_chosen(self):
        r = self.find(["http://10.0.0.9:49152/d.xml", "http://10.0.0.5:49152/d.xml"],
                      {"10.0.0.5"})
        self.assertEqual(r.host, "10.0.0.5")

    def test_the_friendly_name_picks_among_local_ones(self):
        r = self.find(["http://127.0.0.1:1/d.xml", "http://10.0.0.5:49152/d.xml"],
                      {"10.0.0.5", "127.0.0.1"}, name="BOX")
        self.assertEqual(r.name, "box")

    def test_loopback_is_local(self):
        self.assertTrue(openhome.is_local_address("127.0.0.1"))
        self.assertFalse(openhome.is_local_address("192.0.2.1"))


class DidlTest(unittest.TestCase):
    def test_metadata_is_escaped_and_complete(self):
        text = openhome.didl("http://h:49149/qobuz/track/version/1/trackId/1?a=1&b=2",
                             "Tristan & Isolde: <Vorspiel>", artist="Orchestra",
                             album="Wagner", date="2025-01-10", label="Decca (UMO)",
                             track_number=1, disc_number=2, duration=3725)
        self.assertIn("<dc:title>Tristan &amp; Isolde: &lt;Vorspiel&gt;</dc:title>", text)
        self.assertIn("<dc:date>2025-01-10</dc:date>", text)
        self.assertIn("<dc:publisher>Decca (UMO)</dc:publisher>", text)
        self.assertIn('duration="1:02:05"', text)
        self.assertIn("trackId/1?a=1&amp;b=2</res>", text)

    def test_no_composer_role_reaches_the_artist_tag(self):
        # upmpdcli folds every upnp:artist into MPD's Artist, whatever its role.
        self.assertEqual(openhome.didl("u", "t", artist="A").count("<upnp:artist"), 1)


def raw_album(album_id="a1", label="PENTATONE", date="2025-01-10"):
    return {
        "id": album_id, "title": "Symphony No. 7", "version": "",
        "artist": {"name": "Bamberger Symphoniker"}, "composer": {"name": "Anton Bruckner"},
        "label": {"name": label}, "genre": {"name": "Symphonies"},
        "release_date_original": date, "image": {"small": "s.jpg", "large": "l.jpg"},
        "tracks_count": 3, "duration": 3900, "streamable": True,
        "tracks": {"items": [
            {"id": 11, "title": "I. Allegro moderato", "track_number": 1, "media_number": 1,
             "duration": 1200, "performer": {"name": "Jakub Hrusa"},
             "performers": "Jakub Hrusa, Conductor"},
            {"id": 12, "title": "II. Adagio", "track_number": 2, "media_number": 1,
             "duration": 1400, "streamable": False},
            {"id": 13, "title": "III. Scherzo", "version": "Live", "track_number": 3,
             "media_number": 1, "duration": 600},
        ]},
    }


class PlayedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "sub" / "played.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_counts_and_persists(self):
        store = qs.PlayedAlbums(self.path)
        self.assertTrue(store.record(raw_album(), [{"name": "Jakub Hrusa", "roles": []}]))
        store.record(raw_album(), [])
        again = qs.PlayedAlbums(self.path)
        self.assertEqual(again.counts(), {"a1": 2})
        self.assertNotIn("tracks", json.loads(Path(self.path).read_text())["albums"][0]["item"])

    def test_newest_first_and_limited(self):
        store = qs.PlayedAlbums(self.path, limit=2)
        for i in range(3):
            store.record(raw_album(f"a{i}"), [])
        self.assertEqual([c["id"] for c in store.recent()], ["a2", "a1"])

    def test_matching_needs_every_word_performers_included(self):
        store = qs.PlayedAlbums(self.path)
        store.record(raw_album(), [{"name": "Jakub Hrůša", "roles": []}])
        self.assertEqual(len(store.matching("bruckner hrůša")), 1)
        self.assertEqual(store.matching("bruckner mahler"), [])
        self.assertEqual(len(store.matching("")), 1)

    def test_an_unwritable_file_is_not_an_error(self):
        store = qs.PlayedAlbums("/nonexistent-dir/x/played.json")
        with patch("os.makedirs", side_effect=OSError("read-only")):
            self.assertFalse(store.record(raw_album(), []))

    def test_a_played_album_is_found_however_deep_qobuz_ranks_it(self):
        store = qs.PlayedAlbums(self.path)
        store.record(raw_album("deep"), [])
        others = [{**raw_album(f"o{i}", label="Other"), "tracks": None} for i in range(60)]

        def fetch(endpoint, params):
            if endpoint == "catalog/search":
                page = others[params["offset"]:params["offset"] + params["limit"]]
                return {"albums": {"items": page, "total": len(others)}}
            raise AssertionError(endpoint)

        cat = qs.QobuzCatalog(qs.Settings(scan=50, auto_scan=50, max_enrich=0),
                              fetch=fetch, played=store)
        answer = cat.search("symphony", labels=["Pentatone"])
        self.assertEqual([(c["id"], c.get("played")) for c in answer["results"]],
                         [("deep", 1)])


class PlayRouteTest(OpenHomeCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.client = APP.app.test_client()
        albums = {"a1": raw_album()}

        def fetch(endpoint, params):
            if endpoint == "album/get":
                return albums[params["album_id"]]
            raise AssertionError(endpoint)

        self.catalog = qs.QobuzCatalog(qs.Settings(), fetch=fetch,
                                       played=qs.PlayedAlbums(self.tmp.name + "/p.json"))
        target = openhome.Renderer("http://192.0.2.7:49152/d.xml", "box", self.url)
        self.patches = [
            patch.object(qobuz_web, "catalog", return_value=self.catalog),
            patch.object(qobuz_web, "renderer", return_value=target),
            patch.object(qobuz_web, "_renderer_running", lambda: True),
            patch.object(qobuz_web, "_upmpdcli_options", lambda: {}),
        ]
        for p in self.patches:
            p.start()
        qobuz_web._renderer = (0.0, False)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()
        super().tearDown()

    def play(self, **body):
        return self.client.post("/qobuz/play", json=body)

    def test_replace_queues_streamable_tracks_and_plays_the_first(self):
        self.fake.tracks = [(99, "http://old", "m")]
        self.fake.next_id = 100
        data = self.play(album_id="a1", mode="replace").get_json()
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["queued"], 2)
        self.assertEqual([t[1] for t in self.fake.tracks], [
            "http://192.0.2.7:49149/qobuz/track/version/1/trackId/11",
            "http://192.0.2.7:49149/qobuz/track/version/1/trackId/13"])
        self.assertEqual(self.fake.sought, self.fake.tracks[0][0])
        first, second = self.fake.tracks[0][2], self.fake.tracks[1][2]
        self.assertIn("<upnp:artist>Jakub Hrusa</upnp:artist>", first)
        self.assertIn("<upnp:artist>Bamberger Symphoniker</upnp:artist>", second)
        self.assertIn("<dc:title>III. Scherzo (Live)</dc:title>", second)
        self.assertIn("<dc:publisher>PENTATONE</dc:publisher>", first)
        self.assertIn("<dc:date>2025-01-10</dc:date>", first)
        self.assertIn("<upnp:albumArtURI>l.jpg</upnp:albumArtURI>", first)
        self.assertTrue(data["remembered"])
        self.assertEqual(self.catalog.played.counts(), {"a1": 1})

    def test_replace_can_start_at_a_given_track(self):
        self.play(album_id="a1", mode="replace", start="13")
        self.assertEqual(self.fake.sought, self.fake.tracks[1][0])

    def test_append_adds_at_the_end_and_leaves_playback_alone(self):
        self.fake.tracks = [(7, "http://old", "m")]
        self.fake.next_id = 8
        data = self.play(album_id="a1", mode="append").get_json()
        self.assertTrue(data["ok"], data)
        self.assertEqual([t[0] for t in self.fake.tracks], [7, 8, 9])
        self.assertIsNone(self.fake.sought)

    def test_the_plugin_port_and_host_come_from_upmpdcli_conf(self):
        with patch.object(qobuz_web, "_upmpdcli_options",
                          lambda: {"plgmicrohttpport": "50000", "plgmicrohttphost": "box.lan"}):
            self.play(album_id="a1", mode="replace")
        self.assertTrue(self.fake.tracks[0][1].startswith("http://box.lan:50000/qobuz/"))

    def test_a_failure_midway_says_how_far_it_got(self):
        original = self.fake.handle
        inserts = []

        def handle(action, args):
            if action == "Insert":
                inserts.append(1)
                if len(inserts) > 1:
                    return None
            return original(action, args)

        with patch.object(self.fake, "handle", side_effect=handle):
            data = self.play(album_id="a1", mode="replace").get_json()
        self.assertFalse(data["ok"])
        self.assertIn("queued 1 of 2 tracks", data["error"])
        self.assertEqual(self.catalog.played.counts(), {})

    def test_bad_mode(self):
        self.assertEqual(self.play(album_id="a1", mode="shuffle").status_code, 400)

    def test_no_play_without_upmpdcli(self):
        with patch.object(qobuz_web, "_renderer_running", lambda: False):
            response = self.play(album_id="a1")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.fake.calls, [])

    def test_played_list(self):
        self.play(album_id="a1", mode="replace")
        with patch.object(qobuz_web, "played", return_value=self.catalog.played):
            data = self.client.get("/qobuz/played").get_json()
        self.assertEqual([(a["id"], a["played"]) for a in data["albums"]], [("a1", 1)])


if __name__ == "__main__":
    unittest.main()
