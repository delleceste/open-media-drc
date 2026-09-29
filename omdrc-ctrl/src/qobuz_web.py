"""The panel's Qobuz album search endpoints, under /qobuz/.

Kept out of app.py: the search itself is qobuz_search.py, and all this adds is
HTTP, the credentials, and the rule that the search is only offered while
upmpdcli runs -- upmpdcli's Qobuz plugin is what streams the albums found, so
with the other renderer (qobuzconnect2mpd) active a result could not be played.

    GET /qobuz/status              enabled, upmpdcli running, token present
    GET /qobuz/labels              the label groups offered as check boxes
    GET /qobuz/search?q=&label=&last=|from=&to=&sort=&scan=&enrich=
    GET /qobuz/search/stream?...    the same, as server-sent events: partial
                                   results while it reads, then the answer
    GET /qobuz/album/<id>          one album: tracks, performers, description
    POST /qobuz/play               {"album_id", "mode": "replace"|"append",
                                    "start": track id}: queue it on upmpdcli
    GET /qobuz/played              the albums played from here, newest first
    POST /qobuz/played             {"action": "hide"|"show", "album_id"}: out of
                                   (back into) that list; still counted as played
    GET /qobuz/words               search-field completions: the shipped
                                   list and the ones learned from plays
    GET /qobuz/track/<id>          one track and its album (the player's cover)
    GET /qobuz/lowered             albums, labels and artists moved to the end
    POST /qobuz/lowered            {"action": "add", "kind", "key", "name"} |
                                   {"action": "remove", "kind", "key"} |
                                   {"action": "clear"}

Playing goes through upmpdcli's OpenHome playlist (openhome.py), exactly as a
control point browsing the box's own Qobuz library would do it: the queued
URLs are the plugin's (http://<box>:<plgmicrohttpport>/qobuz/track/...), so
the box fetches the music from Qobuz itself, and each track carries its DIDL
-- with the patched upmpdcli, MPD's Date and Label tags come from it.

The credentials are the plugin's own: its user token file (re-read on every
request, so signing in again from the panel takes effect at once) and its app
id -- [qobuz_search] app_id, else upmpdcli.conf's qobuzappid, else the one the
plugin reads from Qobuz's web player, fetched once per panel run.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time

from flask import Blueprint, Response, jsonify, request

import openhome
from qobuz_search import (LoweredList, PlayedAlbums, QobuzCatalog, QobuzError,
                          SearchWords, discover_app_id, read_word_list)

bp = Blueprint("qobuz", __name__, url_prefix="/qobuz")

# Handed over by init_app(); the defaults keep the module importable alone.
_settings = None                # () -> qobuz_search.Settings
_upmpdcli_conf = lambda: None   # noqa: E731 - () -> upmpdcli.conf path | None
_read_options = lambda path: {}  # noqa: E731 - flat key = value reader
_token_file = lambda: ""        # noqa: E731 - the plugin's token file
_plugin_dir = lambda: ""        # noqa: E731 - upmpdcli's cdplugins/qobuz
_renderer_running = lambda: False  # noqa: E731
_state_dir = lambda: ""         # noqa: E731 - where the played list lives

# The path upmpdcli's Qobuz plugin serves its tracks under, and its default
# port (upmpdcli.conf plgmicrohttpport).
PLUGIN_PATH = "/qobuz/track/version/1/trackId/"
PLUGIN_PORT = "49149"

# The service check runs a command; a page typing a query must not pay for it
# on every keystroke.
RENDERER_CHECK_TTL = 5.0

_lock = threading.Lock()
_catalog: QobuzCatalog | None = None
_catalog_settings = None
_app_id = ""
_renderer = (0.0, False)
_played: dict[str, PlayedAlbums] = {}       # one store per file, across reloads
_learned: dict[str, SearchWords] = {}
_lowered: dict[str, LoweredList] = {}
# The shipped completion list, next to this module; re-read when it changes.
WORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qobuz_words.txt")
_words = (None, [])                          # (mtime, entries)
_openhome: openhome.Renderer | None = None


def init_app(app, settings, upmpdcli_conf, read_options, token_file, plugin_dir,
             renderer_running, state_dir) -> None:
    global _settings, _upmpdcli_conf, _read_options, _token_file, _plugin_dir
    global _renderer_running, _state_dir
    _settings, _upmpdcli_conf, _read_options = settings, upmpdcli_conf, read_options
    _token_file, _plugin_dir, _renderer_running = token_file, plugin_dir, renderer_running
    _state_dir = state_dir
    app.register_blueprint(bp)


def _app_id_now() -> str:
    global _app_id
    configured = _settings().app_id
    if configured:
        return configured
    conf = _upmpdcli_conf()
    configured = _read_options(conf).get("qobuzappid", "") if conf else ""
    if configured:
        return configured
    with _lock:
        if not _app_id:
            _app_id = discover_app_id(_plugin_dir(), timeout=_settings().timeout * 4)
        return _app_id


def _credentials() -> tuple[str, str]:
    token = _read_options(_token_file()).get("user_auth_token", "")
    if not token:
        raise QobuzError("upmpdcli's Qobuz plugin has no token: use Qobuz sign-in "
                         "in the panel first")
    return _app_id_now(), token


def _upmpdcli_options() -> dict[str, str]:
    conf = _upmpdcli_conf()
    return _read_options(conf) if conf else {}


def played() -> PlayedAlbums:
    path = os.path.join(_state_dir(), "qobuz-played.json")
    with _lock:
        if path not in _played:
            _played[path] = PlayedAlbums(path, _settings().played_limit)
        _played[path].limit = _settings().played_limit
        return _played[path]


def learned() -> SearchWords:
    path = os.path.join(_state_dir(), "qobuz-words.json")
    with _lock:
        if path not in _learned:
            _learned[path] = SearchWords(path)
        return _learned[path]


def lowered() -> LoweredList:
    path = os.path.join(_state_dir(), "qobuz-lowered.json")
    with _lock:
        if path not in _lowered:
            _lowered[path] = LoweredList(path)
        return _lowered[path]


def word_list() -> list[str]:
    global _words
    try:
        mtime = os.stat(WORDS_FILE).st_mtime
    except OSError:
        return []
    if _words[0] != mtime:
        _words = (mtime, read_word_list(WORDS_FILE))
    return _words[1]


def catalog() -> QobuzCatalog:
    """One client per settings object: a config reload starts a fresh cache."""
    global _catalog, _catalog_settings
    settings = _settings()
    store, lower = played(), lowered()
    with _lock:
        if _catalog is None or _catalog_settings is not settings:
            _catalog = QobuzCatalog(settings, credentials=_credentials, played=store,
                                    lowered=lower)
            _catalog_settings = settings
        _catalog.lowered = lower
        return _catalog


def renderer(fresh: bool = False) -> openhome.Renderer:
    """upmpdcli's OpenHome renderer on this machine, found once and kept."""
    global _openhome
    with _lock:
        if _openhome is None or fresh:
            configured = _settings().renderer
            if configured:
                found = openhome.read_description(configured)
                if not found:
                    raise openhome.OpenHomeError(
                        f"no OpenHome playlist at {configured} ([qobuz_search] renderer)")
            else:
                found = openhome.find_local_renderer(
                    _upmpdcli_options().get("friendlyname", ""))
            _openhome = found
        return _openhome


