"""Album dynamic range remembered across listening, for ranking and for search.

Three things feed it, and it keeps track of which said what:

  - the local collection's dr14.txt reports, the album value they close with
    (and the per-track rows when the report is our own drmeter's);
  - the live meter (the analyzer's DR blocks), one row per track heard, kept
    when the track ends -- heard whole, or only in part;
  - the Measure job, which fetches a record again and measures every file.

Tracks are stored, never an album number: the album figure is worked out when
it is read, the way the reference does it (the mean of the tracks' integer
DRs, rounded).  It is *exact* when a report says so or when every track of the
record has a complete measurement, which may have been gathered over several
evenings; it is an *estimate* when only part of the record was heard, provided
there is enough of it to mean something.

The rolling session figure the DR page shows over the last minutes is never
stored: it is computed over blocks of several tracks at once, which is not how
an album's DR is defined, and it would not compare with a dr14.txt value.

Albums are keyed by where they came from: `qobuz:<album id>`,
`local:<folder relative to the music directory>`, and, for anything else MPD
plays that carries an album tag, `tags:<artist>\\x1f<album>` (never exact: the
number of tracks on such a record is not known).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time

from drmeter import AUDIO_SUFFIXES, album_dr

DB_FILE = "dr-albums.sqlite"
REPORT = "dr14.txt"

# A track heard for less than this is not stored at all: a skip, an intro.
MIN_TRACK_SECONDS = 30.0
# Less than this, over all of an album's tracks, gives no estimate: a handful
# of 3-second blocks makes the second-highest peak and the loudest-20 % mean
# unreliable.
MIN_ESTIMATE_SECONDS = 120.0
# A track counts as heard whole when it was heard from (near) its start, never
# sought, and the blocks cover its length but for this much: the partial last
# block, and the half-second the boundary is polled at.
START_TOLERANCE = 5.0
END_TOLERANCE = 9.0
# An elapsed time this far from where wall-clock time says it should be is a
# seek (or a restart of the track), not jitter.
SEEK_TOLERANCE = 3.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS album (
    key           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    ref           TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    artist        TEXT NOT NULL DEFAULT '',
    year          INTEGER,
    label         TEXT NOT NULL DEFAULT '',
    genre         TEXT NOT NULL DEFAULT '',
    image         TEXT NOT NULL DEFAULT '',
    track_count   INTEGER,
    report_dr     INTEGER,
    report_tracks INTEGER,
    report_mtime  REAL,
    updated       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS track (
    album_key  TEXT NOT NULL,
    track_key  TEXT NOT NULL,
    number     INTEGER,
    title      TEXT NOT NULL DEFAULT '',
    dr         INTEGER NOT NULL,
    dr_exact   REAL NOT NULL,
    seconds    REAL NOT NULL,
    duration   REAL,
    complete   INTEGER NOT NULL,
    method     TEXT NOT NULL,
    at         REAL NOT NULL,
    PRIMARY KEY (album_key, track_key)
);
CREATE INDEX IF NOT EXISTS track_album ON track (album_key);
"""

_META = ("title", "artist", "year", "label", "genre", "image", "track_count")


def tags_key(artist: str, album: str) -> str:
    return "tags:" + " ".join(artist.split()).casefold() + "\x1f" + " ".join(album.split()).casefold()


def summary(album: dict, tracks: list[dict]) -> dict:
    """The album's figure from its row and its stored tracks.

    {"dr": int | None, "kind": "exact" | "estimate" | None,
     "basis": "report" | "measured" | "listened" | "", "heard": tracks stored,
     "complete": complete tracks, "track_count", "seconds": seconds measured,
     "dr_mean": unrounded mean of the tracks, to order equal values by}"""
    heard = len(tracks)
    complete = [t for t in tracks if t["complete"]]
    seconds = sum(t["seconds"] for t in tracks)
    count = album.get("track_count")
    out = {"dr": None, "kind": None, "basis": "", "heard": heard,
           "complete": len(complete), "track_count": count,
           "seconds": round(seconds), "dr_mean": None}
    if album.get("report_dr") is not None:
        out.update(dr=album["report_dr"], kind="exact", basis="report",
                   dr_mean=float(album["report_dr"]))
        if tracks:
            out["dr_mean"] = sum(t["dr_exact"] for t in tracks) / heard
        return out
    if count and len(complete) >= count:
        chosen = complete
        out.update(kind="exact", basis="measured"
                   if all(t["method"] == "measured" for t in chosen) else "listened")
    elif tracks and seconds >= MIN_ESTIMATE_SECONDS:
        chosen = tracks
        out.update(kind="estimate", basis="listened"
                   if any(t["method"] == "live" for t in chosen) else "measured")
    else:
        return out
    out["dr"] = album_dr([t["dr"] for t in chosen])
    out["dr_mean"] = sum(t["dr_exact"] for t in chosen) / len(chosen)
    return out


