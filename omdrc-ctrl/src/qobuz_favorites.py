"""Album folders backed by Qobuz playlists named with slash-separated paths.

One playlist track is an album anchor. Existing playlists stay intact, including
playlists with several tracks from the same album. Album hearts are Qobuz's own
flat favorites; playlist membership supplies the reversible folder path.
"""
from __future__ import annotations

import re
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from qobuz_search import QobuzError, album_card

PAGE = 500
MONTHS = {"GEN": "January", "MAR": "March", "JUN": "June", "SEP": "September",
          "DEC": "December"}
_membership_lock = threading.Lock()
_membership = {}  # user ID -> (monotonic time, playlist IDs, album IDs, cover URLs)
_order_lock = threading.Lock()

CLASSICAL_NAMES = {
    "* classica *": "Classical/**",
    "* classical": "Classical/**",
    "classical 2025": "Classical/2025",
    "discover dg": "Classical/Deutsche Grammophon",
    "deutsche grammophon - gramophone label of the year 2021":
        "Classical/Deutsche Grammophon/Gramophone Label of the Year 2021",
    "discover bis": "Classical/BIS",
    "discover ecm classical": "Classical/ECM",
    "discover reference recordings": "Classical/Reference Recordings",
    "label: aparte": "Classical/Aparté",
    "multi awarded": "Classical/Multi Awarded",
    "organ": "Classical/organ",
    "savall": "Classical/Savall",
}


def legacy_path(name: str) -> str:
    """Give old magazine playlists a useful tree until they are renamed."""
    name = name.strip()
    if "/" in name:
        return name
    if name.casefold() in CLASSICAL_NAMES:
        return CLASSICAL_NAMES[name.casefold()]
    upper = name.upper()
    if upper == "BUJUL-AUG26":
        return "Blow Up/2026/JUL-AUG"
    match = re.fullmatch(r"BU([A-Z]{3})(\d{2})", upper)
    if match and match[1] in MONTHS:
        return f"Blow Up/20{match[2]}/{MONTHS[match[1]]}"
    match = re.fullmatch(r"blow up (?:playlist )?(20\d{2})", name, re.I)
    if match:
        return f"Blow Up/{match[1]}/Playlist"
    match = re.fullmatch(r"blow up (april|may|marzo) (20\d{2})", name, re.I)
    if match:
        return f"Blow Up/{match[2]}/{ {'april': 'April', 'may': 'May', 'marzo': 'March'}[match[1].lower()] }"
    match = re.fullmatch(r"gramophone awards (20\d{2})", name, re.I)
    if match:
        return f"Classical/Gramophone/Awards/{match[1]}"
    match = re.fullmatch(r"gramophone nov\.(20\d{2})", name, re.I)
    if match:
        return f"Classical/Gramophone/{match[1]}/November"
    return name


def valid_path(value: str) -> str:
    parts = [p.strip() for p in str(value).split("/")]
    if not parts or len(parts) > 8 or any(not p or len(p) > 80 or p in (".", "..") for p in parts):
        raise QobuzError("Use 1–8 nonempty folder names separated by / (80 characters each)")
    path = "/".join(parts)
    if len(path) > 255 or path.casefold() == "qobuz" or path.casefold().startswith("qobuz/"):
        raise QobuzError("That path is reserved or too long")
    return path


def _items(cat, endpoint: str, params: dict, key: str) -> list[dict]:
    out = []
    for offset in range(0, 100000, PAGE):
        block = (cat._call(endpoint, {**params, "limit": PAGE, "offset": offset}).get(key) or {})
        items = block.get("items") or []
        out.extend(items)
        if len(items) < PAGE or len(out) >= block.get("total", 0):
            return out
    raise QobuzError("Qobuz library is too large to read safely")


def playlists(cat, user_id: str) -> list[dict]:
    return [p for p in _items(cat, "playlist/getUserPlaylists", {"user_id": user_id}, "playlists")
            if str((p.get("owner") or {}).get("id")) == str(user_id)]


def favorites(cat, user_id: str) -> list[dict]:
    return [album_card(a) for a in _items(cat, "favorite/getUserFavorites",
                                               {"user_id": user_id, "type": "albums"}, "albums")]


def library_snapshot(cat, user_id: str, own: list[dict], cards: list[dict]) -> tuple[list[dict], dict]:
    """Unfiled album hearts and up to three cover images for each folder tile."""
    ids = tuple(sorted(str(p["id"]) + ":" + str(p.get("updated_at")) for p in own))
    with _membership_lock:
        cached = _membership.get(user_id)
    if cached and cached[1] == ids and time.monotonic() - cached[0] < 300:
        categorized, covers = cached[2], cached[3]
    else:
        with ThreadPoolExecutor(max_workers=8) as pool:
            groups = list(pool.map(lambda p: playlist_albums(cat, str(p["id"])), own))
        categorized = {a["id"] for group in groups for a in group}
        covers, seen = {}, {}
        for playlist, albums in zip(own, groups):
            parts = legacy_path(playlist["name"]).split("/")
            for length in range(1, len(parts) + 1):
                path = "/".join(parts[:length])
                images, ids_seen = covers.setdefault(path, []), seen.setdefault(path, set())
                for album in albums:
                    if len(images) >= 3:
                        break
                    if album["id"] not in ids_seen and album.get("image"):
                        ids_seen.add(album["id"])
                        images.append(album["image"])
        with _membership_lock:
            _membership[user_id] = (time.monotonic(), ids, categorized, covers)
    unfiled = [a for a in cards if a["id"] not in categorized]
    return unfiled, {**covers, "Qobuz": [a["image"] for a in unfiled if a.get("image")][:3]}


