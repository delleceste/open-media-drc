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

Several boxes can share what they heard (dr_sync.py).  Every row then says
where it was measured: `origin` is empty for this box and the other box's name
for an imported one.  Rows of different origins sit side by side -- a box only
ever replaces its own, or one box's whole set on import -- and the album figure
takes the best measurement of each track, wherever it came from.  Another
box's local albums are keyed `local@<box>:<folder>`: its folders are not ours.

An album on a drive that carries a volume marker (dr_volumes.py) is keyed
`vol:<volume id>:<folder on the drive>` instead, the same on every box: the
drive can move between boxes and its albums stay one.  Such an album's report
may come from another box (`report_origin`), until this box reads it itself.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time

import dr_volumes
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
# sought, and what was measured covers its length but for this much (MPD's
# durations are rounded, and a track's end is placed by the next one's start).
START_TOLERANCE = 5.0
END_TOLERANCE = 4.0
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
    updated       REAL NOT NULL,
    origin        TEXT NOT NULL DEFAULT '',
    report_origin TEXT NOT NULL DEFAULT ''
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
    origin     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (album_key, track_key, origin)
);
CREATE INDEX IF NOT EXISTS track_album ON track (album_key);
"""

_META = ("title", "artist", "year", "label", "genre", "image", "track_count")


def tags_key(artist: str, album: str) -> str:
    return "tags:" + " ".join(artist.split()).casefold() + "\x1f" + " ".join(album.split()).casefold()


def _rank(t: dict) -> tuple:
    return (bool(t["complete"]), t["method"] == "measured", t["seconds"], t.get("at", 0))


def best_tracks(tracks: list[dict]) -> list[dict]:
    """One row per track: the best of the boxes' measurements of it."""
    best: dict = {}
    for t in tracks:
        have = best.get(t["track_key"])
        if have is None or _rank(t) > _rank(have):
            best[t["track_key"]] = t
    return sorted(best.values(), key=lambda t: (t["number"] is None, t["number"] or 0, t["track_key"]))


def summary(album: dict, tracks: list[dict]) -> dict:
    """The album's figure from its row and its stored tracks.

    {"dr": int | None, "kind": "exact" | "estimate" | None,
     "basis": "report" | "measured" | "listened" | "", "heard": tracks stored,
     "complete": complete tracks, "track_count", "seconds": seconds measured,
     "dr_mean": unrounded mean of the tracks, to order equal values by,
     "origins": the boxes the tracks used were heard on, "" for this one}"""
    tracks = best_tracks(tracks)
    heard = len(tracks)
    complete = [t for t in tracks if t["complete"]]
    seconds = sum(t["seconds"] for t in tracks)
    count = album.get("track_count")
    out = {"dr": None, "kind": None, "basis": "", "heard": heard,
           "complete": len(complete), "track_count": count,
           "seconds": round(seconds), "dr_mean": None,
           "origins": sorted({t.get("origin", "") for t in tracks})}
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
_REPORT_ROW_RE = re.compile(r"^\s*DR\s*(\d{1,2})\s+.*?\s+\d{1,3}(?::\d{2}){1,2}\s+(.+?)\s*$", re.I)


def parse_report(text: str) -> dict | None:
    """The album value of a dr14.txt (ours, dr14_tmeter's or foobar2000's) and
    its track count when it states one."""
    values = _ALBUM_RE.findall(text)
    if not values:
        return None
    tracks = _TRACKS_RE.search(text)
    return {"dr": int(values[-1]), "tracks": int(tracks.group(1)) if tracks else None}