# ── dr14.txt ─────────────────────────────────────────────────────────────────

_ALBUM_RE = re.compile(r"(?:Official DR value:\s*DR|^\s*DR\s*=\s*)(\d+)", re.I | re.M)
_TRACKS_RE = re.compile(r"Number of tracks:\s*(\d+)", re.I)


def parse_report(text: str) -> dict | None:
    """The album value of a dr14.txt (ours, dr14_tmeter's or foobar2000's) and
    its track count when it states one."""
    values = _ALBUM_RE.findall(text)
    if not values:
        return None
    tracks = _TRACKS_RE.search(text)
    return {"dr": int(values[-1]), "tracks": int(tracks.group(1)) if tracks else None}


def audio_files(folder: str) -> int:
    try:
        return sum(1 for n in os.listdir(folder)
                   if n.lower().endswith(AUDIO_SUFFIXES)
                   and os.path.isfile(os.path.join(folder, n)))
    except OSError:
        return 0


# ── following one track through the analyzer ─────────────────────────────────

class TrackWatch:
    """What the analyzer knows of the track playing: where it was first heard,
    whether it was sought, how long it is.  Pure bookkeeping, fed MPD's status
    (currentsong + status, as _mpd_now_playing_via_protocol reads it) every
    half second; the blocks themselves stay in the RollingEstimate."""

    def __init__(self, song: dict, now: float, start_block: int) -> None:
        self.song = dict(song)
        self.start_block = start_block
        elapsed = song.get("elapsed")
        self.first_elapsed = elapsed if isinstance(elapsed, (int, float)) else None
        self.seeks: list[int] = []
        self._elapsed = self.first_elapsed
        self._at = now
        self._state = song.get("state", "")

    def observe(self, song: dict, now: float, block: int) -> bool:
        """Note one poll; true when it shows a seek since the previous one."""
        elapsed = song.get("elapsed")
        elapsed = elapsed if isinstance(elapsed, (int, float)) else None
        state = song.get("state", "")
        sought = False
        if (state == "play" and self._state == "play"
                and elapsed is not None and self._elapsed is not None):
            expected = self._elapsed + (now - self._at)
            if abs(elapsed - expected) > SEEK_TOLERANCE:
                sought = True
                self.seeks.append(block)
        if self.first_elapsed is None and elapsed is not None:
            self.first_elapsed = elapsed
        self._elapsed, self._at, self._state = elapsed, now, state
        for key in ("duration", "title", "album", "artist", "album_artist", "track_no"):
            if song.get(key) not in (None, ""):
                self.song[key] = song[key]
        return sought

    def result(self, dr_exact: float | None, blocks: int, block_seconds: float) -> dict | None:
        """The track's row once it has ended, or None if too little was heard."""
        seconds = blocks * block_seconds
        if dr_exact is None or seconds < MIN_TRACK_SECONDS:
            return None
        duration = self.song.get("duration")
        duration = float(duration) if isinstance(duration, (int, float)) and duration > 0 else None
        complete = bool(duration and not self.seeks
                        and self.first_elapsed is not None
                        and self.first_elapsed <= START_TOLERANCE
                        and seconds >= duration - END_TOLERANCE)
        return {"dr": int(round(dr_exact)), "dr_exact": round(dr_exact, 2),
                "seconds": round(seconds, 1), "duration": duration,
                "complete": complete}


# ── the store ────────────────────────────────────────────────────────────────

