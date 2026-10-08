"""The local collection's settings and status, for the kiosk's Local database page.

MPD keeps the index; this module only reads where the music lives from MPD's
own configuration (`music_directory`, the single source of truth: the DR14
reports are read from, and calculated in, that tree) and how the last scan went.
"""
from __future__ import annotations

import os
import socket
import threading
import time

SCAN_STATUS_FILE = "local-scan.txt"       # written by omdrc-mpd-update-dr14.sh
SCAN_LOG_FILE = "local-scan.log"         # one line per folder, same script
LOG_LINES = 200

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


def _count(path: str) -> dict:
    folders = reports = seen = 0
    # followlinks: a disk linked into the library counts like any folder
    for _root, _dirs, files in os.walk(path, followlinks=True):
        seen += 1
        if seen > COUNT_LIMIT:
            break
        if any(name.lower().endswith(AUDIO) for name in files):
            folders += 1
            if REPORT in files:
                reports += 1
    return {"at": time.time(), "folders": folders, "reports": reports, "truncated": seen > COUNT_LIMIT}


def counts(path: str, fresh: bool = False, since: float = 0) -> dict:
    """Folders with audio and how many have a dr14.txt: {"busy": True} while the
    first count runs, then the last one (recounted in the background when old,
    when `fresh`, or when made before `since`)."""
    with _lock:
        have = _counts.get(path)
        stale = None
        if (have and not have.get("busy") and not fresh and have["at"] >= since
                and time.time() - have["at"] < COUNT_TTL):
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
    """The last DR14 scan, from the line the script leaves: `running <t> [<done> <total>]`
    or `done <t> <calculated> [<failed>]`."""
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
            out = {"state": "running", "since": since}
            if len(parts) >= 4:                  # folders done / to do, once counted
                out["done"], out["total"] = int(parts[2]), int(parts[3])
            return out
        if parts and parts[0] == "done":
            return {"state": "done", "at": int(parts[1]), "calculated": int(parts[2]),
                    "failed": int(parts[3]) if len(parts) > 3 else 0}
    except (IndexError, ValueError):
        pass
    return {"state": "never"}


def scan_log(state_dir: str) -> list[str]:
    """The last lines of the scan log (the file is cleared by each scan)."""
    try:
        with open(os.path.join(state_dir, SCAN_LOG_FILE), encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f.readlines()[-LOG_LINES:]]
    except OSError:
        return []


def status(state_dir: str, mpd_default, conf: str | None = None) -> dict:
    path = mpd_default() or ""
    exists = bool(path) and os.path.isdir(path)
    scan = scan_status(state_dir)
    return {
        "ok": True, "host": host(), "path": path, "exists": exists, "conf": conf or "",
        # a finished scan added reports: count again rather than show the old figure
        "counts": counts(path, since=scan.get("at", 0)) if exists else None,
        "scan": scan, "log": scan_log(state_dir),
    }
