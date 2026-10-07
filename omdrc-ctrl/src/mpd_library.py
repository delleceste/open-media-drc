"""Small MPD protocol client for searching and queueing the local collection."""
from __future__ import annotations

import hashlib
import re
import socket


class MPDError(RuntimeError):
    pass


def _quote(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _command(sock, command: str) -> list[dict[str, str]]:
    f = sock.makefile("rwb", buffering=0)
    f.write((command + "\n").encode())
    rows, row = [], {}
    while True:
        line = f.readline().decode("utf-8", "replace").rstrip("\r\n")
        if line == "OK":
            if row: rows.append(row)
            return rows
        if line.startswith("ACK"):
            raise MPDError(line)
        if not line:
            raise MPDError("MPD closed the connection")
        if ": " not in line: continue
        key, value = line.split(": ", 1)
        if key == "file":
            if row: rows.append(row)
            row = {}
        row.setdefault(key, value)


def _connect():
    s = socket.create_connection(("127.0.0.1", 6600), timeout=3)
    greeting = s.makefile("rb").readline()
    if not greeting.startswith(b"OK MPD"):
        s.close(); raise MPDError("MPD protocol unavailable")
    return s


def search(text: str, limit: int = 5000) -> list[dict]:
    words = re.findall(r"[^\s]+", text or "")[:8]
    if not words: return []
    s = _connect()
    try:
        tracks = None
        for word in words:
            found = _command(s, "search any " + _quote(word))
            tracks = found if tracks is None else [x for x in tracks if x.get("file") in {y.get("file") for y in found}]
            if not tracks: break
    finally: s.close()
    albums = {}
    for raw in (tracks or [])[:limit]:
        t = {key.casefold(): value for key, value in raw.items()}
        title, artist, album = t.get("album", "Unknown album"), t.get("albumartist", t.get("artist", "")), t.get("album", "Unknown album")
        key = (artist.casefold(), album.casefold(), t.get("date", ""))
        card = albums.setdefault(key, {"id": "local:" + hashlib.sha256("\0".join(key).encode()).hexdigest()[:24], "source": "local", "title": album, "artist": artist, "year": (t.get("date", "")[:4] or None), "label": "", "image": "", "image_large": "", "streamable": True, "tracks": [], "track_count": 0})
        card["tracks"].append({"file": t["file"], "title": t.get("title", ""), "duration": int(float(t.get("duration", 0) or 0))})
        card["track_count"] += 1
    return list(albums.values())


def queue(album_id: str, mode: str, tracks: list[dict]) -> int:
    if mode not in ("replace", "append") or not tracks: raise MPDError("invalid local album request")
    s = _connect()
    try:
        if mode == "replace": _command(s, "clear")
        added = 0
        for track in tracks:
            _command(s, "add " + _quote(track["file"]))
            added += 1
        if mode == "replace": _command(s, "play 0")
        return added
    finally: s.close()
