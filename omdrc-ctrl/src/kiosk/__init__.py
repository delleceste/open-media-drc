"""Small-screen (7" touch) UI for omdrcctrl, served under /k/.

Deliberately separate from app.py and templates/index.html: this package is a
pure client of the panel's existing JSON / SSE endpoints (/spectrum/stream,
/drc/*, /qconnect/*, /audio/chain, /system/* ...), so changes to the desktop UI
never touch it and vice versa.  The only things it needs from the panel are
handed over once, in init_app():

    kiosk.init_app(app, commands=lambda: COMMANDS,
                   features=lambda: {"drdb": DRDB.enabled, "cdin": CDIN_ENABLED},
                   mpc=_kiosk_mpc)

Layout, page order and every widget live in static/ (plain JS, no build step).
"""

import os

from flask import Blueprint, jsonify, render_template, request

_HERE = os.path.dirname(os.path.abspath(__file__))

bp = Blueprint(
    "kiosk", __name__,
    url_prefix="/k",
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)

_commands = lambda: []          # noqa: E731 - replaced by init_app()
_features = lambda: {}          # noqa: E731
_mpc = None                     # (args) -> (ok, error), set by init_app()


def _asset_version() -> str:
    """Newest mtime under static/, so a redeploy busts the browser cache."""
    newest = 0
    for root, _dirs, files in os.walk(os.path.join(_HERE, "static")):
        for name in files:
            try:
                newest = max(newest, int(os.stat(os.path.join(root, name)).st_mtime))
            except OSError:
                pass
    return str(newest)


@bp.route("/")
def shell():
    return render_template("kiosk_shell.html", asset_version=_asset_version())


# The panel's own buttons (commands.conf), minus the shell command line: the
# kiosk needs to know what to offer and how to confirm it, never how it runs.
_PUBLIC_KEYS = ("id", "what", "group", "type", "button", "confirm",
                "confirm_message", "url")


@bp.route("/api/config")
def config():
    return jsonify({
        "ok": True,
        "commands": [{k: c[k] for k in _PUBLIC_KEYS if k in c} for c in _commands()],
        "features": {k: bool(v) for k, v in _features().items()},
    })


# Transport for the Now page's play / pause / stop.  Only these three words are
# accepted, and each maps to a fixed mpc command - nothing from the request is
# ever passed on to a shell.
_TRANSPORT = {"play": ["play"], "pause": ["pause"], "stop": ["stop"]}


@bp.route("/api/transport", methods=["POST"])
def transport():
    action = str((request.get_json(silent=True) or {}).get("action", ""))
    if action not in _TRANSPORT:
        return jsonify({"ok": False, "error": "unknown transport action"}), 400
    if _mpc is None:
        return jsonify({"ok": False, "error": "no MPD client configured"}), 503
    ok, error = _mpc(_TRANSPORT[action])
    return jsonify({"ok": ok, "error": error} if not ok else {"ok": True})


def init_app(app, commands=None, features=None, mpc=None):
    """Register the kiosk blueprint.

    `commands` returns the panel's command list; `features` returns which
    optional panels are enabled (the kiosk hides what the panel hides); `mpc`
    runs one mpc command and returns (ok, error) for the transport buttons.
    """
    global _commands, _features, _mpc
    if commands is not None:
        _commands = commands
    if features is not None:
        _features = features
    if mpc is not None:
        _mpc = mpc
    app.register_blueprint(bp)
