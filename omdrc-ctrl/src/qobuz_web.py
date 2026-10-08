"""The panel's Qobuz album search endpoints, under /qobuz/.

Kept out of app.py: the search itself is qobuz_search.py, and all this adds is
HTTP, the credentials, and the rule that the search is only offered while
upmpdcli runs -- upmpdcli's Qobuz plugin is what streams the albums found, so
with the other renderer (qobuzconnect2mpd) active a result could not be played.

    GET /qobuz/status              enabled, upmpdcli running, token present
    GET /qobuz/labels              the label groups offered as check boxes
    GET /qobuz/search?q=&label=&last=|from=&to=&sort=&awarded=&scan=&enrich=
    GET /qobuz/search/stream?...    the same, as server-sent events: partial
                                   results while it reads, then the answer
    GET /qobuz/album/<id>          one album: tracks, performers, description
    GET /qobuz/awards?ids=a,b,...  the awards of up to 30 albums (a search
                                   carries them only for the ones it enriched)
    GET /qobuz/awarded             the albums met with an award or marked
                                   awarded, and the publications to offer
    POST /qobuz/awarded            {"action": "mark", "album_id", "name",
                                    "publication"} | {"action": "unmark",
                                    "album_id", "name"} | {"action": "rate",
                                    "album_id", "rating": 0-3}: the user's own
    POST /qobuz/play               {"album_id", "mode": "replace"|"append",
                                    "start": track id}: queue it on upmpdcli
    GET /qobuz/played              the albums played from here, newest first
    POST /qobuz/played             {"action": "hide"|"show", "album_id"}: out of
                                   (back into) that list; still counted as played
    GET /qobuz/words               search-field completions: the shipped
                                   list and the ones learned from plays, and
                                   the artists with their labels (shipped,
                                   and a ring learned from plays)
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
import re
import shutil
import threading
import time
import uuid
from functools import lru_cache

from flask import Blueprint, Response, jsonify, request

import openhome
import qobuz_ai
import qobuz_favorites
import local_db
import mpd_library
import dr_volumes
from qobuz_search import (AWARD_PRESETS, AwardedAlbums, LoweredList, PlayedAlbums,
                          QobuzCatalog, QobuzError, album_card,
                          SearchWords, ArtistLabels, discover_app_id, read_word_list,
                          read_artist_labels)

bp = Blueprint("qobuz", __name__, url_prefix="/qobuz")

# Handed over by init_app(); the defaults keep the module importable alone.
_settings = None                # () -> qobuz_search.Settings
_upmpdcli_conf = lambda: None   # noqa: E731 - () -> upmpdcli.conf path | None
_read_options = lambda path: {}  # noqa: E731 - flat key = value reader
_token_file = lambda: ""        # noqa: E731 - the plugin's token file
_plugin_dir = lambda: ""        # noqa: E731 - upmpdcli's cdplugins/qobuz
_renderer_running = lambda: False  # noqa: E731
_queue_tail = None
_move_to_end = None
_music_directory = lambda: None
_music_config = lambda: (None, None)     # (music root, the config file naming it)
_state_dir = lambda: ""         # noqa: E731 - where the played list lives
_dr_lookup = lambda keys: {}    # noqa: E731 - album keys -> stored DR (dr_store)

# The path upmpdcli's Qobuz plugin serves its tracks under, and its default
# port (upmpdcli.conf plgmicrohttpport).
PLUGIN_PATH = "/qobuz/track/version/1/trackId/"
PLUGIN_PORT = "49149"

# The service check runs a command; a page typing a query must not pay for it
# on every keystroke.
RENDERER_CHECK_TTL = 5.0

_queue_lock = threading.Lock()
_lock = threading.Lock()
_catalog: QobuzCatalog | None = None
_catalog_settings = None
_app_id = ""
_renderer = (0.0, False)
_played: dict[str, PlayedAlbums] = {}       # one store per file, across reloads
_learned: dict[str, SearchWords] = {}
_lowered: dict[str, LoweredList] = {}
_awarded: dict[str, AwardedAlbums] = {}
_listening_jobs: dict[str, threading.Event] = {}
_listening_cancelled: dict[str, float] = {}
_listening_lock = threading.Lock()
# The shipped completion list, next to this module; re-read when it changes.
WORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qobuz_words.txt")
_words = (None, [])                          # (mtime, entries)
# Artists and their labels, shipped the same way: typing an artist offers its labels.
ARTISTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qobuz_artists.txt")
_artists = (None, [])                        # (mtime, [(artist, [label])])
_artist_labels: dict[str, ArtistLabels] = {}
_openhome: openhome.Renderer | None = None


def init_app(app, settings, upmpdcli_conf, read_options, token_file, plugin_dir,
             renderer_running, state_dir, queue_tail=None, move_to_end=None,
             music_directory=None, music_config=None) -> None:
    global _settings, _upmpdcli_conf, _read_options, _token_file, _plugin_dir
    global _renderer_running, _state_dir, _queue_tail, _move_to_end
    _settings, _upmpdcli_conf, _read_options = settings, upmpdcli_conf, read_options
    _token_file, _plugin_dir, _renderer_running = token_file, plugin_dir, renderer_running
    _state_dir = state_dir
    _queue_tail, _move_to_end = queue_tail, move_to_end
    global _music_directory, _music_config
    _music_directory = music_directory or (lambda: None)
    _music_config = music_config or (lambda: (None, None))
    app.register_blueprint(bp)


def set_dr_lookup(lookup) -> None:
    """`lookup(keys) -> {key: summary}`: the DR log's figure for an album
    (dr_store.DrStore.lookup), shown as a badge on the results."""
    global _dr_lookup
    _dr_lookup = lookup


def dr_key(card: dict) -> str:
    """The DR log's key for a result: its Qobuz album, or its local folder."""
    if card.get("source") == "local":
        tracks = card.get("tracks") or []
        first = tracks[0].get("file", "") if tracks and isinstance(tracks[0], dict) else ""
        return dr_volumes.album_key(_music_directory(), mpd_library.album_folder(first)) if first else ""
    return "qobuz:" + str(card["id"]) if card.get("id") else ""


