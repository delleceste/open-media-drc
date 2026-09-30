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
import threading
import time

from flask import Blueprint, Response, jsonify, request

import openhome
import qobuz_ai
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
_state_dir = lambda: ""         # noqa: E731 - where the played list lives

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
# The shipped completion list, next to this module; re-read when it changes.
WORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qobuz_words.txt")
_words = (None, [])                          # (mtime, entries)
# Artists and their labels, shipped the same way: typing an artist offers its labels.
ARTISTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qobuz_artists.txt")
_artists = (None, [])                        # (mtime, [(artist, [label])])
_artist_labels: dict[str, ArtistLabels] = {}
_openhome: openhome.Renderer | None = None


def init_app(app, settings, upmpdcli_conf, read_options, token_file, plugin_dir,
             renderer_running, state_dir, queue_tail=None, move_to_end=None) -> None:
    global _settings, _upmpdcli_conf, _read_options, _token_file, _plugin_dir
    global _renderer_running, _state_dir, _queue_tail, _move_to_end
    _settings, _upmpdcli_conf, _read_options = settings, upmpdcli_conf, read_options
    _token_file, _plugin_dir, _renderer_running = token_file, plugin_dir, renderer_running
    _state_dir = state_dir
    _queue_tail, _move_to_end = queue_tail, move_to_end
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
            request.args.get("genre", ""), _number("offset", int) or 0)})
    except QobuzError as error:
        return jsonify({"ok": False, "error": str(error)}), 502


@bp.route("/search")
def search():
    """?q=text&label=Pentatone&label=Decca (or label=Pentatone,Decca)
    &last=2 (years back from today) or &from=2021&to=2026 (calendar years)
    &sort=relevance|date &awarded=1 &scan=<albums per query> &enrich=0"""
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
