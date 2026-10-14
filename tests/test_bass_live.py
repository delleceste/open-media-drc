"""Bass depth while listening (bass_live.py): tracking sessions, the
background analysis of the track playing and its copy kept on the music
drive, the listener's taps against the moment heard, what is shared through
the DR log's repository, and the panel's /bass routes."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

import bass_live                                   # noqa: E402
import dr_volumes                                  # noqa: E402
from dr_store import DrStore                       # noqa: E402
from dr_sync import GitSync                        # noqa: E402

BLOCKS = [
    {"context": "solo", "start": 10.0, "end": 20.0, "seconds": 8.0, "coh_ratio": 1.1,
     "fund_db": 5.0, "score": 8.5, "band": "green"},
    {"context": "pizzicato", "start": 12.0, "end": 13.0, "seconds": 0.5},
    {"context": "tutti", "start": 40.0, "end": 50.0, "seconds": 9.0, "coh_ratio": 1.8,
     "fund_db": -10.0, "score": 2.0, "band": "red"},
]
RAW = {"file": "x", "version": "9.9", "duration_s": 60.0, "bass_seconds": 20.0,
       "pitch_variety": 5, "verdict": "none", "verdict_text": "", "segments": [1, 2],
       "contexts": {"solo": {"seconds": 8.0, "passages": [[10, 20]], "coh_ratio": 1.1}},
       "depth": {"score": 8.5, "band": "green"}, "blocks": BLOCKS}

# Stands in for tools/bass_depth.py: writes RAW to --json and records its input.
FAKE_DETECTOR = textwrap.dedent(f"""
    import json, sys
    out, path = sys.argv[2], sys.argv[3]
    with open(path + ".seen", "w") as f:
        f.write(open(path).read())
    json.dump([{json.dumps(RAW)}], open(out, "w"))