def track_url(host: str, track_id: str) -> str:
    """The plugin's permanent URL for a track, as its own browsing hands out."""
    options = _upmpdcli_options()
    host = options.get("plgmicrohttphost") or host
    port = options.get("plgmicrohttpport") or PLUGIN_PORT
    return f"http://{host}:{port}{PLUGIN_PATH}{track_id}"


def queue_entries(album: dict, host: str) -> list[tuple[str, str, str]]:
    """(track id, uri, DIDL) for each streamable track of `album`
    (QobuzCatalog.album())."""
    entries = []
    for track in album["track_list"]:
        if not track["streamable"] or not track["id"]:
            continue
        uri = track_url(host, track["id"])
        title = track["title"] + (f" ({track['version']})" if track["version"] else "")
        entries.append((track["id"], uri, openhome.didl(
            uri, title, item_id=f"qobuz-{track['id']}",
            artist=track["performer"] or album["artist"],
            album=album["title"], date=album["date"], label=album["label"],
            genre=album["genre"], art=album["image_large"] or album["image"],
            track_number=track["number"], disc_number=track["disc"],
            duration=track["duration"])))
    return entries


def renderer_running() -> bool:
    global _renderer
    now = time.monotonic()
    if now - _renderer[0] > RENDERER_CHECK_TTL:
        try:
            running = bool(_renderer_running())
        except Exception:
            running = False
        _renderer = (now, running)
    return _renderer[1]