def parse_report_track_rows(text: str) -> list[dict]:
    """Per-song DR figures in the table of an omdrc, dr14_tmeter or foobar report."""
    rows = []
    for line in text.splitlines():
        match = _REPORT_ROW_RE.match(line)
        if not match:
            continue
        raw = match.group(2).strip()
        name = re.sub(r"^\d{1,3}\s*[-.]\s*", "", raw, count=1)
        name = os.path.splitext(name)[0]
        name = re.sub(r"^\d{1,3}\s*[-.]\s*", "", name, count=1)
        rows.append({"number": len(rows) + 1, "title": name or raw,
                     "dr": int(match.group(1))})
    return rows


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
    whether it was sought, how long it is, and at which frame of the PCM
    stream its first sample went by.  Pure bookkeeping, fed MPD's status
    (currentsong + status, as _mpd_now_playing_via_protocol reads it) every
    half second; the audio's statistics stay in the analyzer (SubBlocks).

    The first sample is known exactly when the track began after silence (play
    after stop: the first frame after the gap is it).  Otherwise -- one track
    running into the next -- every poll offers an estimate, frames read so far
    less MPD's elapsed time; their median, less the pipeline's latency, settles
    within a few seconds, until a seek makes elapsed time useless for it.  A
    track known both ways measures that latency (latency_sample)."""

    ESTIMATES = 21

    def __init__(self, song: dict, now: float, start_block: int,
                 start_frame: int | None = None) -> None:
        self.song = dict(song)
        self.start_block = start_block
        self.exact_start = start_frame
        self.created = now
        self.created_frames = -1    # the stream's frame count when it was seen start
        self.estimates: list[int] = []
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
        for key in ("duration", "title", "album", "artist", "album_artist", "track_no", "audio"):
            if song.get(key) not in (None, ""):
                self.song[key] = song[key]
        return sought

    def estimate_start(self, frames: int, elapsed, rate: int) -> None:
        """One poll's guess at the first sample, before latency: `frames` read
        when MPD said `elapsed` seconds of this track had played."""
        if (not self.seeks and isinstance(elapsed, (int, float))
                and len(self.estimates) < self.ESTIMATES):
            self.estimates.append(int(frames - elapsed * rate))

    def _median(self) -> int:
        return sorted(self.estimates)[len(self.estimates) // 2]

    def start(self, latency: int = 0) -> int | None:
        """The frame of the first sample: exact, or estimated less `latency`."""
        if self.exact_start is not None:
            return self.exact_start
        return self._median() - latency if self.estimates else None

    @property
    def source_rate(self) -> int | None:
        """The sample rate of the file being played (MPD's "audio" status,
        rate:bits:channels), which sets the reference meter's block length."""
        rate = str(self.song.get("audio") or "").split(":")[0]
        return int(rate) if rate.isdigit() and int(rate) > 0 else None

    def latency_sample(self) -> int | None:
        """How far the estimates run ahead of a start known exactly."""
        if self.exact_start is None or len(self.estimates) < 5:
            return None
        return self._median() - self.exact_start

    def result(self, dr_exact: float | None, seconds: float) -> dict | None:
        """The track's row once it has ended, or None if too little was heard."""
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
    def __init__(self, path: str, volumes: dr_volumes.Volumes | None = None) -> None:
        self.path = path
        self.volumes = volumes or dr_volumes.VOLUMES
        self._lock = threading.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        if not self._ready:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        if not self._ready:
            self._migrate(db)
            db.executescript(SCHEMA)
            self._ready = True
        return db

    @staticmethod
    def _migrate(db) -> None:
        """Bring a database from before sharing up to date: rows get an
        origin (this box), and a track's key includes it."""
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "album" in tables and "origin" not in {r[1] for r in db.execute("PRAGMA table_info(album)")}:
            db.execute("ALTER TABLE album ADD COLUMN origin TEXT NOT NULL DEFAULT ''")
        if "album" in tables and "report_origin" not in {r[1] for r in db.execute("PRAGMA table_info(album)")}:
            db.execute("ALTER TABLE album ADD COLUMN report_origin TEXT NOT NULL DEFAULT ''")
        if "track" in tables and "origin" not in {r[1] for r in db.execute("PRAGMA table_info(track)")}:
            db.executescript("""
                ALTER TABLE track RENAME TO track_old;
                DROP INDEX IF EXISTS track_album;
                CREATE TABLE track (
                    album_key TEXT NOT NULL, track_key TEXT NOT NULL, number INTEGER,
                    title TEXT NOT NULL DEFAULT '', dr INTEGER NOT NULL, dr_exact REAL NOT NULL,
                    seconds REAL NOT NULL, duration REAL, complete INTEGER NOT NULL,
                    method TEXT NOT NULL, at REAL NOT NULL, origin TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (album_key, track_key, origin));
                INSERT INTO track (album_key, track_key, number, title, dr, dr_exact, seconds,
                                   duration, complete, method, at, origin)
                    SELECT album_key, track_key, number, title, dr, dr_exact, seconds,
                           duration, complete, method, at, '' FROM track_old;
                DROP TABLE track_old;
            """)
        db.commit()

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
                             "WHERE album_key = ? AND track_key = ? AND origin = ''",
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
                       "report_origin = '', updated = ? WHERE key = ?",
                       (dr, tracks, mtime, time.time(), key))

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

    def recent_tracks(self, since: float, limit: int = 20) -> dict:
        """Recently kept measurements made on this box, including unrated albums.

        The track table keeps the best row per track, not a play-by-play log.
        Imported rows and dr14.txt report rows are not recent listening here.
        """
        where = "t.origin = '' AND t.method IN ('live', 'measured') AND t.at >= ?"
        with self._lock, self._connect() as db:
            count = db.execute(f"SELECT COUNT(*) FROM track t WHERE {where}", (since,)).fetchone()[0]
            rows = [dict(r) for r in db.execute(f"""
                SELECT t.track_key, t.number, t.title, t.dr, t.dr_exact, t.seconds,
                       t.complete, t.method, t.at, a.key AS album_key,
                       a.title AS album_title, a.artist, a.source, a.ref, a.image
                FROM track t JOIN album a ON a.key = t.album_key
                WHERE {where} ORDER BY t.at DESC LIMIT ?
            """, (since, limit))]
            keys = list(dict.fromkeys(row["album_key"] for row in rows))
            albums = self._albums(db, f"WHERE key IN ({','.join('?' * len(keys))})", tuple(keys)) if keys else []
        summaries = {album["key"]: album["dr"] for album in albums}
        return {"tracks": rows, "count": count, "summaries": summaries}

    # -- sharing between boxes (dr_sync.py) --

    def export_rows(self) -> list[dict]:
        """What this box measured or read itself, for the other boxes: its own
        tracks, its albums' names, and its local reports.  Deterministic, so
        an unchanged log exports byte for byte the same file."""
        with self._lock, self._connect() as db:
            tracks = [dict(r) for r in db.execute(
                "SELECT * FROM track WHERE origin = '' ORDER BY album_key, track_key")]
            keys = {t["album_key"] for t in tracks}
            albums = [dict(r) for r in db.execute("SELECT * FROM album WHERE origin = '' ORDER BY key")]
        rows = []
        for a in albums:
            if a["report_origin"]:          # another box's report, not ours to pass on
                a["report_dr"] = a["report_tracks"] = None
            if a["key"] not in keys and a["report_dr"] is None:
                continue
            rows.append({"album": {k: a[k] for k in ("key", "source", "ref", "title", "artist",
                                                     "year", "label", "genre", "image",
                                                     "track_count", "report_dr", "report_tracks")}})
        for t in tracks:
            rows.append({"track": {k: t[k] for k in ("album_key", "track_key", "number", "title",
                                                     "dr", "dr_exact", "seconds", "duration",
                                                     "complete", "method", "at")}})
        return rows

    @staticmethod
    def _foreign_key(key: str, box: str) -> str:
        """Another box's local folder is not one of ours."""
        return f"local@{box}:" + key[len("local:"):] if key.startswith("local:") else key

    def import_box(self, box: str, rows: list[dict]) -> dict:
        """Replace everything known from `box` with `rows` (its export)."""
        albums = [r["album"] for r in rows if isinstance(r.get("album"), dict)]
        tracks = [r["track"] for r in rows if isinstance(r.get("track"), dict)]
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM track WHERE origin = ?", (box,))
            db.execute("DELETE FROM album WHERE origin = ?", (box,))
            reported = [r[0] for r in db.execute(
                "SELECT key FROM album WHERE report_origin = ?", (box,))]
            db.execute("UPDATE album SET report_dr = NULL, report_tracks = NULL, "
                       "report_origin = '' WHERE report_origin = ?", (box,))
            for a in albums:
                key = self._foreign_key(str(a.get("key", "")), box)
                if not key:
                    continue
                own = not key.startswith("local@")
                db.execute("INSERT OR IGNORE INTO album (key, source, ref, updated, origin) "
                           "VALUES (?, ?, ?, ?, ?)",
                           (key, a.get("source") or "stream", a.get("ref") or "", now,
                            "" if own else box))
                meta = {k: a.get(k) for k in _META if a.get(k) not in (None, "")}
                if meta:
                    # fill in only what this box does not know
                    sets = ", ".join(f"{k} = CASE WHEN {k} IS NULL OR {k} = '' THEN ? ELSE {k} END"
                                     for k in meta)
                    db.execute(f"UPDATE album SET {sets} WHERE key = ?", (*meta.values(), key))
                if not own and a.get("report_dr") is not None:
                    db.execute("UPDATE album SET report_dr = ?, report_tracks = ? WHERE key = ?",
                               (a["report_dr"], a.get("report_tracks"), key))
                elif key.startswith(dr_volumes.PREFIX) and a.get("report_dr") is not None:
                    # a drive's report read elsewhere, until this box reads it itself
                    db.execute("UPDATE album SET report_dr = ?, report_tracks = ?, "
                               "report_origin = ? WHERE key = ? AND report_dr IS NULL",
                               (a["report_dr"], a.get("report_tracks"), box, key))
            for t in tracks:
                try:
                    db.execute("INSERT OR REPLACE INTO track (album_key, track_key, number, title, "
                               "dr, dr_exact, seconds, duration, complete, method, at, origin) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (self._foreign_key(str(t["album_key"]), box), str(t["track_key"]),
                                t.get("number"), t.get("title") or "", int(t["dr"]),
                                float(t["dr_exact"]), float(t["seconds"]), t.get("duration"),
                                int(bool(t["complete"])), str(t.get("method") or "live"),
                                float(t.get("at") or now), box))
                except (KeyError, TypeError, ValueError):
                    continue        # a malformed row from elsewhere is skipped, not fatal
            for key in reported:    # a report the box no longer has, and nothing else
                db.execute("DELETE FROM album WHERE key = ? AND report_dr IS NULL AND NOT EXISTS "
                           "(SELECT 1 FROM track WHERE album_key = ?)", (key, key))
        return {"albums": len(albums), "tracks": len(tracks)}

    def stats(self) -> dict:
        with self._lock, self._connect() as db:
            return {"albums": db.execute("SELECT COUNT(*) FROM album").fetchone()[0],
                    "tracks": db.execute("SELECT COUNT(*) FROM track").fetchone()[0]}

    # -- the local collection's reports --

    def _read_report(self, root: str, rel: str, key: str, known, describe,
                     measured: bool = False) -> str:
        """Read one folder's dr14.txt into its album unless it is unchanged
        since it was last read: "read", "unchanged", or "recheck".

        `known` is (report mtime, report DR) as stored, or None.  A report that
        now gives another value than the one stored is not taken: one of the
        two is wrong, and the audio is measured again to settle it ("recheck",
        the stored value stays meanwhile).  A report the DR14 scan has just
        `measured` is that settlement, and is always taken."""
        folder = os.path.join(root, rel)
        path = os.path.join(folder, REPORT)
        known_mtime, known_dr = known or (None, None)
        try:
            mtime = os.stat(path).st_mtime
            if known_mtime == mtime:
                return "unchanged"
            with open(path, encoding="utf-8", errors="replace") as f:
                report = parse_report(f.read())
        except OSError:
            return "unchanged"
        if report is None:
            return "unchanged"
        if not measured and known_dr is not None and report["dr"] != known_dr:
            return "recheck"
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
        return "read"

    def _known_reports(self) -> dict:
        with self._lock, self._connect() as db:
            return {r["key"]: (r["report_mtime"], r["report_dr"]) for r in db.execute(
                "SELECT key, report_mtime, report_dr FROM album "
                "WHERE source = 'local' AND origin = ''")}

    def import_folders(self, root: str, folders, describe=None, measured: bool = False) -> dict:
        """Read the dr14.txt of just these folders, relative to `root` as MPD
        names them (a linked disk stays under its link): the ones a DR14 scan
        has listed or just `measured`, without walking the whole collection.
        Returns {"read": how many, "recheck": [folders to measure again]}."""
        root = os.path.realpath(root)
        known = self._known_reports()
        read, recheck = 0, []
        for folder in dict.fromkeys(folders):
            rel = os.path.normpath(folder) if folder else ""
            if rel == ".":
                rel = ""
            if os.path.isabs(rel) or rel == ".." or rel.startswith("../"):
                continue
            key = dr_volumes.album_key(root, rel, self.volumes)
            outcome = self._read_report(root, rel, key, known.get(key), describe, measured)
            read += outcome == "read"
            if outcome == "recheck":
                recheck.append(rel)
        return {"read": read, "recheck": recheck}

    def import_reports(self, root: str, describe=None, limit: int = 200000) -> dict:
        """Walk the music directory and bring every dr14.txt in: new or changed
        reports are read, unchanged ones skipped, and a local album whose report
        has gone loses its value (and the album too, if nothing else is known).

        `describe(folder relative to root)` may return the album's tags from
        MPD ({"title", "artist", "year", ..., "track_count"}).  Only what it
        does not know is taken from the folder's name: "Artist - Album" gives
        both, any other name the title alone; a parent folder is never taken
        for the artist (it is as often a genre, or a shelf, as an artist).

        A folder on a drive with a volume marker is keyed by the drive; a report
        of a drive that is not plugged in is kept, not taken for deleted.  A
        report that now gives another value than the one stored is not taken
        but listed in "recheck", to be measured again (see _read_report)."""
        root = os.path.realpath(root)
        adopted = self.adopt_volumes(root)
        known = self._known_reports()
        seen, present, added, changed, folders = set(), set(), 0, 0, 0
        recheck = []
        # followlinks: a disk linked into the library (USBHD2 -> /media/...) is
        # part of the collection like any folder; `limit` bounds a link loop.
        for folder, _dirs, files in os.walk(root, followlinks=True):
            folders += 1
            if folders > limit:
                break
            if REPORT not in files:
                continue
            rel = os.path.relpath(folder, root)
            if rel == ".":
                rel = ""
            key = dr_volumes.album_key(root, rel, self.volumes)
            seen.add(key)
            on = dr_volumes.parse(key)
            if on:
                present.add(on[0])
            outcome = self._read_report(root, rel, key, known.get(key), describe)
            if outcome == "recheck":
                recheck.append(rel)
            elif outcome == "read":
                if key in known:
                    changed += 1
                else:
                    added += 1
        removed = 0
        if folders <= limit:
            # a drive that is not here has not lost its albums
            gone = [key for key, (mtime, _dr) in known.items() if mtime is not None and key not in seen
                    and (dr_volumes.parse(key) or ("",))[0] in present | {""}]
            with self._lock, self._connect() as db:
                for key in gone:
                    db.execute("UPDATE album SET report_dr = NULL, report_tracks = NULL, "
                               "report_mtime = NULL WHERE key = ?", (key,))
                    db.execute("DELETE FROM album WHERE key = ? AND NOT EXISTS "
                               "(SELECT 1 FROM track WHERE album_key = ?)", (key, key))
                    removed += 1
        return {"added": added, "changed": changed, "removed": removed, "adopted": adopted,
                "reports": len(seen), "truncated": folders > limit, "recheck": recheck}

    def adopt_volumes(self, root: str) -> int:
        """Rekey this box's `local:` albums that are on a drive with a volume
        marker as `vol:` ones, merged into what is already known under that
        key; a folder that is not here now keeps its key until it is."""
        with self._lock, self._connect() as db:
            keys = {r[0] for r in db.execute(
                "SELECT key FROM album WHERE key LIKE 'local:%' AND origin = '' "
                "UNION SELECT album_key FROM track WHERE album_key LIKE 'local:%' AND origin = ''")}
        moves = {}
        for old in sorted(keys):
            new = dr_volumes.album_key(root, old[len("local:"):], self.volumes)
            if new != old:
                moves[old] = new
        if not moves:
            return 0
        with self._lock, self._connect() as db:
            for old, new in moves.items():
                self._move(db, old, new)
        return len(moves)

    @staticmethod
    def _move(db, old: str, new: str) -> None:
        """Give album `old` the key `new`, merged into what is known there."""
        # a track already known under the new key was heard since: it stays
        db.execute("UPDATE OR IGNORE track SET album_key = ? WHERE album_key = ?", (new, old))
        db.execute("DELETE FROM track WHERE album_key = ?", (old,))
        if db.execute("SELECT 1 FROM album WHERE key = ?", (new,)).fetchone() is None:
            db.execute("UPDATE album SET key = ? WHERE key = ?", (new, old))
            return
        names = {"old": old, "new": new}
        fill = ", ".join(f"{k} = CASE WHEN {k} IS NULL OR {k} = '' THEN "
                         f"(SELECT {k} FROM album WHERE key = :old) ELSE {k} END" for k in _META)
        db.execute(f"UPDATE album SET {fill} WHERE key = :new", names)
        db.execute("""UPDATE album SET (report_dr, report_tracks, report_mtime, report_origin) =
                          (SELECT report_dr, report_tracks, report_mtime, report_origin
                           FROM album WHERE key = :old)
                      WHERE key = :new AND (report_dr IS NULL OR report_origin != '')
                        AND (SELECT report_dr FROM album WHERE key = :old) IS NOT NULL""", names)
        db.execute("DELETE FROM album WHERE key = ?", (old,))
