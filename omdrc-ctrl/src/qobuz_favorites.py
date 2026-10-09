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
_membership = {}  # user ID -> (monotonic time, playlist IDs, album IDs, cover URLs, [(path, albums)])
_order_lock = threading.Lock()
_found = {}  # user ID -> (monotonic time, [album with "paths"]): the Library as find() reads it

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


def _membership_of(cat, user_id: str, own: list[dict]) -> tuple:
    """(album IDs in folders, folder cover URLs, [(folder path, albums)]),
    read from every own playlist and kept five minutes."""
    ids = tuple(sorted(str(p["id"]) + ":" + str(p.get("updated_at")) for p in own))
    with _membership_lock:
        cached = _membership.get(user_id)
    if cached and cached[1] == ids and time.monotonic() - cached[0] < 300:
        return cached[2], cached[3], cached[4]
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
    folders = [(legacy_path(p["name"]), albums) for p, albums in zip(own, groups)]
    with _membership_lock:
        _membership[user_id] = (time.monotonic(), ids, categorized, covers, folders)
    return categorized, covers, folders


def library_snapshot(cat, user_id: str, own: list[dict], cards: list[dict]) -> tuple[list[dict], dict]:
    """Unfiled album hearts and up to three cover images for each folder tile."""
    categorized, covers, _ = _membership_of(cat, user_id, own)
    unfiled = [a for a in cards if a["id"] not in categorized]
    return unfiled, {**covers, "Qobuz": [a["image"] for a in unfiled if a.get("image")][:3]}


def find(cat, user_id: str, text: str, limit: int = 200) -> list[dict]:
    """The Library's albums (in folders, or hearts not filed) holding every
    word of `text` in their title, artist, composer, label or folder path, each
    with the folder paths it is in ("Qobuz" for an unfiled heart).  Qobuz has
    no search within a user's library: this reads the same playlists the
    folder view reads, from the same cache."""
    words = re.sub(r"\s+", " ", (text or "").casefold()).split()
    if not words:
        return []
    with _membership_lock:
        cached = _found.get(user_id)
    if cached and time.monotonic() - cached[0] < 60:     # typed letter by letter
        library = cached[1]
    else:
        own = playlists(cat, user_id)
        _, _, folders = _membership_of(cat, user_id, own)
        found: dict[str, dict] = {}
        for path, albums in folders:
            for album in albums:
                entry = found.setdefault(album["id"], {
                    **{k: v for k, v in album.items() if k != "playlist_track_ids"}, "paths": []})
                if path not in entry["paths"]:
                    entry["paths"].append(path)
        for card in favorites(cat, user_id):
            found.setdefault(card["id"], {**card, "paths": ["Qobuz"]})
        library = list(found.values())
        with _membership_lock:
            _found[user_id] = (time.monotonic(), library)
    out = []
    for album in library:
        haystack = " ".join([str(album.get(k) or "") for k in
                             ("title", "version", "artist", "composer", "label")]
                            + album["paths"]).casefold()
        if all(word in haystack for word in words):
            out.append(album)
            if len(out) >= limit:
                break
    return out


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

    def rewrite_subtree(self, old: str, new: str | None) -> None:
        """Keep saved tile positions aligned with renamed or removed folders."""
        with _order_lock:
            try:
                with open(self.path, encoding="utf-8") as stream:
                    orders = json.load(stream).get("orders", {})
            except (OSError, ValueError, AttributeError):
                return
            if not isinstance(orders, dict):
                return
            def inside(path):
                return path == old or path.startswith(old + "/")
            def changed(path):
                return new + path[len(old):]
            old_parent = old.rpartition("/")[0]
            new_parent = new.rpartition("/")[0] if new is not None else None
            result = {}
            for parent, keys in orders.items():
                if inside(parent) and new is None:
                    continue
                mapped_keys = []
                for key in keys:
                    if key.startswith("f:") and inside(key[2:]):
                        if new is None or (key[2:] == old and parent == old_parent and
                                           new_parent != old_parent):
                            continue
                        key = "f:" + changed(key[2:])
                    mapped_keys.append(key)
                mapped_parent = changed(parent) if inside(parent) else parent
                result[mapped_parent] = mapped_keys
            if new is not None and new_parent != old_parent:
                destination = result.setdefault(new_parent, [])
                if "f:" + new not in destination:
                    destination.append("f:" + new)
            temporary = self.path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "orders": result}, stream, ensure_ascii=False)
            os.replace(temporary, self.path)


def invalidate(user_id: str) -> None:
    with _membership_lock:
        _membership.pop(user_id, None)
        _found.pop(user_id, None)


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


def folder_action(cat, user_id: str, path: str, action: str,
                  order: LibraryOrder, name: str = "", target: str = "") -> dict:
    """Rename, move or delete every owned playlist at and below a folder path."""
    path = valid_path(path)
    own = playlists(cat, user_id)
    matching = [p for p in own if legacy_path(p["name"]) == path or
                legacy_path(p["name"]).startswith(path + "/")]
    if not matching:
        raise QobuzError("Folder no longer exists")
    if action in ("rename", "move"):
        if action == "rename":
            if not isinstance(name, str) or not name.strip() or "/" in name:
                raise QobuzError("Enter one folder name without /")
            new = valid_path("/".join(path.split("/")[:-1] + [name.strip()]))
        else:
            target = valid_path(target)
            if target == path or target.startswith(path + "/"):
                raise QobuzError("A folder cannot be moved inside itself")
            if not any(legacy_path(p["name"]) == target or
                       legacy_path(p["name"]).startswith(target + "/") for p in own):
                raise QobuzError("Destination folder no longer exists")
            new = valid_path(target + "/" + path.rpartition("/")[2])
        if new == path:
            return {"path": path, "playlists": len(matching)}
        if new.casefold() == path.casefold():
            # Qobuz paths are case-sensitive on display; allow a case-only rename.
            pass
        elif any(legacy_path(p["name"]).casefold() == new.casefold() or
                 legacy_path(p["name"]).casefold().startswith(new.casefold() + "/")
                 for p in own if p not in matching):
            raise QobuzError("A folder with that name already exists")
        renamed_playlists = []
        try:
            for playlist in matching:
                old_name = playlist["name"]
                old_path = legacy_path(old_name)
                renamed = new + old_path[len(path):]
                valid_path(renamed)
                cat._call("playlist/update", {"playlist_id": str(playlist["id"]), "name": renamed})
                renamed_playlists.append((playlist, old_name))
        except QobuzError:
            for playlist, old_name in reversed(renamed_playlists):
                try:
                    cat._call("playlist/update", {"playlist_id": str(playlist["id"]), "name": old_name})
                except QobuzError:
                    pass
            raise
        order.rewrite_subtree(path, new)
        return {"path": new, "playlists": len(matching)}
    if action == "delete":
        for playlist in matching:
            cat._call("playlist/delete", {"playlist_id": str(playlist["id"])})
        order.rewrite_subtree(path, None)
        return {"path": path, "playlists": len(matching)}
    raise QobuzError("unknown folder action")
