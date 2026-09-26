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
#
# It plays in a partition of its own, never in the main queue: that queue may
# belong to the Qobuz Connect bridge, which treats any edit as foreign, forces
# its own track back on and loses its session ("Current track not found in
# queue").  The music is stopped (not resumed afterwards), the enabled outputs
# are borrowed into the side partition for the clicks, then handed back.

CLICK_LEAD_S = 1.0
CLICK_GAPS_MS = (700, 530, 860, 610, 940, 480, 770, 650, 890, 560, 720, 830, 590)
CLICK_TAIL_S = 1.2
# Quiet on purpose: a calibration runs at whatever volume the room is listening
# at, and a train of bursts must never stress a tweeter.  -30 dBFS is 30 dB under
# the loudest music; the music is stopped meanwhile, so the phone still hears them
# well above the room's silence (the page detects relative to it).
CLICK_DBFS = -30.0
CLICK_HZ = 1000
CLICK_BURST_MS = 8
_CLICK_URL_MARK = "/k/api/clicks.wav"
_CLICK_PARTITION = "omdrc-cal"
_click_lock = threading.Lock()


def click_track(rate: int) -> bytes:
    """16-bit stereo WAV: silence, then short, soft tone bursts (CLICK_HZ for
    CLICK_BURST_MS under a Hann window, peak CLICK_DBFS)."""
    rate = max(8000, min(384000, int(rate)))
    starts, t = [], CLICK_LEAD_S
    starts.append(t)
    for gap in CLICK_GAPS_MS:
        t += gap / 1000.0
        starts.append(t)
    total = int((t + CLICK_TAIL_S) * rate)
    amp = 32767 * 10 ** (CLICK_DBFS / 20)
    burst_n = int(CLICK_BURST_MS / 1000.0 * rate)
    burst = [amp * math.sin(2 * math.pi * CLICK_HZ * i / rate) * (0.5 - 0.5 * math.cos(2 * math.pi * i / burst_n))
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

    def lines(self, command: str) -> list:
        """Run one command; the reply's raw lines (for replies that repeat keys)."""
        self.sock.sendall((command + "\n").encode())
        out = []
        for line in self.f:
            line = line.rstrip("\n")
            if line == "OK":
                return out
            if line.startswith("ACK"):
                raise RuntimeError(line)
            out.append(line)
        raise RuntimeError("MPD closed the connection")

    def outputs(self) -> list:
        """This partition's outputs, one dict each."""
        outs = []
        for line in self.lines("outputs"):
            key, _, value = line.partition(": ")
            if key == "outputid":
                outs.append({})
            if outs:
                outs[-1][key.lower()] = value
        return outs


def _quote(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _return_outputs(names):
    """Hand borrowed outputs back to the default partition and drop the side one.
    Also the cleanup for a run that died half way (names = whatever is there)."""
    with _Mpd() as mpd:
        for name in names:
            try:
                mpd.cmd("moveoutput " + _quote(name))
            except Exception:
                pass
        try:
            mpd.cmd("delpartition " + _quote(_CLICK_PARTITION))
        except Exception:
            pass                              # never created, or already gone


def _stale_partition_outputs() -> list | None:
    """Outputs left in the side partition by an interrupted run."""
    with _Mpd() as mpd:
        if "partition: " + _CLICK_PARTITION not in mpd.lines("listpartitions"):
            return None
        mpd.cmd("partition " + _quote(_CLICK_PARTITION))
        names = [o.get("outputname") for o in mpd.outputs() if o.get("plugin") != "dummy"]
        mpd.cmd("partition default")      # a client inside blocks delpartition
        return names


def _run_click_test(url: str, borrowed: list):
    try:
        with _Mpd() as mpd:
            mpd.cmd("partition " + _quote(_CLICK_PARTITION))
            song_id = mpd.cmd("addid " + _quote(url))["id"]
            mpd.cmd("playid " + song_id)
            started, deadline = False, time.monotonic() + 30
            while time.monotonic() < deadline:
                time.sleep(0.2)
                st = mpd.cmd("status")
                if st.get("state") == "play":
                    started = True
                elif started or time.monotonic() > deadline - 22:
                    break                     # finished, or it never started
            mpd.cmd("stop")
            mpd.cmd("partition default")  # a client inside blocks delpartition
    except Exception:
        pass
    finally:
        try:
            _return_outputs(borrowed)
        except Exception:
            pass
        _click_lock.release()


@bp.route("/api/clicktest", methods=["POST"])
def clicktest():
    """Stop what is playing and play the click track through MPD in a side
    partition.  Returns at once; the track takes about 11 s."""
    if not _click_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "a click test is already running"}), 409
    borrowed = []
    try:
        stale = _stale_partition_outputs()
        if stale is not None:
            _return_outputs(stale)
        with _Mpd() as mpd:
            before = mpd.cmd("status")
            if before.get("state") != "stop":
                mpd.cmd("stop")
            names = [o["outputname"] for o in mpd.outputs()
                     if o.get("outputenabled") == "1" and o.get("plugin") != "dummy"]
            if not names:
                raise RuntimeError("MPD has no enabled output to play the clicks on")
            mpd.cmd("newpartition " + _quote(_CLICK_PARTITION))
            mpd.cmd("partition " + _quote(_CLICK_PARTITION))
            for name in names:
                mpd.cmd("moveoutput " + _quote(name))
                borrowed.append(name)
            mpd.cmd("partition default")
        rate = int(_playback_rate() or 48000)
        host = request.host.split(":")[-1] if ":" in request.host else "80"
        url = f"http://127.0.0.1:{host}{_CLICK_URL_MARK}?rate={rate}&t={int(time.time())}"
        threading.Thread(target=_run_click_test, args=(url, borrowed), daemon=True).start()
    except Exception as error:
        try:
            _return_outputs(borrowed)
        except Exception:
            pass
        _click_lock.release()
        return jsonify({"ok": False, "error": str(error)}), 500
    duration = CLICK_LEAD_S + sum(CLICK_GAPS_MS) / 1000.0 + CLICK_TAIL_S
    starts = [round(CLICK_LEAD_S * 1000)]
    for gap in CLICK_GAPS_MS:
        starts.append(starts[-1] + gap)
    # everything the calibration log needs to know about what was played
    return jsonify({"ok": True, "rate": rate, "seconds": round(duration, 1),
                    "stopped": before.get("state", "stop"), "outputs": borrowed,
                    "level_dbfs": CLICK_DBFS, "tone_hz": CLICK_HZ, "burst_ms": CLICK_BURST_MS,
                    "burst_starts_ms": starts})


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
