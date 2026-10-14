"""Bass depth while listening, in tracking sessions the listener starts and
stops: every track played during a session is analysed in the background
(tools/bass_depth.py) and kept on the music drive, and the listener's
feedback -- "the basses sound forward / in between / back here" -- is kept
against the moment heard.  Outside a session nothing is fetched or analysed.

The analysis reads the track's source again, not the audio on its way to the
DAC: a local file directly, a streamed track fetched the way the DR Measure
job does.  A session's tracks are kept under `save_dir` ([bass_depth] in
commands.conf; /media/USBHD2/musica/bass-analysis by default), as
<artist>/<album (year)>/<NN - title>.<ext>, so the drive can be carried to
another box and the same recordings studied there.  A streamed track is
fetched straight into that folder; a local track is copied there unless it
is already on the same drive, which travels with it anyway.
When the folder is missing or not on a mounted drive -- an unmounted drive
leaves an empty mount point on the system disk, which must never fill up --
tracks are still analysed, from a work directory, but not kept.

One track at a time, at the lowest CPU priority.  Results are kept per track
and per detector version: a newer detector analyses a track again the next
time it plays in a session.

A tap stores the playback position the listener heard (MPD's elapsed time
minus the DRC chain's delay), the verdict, an optional note, and the passage
of the timeline under it.  Sessions, taps and analyses are shared through the
DR log's git repository (dr_sync.py, folder `bass/`), where calibration
(tools/bass_depth_calibrate.py) reads them.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

DB_FILE = "bass-depth.sqlite"
VERDICTS = ("forward", "between", "back")
MAX_NOTE = 2000
MAX_NAME = 200
# Longer tracks are skipped: the detector holds the whole track in memory
# (16 kHz stereo floats, ~120 MB per 30 minutes) plus its spectra.
MAX_SECONDS = 45 * 60
POLL_S = 4.0
ANALYSE_TIMEOUT_S = 900

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '',
    started REAL NOT NULL, ended REAL);
CREATE TABLE IF NOT EXISTS analysis (
    track_id TEXT PRIMARY KEY, album_key TEXT NOT NULL, track_key TEXT NOT NULL,
    number INTEGER, title TEXT NOT NULL DEFAULT '', artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '', year INTEGER,
    genre TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
    at REAL NOT NULL, result TEXT, saved TEXT NOT NULL DEFAULT '', session INTEGER);
CREATE TABLE IF NOT EXISTS tap (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, track_id TEXT NOT NULL,
    album_key TEXT NOT NULL, track_key TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT '', album TEXT NOT NULL DEFAULT '',
    pos REAL NOT NULL, elapsed REAL, delay REAL, verdict TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', context TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '', session INTEGER);
CREATE INDEX IF NOT EXISTS tap_track ON tap (track_id);
"""

_META = ("number", "title", "artist", "album", "label", "year", "genre", "source")


def track_id(album_key: str, track_key: str) -> str:
    return f"{album_key}|{track_key}"


def block_at(result: dict | None, pos: float) -> dict | None:
    """The passage under a position: the longest one containing it, else the
    latest one ending less than 3 s before it (a tap comes after the sound)."""
    blocks = (result or {}).get("blocks", [])
    inside = [b for b in blocks if b["start"] <= pos <= b["end"]]
    if inside:
        return max(inside, key=lambda b: b.get("seconds", 0))
    before = [b for b in blocks if 0 <= pos - b["end"] < 3.0]
    return max(before, key=lambda b: b["end"]) if before else None


def compact(raw: dict) -> dict:
    """What is kept of bass_depth.py's JSON for one file."""
    keep = ("version", "duration_s", "bass_seconds", "pitch_variety", "verdict",
            "verdict_text", "depth", "blocks")
    out = {k: raw[k] for k in keep if k in raw}
    out["contexts"] = {name: {k: v for k, v in c.items() if k != "passages"}
                       for name, c in (raw.get("contexts") or {}).items()}
    return out


