#!/usr/bin/env python3
"""/now: what the phone's notification and widget show, waiting for changes."""

import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "omdrc_now_app", ROOT / "omdrc-ctrl/src/app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)

VIDEO_SOURCE = ROOT / "video/webremote/src"
sys.path.insert(0, str(VIDEO_SOURCE))
VSPEC = importlib.util.spec_from_file_location("video_now_under_test", VIDEO_SOURCE / "app.py")
VIDEO = importlib.util.module_from_spec(VSPEC)
VSPEC.loader.exec_module(VIDEO)

NP = {"title": "Song", "album": "Album", "artist": "Artist", "album_artist": "",
      "state": "play", "audio": "96000:24:2"}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class NowPlayingTest(unittest.TestCase):
    def setUp(self):
        APP._NOW_CACHE.update(at=0.0, state=None)
        APP._NOW_DRC.clear()
        self.np = dict(NP)
        patches = [
            mock.patch.object(APP, "_mpd_now_playing_via_protocol", side_effect=lambda port: dict(self.np)),
            mock.patch.object(APP, "_resolve_mpd_port", return_value=None),
            mock.patch.object(APP, "_current_renderer", return_value="upmpdcli"),
            mock.patch.object(APP, "_now_video", return_value=None),
            mock.patch.object(APP, "_active_brutefir_process", return_value=None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.client = APP.app.test_client()

    def _card(self):
        return mock.patch.object(APP, "qconnect_status", side_effect=lambda: APP.jsonify(
            {"ok": True, "line1": "Song · Album", "art": "/qconnect/art?v=abc"}))

    def test_reports_music_with_rate_and_cover(self):
        with self._card():
            data = self.client.get("/now").get_json()
        self.assertEqual(data["music"]["title"], "Song")
        self.assertEqual(data["music"]["rate"], 96000)
        self.assertEqual(data["music"]["bits"], 24)
        self.assertEqual(data["music"]["art"], "/qconnect/art?v=abc")
        self.assertEqual(data["drc"], {"running": False})
        self.assertIsNone(data["video"])
        self.assertTrue(data["token"])

    def test_answers_at_once_when_the_token_is_stale(self):
        with self._card():
            first = self.client.get("/now").get_json()
            APP._NOW_CACHE.update(at=0.0, state=None)
            self.np["title"] = "Next song"
            with mock.patch.object(APP.time, "sleep") as sleep:
                second = self.client.get(f"/now?since={first['token']}&wait=20").get_json()
        sleep.assert_not_called()
        self.assertNotEqual(second["token"], first["token"])
        self.assertEqual(second["music"]["title"], "Next song")

    def test_waits_while_nothing_changes(self):
        with self._card():
            first = self.client.get("/now").get_json()
            clock = iter(range(0, 1000, 10))
            with mock.patch.object(APP.time, "sleep") as sleep, \
                 mock.patch.object(APP.time, "monotonic", side_effect=lambda: float(next(clock))):
                same = self.client.get(f"/now?since={first['token']}&wait=25").get_json()
        self.assertGreaterEqual(sleep.call_count, 1)
        self.assertEqual(same["token"], first["token"])

    def test_drc_is_inspected_once_per_configuration(self):
        process = {"config": "/filters/multipos/brutefir-96000@fdw6.conf"}
        full = {"geometry": "multipos", "rate": 96000, "design_id": "fdw6",
                "description": "FDW 6 cycles", "effective_attenuation_db": 8.0,
                "headroom_safe": True}
        with mock.patch.object(APP, "_active_brutefir_process", return_value=process), \
             mock.patch.object(APP, "_active_brutefir_configuration", return_value=full) as inspect:
            first = APP._now_drc()
            second = APP._now_drc()
        inspect.assert_called_once()
        self.assertEqual(first, second)
        self.assertEqual(first["description"], "FDW 6 cycles")
        self.assertTrue(first["running"])



class NowVideoTest(unittest.TestCase):
    def test_video_art_is_served_through_the_panel(self):
        now = {"ok": True, "playing": True, "paused": False, "title": "Alien",
               "year": "1979", "art": "/api/now/art?v=123"}
        with mock.patch.object(APP.urllib.request, "urlopen",
                               return_value=_Response(json.dumps(now).encode())):
            video = APP._now_video()
        self.assertEqual(video["title"], "Alien")
        self.assertEqual(video["art"], "/now/video-art?v=123")


class VideoNowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.film = self.root / "Alien (1979).mkv"
        self.film.write_bytes(b"x")
        saved = VIDEO.ROOTS
        VIDEO.ROOTS = [str(self.root)]
        self.addCleanup(lambda: setattr(VIDEO, "ROOTS", saved))
        self.client = VIDEO.app.test_client()

    def _mpv(self, props):
        return [mock.patch.object(VIDEO.mpvipc, "is_running", return_value=True),
                mock.patch.object(VIDEO.mpvipc, "get_property",
                                  side_effect=lambda sock, name, default=None: props.get(name, default))]

    def test_idle_mpv_plays_nothing(self):
        patches = self._mpv({"idle-active": True})
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.assertFalse(self.client.get("/api/now").get_json()["playing"])

    def test_names_the_library_film_from_the_imdb_cache_only(self):
        props = {"idle-active": False, "pause": True, "media-title": "Alien (1979).mkv",
                 "path": str(self.film)}
        patches = self._mpv(props) + [mock.patch.object(
            VIDEO.imdb, "lookup", return_value={"found": True, "title": "Alien", "year": "1979",
                                                "director": "Ridley Scott", "poster": ""})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        data = self.client.get("/api/now").get_json()
        self.assertTrue(data["playing"])
        self.assertTrue(data["paused"])
        self.assertEqual(data["title"], "Alien")
        self.assertEqual(data["director"], "Ridley Scott")
        self.assertTrue(data["art"].startswith("/api/now/art?v="))
        self.assertTrue(VIDEO.imdb.lookup.call_args.kwargs["cached_only"])

    def test_a_file_outside_the_library_has_only_its_title(self):
        props = {"idle-active": False, "media-title": "stream", "path": "/elsewhere/x.mkv"}
        patches = self._mpv(props)
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        data = self.client.get("/api/now").get_json()
        self.assertEqual(data["title"], "stream")
        self.assertNotIn("art", data)


if __name__ == "__main__":
    unittest.main()
