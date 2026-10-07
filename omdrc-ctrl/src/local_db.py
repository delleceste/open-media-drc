"""The local collection's settings and status, for the kiosk's Local database page.

MPD keeps the index; this module only knows where the music lives *as omdrcctrl
sees it* (the DR14 reports are read from, and calculated in, that tree) and how
the last scan went.  The path is kept per host, like the meter calibrations are
per network: one site's files can describe several boxes, and a library mounted
at /srv/music on one is at /home/me/Music on another.  Without an entry for this
host the path MPD's own configuration names is used.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time

SETTINGS_FILE = "local-library.json"
SCAN_STATUS_FILE = "local-scan.txt"       # written by omdrc-mpd-update-dr14.sh

AUDIO = (".flac", ".mp3", ".ogg", ".opus", ".wav", ".m4a", ".ape", ".wv", ".aiff", ".aif")
REPORT = "dr14.txt"

# Counting folders walks the whole tree, which on a big library on a slow disk
# takes a while: it is done once in the background and kept for a few minutes.
COUNT_TTL = 300.0
COUNT_LIMIT = 200000

# A scan marked running for longer than this was killed (a reboot, say), not slow.
STALE_SCAN = 12 * 3600

_lock = threading.Lock()
_counts: dict[str, dict] = {}            # path -> {"at": t, "folders": n, "reports": n} | {"busy": True}


def host() -> str:
    return socket.gethostname().split(".")[0] or "localhost"


def _read(state_dir: str) -> dict:
    try:
        with open(os.path.join(state_dir, SETTINGS_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def configured_path(state_dir: str, name: str | None = None) -> str:
    entry = (_read(state_dir).get("hosts") or {}).get(name or host())
    return entry.get("music_directory", "") if isinstance(entry, dict) else ""


def set_path(state_dir: str, path: str, name: str | None = None) -> None:
    """Remember `path` for this host; an empty one forgets it (MPD's own is used)."""
    data = _read(state_dir)
    hosts = data.setdefault("hosts", {})
    if not isinstance(hosts, dict):
        hosts = data["hosts"] = {}
    if path:
        hosts[name or host()] = {"music_directory": path}
    else:
        hosts.pop(name or host(), None)
    os.makedirs(state_dir, exist_ok=True)
    target = os.path.join(state_dir, SETTINGS_FILE)
    tmp = f"{target}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, target)


def music_directory(state_dir: str, mpd_default) -> str | None:
    """The music root to read DR14 reports from: this host's, else MPD's."""
    return configured_path(state_dir) or mpd_default() or None


def validate(path: str) -> str:
    """The path as it will be stored, or ValueError saying what is wrong."""
    path = (path or "").strip()
    if not path:
        return ""
    if "\0" in path or "\n" in path:
        raise ValueError("the path contains a control character")
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        raise ValueError("give an absolute path (starting with /)")
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        raise ValueError(f"{path} is not a directory on this host")
    return path


def _count(path: str) -> dict:
    folders = reports = seen = 0
    for _root, _dirs, files in os.walk(path):
        seen += 1
        if seen > COUNT_LIMIT:
            break
        if any(name.lower().endswith(AUDIO) for name in files):
            folders += 1
            if REPORT in files:
                reports += 1
    return {"at": time.time(), "folders": folders, "reports": reports, "truncated": seen > COUNT_LIMIT}


def counts(path: str, fresh: bool = False) -> dict:
    """Folders with audio and how many have a dr14.txt: {"busy": True} while the
    first count runs, then the last one (recounted in the background when old)."""
    with _lock:
        have = _counts.get(path)
        stale = None
        if have and not have.get("busy") and not fresh and time.time() - have["at"] < COUNT_TTL:
            return have
        if have and have.get("busy"):
            return {"busy": True}
        _counts[path] = {"busy": True, "previous": have}
        stale = have

    def work():
        try:
            result = _count(path)
        except OSError:
            result = {"at": time.time(), "folders": 0, "reports": 0, "error": True}
        with _lock:
            _counts[path] = result
    threading.Thread(target=work, daemon=True).start()
    return stale or {"busy": True}


def scan_status(state_dir: str) -> dict:
    """The last DR14 scan, from the line the script leaves: `running <t>` or
    `done <t> <calculated>`."""
    try:
        with open(os.path.join(state_dir, SCAN_STATUS_FILE), encoding="utf-8") as f:
            parts = f.read().split()
    except OSError:
        return {"state": "never"}
    try:
        if parts and parts[0] == "running":
            since = int(parts[1])
            if time.time() - since > STALE_SCAN:
                return {"state": "interrupted", "since": since}
            return {"state": "running", "since": since}
        if parts and parts[0] == "done":
            return {"state": "done", "at": int(parts[1]), "calculated": int(parts[2])}
    except (IndexError, ValueError):
        pass
    return {"state": "never"}


def status(state_dir: str, mpd_default, dr14_available: bool) -> dict:
    configured = configured_path(state_dir)
    mpd_path = mpd_default() or ""
    path = configured or mpd_path
    exists = bool(path) and os.path.isdir(path)
    return {
        "ok": True, "host": host(), "path": path,
        "source": "configured" if configured else ("mpd" if mpd_path else "none"),
        "configured": configured, "mpd_path": mpd_path, "exists": exists,
        "dr14": dr14_available,
        "counts": counts(path) if exists else None,
        "scan": scan_status(state_dir),
    }
