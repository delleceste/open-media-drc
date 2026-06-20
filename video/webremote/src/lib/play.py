"""Translate a browsed item into the mpv IPC commands that play it.

Only library items reachable through the file browser are handled here: plain
files and Blu-ray / DVD *rip folders* on the media drives. Physical optical
discs (/dev/cd0) need the gcache read-ahead lifecycle and stay with the separate
play-bluray.sh launcher (see ../ARCHITECTURE.md) — they have no folder to browse.
"""
import re
import subprocess

_DUR = re.compile(r"duration:\s*(\d+):(\d+):(\d+)")
_MPLS = re.compile(r"playlist:\s*0*([0-9]+)\.mpls")


def bd_longest_mpls(folder: str, timeout: float = 30.0) -> int | None:
    """Probe a Blu-ray (rip folder) and return the longest title's .mpls number.

    Mirrors play-bluray.sh: mpv's bd:// plays the disc *default* playlist, often
    the wrong/short one, so we pick the genuinely longest title and load it as
    bd://mpls/<n>. No gcache here — rip folders live on a fast disk.
    """
    try:
        r = subprocess.run(["bd_list_titles", folder],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    best_n, best_dur = None, -1
    for line in r.stdout.splitlines():
        dm, pm = _DUR.search(line), _MPLS.search(line)
        if not (dm and pm):
            continue
        h, m, s = (int(x) for x in dm.groups())
        dur = h * 3600 + m * 60 + s
        if dur > best_dur:
            best_dur, best_n = dur, int(pm.group(1))
    return best_n


def play_actions(realpath: str, kind: str) -> list[list]:
    """Ordered list of mpv IPC command-arg arrays to start playback."""
    if kind == "file":
        return [["loadfile", realpath, "replace"]]
    if kind == "bdmv":
        n = bd_longest_mpls(realpath)
        target = f"bd://mpls/{n}" if n is not None else "bd://"
        return [["set_property", "bluray-device", realpath],
                ["loadfile", target, "replace"]]
    if kind == "dvd":
        return [["set_property", "dvd-device", realpath],
                ["loadfile", "dvd://", "replace"]]
    raise ValueError(f"not a playable item: {kind}")
