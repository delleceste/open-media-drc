#!/usr/bin/env python3
"""video webremote — phone-driven mpv media browser/remote (LAN only).

Step 1: skeleton + browsing. Lists directories under the whitelisted media
root(s), classifying each entry (folder / Blu-ray rip / DVD rip / playable
file) so the UI can show the right icon and decide what a tap does. Play and
transport control over the mpv JSON IPC socket arrive in later steps; the
`/api/play` endpoint is a clearly-marked stub for now.

See ../ARCHITECTURE.md. Security: bind to the LAN only, never the internet, and
never serve a path outside the configured roots (see lib/roots.py).
"""
import configparser
import os
import threading
import time

from flask import Flask, jsonify, render_template, request, send_file

from lib import (classify, favorites, imdb, mpvipc, play, thumbs, titles,
                 roots as rootlib)

_HERE = os.path.dirname(os.path.abspath(__file__))

# Built-in defaults so the server runs without a config file (dev convenience).
HOST = "0.0.0.0"
PORT = 9080
ROOTS: list[str] = []
_RAW_ROOTS = "/media/USBHD2/video"
CACHE_DIR = "~/.cache/omdrc-video"
SEEK_PCT = 10
MAX_W = 320
OMDB_KEY: str | None = None
MPV_SOCKET = "/tmp/mpv-socket"
PREWARM_INTERVAL = 900
THUMB_CONCURRENCY = 2
DISC_ENABLED = True
DISC_DEV = "cd0"
DISC_CACHE = "bd"
DISC_SCRIPT = os.path.join(_HERE, os.pardir, "disc.sh")


def fav_file() -> str:
    return os.path.join(os.path.expanduser(CACHE_DIR), "favorites.json")


def load_config(path: str | None) -> None:
    """Load config from an INI file; fall back to built-in defaults."""
    global HOST, PORT, ROOTS, _RAW_ROOTS, CACHE_DIR, SEEK_PCT, MAX_W, OMDB_KEY
    global MPV_SOCKET, PREWARM_INTERVAL, THUMB_CONCURRENCY
    global DISC_ENABLED, DISC_DEV, DISC_CACHE
    if path and os.path.isfile(path):
        cfg = configparser.ConfigParser()
        cfg.read(path)
        HOST = cfg.get("server", "host", fallback=HOST)
        PORT = cfg.getint("server", "port", fallback=PORT)
        _RAW_ROOTS = cfg.get("media", "roots", fallback=_RAW_ROOTS)
        CACHE_DIR = cfg.get("thumbs", "cache_dir", fallback=CACHE_DIR)
        SEEK_PCT = cfg.getint("thumbs", "seek_percent", fallback=SEEK_PCT)
        MAX_W = cfg.getint("thumbs", "max_width", fallback=MAX_W)
        OMDB_KEY = cfg.get("imdb", "omdb_api_key", fallback="").strip() or None
        MPV_SOCKET = cfg.get("mpv", "socket", fallback=MPV_SOCKET)
        PREWARM_INTERVAL = cfg.getint("thumbs", "prewarm_interval", fallback=PREWARM_INTERVAL)
        THUMB_CONCURRENCY = cfg.getint("thumbs", "concurrency", fallback=THUMB_CONCURRENCY)
        DISC_ENABLED = cfg.getboolean("disc", "enabled", fallback=DISC_ENABLED)
        DISC_DEV = cfg.get("disc", "device", fallback=DISC_DEV)
        DISC_CACHE = cfg.get("disc", "cache", fallback=DISC_CACHE)
    ROOTS = rootlib.load_roots(_RAW_ROOTS)
    thumbs.set_concurrency(THUMB_CONCURRENCY)


app = Flask(__name__, template_folder=os.path.join(_HERE, "templates"))


def _fav_set() -> set[str]:
    """Currently-valid favourites (inside a root and still a directory)."""
    return {p for p in favorites.load(fav_file())
            if rootlib.is_within(os.path.realpath(p), ROOTS) and os.path.isdir(p)}


def _list_dir(realpath: str) -> list[dict]:
    favs = _fav_set()
    entries = []
    for name in os.listdir(realpath):
        if name.startswith("."):
            continue
        full = os.path.join(realpath, name)
        kind = classify.classify(full)
        if kind is None:
            continue
        title, year = titles.parse_title(name, kind == "file")
        entries.append({
            "name": name,
            "path": full,
            "type": kind,
            "title": title,
            "year": year,
            # whether an IMDb action is worth offering for this entry
            "imdb": titles.can_deduce(title, year, kind),
            # directories can be pinned to the main page
            "favable": kind in ("dir", "bdmv", "dvd"),
            "fav": full in favs,
        })
    entries.sort(key=classify.sort_key)
    return entries