def _guard():
    """None when a search may run, else a ready (response, status)."""
    if not _settings().enabled:
        return jsonify({"ok": False, "error": "Qobuz search disabled"}), 404
    if not renderer_running():
        return jsonify({"ok": False, "renderer": False,
                        "error": "upmpdcli is not running: the Qobuz search plays "
                                 "through it, so switch the renderer to upmpdcli"}), 409
    return None


def _number(name: str, cast):
    value = (request.args.get(name) or "").strip()
    if not value:
        return None
    try:
        return cast(value)
    except ValueError:
        raise QobuzError(f"{name} must be a number")


@bp.route("/status")
def status():
    settings = _settings()
    return jsonify({
        "ok": True,
        "enabled": settings.enabled,
        "renderer": renderer_running() if settings.enabled else False,
        "token": bool(_read_options(_token_file()).get("user_auth_token")),
    })


@bp.route("/labels")
def labels():
    if not _settings().enabled:
        return jsonify({"ok": False, "error": "Qobuz search disabled"}), 404
    return jsonify({"ok": True, "labels": [
        {"name": g.name, "patterns": list(g.patterns)} for g in _settings().labels]})


def _search_args() -> dict:
    """The search's arguments, from the request (QobuzError on a bad number)."""
    names = [name for value in request.args.getlist("label")
             for name in value.split(",")]
    return dict(
        text=request.args.get("q", ""), labels=names,
        last_years=_number("last", float),
        from_year=_number("from", int), to_year=_number("to", int),
        sort=request.args.get("sort", "relevance"),
        enrich=request.args.get("enrich", "1") not in ("0", "no", "false"),
        scan=_number("scan", int))


@bp.route("/search")
def search():
    """?q=text&label=Pentatone&label=Decca (or label=Pentatone,Decca)
    &last=2 (years back from today) or &from=2021&to=2026 (calendar years)
    &sort=relevance|date &scan=<albums per query> &enrich=0"""
    guard = _guard()
    if guard:
        return guard
    try:
        answer = catalog().search(**_search_args())
        return jsonify({"ok": True, **answer})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502


