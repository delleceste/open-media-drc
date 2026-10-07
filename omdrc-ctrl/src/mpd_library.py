"""Small MPD protocol client for searching and queueing the local collection."""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
import posixpath
import re
import socket
import urllib.parse


class MPDError(RuntimeError):
    pass


_music_directory: str | None = None


def set_music_directory(path: str | None) -> None:
    global _music_directory
    _music_directory = path


def album_folder(uri: str) -> str:
    """The folder an indexed track lives in.  A track of a cue sheet is
    addressed inside the sheet, as if it were a folder
    (`Album/Album.cue/track0003`): its album is the folder holding the sheet."""
    folder = posixpath.dirname(uri)
    if folder.lower().endswith(".cue"):
        folder = posixpath.dirname(folder)
    return folder


def is_cue_track(uri: str) -> bool:
    return posixpath.dirname(uri).lower().endswith(".cue")


def _dr14_average(uri: str) -> int | None:
    """Read the album's DR14 T.meter average from its conventional report."""
    if not _music_directory:
        return None
    report = Path(_music_directory, album_folder(uri)) / "dr14.txt"
    try:
        text = report.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # DR14 T.meter reports the album average as `DR = 12` in CLI output and
    # as `Official DR value: DR12` in its generated dr14.txt table.
    matches = re.findall(r"(?:Official DR value:\s*DR|^\s*DR\s*=\s*)(\d+)", text, re.I | re.M)
    return int(matches[-1]) if matches else None


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


def _connect(timeout: float = 3):
    s = socket.create_connection(("127.0.0.1", 6600), timeout=timeout)
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
        if "dr" not in card:
            card["dr"] = _dr14_average(t["file"])
        if not card["image"]:
            art_url = "/qobuz/local/art?file=" + urllib.parse.quote(t["file"], safe="")
            card["image"] = card["image_large"] = art_url
        card["track_count"] += 1
    return list(albums.values())


def albumart(uri: str, max_bytes: int = 16 * 1024 * 1024) -> tuple[bytes, str]:
    """Read MPD's external album art in binary-safe 8 KiB protocol chunks."""
    if (not uri or uri.startswith("/") or "\\" in uri or
            any(part in ("", ".", "..") for part in uri.split("/"))):
        raise MPDError("invalid library path")
    s = _connect(timeout=30)
    output = bytearray()
    total = None
    try:
        f = s.makefile("rwb", buffering=0)
        while total is None or len(output) < total:
            offset = len(output)
            f.write(("albumart " + _quote(uri) + f" {offset}\n").encode())
            headers = {}
            while True:
                line = f.readline()
                if not line:
                    raise MPDError("MPD closed the connection while reading album art")
                if line.startswith(b"ACK"):
                    raise MPDError(line.decode("utf-8", "replace").strip())
                if line == b"OK\n":
                    if total is None:
                        raise MPDError("album art not found")
                    break
                if line.startswith(b"binary: "):
                    amount = int(line.split(b":", 1)[1].strip())
                    total = int(headers.get("size", "0"))
                    if total <= 0 or total > max_bytes:
                        raise MPDError("album art is empty or too large")
                    chunk = f.read(amount)
                    if len(chunk) != amount or f.read(1) != b"\n":
                        raise MPDError("incomplete MPD album art response")
                    output.extend(chunk)
                    ending = f.readline()
                    if ending == b"OK\n":
                        break
                    if ending.startswith(b"ACK"):
                        raise MPDError(ending.decode("utf-8", "replace").strip())
                    raise MPDError("invalid MPD album art completion")
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                if ": " in text:
                    key, value = text.split(": ", 1)
                    headers[key] = value
            if not output:
                raise MPDError("album art not found")
        if len(output) != total:
            raise MPDError("incomplete MPD album art")
    finally:
        s.close()
    if output.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif output.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif output[:4] == b"RIFF" and output[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = mimetypes.guess_type(uri)[0] or "application/octet-stream"
    return bytes(output), mime


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