class LibraryOrder:
    """Per-account tile order in the service state directory, shared by devices."""

    def __init__(self, path: str):
        self.path = path

    def all(self) -> dict[str, list[str]]:
        with _order_lock:
            try:
                with open(self.path, encoding="utf-8") as stream:
                    orders = json.load(stream).get("orders", {})
                return {p: keys for p, keys in orders.items()
                        if isinstance(p, str) and isinstance(keys, list)}
            except (OSError, ValueError, AttributeError):
                return {}

    def save(self, parent: str, keys: list[str]) -> dict[str, list[str]]:
        if parent != "" and parent != "Qobuz":
            valid_path(parent)
        if (not isinstance(keys, list) or len(keys) > 2000 or
                any(not isinstance(k, str) or len(k) > 300 or
                    not (k.startswith("f:") or re.fullmatch(r"a:[0-9A-Za-z]+", k))
                    for k in keys) or len(set(keys)) != len(keys)):
            raise QobuzError("invalid tile order")
        with _order_lock:
            try:
                with open(self.path, encoding="utf-8") as stream:
                    orders = json.load(stream).get("orders", {})
            except (OSError, ValueError, AttributeError):
                orders = {}
            if not isinstance(orders, dict):
                orders = {}
            orders[parent] = keys
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            temporary = self.path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "orders": orders}, stream, ensure_ascii=False)
            os.replace(temporary, self.path)
            return orders


def invalidate(user_id: str) -> None:
    with _membership_lock:
        _membership.pop(user_id, None)


def playlist_albums(cat, playlist_id: str) -> list[dict]:
    tracks = _items(cat, "playlist/get", {"playlist_id": playlist_id, "extra": "tracks"}, "tracks")
    albums = {}
    for track in tracks:
        raw = track.get("album") or {}
        album_id = str(raw.get("id") or "")
        if not album_id:
            continue
        if album_id not in albums:
            albums[album_id] = {**album_card(raw), "playlist_track_ids": []}
        if track.get("playlist_track_id") is not None:
            albums[album_id]["playlist_track_ids"].append(str(track["playlist_track_id"]))
    return list(albums.values())


def add(cat, user_id: str, album_id: str, path: str) -> dict:
    path = valid_path(path)
    album = cat.album(album_id)
    own = playlists(cat, user_id)
    matching = [p for p in own if legacy_path(p["name"]).casefold() == path.casefold()]
    if matching:
        for existing in matching:
            playlist_id = str(existing["id"])
            if any(a["id"] == album_id for a in playlist_albums(cat, playlist_id)):
                return {"playlist_id": playlist_id, "path": path, "already_present": True}
        primary = max(matching, key=lambda p: (p.get("tracks_count") or 0, int(p["id"])))
        playlist_id = str(primary["id"])
    track = next((t for t in album.get("track_list", []) if t.get("streamable") and t.get("id")), None)
    if not track:
        raise QobuzError("This album has no playable track to use as a folder entry")
    if not matching:
        created = cat._call("playlist/create", {"name": path, "is_public": "false"})
        playlist_id = str(created.get("id") or "")
        if not playlist_id:
            raise QobuzError("Qobuz did not return a new playlist ID")
    cat._call("playlist/addTracks", {"playlist_id": playlist_id, "track_ids": str(track["id"])})
    return {"playlist_id": playlist_id, "path": path, "already_present": False}


def remove(cat, user_id: str, album_id: str, playlist_id: str) -> bool:
    if not any(str(p["id"]) == playlist_id for p in playlists(cat, user_id)):
        raise QobuzError("Playlist is not owned by this Qobuz account")
    album = next((a for a in playlist_albums(cat, playlist_id) if a["id"] == album_id), None)
    if not album:
        raise QobuzError("Album is not in this folder")
    ids = album["playlist_track_ids"]
    if not ids:
        raise QobuzError("Qobuz did not supply IDs for these playlist tracks")
    cat._call("playlist/deleteTracks", {"playlist_id": playlist_id,
                                        "playlist_track_ids": ",".join(ids)})
    remaining = cat._call("playlist/get", {"playlist_id": playlist_id, "extra": "tracks",
                                           "limit": 1}).get("tracks") or {}
    if remaining.get("total") == 0:
        cat._call("playlist/delete", {"playlist_id": playlist_id})
        return True
    return False
