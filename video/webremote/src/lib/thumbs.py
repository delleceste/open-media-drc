"""On-demand video thumbnails, cached on disk.

A frame grab via ffmpeg for plain files; for Blu-ray / DVD rips the largest
stream is grabbed; for ordinary folders an existing poster/cover image is used
if present (no expensive recursion into the folder for a video frame).

Generation is lazy and the JPEG is cached under cache_dir keyed by path+mtime,
so browsing a folder twice doesn't re-run ffmpeg. This matters on the headless
box. Returns the cached path, or None when no thumbnail could be made (the UI
then shows a type icon).
"""
import glob
import hashlib
import os
import subprocess
import threading

_IMAGE_NAMES = ("poster.jpg", "poster.png", "folder.jpg", "cover.jpg", "cover.png")
_FAIL = b"FAIL"   # marker file content so we don't retry a known-bad source

# Bound how many ffmpeg jobs run at once (headless box) and dedup concurrent
# requests for the same thumbnail (web request + prewarm thread).
_sem = threading.Semaphore(2)
_locks_guard = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}


def set_concurrency(n: int) -> None:
    global _sem
    _sem = threading.Semaphore(max(1, n))


def _key_lock(base: str) -> threading.Lock:
    with _locks_guard:
        lock = _key_locks.get(base)
        if lock is None:
            lock = threading.Lock()
            _key_locks[base] = lock
        return lock


def _key(realpath: str) -> str:
    try:
        mtime = int(os.path.getmtime(realpath))
    except OSError:
        mtime = 0
    return hashlib.sha1(f"{realpath}|{mtime}".encode()).hexdigest()


def _largest(paths: list[str]) -> str | None:
    best, best_sz = None, -1
    for p in paths:
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        if sz > best_sz:
            best, best_sz = p, sz
    return best


def _folder_image(realpath: str) -> str | None:
    for name in _IMAGE_NAMES:
        p = os.path.join(realpath, name)
        if os.path.isfile(p):
            return p
    imgs = glob.glob(os.path.join(realpath, "*.jpg")) + glob.glob(os.path.join(realpath, "*.png"))
    return _largest(imgs) if imgs else None


def _source(realpath: str, kind: str) -> tuple[str, bool] | None:
    """Return (source_path, is_video). is_video=False means it's already an image."""
    if kind == "file":
        return (realpath, True)
    if kind == "bdmv":
        img = _folder_image(realpath)
        if img:
            return (img, False)
        m2ts = glob.glob(os.path.join(realpath, "BDMV", "STREAM", "*.m2ts"))
        best = _largest(m2ts)
        return (best, True) if best else None
    if kind == "dvd":
        img = _folder_image(realpath)
        if img:
            return (img, False)
        vobs = [v for v in glob.glob(os.path.join(realpath, "VIDEO_TS", "*.VOB"))
                if "VIDEO_TS.VOB" not in os.path.basename(v).upper()]
        best = _largest(vobs)
        return (best, True) if best else None
    if kind == "dir":
        img = _folder_image(realpath)
        return (img, False) if img else None
    return None


def _ff_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _grab(src: str, is_video: bool, out: str, seek_pct: int, max_w: int) -> bool:
    scale = f"scale={max_w}:-2:force_original_aspect_ratio=decrease"
    if not is_video:
        cmd = ["ffmpeg", "-y", "-i", src, "-vf", scale, "-frames:v", "1", out]
    else:
        dur = _ff_duration(src)
        ss = max(1.0, dur * seek_pct / 100.0) if dur else 60.0
        # input-seek (-ss before -i) is fast; one frame, scaled, as JPEG.
        cmd = ["ffmpeg", "-y", "-ss", f"{ss:.2f}", "-i", src,
               "-frames:v", "1", "-vf", scale, "-q:v", "4", out]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        return r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0
    except Exception:
        return False


def _mark_fail(fail: str) -> None:
    try:
        with open(fail, "wb") as f:
            f.write(_FAIL)
    except OSError:
        pass


def thumb(realpath: str, kind: str, cache_dir: str,
          seek_pct: int = 10, max_w: int = 320) -> str | None:
    """Path to a cached JPEG thumbnail for an entry, or None if not makeable.

    Cached by path+mtime: an existing thumbnail is returned without touching
    ffmpeg, so it is generated at most once per file version (the background
    prewarm warms new files; on-demand calls fill any gaps). Concurrency-safe:
    a per-key lock dedups simultaneous callers and a semaphore bounds parallel
    ffmpeg jobs.
    """
    cache_dir = os.path.expanduser(cache_dir)
    thumb_dir = os.path.join(cache_dir, "thumbs")
    base = os.path.join(thumb_dir, _key(realpath))
    out, fail = base + ".jpg", base + ".fail"

    if os.path.isfile(out):
        return out
    if os.path.isfile(fail):
        return None

    with _key_lock(base):
        # Re-check: another caller may have produced it while we waited.
        if os.path.isfile(out):
            return out
        if os.path.isfile(fail):
            return None
        os.makedirs(thumb_dir, exist_ok=True)
        src = _source(realpath, kind)
        if not src:
            _mark_fail(fail)
            return None
        with _sem:
            ok = _grab(src[0], src[1], out, seek_pct, max_w)
        if ok:
            return out
        _mark_fail(fail)
        return None
