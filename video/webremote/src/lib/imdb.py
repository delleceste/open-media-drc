"""IMDb enrichment via the OMDb API (omdbapi.com).

There is no free official IMDb API; OMDb is the de-facto one that returns IMDb
data — title, year, director, cast, plot, rating — plus the imdbID needed for a
verified IMDb title link.

Opt-in and graceful: with no API key configured, lookup() returns None and the
caller falls back to a plain IMDb search link with no external request. When a
key is set, results (including "not found") are cached on disk so repeat views
are instant and work offline once seen.

Privacy note: with a key configured, movie titles are sent to omdbapi.com.
"""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

from . import titles

_TTL_OK = 30 * 24 * 3600     # keep good hits a month
_TTL_MISS = 3 * 24 * 3600    # retry not-found after a few days


def _cache_file(cache_dir: str, title: str, year: str | None) -> str:
    key = hashlib.sha1(f"{title.lower()}|{year or ''}".encode()).hexdigest()
    return os.path.join(cache_dir, "imdb", key + ".json")


def _read_cache(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, ValueError):
        return None
    age = time.time() - blob.get("_at", 0)
    ttl = _TTL_OK if blob.get("data", {}).get("found") else _TTL_MISS
    return blob if age < ttl else None


def _write_cache(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"_at": time.time(), "data": data}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def _omdb_get(params: dict, api_key: str, timeout: float) -> dict | None:
    q = urllib.parse.urlencode({**params, "apikey": api_key})
    url = "https://www.omdbapi.com/?" + q
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _map(raw: dict) -> dict:
    """OMDb JSON -> our compact record."""
    imdb_id = raw.get("imdbID", "")
    return {
        "found": True,
        "title": raw.get("Title", ""),
        "year": raw.get("Year", ""),
        "rated": raw.get("Rated", ""),
        "runtime": raw.get("Runtime", ""),
        "genre": raw.get("Genre", ""),
        "director": raw.get("Director", ""),
        "actors": raw.get("Actors", ""),
        "plot": raw.get("Plot", ""),
        "rating": raw.get("imdbRating", ""),
        "votes": raw.get("imdbVotes", ""),
        "poster": raw.get("Poster", "") if raw.get("Poster", "").startswith("http") else "",
        "imdb_id": imdb_id,
        "url": titles.imdb_title_url(imdb_id) if imdb_id else "",
    }


def lookup(title: str, year: str | None, api_key: str | None,
           cache_dir: str, timeout: float = 6.0) -> dict | None:
    """Return an enriched record for (title, year), or None if unavailable.

    None means "no enrichment" (no key, or network/lookup failed) — the caller
    should fall back to the search link. A dict with found=False means OMDb was
    reached but had no match.
    """
    if not title or not api_key:
        return None

    cache_path = _cache_file(cache_dir, title, year)
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached["data"]

    # Exact title match first; if OMDb doesn't find it, fall back to search and
    # take the top hit so the title is verified against the real database.
    params = {"t": title, "type": "movie"}
    if year:
        params["y"] = year
    raw = _omdb_get(params, api_key, timeout)

    data: dict
    if raw and raw.get("Response") == "True":
        data = _map(raw)
    else:
        srch = _omdb_get({"s": title, "type": "movie"}, api_key, timeout)
        first = (srch or {}).get("Search") or []
        if first and first[0].get("imdbID"):
            byid = _omdb_get({"i": first[0]["imdbID"], "plot": "short"}, api_key, timeout)
            data = _map(byid) if byid and byid.get("Response") == "True" else {"found": False}
        else:
            data = {"found": False}

    _write_cache(cache_path, data)
    return data
