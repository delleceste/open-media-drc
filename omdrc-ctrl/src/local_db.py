"""The local collection's settings and status, for the kiosk's Local database page.

MPD keeps the index; this module only reads where the music lives from MPD's
own configuration (`music_directory`, the single source of truth: the DR14
reports are read from, and calculated in, that tree) and how the last scan went.
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import socket
import subprocess
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
            if len(parts) >= 5 and parts[2] == "cue":
                out.update(phase="cue", done=int(parts[3]), total=int(parts[4]))
            elif len(parts) >= 4:                # folders done / to do, once counted
                out["done"], out["total"] = int(parts[2]), int(parts[3])
            return out
        if parts and parts[0] == "done":
            return {"state": "done", "at": int(parts[1]), "calculated": int(parts[2]),
                    "failed": int(parts[3]) if len(parts) > 3 else 0}
        if parts and parts[0] == "stopped":
            out = {"state": "stopped", "at": int(parts[1])}
            if len(parts) >= 5 and parts[2] == "cue":
                out.update(phase="cue", done=int(parts[3]), total=int(parts[4]))
            elif len(parts) >= 4:
                out["done"], out["total"] = int(parts[2]), int(parts[3])
            return out
    except (IndexError, ValueError):
        pass
    return {"state": "never"}


def scan_activity(root: str) -> list[dict]:
    """Live scanner and its children, including the current meter and decoder."""
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid,ppid,%cpu,etime,command"], capture_output=True,
            text=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    processes = {}
    for line in result.stdout.splitlines()[1:]:
        fields = line.split(None, 4)
        if len(fields) != 5:
            continue
        try:
            pid, parent = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        processes[pid] = (parent, fields[2], fields[3], fields[4])
    scanners = []
    for pid, (_, _, _, command) in processes.items():
        try:
            words = shlex.split(command)
        except ValueError:
            continue
        if any(os.path.basename(word) == "omdrc-mpd-update-dr14.sh" and
               words[i + 1:i + 3] == ["--calculate", root]
               for i, word in enumerate(words)):
            scanners.append(pid)
    if not scanners:
        return []
    selected = set(scanners)
    for _ in range(3):  # scanner -> meter -> ffmpeg; allow one wrapper level
        selected.update(pid for pid, (parent, _, _, _) in processes.items() if parent in selected)
    def kind(command):
        if "omdrc-cue-split.py" in command:
            return "CUE splitter"
        if "shnsplit" in command:
            return "Splitter"
        if "shnhash" in command:
            return "Audio verifier"
        if "drmeter.py" in command:
            return "DR meter"
        if "ffmpeg" in command:
            return "Decoder"
        return "Scanner"
    order = {"Scanner": 0, "CUE splitter": 1, "Splitter": 2, "Audio verifier": 2,
             "DR meter": 2, "Decoder": 3}
    return [{"kind": kind(processes[pid][3]), "pid": pid, "parent": processes[pid][0],
             "cpu": processes[pid][1], "elapsed": processes[pid][2],
             "command": processes[pid][3]}
            for pid in sorted(selected, key=lambda p: (order[kind(processes[p][3])], p))]


def stop_scan(state_dir: str, root: str) -> dict:
    """Stop only the running scan for this music root and its descendants."""
    scan = scan_status(state_dir)
    if scan["state"] != "running":
        return {"ok": True, "message": "The scan is no longer running"}
    activity = scan_activity(root)
    selected = {item["pid"] for item in activity}
    scanners = [item["pid"] for item in activity if item["parent"] not in selected]
    if not scanners and time.time() - scan["since"] < 5:
        return {"ok": False, "error": "The scan is still starting; try Stop again"}

    def send(pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass

    # Freeze the coordinator so it cannot start another album while its
    # children are being stopped. The decoder goes first, then the meter.
    for pid in scanners:
        send(pid, signal.SIGSTOP)
    descendants = [item["pid"] for item in reversed(activity)
                   if item["pid"] not in scanners]
    for pid in descendants:
        send(pid, signal.SIGTERM)
    for pid in scanners:
        send(pid, signal.SIGTERM)
        send(pid, signal.SIGCONT)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and scan_activity(root):
        time.sleep(0.1)
    for item in scan_activity(root):
        send(item["pid"], signal.SIGKILL)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and scan_activity(root):
        time.sleep(0.1)
    if scan_activity(root):
        return {"ok": False, "error": "A scan process did not stop; check Scan activity"}
    phase = "cue " if scan.get("phase") == "cue" else ""
    with open(os.path.join(state_dir, SCAN_STATUS_FILE), "w", encoding="utf-8") as f:
        f.write(f"stopped {int(time.time())} {phase}{scan.get('done', 0)} {scan.get('total', 0)}\n")
    with open(os.path.join(state_dir, SCAN_LOG_FILE), "a", encoding="utf-8") as f:
        f.write("Scan stopped by user\n")
    return {"ok": True, "message": "Scan stopped; Rescan is available"}


ARTIST_CHARS = 28
ALBUM_CHARS = 40
MESSAGE_CHARS = 100
_YEAR = re.compile(r"[(\[]?(19|20)\d\d[)\]]?")
_FORMAT = re.compile(r"\s*[(\[][^)\]]*\b(flac|mp3|wav|alac|aac|ape|wv|hi-?res|\d+\s*-?\s*bits?|\d+(\.\d+)?\s*k(hz)?)\b[^)\]]*[)\]]\s*$", re.I)
_DISC = re.compile(r"(cd|disc|disk)\s*\d+", re.I)
_LINE = re.compile(r"^\[([^\]]+)\] (.*)$")


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip(" -,.") + "…"


def album_label(folder: str, artist: str = "", album: str = "") -> str:
    """`Artist — Album`, each cut to a readable length: from the tags when the
    meter read them, otherwise guessed from the folder's name (`Artist - Year -
    Album`, `Year - Album` under an artist folder, ...)."""
    disc = ""
    if not (artist or album):
        parts = [p for p in folder.split("/") if p]
        if len(parts) >= 2 and _DISC.fullmatch(parts[-1]):    # Album/CD2: the album, then the disc
            disc = " · " + " ".join(parts.pop().split())
        name = _FORMAT.sub("", parts[-1] if parts else folder).lstrip("!").strip()
        bits = [b.strip() for b in name.split(" - ") if b.strip() and not _YEAR.fullmatch(b.strip())]
        if len(bits) >= 2:
            artist, album = bits[0], " - ".join(bits[1:])
        else:
            album = bits[0] if bits else name
            artist = parts[-2] if len(parts) >= 3 else ""
    return " — ".join(x for x in (_short(artist, ARTIST_CHARS), _short(album, ALBUM_CHARS)) if x) + disc


def _readable(line: str, root: str) -> str:
    """One log line as the page shows it: names instead of paths."""
    if root:
        line = line.replace(root.rstrip("/") + "/", "")
    m = _LINE.match(line)
    if not m or "/" not in m.group(1):                 # not a folder's line
        return "    " + _short(line, MESSAGE_CHARS) if line.startswith("    ") else line
    tag, body = m.groups()
    word, _, rest = body.partition(" ")
    rest = rest.lstrip()
    if word == "measuring":
        return f"[{tag}] measuring {album_label(rest)}"
    fields = rest.split("\t")
    if word == "FAILED":
        if len(fields) == 1:                           # before the tab-separated form
            fields = rest.split(": ", 1)
        why = fields[1] if len(fields) > 1 else ""
        return f"[{tag}] FAILED  {album_label(fields[0])}" + (f": {_short(why, MESSAGE_CHARS)}" if why else "")
    fields += ["", ""]
    return f"[{tag}] {word}  {album_label(fields[0], fields[1], fields[2])}"


def scan_log(state_dir: str, root: str = "") -> list[str]:
    """The last lines of the scan log (the file is restarted by each rescan),
    for reading: a folder's or a step's progress lines give way to its latest
    one (`measuring` to the result), and names replace paths."""
    try:
        with open(os.path.join(state_dir, SCAN_LOG_FILE), encoding="utf-8", errors="replace") as f:
            raw = [line.rstrip("\n") for line in f]
    except OSError:
        return []
    out: list[str] = []
    last_tag = None
    for line in raw:
        m = _LINE.match(line)
        tag = m.group(1) if m else None
        if tag is not None and tag == last_tag:
            out[-1] = line
        else:
            out.append(line)
        if not line.startswith("    "):                # a skipped track keeps its folder's place
            last_tag = tag
    return [_readable(line, root) for line in out[-LOG_LINES:]]


def status(state_dir: str, mpd_default, conf: str | None = None) -> dict:
    path = mpd_default() or ""
    exists = bool(path) and os.path.isdir(path)
    scan = scan_status(state_dir)
    return {
        "ok": True, "host": host(), "path": path, "exists": exists, "conf": conf or "",
        # a finished scan added reports: count again rather than show the old figure
        "counts": counts(path, since=scan.get("at", 0)) if exists else None,
        "scan": scan, "log": scan_log(state_dir, path),
        "activity": scan_activity(path) if scan["state"] == "running" and path else [],
    }
