"""Qobuz album search, filtered by label and release date, for the panel.

Qobuz's own apps cannot answer the questions a classical listener asks first:
"this symphony, on Pentatone or Decca, released in the last two years", newest
first.  Its search is a relevance-ranked full-text match with no label or date
facets.  So this module asks Qobuz the plain question, reads the metadata that
every album in the answer already carries (label, release date, composer,
cover), and applies the filters itself.

A match can only be found as deep as the answer is read, so reading goes on
the way scrolling does in Qobuz's app: `scan` albums per query first, then,
while fewer than `want` pass the filters, twice as deep again, up to
`auto_scan`; past that the response says `more` and the caller asks again with
a deeper `scan` (pages already read come from cache).  Each ticked label also
gets a query of its own, "<text> <label>", merged with the plain one, which
reaches into that label sooner; the `queries` list in every response says how
many albums each query brought in, so it is visible whether that helped.

Credentials are not this module's business: the caller hands in a function
returning (app_id, user_auth_token) -- in the panel, the ones upmpdcli's Qobuz
plugin already holds.  Qobuz answers 401 to a search without a user token.

Everything is read-only and cached: a search for `cache_ttl`, an album's
details (which do not change) for `album_cache_ttl`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://www.qobuz.com/api.json/0.2"

# Albums per catalog/search request.  upmpdcli's plugin asks for 50 albums at
# a time; stay with what is known to work.
PAGE_SIZE = 50

# One line per label group: "Name" or "Name: pattern, pattern".  Qobuz spells
# one label many ways ("Decca Music Group Ltd.", "Decca (UMO)", "Decca
# Classics"), so a group matches any label name containing one of its
# patterns, case-insensitively; with no patterns, the name itself is the one.
DEFAULT_LABELS = """\
Pentatone
Decca
Deutsche Grammophon: deutsche grammophon
Harmonia Mundi: harmonia mundi
Alpha: alpha classics
BIS: bis records, bis
Hyperion: hyperion
Chandos: chandos
Channel Classics: channel classics
ECM: ecm
Sony Classical: sony classical
Warner Classics: warner classics, erato
Naxos: naxos
cpo: cpo
"""


@dataclass(frozen=True)
class LabelGroup:
    name: str
    patterns: tuple[str, ...]

    def matches(self, label: str) -> bool:
        text = _fold(label)
        return any(_pattern_in(p, text) for p in self.patterns)


@dataclass
class Settings:
    enabled: bool = True
    base_url: str = DEFAULT_BASE_URL
    # Empty: take the one upmpdcli's plugin uses (see discover_app_id).
    app_id: str = ""
    # A search is made while someone is watching the phone.
    timeout: float = 10.0
    cache_ttl: float = 900.0
    album_cache_ttl: float = 86400.0
    # Albums read per query before the first answer, most relevant first.
    # Filtering happens after, so this is how deep into Qobuz's ranking a
    # match is looked for...
    scan: int = 250
    # ...unless fewer than `want` albums pass the filters: then the search
    # keeps reading, doubling the depth, up to `auto_scan` albums per query.
    # Past that the answer says `more` and the page offers "Load more", which
    # asks again with a deeper `scan` (pages already read come from cache).
    want: int = 20
    auto_scan: int = 1000
    # Albums whose details (performers) are fetched for the result cards.
    max_enrich: int = 30
    workers: int = 4
    max_response_bytes: int = 8 * 1024 * 1024
    labels: list[LabelGroup] = field(default_factory=lambda: parse_labels(DEFAULT_LABELS))
    # upmpdcli's description.xml.  Empty: found by SSDP on this machine.
    renderer: str = ""
    # Albums the played list remembers.
    played_limit: int = 1000


# Deepest scan a request may ask for, whatever the page asks: "Load more"
# pressed many times must not turn one tap into hundreds of requests.
MAX_SCAN = 10000


def settings_from_section(sec, current: Settings) -> Settings:
    """Settings from commands.conf's [qobuz_search], `current` for what it
    leaves out."""
    labels = sec.get("labels", fallback="").strip()
    return Settings(
        enabled=sec.getboolean("enabled", fallback=current.enabled),
        base_url=sec.get("base_url", fallback=current.base_url).strip().rstrip("/"),
        app_id=sec.get("app_id", fallback=current.app_id).strip(),
        timeout=sec.getfloat("timeout", fallback=current.timeout),
        cache_ttl=sec.getfloat("cache_ttl", fallback=current.cache_ttl),
        scan=max(PAGE_SIZE, sec.getint("scan", fallback=current.scan)),
        want=max(1, sec.getint("want", fallback=current.want)),
        auto_scan=min(MAX_SCAN, max(PAGE_SIZE, sec.getint("auto_scan", fallback=current.auto_scan))),
        max_enrich=max(0, sec.getint("max_enrich", fallback=current.max_enrich)),
        labels=parse_labels(labels) if labels else current.labels,
        renderer=sec.get("renderer", fallback=current.renderer).strip(),
        played_limit=max(0, sec.getint("played_limit", fallback=current.played_limit)),
    )


class QobuzError(RuntimeError):
    """A search that could not be answered: network, HTTP or credentials."""


class QobuzAuthError(QobuzError):
    """Qobuz refused the app id or the user token (HTTP 401)."""


# ── labels ──────────────────────────────────────────────────────────────────

def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def _pattern_in(pattern: str, text: str) -> bool:
    """Substring match, but on word boundaries: "bis" must not match
    "Brisbane", nor "ecm" "Thecmusic"."""
    return re.search(r"(?<!\w)" + re.escape(pattern) + r"(?!\w)", text) is not None


def parse_labels(text: str) -> list[LabelGroup]:
    groups = []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        patterns = tuple(_fold(p) for p in rest.split(",") if p.strip()) or (_fold(name),)
        if name:
            groups.append(LabelGroup(name, patterns))
    return groups


# ── album metadata ──────────────────────────────────────────────────────────

def album_date(item: dict) -> str:
    """The album's release date as YYYY-MM-DD, or "".  Qobuz's
    release_date_original is the date of *this* release (a reissue has its
    own), which is what "released in the last two years" means here."""
    value = item.get("release_date_original") or ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    ts = item.get("released_at")
    if isinstance(ts, (int, float)) and ts > 0:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
    return ""


def _name(obj) -> str:
    return (obj or {}).get("name", "") if isinstance(obj, dict) else ""


def album_card(item: dict) -> dict:
    """What a result row shows, from one album object of a search or album/get."""
    image = item.get("image") or {}
    date = album_date(item)
    return {
        "id": str(item.get("id", "")),
        "title": (item.get("title") or "").strip(),
        "version": (item.get("version") or "").strip(),
        "artist": _name(item.get("artist")),
        "composer": _name(item.get("composer")),
        "label": _name(item.get("label")),
        "genre": _name(item.get("genre")),
        "date": date,
        "year": int(date[:4]) if date else None,
        "image": image.get("small") or image.get("thumbnail") or "",
        "image_large": image.get("large") or "",
        "tracks": item.get("tracks_count"),
        "duration": item.get("duration"),
        "bits": item.get("maximum_bit_depth"),
        "rate": item.get("maximum_sampling_rate"),
        "url": f"https://open.qobuz.com/album/{item.get('id', '')}",
    }


# Roles that name who made the recording, not who plays on it.
_TECHNICAL_ROLES = {
    "producer", "co-producer", "executive producer", "associate producer",
    "engineer", "recording engineer", "sound engineer", "balance engineer",
    "mixing engineer", "mastering engineer", "assistant engineer", "mixer",
    "mastering", "mixing", "recording", "editor", "editing", "tonmeister",
    "artistic director", "recording producer", "label", "publisher",
    "music publisher", "composer", "lyricist", "composerlyricist", "author",
    "writer", "librettist", "arranger", "orchestrator", "transcriber",
    "liner notes", "photography", "design", "a&r", "programming",
}
# Roles that only say "is a performer"; shown as nothing more specific.
_GENERIC_ROLES = {"mainartist", "main artist", "performer", "featuredartist",
                  "featured artist", "artist", "associatedperformer",
                  "associated performer"}


def parse_performers(text: str) -> list[tuple[str, list[str]]]:
    """Qobuz's per-track credit string, "Name, Role, Role - Name, Role", as
    [(name, roles)], keeping only people who perform."""
    out = []
    for entry in (text or "").split(" - "):
        parts = [p.strip() for p in entry.split(",") if p.strip()]
        if not parts:
            continue
        name, roles = parts[0], parts[1:]
        folded = [r.casefold() for r in roles]
        if roles and all(r in _TECHNICAL_ROLES for r in folded):
            continue
        out.append((name, [r for r, f in zip(roles, folded)
                           if f not in _GENERIC_ROLES and f not in _TECHNICAL_ROLES]))
    return out


def album_performers(tracks: list[dict], limit: int = 6) -> list[dict]:
    """The album's performers, most present first: [{"name", "roles"}]."""
    seen: dict[str, dict] = {}
    for position, track in enumerate(tracks):
        for name, roles in parse_performers(track.get("performers", "")):
            entry = seen.setdefault(name, {"name": name, "roles": [], "count": 0,
                                           "first": position})
            entry["count"] += 1
            for role in roles:
                if role not in entry["roles"]:
                    entry["roles"].append(role)
    ranked = sorted(seen.values(), key=lambda e: (-e["count"], e["first"]))
    return [{"name": e["name"], "roles": e["roles"]} for e in ranked[:limit]]


