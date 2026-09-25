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

import io
import math
import os
import re
import struct
import threading
import time
import wave

from flask import Blueprint, Response, jsonify, render_template, request

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
_mpc = None                     # (args) -> (ok, stdout | error), set by init_app()
_playback_rate = lambda: 48000  # noqa: E731 - the rate to make a click track at
_mpd_port = lambda: None        # noqa: E731 - MPD's port (None: 6600)


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


# ── click-track calibration ────────────────────────────────────────────────────
# A precise, optional alternative to calibrating on the music: MPD plays a short
# track of clicks through the normal path (so the meters and the speakers both get
# it) while the phone listens; irregular spacing makes the match unambiguous.
# Playback is paused for it and put back afterwards, queue and position included.

CLICK_LEAD_S = 1.0
CLICK_GAPS_MS = (700, 530, 860, 610, 940, 480, 770, 650, 890, 560, 720, 830, 590)
CLICK_TAIL_S = 1.2
CLICK_DBFS = -12.0
_CLICK_URL_MARK = "/k/api/clicks.wav"
_click_lock = threading.Lock()


def click_track(rate: int) -> bytes:
    """16-bit stereo WAV: silence, then short 2 kHz bursts (5 ms, Hann window)."""
    rate = max(8000, min(384000, int(rate)))
    starts, t = [], CLICK_LEAD_S
    starts.append(t)
    for gap in CLICK_GAPS_MS:
        t += gap / 1000.0
        starts.append(t)
    total = int((t + CLICK_TAIL_S) * rate)
    amp = 32767 * 10 ** (CLICK_DBFS / 20)
    burst_n = int(0.005 * rate)
    burst = [amp * math.sin(2 * math.pi * 2000 * i / rate) * (0.5 - 0.5 * math.cos(2 * math.pi * i / burst_n))
             for i in range(burst_n)]
    samples = [0] * total
    for s in starts:
        k0 = int(s * rate)
        for i, v in enumerate(burst):
            if k0 + i < total:
                samples[k0 + i] = int(v)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<hh", v, v) for v in samples))
    return out.getvalue()


@bp.route("/api/clicks.wav")
def clicks_wav():
    try:
        rate = int(request.args.get("rate", "48000"))
    except ValueError:
        rate = 48000
    return Response(click_track(rate), mimetype="audio/wav",
                    headers={"Cache-Control": "no-store"})


class _Mpd:
    """Minimal MPD protocol client (one connection per use)."""

    def __init__(self):
        import socket
        port = int(_mpd_port() or 6600)
        self.sock = socket.create_connection(("localhost", port), timeout=5)
        self.f = self.sock.makefile("r", encoding="utf-8", errors="replace")
        if not self.f.readline().startswith("OK MPD"):
            raise RuntimeError("not an MPD server")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.sock.close()

    def cmd(self, *lines: str) -> dict:
        """Run one command, or several as an atomic command list; the reply as a dict."""
        body = lines[0] + "\n" if len(lines) == 1 else \
            "command_list_begin\n" + "".join(ln + "\n" for ln in lines) + "command_list_end\n"
        self.sock.sendall(body.encode())
        out = {}
        for line in self.f:
            line = line.rstrip("\n")
            if line == "OK":
                return out
            if line.startswith("ACK"):
                raise RuntimeError(line)
            key, _, value = line.partition(": ")
            out.setdefault(key.lower(), value)
        raise RuntimeError("MPD closed the connection")


def _quote(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _run_click_test(url: str, before: dict):
    try:
        with _Mpd() as mpd:
            song_id = mpd.cmd("addid " + _quote(url))["id"]
            mpd.cmd("playid " + song_id)
            started, deadline = False, time.monotonic() + 30
            while time.monotonic() < deadline:
                time.sleep(0.2)
                st = mpd.cmd("status")
                ours = st.get("songid") == song_id and st.get("state") == "play"
                if ours:
                    started = True
                elif started or time.monotonic() > deadline - 22:
                    break                     # finished, or it never started
    except Exception:
        pass
    finally:
        try:
            with _Mpd() as mpd:
                mpd.cmd("stop")
                try:
                    mpd.cmd("deleteid " + song_id)
                except Exception:
                    pass                      # consume mode may have removed it already
                pos, elapsed = before.get("song"), before.get("elapsed")
                if before.get("state") in ("play", "pause") and pos is not None:
                    seek = f"seek {pos} {elapsed or '0'}"
                    # paused: seek and pause as one command list, so nothing is heard
                    mpd.cmd(seek) if before["state"] == "play" else mpd.cmd(seek, "pause 1")
        except Exception:
            pass
        _click_lock.release()


@bp.route("/api/clicktest", methods=["POST"])
def clicktest():
    """Pause what is playing, play the click track through MPD, then put it back.
    Returns at once; the track takes about 11 s."""
    if not _click_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "a click test is already running"}), 409
    try:
        with _Mpd() as mpd:
            before = mpd.cmd("status")
            if before.get("state") == "play":
                mpd.cmd("pause 1")
        rate = int(_playback_rate() or 48000)
        host = request.host.split(":")[-1] if ":" in request.host else "80"
        url = f"http://127.0.0.1:{host}{_CLICK_URL_MARK}?rate={rate}&t={int(time.time())}"
        threading.Thread(target=_run_click_test, args=(url, before), daemon=True).start()
    except Exception as error:
        _click_lock.release()
        return jsonify({"ok": False, "error": str(error)}), 500
    duration = CLICK_LEAD_S + sum(CLICK_GAPS_MS) / 1000.0 + CLICK_TAIL_S
    return jsonify({"ok": True, "rate": rate, "seconds": round(duration, 1),
                    "restores": before.get("state", "stop")})


def init_app(app, commands=None, features=None, mpc=None, playback_rate=None, mpd_port=None):
    """Register the kiosk blueprint.

    `commands` returns the panel's command list; `features` returns which
    optional panels are enabled (the kiosk hides what the panel hides); `mpc`
    runs one mpc command and returns (ok, error) for the transport buttons.
    """
    global _commands, _features, _mpc, _playback_rate, _mpd_port
    if commands is not None:
        _commands = commands
    if features is not None:
        _features = features
    if mpc is not None:
        _mpc = mpc
    if playback_rate is not None:
        _playback_rate = playback_rate
    if mpd_port is not None:
        _mpd_port = mpd_port
    app.register_blueprint(bp)