class DrStore:
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

    def upsert_album(self, key: str, source: str, ref: str = "", **meta) -> None:
        """Create the album or fill in what is known of it; an empty value
        never overwrites a known one."""
        fields = {k: v for k, v in meta.items() if k in _META and v not in (None, "")}
        with self._lock, self._connect() as db:
            db.execute("INSERT OR IGNORE INTO album (key, source, ref, updated) VALUES (?, ?, ?, ?)",
                       (key, source, ref, time.time()))
            if fields:
                sets = ", ".join(f"{k} = ?" for k in fields)
                db.execute(f"UPDATE album SET {sets}, updated = ? WHERE key = ?",
                           (*fields.values(), time.time(), key))

    def record_track(self, album_key: str, track_key: str, *, dr: int, dr_exact: float,
                     seconds: float, complete: bool, method: str,
                     duration: float | None = None, number: int | None = None,
                     title: str = "") -> bool:
        """Keep a track's measurement unless a better one is stored: complete
        beats partial, a measured file beats a live hearing, more seconds beat
        fewer, and a newer one wins a tie.  True when it was kept."""
        rank = (bool(complete), method == "measured", seconds)
        with self._lock, self._connect() as db:
            old = db.execute("SELECT complete, method, seconds FROM track "
                             "WHERE album_key = ? AND track_key = ?",
                             (album_key, track_key)).fetchone()
            if old and (bool(old["complete"]), old["method"] == "measured",
                        old["seconds"]) > rank:
                return False
            db.execute("INSERT OR REPLACE INTO track (album_key, track_key, number, title, dr, "
                       "dr_exact, seconds, duration, complete, method, at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (album_key, track_key, number, title or "", int(dr), float(dr_exact),
                        float(seconds), duration, int(bool(complete)), method, time.time()))
            db.execute("UPDATE album SET updated = ? WHERE key = ?", (time.time(), album_key))
            return True

    def set_report(self, key: str, dr: int | None, tracks: int | None,
                   mtime: float | None) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE album SET report_dr = ?, report_tracks = ?, report_mtime = ?, "
                       "updated = ? WHERE key = ?", (dr, tracks, mtime, time.time(), key))

    def _albums(self, db, where: str = "", args: tuple = ()) -> list[dict]:
        albums = [dict(r) for r in db.execute(f"SELECT * FROM album {where}", args)]
        if not albums:
            return []
        by_key: dict[str, list] = {a["key"]: [] for a in albums}
        if len(albums) <= 500:
            marks = ",".join("?" * len(by_key))
            rows = db.execute(f"SELECT * FROM track WHERE album_key IN ({marks}) "
                              "ORDER BY number, track_key", tuple(by_key))
        else:
            rows = db.execute("SELECT * FROM track ORDER BY number, track_key")
        for row in rows:
            if row["album_key"] in by_key:
                by_key[row["album_key"]].append(dict(row))
        for a in albums:
            a["tracks"] = by_key[a["key"]]
            a["dr"] = summary(a, a["tracks"])
        return albums

    def album(self, key: str) -> dict | None:
        with self._lock, self._connect() as db:
            found = self._albums(db, "WHERE key = ?", (key,))
        return found[0] if found else None

    def lookup(self, keys: list[str]) -> dict[str, dict]:
        """The figure of each album asked for that has one: {key: summary}."""
        keys = [k for k in dict.fromkeys(keys) if k][:500]
        if not keys:
            return {}
        with self._lock, self._connect() as db:
            marks = ",".join("?" * len(keys))
            found = self._albums(db, f"WHERE key IN ({marks})", tuple(keys))
        return {a["key"]: a["dr"] for a in found if a["dr"]["dr"] is not None}

    def ranking(self, source: str = "", exact_only: bool = False, text: str = "",
                limit: int = 200, offset: int = 0) -> dict:
        """Albums with a figure, highest DR first."""
        where, args = [], []
        if source in ("qobuz", "local", "stream"):
            where.append("source = ?")
            args.append(source)
        for word in text.split()[:6]:
            where.append("(title LIKE ? OR artist LIKE ? OR label LIKE ? OR genre LIKE ?)")
            args += [f"%{word}%"] * 4
        with self._lock, self._connect() as db:
            albums = self._albums(db, ("WHERE " + " AND ".join(where)) if where else "", tuple(args))
            totals = {r["source"]: r["n"] for r in db.execute(
                "SELECT source, COUNT(*) AS n FROM album GROUP BY source")}
        rated = [a for a in albums if a["dr"]["dr"] is not None
                 and (a["dr"]["kind"] == "exact" or not exact_only)]
        rated.sort(key=lambda a: (-a["dr"]["dr"], -(a["dr"]["dr_mean"] or 0),
                                  a["dr"]["kind"] != "exact", a["artist"].casefold(),
                                  a["title"].casefold()))
        page = rated[offset:offset + limit]
        for a in page:
            a.pop("report_mtime", None)
        return {"albums": page, "count": len(rated), "totals": totals}

    def stats(self) -> dict:
        with self._lock, self._connect() as db:
            return {"albums": db.execute("SELECT COUNT(*) FROM album").fetchone()[0],
                    "tracks": db.execute("SELECT COUNT(*) FROM track").fetchone()[0]}

    # -- the local collection's reports --

    def import_reports(self, root: str, describe=None, limit: int = 200000) -> dict:
        """Walk the music directory and bring every dr14.txt in: new or changed
        reports are read, unchanged ones skipped, and a local album whose report
        has gone loses its value (and the album too, if nothing else is known).

        `describe(folder relative to root)` may return the album's tags from
        MPD ({"title", "artist", "year", ..., "track_count"}).  Only what it
        does not know is taken from the folder's name: "Artist - Album" gives
        both, any other name the title alone; a parent folder is never taken
        for the artist (it is as often a genre, or a shelf, as an artist)."""
        root = os.path.realpath(root)
        with self._lock, self._connect() as db:
            known = {r["ref"]: r["report_mtime"] for r in db.execute(
                "SELECT ref, report_mtime FROM album WHERE source = 'local'")}
        seen, added, changed, folders = set(), 0, 0, 0
        for folder, _dirs, files in os.walk(root):
            folders += 1
            if folders > limit:
                break
            if REPORT not in files:
                continue
            rel = os.path.relpath(folder, root)
            if rel == ".":
                rel = ""
            seen.add(rel)
            path = os.path.join(folder, REPORT)
            try:
                mtime = os.stat(path).st_mtime
                if known.get(rel) == mtime:
                    continue
                with open(path, encoding="utf-8", errors="replace") as f:
                    report = parse_report(f.read())
            except OSError:
                continue
            if report is None:
                continue
            key = "local:" + rel
            meta = {}
            if describe:
                try:
                    meta = describe(rel) or {}
                except Exception:               # noqa: BLE001 - names fall back
                    meta = {}
            # a tag MPD does not have must not hide the folder's name
            meta = {k: v for k, v in meta.items() if v not in (None, "")}
            parts = [p for p in rel.split("/") if p]
            name = parts[-1] if parts else os.path.basename(root)
            # untagged rips (one image and a cue sheet) are usually named
            # "Artist - Album (year)": take both from that, else the folders
            artist, sep, album = name.partition(" - ")
            if sep and artist.strip() and album.strip():
                meta.setdefault("artist", artist.strip())
                meta.setdefault("title", album.strip())
            meta.setdefault("title", name)
            meta.setdefault("track_count", report["tracks"] or audio_files(folder) or None)
            self.upsert_album(key, "local", rel, **meta)
            self.set_report(key, report["dr"], report["tracks"], mtime)
            if rel in known:
                changed += 1
            else:
                added += 1
        removed = 0
        if folders <= limit:
            gone = [ref for ref, mtime in known.items() if mtime is not None and ref not in seen]
            with self._lock, self._connect() as db:
                for ref in gone:
                    key = "local:" + ref
                    db.execute("UPDATE album SET report_dr = NULL, report_tracks = NULL, "
                               "report_mtime = NULL WHERE key = ?", (key,))
                    db.execute("DELETE FROM album WHERE key = ? AND NOT EXISTS "
                               "(SELECT 1 FROM track WHERE album_key = ?)", (key, key))
                    removed += 1
        return {"added": added, "changed": changed, "removed": removed,
                "reports": len(seen), "truncated": folders > limit}