def _plain_text(markup: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", markup or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── date window ─────────────────────────────────────────────────────────────

def date_window(last_years: float | None = None, from_year: int | None = None,
                to_year: int | None = None,
                today: dt.date | None = None) -> tuple[str, str]:
    """(lo, hi) as inclusive YYYY-MM-DD bounds, "" for open.  `last_years`
    counts back from today (rolling, so "last 1 year" in January is not just
    January); from/to are whole calendar years."""
    today = today or dt.date.today()
    lo = hi = ""
    if last_years:
        days = round(float(last_years) * 365.25)
        lo = (today - dt.timedelta(days=days)).isoformat()
    if from_year:
        lo = max(lo, f"{int(from_year):04d}-01-01")
    if to_year:
        hi = f"{int(to_year):04d}-12-31"
    return lo, hi


# ── app id ──────────────────────────────────────────────────────────────────

_DISCOVER = ("import sys; sys.path.insert(0, sys.argv[1]); import bundle; "
             "print(bundle.Bundle().get_app_id())")


def discover_app_id(plugin_dir: str, python: str | None = None,
                    timeout: float = 45.0) -> str:
    """The app id upmpdcli's Qobuz plugin would use, read the way it reads it:
    its own bundle.py parses Qobuz's web player.  Run in a child process so
    the plugin's code (and its `requests` import) stays out of the panel."""
    api_dir = os.path.join(plugin_dir, "api")
    if not os.path.isfile(os.path.join(api_dir, "bundle.py")):
        raise QobuzError(f"upmpdcli's Qobuz plugin not found in {plugin_dir}")
    try:
        r = subprocess.run([python or sys.executable, "-c", _DISCOVER, api_dir],
                           capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as error:
        raise QobuzError(f"cannot read the Qobuz app id: {error}") from error
    app_id = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if r.returncode or not app_id.isdigit():
        detail = (r.stderr.strip().splitlines() or ["no output"])[-1]
        raise QobuzError(f"cannot read the Qobuz app id: {detail}")
    return app_id


# ── albums played ───────────────────────────────────────────────────────────

# What of an album/get answer the played list keeps: enough to rebuild a
# result card and to filter it like a fresh search result.
_KEEP = ("id", "title", "version", "artist", "composer", "label", "genre",
         "release_date_original", "released_at", "image", "tracks_count",
         "duration", "maximum_bit_depth", "maximum_sampling_rate", "streamable")


class PlayedAlbums:
    """The albums played from the search, newest first, kept in a JSON file.

    They serve twice: as a "recently played" list, and as search candidates
    of their own -- an album once played is found again by any search whose
    words it contains, however deep Qobuz ranks it."""

    def __init__(self, path: str, limit: int = 1000) -> None:
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()
        self._entries: list[dict] | None = None

    def _load(self) -> list[dict]:
        if self._entries is None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                self._entries = [e for e in data.get("albums", [])
                                 if isinstance(e, dict) and isinstance(e.get("item"), dict)]
            except (OSError, ValueError, AttributeError):
                self._entries = []
        return self._entries

    def record(self, raw: dict, performers: list[dict], when: str | None = None) -> bool:
        """Put an album (an album/get answer) at the head of the list.  False
        when the file cannot be written: remembering is never worth failing
        the play for."""
        album_id = str(raw.get("id", ""))
        if not album_id or not self.limit:
            return False
        with self._lock:
            entries = self._load()
            old = next((e for e in entries if str(e["item"].get("id")) == album_id), None)
            entry = {
                "item": {k: raw[k] for k in _KEEP if k in raw},
                "performers": [p["name"] for p in performers],
                "count": (old or {}).get("count", 0) + 1,
                "last": when or dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            entries[:] = [entry] + [e for e in entries if e is not old][:self.limit - 1]
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                tmp = f"{self.path}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"albums": entries}, f, ensure_ascii=False)
                os.replace(tmp, self.path)
            except OSError:
                return False
        return True

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {str(e["item"].get("id")): e.get("count", 1) for e in self._load()}

    def matching(self, text: str) -> list[dict]:
        """The played albums containing every word of `text` in their title,
        artist, composer, label or performers (all of them for no text)."""
        words = _fold(text).split()
        with self._lock:
            entries = list(self._load())
        out = []
        for entry in entries:
            card = album_card(entry["item"])
            haystack = _fold(" ".join([card["title"], card["version"], card["artist"],
                                       card["composer"], card["label"]]
                                      + entry.get("performers", [])))
            if all(word in haystack for word in words):
                out.append(entry["item"])
        return out

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            entries = list(self._load()[:limit])
        out = []
        for entry in entries:
            card = album_card(entry["item"])
            card.update(played=entry.get("count", 1), last_played=entry.get("last", ""),
                        performers=[{"name": n, "roles": []} for n in entry.get("performers", [])])
            out.append(card)
        return out


# ── search-field completions ────────────────────────────────────────────────

def read_word_list(path: str) -> list[str]:
    """The completion list (qobuz_words.txt): one entry per line, "#"
    comments, the first spelling of a repeated entry kept.  A missing file is
    an empty list: completion is a convenience, never a failure."""
    words, seen = [], set()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    for line in lines:
        entry = re.sub(r"\s+", " ", line.split("#", 1)[0]).strip()
        if entry and _fold(entry) not in seen:
            seen.add(_fold(entry))
            words.append(entry)
    return words


class SearchWords:
    """Completions learned from what is played: the search text that found an
    album, and the album's artist and composer ("Pink Floyd" is offered
    once a Pink Floyd album has been played from a search).  A JSON file,
    each entry {"text", "count", "last"}, newest first."""

    def __init__(self, path: str, limit: int = 500) -> None:
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()
        self._entries: list[dict] | None = None

    def _load(self) -> list[dict]:
        if self._entries is None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                self._entries = [e for e in data.get("words", [])
                                 if isinstance(e, dict) and isinstance(e.get("text"), str)]
            except (OSError, ValueError, AttributeError):
                self._entries = []
        return self._entries

    def record(self, texts: list[str], when: str | None = None) -> bool:
        """Put each text at the head of the list (counted again if known,
        under its latest spelling).  False when the file cannot be written."""
        texts = [re.sub(r"\s+", " ", t or "").strip() for t in texts]
        texts = [t for t in texts if 1 < len(t) <= 120]
        if not texts or not self.limit:
            return False
        when = when or dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            entries = self._load()
            for text in dict.fromkeys(reversed(texts)):   # the first ends up on top
                old = next((e for e in entries if _fold(e["text"]) == _fold(text)), None)
                entries[:] = [{"text": text, "count": (old or {}).get("count", 0) + 1,
                               "last": when}] + [e for e in entries if e is not old]
            del entries[self.limit:]
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                tmp = f"{self.path}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"words": entries}, f, ensure_ascii=False)
                os.replace(tmp, self.path)
            except OSError:
                return False
        return True

    def recent(self) -> list[dict]:
        with self._lock:
            return [{"text": e["text"], "count": e.get("count", 1)} for e in self._load()]


# ── client ──────────────────────────────────────────────────────────────────

class QobuzCatalog:
    """Label- and date-filtered album search over Qobuz's catalog API."""

    def __init__(self, settings: Settings | None = None, credentials=None,
                 fetch=None, today=None, played: PlayedAlbums | None = None) -> None:
        self.settings = settings or Settings()
        self.played = played
        # () -> (app_id, user_auth_token); raises QobuzError when unavailable.
        self._credentials = credentials or (lambda: (self.settings.app_id, ""))
        self._fetch_override = fetch          # tests: (endpoint, params) -> dict
        self._today = today or dt.date.today
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    # -- transport --

    def _cached(self, key: str, ttl: float, make):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = make()
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    def _call(self, endpoint: str, params: dict) -> dict:
        if self._fetch_override is not None:
            return self._fetch_override(endpoint, dict(params))
        app_id, token = self._credentials()
        if not app_id:
            raise QobuzError("no Qobuz app id")
        query = urllib.parse.urlencode({**params, "app_id": app_id})
        headers = {"X-App-Id": app_id, "Accept": "application/json",
                   "User-Agent": "open-media-drc panel (album search)"}
        if token:
            headers["X-User-Auth-Token"] = token
        request = urllib.request.Request(
            f"{self.settings.base_url.rstrip('/')}/{endpoint}?{query}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                raw = response.read(self.settings.max_response_bytes)
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise QobuzAuthError(
                    "Qobuz refused the credentials (HTTP 401): sign in to Qobuz "
                    "again from the panel" if token else
                    "Qobuz wants a signed-in user (HTTP 401): upmpdcli's Qobuz "
                    "plugin has no token yet") from error
            raise QobuzError(f"Qobuz answered HTTP {error.code} to {endpoint}") from error
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise QobuzError(f"cannot reach Qobuz: {reason}") from error
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as error:
            raise QobuzError(f"Qobuz sent no JSON for {endpoint}") from error
        if isinstance(data, dict) and data.get("status") == "error":
            raise QobuzError(f"Qobuz: {data.get('message') or 'error'}")
        return data

    # -- queries --

    def _page(self, query: str, offset: int) -> tuple[list[dict], int | None]:
        """One page of one query's albums, and the total Qobuz reports."""
        def make():
            data = self._call("catalog/search", {
                "query": query, "type": "albums", "limit": PAGE_SIZE, "offset": offset})
            block = data.get("albums") or {}
            return block.get("items") or [], block.get("total")
        return self._cached(f"page:{_fold(query)}:{offset}", self.settings.cache_ttl, make)

    def _read(self, pool, scans: list[dict], end: int) -> None:
        """Extend each query's album list to `end` albums, or to its last one.

        A query's first page comes first, alone: it gives the total, so no
        page past the end is ever asked for.  The rest go in parallel.  A
        first page that fails fails the search (it is the credentials, or the
        network); a later one only ends that query, with the error noted."""
        fresh = [s for s in scans if s["total"] is None and not s["done"]]
        for scan, (items, total) in zip(fresh, pool.map(
                lambda s: self._page(s["query"], 0), fresh)):
            scan["items"] = list(items)
            scan["total"] = total if total is not None else len(items)
            scan["done"] = len(items) < PAGE_SIZE or len(items) >= scan["total"]

        jobs = [(scan, offset) for scan in scans if not scan["done"]
                for offset in range(len(scan["items"]), min(end, scan["total"]), PAGE_SIZE)]

        def fetch(job):
            scan, offset = job
            try:
                return self._page(scan["query"], offset)[0], ""
            except QobuzError as error:
                return None, str(error)

        for (scan, offset), (items, error) in zip(jobs, pool.map(fetch, jobs)):
            if scan["done"] or offset != len(scan["items"]):
                continue                      # an earlier page of it ended it
            if items is None:
                scan["done"], scan["error"] = True, error
                continue
            scan["items"].extend(items)
            if len(items) < PAGE_SIZE or len(scan["items"]) >= scan["total"]:
                scan["done"] = True

    def record_played(self, album_id: str) -> bool:
        """Remember an album as played (see PlayedAlbums)."""
        if not self.played:
            return False
        raw = self._album_raw(str(album_id))
        return self.played.record(
            raw, album_performers((raw.get("tracks") or {}).get("items") or []))

    def track(self, track_id: str) -> dict:
        """One track and its album's card: what the player shows for the
        track playing (the cover, above all), whoever queued it."""
        track_id = str(track_id).strip()
        if not track_id.isdigit():
            raise QobuzError("bad track id")
        raw = self._cached("track:" + track_id, self.settings.album_cache_ttl,
                           lambda: self._call("track/get", {"track_id": track_id}))
        return {
            "id": track_id,
            "title": (raw.get("title") or "").strip(),
            "version": (raw.get("version") or "").strip(),
            "work": (raw.get("work") or "").strip(),
            "duration": raw.get("duration"),
            "composer": _name(raw.get("composer")),
            "performer": _name(raw.get("performer")),
            "album": album_card(raw.get("album") or {}),
        }

    def _album_raw(self, album_id: str) -> dict:
        return self._cached("album:" + album_id, self.settings.album_cache_ttl,
                            lambda: self._call("album/get", {"album_id": album_id}))

    def label_groups(self, names: list[str]) -> list[LabelGroup]:
        """The configured groups named in `names`; an unknown name is taken as
        a group of its own (so a label seen in the results can be ticked
        before it is saved as a favourite)."""
        known = {g.name.casefold(): g for g in self.settings.labels}
        groups = []
        for name in names:
            name = name.strip()
            if name:
                groups.append(known.get(name.casefold()) or LabelGroup(name, (_fold(name),)))
        return groups

    def _filter(self, scans: list[dict], groups: list[LabelGroup], lo: str, hi: str):
        """Merge the queries' albums, keeping each one's best position (the
        plain query's order is Qobuz's relevance; a label query's hits
        interleave by their own position), and apply the filters."""
        found: dict[str, tuple[int, dict]] = {}
        played = self.played.counts() if self.played else {}
        for scan in scans:
            for position, item in enumerate(scan["items"]):
                album_id = str(item.get("id", ""))
                if album_id and (album_id not in found or position < found[album_id][0]):
                    found[album_id] = (position, item)

        results, labels_seen, unstreamable = [], {}, 0
        for position, item in found.values():
            card = album_card(item)
            if item.get("streamable") is False:
                unstreamable += 1
                continue
            if (lo or hi) and not card["date"]:
                continue
            if (lo and card["date"] < lo) or (hi and card["date"] > hi):
                continue
            if card["label"]:
                labels_seen[card["label"]] = labels_seen.get(card["label"], 0) + 1
            card["groups"] = [g.name for g in self.settings.labels if g.matches(card["label"])]
            if groups and not any(g.matches(card["label"]) for g in groups):
                continue
            card["rank"] = position
            if card["id"] in played:
                card["played"] = played[card["id"]]
            results.append(card)
        return results, labels_seen, unstreamable, len(found)

    def search(self, text: str = "", labels: list[str] | None = None,
               last_years: float | None = None, from_year: int | None = None,
               to_year: int | None = None, sort: str = "date",
               enrich: bool = True, scan: int | None = None) -> dict:
        text = (text or "").strip()
        groups = self.label_groups(labels or [])
        if not text and not groups:
            raise QobuzError("nothing to search for: type something or tick a label")
        if sort not in ("date", "relevance"):
            raise QobuzError(f"unknown sort '{sort}'")
        lo, hi = date_window(last_years, from_year, to_year, today=self._today())

        queries = ([text] if text else []) + [
            f"{text} {g.name}".strip() for g in groups]
        scans = [{"query": q, "items": [], "total": None, "done": False, "error": ""}
                 for q in queries]
        if self.played:
            # Albums played before and matching the words: in the running
            # whatever Qobuz's ranking does with them.
            mine = self.played.matching(text)
            scans.append({"query": "(played before)", "items": mine, "total": len(mine),
                          "done": True, "error": ""})
        depth = min(MAX_SCAN, max(PAGE_SIZE, int(scan or self.settings.scan)))
        with ThreadPoolExecutor(max_workers=max(1, self.settings.workers)) as pool:
            while True:
                self._read(pool, scans, depth)
                results, labels_seen, unstreamable, considered = self._filter(
                    scans, groups, lo, hi)
                if (len(results) >= self.settings.want or all(s["done"] for s in scans)
                        or depth >= max(self.settings.auto_scan, scan or 0)):
                    break
                depth = min(depth * 2, self.settings.auto_scan)

        if sort == "date":
            results.sort(key=lambda c: (c["date"] or "0000", -c["rank"]), reverse=True)
        else:
            results.sort(key=lambda c: c["rank"])

        enriched = 0
        if enrich and results:
            head = results[:self.settings.max_enrich]
            with ThreadPoolExecutor(max_workers=max(1, self.settings.workers)) as pool:
                details = list(pool.map(self._performers_quietly, [c["id"] for c in head]))
            for card, performers in zip(head, details):
                if performers is not None:
                    card["performers"] = performers
                    enriched += 1

        return {
            "query": text,
            "labels": [g.name for g in groups],
            "window": {"from": lo, "to": hi},
            "sort": sort,
            "results": results,
            "count": len(results),
            "considered": considered,
            "unstreamable": unstreamable,
            "enriched": enriched,
            # How deep each query was read.  `more`: some query has albums
            # left; ask again with scan=next_scan to read them.
            "scan": depth,
            "more": not all(s["done"] for s in scans) and depth < MAX_SCAN,
            "next_scan": min(MAX_SCAN, depth * 2),
            "queries": [{"query": s["query"], "fetched": len(s["items"]),
                         "total": s["total"], "done": s["done"], "error": s["error"]}
                        for s in scans],
            # Label names in the date window, most frequent first: what can be
            # ticked or saved as a favourite, spelled the way Qobuz spells it.
            "labels_seen": [
                {"name": name, "count": count,
                 "groups": [g.name for g in self.settings.labels if g.matches(name)]}
                for name, count in sorted(labels_seen.items(), key=lambda kv: (-kv[1], kv[0]))],
        }

    def _performers_quietly(self, album_id: str) -> list[dict] | None:
        """A card's performers, or None: one album failing to load must not
        cost the whole result list."""
        try:
            raw = self._album_raw(album_id)
        except QobuzError:
            return None
        return album_performers((raw.get("tracks") or {}).get("items") or [])

    def album(self, album_id: str) -> dict:
        """One album with its tracks, performers and description."""
        album_id = str(album_id).strip()
        if not re.fullmatch(r"[0-9A-Za-z]+", album_id):
            raise QobuzError("bad album id")
        raw = self._album_raw(album_id)
        tracks = (raw.get("tracks") or {}).get("items") or []
        card = album_card(raw)
        card["groups"] = [g.name for g in self.settings.labels if g.matches(card["label"])]
        card["performers"] = album_performers(tracks, limit=12)
        card["description"] = _plain_text(raw.get("description") or "")
        card["copyright"] = raw.get("copyright") or ""
        card["streamable"] = raw.get("streamable") is not False
        card["track_list"] = [{
            "id": str(t.get("id", "")),
            "title": (t.get("title") or "").strip(),
            "version": (t.get("version") or "").strip(),
            "work": (t.get("work") or "").strip(),
            "number": t.get("track_number"),
            "disc": t.get("media_number"),
            "duration": t.get("duration"),
            "composer": _name(t.get("composer")),
            "performer": _name(t.get("performer")),
            "streamable": t.get("streamable") is not False,
        } for t in tracks]
        return card
