#!/usr/bin/env python3
"""The small-screen kiosk UI (omdrc-ctrl/src/kiosk, served under /k/).

It is a separate package and a pure client of the panel's routes, so what needs
pinning is the seam: it is registered, it serves its shell and every script the
shell references, it never exposes a command line, and the desktop panel can
still hand it over with ?view=mini.
"""
import importlib.util
import re
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

SPEC = importlib.util.spec_from_file_location("omdrc_kiosk_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)


class KioskTests(unittest.TestCase):
    def setUp(self):
        self.client = APP.app.test_client()
        self._saved = (APP.COMMANDS, APP.CMD_MAP)
        APP.COMMANDS = [{"id": "drc_off", "what": "OFF", "group": "drc", "type": "WRITE",
                         "button": "Apply", "confirm": "yes", "cmd": "/bin/secret --flag"}]
        APP.CMD_MAP = {c["id"]: c for c in APP.COMMANDS}

    def tearDown(self):
        APP.COMMANDS, APP.CMD_MAP = self._saved

    def test_shell_is_served_and_lists_existing_scripts(self):
        page = self.client.get("/k/")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        scripts = re.findall(r'src="(/k/static/[^"?]+)', html)
        self.assertIn("/k/static/main.js", scripts)
        for path in scripts:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.get("/k/static/kiosk.css").status_code, 200)

    def test_versioned_assets_are_cached_for_good_the_shell_is_not(self):
        """The shell names each script with ?v=<asset version>, which a deploy
        changes: those URLs may stay cached, so a restart of the Android app
        does not ask about every file again.  The shell itself must not be."""
        page = self.client.get("/k/")
        self.assertNotIn("max-age=31536000", page.headers.get("Cache-Control", ""))
        url = re.findall(r'src="(/k/static/core\.js\?v=[^"]+)"', page.get_data(as_text=True))[0]
        self.assertIn("max-age=31536000", self.client.get(url).headers["Cache-Control"])
        plain = self.client.get("/k/static/core.js").headers.get("Cache-Control", "")
        self.assertNotIn("max-age=31536000", plain)

    def test_version_is_the_one_the_shell_names_its_scripts_with(self):
        """An open kiosk compares /k/version with the v= of its own scripts and
        reloads when they differ (main.js): the two must agree while nothing changed."""
        page = self.client.get("/k/").get_data(as_text=True)
        shell_v = re.findall(r'src="/k/static/main\.js\?v=([^"]+)"', page)[0]
        self.assertEqual(self.client.get("/k/version").get_json()["version"], shell_v)

    def test_config_exposes_commands_but_never_their_command_line(self):
        data = self.client.get("/k/api/config").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual([c["id"] for c in data["commands"]], ["drc_off"])
        self.assertNotIn("cmd", data["commands"][0])
        self.assertEqual(data["commands"][0]["confirm"], "yes")
        self.assertEqual(set(data["features"]), {"drdb", "cdin", "drc", "qobuz_search"})

    def test_recent_dr_route_bounds_the_lookback_and_uses_saved_tracks(self):
        with patch.object(APP._DR_STORE, "recent_tracks", return_value={"tracks": [{"dr": 12}], "count": 1}) as recent:
            data = self.client.get("/dr/library/recent?hours=168&limit=20").get_json()
            self.assertEqual(data["tracks"], [{"dr": 12}])
            self.assertEqual(data["count"], 1)
            self.assertEqual(recent.call_args.args[1], 20)
        self.assertEqual(self.client.get("/dr/library/recent?hours=169").status_code, 400)

    def test_local_dr_cover_is_read_only_and_stays_in_music_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            covers = root / "album" / "Covers"
            covers.mkdir(parents=True)
            (covers / "1.jpg").write_bytes(b"cover")
            album = {"source": "local", "origin": "", "ref": "album"}
            with patch.object(APP._DR_STORE, "album", return_value=album), \
                 patch.object(APP, "_resolve_mpd_music_directory", return_value=tmp):
                response = self.client.get("/dr/library/art?key=local:album")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b"cover")
                self.assertEqual(self.client.get("/dr/library/art?key=qobuz:album").status_code, 404)
                music = root / "music"
                music.mkdir()
                (music / "USBHD2").symlink_to(root, target_is_directory=True)
                album["ref"] = "USBHD2/album"
                with patch.object(APP, "_resolve_mpd_music_directory", return_value=str(music)):
                    self.assertEqual(self.client.get("/dr/library/art?key=local:album").data, b"cover")
                album["ref"] = "../outside"
                self.assertEqual(self.client.get("/dr/library/art?key=local:album").status_code, 404)

    def test_video_page_follows_the_local_remote_health(self):
        import kiosk
        class Reply:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, *_):
                return b'{"ok":true,"roots":[]}'
        with patch.object(kiosk.urllib.request, "urlopen", return_value=Reply()) as probe:
            self.assertEqual(self.client.get("/k/api/video").get_json(), {"ok": True, "available": True})
            probe.assert_called_once_with("http://127.0.0.1:9080/api/roots", timeout=0.5)
        with patch.object(kiosk.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertEqual(self.client.get("/k/api/video").get_json(), {"ok": True, "available": False})

    def test_view_mini_redirects_the_desktop_panel_to_the_kiosk(self):
        r = self.client.get("/?view=mini")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/k/"))

    def test_transport_maps_fixed_words_to_fixed_mpc_commands(self):
        import kiosk
        seen = []
        saved = kiosk._mpc
        kiosk._mpc = lambda args: (seen.append(args) or (True, ""))
        try:
            for word in ("play", "pause", "stop", "next", "prev"):
                r = self.client.post("/k/api/transport", json={"action": word})
                self.assertEqual((r.status_code, r.get_json()["ok"]), (200, True), word)
            self.assertEqual(seen, [["play"], ["pause"], ["stop"], ["next"], ["prev"]])
            for bad in ("clear", "play; reboot", "", None):
                r = self.client.post("/k/api/transport", json={"action": bad})
                self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(len(seen), 5, "nothing but the known words reaches mpc")
            kiosk._mpc = lambda args: (False, "MPD is down")
            r = self.client.post("/k/api/transport", json={"action": "play"})
            self.assertEqual(r.get_json(), {"ok": False, "error": "MPD is down"})
        finally:
            kiosk._mpc = saved

    def test_seek_passes_only_a_number_formatted_here(self):
        """The player strip's seek: the request's seconds become an H:MM:SS
        we format ourselves; anything that is not a non-negative number is
        refused before mpc is run."""
        import kiosk
        seen = []
        saved = kiosk._mpc
        kiosk._mpc = lambda args: (seen.append(args) or (True, ""))
        try:
            for seconds, clock in ((0, "0:00:00"), (95.7, "0:01:35"), (3725, "1:02:05"), ("61", "0:01:01")):
                r = self.client.post("/k/api/transport", json={"action": "seek", "seconds": seconds})
                self.assertEqual(r.status_code, 200, seconds)
                self.assertEqual(seen[-1], ["seek", clock])
            for bad in (-1, "1:00; reboot", None, "nan", "inf", [3]):
                r = self.client.post("/k/api/transport", json={"action": "seek", "seconds": bad})
                self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(len(seen), 4)
        finally:
            kiosk._mpc = saved

    def test_jump_plays_a_queue_position_and_nothing_else(self):
        """The full player's queue: a tap plays song n (1-based), passed on as
        a number we write, never as the request's text."""
        import kiosk
        seen = []
        saved = kiosk._mpc
        kiosk._mpc = lambda args: (seen.append(args) or (True, ""))
        try:
            r = self.client.post("/k/api/transport", json={"action": "jump", "pos": 3})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(seen, [["play", "3"]])
            for bad in (0, -1, "3", "3; reboot", 2.5, True, None):
                r = self.client.post("/k/api/transport", json={"action": "jump", "pos": bad})
                self.assertEqual(r.status_code, 400, bad)
            self.assertEqual(len(seen), 1)
        finally:
            kiosk._mpc = saved

    def test_every_page_script_registers_a_page(self):
        pages = sorted((SRC / "kiosk/static/pages").glob("*.js"))
        self.assertGreaterEqual(len(pages), 8)
        for path in pages:
            if path.name == "favorites.js":  # Qobuz's Library view, not a pager page
                continue
            self.assertIn("K.registerPage(", path.read_text(), path.name)

    def test_a_page_stops_its_work_when_hidden(self):
        # "no computation without visible feedback": every page that starts
        # streams or pollers must give them back in hide().
        for path in (SRC / "kiosk/static/pages").glob("*.js"):
            text = path.read_text()
            if "K.Poller(" in text or "K.streams.open(" in text or "listen(" in text:
                self.assertIn(".hide =", text, f"{path.name} starts work but has no hide()")



class FakeMpd:
    """Just enough of the MPD protocol for the click test: partitions, outputs and
    a player per partition.  Records every command as "<partition>: <command>"."""

    def __init__(self, state="play", song="3", elapsed="95.500",
                 outputs=(("OKTO-DAC", True), ("DRC-native", False), ("OMDRC Spectrum", True)),
                 stale=()):
        import socket
        self.log = []
        self.song, self.elapsed = song, elapsed
        self.players = {"default": {"state": state, "songid": "7"}}
        self.outputs = [{"name": n, "enabled": e, "part": "default"} for n, e in outputs]
        if stale:                           # left behind by an interrupted run
            self.players["omdrc-cal"] = {"state": "stop", "songid": ""}
            for o in self.outputs:
                if o["name"] in stale:
                    o["part"] = "omdrc-cal"
        self.clients = {}                   # partition -> connections inside it
        self.next_id = 100
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.client, args=(conn,), daemon=True).start()

    def client(self, conn):
        f = conn.makefile("r")
        conn.sendall(b"OK MPD 0.24.0\n")
        me = {"part": "default"}
        in_list = False
        for line in f:
            line = line.strip()
            if line == "command_list_begin":
                in_list, reply = True, ""
                continue
            if line == "command_list_end":
                in_list = False
                conn.sendall((reply + "OK\n").encode())
                continue
            self.log.append(f"{me['part']}: {line}")
            out = self.run(line, me)
            if in_list:
                reply += out
            elif out.startswith("ACK"):
                conn.sendall(out.encode())
            else:
                conn.sendall((out + "OK\n").encode())
        me["part"] = None                   # the connection is gone

    def arg(self, line):
        return line.split(" ", 1)[1].strip().strip('"')

    def run(self, line, me):
        cmd = line.split(" ", 1)[0]
        part = me["part"]
        player = self.players.get(part, {})
        if cmd == "status":
            return f"state: {player['state']}\nsong: {self.song}\nsongid: {player['songid']}\nelapsed: {self.elapsed}\n"
        if cmd == "outputs":
            out = ""
            for i, o in enumerate(self.outputs):
                mine = o["part"] == part
                out += (f"outputid: {i}\noutputname: {o['name']}\nplugin: {'alsa' if mine else 'dummy'}\n"
                        f"outputenabled: {1 if mine and o['enabled'] else 0}\n")
            return out
        if cmd == "currentsong":            # what the player strip reads
            return ("file: http://192.168.1.10:49149/qobuz/track/version/1/trackId/4636613\n"
                    "Title: Adagio\nArtist: Nelsons\nAlbum: Bruckner 7\nDate: 2019\n"
                    "Label: Deutsche Grammophon\nTime: 1301\nduration: 1301.200\n")
        if cmd == "playlistinfo":
            return ("file: http://h:49149/qobuz/track/version/1/trackId/11\nTitle: I. Allegro\n"
                    "Artist: Nelsons\nduration: 1250.000\nPos: 0\nId: 7\n"
                    "file: /music/b.flac\nPos: 1\nId: 8\n")
        if cmd == "listpartitions":
            return "".join(f"partition: {n}\n" for n in self.players)
        if cmd == "newpartition":
            self.players[self.arg(line)] = {"state": "stop", "songid": ""}
        if cmd == "partition":
            me["part"] = self.arg(line)
        if cmd == "delpartition":
            name = self.arg(line)
            if any(o["part"] == name for o in self.outputs):
                return "ACK [5@0] {delpartition} partition still has outputs\n"
            self.players.pop(name, None)
        if cmd == "moveoutput":
            for o in self.outputs:
                if o["name"] == self.arg(line):
                    o["part"] = part
        if cmd == "addid":
            self.next_id += 1
            return f"Id: {self.next_id}\n"
        if cmd == "playid":
            player["songid"], player["state"] = line.split()[1], "play"
            # the click track "ends" shortly after
            threading.Timer(0.5, lambda: player.__setitem__("state", "stop")).start()
        if cmd == "pause":
            player["state"] = "pause"
        if cmd == "stop":
            player["state"] = "stop"
        return ""