def _fav_entries() -> list[dict]:
    """Favourites for the main page, newest config order preserved."""
    favs = [p for p in favorites.load(fav_file())
            if rootlib.is_within(os.path.realpath(p), ROOTS) and os.path.isdir(p)]
    out = []
    for p in favs:
        kind = classify.classify(p) or "dir"
        title, year = titles.parse_title(os.path.basename(p), False)
        out.append({"name": os.path.basename(p), "path": p, "type": kind,
                    "title": title or os.path.basename(p), "year": year,
                    "imdb": titles.can_deduce(title, year, kind),
                    "favable": True, "fav": True})
    return out


# ── background thumbnail prewarm ──────────────────────────────────────────────

_prewarm_lock = threading.Lock()


def _iter_media(root: str):
    """Yield (path, kind) media items under root; rip folders are leaves."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if name.startswith("."):
                continue
            full = os.path.join(d, name)
            kind = classify.classify(full)
            if kind in ("file", "bdmv", "dvd"):
                yield full, kind          # rip folders (bdmv/dvd) not descended
            elif kind == "dir":
                stack.append(full)


def prewarm_once() -> int:
    """Generate any missing thumbnails across the roots. Returns count attempted."""
    n = 0
    for root in ROOTS:
        for full, kind in _iter_media(root):
            thumbs.thumb(full, kind, CACHE_DIR, SEEK_PCT, MAX_W)  # no-op if cached
            n += 1
    return n


def _prewarm_run() -> bool:
    """Run one prewarm pass unless one is already in progress."""
    if not _prewarm_lock.acquire(blocking=False):
        return False
    try:
        prewarm_once()
    except Exception:
        pass
    finally:
        _prewarm_lock.release()
    return True


def _prewarm_loop() -> None:
    while True:
        _prewarm_run()
        if PREWARM_INTERVAL <= 0:
            break
        time.sleep(PREWARM_INTERVAL)


def start_prewarm() -> None:
    threading.Thread(target=_prewarm_loop, name="thumb-prewarm", daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/roots")
def api_roots():
    """Main page: configured media roots plus pinned favourites."""
    return jsonify({
        "ok": True,
        "roots": [{"name": os.path.basename(r) or r, "path": r,
                   "type": "dir", "favable": False} for r in ROOTS],
        "favorites": _fav_entries(),
        "disc": DISC_ENABLED,
    })


@app.route("/api/browse")
def api_browse():
    """List a directory under the whitelist.

    With no `path`, returns the roots as the starting point. `parent` is the
    directory to go up to, or null when already at a root (can't go above).
    """
    req = request.args.get("path", "").strip()
    if not req:
        return api_roots()

    realpath = rootlib.safe_resolve(req, ROOTS)
    if realpath is None:
        return jsonify({"ok": False, "error": "path is outside the allowed media folders"}), 403
    if not os.path.isdir(realpath):
        return jsonify({"ok": False, "error": "not a directory"}), 400

    root = rootlib.containing_root(realpath, ROOTS)
    parent = None
    if realpath != root:
        cand = os.path.dirname(realpath)
        if rootlib.is_within(cand, ROOTS):
            parent = cand

    try:
        entries = _list_dir(realpath)
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "path": realpath, "parent": parent, "entries": entries})


def _resolve_media(req: str):
    """Resolve a client path inside the whitelist and classify it.

    Returns (realpath, kind) or (None, error_response_tuple).
    """
    realpath = rootlib.safe_resolve(req, ROOTS)
    if realpath is None:
        return None, (jsonify({"ok": False, "error": "path is outside the allowed media folders"}), 403)
    kind = classify.classify(realpath)
    if kind is None:
        return None, (jsonify({"ok": False, "error": "not a playable item"}), 400)
    return (realpath, kind), None


@app.route("/api/thumb")
def api_thumb():
    """Cached JPEG thumbnail for an entry; 404 → UI falls back to a type icon."""
    realpath = rootlib.safe_resolve(request.args.get("path", "").strip(), ROOTS)
    if realpath is None:
        return "forbidden", 403
    kind = classify.classify(realpath)
    if kind is None:
        return "not found", 404
    path = thumbs.thumb(realpath, kind, CACHE_DIR, SEEK_PCT, MAX_W)
    if not path:
        return "no thumbnail", 404
    resp = send_file(path, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/api/imdb")
def api_imdb():
    """IMDb info for an entry: enriched via OMDb when a key is set, else a
    plain search link. Always returns a `find_url` fallback."""
    res, err = _resolve_media(request.args.get("path", "").strip())
    if err:
        return err
    realpath, kind = res
    title, year = titles.parse_title(os.path.basename(realpath), kind == "file")
    if not titles.can_deduce(title, year, kind):
        return jsonify({"ok": False, "error": "could not deduce a movie title"})

    find_url = titles.imdb_find_url(title, year)
    data = imdb.lookup(title, year, OMDB_KEY, os.path.expanduser(CACHE_DIR))
    if data and data.get("found"):
        return jsonify({"ok": True, "found": True, "info": data, "find_url": find_url})
    # no key, lookup failed, or no match -> the search link still works
    return jsonify({"ok": True, "found": False, "title": title, "year": year,
                    "find_url": find_url, "enriched": OMDB_KEY is not None})


@app.route("/api/favorites", methods=["GET", "POST", "DELETE"])
def api_favorites():
    """List / pin / unpin folders shown on the main page.

    GET    -> current favourites
    POST   {path}  -> pin a directory (must be inside a root)
    DELETE {path}  -> unpin
    """
    if request.method == "GET":
        return jsonify({"ok": True, "favorites": _fav_entries()})

    body = request.get_json(silent=True) or {}
    realpath = rootlib.safe_resolve((body.get("path") or "").strip(), ROOTS)
    if realpath is None:
        return jsonify({"ok": False, "error": "path is outside the allowed media folders"}), 403

    if request.method == "POST":
        if not os.path.isdir(realpath):
            return jsonify({"ok": False, "error": "only folders can be favourited"}), 400
        favorites.add(fav_file(), realpath)
        return jsonify({"ok": True, "fav": True})

    favorites.remove(fav_file(), realpath)   # DELETE
    return jsonify({"ok": True, "fav": False})


@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    """Kick a background thumbnail prewarm pass (no-op if one is running)."""
    started = {"v": False}

    def run():
        started["v"] = _prewarm_run()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(0.1)   # let it grab the lock so we can report busy vs started
    return jsonify({"ok": True, "busy": _prewarm_lock.locked()})


# ── physical Blu-ray disc (gcache + idle mpv over IPC) ────────────────────────

_disc_lock = threading.Lock()
_disc_active = False


def _disc_env() -> dict:
    e = dict(os.environ)
    e["DISC_DEV"], e["DISC_CACHE"] = DISC_DEV, DISC_CACHE
    dirs = e.get("PATH", "").split(":")
    for d in ("/sbin", "/usr/sbin", "/usr/local/sbin", "/usr/local/bin"):
        if d not in dirs:
            dirs.append(d)
    e["PATH"] = ":".join(dirs)
    return e


def _gcache_up() -> str:
    import subprocess
    r = subprocess.run([DISC_SCRIPT, "up"], capture_output=True, text=True,
                       timeout=60, env=_disc_env())
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "gcache setup failed")
    out = r.stdout.strip().splitlines()
    return out[-1] if out else f"/dev/cache/{DISC_CACHE}"


def _gcache_down() -> None:
    import subprocess
    try:
        subprocess.run([DISC_SCRIPT, "down"], capture_output=True, text=True,
                       timeout=60, env=_disc_env())
    except Exception:
        pass


def _disc_watch() -> None:
    """Tear the gcache down once the disc stops (idle, or switched to a file)."""
    global _disc_active
    for _ in range(30):                       # wait until the disc has loaded
        if (mpvipc.get_property(MPV_SOCKET, "path") or "").startswith("bd:"):
            break
        if not mpvipc.is_running(MPV_SOCKET):
            break
        time.sleep(0.5)
    while mpvipc.is_running(MPV_SOCKET):       # ...then until it leaves bd://
        if not (mpvipc.get_property(MPV_SOCKET, "path") or "").startswith("bd:"):
            break
        time.sleep(3)
    time.sleep(1)                             # let mpv release /dev/cache/<cache>
    _gcache_down()
    with _disc_lock:
        _disc_active = False


@app.route("/api/disc", methods=["POST"])
def api_disc():
    """Play (or eject) the physical USB Blu-ray disc on the idle mpv.

    op=play  : set up the gcache, pick the longest title, loadfile bd:// (IPC).
    op=eject : stop playback and destroy the gcache so the disc can be removed.
    """
    global _disc_active
    if not DISC_ENABLED:
        return jsonify({"ok": False, "error": "disc playback is disabled"}), 400
    if not mpvipc.is_running(MPV_SOCKET):
        return jsonify({"ok": False, "error": "mpv is not running (start mpv-idle.sh)"}), 503

    op = (request.get_json(silent=True) or {}).get("op", "play")

    if op == "eject":
        try:
            mpvipc.command(MPV_SOCKET, ["stop"])
        except Exception:
            pass
        _gcache_down()
        with _disc_lock:
            _disc_active = False
        return jsonify({"ok": True})

    with _disc_lock:
        try:
            cache_dev = f"/dev/cache/{DISC_CACHE}" if _disc_active else _gcache_up()
            n = play.bd_longest_mpls(cache_dev)
            target = f"bd://mpls/{n}" if n is not None else "bd://"
            mpvipc.command(MPV_SOCKET, ["set_property", "bluray-device", cache_dev])
            mpvipc.command(MPV_SOCKET, ["loadfile", target, "replace"])
            if not _disc_active:
                _disc_active = True
                threading.Thread(target=_disc_watch, name="disc-watch", daemon=True).start()
            return jsonify({"ok": True, "target": target})
        except Exception as e:
            if not _disc_active:        # setup failed before activation → clean up
                _gcache_down()
            return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/play", methods=["POST"])
def api_play():
    """Play a browsed item on the persistent mpv via IPC loadfile."""
    body = request.get_json(silent=True) or {}
    res, err = _resolve_media((body.get("path") or "").strip())
    if err:
        return err
    realpath, kind = res
    if not mpvipc.is_running(MPV_SOCKET):
        return jsonify({"ok": False, "error": "mpv is not running (start mpv-idle.sh)"}), 503
    try:
        for args in play.play_actions(realpath, kind):
            mpvipc.command(MPV_SOCKET, args)
        return jsonify({"ok": True})
    except mpvipc.MpvNotRunning:
        return jsonify({"ok": False, "error": "mpv is not running"}), 503
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/status")
def api_status():
    """Live mpv playback state for the transport bar (polled by the UI)."""
    if not mpvipc.is_running(MPV_SOCKET):
        return jsonify({"ok": True, "running": False})
    g = lambda name, d=None: mpvipc.get_property(MPV_SOCKET, name, d)
    idle = bool(g("idle-active", True))
    return jsonify({
        "ok": True, "running": True, "idle": idle,
        "title": g("media-title", ""),
        "pause": bool(g("pause", False)),
        "time": g("time-pos"),
        "duration": g("duration"),
        "volume": g("volume"),
        "mute": bool(g("mute", False)),
    })


# op -> mpv IPC command builder. Value (when needed) comes from the request body.
_CMD_OPS = {
    "toggle": lambda v: ["cycle", "pause"],
    "pause":  lambda v: ["set_property", "pause", True],
    "play":   lambda v: ["set_property", "pause", False],
    "stop":   lambda v: ["stop"],                       # back to idle -> window hides
    "seek":   lambda v: ["seek", float(v), "relative"],
    "seekto": lambda v: ["seek", float(v), "absolute"],
    "volume": lambda v: ["set_property", "volume", float(v)],
    "mute":   lambda v: ["cycle", "mute"],
    "audio":  lambda v: ["set_property", "aid", v],
    "sub":    lambda v: ["set_property", "sid", v],
}


@app.route("/api/cmd", methods=["POST"])
def api_cmd():
    """Transport control: {op, value?}."""
    body = request.get_json(silent=True) or {}
    op = body.get("op")
    if op not in _CMD_OPS:
        return jsonify({"ok": False, "error": f"unknown op: {op}"}), 400
    if not mpvipc.is_running(MPV_SOCKET):
        return jsonify({"ok": False, "error": "mpv is not running"}), 503
    try:
        mpvipc.command(MPV_SOCKET, _CMD_OPS[op](body.get("value")))
        return jsonify({"ok": True})
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "bad value"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="video webremote")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--config",
        default=os.path.join(_HERE, os.pardir, "webremote.conf"),
    )
    args = parser.parse_args()
    load_config(args.config)
    host = args.host or HOST
    port = args.port or PORT
    if not ROOTS:
        print(f"WARNING: no usable media roots (configured: {_RAW_ROOTS!r}); "
              "browsing will be empty until the drive is mounted.")
    start_prewarm()   # background: generate missing thumbnails for new files
    app.run(host=host, port=port, threaded=True)