def annotate_dr(results: list) -> list:
    """Add "dr_log" (exact or estimate, and how it was reached) to the results
    the log knows; a local album whose dr14.txt was read already keeps that."""
    try:
        cards = [c for c in results if isinstance(c, dict)
                 and not (c.get("source") == "local" and c.get("dr") is not None)]
        found = _dr_lookup([dr_key(c) for c in cards])
    except Exception:                       # noqa: BLE001 - a badge is optional
        return results
    for card in cards:
        summary = found.get(dr_key(card))
        if summary:
            card["dr_log"] = {k: summary[k] for k in
                              ("dr", "kind", "basis", "heard", "complete", "track_count")}
    return results


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


def _qobuz_user_id() -> str:
    user_id = _read_options(_token_file()).get("user_id", "")
    if not user_id.isdigit():
        raise QobuzError("Qobuz user ID is unavailable: sign in again")
    return user_id


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


def artist_list() -> list[tuple[str, list[str]]]:
    global _artists
    try:
        mtime = os.stat(ARTISTS_FILE).st_mtime
    except OSError:
        return []
    if _artists[0] != mtime:
        _artists = (mtime, read_artist_labels(ARTISTS_FILE))
    return _artists[1]


def artist_labels() -> ArtistLabels:
    path = os.path.join(_state_dir(), "qobuz-artist-labels.json")
    with _lock:
        if path not in _artist_labels:
            _artist_labels[path] = ArtistLabels(path, shipped=artist_list)
        _artist_labels[path].limit = _settings().artist_ring
        return _artist_labels[path]


def lowered() -> LoweredList:
    path = os.path.join(_state_dir(), "qobuz-lowered.json")
    with _lock:
        if path not in _lowered:
            _lowered[path] = LoweredList(path)
        return _lowered[path]