@bp.route("/search/stream")
def search_stream():
    """The search above as server-sent events: {"ok": true, "partial": true, ...}
    frames while Qobuz is being read (in the final order, without performers),
    then one frame with the whole answer, or {"ok": false, "error"}; then the
    stream ends.  Always 200, so that an EventSource sees the error frame."""
    def frame(d):
        return f"data: {json.dumps(d, separators=(',', ':'))}\n\n"

    guard = _guard()
    if guard:
        body = guard[0].get_json()
        return Response(frame(body), mimetype="text/event-stream")
    try:
        args = _search_args()
    except QobuzError as error:
        return Response(frame({"ok": False, "error": str(error)}), mimetype="text/event-stream")

    frames: queue.Queue = queue.Queue()
    target = catalog()

    def run():
        try:
            answer = target.search(**args, progress=lambda d: frames.put({"ok": True, **d}))
            frames.put({"ok": True, **answer})
        except QobuzError as error:
            frames.put({"ok": False, "error": str(error)})
        except Exception as error:      # noqa: BLE001 - the page must hear of it
            frames.put({"ok": False, "error": f"search failed: {error}"})
        frames.put(None)

    threading.Thread(target=run, name="qobuz-search", daemon=True).start()

    def events():
        while True:
            try:
                d = frames.get(timeout=10)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if d is None:
                return
            yield frame(d)

    return Response(events(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/played", methods=["GET", "POST"])
def played_albums():
    if not _settings().enabled:
        return jsonify({"ok": False, "error": "Qobuz search disabled"}), 404
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        action, album_id = body.get("action"), str(body.get("album_id") or "")
        if action not in ("hide", "show") or not album_id:
            return jsonify({"ok": False, "error": "want action hide|show and album_id"}), 400
        if not played().hide(album_id, action == "hide"):
            return jsonify({"ok": False, "error": "not in the played list, or it cannot be saved"}), 409
        return jsonify({"ok": True})
    limit = request.args.get("limit", "50")
    return jsonify({"ok": True, "albums": played().recent(int(limit) if limit.isdigit() else 50)})


def _queue(album: dict, mode: str, start: str) -> dict:
    """Put the album on upmpdcli's playlist: `replace` clears it and plays
    (from `start` if given), `append` adds at the end and leaves playback
    alone."""
    target = renderer()
    playlist = openhome.Playlist(target.control_url)
    try:
        existing = playlist.ids()
    except openhome.OpenHomeError:
        # upmpdcli restarted on another address: look again, once.
        target = renderer(fresh=True)
        playlist = openhome.Playlist(target.control_url)
        existing = playlist.ids()

    entries = queue_entries(album, target.host)
    if not entries:
        raise QobuzError("none of this album's tracks can be streamed")
    if mode == "replace":
        playlist.delete_all()
        after = 0
    else:
        after = existing[-1] if existing else 0

    queued: list[tuple[str, int]] = []
    try:
        for track_id, uri, metadata in entries:
            after = playlist.insert(after, uri, metadata)
            queued.append((track_id, after))
    except openhome.OpenHomeError as error:
        raise openhome.OpenHomeError(
            f"queued {len(queued)} of {len(entries)} tracks, then: {error}") from error

    if mode == "replace":
        first = next((qid for tid, qid in queued if tid == start), queued[0][1])
        playlist.seek_id(first)
    return {"queued": len(queued), "tracks": len(album["track_list"]),
            "renderer": target.name}


@bp.route("/play", methods=["POST"])
def play():
    """{"album_id": "...", "mode": "replace" | "append", "start": "<track id>",
    "query": "<the search text that found it>"}.  With "query" (the search
    page always sends it, empty or not) the text and the album's artist and
    composer become search-field completions."""
    guard = _guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    album_id = str(body.get("album_id", "")).strip()
    mode = str(body.get("mode", "replace"))
    if mode not in ("replace", "append"):
        return jsonify({"ok": False, "error": "mode must be replace or append"}), 400
    try:
        album = catalog().album(album_id)
        answer = _queue(album, mode, str(body.get("start", "")))
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502
    except openhome.OpenHomeError as error:
        return jsonify({"ok": False, "error": str(error)}), 502
    answer["remembered"] = catalog().record_played(album_id)
    if "query" in body:
        answer["learned"] = learned().record(
            [str(body.get("query") or ""), album["artist"], album["composer"]])
    return jsonify({"ok": True, "mode": mode, **answer})


@bp.route("/album/<album_id>")
def album(album_id):
    guard = _guard()
    if guard:
        return guard
    try:
        return jsonify({"ok": True, "album": catalog().album(album_id)})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502


@bp.route("/words")
def words():
    """Everything the search field completes from, sent once: a few tens of
    KB, and the page matches as the user types without asking again."""
    if not _settings().enabled:
        return jsonify({"ok": False, "error": "Qobuz search disabled"}), 404
    return jsonify({"ok": True, "words": word_list(), "learned": learned().recent()})


@bp.route("/track/<track_id>")
def track(track_id):
    guard = _guard()
    if guard:
        return guard
    try:
        return jsonify({"ok": True, "track": catalog().track(track_id)})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502


@bp.route("/lowered", methods=["GET", "POST"])
def lowered_list():
    """The lowered list: read it, or add, restore (remove) or clear.  It only
    reorders results, so it needs neither upmpdcli nor a Qobuz sign-in."""
    if not _settings().enabled:
        return jsonify({"ok": False, "error": "Qobuz search disabled"}), 404
    store = lowered()
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        action = body.get("action")
        try:
            if action == "add":
                store.add(str(body.get("kind", "")), str(body.get("key", "")),
                          str(body.get("name", "")))
            elif action == "remove":
                store.remove(str(body.get("kind", "")), str(body.get("key", "")))
            elif action == "clear":
                store.clear()
            else:
                return jsonify({"ok": False, "error": "action must be add, remove or clear"}), 400
        except QobuzError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "entries": store.entries()})
