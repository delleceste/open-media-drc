"""Classify a filesystem entry so the UI knows what a tap does.

  bdmv  -> Blu-ray rip folder  (BDMV/index.bdmv present) -> play as a unit
  dvd   -> DVD rip folder      (VIDEO_TS present)         -> play as a unit
  file  -> a single playable media file
  dir   -> an ordinary directory to descend into
  None  -> ignore (not playable, not a directory we care about)
"""
import os

# Extensions we treat as directly playable media files. mpv opens far more than
# this, but this keeps the browser from listing stray non-video files.
PLAYABLE_EXTS: frozenset[str] = frozenset({
    "mkv", "mp4", "m4v", "m2ts", "mts", "ts", "avi", "mov", "wmv",
    "webm", "flv", "mpg", "mpeg", "vob", "ogv", "iso",
})


def is_bluray_rip(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "BDMV", "index.bdmv"))


def is_dvd_rip(path: str) -> bool:
    return os.path.isdir(os.path.join(path, "VIDEO_TS"))


def classify(path: str) -> str | None:
    if os.path.isdir(path):
        if is_bluray_rip(path):
            return "bdmv"
        if is_dvd_rip(path):
            return "dvd"
        return "dir"
    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext in PLAYABLE_EXTS:
            return "file"
    return None


# Order entries by kind first (folders/rips before plain files), then by name.
_KIND_ORDER = {"dir": 0, "bdmv": 1, "dvd": 1, "file": 2}


def sort_key(entry: dict) -> tuple:
    return (_KIND_ORDER.get(entry["type"], 9), entry["name"].lower())