def awarded() -> AwardedAlbums:
    path = os.path.join(_state_dir(), "qobuz-awarded.json")
    with _lock:
        if path not in _awarded:
            _awarded[path] = AwardedAlbums(path)
        return _awarded[path]


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
    store, lower, prizes = played(), lowered(), awarded()
    with _lock:
        if _catalog is None or _catalog_settings is not settings:
            _catalog = QobuzCatalog(settings, credentials=_credentials, played=store,
                                    lowered=lower, awarded=prizes)
            _catalog_settings = settings
        _catalog.lowered = lower
        _catalog.awarded = prizes
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


def _guard(renderer_needed: bool = True):
    """None when a search may run, else a ready (response, status).  Reading
    about an album (its details, its awards) needs only the catalog, not
    upmpdcli: the Now page asks whichever renderer plays."""
    if not _settings().enabled:
        return jsonify({"ok": False, "error": "Qobuz search disabled"}), 404
    if renderer_needed and not renderer_running():
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
        awarded_only=request.args.get("awarded", "0").lower() in ("1", "true", "yes"),
        exclude_cd=request.args.get("hires", "0").lower() in ("1", "true", "yes"),
        enrich=request.args.get("enrich", "1") not in ("0", "no", "false"),
        scan=_number("scan", int))


def _ai_mutation_guard():
    origin = request.headers.get("Origin")
    if (not request.is_json or request.headers.get("X-Qobuz-AI") != "1"
            or (origin and origin.rstrip("/") != request.host_url.rstrip("/"))):
        return jsonify({"ok": False, "error": "Use AI controls in this app."}), 403
    return None


@bp.route("/ai/settings", methods=["GET", "POST"])
def ai_settings():
    guard = _guard(False)
    if guard:
        return guard
    try:
        if request.method == "POST":
            guard = _ai_mutation_guard()
            if guard:
                return guard
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return jsonify({"ok": False, "error": "Invalid settings."}), 400
            answer = qobuz_ai.save_settings(_state_dir(), body)
        else:
            answer = qobuz_ai.public_settings(_state_dir())
        response = jsonify({"ok": True, **answer})
        response.headers["Cache-Control"] = "no-store"
        return response
    except (qobuz_ai.AIError, OSError):
        return jsonify({"ok": False, "error": "Could not read or save AI settings. Check the provider, model, key and state directory."}), 400


@bp.route("/ai/recommend", methods=["POST"])
def ai_recommend():
    guard = _guard() or _ai_mutation_guard()
    if guard:
        return guard
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "Invalid recommendation request."}), 400
    try:
        filters = _search_args()
        for key in ("text", "sort", "scan"):
            filters.pop(key)
        # Keep catalog research bounded: at most four queries and 30 candidates each.
        filters["scan"] = 250
        answer = qobuz_ai.recommend(_state_dir(), catalog(), body.get("prompt"),
                                    filters, body.get("count"))
        return jsonify({"ok": True, **answer})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502


@bp.route("/ai/listening", methods=["POST"])
def ai_listening():
    guard = _guard(False) or _ai_mutation_guard()
    if guard:
        return guard
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "Invalid listening request."}), 400
    job = body.get("job")
    if not isinstance(job, str) or len(job) != 36:
        return jsonify({"ok": False, "error": "Invalid research session."}), 400
    try:
        uuid.UUID(job)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid research session."}), 400
    cancel = threading.Event()
    with _listening_lock:
        if job in _listening_cancelled:
            cancel.set()
            _listening_cancelled.pop(job, None)
        _listening_jobs[job] = cancel
    try:
        answer = qobuz_ai.listening_research(_state_dir(), body.get("album"), body.get("tracks"), cancel=cancel)
        response = jsonify({"ok": True, **answer})
        response.headers["Cache-Control"] = "no-store"
        return response
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502
    finally:
        with _listening_lock:
            _listening_jobs.pop(job, None)