# ── where a session's tracks are kept ────────────────────────────────────────

def _component(text: str, fallback: str) -> str:
    """One path component that FAT/NTFS/ext4 all accept."""
    text = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", text or "").strip().rstrip(". ")
    return (text or fallback)[:120]


def mount_of(path: str) -> str:
    """The mount point `path` lies on ("/" for the system disk)."""
    p = os.path.realpath(path)
    while p not in ("/", ""):
        if os.path.ismount(p):
            return p
        p = os.path.dirname(p)
    return "/"


def on_mounted_drive(path: str) -> bool:
    """True when `path` lies below a mount point other than /: below an
    unmounted drive's empty mount point it would be on the system disk."""
    return mount_of(path) != "/"


def kept_name(root: str, meta: dict) -> str:
    """<root>/<artist>/<album (year)>/<NN - title>, without an extension."""
    album = meta.get("album") or "Unknown album"
    if meta.get("year"):
        album = f"{album} ({meta['year']})"
    title = meta.get("title") or "Untitled"
    number = meta.get("number")
    stem = f"{number:02d} - {title}" if isinstance(number, int) else title
    return os.path.join(root, _component(meta.get("artist") or "", "Unknown artist"),
                        _component(album, "Unknown album"), _component(stem, "Untitled"))


def sniff_ext(path: str) -> str:
    """The extension a fetched file deserves, from its first bytes."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return ".bin"
    if head.startswith(b"fLaC"):
        return ".flac"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head.startswith(b"RIFF"):
        return ".wav"
    if head.startswith(b"OggS"):
        return ".ogg"
    return ".bin"


class BassStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        if not self._ready:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.executescript(SCHEMA)
            self._ready = True
        return db

    # -- sessions --

    def active_session(self) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM session WHERE ended IS NULL ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                return None
            s = dict(row)
            s["tracks"] = db.execute("SELECT COUNT(*) FROM analysis WHERE session = ?", (s["id"],)).fetchone()[0]
            s["taps"] = db.execute("SELECT COUNT(*) FROM tap WHERE session = ?", (s["id"],)).fetchone()[0]
        return s

    def start_session(self, name: str) -> dict:
        with self._lock, self._connect() as db:
            db.execute("UPDATE session SET ended = ? WHERE ended IS NULL", (time.time(),))
            db.execute("INSERT INTO session (name, started) VALUES (?, ?)", (name[:MAX_NAME], time.time()))
        return self.active_session()

    def stop_session(self) -> bool:
        with self._lock, self._connect() as db:
            return db.execute("UPDATE session SET ended = ? WHERE ended IS NULL", (time.time(),)).rowcount > 0

    # -- analyses --

    def analysis(self, tid: str) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM analysis WHERE track_id = ?", (tid,)).fetchone()
        if not row:
            return None
        a = dict(row)
        a["result"] = json.loads(a["result"]) if a["result"] else None
        return a

    def save_analysis(self, tid: str, album_key: str, track_key: str, meta: dict,
                      version: str, status: str, result: dict | None = None,
                      error: str = "", saved: str = "", session: int | None = None) -> None:
        meta = {k: meta.get(k) for k in _META}
        for k in ("title", "artist", "album", "label", "genre", "source"):
            meta[k] = str(meta[k] or "")
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO analysis (track_id, album_key, track_key, number, title, "
                "artist, album, label, year, genre, source, version, status, error, at, result, "
                "saved, session) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, album_key, track_key, meta["number"], meta["title"], meta["artist"],
                 meta["album"], meta["label"], meta["year"], meta["genre"], meta["source"],
                 version, status, error[:300], time.time(),
                 json.dumps(result, separators=(",", ":")) if result is not None else None,
                 saved, session))

    def list_analyses(self) -> list[dict]:
        """Every finished analysis, newest first, with its score and the
        number of taps on it: the card's list of tracks to look at again."""
        with self._lock, self._connect() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT track_id, title, artist, album, label, year, version, at, session, result "
                "FROM analysis WHERE status = 'done' ORDER BY at DESC")]
            taps = dict(db.execute("SELECT track_id, COUNT(*) FROM tap GROUP BY track_id").fetchall())
        for r in rows:
            res = json.loads(r.pop("result") or "null") or {}
            r["depth"] = res.get("depth")
            r["duration"] = res.get("duration_s")
            r["taps"] = taps.get(r["track_id"], 0)
        return rows

    def recent_analyses(self, limit: int = 30) -> list[dict]:
        with self._lock, self._connect() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT track_id, title, artist, album, version, status, error, at, saved, "
                "session, result FROM analysis ORDER BY at DESC LIMIT ?", (limit,))]
        for r in rows:
            res = json.loads(r.pop("result")) if r.get("result") else {}
            r["depth"] = res.get("depth")
        return rows

    # -- taps --

    def add_tap(self, tid: str, album_key: str, track_key: str, meta: dict, pos: float,
                elapsed: float | None, delay: float | None, verdict: str, note: str,
                context: str, version: str, session: int | None = None) -> dict:
        if verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
        with self._lock, self._connect() as db:
            cur = db.execute(
                "INSERT INTO tap (at, track_id, album_key, track_key, title, artist, album, pos, "
                "elapsed, delay, verdict, note, context, version, session) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), tid, album_key, track_key, str(meta.get("title") or ""),
                 str(meta.get("artist") or ""), str(meta.get("album") or ""), float(pos),
                 elapsed, delay, verdict, note[:MAX_NOTE], context, version, session))
            row = db.execute("SELECT * FROM tap WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def set_note(self, tap_id: int, note: str) -> bool:
        with self._lock, self._connect() as db:
            return db.execute("UPDATE tap SET note = ? WHERE id = ?",
                              (note[:MAX_NOTE], tap_id)).rowcount > 0

    def delete_tap(self, tap_id: int) -> bool:
        with self._lock, self._connect() as db:
            return db.execute("DELETE FROM tap WHERE id = ?", (tap_id,)).rowcount > 0

    def taps(self, tid: str | None = None, limit: int = 200) -> list[dict]:
        with self._lock, self._connect() as db:
            if tid is not None:
                rows = db.execute("SELECT * FROM tap WHERE track_id = ? ORDER BY pos", (tid,))
            else:
                rows = db.execute("SELECT * FROM tap ORDER BY at DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._lock, self._connect() as db:
            return {"analyses": db.execute("SELECT COUNT(*) FROM analysis WHERE status = 'done'").fetchone()[0],
                    "taps": db.execute("SELECT COUNT(*) FROM tap").fetchone()[0],
                    "sessions": db.execute("SELECT COUNT(*) FROM session").fetchone()[0]}

    # -- sharing (dr_sync.py) --

    def export_rows(self) -> list[dict]:
        """Every session, tap and finished analysis, timelines included:
        analyses only happen in sessions, on the recordings being studied,
        and the other boxes show them too.  Deterministic, so an unchanged
        store exports the same file."""
        with self._lock, self._connect() as db:
            sessions = [dict(r) for r in db.execute("SELECT * FROM session ORDER BY id")]
            taps = [dict(r) for r in db.execute("SELECT * FROM tap ORDER BY id")]
            analyses = [dict(r) for r in db.execute(
                "SELECT * FROM analysis WHERE status = 'done' ORDER BY track_id")]
        rows = [{"session": s} for s in sessions]
        for a in analyses:
            a["result"] = json.loads(a.pop("result") or "null") or {}
            rows.append({"bass": a})
        rows += [{"tap": t} for t in taps]
        return rows


class BassLive:
    """The background worker and the state the pages show."""

    def __init__(self, store: BassStore, script: str, *, now, identify, local_path,
                 delay, download, workdir: str, version: str, save_dir: str = "",
                 min_free: int = 1 << 30, python: str = sys.executable) -> None:
        self.store = store
        self.script = script
        self.now = now                  # () -> MPD currentsong + status dict
        self.identify = identify        # song -> {key, track_key, meta...} or None
        self.local_path = local_path    # MPD file -> absolute path or None
        self.delay = delay              # () -> seconds the DRC chain holds the sound
        self.download = download        # (url, dest, progress, cancelled) -> None
        self.workdir = workdir
        self.version = version
        self.save_dir = save_dir
        # the DR log sync's clone: other boxes' bass/<box>.jsonl, read-only
        self.shared_dir = ""
        self.box = ""
        self._shared_cache: dict[str, tuple[float, dict]] = {}
        self.min_free = min_free
        self.python = python
        self.lock = threading.Lock()
        self.state = {"status": "off", "track_id": "", "message": "", "at": 0.0}
        self._thread: threading.Thread | None = None
        self._last_file = ""
        self._ident: dict = {}          # MPD file -> identity, for the track playing
        self._force: set[str] = set()
        self._run_lock = threading.Lock()   # one detector at a time, live or on request

    # -- sessions --

    def start_session(self, name: str) -> dict:
        self._last_file = ""            # the track playing counts from now
        return self.store.start_session(name)

    def stop_session(self) -> bool:
        return self.store.stop_session()

    def save_status(self) -> dict:
        """Where a session's tracks go, and whether they can go there now."""
        d = self.save_dir
        if not d:
            return {"dir": "", "ok": False, "why": "no save_dir in [bass_depth]"}
        if not os.path.isdir(d):
            return {"dir": d, "ok": False, "why": "the folder is missing: is the drive mounted?"}
        if not on_mounted_drive(d):
            return {"dir": d, "ok": False, "why": "the folder is not on a mounted drive"}
        if not os.access(d, os.W_OK):
            return {"dir": d, "ok": False, "why": "the folder is not writable"}
        return {"dir": d, "ok": True, "why": ""}

    # -- identity of the track playing --

    def identity(self, song: dict) -> dict | None:
        file = song.get("file") or ""
        if not file:
            return None
        if file in self._ident:
            return self._ident[file]
        album = self.identify(song)
        if album is None:
            ident = None
        else:
            meta = dict(album.get("meta") or {})
            ident = {"track_id": track_id(album["key"], str(album["track_key"])),
                     "album_key": album["key"], "track_key": str(album["track_key"]),
                     "meta": {"number": album.get("number"),
                              "title": song.get("title") or "",
                              "artist": meta.get("artist") or song.get("album_artist")
                              or song.get("artist") or "",
                              "album": meta.get("title") or song.get("album") or "",
                              "label": meta.get("label") or "", "year": meta.get("year"),
                              "genre": meta.get("genre") or "", "source": album.get("source") or ""}}
        self._ident = {file: ident}     # only the track playing is worth keeping
        return ident

    def _set(self, **changes) -> None:
        with self.lock:
            self.state.update(changes, at=time.time())

    # -- the worker --

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="bass-depth", daemon=True)
            self._thread.start()

    def reanalyse(self, tid: str) -> None:
        self._force.add(tid)
        self._last_file = ""

    def _loop(self) -> None:
        while True:
            try:
                self.step()
            except Exception as error:                  # noqa: BLE001 - never let the thread die
                self._set(status="error", message=str(error)[:300])
            time.sleep(POLL_S)

    def step(self) -> None:
        """One look at what is playing; analyses it when it is new."""
        session = self.store.active_session()
        if session is None:
            self._set(status="off", message="")
            self._last_file = ""
            return
        song = self.now()
        file = song.get("file") or ""
        if song.get("state") not in ("play", "pause") or not file:
            if self.state["status"] not in ("done", "error", "skipped"):
                self._set(status="idle", message="nothing is playing")
            return
        if file == self._last_file:
            return
        self._last_file = file
        ident = self.identity(song)
        if ident is None:
            self._set(status="skipped", track_id="", message="this track belongs to no album the box can name")
            return
        tid = ident["track_id"]
        old = self.store.analysis(tid)
        if (old and old["version"] == self.version and old["status"] in ("done", "skipped")
                and tid not in self._force and (old["saved"] or not self.save_status()["ok"])):
            self._set(status=old["status"], track_id=tid, message=old["error"])
            return
        self._force.discard(tid)
        self.analyse(file, song, ident, session["id"])

    def analyse(self, file: str, song: dict, ident: dict, session: int | None = None) -> None:
        tid = ident["track_id"]
        saved = ""

        def save(status, result=None, error=""):
            self.store.save_analysis(tid, ident["album_key"], ident["track_key"], ident["meta"],
                                     self.version, status, result, error, saved, session)

        def skip(msg):
            save("skipped", error=msg)
            self._set(status="skipped", track_id=tid, message=msg)

        duration = song.get("duration")
        if duration and duration > MAX_SECONDS:
            return skip(f"longer than {MAX_SECONDS // 60} minutes: not analysed")
        scheme = urlsplit(file).scheme if "://" in file else ""
        keep = self.save_status()
        tmp = None
        try:
            if scheme in ("http", "https"):
                path = self._existing_copy(ident) if keep["ok"] else None
                if path is None:
                    folder = keep["dir"] if keep["ok"] else self.workdir
                    if shutil.disk_usage(folder).free < self.min_free:
                        raise RuntimeError(f"not enough free space in {folder}")
                    if keep["ok"]:
                        stem = kept_name(keep["dir"], ident["meta"])
                        os.makedirs(os.path.dirname(stem), exist_ok=True)
                    else:
                        tmp = tempfile.mkdtemp(prefix="omdrc-bass-", dir=self.workdir)
                        stem = os.path.join(tmp, "track")
                    part = stem + ".part"
                    self._set(status="fetching", track_id=tid, message="fetching the track")
                    try:
                        self.download(file, part, lambda f: self._set(message=f"fetching the track: {f:.0%}"),
                                      lambda: False)
                        path = stem + sniff_ext(part)
                        os.replace(part, path)
                    finally:
                        if os.path.exists(part):
                            os.remove(part)
                if keep["ok"]:
                    saved = path
            elif scheme:
                return skip(f"{scheme}:// sources cannot be read again")
            else:
                path = self.local_path(file)
                if not path or not os.path.isfile(path):
                    return skip("the file is not readable here (a cue sheet track, or the drive is away)")
                if keep["ok"]:
                    saved = self._keep_local(path, ident, keep["dir"])
            self._set(status="analysing", track_id=tid, message="analysing")
            started = time.time()
            result = self.run_detector(path)
            save("done", compact(result))
            self._set(status="done", track_id=tid,
                      message=f"analysed in {time.time() - started:.0f} s"
                      + ("" if saved else " (not kept: " + (keep["why"] or "no folder") + ")"))
        except Exception as error:                      # noqa: BLE001 - shown on the page
            save("error", error=str(error))
            self._set(status="error", track_id=tid, message=str(error)[:300])
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    def _existing_copy(self, ident: dict) -> str | None:
        """A copy kept by an earlier session, so it is not fetched twice."""
        stem = kept_name(self.save_dir, ident["meta"])
        found = [p for p in glob.glob(glob.escape(stem) + ".*") if not p.endswith(".part")]
        return found[0] if found else None

    def _keep_local(self, path: str, ident: dict, root: str) -> str:
        """A local track on the drive: where it already is, if it is on the
        same drive as the kept folder (it travels with it); otherwise a copy
        in the kept folder (from an earlier session, if one is there)."""
        if mount_of(path) == mount_of(root):
            return path
        dest = kept_name(root, ident["meta"]) + os.path.splitext(path)[1].lower()
        if os.path.exists(dest) and os.path.getsize(dest) == os.path.getsize(path):
            return dest
        if shutil.disk_usage(root).free < self.min_free + os.path.getsize(path):
            raise RuntimeError(f"not enough free space in {root}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        self._set(status="fetching", track_id=ident["track_id"], message="copying the track to the drive")
        shutil.copyfile(path, dest + ".part")
        os.replace(dest + ".part", dest)
        return dest

    def reanalyse_kept(self, tid: str) -> None:
        """Analyse a track again from its copy kept on the drive, without
        playing it (in a thread; the card polls the track for the result)."""
        a = self.store.analysis(tid)
        if not a:
            raise LookupError("no such analysis")
        if not a["saved"] or not os.path.isfile(a["saved"]):
            raise LookupError("the kept copy is not here (is the drive mounted?)")

        def run():
            meta = {k: a.get(k) for k in _META}
            try:
                result = self.run_detector(a["saved"])
                self.store.save_analysis(tid, a["album_key"], a["track_key"], meta, self.version,
                                         "done", compact(result), "", a["saved"], a["session"])
            except Exception as error:                  # noqa: BLE001 - the old analysis stays
                self._set(status="error", track_id=tid, message=str(error)[:300])

        threading.Thread(target=run, name="bass-reanalyse", daemon=True).start()

    def run_detector(self, path: str) -> dict:
        """bass_depth.py on one file, in a subprocess at the lowest CPU
        priority: it shares the box with the DRC convolution.  One at a time."""
        with self._run_lock:
            return self._run_detector(path)

    def _run_detector(self, path: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="omdrc-bass-json-") as tmp:
            out = os.path.join(tmp, "result.json")
            done = subprocess.run(["nice", "-n", "19", self.python, self.script, "--json", out, path],
                                  capture_output=True, text=True, timeout=ANALYSE_TIMEOUT_S)
            try:
                with open(out, encoding="utf-8") as f:
                    results = json.load(f)
            except (OSError, ValueError):
                results = []
        if not results:
            lines = (done.stderr or "the detector gave no result").strip().splitlines()
            raise RuntimeError((lines[-1] if lines else "the detector failed")[:300])
        return results[0]

    # -- analyses to look at again, this box's and the other boxes' --

    def _shared(self) -> dict[str, dict]:
        """{box: {"analyses": {track id: row}, "taps": [rows]}} from the other
        boxes' files in the sync clone, re-read when a file changes."""
        out = {}
        folder = self.shared_dir
        for name in sorted(os.listdir(folder)) if folder and os.path.isdir(folder) else []:
            box, ext = os.path.splitext(name)
            if ext != ".jsonl" or box == self.box:
                continue
            path = os.path.join(folder, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            cached = self._shared_cache.get(path)
            if cached and cached[0] == mtime:
                out[box] = cached[1]
                continue
            data = {"analyses": {}, "taps": []}
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row.get("bass"), dict) and row["bass"].get("track_id"):
                        data["analyses"][row["bass"]["track_id"]] = row["bass"]
                    elif isinstance(row.get("tap"), dict):
                        data["taps"].append(row["tap"])
            self._shared_cache[path] = (mtime, data)
            out[box] = data
        return out

    def catalog(self, q: str = "", limit: int = 300) -> list[dict]:
        """Analysed tracks, newest first: this box's (origin "") and those
        the other boxes share; `q` filters on title, artist and album."""
        rows = [dict(r, origin="") for r in self.store.list_analyses()]
        for box, data in self._shared().items():
            counts: dict[str, int] = {}
            for t in data["taps"]:
                counts[t.get("track_id")] = counts.get(t.get("track_id"), 0) + 1
            for tid, a in data["analyses"].items():
                res = a.get("result") or {}
                rows.append({"track_id": tid, "title": a.get("title", ""), "artist": a.get("artist", ""),
                             "album": a.get("album", ""), "label": a.get("label", ""), "year": a.get("year"),
                             "version": a.get("version", ""), "at": a.get("at", 0), "session": a.get("session"),
                             "depth": res.get("depth"), "duration": res.get("duration_s"),
                             "taps": counts.get(tid, 0), "origin": box,
                             "timeline": bool(res.get("blocks"))})
        words = [w.casefold() for w in q.split()]
        if words:
            rows = [r for r in rows if all(w in f"{r['title']} {r['artist']} {r['album']}".casefold() for w in words)]
        rows.sort(key=lambda r: -(r.get("at") or 0))
        return rows[:limit]

    def track_view(self, tid: str, origin: str = "") -> dict | None:
        """One analysed track as the card draws it, without playback."""
        if origin:
            data = self._shared().get(origin)
            a = data and data["analyses"].get(tid)
            if not a:
                return None
            taps = sorted((t for t in data["taps"] if t.get("track_id") == tid), key=lambda t: t.get("pos", 0))
            result = a.get("result")
        else:
            a = self.store.analysis(tid)
            if not a or a["status"] != "done":
                return None
            taps = self.store.taps(tid)
            result = a["result"]
        return {"track": {"id": tid, "title": a.get("title", ""), "artist": a.get("artist", ""),
                          "album": a.get("album", ""), "duration": (result or {}).get("duration_s"),
                          "origin": origin, "at": a.get("at"), "saved": a.get("saved", "")},
                "analysis": {"status": "done", "version": a.get("version", ""), "result": result,
                             "current": a.get("version") == self.version},
                "taps": taps}

    # -- what the pages ask --

    def heard_position(self, song: dict) -> tuple[float | None, float]:
        """Where in the track the listener is now: MPD's position minus what
        the DRC chain still holds."""
        try:
            delay = max(0.0, float(self.delay() or 0.0))
        except Exception:                               # noqa: BLE001
            delay = 0.0
        elapsed = song.get("elapsed")
        if elapsed is None:
            return None, delay
        return max(0.0, float(elapsed) - delay), delay

    def tap(self, verdict: str, note: str = "") -> dict:
        session = self.store.active_session()
        if session is None:
            raise LookupError("start a tracking session first")
        song = self.now()
        ident = self.identity(song) if song.get("file") else None
        if ident is None:
            raise LookupError("nothing identifiable is playing")
        pos, delay = self.heard_position(song)
        if pos is None:
            raise LookupError("MPD does not report a position")
        analysis = self.store.analysis(ident["track_id"])
        result = analysis["result"] if analysis and analysis["status"] == "done" else None
        block = block_at(result, pos)
        return self.store.add_tap(ident["track_id"], ident["album_key"], ident["track_key"],
                                  ident["meta"], pos, song.get("elapsed"), delay, verdict, note,
                                  block["context"] if block else "", (result or {}).get("version", ""),
                                  session["id"])

    def now_view(self, have: str = "") -> dict:
        """The card's state.  The analysis itself only when the page does not
        hold this version of it yet (`have` = "<track id>@<at>")."""
        song = self.now()
        playing = song.get("state") in ("play", "pause") and bool(song.get("file"))
        ident = self.identity(song) if playing else None
        with self.lock:
            state = dict(self.state)
        view = {"session": self.store.active_session(), "save": self.save_status(),
                "state": state, "playing": playing, "track": None, "analysis": None,
                "taps": [], "stats": self.store.stats()}
        if ident is None:
            return view
        pos, delay = self.heard_position(song)
        view["track"] = {"id": ident["track_id"], **ident["meta"], "state": song.get("state"),
                         "elapsed": song.get("elapsed"), "duration": song.get("duration"),
                         "pos": pos, "delay": delay}
        a = self.store.analysis(ident["track_id"])
        if a:
            stamp = f"{a['track_id']}@{a['at']:.3f}"
            view["analysis"] = {"status": a["status"], "version": a["version"], "error": a["error"],
                                "saved": a["saved"], "stamp": stamp, "current": a["version"] == self.version}
            if stamp != have:
                view["analysis"]["result"] = a["result"]
        view["taps"] = self.store.taps(ident["track_id"])
        return view