""")


class KeptNames(unittest.TestCase):
    def test_names_any_drive_accepts(self):
        self.assertEqual(bass_live.kept_name("/d", {"artist": "A/B", "album": 'X: "Y"?', "year": 2020,
                                                    "title": "Largo.", "number": 3}),
                         "/d/A_B/X_ _Y__ (2020)/03 - Largo")
        self.assertEqual(bass_live.kept_name("/d", {}), "/d/Unknown artist/Unknown album/Untitled")


class BlockAt(unittest.TestCase):
    def test_longest_passage_containing_the_position(self):
        self.assertEqual(bass_live.block_at({"blocks": BLOCKS}, 12.5)["context"], "solo")

    def test_a_tap_just_after_a_passage_still_refers_to_it(self):
        self.assertEqual(bass_live.block_at({"blocks": BLOCKS}, 52.0)["context"], "tutti")
        self.assertIsNone(bass_live.block_at({"blocks": BLOCKS}, 30.0))
        self.assertIsNone(bass_live.block_at(None, 12.0))


class Worker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.script = os.path.join(self.dir, "fake_detector.py")
        with open(self.script, "w") as f:
            f.write(FAKE_DETECTOR)
        self.music = os.path.join(self.dir, "music")
        os.makedirs(os.path.join(self.music, "Album"))
        with open(os.path.join(self.music, "Album", "01.flac"), "w") as f:
            f.write("local audio")
        self.store = bass_live.BassStore(os.path.join(self.dir, "state", "bass.sqlite"))
        self.song = {"file": "Album/01.flac", "state": "play", "title": "Largo",
                     "artist": "BSO", "album": "Sym 2", "elapsed": 100.0, "duration": 600.0}
        self.downloads = []
        self.drive = os.path.join(self.dir, "drive")
        self.kept = os.path.join(self.drive, "musica", "bass-analysis")
        os.makedirs(self.kept)
        # the "drive" folder stands for a mounted USB drive, the rest for the system disk
        mounted = patch.object(bass_live, "mount_of",
                               lambda p: "/drive" if os.path.realpath(p).startswith(os.path.realpath(self.drive)) else "/")
        mounted.start()
        self.addCleanup(mounted.stop)
        self.live = bass_live.BassLive(
            self.store, self.script, now=lambda: dict(self.song), identify=self.identify,
            local_path=lambda file: os.path.join(self.music, file), delay=lambda: 0.4,
            download=self.download, workdir=self.dir, version="9.9", save_dir=self.kept,
            min_free=0)
        self.session = self.live.start_session("Nelsons")

    def tearDown(self):
        self.tmp.cleanup()

    def identify(self, song):
        return {"key": "local:Album", "source": "local", "ref": "Album",
                "meta": {"title": "Sym 2", "artist": "BSO", "genre": "Classical", "year": 2023},
                "track_key": os.path.basename(song["file"]), "number": 1}

    def download(self, url, dest, progress, cancelled):
        self.downloads.append(url)
        with open(dest, "wb") as f:
            f.write(b"fLaC streamed audio")
        progress(1.0)

    def test_a_local_track_is_analysed_once_per_version(self):
        self.live.step()
        a = self.store.analysis("local:Album|01.flac")
        self.assertEqual(a["status"], "done")
        self.assertEqual(a["result"]["depth"]["score"], 8.5)
        self.assertNotIn("segments", a["result"])                      # compacted
        self.assertNotIn("passages", a["result"]["contexts"]["solo"])
        self.assertEqual(a["genre"], "Classical")
        self.assertTrue(os.path.exists(os.path.join(self.music, "Album", "01.flac.seen")))
        first = a["at"]
        self.live._last_file = ""                                      # played again
        self.live.step()
        self.assertEqual(self.store.analysis("local:Album|01.flac")["at"], first)
        self.live.reanalyse("local:Album|01.flac")
        self.live.step()
        self.assertGreater(self.store.analysis("local:Album|01.flac")["at"], first)

    def test_nothing_happens_outside_a_session(self):
        self.live.stop_session()
        self.live.step()
        self.assertEqual(self.live.state["status"], "off")
        self.assertIsNone(self.store.analysis("local:Album|01.flac"))
        with self.assertRaises(LookupError):
            self.live.tap("back")

    def test_a_streamed_track_is_fetched_onto_the_drive_once(self):
        self.song.update(file="http://box:49149/qobuz/track/123", title="Largo / Allegro")
        self.live.step()
        self.assertEqual(self.downloads, ["http://box:49149/qobuz/track/123"])
        a = self.store.analysis("local:Album|123")
        want = os.path.join(self.kept, "BSO", "Sym 2 (2023)", "01 - Largo _ Allegro.flac")
        self.assertEqual((a["status"], a["saved"], a["session"]), ("done", want, self.session["id"]))
        self.assertTrue(os.path.isfile(want))
        self.assertTrue(os.path.exists(want + ".seen"))                # analysed from there
        self.assertEqual(self.store.active_session()["tracks"], 1)
        self.live.reanalyse("local:Album|123")                         # a later session
        self.live.step()
        self.assertEqual(len(self.downloads), 1)

    def test_without_the_drive_a_streamed_track_is_analysed_then_deleted(self):
        os.rmdir(self.kept)
        self.song["file"] = "http://box:49149/qobuz/track/123"
        self.live.step()
        a = self.store.analysis("local:Album|123")
        self.assertEqual((a["status"], a["saved"]), ("done", ""))
        self.assertIn("missing", self.live.save_status()["why"])
        self.assertFalse(os.path.exists(self.kept))                    # never created
        self.assertEqual([n for n in os.listdir(self.dir) if n.startswith("omdrc-bass-")], [])

    def test_a_folder_on_the_system_disk_is_refused(self):
        with patch.object(bass_live, "on_mounted_drive", lambda p: False):
            self.assertFalse(self.live.save_status()["ok"])

    def test_a_local_track_is_copied_to_the_drive(self):
        self.live.step()
        a = self.store.analysis("local:Album|01.flac")
        self.assertEqual(a["saved"], os.path.join(self.kept, "BSO", "Sym 2 (2023)", "01 - Largo.flac"))
        with open(a["saved"]) as f:
            self.assertEqual(f.read(), "local audio")

    def test_a_local_track_already_on_the_drive_stays_where_it_is(self):
        elsewhere = os.path.join(self.drive, "musica", "Classica", "Album")
        os.makedirs(elsewhere)
        shutil.move(os.path.join(self.music, "Album", "01.flac"), os.path.join(elsewhere, "01.flac"))
        self.live.local_path = lambda file: os.path.join(self.drive, "musica", "Classica", file)
        self.live.step()
        a = self.store.analysis("local:Album|01.flac")
        self.assertEqual(a["saved"], os.path.join(elsewhere, "01.flac"))
        self.assertEqual(os.listdir(self.kept), [])

    def test_unreadable_sources_are_skipped_with_a_reason(self):
        self.song["file"] = "cdda:///1"
        self.live.step()
        a = self.store.analysis("local:Album|1")
        self.assertEqual(a["status"], "skipped")
        self.assertIn("cdda", a["error"])
        self.song["file"] = "Album/missing.flac"
        self.live.step()
        self.assertEqual(self.store.analysis("local:Album|missing.flac")["status"], "skipped")

    def test_long_tracks_and_switched_off_do_nothing(self):
        self.song["duration"] = 3 * 3600.0
        self.live.step()
        self.assertEqual(self.store.analysis("local:Album|01.flac")["status"], "skipped")
        self.assertIn("longer", self.live.state["message"])

    def test_a_tap_is_kept_at_the_moment_heard(self):
        self.live.step()
        self.song["elapsed"] = 12.9                    # 12.5 s heard, through the DRC delay
        tap = self.live.tap("forward", "pizz in the speaker")
        self.assertAlmostEqual(tap["pos"], 12.5)
        self.assertAlmostEqual(tap["delay"], 0.4)
        self.assertEqual(tap["context"], "solo")
        self.assertEqual(tap["version"], "9.9")
        self.assertEqual(tap["note"], "pizz in the speaker")
        self.assertEqual(tap["session"], self.session["id"])
        with self.assertRaises(ValueError):
            self.live.tap("loud")
        self.song["state"] = "stop"
        self.song["file"] = ""
        with self.assertRaises(LookupError):
            self.live.tap("back")

    def test_the_view_sends_the_analysis_only_when_the_page_lacks_it(self):
        self.live.step()
        self.live.tap("back")
        view = self.live.now_view()
        self.assertEqual(view["track"]["id"], "local:Album|01.flac")
        self.assertAlmostEqual(view["track"]["pos"], 99.6)
        self.assertIn("result", view["analysis"])
        self.assertEqual(len(view["taps"]), 1)
        again = self.live.now_view(view["analysis"]["stamp"])
        self.assertNotIn("result", again["analysis"])

    def test_export_keeps_timelines_only_for_tapped_tracks(self):
        self.live.step()
        self.song["file"] = "Album/02.flac"
        shutil.copy(os.path.join(self.music, "Album", "01.flac"), os.path.join(self.music, "Album", "02.flac"))
        self.live.step()
        tap = self.live.tap("between")
        self.store.set_note(tap["id"], "basses and cellos")
        rows = self.store.export_rows()
        self.assertEqual(rows[0]["session"]["name"], "Nelsons")
        bass = {r["bass"]["track_id"]: r["bass"] for r in rows if "bass" in r}
        self.assertNotIn("blocks", bass["local:Album|01.flac"]["result"])
        self.assertIn("blocks", bass["local:Album|02.flac"]["result"])
        self.assertEqual([r["tap"]["note"] for r in rows if "tap" in r], ["basses and cellos"])
        self.assertEqual(rows, self.store.export_rows())
        self.assertTrue(self.store.delete_tap(tap["id"]))
        self.assertEqual(self.store.taps(), [])


@unittest.skipUnless(shutil.which("git"), "git not installed")
class SharedThroughTheDrLog(unittest.TestCase):
    def test_taps_and_analyses_go_to_their_own_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "shared.git")
            subprocess.run(["git", "init", "--quiet", "--bare", repo], check=True)
            dr = DrStore(os.path.join(tmp, "dr.sqlite"), dr_volumes.Volumes(create=False))
            bass = bass_live.BassStore(os.path.join(tmp, "bass.sqlite"))
            bass.start_session("test")
            bass.save_analysis("q:1|2", "q:1", "2", {"title": "Largo"}, "1.2", "done", {"depth": None})
            bass.add_tap("q:1|2", "q:1", "2", {"title": "Largo"}, 30.0, 30.5, 0.5, "back", "", "", "1.2")
            sync = GitSync(dr, repo, "arki", os.path.join(tmp, "sync"),
                           extras={"bass": bass.export_rows})
            state = sync.run_once()
            self.assertTrue(state["ok"], state)
            check = os.path.join(tmp, "check")
            subprocess.run(["git", "clone", "--quiet", "--branch", "main", repo, check], check=True)
            with open(os.path.join(check, "bass", "arki.jsonl")) as f:
                rows = [json.loads(line) for line in f]
            self.assertEqual([next(iter(r)) for r in rows], ["session", "bass", "tap"])
            self.assertTrue(os.path.exists(os.path.join(check, "boxes", "arki.jsonl")))


SPEC = importlib.util.spec_from_file_location("omdrc_bass_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = bass_live.BassStore(os.path.join(self.tmp.name, "bass.sqlite"))
        self.song = {"file": "http://box/qobuz/track/7", "state": "play", "title": "Largo",
                     "elapsed": 61.0, "duration": 600.0}
        self.live = bass_live.BassLive(
            self.store, "unused", now=lambda: dict(self.song),
            identify=lambda s: {"key": "qobuz:9", "source": "qobuz", "ref": "9",
                                "meta": {"title": "Sym 2"}, "track_key": "7", "number": 1},
            local_path=lambda f: None, delay=lambda: 1.0, download=None,
            workdir=self.tmp.name, version="1.2")
        self.patches = [patch.object(APP, "_BASS", self.live), patch.object(APP, "_BASS_STORE", self.store)]
        for p in self.patches:
            p.start()
        self.client = APP.app.test_client()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_tap_note_delete(self):
        self.assertEqual(self.client.post("/bass/tap", json={"verdict": "back"}).status_code, 409)
        self.assertTrue(self.client.post("/bass/session", json={"action": "start", "name": "x"}).get_json()["ok"])
        d = self.client.post("/bass/tap", json={"verdict": "back"}).get_json()
        self.assertTrue(d["ok"], d)
        self.assertAlmostEqual(d["tap"]["pos"], 60.0)
        tid = d["tap"]["id"]
        self.assertTrue(self.client.post(f"/bass/tap/{tid}", json={"note": "deep"}).get_json()["ok"])
        view = self.client.get("/bass").get_json()
        self.assertEqual(view["taps"][0]["note"], "deep")
        self.assertEqual(view["track"]["title"], "Largo")
        self.assertEqual(self.client.post("/bass/tap", json={"verdict": "x"}).status_code, 400)
        self.assertTrue(self.client.post(f"/bass/tap/{tid}/delete").get_json()["ok"])
        self.assertEqual(self.client.post(f"/bass/tap/{tid}/delete").status_code, 404)

    def test_sessions(self):
        self.assertEqual(self.client.post("/bass/session", json={"action": "pause"}).status_code, 400)
        d = self.client.post("/bass/session", json={"action": "start", "name": "Altinoglu 8"}).get_json()
        self.assertEqual(d["session"]["name"], "Altinoglu 8")
        self.assertEqual(self.client.get("/bass").get_json()["session"]["name"], "Altinoglu 8")
        self.assertTrue(self.client.post("/bass/session", json={"action": "stop"}).get_json()["stopped"])
        self.assertIsNone(self.client.get("/bass").get_json()["session"])

    def test_the_shipped_config_loads_with_its_bass_section(self):
        # [bass_depth] once took the panel down: an unreserved section is read
        # as a command and must have a 'what'.
        import re
        text = (SRC / "commands.conf.in").read_text(encoding="utf-8")
        text = re.sub(r"@[A-Z_]+@", "", text)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "commands.conf")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace("save_dir = /media/USBHD2/musica/bass-analysis", f"save_dir = {tmp}"))
            saved = (APP.COMMANDS, APP.CMD_MAP, APP.BASS_SAVE_DIR, APP._BASS.save_dir)
            try:
                APP.load_config(path)
                self.assertEqual(APP.BASS_SAVE_DIR, tmp)
                self.assertEqual(self.live.save_dir, tmp)
                self.assertNotIn("bass_depth", APP.CMD_MAP)
            finally:
                APP.COMMANDS, APP.CMD_MAP, APP.BASS_SAVE_DIR, _ = saved

    def test_pages_carry_the_card(self):
        with patch.object(APP, "_resolve_mpd_port", return_value=None):
            index = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="bass-depth-root"', index)
        self.assertIn("widgets/bass-depth.js", index)
        shell = self.client.get("/k/").get_data(as_text=True)
        self.assertIn("pages/bass.js", shell)
        self.assertEqual(self.client.get("/k/static/widgets/bass-depth.js").status_code, 200)
        self.assertEqual(self.client.get("/k/static/pages/bass.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()