@bp.route("/ai/listening/cancel", methods=["POST"])
def ai_listening_cancel():
    guard = _guard(False) or _ai_mutation_guard()
    if guard:
        return guard
    body = request.get_json(silent=True) or {}
    job = body.get("job") if isinstance(body, dict) else None
    with _listening_lock:
        event = _listening_jobs.get(job) if isinstance(job, str) else None
        if event:
            event.set()
        elif isinstance(job, str) and len(job) == 36:
            try:
                uuid.UUID(job)
            except ValueError:
                job = None
            if job:
                _listening_cancelled[job] = time.monotonic()
            for key, age in list(_listening_cancelled.items()):
                if time.monotonic() - age >= 300:
                    _listening_cancelled.pop(key, None)
            while len(_listening_cancelled) > 256:
                _listening_cancelled.pop(next(iter(_listening_cancelled)))
    return jsonify({"ok": True})


@bp.route("/genres")
def genres():
    guard = _guard(False)
    if guard:
        return guard
    try:
        return jsonify({"ok": True, "genres": catalog().genres()})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502


@bp.route("/discover")
def discover():
    guard = _guard(False)
    if guard:
        return guard
    try:
        return jsonify({"ok": True, **catalog().discover(
            request.args.get("genre", ""), _number("offset", int) or 0,
            request.args.getlist("label"), request.args.get("awarded") == "1",
            request.args.get("hires") == "1")})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502


@bp.route("/search")
def search():
    """?q=text&label=Pentatone&label=Decca (or label=Pentatone,Decca)
    &last=2 (years back from today) or &from=2021&to=2026 (calendar years)
    &sort=relevance|date &awarded=1 &scan=<albums per query> &enrich=0"""
    guard = _guard(False)
    if guard:
        return guard
    try:
        mpd_library.set_music_directory(_library_root())
        args = _search_args()
        local_only = request.args.get("local", "0").lower() in ("1", "true", "yes")
        try:
            local = mpd_library.search(args["text"])
            local_error = ""
        except mpd_library.MPDError as error:
            local, local_error = [], str(error)
        qobuz_error = ""
        if not local_only and renderer_running():
            try:
                answer = catalog().search(**args)
            except QobuzError as error:
                qobuz_error = str(error)
                answer = {"results": [], "count": 0, "more": False, "next_scan": 0,
                          "labels_seen": [], "queries": [], "query": args["text"],
                          "window": {"from": "", "to": ""}, "considered": 0,
                          "unstreamable": 0, "sort": args["sort"], "lowered": 0}
        elif not local_only:
            qobuz_error = "Qobuz unavailable: upmpdcli is not running"
            answer = {"results": [], "count": 0, "more": False, "next_scan": 0,
                      "labels_seen": [], "queries": [], "query": args["text"],
                      "window": {"from": "", "to": ""}, "considered": 0,
                      "unstreamable": 0, "sort": args["sort"], "lowered": 0}
        else:
            answer = {"results": [], "count": 0, "more": False, "next_scan": 0,
                      "labels_seen": [], "queries": [], "query": args["text"],
                      "window": {"from": "", "to": ""}, "considered": 0,
                      "unstreamable": 0, "sort": args["sort"], "lowered": 0}
        # Local collection results are plain text matches. Qobuz facets and AI
        # remain attached only to the Qobuz catalog response.
        combined = []
        qresults = answer.get("results", [])
        for i in range(max(len(local), len(qresults))):
            if i < len(local): combined.append(local[i])
            if i < len(qresults): combined.append(qresults[i])
        answer["results"] = annotate_dr(combined)
        answer["count"] = len(combined)
        answer["local_count"] = len(local)
        answer["local_error"] = local_error
        answer["qobuz_error"] = qobuz_error
        return jsonify({"ok": True, **answer})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502


@bp.route("/local/play", methods=["POST"])
def local_play():
    body = request.get_json(silent=True) or {}
    try:
        tracks = body.get("tracks")
        if (not isinstance(tracks, list) or any(not isinstance(t, dict) or not isinstance(t.get("file"), str)
                or t["file"].startswith("/") or ".." in t["file"].split("/") for t in tracks)):
            return jsonify({"ok": False, "error": "invalid local album"}), 400
        queued = mpd_library.queue(str(body.get("album_id", "")), body.get("mode", "replace"), tracks)
        return jsonify({"ok": True, "queued": queued})
    except mpd_library.MPDError as error:
        return jsonify({"ok": False, "error": str(error)}), 503