class ClickTestTests(unittest.TestCase):
    def run_test(self, state="play", **fake):
        import kiosk
        mpd = FakeMpd(state=state, **fake)
        saved = (kiosk._mpd_port, kiosk._playback_rate)
        kiosk._mpd_port, kiosk._playback_rate = (lambda: mpd.port), (lambda: 44100)
        try:
            client = APP.app.test_client()
            r = client.post("/k/api/clicktest")
            for _ in range(60):                      # wait for the worker to finish
                if kiosk._click_lock.acquire(blocking=False):
                    kiosk._click_lock.release()
                    break
                import time; time.sleep(0.1)
            return r, mpd
        finally:
            kiosk._mpd_port, kiosk._playback_rate = saved

    def test_the_clicks_play_in_a_side_partition_and_the_main_queue_is_untouched(self):
        # qobuzconnect2mpd owns the main queue and reacts to any foreign edit
        r, mpd = self.run_test("play")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["rate"], 44100)
        self.assertEqual(r.get_json()["outputs"], ["OKTO-DAC", "OMDRC Spectrum"])   # the enabled ones
        add = next(c for c in mpd.log if " addid " in c)
        self.assertTrue(add.startswith("omdrc-cal: "), add)
        self.assertIn("/k/api/clicks.wav?rate=44100", add)
        self.assertIn("omdrc-cal: playid 101", mpd.log)
        self.assertFalse([c for c in mpd.log if c.startswith("default: ") and
                          c.split(": ", 1)[1].split()[0] in ("addid", "playid", "deleteid", "seek", "clear")])

    def test_the_music_is_stopped_and_not_resumed(self):
        for state in ("play", "pause"):
            r, mpd = self.run_test(state)
            self.assertIn("default: stop", mpd.log)
            self.assertEqual(mpd.players["default"]["state"], "stop")
            self.assertFalse([c for c in mpd.log if c.startswith("default: pause") or c.startswith("default: play")])
            self.assertEqual(r.get_json()["stopped"], state)

    def test_the_outputs_are_handed_back_and_the_partition_removed(self):
        r, mpd = self.run_test("play")
        self.assertTrue(all(o["part"] == "default" for o in mpd.outputs))
        self.assertEqual(list(mpd.players), ["default"])
        self.assertIn("default: delpartition \"omdrc-cal\"", mpd.log)

    def test_an_interrupted_run_is_cleaned_up_first(self):
        r, mpd = self.run_test("play", stale=("OKTO-DAC",))
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("OKTO-DAC", r.get_json()["outputs"])    # recovered, then borrowed again
        self.assertTrue(all(o["part"] == "default" for o in mpd.outputs))
        self.assertEqual(list(mpd.players), ["default"])

    def test_with_no_enabled_output_nothing_is_played(self):
        r, mpd = self.run_test("play", outputs=(("OKTO-DAC", False),))
        self.assertEqual(r.status_code, 500)
        self.assertFalse([c for c in mpd.log if " addid " in c or " newpartition " in c])

    def test_the_click_track_is_a_short_wav_at_the_requested_rate(self):
        import io, wave
        data = APP.app.test_client().get("/k/api/clicks.wav?rate=96000").data
        w = wave.open(io.BytesIO(data))
        self.assertEqual((w.getframerate(), w.getnchannels()), (96000, 2))
        self.assertTrue(10 < w.getnframes() / 96000 < 13)

    def test_the_clicks_are_quiet_enough_for_any_listening_volume(self):
        import io, struct, wave
        data = APP.app.test_client().get("/k/api/clicks.wav?rate=48000").data
        w = wave.open(io.BytesIO(data))
        raw = w.readframes(w.getnframes())
        peak = max(abs(v) for v in struct.unpack("<%dh" % (len(raw) // 2), raw))
        self.assertLessEqual(peak, 32767 * 10 ** (-29 / 20))     # at most -29 dBFS
        self.assertGreater(peak, 32767 * 10 ** (-31 / 20))       # but there


class PlayerTests(unittest.TestCase):
    """/k/api/player, the Qobuz page's strip: MPD's own state over one protocol
    connection (no pgrep/ps/mpc, so it can be polled)."""

    def ask(self, **fake):
        import kiosk
        mpd = FakeMpd(**fake)
        saved = kiosk._mpd_port
        kiosk._mpd_port = lambda: mpd.port
        try:
            return APP.app.test_client().get("/k/api/player").get_json(), mpd
        finally:
            kiosk._mpd_port = saved

    def test_reports_state_position_and_the_current_song(self):
        d, mpd = self.ask(state="pause", song="3", elapsed="95.500")
        self.assertTrue(d["ok"])
        self.assertEqual((d["state"], d["elapsed"], d["duration"], d["pos"]), ("pause", 95.5, 1301.0, 4))
        self.assertEqual((d["title"], d["artist"], d["album"], d["label"]),
                         ("Adagio", "Nelsons", "Bruckner 7", "Deutsche Grammophon"))
        self.assertEqual([c.split(": ", 1)[1] for c in mpd.log], ["status", "currentsong"])

    def test_queue_edits_use_ids_and_remove_album_beyond_display_limit(self):
        import kiosk
        from unittest.mock import MagicMock, patch
        mpd = MagicMock()
        mpd.__enter__.return_value = mpd
        mpd.lines.return_value = ["file: a", "Id: 0", "Album: Record", "AlbumArtist: A",
                                  "file: b", "Id: 9", "Album: Other",
                                  "file: c", "Id: 600", "Album: Record", "AlbumArtist: A"]
        client = APP.app.test_client()
        with patch.object(kiosk, "_Mpd", return_value=mpd):
            for action, extra, commands in [
                    ("clear", {}, ("clear",)),
                    ("remove", {"id": "0"}, ("deleteid 0",)),
                    ("remove_album", {"id": "0"}, ("deleteid 0", "deleteid 600"))]:
                result = client.post("/k/api/queue", json={"action": action, **extra}).get_json()
                self.assertTrue(result["ok"], result)
                self.assertEqual(mpd.cmd.call_args.args, commands)
            self.assertEqual(client.post("/k/api/queue", json={"action": "remove", "id": "1\nclear"}).status_code, 400)

    def test_the_queue_marks_the_song_playing(self):
        import kiosk
        mpd = FakeMpd(state="play")
        saved = kiosk._mpd_port
        kiosk._mpd_port = lambda: mpd.port
        try:
            d = APP.app.test_client().get("/k/api/queue").get_json()
        finally:
            kiosk._mpd_port = saved
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["songid"], "7")
        self.assertEqual([(s["pos"], s["id"], s["title"], s["duration"]) for s in d["songs"]],
                         [(1, "7", "I. Allegro", 1250.0), (2, "8", "b.flac", None)])

    def test_no_mpd_is_an_answer_not_an_exception(self):
        import kiosk
        saved = kiosk._mpd_port
        kiosk._mpd_port = lambda: 1              # nothing listens there
        try:
            d = APP.app.test_client().get("/k/api/player").get_json()
        finally:
            kiosk._mpd_port = saved
        self.assertFalse(d["ok"])
        self.assertTrue(d["error"])


if __name__ == "__main__":
    unittest.main()
