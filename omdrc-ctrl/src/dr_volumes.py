"""Which drive a local album lives on, so that a disk moved between boxes keeps
its albums' keys.

A local album was keyed by its folder relative to MPD's music directory, which
is one box's view of it: the same USB disk may be mounted elsewhere on another
box, or reached through a link.  An album on a drive that carries a marker is
keyed instead `vol:<volume id>:<folder relative to the drive's root>`, the same
on every box the drive is plugged into.

The marker is a file at the root of the filesystem, `.omdrc-volume`, holding a
random id.  The first box that sees the drive writes it (`uuidgen >
<mount>/.omdrc-volume` does the same by hand on a read-only one).  The root
filesystem never gets one: what lives there does not move.  A folder whose
drive has no marker keeps its `local:` key.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid

MARKER = ".omdrc-volume"
PREFIX = "vol:"
_ID_RE = re.compile(r"^[0-9a-f][0-9a-f-]{7,63}$")
# a mount's id is read again after this long: a drive may be swapped
TTL = 30.0


def parse(key: str) -> tuple[str, str] | None:
    """(volume id, folder inside it) of a `vol:` key."""
    if not key.startswith(PREFIX):
        return None
    vid, sep, rel = key[len(PREFIX):].partition(":")
    return (vid, rel) if sep and _ID_RE.match(vid) else None


class Volumes:
    def __init__(self, create: bool = True) -> None:
        self.create = create
        self._lock = threading.Lock()
        self._writing = threading.Lock()
        self._by_dev: dict[int, tuple[str, str | None, float]] = {}
        self._by_id: dict[str, str] = {}

    def mount_of(self, path: str) -> str:
        """The root of the filesystem `path` (a real path) is on."""
        dev = os.stat(path).st_dev
        while path != os.sep:
            parent = os.path.dirname(path)
            if os.stat(parent).st_dev != dev:
                break
            path = parent
        return path

    def _marker(self, mount: str) -> str | None:
        try:
            with open(os.path.join(mount, MARKER), encoding="ascii", errors="replace") as f:
                vid = f.read(100).strip().lower()
        except OSError:
            return None
        return vid if _ID_RE.match(vid) else None

    def _read(self, mount: str) -> str | None:
        """The drive's id, writing a marker on a drive that has none yet.

        One thread at a time: two writing their own ids at once would each
        believe theirs (an exclusive create is not atomic on every fusefs).
        What is on the drive afterwards is the id, whoever wrote it."""
        with self._writing:
            vid = self._marker(mount)
            marker = os.path.join(mount, MARKER)
            if vid or not self.create or mount == os.sep or os.path.lexists(marker):
                return vid
            try:
                with open(marker, "x", encoding="ascii") as f:
                    f.write(str(uuid.uuid4()) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except FileExistsError:
                pass
            except OSError:
                return None
            return self._marker(mount)

    def volume(self, path: str) -> tuple[str, str] | None:
        """(mount, volume id) of the drive holding `path`, None without a marker."""
        try:
            real = os.path.realpath(path)
            dev = os.stat(real).st_dev
        except OSError:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._by_dev.get(dev)
        if cached and now - cached[2] < TTL:
            mount, vid = cached[0], cached[1]
        else:
            try:
                mount = self.mount_of(real)
            except OSError:
                return None
            vid = self._read(mount)
            with self._lock:
                self._by_dev[dev] = (mount, vid, now)
                if vid:
                    self._by_id[vid] = mount
        if not vid:
            return None
        if real != mount and not real.startswith(mount.rstrip(os.sep) + os.sep):
            return None
        return mount, vid

    def key(self, folder: str) -> str | None:
        """The `vol:` key of an album folder, None when its drive has no marker."""
        found = self.volume(folder)
        if not found:
            return None
        mount, vid = found
        rel = os.path.relpath(os.path.realpath(folder), mount)
        return PREFIX + vid + ":" + ("" if rel == "." else rel.replace(os.sep, "/"))

    def folder(self, key: str) -> str | None:
        """Where a `vol:` album is on this box, if its drive is here and was seen."""
        parsed = parse(key)
        if not parsed:
            return None
        vid, rel = parsed
        with self._lock:
            mount = self._by_id.get(vid)
        if not mount or self._marker(mount) != vid:
            return None
        parts = [p for p in rel.split("/") if p]
        if ".." in parts:
            return None
        folder = os.path.join(mount, *parts)
        return folder if os.path.isdir(folder) else None


VOLUMES = Volumes()


def album_key(root: str | None, rel: str, volumes: Volumes | None = None) -> str:
    """The DR log's key for the local album in `rel` (relative to MPD's music
    directory `root`): its drive's, else its folder's."""
    if root:
        key = (volumes or VOLUMES).key(os.path.join(root, rel))
        if key:
            return key
    return "local:" + rel


def signature(root: str | None) -> tuple:
    """What is mounted where in the music directory, one level down: changes
    when a drive linked or mounted there comes or goes."""
    if not root:
        return ()
    marks = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return ()
    for name in [""] + names[:1000]:
        try:
            marks.append((name, os.stat(os.path.join(root, name)).st_dev))
        except OSError:
            marks.append((name, None))
    return tuple(marks)