def _library_root() -> str | None:
    """Where DR14 reports are read from: MPD's own music_directory."""
    return _music_directory()


@bp.route("/local/status")
def local_status():
    return jsonify({**local_db.status(_state_dir(), _music_directory, _music_config()[1]),
                    "dr14_tools": _dr14_tools()})


@bp.route("/local/stop", methods=["POST"])
def local_stop():
    root = _music_directory()
    if not root:
        return jsonify({"ok": False, "error": "Music directory is not configured"}), 503
    try:
        result = local_db.stop_scan(_state_dir(), root)
    except OSError as error:
        return jsonify({"ok": False, "error": str(error)}), 503
    return jsonify(result), 200 if result["ok"] else 503


def _dr14_tools() -> dict:
    """Refresh installed-tool availability within a minute without busy polling."""
    return _dr14_tools_at(int(time.monotonic() // 60))


@lru_cache(maxsize=2)
def _dr14_tools_at(_minute: int) -> dict:
    import subprocess
    prefix = os.environ.get("PREFIX", "/usr/local")
    meter = os.path.join(prefix, "lib", "omdrcctrl", "drmeter.py")
    converter = os.path.join(prefix, "libexec", "omdrc", "scripts", "omdrc-cue-split.py")
    specs = (("cuetools", "cuebreakpoints", "-V", "Reads track boundaries from the CUE sheet"),
             ("shntool", "shnsplit", "-v", "Splits the FLAC and verifies the combined audio hash"),
             ("FLAC", "flac", "--version", "Encodes and checks lossless track files"),
             ("metaflac", "metaflac", "--version", "Writes and checks track tags"))
    tools = []
    for name, command, option, purpose in specs:
        path = shutil.which(command)
        version = ""
        if path:
            try:
                result = subprocess.run([path, option], capture_output=True, text=True, timeout=3)
                version = (result.stdout or result.stderr).splitlines()[0].strip()
            except (OSError, subprocess.TimeoutExpired, IndexError):
                pass
        tools.append({"name": name, "version": version, "path": path or "", "purpose": purpose})
    needed = ("cuebreakpoints", "cueprint", "cuetag.sh", "shnsplit", "shnhash", "flac", "metaflac")
    return {"cue_split_available": all(shutil.which(command) for command in needed)
            and os.path.isfile(converter) and os.path.isfile(meter),
            "tools": tools, "meter": {"path": meter, "available": os.path.isfile(meter),
                                        "purpose": "Measures each FLAC track with the TT Dynamic Range algorithm and writes dr14.txt"}}


@bp.route("/local/refresh", methods=["POST"])
def local_refresh():
    """Update MPD's index, then start the host's background DR14 scan."""
    import subprocess
    body = request.get_json(silent=True) or {}
    split_cue = body.get("split_cue", False)
    if not isinstance(split_cue, bool):
        return jsonify({"ok": False, "error": "split_cue must be true or false"}), 400
    if split_cue and not _dr14_tools()["cue_split_available"]:
        return jsonify({"ok": False, "error": "CUE splitting tools are not installed"}), 400
    script = os.path.join(os.environ.get("PREFIX", "/usr/local"),
                          "libexec", "omdrc", "scripts", "omdrc-mpd-update-dr14.sh")
    scan = local_db.scan_status(_state_dir())
    if scan["state"] == "running" and time.time() - scan["since"] < local_db.STALE_SCAN:
        return jsonify({"ok": True, "message": "A DR14 scan is already running"})
    env = dict(os.environ, OMDRC_SCAN_STATUS=os.path.join(_state_dir(), local_db.SCAN_STATUS_FILE))
    env["OMDRC_SPLIT_CUE"] = "1" if split_cue else "0"
    try:
        result = subprocess.run([script], capture_output=True, text=True, timeout=15, env=env)
    except (OSError, subprocess.TimeoutExpired) as error:
        return jsonify({"ok": False, "error": str(error)}), 503
    if result.returncode:
        return jsonify({"ok": False, "error": (result.stderr or result.stdout).strip()}), 503
    root = _music_directory()
    if root and os.path.isdir(root):
        local_db.counts(root, fresh=True)        # MPD may have found new folders
    return jsonify({"ok": True, "message": "MPD database updated; DR14 scan started"})


@bp.route("/local/art")
def local_art():
    """Serve MPD's albumart binary response for one indexed library track."""
    uri = request.args.get("file", "")
    if len(uri) > 4096 or not uri:
        return Response(status=404)
    if uri.startswith("/") or "\\" in uri or any(part in ("", ".", "..") for part in uri.split("/")):
        return Response(status=400)
    try:
        image, mime = mpd_library.albumart(uri)
        return Response(image, mimetype=mime,
                        headers={"Cache-Control": "public, max-age=86400"})
    except (mpd_library.MPDError, OSError) as error:
        status = 404 if "not found" in str(error).lower() or "no file exists" in str(error).lower() else 503
        # Never let a temporary MPD miss poison this stable artwork URL: the
        # browser can otherwise keep showing a broken image after a reload.
        return Response(status=status, headers={"Cache-Control": "no-store"})


@bp.route("/search/stream")
def search_stream():
    """The search above as server-sent events: {"ok": true, "partial": true, ...}
    frames while Qobuz is being read (in the final order, without performers),
    then one frame with the whole answer, or {"ok": false, "error"}; then the
    stream ends.  Always 200, so that an EventSource sees the error frame."""
    def frame(d):
        return f"data: {json.dumps(d, separators=(',', ':'))}\n\n"

    guard = _guard(False)
    if guard:
        body = guard[0].get_json()
        return Response(frame(body), mimetype="text/event-stream")
    try:
        args = _search_args()
    except QobuzError as error:
        return Response(frame({"ok": False, "error": str(error)}), mimetype="text/event-stream")
    local_only = request.args.get("local", "0").lower() in ("1", "true", "yes")

    frames: queue.Queue = queue.Queue()
    target = None if local_only else catalog()

    def run():
        try:
            mpd_library.set_music_directory(_library_root())
            try:
                local = mpd_library.search(args["text"])
                local_error = ""
            except mpd_library.MPDError as error:
                local, local_error = [], str(error)
            if not local_only and renderer_running():
                answer = target.search(**args, progress=lambda d: frames.put(
                    {"ok": True, **d, "results": annotate_dr(d.get("results") or [])}))
            elif not local_only:
                answer = {"results": [], "count": 0, "more": False, "next_scan": 0,
                          "labels_seen": [], "queries": [], "query": args["text"],
                          "window": {"from": "", "to": ""}, "considered": 0,
                          "unstreamable": 0, "sort": args["sort"], "lowered": 0}
                answer["qobuz_error"] = "Qobuz unavailable: upmpdcli is not running"
            else:
                answer = {"results": [], "count": 0, "more": False, "next_scan": 0,
                          "labels_seen": [], "queries": [], "query": args["text"],
                          "window": {"from": "", "to": ""}, "considered": 0,
                          "unstreamable": 0, "sort": args["sort"], "lowered": 0}
            qresults = answer.get("results", [])
            answer["results"] = [item for i in range(max(len(local), len(qresults)))
                                 for item in (([local[i]] if i < len(local) else []) +
                                              ([qresults[i]] if i < len(qresults) else []))]
            answer["results"] = annotate_dr(answer["results"])
            answer["count"] = len(answer["results"])
            answer["local_count"] = len(local)
            answer["local_error"] = local_error
            frames.put({"ok": True, **answer})
        except QobuzError as error:
            try:
                local = mpd_library.search(args["text"])
                frames.put({"ok": True, "query": args["text"], "results": local,
                            "count": len(local), "local_count": len(local), "more": False,
                            "next_scan": 0, "qobuz_error": str(error), "queries": [],
                            "window": {"from": "", "to": ""}, "considered": 0,
                            "unstreamable": 0, "sort": args["sort"], "lowered": 0})
            except mpd_library.MPDError as local_error:
                frames.put({"ok": False, "error": f"{error}; local collection unavailable: {local_error}"})
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
    albums = played().recent(int(limit) if limit.isdigit() else 50)
    for card in albums:                  # the user's own awards too (see AwardedAlbums)
        if "awards" in card:
            card["awards"] = awarded().merged(card["id"], card["awards"])
        card["rating"] = awarded().rating(card["id"])
    return jsonify({"ok": True, "albums": albums})


def _queue(album: dict, mode: str, start: str) -> dict:
    with _queue_lock:
        return _queue_album(album, mode, start)


def _queue_album(album: dict, mode: str, start: str) -> dict:
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

    tail_position = 0
    if mode == "append" and _queue_tail is not None:
        try:
            after, tail_position = _queue_tail()
        except Exception as error:
            raise openhome.OpenHomeError(f"cannot read MPD queue: {error}") from error

    zero_tail = mode == "append" and tail_position > 0 and after == 0
    queued: list[tuple[str, int]] = []
    try:
        for track_id, uri, metadata in entries:
            after = playlist.insert(after, uri, metadata)
            if not queued and zero_tail:
                try:
                    _move_to_end(after, tail_position)
                except Exception as error:
                    raise openhome.OpenHomeError(f"cannot move appended track: {error}") from error
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
        # classical labels have their own check boxes: only the rest is learned
        if not album.get("groups") and not re.search(r"classi|opera", album.get("genre", ""), re.I):
            answer["label_learned"] = artist_labels().record(album["artist"], album["label"])
    return jsonify({"ok": True, "mode": mode, **answer})


@bp.route("/album/<album_id>")
def album(album_id):
    guard = _guard(renderer_needed=False)
    if guard:
        return guard
    try:
        return jsonify({"ok": True, "album": catalog().album(album_id)})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502


@bp.route("/favorites", methods=["GET", "POST"])
def favorites():
    """Qobuz's own playlists are folder paths; album hearts are the fallback."""
    guard = _guard(renderer_needed=False)
    if guard:
        return guard
    try:
        cat, user_id = catalog(), _qobuz_user_id()
        if request.method == "GET":
            own = qobuz_favorites.playlists(cat, user_id)
            unfiled, covers = qobuz_favorites.library_snapshot(
                cat, user_id, own, qobuz_favorites.favorites(cat, user_id))
            folders = [{"id": str(p["id"]), "path": qobuz_favorites.legacy_path(p["name"]),
                        "tracks": p.get("tracks_count", 0)}
                       for p in own]
            return jsonify({"ok": True, "folders": folders,
                            "albums": unfiled, "covers": covers,
                            "order": qobuz_favorites.LibraryOrder(os.path.join(
                                _state_dir(), f"qobuz-library-order-{user_id}.json")).all()})
        body = request.get_json(silent=True) or {}
        action = body.get("action")
        album_id = str(body.get("album_id") or "")
        if not re.fullmatch(r"[0-9A-Za-z]+", album_id):
            raise QobuzError("bad album ID")
        if action == "add":
            result = qobuz_favorites.add(cat, user_id, album_id, body.get("path") or "")
            qobuz_favorites.invalidate(user_id)
            if album_id not in {a["id"] for a in qobuz_favorites.favorites(cat, user_id)}:
                cat._call("favorite/create", {"album_ids": album_id})
            return jsonify({"ok": True, **result})
        if action == "remove":
            folder_removed = qobuz_favorites.remove(
                cat, user_id, album_id, str(body.get("playlist_id") or ""))
            qobuz_favorites.invalidate(user_id)
            return jsonify({"ok": True, "folder_removed": folder_removed})
        if action == "unfavorite":
            cat._call("favorite/delete", {"album_ids": album_id})
            return jsonify({"ok": True})
        if action == "favorite":
            if album_id not in {a["id"] for a in qobuz_favorites.favorites(cat, user_id)}:
                cat._call("favorite/create", {"album_ids": album_id})
            return jsonify({"ok": True})
        raise QobuzError("unknown favorites action")
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 400


@bp.route("/favorites/order", methods=["POST"])
def favorite_order():
    guard = _guard(renderer_needed=False)
    if guard:
        return guard
    try:
        user_id = _qobuz_user_id()
        body = request.get_json(silent=True) or {}
        parent = body.get("parent")
        if not isinstance(parent, str):
            raise QobuzError("invalid folder path")
        store = qobuz_favorites.LibraryOrder(os.path.join(
            _state_dir(), f"qobuz-library-order-{user_id}.json"))
        store.save(parent, body.get("keys"))
        return jsonify({"ok": True})
    except (QobuzError, OSError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400


@bp.route("/favorites/folder", methods=["POST"])
def favorite_folder():
    guard = _guard(renderer_needed=False)
    if guard:
        return guard
    try:
        cat, user_id = catalog(), _qobuz_user_id()
        body = request.get_json(silent=True) or {}
        order = qobuz_favorites.LibraryOrder(os.path.join(
            _state_dir(), f"qobuz-library-order-{user_id}.json"))
        result = qobuz_favorites.folder_action(
            cat, user_id, body.get("path") or "", body.get("action") or "",
            order, body.get("name") or "", body.get("target") or "")
        qobuz_favorites.invalidate(user_id)
        return jsonify({"ok": True, **result})
    except (QobuzError, OSError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400


@bp.route("/favorites/playlist/<playlist_id>")
def favorite_playlist(playlist_id):
    guard = _guard(renderer_needed=False)
    if guard:
        return guard
    try:
        if not playlist_id.isdigit():
            raise QobuzError("bad playlist ID")
        cat, user_id = catalog(), _qobuz_user_id()
        if not any(str(p["id"]) == playlist_id for p in qobuz_favorites.playlists(cat, user_id)):
            raise QobuzError("Playlist is not owned by this Qobuz account")
        return jsonify({"ok": True, "albums": qobuz_favorites.playlist_albums(cat, playlist_id)})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 400


@bp.route("/awards")
def awards():
    guard = _guard(renderer_needed=False)
    if guard:
        return guard
    ids = [i for i in request.args.get("ids", "").split(",") if i.strip()]
    try:
        cat = catalog()
        found = cat.awards(ids)
        return jsonify({"ok": True, "awards": found, "ratings": cat.ratings(list(found))})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502


@bp.route("/awarded", methods=["GET", "POST"])
def awarded_albums():
    guard = _guard(renderer_needed=False)
    if guard:
        return guard
    if request.method == "GET":
        return jsonify({"ok": True, "albums": awarded().albums(), "presets": list(AWARD_PRESETS)})
    body = request.get_json(silent=True) or {}
    album_id = str(body.get("album_id") or "").strip()
    action = body.get("action")
    if action not in ("mark", "unmark", "rate"):
        return jsonify({"ok": False, "error": "want action mark|unmark|rate"}), 400
    try:
        card = album_card(catalog()._album_raw(album_id)) if album_id.isalnum() else {}
        if not card.get("id"):
            raise QobuzError("unknown album")
        store = awarded()
        if action == "mark":
            store.mark(card, body.get("name", ""), body.get("publication", ""))
        elif action == "unmark":
            store.unmark(album_id, body.get("name", ""))
        else:
            try:
                store.rate(card, int(body.get("rating", 0)))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "rating: 0 to 3"}), 400
        # the album's awards as shown everywhere: Qobuz's, then the user's
        qobuz = [a for a in catalog().awards([album_id]).get(album_id, []) if not a.get("mine")]
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502
    return jsonify({"ok": True, "awards": store.merged(album_id, qobuz), "rating": store.rating(album_id)})


@bp.route("/words")
def words():
    """Everything the search field completes from, sent once: a few tens of
    KB, and the page matches as the user types without asking again."""
    if not _settings().enabled:
        return jsonify({"ok": False, "error": "Qobuz search disabled"}), 404
    return jsonify({"ok": True, "words": word_list(), "learned": learned().recent(),
                    "artists": [[a, ls] for a, ls in artist_list()],
                    "artist_pairs": artist_labels().pairs()})


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
