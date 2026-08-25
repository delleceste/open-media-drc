#!/usr/bin/env python3
import glob
import hashlib
import json
import math
import os
import platform
import pwd
import re
import shutil
import shlex
import subprocess
import configparser
import threading
import time
import tempfile
from urllib.parse import quote, unquote, urlsplit
import markdown as md_lib
from flask import Flask, Response, render_template, jsonify, request, send_from_directory

_HERE = os.path.dirname(os.path.abspath(__file__))

# Service control differs by OS: Linux drives systemd --user units, FreeBSD the
# rc(8) services.  See _service_running / _service_action / _unit_active.
_IS_LINUX = platform.system() == "Linux"

# The live spectrum analyzer taps an MPD `fifo` output.  That path is POSIX
# (os.mkfifo + non-blocking read) and works the same on Linux and FreeBSD; only
# Windows (no os.mkfifo) is excluded.
_SPECTRUM_OS_OK = platform.system() in ("Linux", "FreeBSD")

# ── FreeBSD /dev/sndstat fmt bitmask (sys/soundcard.h) ───────────────────────
_AFMT_BITS: list[tuple[int, str]] = [
    (0x00100000, "PCM_CAP_ANALOGOUT"),
    (0x00200000, "PCM_CAP_ANALOGIN"),
    (0x00400000, "PCM_CAP_DIGITALOUT"),
    (0x00800000, "PCM_CAP_DIGITALIN"),
    (0x00000008, "AFMT_U8"),
    (0x00000010, "AFMT_S16_LE"),
    (0x00000020, "AFMT_S16_BE"),
    (0x00000040, "AFMT_S8"),
    (0x00001000, "AFMT_S32_LE"),
    (0x00002000, "AFMT_S32_BE"),
    (0x00004000, "AFMT_U32_LE"),
]

def _decode_afmt(val: int) -> str:
    names, rest = [], val
    for bit, name in _AFMT_BITS:
        if val & bit:
            names.append(name)
            rest &= ~bit
    if rest:
        names.append(hex(rest))
    return "|".join(names) if names else hex(val)


def _decode_sndstat_fmt(line: str) -> str:
    def repl(match: re.Match) -> str:
        raw = match.group(1)
        return f"fmt: {_decode_afmt(int(raw, 16))}"

    return re.sub(r'\bfmt\s+(0x[0-9a-fA-F]+)', repl, line)

app = Flask(__name__, template_folder=os.path.join(_HERE, "templates"))

# Paths to qconnect2mpd output files.
# Set by [qconnect] section in commands.conf; env vars are the fallback.
QCONNECT_STATUS_FILE = os.environ.get("QCONNECT_STATUS_FILE", "/tmp/qconnect2mpd-status.txt")
QCONNECT_LOG_FILE    = os.environ.get("QCONNECT_LOG_FILE",    "/tmp/qconnect2mpd.log")

# qobuzconnect2mpd and upmpdcli are mutually exclusive renderers driving MPD;
# only one may run at a time.  On Linux they are systemd --user services
# (switched with `systemctl --user start|stop`, polled with `is-active`); on
# FreeBSD they are rc services (`sudo service <name> onestart|onestop`, polled
# with `service <name> onestatus`).  See _service_running / _service_action.
QCONNECT_SERVICE   = "qobuzconnect2mpd"
UPMPDCLI_SERVICE   = "upmpdcli"
SWITCHABLE_SERVICES = (QCONNECT_SERVICE, UPMPDCLI_SERVICE)

# Which renderer the boot service starts when the toggle has never recorded a
# choice (first boot after an install, or a wiped state dir).  Mirrors
# scripts/omdrc-renderer's DEFAULT_RENDERER / $OMDRC_RENDERER_DEFAULT, which is
# what actually decides it — keep the two in step so the panel does not promise
# a different renderer than the one that comes up.
_renderer_default   = os.environ.get("OMDRC_RENDERER_DEFAULT", UPMPDCLI_SERVICE)
RENDERER_BOOT_DEFAULT = (_renderer_default if _renderer_default in SWITCHABLE_SERVICES
                         else UPMPDCLI_SERVICE)

# ── log viewer and log alerts ────────────────────────────────────────────────
# The Logs card shows any log file the box writes; the alert rules watch those
# same files for lines that mean something is wrong but that nothing else in the
# UI would surface — the Qobuz OAuth token going missing being the case that
# prompted this (upmpdcli keeps serving, it just cannot log in).
#
# Sources come from [logs] in commands.conf, rules from [alert:<id>] sections.
# Both fall back to the defaults below so an install whose commands.conf predates
# this still gets the renderer logs and the Qobuz warning.
LOG_TAIL_BYTES = 200_000     # ceiling on what one /logs/tail read returns
LOG_SCAN_BYTES = 65_536      # tail of each source the alert scan looks at
LOG_ALERT_INTERVAL = 20      # seconds between browser /logs/alerts polls
_LOG_SETTING_KEYS = frozenset({"tail_bytes", "scan_bytes", "alert_interval"})
_ALERT_PREFIX = "alert:"
_SEVERITIES = ("error", "warn", "info", "ok")

# A rule fires when its `pattern` matches a line that is *newer* than the last
# line matching `clears`, so only the last outcome in the log is reported.
#
# All three read the plugin console log, because upmpdcli's Qobuz plugin writes
# every line quoted here to the stderr it inherits from upmpdcli (upmplgutils.uplog
# / cmdtalkplugin.log), not to upmpdcli's own logfilename.  Keeping a rule and its
# `clears` on one stream is what makes "newer than" meaningful.
#
# The patterns match a failure *sense* — "not done", "returns", "failed" — rather
# than the mere co-occurrence of "qobuz" and "oauth".  A successful sign-in logs
# `session: init_oauth: auth_code ...` and `Qobuz: trackuri: OAuth initialisation`,
# both of which name Qobuz and OAuth while meaning the opposite, and a looser
# pattern reports a working login as broken.
#
# There is no explicit success line to look for: qobuz-app.py logs "Qobuz
# running" and then calls session.login(), which is silent when it works and
# prints "Qobuz login: oauth initialisation not done" (no token) or
# "/user/login returns ..." (token refused) when it does not.  So a connection is
# reported as good when a startup or a completed sign-in is followed by no
# failure line — the strongest statement the log actually supports.
# Written without the (?i) flag so they can be composed; the rules add it.
_QOBUZ_NO_TOKEN = (r"(oauth\s+initiali\w*\s+not\s+done|oauth.*\bnot\s+done\b"
                   r"|\bnot\s+done\b.*oauth|oauth\s+initiali\w*\s+missing"
                   r"|(qobuz|oauth).*\bno\s+token\b)")
_QOBUZ_REFUSED = r"(/user/login\s+returns|tried login but failed|qobuz.*login.*fail)"
# Lines that mean the opposite: the plugin came up, or a sign-in completed.
_QOBUZ_GOOD = (r"(qobuz.*running|got\s+auth_code|init_oauth:\s*auth_code"
               r"|trackuri.*oauth\s+initiali)")
_UPMPDCLI_MPD_GOOD = r"mpdcli::openconn:\s*mpd connected ok"
_UPMPDCLI_MPD_BAD = (r"(mpd connection failed|mpdcli::openconn:.*failed"
                      r"|mpdcli::eventloop:\s*could not open connection)")

# qobuzconnect2mpd's own signals.  It is a different program with a different
# login: its own OAuth flow (`-L`) and its own token under qconnectstatedir.  It
# shares AUDIO_USER with the controller on both OSes, but not a token format or
# path with upmpdcli, so its post-authentication startup log remains the clean
# source of truth instead of teaching the generic alert scanner another file.
_QCONNECT_BAD = (r"(not authenticated|no auth token"
                 r"|cannot stream until it is authenticated"
                 r"|waiting for .*oauth login|login not completed within the timeout"
                 r"|oauth (code exchange|token exchange|callback) failed"
                 r"|token could not be persisted)")
_QCONNECT_GOOD = r"qobuzconnect2mpd:\s*qobuz plugin connected"
_QCONNECT_MPD_GOOD = r"qconnect2mpd:\s*mpd connected ok"
_QCONNECT_MPD_BAD = r"qconnect2mpd:\s*mpd connect failed"

DEFAULT_LOG_ALERTS: list[dict] = [
    {
        "id":       "qobuz_oauth",
        "pattern":  f"(?i){_QOBUZ_NO_TOKEN}",
        "clears":   f"(?i){_QOBUZ_GOOD}",
        # The token file is what the log line is *about*, so a stored token
        # settles it whatever a stale tail still says.
        "clears_file":         "@qobuz_token@",
        "clears_file_pattern": r"user_auth_token\s*=\s*\S",
        "message":  "upmpdcli: Qobuz OAuth initialisation not done",
        "hint":     "Press Qobuz sign-in: the panel runs the OAuth script and "
                    "shows the URL to open in this browser.  upmpdcli must be "
                    "running to receive the redirect.",
        "severity": "warn",
        "sources":  ["upmpdcli-console"],
        "service":  "upmpdcli",
        "action":   "qobuz-oauth",
    },
    {
        # A token exists but Qobuz would not take it, so no file check here:
        # this is precisely the case a stored token does not settle.
        "id":       "qobuz_login",
        "pattern":  f"(?i){_QOBUZ_REFUSED}",
        "clears":   f"(?i){_QOBUZ_GOOD}",
        "message":  "upmpdcli: Qobuz refused the stored login",
        "hint":     "The token is there but was not accepted — sign in again.",
        "severity": "warn",
        "sources":  ["upmpdcli-console"],
        "service":  "upmpdcli",
        "action":   "qobuz-oauth",
    },
    {
        "id":       "qobuz_ok",
        "pattern":  f"(?i){_QOBUZ_GOOD}",
        "clears":   f"(?i)({_QOBUZ_NO_TOKEN}|{_QOBUZ_REFUSED})",
        "message":  "upmpdcli: Qobuz plugin connected",
        "hint":     "Started or signed in, and reported no login failure afterwards.",
        "severity": "ok",
        "sources":  ["upmpdcli-console"],
        "service":  "upmpdcli",
    },
    {
        "id":       "upmpdcli_mpd_ok",
        "pattern":  f"(?i){_UPMPDCLI_MPD_GOOD}",
        "clears":   f"(?i){_UPMPDCLI_MPD_BAD}",
        "message":  "upmpdcli: MPD connected",
        "hint":     "The renderer established its local MPD control connection.",
        "severity": "ok",
        "sources":  ["upmpdcli"],
        "service":  "upmpdcli",
    },
    {
        "id":       "qconnect_auth",
        "pattern":  f"(?i){_QCONNECT_BAD}",
        "clears":   f"(?i){_QCONNECT_GOOD}",
        "message":  "qobuzconnect2mpd is not signed in to Qobuz",
        "hint":     "Press sign in: the panel starts qobuzconnect2mpd's own "
                    "OAuth bootstrap and opens its remote-browser redirect flow.",
        "severity": "warn",
        "sources":  ["qobuzconnect2mpd"],
        "service":  "qobuzconnect2mpd",
        "action":   "qconnect-oauth",
    },
    {
        "id":       "qconnect_mpd_ok",
        "pattern":  f"(?i){_QCONNECT_MPD_GOOD}",
        "clears":   f"(?i){_QCONNECT_MPD_BAD}",
        "message":  "qobuzconnect2mpd: MPD connected",
        "hint":     "The renderer established its local MPD control connection.",
        "severity": "ok",
        "sources":  ["qobuzconnect2mpd"],
        "service":  "qobuzconnect2mpd",
    },
    {
        "id":       "qconnect_ok",
        "pattern":  f"(?i){_QCONNECT_GOOD}",
        "clears":   f"(?i){_QCONNECT_BAD}",
        "message":  "qobuzconnect2mpd: Qobuz plugin connected",
        "hint":     "Qobuz accepted its OAuth-backed cloud session.",
        "severity": "ok",
        "sources":  ["qobuzconnect2mpd"],
        "service":  "qobuzconnect2mpd",
    },
]

# ── Qobuz OAuth initialisation ───────────────────────────────────────────────
# The token upmpdcli's Qobuz plugin needs is obtained by signing in at qobuz.com
# and letting it redirect back to upmpdcli's own media-server HTTP port, which
# hands the code to the plugin (qobuz-app.py `trackuri`, path /qobuz/oauth/).
# upmpdcli's qobuz-init-oauth.py does not serve anything itself: it only prints
# the two sign-in URLs and exits, which is why this can be driven from a phone —
# the panel runs the script, shows the URL, and watches the token file.
#
# Consequences worth knowing: upmpdcli must be RUNNING to catch the redirect
# (its port, not qobuzconnect2mpd's — the two renderers are unrelated programs),
# and the browser must be able to reach the host in the redirect URL.
QOBUZ_OAUTH_SCRIPT = "/usr/local/share/upmpdcli/cdplugins/qobuz/qobuz-init-oauth.py"
QOBUZ_UPMPDCLI_CONF = ""      # empty: search the usual locations
QOBUZ_CACHE_CONFIG = ""       # empty: derive from upmpdcli.conf's cachedir
QOBUZ_OAUTH_TIMEOUT = 45      # the script fetches the Qobuz app id over the net
# `clears_file = @qobuz_token@` in an alert rule means the token file above,
# wherever it turns out to live, so a rule stays host-independent.
_QOBUZ_TOKEN_PLACEHOLDER = "@qobuz_token@"

# qobuzconnect2mpd has a separate OAuth token and a different bootstrap.  Its
# `-L` process prints one sign-in URL, keeps its HTTP callback alive for up to
# five minutes, writes the token, and exits.  The panel tracks that process in
# the background, then starts the normal service so the renderer is immediately
# usable (and its normal startup log can report the green connected state).
QCONNECT_OAUTH_BINARY = "qobuzconnect2mpd"
QCONNECT_OAUTH_CONFIG = ""       # empty: search the service's standard paths
QCONNECT_OAUTH_USER = ""        # same AUDIO_USER as omdrcctrl on both OSes
QCONNECT_OAUTH_URL_TIMEOUT = 45   # time allowed for app-id lookup / URL output

_QCONNECT_OAUTH_LOCK = threading.Condition()
_QCONNECT_OAUTH_PROCESS: subprocess.Popen | None = None
_QCONNECT_OAUTH_SESSION: dict = {
    "phase": "idle", "output": "", "error": "", "returncode": None,
    "started_at": None,
}

# Set to the defaults just below the parsers, then replaced by load_config().
LOG_SOURCES: list[dict] = []
LOG_ALERTS:  list[dict] = []

# [monitor] section defaults
TOPCPU_THRESHOLD = 4.0   # minimum %CPU to include in the top-processes list
MONITOR_INTERVAL = 5     # seconds between MPD refreshes
TOPCPU_INTERVAL = 3      # seconds between top-CPU refreshes
SNDSTAT_INTERVAL = 5     # seconds between audio-device refreshes
BRUTEFIR_INTERVAL = 5    # seconds between brutefir CPU refreshes
_TOPCPU_CACHE: dict | None = None
_TOPCPU_CACHE_AT = 0.0

# [spectrum] section defaults.  The source is an MPD FIFO output, disabled by
# default in mpd.conf and enabled only while a browser is actively streaming.
SPECTRUM_ENABLED = False
SPECTRUM_OUTPUT_NAME = "OMDRC Spectrum"
SPECTRUM_FIFO = "/tmp/omdrc-spectrum.fifo"
SPECTRUM_RATE = 48000
SPECTRUM_BITS = 32
SPECTRUM_CHANNELS = 2
SPECTRUM_REFRESH_HZ = 10.0
SPECTRUM_FFT_SIZE = 16384
SPECTRUM_PRECISION_FFT_SIZE = 65536
SPECTRUM_BANDS = 24
SPECTRUM_VU_MODE = "bars"
SPECTRUM_FLOOR_DB = -40.0
SPECTRUM_MIN_FREQ = 31.5
# Manual fine-tune (ms) added to the auto-measured DRC filter delay used to keep
# the FIFO-derived display in sync with the audible, post-BruteFIR signal.  The
# bulk delay (filter group delay) is measured from the active filter; this trim
# absorbs the smaller, constant BruteFIR block + ALSA loopback buffer latency.
SPECTRUM_DRC_DELAY_TRIM_MS = 0.0
# Interactive "sync" slider: a runtime delta (ms) the listener adds on top of the
# auto-measured base delay to line the display up with what they actually hear.
# Neutral at 0, bounded by the two config-editable limits below, and remembered
# across restarts in a small state file (see _DELTA_STATE_FILE).
SPECTRUM_DRC_DELAY_DELTA_MS = 0.0
SPECTRUM_DRC_DELAY_DELTA_MIN_MS = -1000.0
SPECTRUM_DRC_DELAY_DELTA_MAX_MS = 2000.0

GROUP_ORDER  = ["drc", "apps", "system"]
# ── the CD / S-PDIF input bridge (omdrc-cdin) ────────────────────────────────
# omdrc-cdin is a daemon, not a command.  It is meant to run continuously —
# whether or not the CD player is on, whether or not the interface is plugged
# in — because it holds the output device only while audio is actually on the
# wire and releases it after a run of digital silence.  So the panel REPORTS it
# rather than driving it, and everything reported comes out of the daemon's own
# log, whose lines are a contract for this purpose (cdin/src/main.c).
#
# Two axes, kept deliberately apart:
#
#   availability  can each end be opened at all?  This is what the LED shows,
#                 because when either end is unreachable no disc can play.
#   holding       is the output device open at this instant?  Normal operation
#                 swings that back and forth all day and it is never a fault —
#                 showing it as one would train the eye to ignore the LED.
CDIN_ENABLED = True
CDIN_LOG_FILE = "/tmp/omdrc-cdin.log"
CDIN_PROCESS = "omdrc-cdin"
CDIN_SERVICE = "omdrc_cdin"
CDIN_INTERVAL = 5        # seconds between browser /cdin/status polls
CDIN_MAX_EVENTS = 20     # past error/warn events the card keeps
CDIN_SCAN_BYTES = 65_536 # tail of the log the card reads
# Whether the card may start and stop the daemon.  Watching it is the normal
# mode and needs no privilege; the two buttons need the rc.d grant in sudoers,
# so a box without it turns this off rather than offering a button that can
# only fail.  Stopping is a real thing to want: the bridge holds
# /dev/dsp.play while a disc plays, and that is the handle to give back by
# hand when something downstream needs the chain to itself.
CDIN_CONTROL = True
# How long to wait for the process to appear (or go) after the rc verb returns.
# `service ... onestart` forks a daemon(8) and answers immediately, so the
# answer is not yet evidence of anything.
CDIN_SETTLE_SECONDS = 6.0

_CDIN_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+ \[(?P<lvl>ERR|WRN|INF|DBG)\] (?P<msg>.*)$")
# "capture /dev/dsp.capture: available" / "playback /dev/dsp.play: unavailable — ..."
_CDIN_DEVICE = re.compile(
    r"^(?P<dev>capture|playback) (?P<path>\S+): "
    r"(?P<what>available|unavailable|acquired|released)\b[ ,—-]*(?P<rest>.*)$")
_CDIN_STATE = re.compile(r"^state (?P<state>[a-z-]+): (?P<why>.*)$")
_CDIN_STATS = re.compile(r"^\[stats\] (?P<body>.*)$")
# The input path may be a directory of WAVs (the test rig), so it can contain
# spaces; the output is always a device node.
_CDIN_START = re.compile(
    r"^omdrc-cdin \S+ starting: in=(?P<inpath>.+?) out=(?P<outpath>\S+) \d+ Hz")

_CDIN_DEVICE_LABEL = {"capture": "capture", "playback": "output"}

# ── the audio chain card: who is holding the sound devices ───────────────────
# Everything else in this panel reports what open-media-drc *believes*: drc.sh's
# saved state, the cdin daemon's log, MPD's own status.  That is exactly the
# wrong instrument for the failure this card exists for — a process OUTSIDE the
# chain sitting on a device node.  A stray PulseAudio on /dev/dsp.dac, an mpv
# left running, a second brutefir from a half-finished restart: none of them
# appear in any log this project owns, and every one of them makes the chain
# fail to open with a bare EBUSY.  So the question is put to the OS instead —
# fstat(1) on FreeBSD, fuser(1) on Linux — which answer for every process on the
# box whether or not it belongs to us.
#
# Four roles, in flow order.  On FreeBSD they are the role symlinks
# omdrc_audio keeps pointed at the right cards (etc/rc.d/omdrc_audio) plus
# the two cuse nodes virtual_oss creates; on Linux they are ALSA nodes under
# /dev/snd, named here the way brutefir names them ("hw:1,1") and translated to
# the node that fuser can be asked about.
#
#   capture   the CD / S-PDIF input card                   omdrc-cdin reads it
#   bridge    what the players write into                  MPD, omdrc-cdin, mpv
#   loop      the other side of that bridge                brutefir reads it
#   dac       the DAC everything ends up at                brutefir writes it
#
# Leave a role empty to drop it from the diagram: a box with no capture card
# sets `capture =` and the input block disappears rather than sitting there
# permanently grey.
CHAIN_ENABLED = True
CHAIN_INTERVAL = 4       # seconds between browser /audio/chain polls
# Whether to re-ask through `sudo -n` when the plain tool runs as an
# unprivileged user.  fstat(1) and fuser(1) can only report file descriptors of
# processes the caller may debug, so omdrcctrl running as AUDIO_USER sees its
# own chain (brutefir, MPD, omdrc-cdin all run as that user) but NOT a root
# process squatting a device — which is half the point of the card.  With the
# usual sudoers grant one escalated call per poll closes that hole; without one
# the `sudo -n` fails immediately, is never retried, and the card says so.
CHAIN_PRIVILEGED = True
# Per-role device overrides from [chain]; empty string means "use the default
# for this OS", and a role missing from the defaults is simply not drawn.
CHAIN_DEVICES: dict[str, str] = {}

_CHAIN_ROLES = ("capture", "bridge", "loop", "dac")
_CHAIN_DEFAULT_DEVICES = {
    "FreeBSD": {"capture": "/dev/dsp.capture", "bridge": "/dev/dsp.play",
                "loop": "/dev/dsp.loop", "dac": "/dev/dsp.dac"},
    # snd-aloop is card 1 by convention here: MPD plays into hw:1,0 and brutefir
    # reads hw:1,1 (brutefir_defaults.linux.conf).  The capture card has no
    # conventional number, so it stays off until [chain] names it.
    "Linux":   {"capture": "", "bridge": "hw:1,0",
                "loop": "hw:1,1", "dac": "hw:0,0"},
}
# Which side of an ALSA pcm each role is: the bridge and the DAC are written to,
# the capture card and the loopback's far side are read from.
_CHAIN_ALSA_SIDE = {"capture": "c", "bridge": "p", "loop": "c", "dac": "p"}
_CHAIN_ROLE_TITLE = {"capture": "Capture in", "bridge": "Bridge",
                     "loop": "Bridge", "dac": "DAC out"}

# What a process holding one of these devices is, said in the diagram.  The
# point of the list is not the friendly names — it is that anything NOT in it is
# drawn as a warning: an unexpected holder is the failure this card is for.
_CHAIN_APPS = {
    "brutefir":         "DRC convolver",
    "omdrc-cdin":       "CD / S-PDIF bridge",
    "mpd":              "music player daemon",
    "musicpd":          "music player daemon",
    "virtual_oss":      "OSS bridge",
    "mpv":              "video player",
    "kodi":             "media centre",
    "kodi.bin":         "media centre",
    "qobuzconnect2mpd": "Qobuz Connect renderer",
    "upmpdcli":         "UPnP renderer",
}
# Processes that are a problem by their mere presence, with the reason.  These
# still get drawn — loudly — because seeing them is the whole point.
_CHAIN_INTRUDERS = {
    "pulseaudio":  "PulseAudio must not run on this box — it breaks bit-perfect",
    "pipewire":    "PipeWire must not run on this box — it breaks bit-perfect",
    "pipewire-pulse": "PipeWire must not run on this box — it breaks bit-perfect",
    "jackd":       "JACK is holding an audio device",
    "sndiod":      "sndiod is holding an audio device",
}


GROUP_LABELS = {
    "drc":    "Digital Room Correction",
    "apps":   "Applications",
    "system": "System",
}

COMMANDS: list[dict] = []
CMD_MAP:  dict[str, dict] = {}


def _default_log_sources() -> list[dict]:
    """Logs worth showing on a stock install.  upmpdcli writes its own log to
    logfilename; its cdplugin subprocesses (Qobuz among them) write to the
    inherited stderr instead, which the service scripts capture separately."""
    return [
        {"id": "mpd",               "label": "MPD",                  "path": os.path.expanduser("~/.local/share/mpd/mpd.log")},
        {"id": "upmpdcli",         "label": "upmpdcli",           "path": "/tmp/upmpdcli.log"},
        {"id": "upmpdcli-console", "label": "upmpdcli (plugins)", "path": "/tmp/upmpdcli-console.log"},
        {"id": "qobuzconnect2mpd", "label": "qobuzconnect2mpd",   "path": QCONNECT_LOG_FILE},
        {"id": "brutefir",         "label": "BruteFIR",           "path": "/tmp/brutefir.out"},
        {"id": "omdrc-cdin",       "label": "CD input",           "path": CDIN_LOG_FILE},
    ]


def _parse_log_sources(cfg: configparser.ConfigParser) -> list[dict]:
    """[logs] maps a source id to a path; `<id>.label` renames it in the UI."""
    if not cfg.has_section("logs"):
        return _default_log_sources()
    labels, sources = {}, []
    for key, value in cfg.items("logs"):
        if key in _LOG_SETTING_KEYS:
            continue
        if key.endswith(".label"):
            labels[key[:-len(".label")]] = value.strip()
            continue
        path = value.strip()
        if path:
            sources.append({"id": key, "label": key, "path": path})
    for src in sources:
        src["label"] = labels.get(src["id"], src["label"])
    return sources


def _compile_alert(rule: dict) -> dict:
    """Attach the compiled `pattern` / `clears` / `clears_file_pattern` regexes."""
    clears = rule.get("clears", "")
    file_pattern = rule.get("clears_file_pattern", "")
    return dict(rule,
                regex=re.compile(rule["pattern"]),
                clears_regex=re.compile(clears) if clears else None,
                clears_file_regex=re.compile(file_pattern) if file_pattern else None)


def _parse_log_alerts(cfg: configparser.ConfigParser) -> list[dict]:
    """Each [alert:<id>] section is one rule: a regex, an optional regex that
    cancels it again, and what to tell the listener while it is matched."""
    rules = []
    for sid in cfg.sections():
        if not sid.lower().startswith(_ALERT_PREFIX):
            continue
        sec = cfg[sid]
        rid = sid[len(_ALERT_PREFIX):].strip() or sid
        pattern = sec.get("pattern", "").strip()
        if not pattern:
            raise ValueError(f"[{sid}] missing required key: 'pattern'")
        severity = sec.get("severity", "warn").strip().lower()
        if severity not in _SEVERITIES:
            severity = "warn"
        rule = {
            "id":       rid,
            "pattern":  pattern,
            "clears":   sec.get("clears", "").strip(),
            "message":  sec.get("message", "").strip() or rid,
            "hint":     sec.get("hint", "").strip(),
            "severity": severity,
            "sources":  [s.strip() for s in sec.get("sources", "").split(",") if s.strip()],
            "action":   sec.get("action", "").strip(),
            "service":  sec.get("service", "").strip(),
            "clears_file":         sec.get("clears_file", "").strip(),
            "clears_file_pattern": sec.get("clears_file_pattern", "").strip(),
        }
        try:
            rules.append(_compile_alert(rule))
        except re.error as e:
            raise ValueError(f"[{sid}] invalid pattern: {e}") from None
    if rules:
        return rules
    return [_compile_alert(r) for r in DEFAULT_LOG_ALERTS]


LOG_SOURCES = _default_log_sources()
LOG_ALERTS = [_compile_alert(r) for r in DEFAULT_LOG_ALERTS]


def load_config(path: str) -> None:
    global COMMANDS, CMD_MAP, QCONNECT_STATUS_FILE, QCONNECT_LOG_FILE
    global LOG_SOURCES, LOG_ALERTS, LOG_TAIL_BYTES, LOG_SCAN_BYTES, LOG_ALERT_INTERVAL
    global QOBUZ_OAUTH_SCRIPT, QOBUZ_UPMPDCLI_CONF, QOBUZ_CACHE_CONFIG, QOBUZ_OAUTH_TIMEOUT
    global QCONNECT_OAUTH_BINARY, QCONNECT_OAUTH_CONFIG, QCONNECT_OAUTH_USER
    global QCONNECT_OAUTH_URL_TIMEOUT
    global TOPCPU_THRESHOLD, MONITOR_INTERVAL, TOPCPU_INTERVAL
    global SNDSTAT_INTERVAL, BRUTEFIR_INTERVAL
    global SPECTRUM_ENABLED, SPECTRUM_OUTPUT_NAME, SPECTRUM_FIFO
    global SPECTRUM_RATE, SPECTRUM_BITS, SPECTRUM_CHANNELS
    global SPECTRUM_REFRESH_HZ, SPECTRUM_FFT_SIZE, SPECTRUM_PRECISION_FFT_SIZE, SPECTRUM_BANDS
    global SPECTRUM_VU_MODE, SPECTRUM_FLOOR_DB, SPECTRUM_MIN_FREQ
    global SPECTRUM_DRC_DELAY_TRIM_MS, SPECTRUM_DRC_DELAY_DELTA_MS
    global SPECTRUM_DRC_DELAY_DELTA_MIN_MS, SPECTRUM_DRC_DELAY_DELTA_MAX_MS
    global CDIN_ENABLED, CDIN_LOG_FILE, CDIN_PROCESS, CDIN_SERVICE
    global CDIN_INTERVAL, CDIN_MAX_EVENTS, CDIN_CONTROL
    global CHAIN_ENABLED, CHAIN_INTERVAL, CHAIN_PRIVILEGED, CHAIN_DEVICES
    cfg = configparser.ConfigParser()
    if not cfg.read(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    # [qconnect] is a settings section, not a command — read and skip it.
    if cfg.has_section("qconnect"):
        QCONNECT_STATUS_FILE = cfg.get("qconnect", "status_file", fallback=QCONNECT_STATUS_FILE)
        QCONNECT_LOG_FILE    = cfg.get("qconnect", "log_file",    fallback=QCONNECT_LOG_FILE)

    # [monitor] is a settings section — read and skip it.
    if cfg.has_section("monitor"):
        TOPCPU_THRESHOLD = cfg.getfloat("monitor", "topcpu_threshold", fallback=TOPCPU_THRESHOLD)
        MONITOR_INTERVAL = max(1, cfg.getint("monitor", "monitor_interval", fallback=MONITOR_INTERVAL))
        TOPCPU_INTERVAL = max(1, cfg.getint("monitor", "topcpu_interval", fallback=TOPCPU_INTERVAL))
        SNDSTAT_INTERVAL = max(1, cfg.getint("monitor", "sndstat_interval", fallback=SNDSTAT_INTERVAL))
        BRUTEFIR_INTERVAL = max(1, cfg.getint("monitor", "brutefir_interval", fallback=BRUTEFIR_INTERVAL))

    if cfg.has_section("spectrum"):
        SPECTRUM_ENABLED = cfg.getboolean("spectrum", "enabled", fallback=SPECTRUM_ENABLED)
        SPECTRUM_OUTPUT_NAME = cfg.get("spectrum", "mpd_output_name", fallback=SPECTRUM_OUTPUT_NAME)
        SPECTRUM_FIFO = cfg.get("spectrum", "fifo_path", fallback=SPECTRUM_FIFO)
        SPECTRUM_RATE = max(8000, cfg.getint("spectrum", "sample_rate", fallback=SPECTRUM_RATE))
        SPECTRUM_BITS = cfg.getint("spectrum", "bits", fallback=SPECTRUM_BITS)
        SPECTRUM_CHANNELS = max(1, cfg.getint("spectrum", "channels", fallback=SPECTRUM_CHANNELS))
        SPECTRUM_REFRESH_HZ = max(1.0, cfg.getfloat("spectrum", "refresh_hz", fallback=SPECTRUM_REFRESH_HZ))
        SPECTRUM_FFT_SIZE = max(4096, cfg.getint("spectrum", "fft_size", fallback=SPECTRUM_FFT_SIZE))
        SPECTRUM_PRECISION_FFT_SIZE = max(
            SPECTRUM_FFT_SIZE,
            cfg.getint("spectrum", "precision_fft_size", fallback=SPECTRUM_PRECISION_FFT_SIZE),
        )
        SPECTRUM_BANDS = max(6, min(32, cfg.getint("spectrum", "bands", fallback=SPECTRUM_BANDS)))
        SPECTRUM_VU_MODE = cfg.get("spectrum", "vu_mode", fallback=SPECTRUM_VU_MODE).strip().lower()
        if SPECTRUM_VU_MODE not in ("bars", "needles"):
            SPECTRUM_VU_MODE = "bars"
        SPECTRUM_FLOOR_DB = max(-90.0, min(-24.0, cfg.getfloat("spectrum", "floor_db", fallback=SPECTRUM_FLOOR_DB)))
        SPECTRUM_MIN_FREQ = max(5.0, min(200.0, cfg.getfloat("spectrum", "min_frequency", fallback=SPECTRUM_MIN_FREQ)))
        SPECTRUM_DRC_DELAY_TRIM_MS = cfg.getfloat("spectrum", "drc_delay_trim_ms", fallback=SPECTRUM_DRC_DELAY_TRIM_MS)
        SPECTRUM_DRC_DELAY_DELTA_MIN_MS = cfg.getfloat(
            "spectrum", "drc_delay_delta_min_ms", fallback=SPECTRUM_DRC_DELAY_DELTA_MIN_MS)
        SPECTRUM_DRC_DELAY_DELTA_MAX_MS = cfg.getfloat(
            "spectrum", "drc_delay_delta_max_ms", fallback=SPECTRUM_DRC_DELAY_DELTA_MAX_MS)
        if SPECTRUM_DRC_DELAY_DELTA_MAX_MS < SPECTRUM_DRC_DELAY_DELTA_MIN_MS:
            SPECTRUM_DRC_DELAY_DELTA_MIN_MS, SPECTRUM_DRC_DELAY_DELTA_MAX_MS = (
                SPECTRUM_DRC_DELAY_DELTA_MAX_MS, SPECTRUM_DRC_DELAY_DELTA_MIN_MS)

    # The slider positions are runtime settings, not config values: restore the
    # last ones the listener dialled in (clamped to the config bounds/limits).
    saved_delta = _read_state_float(_DELTA_STATE_FILE)
    if saved_delta is not None:
        SPECTRUM_DRC_DELAY_DELTA_MS = saved_delta
    SPECTRUM_DRC_DELAY_DELTA_MS = _clamp_delta(SPECTRUM_DRC_DELAY_DELTA_MS)
    saved_floor = _read_state_float(_FLOOR_STATE_FILE)
    if saved_floor is not None:
        SPECTRUM_FLOOR_DB = max(-90.0, min(-24.0, saved_floor))

    # [logs] and [alert:<id>] are settings sections — read them here, and skip
    # them (like the other reserved ones) when collecting commands below.
    if cfg.has_section("logs"):
        LOG_TAIL_BYTES = max(4096, cfg.getint("logs", "tail_bytes", fallback=LOG_TAIL_BYTES))
        LOG_SCAN_BYTES = max(1024, cfg.getint("logs", "scan_bytes", fallback=LOG_SCAN_BYTES))
        LOG_ALERT_INTERVAL = max(5, cfg.getint("logs", "alert_interval", fallback=LOG_ALERT_INTERVAL))
    LOG_SOURCES = _parse_log_sources(cfg)
    LOG_ALERTS = _parse_log_alerts(cfg)

    # [cdin] is a settings section — the CD bridge is a daemon the panel
    # watches, not a command it runs, so it has no [section] of the command
    # kind.  Its log path must match omdrc_cdin_logfile in rc.conf.
    if cfg.has_section("cdin"):
        CDIN_ENABLED = cfg.getboolean("cdin", "enabled", fallback=CDIN_ENABLED)
        CDIN_LOG_FILE = cfg.get("cdin", "log_file", fallback=CDIN_LOG_FILE).strip()
        CDIN_PROCESS = cfg.get("cdin", "process", fallback=CDIN_PROCESS).strip()
        CDIN_SERVICE = cfg.get("cdin", "service", fallback=CDIN_SERVICE).strip()
        CDIN_INTERVAL = max(1, cfg.getint("cdin", "refresh", fallback=CDIN_INTERVAL))
        CDIN_MAX_EVENTS = max(1, cfg.getint("cdin", "max_events", fallback=CDIN_MAX_EVENTS))
        CDIN_CONTROL = cfg.getboolean("cdin", "control", fallback=CDIN_CONTROL)

    # [chain] is a settings section — the audio-chain diagram names the four
    # device roles it asks the OS about.  Leave a key out to take the default
    # for this platform, set it empty to drop that role from the diagram.
    if cfg.has_section("chain"):
        CHAIN_ENABLED = cfg.getboolean("chain", "enabled", fallback=CHAIN_ENABLED)
        CHAIN_INTERVAL = max(1, cfg.getint("chain", "refresh", fallback=CHAIN_INTERVAL))
        CHAIN_PRIVILEGED = cfg.getboolean("chain", "privileged", fallback=CHAIN_PRIVILEGED)
        CHAIN_DEVICES = {role: cfg.get("chain", role)
                         for role in _CHAIN_ROLES if cfg.has_option("chain", role)}

    if cfg.has_section("qobuz_oauth"):
        QOBUZ_OAUTH_SCRIPT = cfg.get("qobuz_oauth", "script", fallback=QOBUZ_OAUTH_SCRIPT)
        QOBUZ_UPMPDCLI_CONF = cfg.get("qobuz_oauth", "upmpdcli_config", fallback=QOBUZ_UPMPDCLI_CONF)
        QOBUZ_CACHE_CONFIG = cfg.get("qobuz_oauth", "cache_config", fallback=QOBUZ_CACHE_CONFIG)
        QOBUZ_OAUTH_TIMEOUT = max(5, cfg.getint("qobuz_oauth", "timeout", fallback=QOBUZ_OAUTH_TIMEOUT))

    if cfg.has_section("qconnect_oauth"):
        QCONNECT_OAUTH_BINARY = cfg.get(
            "qconnect_oauth", "binary", fallback=QCONNECT_OAUTH_BINARY).strip()
        QCONNECT_OAUTH_CONFIG = cfg.get(
            "qconnect_oauth", "config", fallback=QCONNECT_OAUTH_CONFIG).strip()
        QCONNECT_OAUTH_USER = cfg.get(
            "qconnect_oauth", "run_user", fallback=QCONNECT_OAUTH_USER).strip()
        QCONNECT_OAUTH_URL_TIMEOUT = max(
            5, cfg.getint("qconnect_oauth", "url_timeout",
                          fallback=QCONNECT_OAUTH_URL_TIMEOUT))

    _RESERVED = {"qconnect", "monitor", "spectrum", "logs", "qobuz_oauth",
                 "qconnect_oauth", "cdin", "chain"}
    COMMANDS = []
    for sid in cfg.sections():
        if sid in _RESERVED or sid.lower().startswith(_ALERT_PREFIX):
            continue
        c = dict(cfg[sid])
        c["id"] = sid
        for key in ("what", "group", "type"):
            if key not in c:
                raise ValueError(f"[{sid}] missing required key: '{key}'")
        if c["type"] not in ("READ", "WRITE", "LINK"):
            raise ValueError(f"[{sid}] type must be READ, WRITE or LINK, got: '{c['type']}'")
        if c["type"] in ("READ", "WRITE") and "cmd" not in c:
            raise ValueError(f"[{sid}] missing required key: 'cmd'")
        if c["type"] == "WRITE" and "button" not in c:
            raise ValueError(f"[{sid}] WRITE command missing 'button' key")
        if c["type"] == "LINK" and "url" not in c:
            raise ValueError(f"[{sid}] LINK command missing 'url' key")
        COMMANDS.append(c)
    CMD_MAP = {c["id"]: c for c in COMMANDS}


def _groups() -> list[tuple]:
    d: dict[str, list] = {}
    for c in COMMANDS:
        d.setdefault(c["group"], []).append(c)
    order = GROUP_ORDER + [g for g in d if g not in GROUP_ORDER]
    return [
        (g, GROUP_LABELS.get(g, g.replace("_", " ").title()), d[g])
        for g in order if g in d
    ]


def _dac_unit() -> str:
    """pcm unit of the DAC, resolved from the /dev/dsp.dac role symlink.

    FreeBSD hands out pcm unit numbers in attach order, so dev.pcm.0.* is the
    DAC only by luck on a box with more than one card.  The omdrc_audio
    service keeps /dev/dsp.dac on the right one; a symlink cannot cover a sysctl
    OID, so the unit is read back from the link ("dsp1" -> "1").  Falls back to
    unit 0, which is what a single-card box has.
    """
    try:
        target = os.path.basename(os.readlink("/dev/dsp.dac"))
    except OSError:
        return "0"
    if target.startswith("dsp") and target[3:].isdigit():
        return target[3:]
    return "0"


def _env() -> dict:
    e = dict(os.environ)
    if not e.get("HOME") or e["HOME"] == "/":
        e["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    e.setdefault("XDG_CONFIG_HOME", os.path.join(e["HOME"], ".config"))
    e.setdefault("DISPLAY", ":0")
    # `systemctl --user` (renderer switching) needs the user session bus.
    # omdrcctrl runs as a system-scope service (User=<user>), which inherits
    # neither XDG_RUNTIME_DIR nor DBUS_SESSION_BUS_ADDRESS, so systemctl cannot
    # find the bus ("$DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not
    # defined").  Derive them from the uid when the runtime dir exists.
    if _IS_LINUX:
        run_dir = e.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        if os.path.isdir(run_dir):
            e["XDG_RUNTIME_DIR"] = run_dir
            e.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={run_dir}/bus")
    # FreeBSD rc.d services start with a minimal PATH that omits /usr/local/{s,}bin
    # where brutefir, mpc, virtual_oss, pgrep, … live.
    path_dirs = e.get("PATH", "").split(":")
    for d in ("/usr/local/sbin", "/usr/local/bin"):
        if d not in path_dirs:
            path_dirs.insert(0, d)
    e["PATH"] = ":".join(path_dirs)
    return e


def _find_dyn_details(cmd: dict, config_name: str) -> str | None:
    root = cmd.get("details_root")
    if not root:
        return None
    for fname in ("README.md", "INDEX.md"):
        path = os.path.join(root, config_name, fname)
        if os.path.isfile(path):
            return path
    return None


def _unit_active(unit: str) -> bool:
    """True if a systemd unit is active.  Checks the --user scope first (the
    renderers and web UIs run there after the Linux scope alignment) then the
    system scope; on FreeBSD systemctl is absent, so both fail and this is False."""
    for scope in (["--user"], []):
        try:
            r = subprocess.run(
                ["systemctl", *scope, "is-active", "--quiet", unit],
                timeout=3, capture_output=True, env=_env(),
            )
            if r.returncode == 0:
                return True
        except Exception:
            pass
    return False


def _process_running(process: str) -> bool:
    try:
        r = subprocess.run(
            ["pgrep", "-x", process],
            timeout=3, capture_output=True,
        )
        return r.returncode == 0
    except Exception:
        return False


def _process_name(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return os.path.basename(parts[0]) if parts else ""


def _hide_from_topcpu(row: dict) -> bool:
    name = _process_name(row["name"]).lower()
    if row["pid"] == "0":
        return True
    if name in {"idle", "kernel", "ps", "pgrep"}:
        return True
    return name.startswith("[") and name.endswith("]")


def _ps_processes() -> list[dict]:
    candidates = [
        ["ps", "axo", "user,pid,pcpu,comm"],
        ["ps", "ax", "-o", "user", "-o", "pid", "-o", "pcpu", "-o", "comm"],
        ["ps", "ax", "-o", "user=,pid=,pcpu=,comm="],
    ]
    errors = []
    for cmd in candidates:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            errors.append((r.stderr or r.stdout).strip())
            continue
        rows = []
        for line in r.stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            if parts[1].upper() == "PID" or parts[2].upper() in ("%CPU", "PCPU"):
                continue
            try:
                rows.append({
                    "user": parts[0],
                    "pid": parts[1],
                    "cpu": float(parts[2]),
                    "name": parts[3].strip(),
                })
            except ValueError:
                continue
        if rows:
            return rows
    raise RuntimeError("; ".join(e for e in errors if e) or "could not parse ps output")


def _tail_file(path: str, limit: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit), os.SEEK_SET)
            return f.read().decode(errors="replace").strip()
    except OSError:
        return ""


def _tail_text(path: str, limit: int) -> tuple[str, bool]:
    """Last `limit` bytes of a file, plus whether the read started mid-file.  A
    byte-bounded read can land inside a line, so the leading fragment is dropped
    — the log viewer and the alert scanner both work line by line."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        start = max(0, f.tell() - limit)
        f.seek(start, os.SEEK_SET)
        data = f.read().decode("utf-8", errors="replace")
    if start > 0:
        _, sep, rest = data.partition("\n")
        data = rest if sep else ""
    return data, start > 0


def _read_text_quietly(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _command_failure_output(cmd: dict, log_path: str) -> str:
    parts = []
    out = _tail_file(log_path)
    if out:
        parts.append(out)

    if "drc.sh" in cmd.get("cmd", ""):
        brutefir_out = _tail_file("/tmp/brutefir.out")
        if brutefir_out:
            parts.append("--- /tmp/brutefir.out ---\n" + brutefir_out)

    return "\n".join(parts).strip()


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _wait_and_cleanup(proc: subprocess.Popen, path: str) -> None:
    proc.wait()
    _unlink_quietly(path)


# ── MPD helpers ───────────────────────────────────────────────────────────────

def _mpd_conf_from_cmdline(cmdline: str) -> str | None:
    try:
        tokens = shlex.split(cmdline)
    except ValueError:
        tokens = cmdline.split()
    i = 1  # skip argv[0] (binary path)
    while i < len(tokens):
        t = tokens[i]
        if t in ("--config", "-c") and i + 1 < len(tokens):
            return tokens[i + 1]
        if t.startswith("--config="):
            return t.split("=", 1)[1]
        if not t.startswith("-"):
            return t   # first non-flag positional = config file
        i += 1
    return None


def _mpd_port_from_conf(conf_path: str) -> str | None:
    try:
        with open(conf_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.startswith("#"):
                    continue
                m = re.match(r'^port\s+"(\d+)"', s)
                if m:
                    return m.group(1)
                m = re.match(r'^bind_to_address\s+"[^"]*:(\d+)"', s)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def _mpc_client() -> list[str] | None:
    preferred = ("musicpc", "mpc") if platform.system() == "FreeBSD" else ("mpc", "musicpc")
    path = _env().get("PATH")
    for name in preferred:
        exe = shutil.which(name, path=path)
        if exe:
            return [exe]
    return None


def _parse_mpc_audio(audio: str) -> dict:
    parsed = {"sample_rate": None, "bit_depth": None, "channels": None}
    m = re.search(r'(\d+(?:\.\d+)?)\s*kHz\b', audio, re.I)
    if m:
        parsed["sample_rate"] = int(round(float(m.group(1)) * 1000))
    else:
        m = re.search(r'(\d+)\s*Hz\b', audio, re.I)
        if m:
            parsed["sample_rate"] = int(m.group(1))

    m = re.search(r'(\d+)\s*bits?\b', audio, re.I)
    if m:
        parsed["bit_depth"] = int(m.group(1))

    m = re.search(r'(\d+)\s*channels?\b', audio, re.I)
    if m:
        parsed["channels"] = int(m.group(1))
    elif re.search(r'\bstereo\b', audio, re.I):
        parsed["channels"] = 2
    elif re.search(r'\bmono\b', audio, re.I):
        parsed["channels"] = 1

    if parsed["sample_rate"] is None:
        m = re.search(r'\b(\d{4,6})\s*:\s*(\d+)\s*:\s*(\d+)\b', audio)
        if m:
            parsed["sample_rate"] = int(m.group(1))
            parsed["bit_depth"] = int(m.group(2))
            parsed["channels"] = int(m.group(3))
    return parsed


def _mpd_audio_via_protocol(port: str | None) -> str:
    """Query MPD directly for the audio field.

    Modern mpc (0.35+) dropped the 'audio:' line from its default status
    output on Linux.  The MPD protocol always includes it when playing.
    """
    import socket
    try:
        p = int(port) if port else 6600
        with socket.create_connection(("localhost", p), timeout=3) as sock:
            with sock.makefile("r", encoding="utf-8", errors="replace") as f:
                if not f.readline().startswith("OK"):
                    return ""
                sock.sendall(b"status\n")
                for line in f:
                    line = line.rstrip("\n")
                    if line == "OK" or line.startswith("ACK"):
                        break
                    if line.lower().startswith("audio:"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


def _mpc_status(port: str | None = None) -> dict:
    cmd = _mpc_client()
    if not cmd:
        return {"client": None, "state": "unknown", "error": "mpc/musicpc not found"}
    if port:
        cmd = cmd + ["-p", str(port)]
    cmd.append("status")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=_env())
    text = (r.stdout + r.stderr).strip()
    info = {
        "client": os.path.basename(cmd[0]),
        "state": "stopped",
        "song": "",
        "audio": "",
        "sample_rate": None,
        "bit_depth": None,
        "channels": None,
        "error": "",
    }
    if r.returncode != 0:
        info["state"] = "unknown"
        info["error"] = text or f"exit {r.returncode}"
        return info

    lines = text.splitlines()
    state_line = next((line for line in lines if re.search(r'\[(playing|paused|stopped)\]', line)), "")
    if state_line:
        m = re.search(r'\[(playing|paused|stopped)\]', state_line)
        if m:
            info["state"] = m.group(1)
        state_idx = lines.index(state_line)
        if state_idx > 0:
            info["song"] = lines[state_idx - 1].strip()

    for line in lines:
        if re.search(r'^(audio|format)\s*:', line, re.I):
            _, _, audio = line.partition(":")
            info["audio"] = audio.strip()
            info.update(_parse_mpc_audio(info["audio"]))
            break

    if not info["audio"] and info["state"] in ("playing", "paused"):
        audio = _mpd_audio_via_protocol(port)
        if audio:
            info["audio"] = audio
            info.update(_parse_mpc_audio(audio))

    return info


# ── Live spectrum analyzer ───────────────────────────────────────────────────

def _mpd_output_id(name: str, port: str | None = None) -> str | None:
    cmd = _mpc_client()
    if not cmd:
        return None
    if port:
        cmd = cmd + ["-p", str(port)]
    try:
        r = subprocess.run(cmd + ["outputs"], capture_output=True, text=True,
                           timeout=5, env=_env())
    except Exception:
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        m = re.match(r'Output\s+(\d+)\s+\((.*)\)\s+is\s+(enabled|disabled)', line)
        if m and m.group(2) == name:
            return m.group(1)
    return None


def _mpd_output_action(name: str, action: str) -> tuple[bool, str]:
    """Enable or disable an MPD output by name."""
    port = _resolve_mpd_port()
    out_id = _mpd_output_id(name, port)
    if out_id is None:
        return False, f'MPD output "{name}" not found'
    cmd = _mpc_client()
    if not cmd:
        return False, "mpc/musicpc not found"
    if port:
        cmd = cmd + ["-p", str(port)]
    try:
        r = subprocess.run(cmd + [action, out_id], capture_output=True, text=True,
                           timeout=5, env=_env())
        if r.returncode == 0:
            return True, ""
        return False, (r.stderr or r.stdout).strip() or f"mpc {action} failed"
    except subprocess.TimeoutExpired:
        return False, "mpc timeout"
    except OSError as e:
        return False, str(e)


def _spectrum_band_label(freq: float) -> str:
    if freq >= 1000:
        v = freq / 1000.0
        return f"{v:.1f}k" if v < 10 and abs(v - round(v)) > 0.05 else f"{v:.0f}k"
    return f"{freq:.1f}".rstrip("0").rstrip(".")


def _spectrum_band_defs(bands: int, nyquist: float, min_freq: float | None = None) -> list[dict]:
    import numpy as np
    preferred = [
        10.0, 12.5, 16.0, 20.0, 25.0, 31.5, 40.0, 50.0,
        63.0, 80.0, 100.0, 125.0, 160.0, 250.0, 400.0, 630.0,
        1000.0, 1600.0, 2500.0, 4000.0, 6300.0, 10000.0,
        16000.0, 20000.0,
    ]
    low = SPECTRUM_MIN_FREQ if min_freq is None else min_freq
    centers = [f for f in preferred if low <= f < nyquist]
    if not centers:
        centers = [min(10.0, nyquist)]
    if bands < len(centers):
        idx = np.unique(np.round(np.linspace(0, len(centers) - 1, bands)).astype(int))
        centers = [centers[int(i)] for i in idx]

    edges = []
    for i, center in enumerate(centers):
        if i == 0:
            lo = max(1.0, center / math.sqrt(centers[i + 1] / center)) if len(centers) > 1 else center / math.sqrt(2.0)
        else:
            lo = math.sqrt(centers[i - 1] * center)
        if i == len(centers) - 1:
            hi = min(nyquist, center * math.sqrt(center / centers[i - 1])) if i > 0 else min(nyquist, center * math.sqrt(2.0))
        else:
            hi = math.sqrt(center * centers[i + 1])
        edges.append({
            "freq": center,
            "label": _spectrum_band_label(center),
            "lo": max(0.0, lo),
            "hi": min(float(nyquist), hi),
        })
    return edges


def _spectrum_tiers(fft_size: int, rate: int, multi: bool) -> list[dict]:
    """Analysis tiers for the multi-resolution band display.

    Low frequencies use the full window (fine bin spacing needed for bass);
    high frequencies use progressively shorter windows (fast transient response
    for cymbals/drums).  Tier 0 is always the full window (longest).  A single
    tier is returned when `multi` is False (precision/measurement mode) or the
    window is already short."""
    import numpy as np
    lengths = [fft_size]
    if multi:
        for L in (max(2048, fft_size // 4), max(1024, fft_size // 8)):
            if L < lengths[-1]:
                lengths.append(L)
    tiers = []
    for L in lengths:
        w = np.hanning(L).astype(np.float32)
        tiers.append({
            "n": L,
            "window": w,
            "freqs": np.fft.rfftfreq(L, 1.0 / rate),
            "scale": max(float(w.sum()) / 2.0, 1.0),
        })
    return tiers


def _assign_band_tiers(band_defs: list[dict], tiers: list[dict], rate: int,
                       min_bins: float = 3.0) -> list[int]:
    """Pick, per display band, the shortest tier that still lands >= min_bins FFT
    bins inside the band — the fastest window that keeps enough resolution.  Very
    narrow low bands fall back to tier 0 (the full window)."""
    short_to_long = sorted(range(len(tiers)), key=lambda i: tiers[i]["n"])
    assign = []
    for band in band_defs:
        width = max(1e-6, band["hi"] - band["lo"])
        chosen = 0
        for i in short_to_long:
            if width * tiers[i]["n"] / rate >= min_bins:
                chosen = i
                break
        assign.append(chosen)
    return assign


def _spectrum_multi_bins(chan, band_defs: list[dict], band_tier: list[int],
                         tiers: list[dict]) -> list[float]:
    """Per-band magnitudes (dBFS) using each band's assigned resolution tier.
    `chan` is the analysis window (newest sample last); shorter tiers reuse its
    most-recent samples so every tier is time-aligned to the same window end."""
    import numpy as np
    mags = []
    for t in tiers:
        seg = chan[-t["n"]:]
        mags.append(np.abs(np.fft.rfft(seg * t["window"])) / t["scale"])
    out = []
    for b, band in enumerate(band_defs):
        ti = band_tier[b]
        freqs, mag = tiers[ti]["freqs"], mags[ti]
        mask = (freqs >= band["lo"]) & (freqs < band["hi"])
        if mask.any():
            m = float(np.sqrt(np.sum(np.square(mag[mask]))))
        else:
            m = float(mag[int(np.argmin(np.abs(freqs - band["freq"])))])
        out.append(20.0 * math.log10(max(min(m, 1.0), 1e-9)))
    return out


def _spectrum_level_db(samples) -> tuple[float, float]:
    import numpy as np
    if samples.size == 0:
        return -120.0, -120.0
    s = samples.astype(np.float64, copy=False)
    rms = float(np.sqrt(np.mean(np.square(s))))
    peak = float(np.max(np.abs(samples)))
    rms_db = 20.0 * math.log10(max(rms, 1e-9))
    peak_db = 20.0 * math.log10(max(peak, 1e-9))
    return max(-120.0, min(0.0, rms_db)), max(-120.0, min(0.0, peak_db))


class SpectrumAnalyzer:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.clients = 0
        self.seq = 0
        self.frame = {
            "ok": False,
            "state": "idle",
            "error": "not started",
            "left": [],
            "right": [],
            "bands": [],
            "vu": {},
        }
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.mode = "music"

    def settings(self) -> dict:
        return {
            "enabled": SPECTRUM_ENABLED,
            "output_name": SPECTRUM_OUTPUT_NAME,
            "fifo": SPECTRUM_FIFO,
            "sample_rate": SPECTRUM_RATE,
            "bits": SPECTRUM_BITS,
            "channels": SPECTRUM_CHANNELS,
            "refresh_hz": SPECTRUM_REFRESH_HZ,
            "fft_size": SPECTRUM_FFT_SIZE,
            "precision_fft_size": SPECTRUM_PRECISION_FFT_SIZE,
            "bands": SPECTRUM_BANDS,
            "vu_mode": SPECTRUM_VU_MODE,
            "floor_db": SPECTRUM_FLOOR_DB,
            "min_frequency": SPECTRUM_MIN_FREQ,
            "mode": self.mode,
            # DRC-sync slider: measured base delay, the listener's live delta and
            # the bounds it can travel between (all milliseconds).
            "drc_delay_base_ms": round(_drc_display_delay_seconds() * 1000.0, 1),
            "drc_delay_delta_ms": round(SPECTRUM_DRC_DELAY_DELTA_MS, 1),
            "drc_delay_delta_min_ms": round(SPECTRUM_DRC_DELAY_DELTA_MIN_MS, 1),
            "drc_delay_delta_max_ms": round(SPECTRUM_DRC_DELAY_DELTA_MAX_MS, 1),
        }

    def _start_thread_locked(self, mode: str) -> None:
        # caller holds self.lock
        self.clients += 1
        self.mode = mode
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def acquire(self, mode: str = "music") -> None:
        mode = "precision" if mode == "precision" else "music"
        # If a previous analyzer thread is still unwinding (its finally-block is
        # about to `mpc disable` the FIFO output), wait for it to finish before
        # starting a fresh one.  This serialises the output enable/disable so a
        # quick Stop→Start (e.g. a Music/Precision switch) can never leave the
        # output enabled with no consumer, nor race an old thread's disable
        # against a new thread's enable.  Additional clients on a healthy thread
        # just join the existing SSE broadcast.
        for _ in range(50):
            old = None
            with self.lock:
                if self.thread is not None and not self.thread.is_alive():
                    self.thread = None
                if self.thread is not None and self.stop_event.is_set():
                    old = self.thread          # shutting down — wait outside lock
                elif self.thread is not None:
                    self.clients += 1          # healthy thread — share it
                    self.mode = mode
                    return
                else:
                    self._start_thread_locked(mode)
                    return
            old.join(timeout=0.1)
        # Fallback: the previous thread is wedged.  Start fresh so the UI gets a
        # stream rather than hanging; the stale thread is a daemon and will exit.
        with self.lock:
            self._start_thread_locked(mode)

    def release(self) -> None:
        with self.lock:
            self.clients = max(0, self.clients - 1)
            if self.clients == 0:
                self.stop_event.set()
                self.cond.notify_all()

    def ensure_disabled(self) -> None:
        """Best-effort: force the MPD FIFO output off at startup so a crash
        that left it enabled is recovered.  It stays disabled until Start."""
        if not SPECTRUM_ENABLED or not _SPECTRUM_OS_OK:
            return
        try:
            _mpd_output_action(SPECTRUM_OUTPUT_NAME, "disable")
        except Exception:
            pass

    def snapshot(self) -> tuple[int, dict]:
        with self.lock:
            return self.seq, dict(self.frame)

    def wait_next(self, last_seq: int, timeout: float = 5.0) -> tuple[int, dict]:
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq or self.stop_event.is_set(),
                               timeout=timeout)
            return self.seq, dict(self.frame)

    def _publish(self, frame: dict) -> None:
        with self.cond:
            self.seq += 1
            self.frame = frame
            self.cond.notify_all()

    def _ensure_fifo(self) -> tuple[bool, str]:
        parent = os.path.dirname(SPECTRUM_FIFO) or "."
        try:
            os.makedirs(parent, exist_ok=True)
            if os.path.exists(SPECTRUM_FIFO):
                if not os.path.exists(SPECTRUM_FIFO) or not stat_is_fifo(SPECTRUM_FIFO):
                    return False, f"{SPECTRUM_FIFO} exists and is not a FIFO"
            else:
                os.mkfifo(SPECTRUM_FIFO, 0o600)
            return True, ""
        except OSError as e:
            return False, str(e)

    def _run(self) -> None:
        if not SPECTRUM_ENABLED:
            self._publish({"ok": False, "state": "disabled",
                           "error": "spectrum analyzer disabled in commands.conf",
                           "left": [], "right": [], "bands": [], "vu": {}})
            return
        if not _SPECTRUM_OS_OK:
            self._publish({"ok": False, "state": "unsupported",
                           "error": "MPD FIFO spectrum analyzer needs Linux or FreeBSD",
                           "left": [], "right": [], "bands": [], "vu": {}})
            return
        if SPECTRUM_BITS != 32 or SPECTRUM_CHANNELS < 2:
            self._publish({"ok": False, "state": "bad-config",
                           "error": "only stereo S32_LE FIFO capture is implemented",
                           "left": [], "right": [], "bands": [], "vu": {}})
            return
        try:
            import numpy as np
        except ImportError:
            self._publish({"ok": False, "state": "missing-numpy",
                           "error": "numpy is required for live spectrum analysis",
                           "left": [], "right": [], "bands": [], "vu": {}})
            return

        ok, err = self._ensure_fifo()
        if not ok:
            self._publish({"ok": False, "state": "fifo-error", "error": err,
                           "left": [], "right": [], "bands": [], "vu": {}})
            return

        fd = None
        output_enabled = False
        terminal_error = False
        try:
            fd = os.open(SPECTRUM_FIFO, os.O_RDONLY | os.O_NONBLOCK)
            ok, err = _mpd_output_action(SPECTRUM_OUTPUT_NAME, "enable")
            if not ok:
                terminal_error = True
                self._publish({"ok": False, "state": "mpd-error", "error": err,
                               "left": [], "right": [], "bands": [], "vu": {}})
                return
            output_enabled = True
            self._publish({"ok": True, "state": "waiting",
                           "error": "", "left": [], "right": [], "bands": [], "vu": {},
                           "rate": SPECTRUM_RATE, "mode": self.mode})

            bytes_per_sample = 4
            frame_bytes = bytes_per_sample * SPECTRUM_CHANNELS
            fft_size = SPECTRUM_PRECISION_FFT_SIZE if self.mode == "precision" else SPECTRUM_FFT_SIZE
            need_bytes = fft_size * frame_bytes
            chunk_bytes = max(4096, int(SPECTRUM_RATE / SPECTRUM_REFRESH_HZ) * frame_bytes)
            buf = bytearray()
            interval = 1.0 / SPECTRUM_REFRESH_HZ
            next_at = time.monotonic()
            last_data_at = 0.0
            # Multi-resolution band analysis: bass keeps the full window for fine
            # frequency resolution, treble uses short windows for snappy transient
            # response (cymbals/drums).  Disabled in precision mode, which wants
            # maximum resolution everywhere for test tones.
            multi_res = self.mode != "precision" and fft_size >= 2048
            tiers = _spectrum_tiers(fft_size, SPECTRUM_RATE, multi_res)
            band_defs = _spectrum_band_defs(SPECTRUM_BANDS, SPECTRUM_RATE / 2.0, SPECTRUM_MIN_FREQ)
            band_tier = _assign_band_tiers(band_defs, tiers, SPECTRUM_RATE)
            # VU ballistics are computed over a short trailing slice (~50 ms) of
            # the captured buffer rather than the whole FFT window so the meters
            # track the music instead of lagging behind by the FFT length
            # (341 ms music / 1.36 s precision).
            vu_window = max(256, min(fft_size, int(SPECTRUM_RATE * 0.05)))
            silence_bands = band_defs

            # DRC sync: the FIFO is pre-DRC, so slide the analysis window back by
            # the BruteFIR path delay to match the audible signal.  Re-checked on
            # a slow cadence (the heavy filter read is cached) so it tracks filter
            # / preset / rate changes without per-frame cost.
            delay_bytes = 0
            base_delay_s = 0.0
            delay_check_interval = 2.0
            next_delay_check = 0.0
            # Declare silence quickly once the FIFO stops delivering, so the bars
            # and VU meters drop the moment playback stops instead of freezing on
            # the last delay-held window for half a second.  PCM flows continuously
            # during playback and only a real stop/pause halts the writes, so this
            # short timeout does not false-trigger on quiet musical passages.
            silence_timeout = 0.15

            def keep_bytes() -> int:
                # Hold enough history for the FFT window plus the sync delay.
                return need_bytes + delay_bytes + chunk_bytes * 2

            def publish_silence() -> None:
                # Emit a level well below any reachable UI floor so the bars,
                # VU bars and needles all bottom out (effectively -inf) when the
                # music stops, independent of where the floor slider sits.
                silent_db = -120.0
                vals = [silent_db] * len(silence_bands)
                self._publish({
                    "ok": True,
                    "state": "running",
                    "error": "",
                    "rate": SPECTRUM_RATE,
                    "mode": self.mode,
                    "fft_size": fft_size,
                    "bands": [
                        {
                            "freq": round(b["freq"], 1),
                            "label": b["label"],
                            "lo": round(b["lo"], 1),
                            "hi": round(b["hi"], 1),
                        }
                        for b in silence_bands
                    ],
                    "left": vals,
                    "right": vals,
                    "vu": {
                        "left_rms": silent_db,
                        "right_rms": silent_db,
                        "left_peak": silent_db,
                        "right_peak": silent_db,
                    },
                })

            while not self.stop_event.is_set():
                try:
                    data = os.read(fd, chunk_bytes)
                    if data:
                        last_data_at = time.monotonic()
                        buf.extend(data)
                        max_keep = keep_bytes()
                        if len(buf) > max_keep:
                            del buf[:len(buf) - max_keep]
                    else:
                        time.sleep(0.02)
                except BlockingIOError:
                    time.sleep(0.02)
                except OSError as e:
                    terminal_error = True
                    self._publish({"ok": False, "state": "read-error", "error": str(e),
                                   "left": [], "right": [], "bands": [], "vu": {}})
                    break

                now = time.monotonic()
                if now < next_at:
                    continue
                next_at = now + interval
                if now >= next_delay_check:
                    next_delay_check = now + delay_check_interval
                    base_delay_s = _drc_display_delay_seconds()
                    # Total hold-back = measured base + the listener's sync delta,
                    # floored at 0 (cannot show samples not yet played).
                    total_delay_s = max(0.0, base_delay_s + SPECTRUM_DRC_DELAY_DELTA_MS / 1000.0)
                    delay_frames = int(round(total_delay_s * SPECTRUM_RATE))
                    delay_bytes = delay_frames * frame_bytes
                if last_data_at and now - last_data_at > silence_timeout:
                    publish_silence()
                    continue
                # Window ends `delay_bytes` before the newest sample so the
                # display matches the audible (post-DRC) signal.
                end = len(buf) - delay_bytes
                if end < need_bytes:
                    self._publish({"ok": True, "state": "waiting",
                                   "error": "", "left": [], "right": [], "bands": [], "vu": {},
                                   "rate": SPECTRUM_RATE, "mode": self.mode, "fft_size": fft_size})
                    continue

                raw = bytes(buf[end - need_bytes:end])
                pcm = np.frombuffer(raw, dtype="<i4").reshape(-1, SPECTRUM_CHANNELS)
                left = pcm[:, 0].astype(np.float32) / 2147483648.0
                right = pcm[:, 1].astype(np.float32) / 2147483648.0
                bands = band_defs
                l_bins = _spectrum_multi_bins(left, band_defs, band_tier, tiers)
                r_bins = _spectrum_multi_bins(right, band_defs, band_tier, tiers)
                l_rms, l_peak = _spectrum_level_db(left[-vu_window:])
                r_rms, r_peak = _spectrum_level_db(right[-vu_window:])
                self._publish({
                    "ok": True,
                    "state": "running",
                    "error": "",
                    "rate": SPECTRUM_RATE,
                    "mode": self.mode,
                    "fft_size": fft_size,
                    "drc_delay": round(delay_bytes / frame_bytes / SPECTRUM_RATE, 3),
                    "drc_delay_base": round(base_delay_s, 3),
                    "drc_delay_delta": round(SPECTRUM_DRC_DELAY_DELTA_MS / 1000.0, 3),
                    "bands": [
                        {
                            "freq": round(b["freq"], 1),
                            "label": b["label"],
                            "lo": round(b["lo"], 1),
                            "hi": round(b["hi"], 1),
                        }
                        for b in bands
                    ],
                    "left": [round(v, 1) for v in l_bins],
                    "right": [round(v, 1) for v in r_bins],
                    "vu": {
                        "left_rms": round(l_rms, 1),
                        "right_rms": round(r_rms, 1),
                        "left_peak": round(l_peak, 1),
                        "right_peak": round(r_peak, 1),
                    },
                })
        finally:
            if output_enabled:
                _mpd_output_action(SPECTRUM_OUTPUT_NAME, "disable")
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if not terminal_error:
                self._publish({"ok": False, "state": "idle", "error": "not streaming",
                               "left": [], "right": [], "bands": [], "vu": {}})


def stat_is_fifo(path: str) -> bool:
    import stat
    try:
        return stat.S_ISFIFO(os.stat(path).st_mode)
    except OSError:
        return False


_SPECTRUM = SpectrumAnalyzer()


def _ps_arg_lines() -> list[str]:
    for cmd in (["ps", "axo", "args"], ["ps", "ax", "-o", "args="]):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return [
                line.strip() for line in r.stdout.splitlines()
                if line.strip() and line.strip().lower() != "args"
            ]
    return []


def _virtual_oss_rate() -> int | None:
    for line in _ps_arg_lines():
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        if not parts:
            continue
        name = os.path.basename(parts[0])
        if name == "sudo" and len(parts) > 1:
            name = os.path.basename(parts[1])
            args = parts[1:]
        else:
            args = parts
        if name != "virtual_oss":
            continue
        for i, arg in enumerate(args):
            if arg == "-r" and i + 1 < len(args):
                try:
                    return int(args[i + 1])
                except ValueError:
                    return None
    return None


def _alsa_hw_params() -> dict | None:
    """hw_params of the first active ALSA playback stream (Linux only).

    Reflects exactly what the DAC is being fed right now: format (bit depth),
    rate, channels, and the period/buffer sizes.  Returns None when no stream
    is open.
    """
    for path in sorted(glob.glob("/proc/asound/card*/pcm*p/sub*/hw_params")):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        if content.strip() in ("", "closed"):
            continue
        fields: dict[str, str] = {}
        for line in content.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fields[k.strip()] = v.strip()
        rate = None
        m = re.match(r'(\d+)', fields.get("rate", ""))
        if m:
            rate = int(m.group(1))
        # card/device id from the path: /proc/asound/card0/pcm0p/sub0/hw_params
        cm = re.search(r'card(\d+)/pcm(\d+)', path)
        return {
            "card": int(cm.group(1)) if cm else None,
            "device": int(cm.group(2)) if cm else None,
            "format": fields.get("format"),
            "rate": rate,
            "channels": int(fields["channels"]) if fields.get("channels", "").isdigit() else None,
            "period_size": int(fields["period_size"]) if fields.get("period_size", "").isdigit() else None,
            "buffer_size": int(fields["buffer_size"]) if fields.get("buffer_size", "").isdigit() else None,
        }
    return None


def _alsa_rate() -> int | None:
    """Rate of the first active ALSA playback stream (Linux only)."""
    hw = _alsa_hw_params()
    return hw["rate"] if hw else None


def _brutefir_rate() -> int | None:
    for line in _ps_arg_lines():
        if "brutefir" not in line:
            continue
        m = re.search(r'brutefir-(\d+)[^ /]*\.conf', line)
        if m:
            return int(m.group(1))
    return None


def _rate_status(mpd_rate: int | None, virtual_rate: int | None, brutefir_rate: int | None) -> dict:
    rates = [r for r in (virtual_rate, brutefir_rate) if r]
    if not mpd_rate or not rates:
        return {"kind": "unknown", "text": "sample-rate comparison unavailable"}
    if all(mpd_rate == r for r in rates):
        return {"kind": "match", "text": "SAMPLE RATE MATCH"}
    return {"kind": "mismatch", "text": "RESAMPLING"}


def _path_status(rate_status: dict, brutefir_running: bool) -> dict:
    """Plain-language verdict on the audio path, for the bit-perfect hint.

    Honest framing: only the DRC-off + rates-matched case is truly
    bit-transparent.  With DRC engaged the signal is intentionally modified,
    but still at native rate in 64-bit float with no resampling stage.
    """
    kind = rate_status.get("kind")
    if kind == "mismatch":
        return {
            "kind": "mismatch",
            "text": "Resampling active",
            "detail": "Sample-rate conversion is in the chain — the stream is not bit-transparent.",
        }
    if kind == "match":
        if brutefir_running:
            return {
                "kind": "drc",
                "text": "Full-resolution DRC · no resampling",
                "detail": "BruteFIR applies room correction at the native rate in 64-bit float. "
                          "No sample-rate conversion and no lossy stage between MPD and the DAC.",
            }
        return {
            "kind": "match",
            "text": "Bit-perfect passthrough",
            "detail": "DRC is off and every stage runs at the same rate — samples reach the DAC unaltered.",
        }
    return {"kind": "unknown", "text": "Path status unavailable", "detail": ""}


# ── BruteFIR filter (FIR coefficient) inspection ───────────────────────────────

# numpy dtype for each BruteFIR coeff `format:` string.
_RAW_DTYPES: dict[str, str] = {
    "FLOAT64_LE": "<f8", "FLOAT64_BE": ">f8",
    "FLOAT_LE":   "<f4", "FLOAT_BE":   ">f4",
    "FLOAT32_LE": "<f4", "FLOAT32_BE": ">f4",
    "S32_LE": "<i4", "S32_BE": ">i4",
    "S16_LE": "<i2", "S16_BE": ">i2",
}

# Match scripts/headroom_calc.py and the publication audit: the live page adds
# one dB above the worst raw-filter FFT gain, then rounds the required BruteFIR
# attenuation upward to one decimal place.
_HEADROOM_SAFETY_MARGIN_DB = 1.0


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bundle_identity(manifest: dict) -> dict:
    """Reconstruct the content identity used by scripts/deploy_filter.py."""
    if manifest.get("schema") != 2:
        raise ValueError(f"unsupported bundle schema {manifest.get('schema')!r}")
    return {
        "schema": manifest["schema"],
        "geometry": manifest["geometry"],
        "variant": manifest["variant"],
        "design_id": manifest.get("design_id", manifest["variant"]),
        "description": manifest["description"],
        "source_provenance_sha256": _canonical_hash(manifest["source"]),
        "source_artifacts": {
            role: item["sha256"]
            for role, item in manifest["source"]["artifacts"].items()
        },
        "runtime": {
            rate: {
                "config": item["config"],
                "config_sha256": item["config_sha256"],
                "format": item["format"],
                "attenuation_db": item["attenuation_db"],
                "channels": {
                    channel: data["sha256"]
                    for channel, data in item["channels"].items()
                },
            }
            for rate, item in manifest["runtime"]["rates"].items()
        },
        "analysis_sha256": manifest["analysis"]["sha256"],
    }


def _bundle_roots(coeffs: list[dict]) -> list[str]:
    """Candidate geometry roots, derived only from the active coeff paths."""
    paths = [os.path.realpath(item["filename"]) for item in coeffs]
    if not paths:
        return []
    try:
        current = os.path.commonpath(paths)
    except ValueError:
        return []
    if not os.path.isdir(current):
        current = os.path.dirname(current)
    roots = []
    for _ in range(5):
        if os.path.isdir(os.path.join(current, "provenance")):
            roots.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return roots


def _safe_bundle_path(root: str, relative: str) -> str:
    if os.path.isabs(relative):
        raise ValueError(f"bundle path is absolute: {relative}")
    root_real = os.path.realpath(root)
    result = os.path.realpath(os.path.join(root_real, relative))
    if os.path.commonpath((root_real, result)) != root_real:
        raise ValueError(f"bundle path escapes geometry root: {relative}")
    return result


def _verified_filter_bundle(parsed: dict) -> tuple[dict | None, dict]:
    """Match audited graph data to the exact coefficient bytes in use.

    Room measurements are returned only when a single manifest matches the
    active L/R paths, hashes, formats, rate and attenuations.
    """
    coeffs = parsed.get("coeffs") or []
    rate = parsed.get("rate")
    active: dict[str, dict] = {}
    for coeff in coeffs:
        name = _coeff_channel(coeff).lower()
        channel = "left" if name == "left" else "right" if name == "right" else ""
        if not channel or channel in active:
            continue
        active[channel] = {
            "path": os.path.realpath(coeff["filename"]),
            "sha256": _sha256_file(coeff["filename"]),
            "format": coeff["format"].upper(),
            "attenuation_db": float(coeff["attenuation"]),
        }
    if set(active) != {"left", "right"}:
        return None, {"status": "mismatch", "message": "active config has no unique L/R coefficient pair"}

    failures: list[str] = []
    matches: list[tuple[dict, dict, str]] = []
    for root in _bundle_roots(coeffs):
        for manifest_path in sorted(glob.glob(os.path.join(root, "provenance", "*.json"))):
            if manifest_path.endswith(".source.json"):
                continue
            try:
                with open(manifest_path, encoding="utf-8") as stream:
                    manifest = json.load(stream)
                if not manifest.get("bundle_id") or manifest.get("schema") != 2:
                    continue
                if manifest.get("verification", {}).get("status") != "verified":
                    raise ValueError("manifest status is not verified")
                if _canonical_hash(_bundle_identity(manifest)) != manifest["bundle_id"]:
                    raise ValueError("bundle ID does not match manifest content")
                runtime = manifest["runtime"]["rates"][str(rate)]
                for channel in ("left", "right"):
                    expected = runtime["channels"][channel]
                    relative = os.path.relpath(active[channel]["path"], os.path.realpath(root))
                    if relative != expected["path"]:
                        raise ValueError(f"{channel} active path differs")
                    if active[channel]["sha256"] != expected["sha256"]:
                        raise ValueError(f"{channel} active SHA-256 differs")
                    if active[channel]["format"] != runtime["format"].upper():
                        raise ValueError(f"{channel} active format differs")
                    if abs(active[channel]["attenuation_db"] - float(runtime["attenuation_db"])) > 1e-9:
                        raise ValueError(f"{channel} active attenuation differs")
                analysis_path = _safe_bundle_path(root, manifest["analysis"]["path"])
                if _sha256_file(analysis_path) != manifest["analysis"]["sha256"]:
                    raise ValueError("analysis SHA-256 differs")
                with open(analysis_path, encoding="utf-8") as stream:
                    analysis = json.load(stream)
                if analysis.get("schema") != 2:
                    raise ValueError(f"unsupported analysis schema {analysis.get('schema')!r}")
                if (analysis.get("geometry") != manifest["geometry"] or
                        analysis.get("variant") != manifest["variant"] or
                        analysis.get("design_id", analysis.get("variant")) !=
                        manifest.get("design_id", manifest["variant"])):
                    raise ValueError("analysis identity differs")
                if ("description" in manifest and
                        analysis.get("description") != manifest["description"]):
                    raise ValueError("analysis description differs")
                inputs = analysis.get("inputs", {})
                for role, expected in manifest["source"]["artifacts"].items():
                    if inputs.get(role) != expected["sha256"]:
                        raise ValueError(f"analysis input hash differs for {role}")
                matches.append((manifest, analysis, manifest_path))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                failures.append(f"{os.path.basename(manifest_path)}: {error}")

    if len(matches) != 1:
        if len(matches) > 1:
            message = "more than one provenance manifest matches the active coefficient pair"
        elif failures:
            message = "; ".join(failures[:3])
        else:
            message = "no provenance manifest matches the active coefficient hashes"
        return None, {
            "status": "mismatch",
            "message": message,
            "active_hashes": {channel: item["sha256"] for channel, item in active.items()},
        }

    manifest, analysis, manifest_path = matches[0]
    # The stored traces are released exactly as deployed.  Nothing is scaled,
    # offset or resampled on the way to the browser: the numbers the page draws
    # are the ones REW exported.
    return {
        "manifest": manifest,
        "analysis": analysis,
        "manifest_file": os.path.basename(manifest_path),
        "active": active,
    }, {
        "status": manifest["verification"]["status"],
        "message": (
            "Active L/R bytes, config and graph dependencies match the manifest; "
            f"bundle {manifest['bundle_id'][:16]}"),
        "bundle_id": manifest["bundle_id"],
        "active_hashes": {channel: item["sha256"] for channel, item in active.items()},
    }


def _active_brutefir_process() -> dict | None:
    """Command line and config path of the running BruteFIR process.

    Match the real brutefir process by argv[0] (handling a sudo prefix) rather
    than any command line that merely mentions "brutefir" — otherwise an editor
    open on a brutefir*.conf, or a grep, would be mistaken for the engine."""
    for line in _ps_arg_lines():
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        if not parts:
            continue
        if os.path.basename(parts[0]) == "sudo" and len(parts) > 1:
            parts = parts[1:]
        if os.path.basename(parts[0]) != "brutefir":
            continue
        for p in parts[1:]:
            if p.endswith(".conf"):
                return {"command_line": line, "argv": parts, "config": p}
    return None


def _active_brutefir_conf() -> str | None:
    """Path of the .conf the running BruteFIR was started with."""
    process = _active_brutefir_process()
    return process["config"] if process else None


def _design_selector_from_conf(path: str) -> tuple[int | None, str]:
    """Return the rate and selector encoded by a BruteFIR config basename."""
    match = re.fullmatch(r"brutefir-(\d+)(.*)\.conf", os.path.basename(path))
    if not match:
        return None, "unknown"
    return int(match.group(1)), match.group(2) or "default"


def _parse_brutefir_conf(path: str) -> dict:
    """Extract sampling_rate and coeff (filename/format/attenuation) blocks."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    m = re.search(r'sampling_rate:\s*(\d+)', text)
    rate = int(m.group(1)) if m else None
    coeffs = []
    for cm in re.finditer(r'coeff\s+"([^"]+)"\s*\{([^}]*)\}', text):
        label, body = cm.group(1), cm.group(2)
        fn  = re.search(r'filename:\s*"([^"]+)"', body)
        fmt = re.search(r'format:\s*"([^"]+)"', body)
        att = re.search(r'attenuation:\s*([-\d.]+)', body)
        if not fn:
            continue
        coeffs.append({
            "label":       label,
            "filename":    fn.group(1),
            "format":      fmt.group(1) if fmt else "FLOAT64_LE",
            "attenuation": float(att.group(1)) if att else 0.0,
        })
    return {"rate": rate, "coeffs": coeffs}


def _raw_filter_headroom(filename: str, fmt: str,
                         margin_db: float = _HEADROOM_SAFETY_MARGIN_DB) -> dict:
    """Calculate clipping-safe attenuation directly from one active RAW FIR.

    The calculation deliberately does not trust provenance metadata: it reads
    the bytes named by the running config, finds the filter's peak FFT gain and
    adds the requested safety margin.  Attenuation is a positive BruteFIR gain
    reduction, rounded upward to the one-decimal precision used by our configs.
    """
    import numpy as np

    dtype_name = _RAW_DTYPES.get(fmt.upper())
    if not dtype_name:
        raise ValueError(f"unsupported BruteFIR coefficient format: {fmt}")
    samples = np.fromfile(filename, dtype=dtype_name)
    if samples.size == 0:
        raise ValueError(f"empty filter: {filename}")
    dtype = np.dtype(dtype_name)
    if np.issubdtype(dtype, np.integer):
        samples = samples.astype(np.float64) / (2 ** (dtype.itemsize * 8 - 1))
    else:
        samples = samples.astype(np.float64)
    if not np.all(np.isfinite(samples)):
        raise ValueError(f"filter contains non-finite coefficients: {filename}")

    fft_size = 1 << (max(1, int(samples.size)) - 1).bit_length()
    peak_linear = float(np.max(np.abs(np.fft.rfft(samples, n=fft_size))))
    peak_db = 20.0 * math.log10(peak_linear) if peak_linear > 0.0 else None
    required = 0.0 if peak_db is None else max(
        0.0, math.ceil((peak_db + margin_db) * 10.0) / 10.0)
    return {
        "taps": int(samples.size),
        "peak_gain_db": round(peak_db, 6) if peak_db is not None else None,
        "safety_margin_db": float(margin_db),
        "safe_attenuation_db": round(required, 1),
    }


def _active_brutefir_configuration() -> dict:
    """Inspect the exact config and RAW filters loaded by BruteFIR right now."""
    process = _active_brutefir_process()
    if not process:
        return {
            "ok": False, "running": False,
            "error": "BruteFIR is not running — no active configuration loaded.",
        }

    conf_path = process["config"]
    parsed = _parse_brutefir_conf(conf_path)
    rate_from_name, selector = _design_selector_from_conf(conf_path)
    rate = parsed.get("rate") or rate_from_name
    geometry = os.path.basename(os.path.dirname(conf_path))
    design_id = selector[1:] if selector.startswith("@") else selector
    description = design_id
    verification = None

    # A verified manifest supplies the human-readable design name, but the
    # page remains useful for legacy/unpublished configs and never relies on a
    # manifest for its live headroom figures.
    if parsed["coeffs"] and all(os.path.isfile(c["filename"]) for c in parsed["coeffs"]):
        try:
            bundle, verification = _verified_filter_bundle(parsed)
            if bundle:
                manifest = bundle["manifest"]
                design_id = manifest.get("design_id", manifest["variant"])
                description = manifest.get("description", design_id)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass

    filters = []
    for coeff in parsed["coeffs"]:
        filename = coeff["filename"]
        item = {
            "label": coeff["label"],
            "channel": _coeff_channel(coeff),
            "filename": filename,
            "file": os.path.basename(filename),
            "format": coeff["format"],
            "is_raw": filename.lower().endswith(".raw"),
            "exists": os.path.isfile(filename),
            "configured_attenuation_db": float(coeff["attenuation"]),
        }
        if not item["is_raw"]:
            item["analysis_error"] = "coefficient is not a .raw filter file"
        elif not item["exists"]:
            item["analysis_error"] = "filter file does not exist or is not readable"
        else:
            try:
                headroom = _raw_filter_headroom(filename, coeff["format"])
                item.update(headroom)
                item["safe"] = (
                    item["configured_attenuation_db"] + 1e-9 >=
                    item["safe_attenuation_db"]
                )
            except (OSError, ValueError) as error:
                item["analysis_error"] = str(error)
        filters.append(item)

    analysed = [item for item in filters if "safe_attenuation_db" in item]
    headroom_complete = bool(analysed) and len(analysed) == len(filters)
    safe_attenuation = (
        max(item["safe_attenuation_db"] for item in analysed)
        if headroom_complete else None)
    configured_values = sorted({item["configured_attenuation_db"] for item in filters})
    configured_attenuation = (
        configured_values[0] if len(configured_values) == 1 else None)
    headroom_safe = headroom_complete and all(item.get("safe") for item in analysed)

    result = {
        "ok": True,
        "running": True,
        "command_line": process["command_line"],
        "config_path": conf_path,
        "config": os.path.basename(conf_path),
        "geometry": geometry,
        "rate": rate,
        "design": selector,
        "design_id": design_id,
        "description": description,
        "safety_margin_db": _HEADROOM_SAFETY_MARGIN_DB,
        "configured_attenuation_db": configured_attenuation,
        "configured_attenuations_db": configured_values,
        "safe_attenuation_db": safe_attenuation,
        "headroom_safe": headroom_safe,
        "filters": filters,
    }
    if verification:
        result["verification"] = verification
    return result


# ── DRC display-sync delay ────────────────────────────────────────────────────
# The spectrum/VU tap is the pre-DRC MPD FIFO; the audible signal is delayed by
# the BruteFIR path, so the browser must hold the FIFO-derived display back by
# that much to stay in sync with what is heard.  The delay has two exactly
# computable parts (see video/AV-SYNC-DELAY.md for the full derivation):
#
#   filter group delay = argmax(|h|) / rate   — impulse-response peak, ~0.5 s,
#                                                rate-independent in time
# + partition latency  = filter_length / rate — one BruteFIR block; rate-DEPENDENT
#
# plus a small, runtime BruteFIR/loopback buffering term left to the configurable
# `drc_delay_trim_ms`.  Computed from the *active* filter and cached, recomputed
# only when the active conf / filter / defaults change — not per frame.

_BRUTEFIR_DEFAULTS = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "BruteFIR", "brutefir_defaults.conf",
)

_drc_delay_cache: dict = {"key": None, "seconds": 0.0}
_drc_delay_lock = threading.Lock()

# The analyzer slider positions (Sync delta and Floor) are per-listener runtime
# settings, remembered between runs in tiny state files.  They are deliberately
# kept out of commands.conf so the installed config stays declarative; only the
# *defaults* / *bounds* live there.


def _resolve_state_dir() -> str:
    """Runtime state directory, mirroring drc.sh's resolution so the whole stack
    shares one location (see doc/FREEBSD-PORT-PLAN.md 1.4):

        $OMDRC_STATE_DIR            explicit override — services pin this
        run-from-repo (config.env)  beside the checkout, as drc.sh does
        root                        /var/db/omdrc
        otherwise                   ${XDG_STATE_HOME:-~/.local/state}/omdrc

    A packaged install must never write inside its own installed files: pkg
    check -s flags any modified packaged file.  Note the omdrcctrl rc.d script
    drops privileges to a service user, so the root branch will not be taken
    under service(8) — pin OMDRC_STATE_DIR (rc.conf/omdrc.conf) to share state
    with drc.sh in that case."""
    env_dir = os.environ.get("OMDRC_STATE_DIR")
    if env_dir:
        return env_dir
    repo_root = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
    if os.path.isfile(os.path.join(repo_root, "config.env")):
        return repo_root
    if os.geteuid() == 0:
        return "/var/db/omdrc"
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(xdg, "omdrc")


_STATE_DIR = _resolve_state_dir()
_DELTA_STATE_FILE = os.path.join(_STATE_DIR, "spectrum-drc-delay-delta")
_FLOOR_STATE_FILE = os.path.join(_STATE_DIR, "spectrum-floor-db")
# Which renderer the toggle last selected.  Unlike the two sliders above this is
# not read back by the panel to restore itself — the boot service reads it (see
# scripts/omdrc-renderer, driven by etc/rc.d/omdrc_renderer or the systemd
# --user omdrc-renderer.service) so the box comes back on the renderer it was
# left on.  Name and format follow drc.sh's last_arg / last_power: one value,
# one line, same state dir.
_RENDERER_STATE_FILE = os.path.join(_STATE_DIR, "last_renderer")
# The timestamp of the newest cdin failure the user has waved away.  A
# watermark rather than a flag: failures NEWER than it still show, so
# dismissing can never blind the card to something that happens next.
_CDIN_ACK_FILE = os.path.join(_STATE_DIR, "cdin_error_ack")


def _clamp_delta(ms: float) -> float:
    return max(SPECTRUM_DRC_DELAY_DELTA_MIN_MS,
               min(SPECTRUM_DRC_DELAY_DELTA_MAX_MS, float(ms)))


def _read_state_float(path: str) -> float | None:
    """A single float saved by a previous run, or None if unset/unreadable."""
    try:
        with open(path) as fh:
            return float(fh.read().strip())
    except (OSError, ValueError):
        return None


def _write_state_float(path: str, val: float) -> None:
    """Persist a single float atomically; best-effort (never raises)."""
    _write_state_str(path, f"{val:.1f}")


def _read_state_str(path: str) -> str | None:
    """A single string saved by a previous run, or None if unset/unreadable."""
    try:
        with open(path) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _write_state_str(path: str, val: str) -> None:
    """Persist a single line atomically; best-effort (never raises)."""
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as fh:
            fh.write(f"{val}\n")
        os.replace(tmp, path)
    except OSError:
        pass


def _brutefir_partition_size(conf_text: str) -> int | None:
    """partition_size from `filter_length: P[,n];`.  Lives in the runtime
    defaults (~/.config/BruteFIR/brutefir_defaults.conf), but honour a per-conf
    override if one is ever added."""
    for text in (conf_text, _read_text_quietly(_BRUTEFIR_DEFAULTS)):
        if not text:
            continue
        m = re.search(r'filter_length:\s*(\d+)', text)
        if m:
            return int(m.group(1))
    return None


def _fir_peak_delay_seconds(filename: str, fmt: str, rate: int) -> float:
    """Bulk (group) delay of a raw FIR = impulse-response peak index / rate."""
    import numpy as np
    if not rate:
        return 0.0
    dtype = _RAW_DTYPES.get(fmt.upper(), "<f8")
    data = np.fromfile(filename, dtype=dtype)
    if data.size == 0:
        return 0.0
    return int(np.argmax(np.abs(data))) / float(rate)


def _drc_display_delay_seconds() -> float:
    """Seconds to hold the FIFO-derived display back so it matches the audible,
    post-BruteFIR signal.  0 when DRC is bypassed (BruteFIR not running).

    Cached; recomputed only when the active conf, filter file, defaults file or
    trim change."""
    conf = _active_brutefir_conf()
    if not conf:
        return 0.0   # DRC off → no compensation
    try:
        conf_text = _read_text_quietly(conf)
        parsed = _parse_brutefir_conf(conf)
        coeffs = parsed.get("coeffs") or []
        rate = parsed.get("rate")
        if not coeffs or not rate:
            return 0.0
        coeff = coeffs[0]
        fn = coeff["filename"]
        fn_mtime = os.stat(fn).st_mtime
        try:
            defaults_mtime = os.stat(_BRUTEFIR_DEFAULTS).st_mtime
        except OSError:
            defaults_mtime = 0.0
        key = (fn, fn_mtime, rate, defaults_mtime, SPECTRUM_DRC_DELAY_TRIM_MS)
        with _drc_delay_lock:
            if _drc_delay_cache["key"] == key:
                return _drc_delay_cache["seconds"]
        group = _fir_peak_delay_seconds(fn, coeff["format"], rate)
        part = _brutefir_partition_size(conf_text)
        partition = (part / rate) if part else 0.0
        secs = max(0.0, group + partition + SPECTRUM_DRC_DELAY_TRIM_MS / 1000.0)
        with _drc_delay_lock:
            _drc_delay_cache["key"] = key
            _drc_delay_cache["seconds"] = secs
        return secs
    except (OSError, ValueError, KeyError):
        return 0.0


def _coeff_channel(coeff: dict) -> str:
    """Human channel name from coeff label / filename (Left / Right / fallback)."""
    blob = (coeff["label"] + " " + os.path.basename(coeff["filename"])).lower()
    if re.search(r'\b(l|left|fl)\b', blob) or blob.startswith("l") or "/l." in blob or "l.raw" in blob:
        return "Left"
    if re.search(r'\b(r|right|fr)\b', blob) or blob.startswith("r") or "r.raw" in blob:
        return "Right"
    return coeff["label"]


def _fir_taps(filename: str, fmt: str) -> int:
    """Coefficient count of a raw FIR, from its size alone.

    No transform is taken here.  The response page plots REW's own exports, so
    the only thing the live coefficients still have to report is how many taps
    BruteFIR loaded.
    """
    width = {"FLOAT64_LE": 8, "FLOAT32_LE": 4, "S32_LE": 4, "S16_LE": 2}.get(
        fmt.upper(), 8)
    try:
        return os.path.getsize(filename) // width
    except OSError:
        return 0


def _format_read_output(cmd_id: str, output: str) -> str:
    if cmd_id == "drc_status" and output:
        parts = output.split()
        if not parts:
            return output
        if parts[-1].lower() == "off":
            return "Off"
        if output.lower().startswith("cd input"):
            return output
        if len(parts) > 1:
            return " ".join(parts[1:])
    return output


def _control_title() -> str:
    try:
        r = subprocess.run(
            ["uname", "-sr"], capture_output=True, text=True, timeout=3,
        )
        label = r.stdout.strip()
        if r.returncode == 0 and label:
            return f"{label} Control"
    except Exception:
        pass
    return "System Control"


# ── page routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template(
        "index.html",
        control_title=_control_title(),
        groups=_groups(),
        topcpu_threshold=TOPCPU_THRESHOLD,
        monitor_interval=MONITOR_INTERVAL,
        topcpu_interval=TOPCPU_INTERVAL,
        sndstat_interval=SNDSTAT_INTERVAL,
        brutefir_interval=BRUTEFIR_INTERVAL,
        spectrum=_SPECTRUM.settings(),
        log_sources=[{"id": s["id"], "label": s["label"]} for s in LOG_SOURCES],
        log_alert_interval=LOG_ALERT_INTERVAL,
        cdin_enabled=CDIN_ENABLED,
        cdin_interval=CDIN_INTERVAL,
        cdin_control=CDIN_CONTROL,
        cdin_log_id=_cdin_log_source_id(),
        chain_enabled=CHAIN_ENABLED,
        chain_interval=CHAIN_INTERVAL,
    )


@app.route("/details/<cmd_id>")
def details_page(cmd_id):
    if cmd_id not in CMD_MAP:
        return "Unknown command", 404
    cmd = CMD_MAP[cmd_id]
    if "details" not in cmd:
        return "No details file configured for this command", 404

    path = cmd["details"]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return f"Details file not found: {path}", 404
    except OSError as e:
        return f"Cannot read details file: {e}", 500

    html = md_lib.markdown(
        text,
        extensions=["tables", "fenced_code", "extra"],
    )
    # rewrite relative asset paths (src="..." href="...") so the browser can
    # fetch images and local links through /details-asset/<cmd_id>/...
    html = re.sub(
        r'(src|href)="(?!https?://|/)([^"]+)"',
        lambda m: f'{m.group(1)}="/details-asset/{cmd_id}/{m.group(2)}"',
        html,
    )
    return render_template("details.html", title=cmd["what"], content=html)


@app.route("/details-asset/<cmd_id>/<path:filename>")
def details_asset(cmd_id, filename):
    """Serve images and other files relative to the details .md file."""
    if cmd_id not in CMD_MAP:
        return "Not found", 404
    cmd = CMD_MAP[cmd_id]
    if "details" not in cmd:
        return "Not found", 404
    base_dir = os.path.dirname(os.path.abspath(cmd["details"]))
    return send_from_directory(base_dir, filename)


@app.route("/details-dyn/<cmd_id>/<config_name>")
def details_dyn_page(cmd_id, config_name):
    if cmd_id not in CMD_MAP:
        return "Unknown command", 404
    cmd = CMD_MAP[cmd_id]
    path = _find_dyn_details(cmd, config_name)
    if not path:
        return f"No README.md or INDEX.md found for: {config_name}", 404
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return f"Cannot read file: {e}", 500
    html = md_lib.markdown(text, extensions=["tables", "fenced_code", "extra"])
    html = re.sub(
        r'(src|href)="(?!https?://|/)([^"]+)"',
        lambda m: f'{m.group(1)}="/details-dyn-asset/{cmd_id}/{config_name}/{m.group(2)}"',
        html,
    )
    return render_template("details.html", title=config_name, content=html)


@app.route("/details-dyn-asset/<cmd_id>/<config_name>/<path:filename>")
def details_dyn_asset(cmd_id, config_name, filename):
    if cmd_id not in CMD_MAP:
        return "Not found", 404
    cmd = CMD_MAP[cmd_id]
    path = _find_dyn_details(cmd, config_name)
    if not path:
        return "Not found", 404
    return send_from_directory(os.path.dirname(path), filename)


@app.route("/readme")
def readme_page():
    # Installed layout keeps README.md next to app.py; the source tree keeps it
    # one level up (repo root).  Try both so the link works either way.
    text = None
    for path in (os.path.join(_HERE, "README.md"),
                 os.path.join(_HERE, os.pardir, "README.md")):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            break
        except FileNotFoundError:
            continue
    if text is None:
        return "README not found", 404
    html = md_lib.markdown(text, extensions=["tables", "fenced_code", "extra"])
    return render_template("details.html", title="omdrcctrl — README", content=html)


@app.route("/details-spectrum")
def spectrum_details_page():
    text = None
    for path in (os.path.join(_HERE, "SPECTRUM_ANALYZER.md"),
                 os.path.join(_HERE, os.pardir, "SPECTRUM_ANALYZER.md")):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            break
        except FileNotFoundError:
            continue
    if text is None:
        return "Spectrum analyzer documentation not found", 404
    html = md_lib.markdown(text, extensions=["tables", "fenced_code", "extra"])
    return render_template("details.html", title="Live spectrum analyzer", content=html)


# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/run/<cmd_id>", methods=["POST"])
def run_command(cmd_id):
    if cmd_id not in CMD_MAP:
        return jsonify({"ok": False, "error": "Unknown command"}), 404
    cmd = CMD_MAP[cmd_id]
    if cmd["type"] != "WRITE":
        return jsonify({"ok": False, "error": "Not a WRITE command"}), 400

    log = tempfile.NamedTemporaryFile(
        mode="w+b",
        prefix=f"omdrcctrl-{cmd_id}-",
        suffix=".log",
        delete=False,
    )
    log_path = log.name
    proc = subprocess.Popen(
        cmd["cmd"], shell=True, env=_env(),
        stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    try:
        rc = proc.wait(timeout=5)
        if rc != 0:
            err = _command_failure_output(cmd, log_path)
            _unlink_quietly(log_path)
            return jsonify({
                "ok": False,
                "error": err or f"exit code {rc}",
                "output": err,
            })
        _unlink_quietly(log_path)
        return jsonify({"ok": True})
    except subprocess.TimeoutExpired:
        # Keep waiting in the background so the child is reaped, but leave its
        # stdio detached from the HTTP request.
        threading.Thread(target=_wait_and_cleanup, args=(proc, log_path), daemon=True).start()
        return jsonify({"ok": True})  # still running → launched successfully


@app.route("/read/<cmd_id>")
def read_command(cmd_id):
    if cmd_id not in CMD_MAP:
        return jsonify({"ok": False, "error": "Unknown command"}), 404
    cmd = CMD_MAP[cmd_id]
    if cmd["type"] != "READ":
        return jsonify({"ok": False, "error": "Not a READ command"}), 400

    try:
        result = subprocess.run(
            cmd["cmd"], shell=True, env=_env(),
            capture_output=True, text=True, timeout=10,
        )
        ok     = result.returncode == 0
        output = (result.stdout + result.stderr).strip()
        if ok:
            output = _format_read_output(cmd_id, output)
        resp   = {"ok": ok, "output": output or (None if ok else f"exit {result.returncode}")}
        if ok and output and "details_root" in cmd and _find_dyn_details(cmd, output):
            resp["details_url"] = f"/details-dyn/{cmd_id}/{output}"
        return jsonify(resp)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "output": "timeout"})


@app.route("/qconnect/status")
def qconnect_status():
    try:
        with open(QCONNECT_STATUS_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
        return jsonify({
            "ok":    True,
            "line1": lines[0] if len(lines) > 0 else "",
            "line2": lines[1] if len(lines) > 1 else "",
            # Line 3 is qobuzconnect2mpd's activity line: what it is doing
            # between the phone's play command and the first sound (resolving
            # stream URLs, reconstructing segments, waiting on MPD), or the
            # download error that stopped it.  Empty when it is just playing.
            "line3": lines[2] if len(lines) > 2 else "",
        })
    except FileNotFoundError:
        return jsonify({"ok": False, "line1": "", "line2": "", "line3": ""})
    except OSError as e:
        return jsonify({"ok": False, "line1": "", "line2": "", "line3": "",
                        "error": str(e)})


def _service_running(name: str) -> bool:
    """True if renderer service `name` is currently running.  Linux: systemd
    --user is-active.  FreeBSD: rc `onestatus` (works whether or not the service
    is enabled in rc.conf), falling back to a process check.

    `service <name> onestatus` was unreliable with older qobuzconnect2mpd rc
    scripts: their pidfile lived below a dedicated account's 0700 state dir and
    the panel could not traverse it.  Current installs run every renderer as
    AUDIO_USER and keep qobuzconnect2mpd's pidfiles under /var/run, but retain
    the process fallback so an upgrade cannot briefly mis-detect the old layout
    and let both mutually-exclusive renderers drive MPD at once.

    Process visibility is not privileged, so fall back to matching the running
    binary — both renderers install as <prefix>/bin/<service-name>, the same
    argv[0] their rc scripts use as `procname`.  Matching argv[0] rather than
    the whole line keeps daemon(8) wrappers and greps from counting as hits.
    """
    if _IS_LINUX:
        cmd = ["systemctl", "--user", "is-active", "--quiet", name]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                               env=_env())
            return r.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False
    try:
        r = subprocess.run(["service", name, "onestatus"],
                           capture_output=True, text=True, timeout=10,
                           env=_env())
        if r.returncode == 0:
            return True
    except (subprocess.TimeoutExpired, OSError):
        pass
    return _proc_running(name)


def _proc_running(binname: str) -> bool:
    """True if a process whose argv[0] basename is `binname` is running.
    Unprivileged and config-free — see _service_running for why the rc status
    check alone is not trustworthy."""
    for line in _ps_arg_lines():
        try:
            argv = shlex.split(line)
        except ValueError:
            argv = line.split()
        if argv and os.path.basename(argv[0]) == binname:
            return True
    return False


def _service_action(name: str, action: str):
    """Start or stop renderer service `name`.  `action` is the FreeBSD verb
    (onestart / onestop); on Linux it maps to `systemctl --user start|stop`
    (user scope — no sudo needed)."""
    if _IS_LINUX:
        verb = "start" if action == "onestart" else "stop"
        cmd = ["systemctl", "--user", verb, name]
    else:
        cmd = ["sudo", "service", name, action]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                          env=_env())


def _resolve_mpd_port() -> str | None:
    """Best-effort MPD port from the common default config locations (Linux and
    FreeBSD).  Returns None to let mpc fall back to its own default."""
    for p in ("/usr/local/etc/musicpd.conf",
              "/usr/local/etc/mpd.conf",
              "/etc/mpd.conf",
              os.path.expanduser("~/.config/mpd/mpd.conf"),
              os.path.expanduser("~/.mpdconf")):
        if os.path.isfile(p):
            return _mpd_port_from_conf(p)
    return None


def _mpc_quiesce():
    """Stop playback and clear the queue so the incoming renderer starts from a
    clean MPD state.  Best-effort: failures are ignored (MPD may be down)."""
    cmd = _mpc_client()
    if not cmd:
        return
    port = _resolve_mpd_port()
    base = cmd + (["-p", str(port)] if port else [])
    for action in ("stop", "clear"):
        try:
            subprocess.run(base + [action],
                           capture_output=True, text=True, timeout=5, env=_env())
        except (subprocess.TimeoutExpired, OSError):
            pass


@app.route("/qconnect/services")
def qconnect_services():
    """Running state of the two mutually-exclusive renderers — used to keep the
    web UI toggle in sync with reality.

    `remembered` is what the toggle last recorded (None until it is first used);
    `boot` is the renderer the boot service would actually bring up on the NEXT
    reboot, which is the remembered one or, when nothing is recorded yet, the
    default the helper falls back to — `boot_is_default` says which of the two
    it is.  Neither field says anything about what the *last* boot started: the
    panel must word it as a statement about the next one."""
    remembered = _read_state_str(_RENDERER_STATE_FILE)
    if remembered not in SWITCHABLE_SERVICES:
        remembered = None       # unset, or a stale name the helper would ignore
    # The temporary `-L` callback receiver has the same argv[0] as the normal
    # daemon, so the process fallback in _service_running() can see it.  It is
    # not an active renderer and must not light the qobuzconnect2mpd toggle.
    qconnect_running = (_service_running(QCONNECT_SERVICE) and
                        not _qconnect_oauth_active())
    return jsonify({
        "ok":               True,
        "qobuzconnect2mpd": qconnect_running,
        "upmpdcli":         _service_running(UPMPDCLI_SERVICE),
        "remembered":       remembered,
        "boot":             remembered or RENDERER_BOOT_DEFAULT,
        "boot_is_default":  remembered is None,
    })


@app.route("/qconnect/switch", methods=["POST"])
def qconnect_switch():
    """Switch the active renderer: stop the other service, then start the
    target.  Body: {"target": "qobuzconnect2mpd"|"upmpdcli"}."""
    data   = request.get_json(silent=True) or {}
    target = data.get("target")
    if target not in SWITCHABLE_SERVICES:
        return jsonify({"ok": False, "error": "invalid target"}), 400
    if target == QCONNECT_SERVICE and _qconnect_oauth_active():
        return jsonify({"ok": False,
                        "error": "qobuzconnect2mpd sign-in is still in progress"}), 409
    ok, error = _activate_renderer(target)
    return jsonify({"ok": ok, **({"active": target} if ok else {"error": error})})


def _activate_renderer(target: str) -> tuple[bool, str]:
    """Stop the other renderer, start `target`, and remember it for boot.

    Shared by the ordinary renderer toggle and qobuzconnect2mpd's OAuth worker:
    after `-L` caches the token, the background worker completes the same safe
    switch the listener would otherwise have to perform by hand.
    """
    other = UPMPDCLI_SERVICE if target == QCONNECT_SERVICE else QCONNECT_SERVICE
    try:
        # Stop the active one before starting the next.  The stop is issued
        # unconditionally rather than gated on _service_running(): a status
        # check that wrongly reports "not running" would otherwise skip it and
        # leave both renderers driving MPD.  onestop on an already-stopped
        # service exits non-zero, so what counts is the state afterwards.
        _service_action(other, "onestop")
        if _service_running(other):
            return False, f"could not stop {other}"
        # Leave MPD in a clean state for the incoming renderer.
        _mpc_quiesce()
        if not _service_running(target):
            r = _service_action(target, "onestart")
            if r.returncode != 0:
                return False, f"starting {target}: {(r.stderr or r.stdout).strip()}"
        # Remember the choice for the next boot.  Written only once the switch
        # has actually succeeded, so a failed switch does not arm the boot
        # service with a renderer that would not start.
        _write_state_str(_RENDERER_STATE_FILE, target)
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError as e:
        return False, str(e)


@app.route("/qconnect/restart", methods=["POST"])
def qconnect_restart():
    try:
        r = _service_action(QCONNECT_SERVICE, "onestop")
        if r.returncode != 0:
            return jsonify({"ok": False, "error": (r.stderr or r.stdout).strip()})
        r = _service_action(QCONNECT_SERVICE, "onestart")
        if r.returncode != 0:
            return jsonify({"ok": False, "error": (r.stderr or r.stdout).strip()})
        return jsonify({"ok": True})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/qconnect/log")
def qconnect_log():
    try:
        with open(QCONNECT_LOG_FILE, encoding="utf-8") as f:
            content = f.read()
        return jsonify({"ok": True, "content": content})
    except FileNotFoundError:
        return jsonify({"ok": True, "content": "(log file not found)"})
    except OSError as e:
        return jsonify({"ok": False, "content": str(e)})


# ── log viewer + log alerts ──────────────────────────────────────────────────

def _log_source(source_id: str) -> dict | None:
    return next((s for s in LOG_SOURCES if s["id"] == source_id), None)


def _log_stat(path: str) -> dict:
    try:
        st = os.stat(path)
        return {"exists": True, "size": st.st_size, "mtime": int(st.st_mtime)}
    except OSError:
        return {"exists": False, "size": 0, "mtime": None}


def _source_lines(source: dict, cache: dict[str, list[str]]) -> list[str]:
    if source["id"] not in cache:
        try:
            text, _ = _tail_text(source["path"], LOG_SCAN_BYTES)
        except OSError:
            text = ""
        cache[source["id"]] = text.splitlines()
    return cache[source["id"]]


def _rule_applies(rule: dict, source_id: str) -> bool:
    return not rule["sources"] or source_id in rule["sources"]


def _rule_settled_by_file(rule: dict) -> bool:
    """True when a file on disk contradicts the rule outright.

    Ordering in a log is a proxy; some conditions have a fact behind them.  A
    rule that names `clears_file` is not raised while that file matches
    `clears_file_pattern`, whatever a stale tail still says — which is how a
    stored Qobuz token silences "OAuth initialisation not done" without waiting
    for the log to be rewritten.  Use it only where the file *is* the subject of
    the log line: a token Qobuz refuses is a different condition, and the rule
    for that one deliberately has no file check."""
    if rule.get("clears_file_regex") is None:
        return False
    path = rule.get("clears_file", "")
    if path == _QOBUZ_TOKEN_PLACEHOLDER:
        path = _qobuz_cache_config()
    return bool(path) and bool(rule["clears_file_regex"].search(_read_text_quietly(path)))


def _scan_log_alerts() -> list[dict]:
    """Evaluate every rule against every source it applies to.

    A rule is active when the last line matching `pattern` is newer than the
    last line matching `clears` — so a failure that a later restart fixed (and a
    success that a later failure undid) both stop being reported, without the
    app having to keep any state of its own."""
    cache: dict[str, list[str]] = {}
    running: dict[str, bool] = {}
    active = []
    for rule in LOG_ALERTS:
        if _rule_settled_by_file(rule):
            continue
        # A rule that names a `service` is speaking for that program, and its
        # log outlives it.  With the service stopped the log is last week's
        # news: worth showing, not worth demanding anything about — so a
        # failure is reported as info, and a success is not reported at all
        # (nothing is connected while nothing is running).
        service = rule.get("service", "")
        if service and service not in running:
            running[service] = _service_running(service)
            if service == QCONNECT_SERVICE and _qconnect_oauth_active():
                running[service] = False
        idle = bool(service) and not running[service]
        if idle and rule["severity"] == "ok":
            continue
        severity = "info" if idle else rule["severity"]
        for source in LOG_SOURCES:
            if not _rule_applies(rule, source["id"]):
                continue
            lines = _source_lines(source, cache)
            hits = [i for i, line in enumerate(lines) if rule["regex"].search(line)]
            if not hits:
                continue
            if rule["clears_regex"] is not None:
                cleared = [i for i, line in enumerate(lines)
                           if rule["clears_regex"].search(line)]
                if cleared and cleared[-1] > hits[-1]:
                    continue
            line = lines[hits[-1]].strip()
            stat = _log_stat(source["path"])
            active.append({
                "id":           rule["id"],
                "severity":     severity,
                "service":      service,
                "service_running": running.get(service, True) if service else True,
                "message":      rule["message"],
                "hint":         rule["hint"],
                "source":       source["id"],
                "source_label": source["label"],
                "line":         line[:400],
                "action":       rule.get("action", ""),
                "count":        len(hits),
                "at":           stat["mtime"],
                # Changes whenever the matched line or its repeat count does, so
                # the browser can re-raise a dismissed alert on a fresh hit.
                "key": hashlib.sha1(
                    f"{rule['id']}|{source['id']}|{line}|{len(hits)}".encode()
                ).hexdigest()[:16],
            })
    order = {s: i for i, s in enumerate(("error", "warn", "info", "ok"))}
    active.sort(key=lambda a: (order.get(a["severity"], 9), a["id"], a["source"]))
    return active


def _log_source_summary() -> list[dict]:
    return [dict(s, **_log_stat(s["path"])) for s in LOG_SOURCES]


@app.route("/logs/sources")
def logs_sources():
    return jsonify({"ok": True, "sources": _log_source_summary()})


@app.route("/logs/tail")
def logs_tail():
    """Tail of one configured log, with the indices of the lines that trip an
    alert rule so the viewer can mark them."""
    source = _log_source(request.args.get("source", ""))
    if source is None:
        return jsonify({"ok": False, "error": "unknown log source"}), 404
    try:
        limit = int(request.args.get("bytes", LOG_TAIL_BYTES))
    except ValueError:
        limit = LOG_TAIL_BYTES
    limit = max(4096, min(LOG_TAIL_BYTES, limit))
    stat = _log_stat(source["path"])
    try:
        content, truncated = _tail_text(source["path"], limit)
    except FileNotFoundError:
        return jsonify({"ok": True, "id": source["id"], "path": source["path"],
                        "content": "", "truncated": False, "matches": [], **stat})
    except OSError as e:
        return jsonify({"ok": False, "error": str(e), "id": source["id"],
                        "path": source["path"]})
    rules = [r for r in LOG_ALERTS
             if _rule_applies(r, source["id"]) and r["severity"] != "ok"]
    matches = [i for i, line in enumerate(content.splitlines())
               if any(r["regex"].search(line) for r in rules)]
    return jsonify({"ok": True, "id": source["id"], "label": source["label"],
                    "path": source["path"], "content": content,
                    "truncated": truncated, "matches": matches, **stat})


@app.route("/logs/alerts")
def logs_alerts():
    # status_sources are the logs the `ok` rules read: when none of them has
    # matched, the UI needs those to say whether the status is simply quiet or
    # the log it would come from has not been written yet.
    watched = {s for r in LOG_ALERTS if r["severity"] == "ok" for s in r["sources"]}

    summary = _log_source_summary()
    return jsonify({
        "ok": True,
        "alerts": _scan_log_alerts(),
        "sources": summary,
        "status_sources": [s for s in summary if not watched or s["id"] in watched],
    })




def _cdin_stats_fields(body: str) -> dict:
    """The numbers out of one `[stats]` line (cdin/src/main.c formats it).

    The line is ~200 characters of which two decide whether anything is wrong:
    how much margin is left (`lead`) and how much of it has already run out
    (`starves`).  The rest is context.  Anything the line does not carry is
    absent from the result rather than zero — a capture-only run has no lead at
    all, and reporting that as `lead 0` would read as a fault."""
    f: dict = {}
    if "capture-only" in body:
        f["measure_only"] = True

    m = re.search(r"\blead (\d+) ms(?: \(min (\d+), max (\d+)\))?", body)
    if m:
        f["lead_ms"] = int(m.group(1))
        if m.group(2):
            f["lead_min_ms"] = int(m.group(2))
            f["lead_max_ms"] = int(m.group(3))

    # The drift field is prose with a number in it, and it has more states than
    # "a number": it settles, it is anchored, and it is thrown away whenever the
    # lead moves for a reason that is not the clocks.
    m = re.search(r"\bdrift ([+-][\d.]+) ppm", body)
    if m:
        f["drift_ppm"] = float(m.group(1))
    elif re.search(r"\bdrift ~0 ppm", body):
        f["drift_ppm"] = 0.0
    elif re.search(r"\bdrift settling", body):
        f["drift_settling"] = True
    elif re.search(r"\bdrift ref (?:set|dropped)", body):
        f["drift_settling"] = True
    m = re.search(r"\b(lead drains|ring fills) in (\d+) h", body)
    if m:
        f["horizon_what"] = m.group(1)
        f["horizon_h"] = int(m.group(2))

    for key, pattern in (("drops_bytes", r"\bdrops (\d+) B"),
                         ("starves",     r"\bstarves (\d+)"),
                         ("dropouts",    r"\bdropouts (\d+)"),
                         ("rig_stalls",  r"\brig stalls (\d+)"),
                         ("rig_slips",   r"\bslips (\d+)"),
                         ("up_s",        r"\bup (\d+) s")):
        m = re.search(pattern, body)
        if m:
            f[key] = int(m.group(1))
    for key, pattern in (("in_hz", r"\bin ([\d.]+) Hz"),
                         ("out_hz", r"\bout ([\d.]+) Hz")):
        m = re.search(pattern, body)
        if m:
            f[key] = float(m.group(1))
    m = re.search(r"\bsilence (\d+%|n/a)", body)
    if m:
        f["silence"] = m.group(1)
    return f


def _cdin_uptime(seconds: int) -> str:
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} m"
    if seconds >= 60:
        return f"{seconds // 60} m {seconds % 60:02d} s"
    return f"{seconds} s"


def _cdin_plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _cdin_readout(status: dict) -> tuple[list, list]:
    """The chips and the problem list the card shows instead of the raw line.

    A chip is a measurement; a problem is a measurement that has already cost
    something audible.  They are separate because `starves 0` is worth a glance
    and `starves 3` is worth a sentence: three dropouts happened, they are in
    the past, and no live status line would ever mention them again."""
    f = status.get("stats_fields") or {}
    metrics: list[dict] = []
    problems: list[dict] = []

    def chip(key, label, value, level="ok"):
        metrics.append({"key": key, "label": label, "value": value, "level": level})

    if "lead_ms" in f:
        lead = f["lead_ms"]
        # Below ~250 ms there is nothing left to absorb a transport seek — the
        # same floor the daemon warns about at startup.
        low = lead < 250
        value = f"{lead} ms"
        if "lead_min_ms" in f:
            value += f" (min {f['lead_min_ms']})"
        chip("lead", "buffer", value, "warn" if low else "ok")
        if low:
            problems.append({"level": "warn", "text":
                f"lead down to {lead} ms — nothing left to absorb a transport seek"})

    if "drift_ppm" in f:
        value = f"{f['drift_ppm']:+.1f} ppm"
        if "horizon_h" in f:
            what = "drains" if f.get("horizon_what") == "lead drains" else "fills"
            value += f", {what} in {f['horizon_h']} h"
        chip("drift", "drift", value)
    elif f.get("drift_settling"):
        chip("drift", "drift", "settling", "muted")

    starves = f.get("starves")
    if starves is not None:
        chip("starves", "underruns", str(starves), "error" if starves else "ok")
        if starves:
            problems.append({"level": "error", "text":
                f"{_cdin_plural(starves, 'underrun')} — the output ran dry, "
                "which is an audible dropout"})

    drops = f.get("drops_bytes")
    if drops is not None:
        chip("drops", "dropped", f"{drops} B", "error" if drops else "ok")
        if drops:
            problems.append({"level": "error", "text":
                f"{drops} B discarded — the ring filled up, one discontinuity per drop"})

    if f.get("silence"):
        chip("silence", "silence", f["silence"], "muted")
    if "up_s" in f:
        chip("up", "up", _cdin_uptime(f["up_s"]), "muted")

    if f.get("dropouts"):
        # Scripted carrier drops (the test rig).  Deliberate, and the lead they
        # cost is the measurement — never a fault.
        problems.append({"level": "info", "text":
            f"{_cdin_plural(f['dropouts'], 'carrier dropout')} absorbed by the lead"})
    if f.get("rig_stalls") or f.get("rig_slips"):
        problems.append({"level": "warn", "text":
            f"the source medium could not keep up (stalls {f.get('rig_stalls', 0)}, "
            f"slips {f.get('rig_slips', 0)}) — a property of the test rig, not the bridge"})

    for end in (status["capture"], status["output"]):
        if end["available"] is False:
            problems.append({"level": "error", "text":
                f"{end['label']} {end['path']} cannot be opened"
                + (f" — {end['error']}" if end["error"] else "")})

    return metrics, problems


def _cdin_log_source_id() -> str:
    """The [logs] id that points at the daemon's log, so the card's Log button
    can open the whole thing in the Logs card instead of duplicating a viewer.
    Empty when the log is not listed there — then there is nothing to open."""
    target = os.path.abspath(CDIN_LOG_FILE)
    for src in LOG_SOURCES:
        if os.path.abspath(src["path"]) == target:
            return src["id"]
    return ""


def _cdin_blank_end(kind: str) -> dict:
    """An end of the bridge before the log has said anything about it.

    `available` is None rather than False on purpose: "not known yet" and
    "known to be broken" are different answers, and only the second one is a
    red light.  A daemon that has just started, or one whose log was rotated
    out from under it, is in the first."""
    return {"kind": kind, "label": _CDIN_DEVICE_LABEL[kind], "path": "",
            "available": None, "error": "", "held": False, "at": ""}


def _cdin_status() -> dict:
    """Read the daemon's log and reduce it to what the card shows.

    Errors and the current status are treated differently on purpose.  A
    failure is an EVENT — it happened, it is worth keeping, and it stays in the
    list in red even after the condition clears, because "the output was
    missing for ten minutes this morning" is exactly the thing a live status
    line cannot tell you.  Everything healthy is a STATE, so only its most
    recent line is kept: a hundred identical "state playing" lines say no more
    than one, and would push the failures off the top."""
    running = _process_running(CDIN_PROCESS)
    stat = _log_stat(CDIN_LOG_FILE)
    status = {
        "ok": True,
        "enabled": CDIN_ENABLED,
        "running": running,
        "process": CDIN_PROCESS,
        "service": CDIN_SERVICE,
        "log": dict(stat, path=CDIN_LOG_FILE),
        "state": "",
        "state_why": "",
        "capture": _cdin_blank_end("capture"),
        "output": _cdin_blank_end("playback"),
        "stats": "",
        "stats_at": "",
        "stats_fields": {},
        "metrics": [],
        "problems": [],
        "events": [],
        "last_error": None,
        "truncated": False,
        "led": "idle",
        "summary": "",
        # `active` is the card's own question, and it is narrower than
        # `running`: is a disc playing THROUGH the bridge right now?  That is
        # what decides whether the card is worth the screen space it takes.
        "active": False,
        "control": CDIN_CONTROL,
    }

    try:
        text, _ = _tail_text(CDIN_LOG_FILE, CDIN_SCAN_BYTES)
    except OSError:
        text = ""
    if not text:
        status["summary"] = ("no log yet at " + CDIN_LOG_FILE) if running \
            else "not running"
        return status

    ends = {"capture": status["capture"], "playback": status["output"]}
    errors: list[dict] = []       # kept: every failure in the window
    current: dict | None = None   # replaced: the newest healthy line
    dropped = 0

    for index, raw in enumerate(text.splitlines()):
        m = _CDIN_LINE.match(raw)
        if m is None:
            continue
        ts, level, msg = m.group("ts"), m.group("lvl"), m.group("msg")
        if level == "DBG":
            continue

        start = _CDIN_START.match(msg)
        if start is not None:
            # A restart: the ends are whatever the new run finds them to be,
            # not what the old one left behind.
            for kind, key in (("capture", "inpath"), ("playback", "outpath")):
                ends[kind].update(_cdin_blank_end(kind))
                ends[kind]["path"] = start.group(key)

        stats = _CDIN_STATS.match(msg)
        if stats is not None:
            status["stats"] = stats.group("body")
            status["stats_at"] = ts
            continue

        event = {"at": ts, "text": msg, "severity": "info", "index": index}

        device = _CDIN_DEVICE.match(msg)
        state = _CDIN_STATE.match(msg)
        if device is not None:
            end = ends[device.group("dev")]
            what = device.group("what")
            end["path"] = device.group("path")
            end["at"] = ts
            if what == "unavailable":
                end["available"] = False
                end["error"] = device.group("rest").strip()
            elif what == "available":
                end["available"] = True
                end["error"] = ""
            elif what == "acquired":
                end["held"] = True
                end["available"] = True
            else:                       # released
                end["held"] = False
        elif state is not None:
            status["state"] = state.group("state")
            status["state_why"] = state.group("why")

        if level == "ERR":
            event["severity"] = "error"
        elif level == "WRN":
            event["severity"] = "warn"
        elif device is not None or state is not None:
            event["severity"] = "ok"

        if event["severity"] in ("error", "warn"):
            errors.append(event)
            if len(errors) > CDIN_MAX_EVENTS:
                errors.pop(0)
                dropped += 1
        elif event["severity"] == "ok":
            current = event

    # Chronological, with the healthy line wherever it actually belongs: it is
    # last when nothing has gone wrong since, and not last when something has.
    events = errors + ([current] if current is not None else [])
    events.sort(key=lambda e: e["index"])
    for e in events:
        e.pop("index", None)
    status["events"] = events
    status["truncated"] = dropped > 0
    # The newest failure, kept apart from the list so the card can put it on one
    # red line without the reader having to scan for it.  It survives the
    # condition clearing, which is the whole point of keeping failures at all —
    # so the only thing that takes it down is the reader saying they have read
    # it.  That is a watermark, not a flag: anything newer than the dismissed
    # timestamp still shows, and the events list below keeps the history either
    # way.  Dismissing is a note about the READER, not about the daemon.
    newest = errors[-1] if errors else None
    acked = _read_state_str(_CDIN_ACK_FILE)
    status["last_error"] = None if (
        newest is not None and acked is not None
        and newest["at"] <= acked) else newest

    status["led"], status["summary"] = _cdin_verdict(status)
    status["active"] = bool(running and status["state"] == "playing")
    if status["stats"]:
        status["stats_fields"] = _cdin_stats_fields(status["stats"])
    status["metrics"], status["problems"] = _cdin_readout(status)
    return status


def _cdin_verdict(status: dict) -> tuple[str, str]:
    """The LED and the one line beside it.

    The daemon's log outlives the daemon, so a stopped bridge is reported as
    idle rather than broken however alarming its last lines are — the same rule
    the [alert:*] rules apply to a stopped renderer.  Nothing is unavailable
    when nothing is trying to open it."""
    if not status["running"]:
        return "idle", "not running"

    down = [end for end in (status["capture"], status["output"])
            if end["available"] is False]
    if down:
        which = " and ".join(e["label"] for e in down)
        why = next((e["error"] for e in down if e["error"]), "")
        # "(retrying every 2 s)" is true of every one of these and says nothing
        # about this one; the full line is still in the event list below.
        why = re.sub(r"\s*\([^()]*\)\s*$", "", why)
        return "red", f"{which} unavailable" + (f" — {why}" if why else "")

    if status["state"] == "playing":
        return "green", "playing — audio on the wire"
    if status["state"] == "idle":
        return "green", "idle — waiting for audio, output released"
    if status["state"] == "no-carrier":
        # Not red.  A CD player that is switched off drops the S/PDIF carrier,
        # and the daemon parking in no-carrier is it working correctly — the
        # output is released and MPD has the chain.  A carrier loss that IS a
        # fault (the interface gone, the device refusing to open) shows up as
        # `available is False` and was already caught above, in red.
        return "idle", "no carrier — " + (status["state_why"] or "nothing on the wire")
    if any(end["available"] is None for end in (status["capture"], status["output"])):
        return "idle", "starting up"
    return "green", "running"


@app.route("/cdin/status")
def cdin_status():
    if not CDIN_ENABLED:
        return jsonify({"ok": True, "enabled": False})
    return jsonify(_cdin_status())


_CDIN_AT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


@app.route("/cdin/dismiss", methods=["POST"])
def cdin_dismiss():
    """Take the standing red line down, without touching the log.

    Clearing the log itself would be the obvious way and is the wrong one: the
    card reads the daemon's CURRENT state out of those same lines, so
    truncating the file to hide an old failure would also blank the state, the
    device rows and the stats — the card would go blind to hide one sentence.
    Nothing is deleted here.  We record which failure the reader has seen, and
    anything newer than it still comes through.

    The client sends the timestamp it is actually looking at, so a failure that
    arrives between the render and the click is not swept up with it; falling
    back to the newest known failure only covers a client that sends nothing.
    """
    if not CDIN_ENABLED:
        return jsonify({"ok": False, "error": "the CD input card is disabled"})

    at = (request.get_json(silent=True) or {}).get("at")
    if at is not None and not _CDIN_AT.match(str(at).strip()):
        return jsonify({"ok": False, "error": "not a log timestamp"}), 400
    if at is None:
        newest = _cdin_status().get("last_error")
        if newest is None:
            return jsonify({"ok": True, **_cdin_status()})
        at = newest["at"]

    _write_state_str(_CDIN_ACK_FILE, str(at).strip())
    return jsonify({"ok": True, **_cdin_status()})


@app.route("/cdin/control", methods=["POST"])
def cdin_control():
    """Start or stop the bridge by hand.

    The daemon is designed to be left running, so this is not the normal way to
    use it — but "not normal" is not "never": the bridge is the only thing
    besides MPD and mpv that opens /dev/dsp.play, and being able to take it out
    of the chain without an ssh session is worth two buttons.

    The rc verb's exit status is not the answer.  `service ... onestart` forks
    a daemon(8) and returns at once, so it can succeed while the daemon dies a
    moment later on a device that is not there; and `onestop` returns before
    the process has finished closing its devices.  So the process itself is
    polled until it agrees, and that is what gets reported."""
    if not CDIN_ENABLED:
        return jsonify({"ok": False, "error": "the CD input card is disabled"}), 404
    if not CDIN_CONTROL:
        return jsonify({"ok": False,
                        "error": "manual control is off (control = no in [cdin])"}), 403

    action = ((request.get_json(silent=True) or {}).get("action") or "").strip()
    verb = {"start": "onestart", "stop": "onestop"}.get(action)
    if verb is None:
        return jsonify({"ok": False, "error": "invalid action"}), 400

    try:
        r = _service_action(CDIN_SERVICE, verb)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": f"`service {CDIN_SERVICE} {verb}` timed out",
                        "status": _cdin_status()})
    except OSError as e:
        return jsonify({"ok": False, "error": str(e), "status": _cdin_status()})

    want = (action == "start")
    deadline = time.monotonic() + CDIN_SETTLE_SECONDS
    running = _process_running(CDIN_PROCESS)
    while running != want and time.monotonic() < deadline:
        time.sleep(0.25)
        running = _process_running(CDIN_PROCESS)

    status = _cdin_status()
    if running == want:
        return jsonify({"ok": True, "action": action, "status": status})

    detail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
    error = detail[-1] if detail else ""
    if not error:
        error = (f"{CDIN_SERVICE} did not start — see the log"
                 if want else f"{CDIN_SERVICE} is still running")
    # The one failure worth naming, because it is the one a fresh install hits.
    if "password" in error.lower() or "sudo" in error.lower():
        error += (f" (the panel needs a NOPASSWD grant for "
                  f"`service {CDIN_SERVICE} {verb}`)")
    return jsonify({"ok": False, "action": action, "error": error, "status": status})


# ── Qobuz OAuth initialisation ───────────────────────────────────────────────

# ── the audio chain: device holders, and the diagram built out of them ───────

_CHAIN_SUDO_OK: bool | None = None   # None = not tried yet, False = no grant


def _chain_tool_command(argv: list[str]) -> list[str]:
    """`argv`, escalated once through `sudo -n` when that is worth trying.

    Running as root, or with escalation turned off, or after a `sudo -n` has
    already been refused, the command is used as it is — a grant that is not
    there will not appear on the next poll, and retrying it every four seconds
    would only fill the auth log."""
    if os.geteuid() == 0 or not CHAIN_PRIVILEGED or _CHAIN_SUDO_OK is False:
        return argv
    return ["sudo", "-n", *argv]


# What sudo(8) says when it will not run the command: no grant, or a password
# it is forbidden from asking for.  This has to be told apart from the TOOL
# failing, because `fuser -v` on a device nobody holds also exits non-zero with
# nothing to say — reading that as a refusal would permanently downgrade the
# card on a perfectly healthy idle box.
_SUDO_REFUSAL = re.compile(
    r"^sudo:|a (?:password|terminal) is required|not allowed to execute",
    re.MULTILINE)


def _chain_run_tool(argv: list[str], timeout: float = 5.0) -> tuple[str, bool]:
    """Run a holder-listing tool, downgrading out of sudo on the first refusal.

    Returns (combined output, privileged).  `privileged` is what the answer was
    actually produced with, not what was asked for, so the card can say "only
    this user's processes are listed" when it matters."""
    global _CHAIN_SUDO_OK
    command = _chain_tool_command(argv)
    escalated = command[0] == "sudo"
    try:
        r = subprocess.run(command, capture_output=True, text=True,
                           timeout=timeout, env=_env())
    except Exception:
        if escalated:
            _CHAIN_SUDO_OK = False      # no sudo on this box at all
        return "", os.geteuid() == 0
    output = r.stdout + r.stderr
    if escalated and _SUDO_REFUSAL.search(output):
        # Remember it: a grant that is not there will not appear on the next
        # poll, and retrying every few seconds would only fill the auth log.
        _CHAIN_SUDO_OK = False
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout, env=_env())
        except Exception:
            return "", False
        return r.stdout + r.stderr, False
    if escalated:
        _CHAIN_SUDO_OK = True
    return output, escalated or os.geteuid() == 0


def _merge_mode(old: str, new: str) -> str:
    return "".join(c for c in "rw" if c in old or c in new) or old or new


def _add_holder(into: dict, key, pid: str, cmd: str, user: str, mode: str) -> None:
    """One row per process per device, with the modes of all its descriptors on
    that device merged: a program that opened the DAC twice is one block in the
    diagram, and one that opened it O_RDWR is both a reader and a writer."""
    holders = into.setdefault(key, {})
    row = holders.get(pid)
    if row is None:
        holders[pid] = {"pid": pid, "cmd": cmd, "user": user, "mode": mode}
    else:
        row["mode"] = _merge_mode(row["mode"], mode)


def _holders_fstat(paths: list[str]) -> tuple[dict[str, list[dict]], bool]:
    """FreeBSD: fstat(1), one call for every device, matched back by inode.

    Matching on the NAME column would be wrong: fstat resolves symlinks and
    prints one row per open file, so asking about /dev/dsp.dac (a symlink
    omdrc_audio points at the real card) and /dev/dsp0 in the same call yields
    rows that name only one of them.  The inode is the identity that survives
    that, and stat(2) gives us the same number the INUM column carries."""
    inodes: dict[int, list[str]] = {}
    for path in paths:
        try:
            inodes.setdefault(os.stat(path).st_ino, []).append(path)
        except OSError:
            continue
    if not inodes:
        return {}, os.geteuid() == 0

    # One path per inode is enough to ask about; the answer is recorded against
    # every name that resolves to it, so a card configured with both
    # /dev/dsp.dac and /dev/dsp0 does not lose one of them to the de-duplication.
    out, privileged = _chain_run_tool(
        ["fstat", *sorted(names[0] for names in inodes.values())])
    found: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.split()
        # USER CMD PID FD MOUNT INUM MODE SZ|DV R/W [NAME]
        if len(parts) < 9 or parts[0] == "USER":
            continue
        try:
            inode = int(parts[5])
        except ValueError:
            continue
        mode = "".join(c for c in parts[8].lower() if c in "rw")
        for path in inodes.get(inode, ()):
            _add_holder(found, path, parts[2], parts[1], parts[0], mode)
    return {p: list(v.values()) for p, v in found.items()}, privileged


_FUSER_ROW = re.compile(r"^(?P<user>\S+)\s+(?P<pid>\d+)\s+(?P<access>\S+)\s+(?P<cmd>.+)$")


def _holders_fuser(paths: list[str]) -> tuple[dict[str, list[dict]], bool]:
    """Linux: fuser(1) -v, whose table names the file it is talking about.

    The ACCESS column is a fixed set of letters; only the two that mean "has
    this open" are of interest here — `F` for a descriptor opened for writing
    and `f` for one that is not.  A device opened O_RDWR reports as `F`, so a
    read-write holder is recorded as a writer, which is the side that matters
    for every device in this chain."""
    live = [p for p in paths if os.path.exists(p)]
    if not live:
        return {}, os.geteuid() == 0
    out, privileged = _chain_run_tool(["fuser", "-v", *live])
    found: dict[str, dict] = {}
    current = ""
    for line in out.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            # "/dev/snd/pcmC0D0p:   giacomo   1234 F.... brutefir"
            name, sep, rest = line.partition(":")
            if not sep:
                continue
            current = name.strip()
            line = rest
        if current not in live:
            continue
        m = _FUSER_ROW.match(line.strip())
        if m is None:
            continue
        access = m.group("access")
        mode = "w" if "F" in access else ("r" if "f" in access else "")
        if not mode:
            continue
        _add_holder(found, current, m.group("pid"), m.group("cmd").strip(),
                    m.group("user"), mode)
    return {p: list(v.values()) for p, v in found.items()}, privileged


def _holders_proc(paths: list[str]) -> tuple[dict[str, list[dict]], bool]:
    """Linux fallback when fuser is not installed: walk /proc ourselves.

    Slower and noisier than fuser, but it reads the open flags out of
    /proc/<pid>/fdinfo, so unlike fuser's ACCESS letters it can tell O_RDONLY
    from O_RDWR exactly."""
    wanted: dict[tuple[int, int], str] = {}
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            continue
        wanted[(st.st_dev, st.st_ino)] = path
    if not wanted:
        return {}, os.geteuid() == 0
    found: dict[str, dict] = {}
    complete = True
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            fds = os.listdir(fd_dir)
        except PermissionError:
            complete = False
            continue
        except OSError:
            continue
        for fd in fds:
            try:
                st = os.stat(f"{fd_dir}/{fd}")
            except OSError:
                continue
            path = wanted.get((st.st_dev, st.st_ino))
            if path is None:
                continue
            mode = "rw"
            try:
                with open(f"/proc/{entry}/fdinfo/{fd}") as f:
                    flags = next((int(l.split(":")[1].strip(), 8)
                                  for l in f if l.startswith("flags:")), 0)
                accmode = flags & 3
                mode = {0: "r", 1: "w"}.get(accmode, "rw")
            except (OSError, ValueError, StopIteration):
                pass
            try:
                with open(f"/proc/{entry}/comm") as f:
                    comm = f.read().strip()
            except OSError:
                comm = "?"
            try:
                user = pwd.getpwuid(os.stat(f"/proc/{entry}").st_uid).pw_name
            except (OSError, KeyError):
                user = "?"
            _add_holder(found, path, entry, comm, user, mode)
    return {p: list(v.values()) for p, v in found.items()}, complete


def _device_holders(paths: list[str]) -> tuple[dict[str, list[dict]], bool]:
    """{device path: [holder, ...]}, plus whether every process was visible."""
    if not paths:
        return {}, True
    if _IS_LINUX:
        if shutil.which("fuser"):
            return _holders_fuser(paths)
        return _holders_proc(paths)
    return _holders_fstat(paths)


_SNDSTAT_CARD = re.compile(r"^pcm(?P<unit>\d+):\s*<(?P<desc>[^>]*)>")
_ALSA_SPEC = re.compile(r"^(?:plug)?(?:hw|default):?(?P<card>[^,:]+)(?:[,:](?P<dev>\d+))?$")


def _sndstat_cards() -> dict[str, str]:
    """{pcm unit: card description} from /dev/sndstat, so a device node can be
    labelled with the card behind it rather than with its number."""
    cards: dict[str, str] = {}
    try:
        with open("/dev/sndstat", errors="replace") as f:
            for line in f:
                m = _SNDSTAT_CARD.match(line.strip())
                if m:
                    cards[m.group("unit")] = m.group("desc").strip()
    except OSError:
        pass
    return cards


def _alsa_card_name(card: str) -> str:
    for name in (f"/proc/asound/card{card}/id", f"/proc/asound/card{card}/../cards"):
        try:
            with open(name) as f:
                first = f.read().strip().splitlines()
                if first:
                    return first[0].strip()
        except OSError:
            continue
    return ""


def _alsa_node(spec: str, side: str) -> str:
    """"hw:1,0" -> "/dev/snd/pcmC1D0p".  A card given by name (hw:CARD=Loopback,
    or plain "Loopback") is resolved through /proc/asound, which is a symlink
    farm from card ids to cardN."""
    m = _ALSA_SPEC.match(spec.strip())
    if m is None:
        return ""
    card, dev = m.group("card"), m.group("dev") or "0"
    if card.startswith("CARD="):
        card = card[5:]
    if not card.isdigit():
        target = os.path.realpath(os.path.join("/proc/asound", card))
        base = os.path.basename(target)
        if not base.startswith("card") or not base[4:].isdigit():
            return ""
        card = base[4:]
    return f"/dev/snd/pcmC{card}D{dev}{side}"


def _chain_device_spec(role: str) -> str:
    configured = CHAIN_DEVICES.get(role)
    if configured is not None:
        return configured.strip()
    return _CHAIN_DEFAULT_DEVICES.get(platform.system(), {}).get(role, "")


def _chain_resolve_devices() -> dict[str, dict]:
    """The four roles resolved to real device nodes, with the card behind each.

    A role whose spec is empty is dropped entirely (that box has no capture
    card, and a permanently grey block teaches nothing).  A role whose node is
    simply not there is kept and reported absent — the DAC being unplugged is
    exactly the thing the diagram should show."""
    cards = {} if _IS_LINUX else _sndstat_cards()
    devices: dict[str, dict] = {}
    for role in _CHAIN_ROLES:
        spec = _chain_device_spec(role)
        if not spec:
            continue
        if _IS_LINUX and not spec.startswith("/"):
            path = _alsa_node(spec, _CHAIN_ALSA_SIDE[role])
        else:
            path = spec
        entry = {"role": role, "spec": spec, "path": path, "target": "",
                 "label": "", "present": bool(path) and os.path.exists(path),
                 "holders": [], "readers": [], "writers": []}
        if entry["present"] and not _IS_LINUX:
            try:
                entry["target"] = os.path.basename(os.readlink(entry["path"]))
            except OSError:
                entry["target"] = os.path.basename(entry["path"])
            unit = entry["target"][3:] if entry["target"].startswith("dsp") else ""
            entry["label"] = cards.get(unit, "")
        elif entry["present"]:
            m = re.search(r"pcmC(\d+)D", entry["path"])
            entry["label"] = _alsa_card_name(m.group(1)) if m else ""
        devices[role] = entry
    return devices


# Where omdrc_audio publishes the role assignment it just made.  Reading it
# is one open per poll and needs no privilege, which is the point: the panel can
# report a DAC that the service GUESSED without shelling out to `service
# omdrc_audio status` every few seconds.
AUDIO_ROLES_FILE = "/var/run/omdrc/audio.roles"


def _audio_roles() -> dict[str, str]:
    """The role assignment omdrc_audio last made, or {} when the service is
    not installed, not enabled, or too old to publish it."""
    try:
        with open(AUDIO_ROLES_FILE, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}
    roles = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            roles[key.strip()] = value.strip()
    return roles


def _chain_bridge_name() -> str:
    """What is standing between the players and brutefir, named as the thing to
    go and look at when the middle of the chain is broken."""
    return "snd-aloop" if _IS_LINUX else "virtual_oss"


def _chain_app_note(cmd: str) -> tuple[str, bool]:
    """(what this program is, is it expected here)."""
    name = os.path.basename(cmd)
    if name in _CHAIN_INTRUDERS:
        return _CHAIN_INTRUDERS[name], False
    if name in _CHAIN_APPS:
        return _CHAIN_APPS[name], True
    return "not part of the DRC chain", False


_CHAIN_ACTIVITY_TTL = 2.5      # seconds; the card polls every CHAIN_INTERVAL
_CHAIN_ACTIVITY_AT = 0.0
_CHAIN_ACTIVITY: dict[str, bool | None] = {}


def _chain_activity() -> dict[str, bool | None]:
    """Is each source we understand actually producing audio right now?

    Holding a device and putting samples through it are different questions, and
    only the first one fstat can answer: brutefir opens the DAC when it starts
    and keeps it open all day, MPD keeps its output open while paused.  So the
    two programs that can be asked are asked — MPD over its own protocol, the CD
    bridge through the state line in its log — and everything else stays None,
    meaning "it has the device open and we cannot say more than that".

    Cached briefly: `mpc status` is a subprocess and a socket round-trip, and
    two cards polling at four seconds should not pay for it twice."""
    global _CHAIN_ACTIVITY_AT, _CHAIN_ACTIVITY
    now = time.time()
    if now - _CHAIN_ACTIVITY_AT < _CHAIN_ACTIVITY_TTL:
        return _CHAIN_ACTIVITY
    activity: dict[str, bool | None] = {}
    try:
        activity["mpd"] = _mpc_status(_resolve_mpd_port())["state"] == "playing"
    except Exception:
        activity["mpd"] = None
    if CDIN_ENABLED:
        try:
            activity["omdrc-cdin"] = _cdin_status()["state"] == "playing"
        except Exception:
            activity["omdrc-cdin"] = None
    _CHAIN_ACTIVITY, _CHAIN_ACTIVITY_AT = activity, now
    return activity


def _chain_app_activity(cmd: str, activity: dict) -> bool | None:
    name = os.path.basename(cmd)
    if name in ("mpd", "musicpd"):
        return activity.get("mpd")
    return activity.get(name)


def _chain_producing(node: dict) -> bool:
    """Is this source putting samples on the wire?

    True and False come from the program itself where it can be asked.  None
    means it has a device open and we have no way to ask — a squatter, an mpv,
    a second brutefir — and that is treated as producing: we cannot prove it
    silent, and a holder that turns out to be quiet is still the one to go and
    kill.  A program that is merely running, holding nothing, is not."""
    return bool(node["active"]) or (node["active"] is None and not node.get("idle"))


def _chain_app_node(pid: str, cmd: str, user: str, roles: dict[str, str],
                    activity: dict) -> dict:
    note, expected = _chain_app_note(cmd)
    return {
        "id": f"app:{pid}" if pid else f"app:{cmd}",
        "kind": "app",
        "title": os.path.basename(cmd),
        "sub": note,
        "pid": pid,
        "user": user,
        "roles": roles,          # role -> "r" / "w" / "rw" it holds it with
        "expected": expected,
        "running": True,
        "active": _chain_app_activity(cmd, activity),
    }


def _chain_phantom(name: str, activity: dict) -> dict:
    """A chain program that is running but holds nothing.

    MPD closes its output when playback stops and omdrc-cdin releases the
    bridge after a run of digital silence, so "no descriptor" is the NORMAL
    resting state for both.  Dropping them from the diagram would make a healthy
    idle box look like a broken one, so they are drawn, greyed, in the place
    they occupy when they wake up."""
    node = _chain_app_node("", name, "", {}, activity)
    node["idle"] = True
    return node


def _chain_status() -> dict:
    devices = _chain_resolve_devices()
    activity = _chain_activity()

    # Capture and omdrc-cdin are one optional lane in the drawing.  A configured
    # role is not evidence that the interface is attached, and an attached card
    # by itself does not mean the bridge is in use.  Keep both blocks out unless
    # the two facts that make the lane real are true at this instant.
    capture_dev = devices.get("capture")
    cdin_visible = bool(CDIN_ENABLED and capture_dev
                        and capture_dev["present"]
                        and _process_running(CDIN_PROCESS))

    paths = [d["path"] for d in devices.values() if d["present"]]
    holders, privileged = _device_holders(paths)
    for dev in devices.values():
        rows = sorted(holders.get(dev["path"], []), key=lambda h: int(h["pid"]))
        dev["holders"] = rows
        dev["readers"] = [h for h in rows if "r" in h["mode"]]
        dev["writers"] = [h for h in rows if "w" in h["mode"]]

    def held(role: str, direction: str) -> dict[str, dict]:
        dev = devices.get(role)
        if dev is None:
            return {}
        key = "readers" if direction == "r" else "writers"
        return {h["pid"]: h for h in dev[key]}

    capture_readers = held("capture", "r")
    bridge_writers  = held("bridge", "w")
    loop_readers    = held("loop", "r")
    dac_writers     = held("dac", "w")

    # Which lane a holder belongs in.  A process reading the loopback is a
    # filter (brutefir); everything else that has a hand on the chain is a
    # source, including a player writing the DAC straight (the bypass case) and
    # including anything we did not expect to find there at all.
    filter_pids = dict(loop_readers)
    source_pids: dict[str, dict] = {}
    for group in (capture_readers, bridge_writers, dac_writers):
        for pid, h in group.items():
            if pid not in filter_pids:
                source_pids.setdefault(pid, h)

    def roles_of(pid: str) -> dict[str, str]:
        out = {}
        for role, dev in devices.items():
            for h in dev["holders"]:
                if h["pid"] == pid:
                    out[role] = h["mode"]
        return out

    sources = [_chain_app_node(pid, h["cmd"], h["user"], roles_of(pid), activity)
               for pid, h in sorted(source_pids.items(), key=lambda kv: int(kv[0]))]
    filters = [_chain_app_node(pid, h["cmd"], h["user"], roles_of(pid), activity)
               for pid, h in sorted(filter_pids.items(), key=lambda kv: int(kv[0]))]

    # A stale descriptor row during process teardown must not keep the optional
    # CD lane on screen after pgrep says the daemon is gone.  The raw descriptor
    # remains available in the expanded device list for diagnosis.
    if not cdin_visible:
        cdin_name = os.path.basename(CDIN_PROCESS)
        sources = [n for n in sources if n["title"] != cdin_name]

    # The resting state of a healthy box: running, holding nothing.
    named = {n["title"] for n in sources + filters}
    for name in ("musicpd", "mpd"):
        if name not in named and _process_running(name):
            sources.append(_chain_phantom(name, activity))
            break
    if cdin_visible and os.path.basename(CDIN_PROCESS) not in named:
        sources.append(_chain_phantom(CDIN_PROCESS, activity))
    if "brutefir" not in named and _process_running("brutefir"):
        filters.append(_chain_phantom("brutefir", activity))

    # A renderer feeding MPD over its own protocol holds no audio device, so it
    # can only come from the service state — but it is the block the listener
    # actually recognises ("qobuzconnect2mpd -> mpd -> brutefir"), so it is
    # drawn whenever there is an MPD for it to be feeding.
    feeders = []
    mpd_node = next((n for n in sources if n["title"] in ("mpd", "musicpd")), None)
    if mpd_node is not None:
        for service in SWITCHABLE_SERVICES:
            # The temporary `-L` OAuth receiver has the same argv[0] as the
            # real daemon; it is not a renderer and must not be drawn as one.
            if service == QCONNECT_SERVICE and _qconnect_oauth_active():
                continue
            if _service_running(service):
                note, _ = _chain_app_note(service)
                feeders.append({
                    "id": f"app:{service}", "kind": "app", "title": service,
                    "sub": note, "pid": "", "user": "", "roles": {},
                    "expected": True, "running": True,
                    "active": mpd_node["active"], "feeder": True,
                })

    # Anything downstream is carrying audio exactly when some source is.  An
    # unexpected holder counts as producing: we cannot ask it, and a squatter
    # that turns out to be silent is still the thing to go and kill.
    flowing = any(_chain_producing(n) for n in sources)

    bridge_used = bool(bridge_writers or loop_readers or filters)
    bridge_dev = devices.get("bridge")
    loop_dev = devices.get("loop")
    bridge_node = None
    if (bridge_dev or loop_dev) and (bridge_used or
                                     (bridge_dev and bridge_dev["present"])):
        present = bool((bridge_dev and bridge_dev["present"]) or
                       (loop_dev and loop_dev["present"]))
        bridge_node = {
            "id": "bridge", "kind": "bridge",
            "title": _chain_bridge_name(),
            "sub": " → ".join(d["path"] for d in (bridge_dev, loop_dev) if d),
            "present": present,
            "running": _process_running(_chain_bridge_name()) if not _IS_LINUX else present,
            "active": flowing,
        }

    def device_state(dev: dict, direction: str, traffic: bool) -> str:
        if not dev["present"]:
            return "absent"
        key = "readers" if direction == "r" else "writers"
        holders_here = dev[key]
        if holders_here and traffic:
            return "active"
        return "held" if holders_here else "free"

    def dev_node(role: str, colour: str) -> dict | None:
        dev = devices.get(role)
        if dev is None:
            return None
        if role == "capture":
            direction = "r"
            traffic = any(_chain_app_activity(h["cmd"], activity) is not False
                          for h in dev["readers"])
        else:
            direction = "w"
            traffic = flowing
        return {
            "id": f"dev:{role}", "kind": "device", "role": role,
            "title": (dev["label"] or (os.path.basename(dev["path"])
                                       if dev["present"] else "")
                      or _CHAIN_ROLE_TITLE.get(role, role)),
            "sub": dev["path"] or dev["spec"],
            "target": dev["target"], "present": dev["present"],
            "state": device_state(dev, direction, traffic), "colour": colour,
            "holders": dev["holders"],
        }

    capture_node = dev_node("capture", "red") if cdin_visible else None
    dac_node = dev_node("dac", "green")

    # dsp.play and dsp.loop are deliberately too small to deserve two more
    # blocks in the graph.  Expose their already-collected open/traffic state
    # as labelled ports on the virtual audio bridge: these LEDs describe the
    # two virtual devices, not BruteFIR itself.
    bridge_flowing = any(
        _chain_producing(n) and "w" in n["roles"].get("bridge", "")
        for n in sources)

    def port_led(role: str, direction: str, traffic: bool) -> dict | None:
        dev = devices.get(role)
        if dev is None:
            return None
        return {
            "role": role,
            "label": os.path.basename(dev["spec"] or dev["path"]),
            "sub": dev["path"] or dev["spec"],
            "present": dev["present"],
            "state": device_state(dev, direction, traffic),
            "colour": "green",
            "direction": direction,
            "holders": dev["holders"],
        }

    bridge_ports = [p for p in (
        port_led("bridge", "w", bridge_flowing),
        port_led("loop", "r", bridge_flowing),
    ) if p is not None]
    if bridge_node:
        bridge_node["ports"] = bridge_ports

    rows: list[list[dict]] = [
        [n for n in ([capture_node] if capture_node else []) + feeders],
        sources,
        [bridge_node] if bridge_node else [],
        filters,
        [dac_node] if dac_node else [],
    ]
    for index, row in enumerate([r for r in rows if r]):
        for node in row:
            node["row"] = index
    nodes = [n for row in rows for n in row]
    by_id = {n["id"]: n for n in nodes}

    edges = []

    def link(src: dict | None, dst: dict | None, label: str = "",
             active: bool | None = None, warn: str = "") -> None:
        if src is None or dst is None:
            return
        edges.append({"from": src["id"], "to": dst["id"], "label": label,
                      "active": bool(active), "warn": warn})

    for feeder in feeders:
        link(feeder, mpd_node, "mpd protocol", feeder["active"])
    for node in sources:
        if capture_node and "capture" in node["roles"]:
            link(capture_node, node, "", capture_node["state"] == "active")

    for node in sources:
        writes = node["roles"]
        drawn = False
        if bridge_node is not None and "w" in writes.get("bridge", ""):
            link(node, bridge_node, "", _chain_producing(node))
            drawn = True
        if "w" in writes.get("dac", ""):
            # A player on the DAC with no convolver in front of it is the
            # bypass path, and it is worth naming: it is a legitimate mode
            # (drc.sh off) and a symptom (brutefir died) with the same shape.
            link(node, dac_node, "direct — DRC bypassed", _chain_producing(node))
            drawn = True
        # Nothing drawn, and nothing invented.  An arc in this diagram means
        # one thing only: a descriptor is open, right now, between those two
        # blocks.  A program that is running and holding nothing has no arc —
        # it floats, labelled as holding nothing, which is exactly its state.
        # The alternative is guessing the route it WOULD take, and a guessed
        # line is indistinguishable from a real one to the eye reading it.
    for f in filters:
        if bridge_node is not None:
            link(bridge_node, f, "", flowing and "r" in f["roles"].get("loop", ""))
        link(f, dac_node, "", flowing and "w" in f["roles"].get("dac", ""))

    status = {
        "ok": True,
        "enabled": True,
        "interval": CHAIN_INTERVAL,
        "os": platform.system(),
        "privileged": privileged,
        "flowing": flowing,
        "nodes": nodes,
        "edges": edges,
        "devices": [devices[r] for r in _CHAIN_ROLES if r in devices],
        "input": capture_node,
        "output": dac_node,
    }
    status["problems"] = _chain_problems(status, bridge_node, filters, sources)
    status["summary"] = _chain_summary(status, sources, filters)
    return status


def _chain_problems(status: dict, bridge_node: dict | None,
                    filters: list[dict], sources: list[dict]) -> list[dict]:
    """The short list of things that are actually wrong, worst first.  Anything
    that is merely idle belongs in the diagram, not here."""
    problems = []
    for node in sources + filters:
        if not node["expected"]:
            problems.append({
                "severity": "error",
                "text": f"{node['title']}"
                        + (f" (pid {node['pid']})" if node["pid"] else "")
                        + f" — {node['sub']}",
            })
    for dev in status["devices"]:
        if dev["present"]:
            continue
        role = dev["role"]
        if role == "dac":
            problems.append({"severity": "error",
                             "text": f"no DAC at {dev['path'] or dev['spec']}"})
        elif role == "capture":
            problems.append({"severity": "info",
                             "text": f"capture interface not attached "
                                     f"({dev['path'] or dev['spec']})"})
        elif filters or bridge_node:
            problems.append({"severity": "warn",
                             "text": f"{dev['path'] or dev['spec']} is missing — "
                                     f"{_chain_bridge_name()} is not up"})
    # A DAC the service had to guess between two candidates is the failure that
    # is invisible everywhere else: the chain comes up, the DAC lights, and
    # every byte goes to the wrong card.  omdrc_audio knows it guessed; this
    # is the only place a listener would ever be told.
    roles = _audio_roles()
    if roles.get("dac_guessed") == "1":
        problems.append({
            "severity": "warn",
            "text": f"the DAC was guessed: pcm{roles.get('dac_unit', '?')} "
                    f"{roles.get('dac_desc', '')} — more than one playback card "
                    f"and no omdrc_audio_dac in rc.conf "
                    f"(see `service omdrc_audio status`)",
        })
    if roles.get("capture_wanted") and not roles.get("capture_unit"):
        problems.append({
            "severity": "warn",
            "text": f"no card matches omdrc_audio_capture="
                    f"\"{roles['capture_wanted']}\" — the capture link is missing",
        })

    if not status["privileged"]:
        problems.append({
            "severity": "warn",
            "text": "only this user's processes are listed — no sudo grant for "
                    "fstat/fuser, so a root process holding a device is invisible",
        })
    order = {"error": 0, "warn": 1, "info": 2}
    problems.sort(key=lambda p: order.get(p["severity"], 3))
    return problems


def _chain_summary(status: dict, sources: list[dict], filters: list[dict]) -> str:
    """The one line beside the LEDs.

    An arrow in this line means "feeds": it may only ever join stages that are
    really connected.  Two players sitting side by side are alternatives, not a
    pipeline, so they are never strung together with arrows — and when nothing
    is playing there is no path to draw at all, so the line says what the
    output device is doing instead of inventing one."""
    out = status["output"]
    if out is not None and not out["present"]:
        return "no DAC"
    if not status["flowing"]:
        if out is None:
            return "idle"
        held = out["holders"]
        if held:
            who = ", ".join(sorted({h["cmd"] for h in held}))
            return f"idle — {out['title']} held by {who}"
        return f"idle — {out['title']} free"
    live = [n["title"] for n in sources if _chain_producing(n)]
    head = " + ".join(live[:2]) + (f" +{len(live) - 2}" if len(live) > 2 else "")
    stages = [head] + [f["title"] for f in filters] + [out["title"] if out else "?"]
    return " → ".join(s for s in stages if s)


@app.route("/audio/chain")
def audio_chain():
    if not CHAIN_ENABLED:
        return jsonify({"ok": True, "enabled": False})
    try:
        return jsonify(_chain_status())
    except Exception as e:
        return jsonify({"ok": False, "enabled": True, "error": str(e)})


def _upmpdcli_conf_path() -> str | None:
    """The upmpdcli.conf the OAuth script must be pointed at: it reads the
    media-server host/port from it, and they have to be the ones the running
    upmpdcli is listening on."""
    if QOBUZ_UPMPDCLI_CONF:
        return QOBUZ_UPMPDCLI_CONF if os.path.isfile(QOBUZ_UPMPDCLI_CONF) else None
    prefix = os.environ.get("PREFIX", "/usr/local")
    for path in (os.path.join(prefix, "etc", "open-media-drc", "upmpdcli.conf"),
                 os.path.join(prefix, "etc", "upmpdcli.conf"),
                 "/etc/upmpdcli.conf"):
        if os.path.isfile(path):
            return path
    return None


def _upmpdcli_options(path: str) -> dict[str, str]:
    """upmpdcli.conf is flat `key = value` with # comments."""
    out = {}
    for line in _read_text_quietly(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _qobuz_cache_config() -> str:
    """Where the plugin stores the token it gets from the OAuth redirect."""
    if QOBUZ_CACHE_CONFIG:
        return QOBUZ_CACHE_CONFIG
    conf = _upmpdcli_conf_path()
    cachedir = _upmpdcli_options(conf).get("cachedir", "") if conf else ""
    if not cachedir:
        cachedir = os.path.join(_env()["HOME"], ".cache", "upmpdcli")
    return os.path.join(cachedir, "qobuz", "config")


def _qobuz_token_state() -> dict:
    """Whether the plugin currently holds a usable Qobuz token.  This is the
    authoritative answer — the log only says what happened at the last login."""
    path = _qobuz_cache_config()
    values = _upmpdcli_options(path)
    user_id = values.get("user_id", "")
    return {
        "cache_config": path,
        "token":        bool(values.get("user_auth_token") and user_id),
        "user_id":      user_id,
        "token_mtime":  _log_stat(path)["mtime"],
    }


def _oauth_redirect(url: str) -> tuple[str, int | None, str, tuple[int, int]]:
    """Return host, port and decoded redirect URL plus its value span.

    upmpdcli currently prints `redirect_url=http://...`, while
    qobuzconnect2mpd percent-encodes that value.  Treat both forms alike so the
    remote-browser address can be substituted without changing either daemon.
    """
    match = re.search(r"(?:[?&])redirect_url=([^&\s]+)", url)
    if not match:
        return "", None, "", (0, 0)
    redirect = unquote(match.group(1))
    try:
        parsed = urlsplit(redirect)
        return (parsed.hostname or "", parsed.port, redirect, match.span(1))
    except ValueError:
        return "", None, redirect, match.span(1)


def _rewrite_oauth_redirect(url: str, request_host: str) -> str:
    host, port, redirect, span = _oauth_redirect(url)
    if not host or not request_host or not redirect:
        return url
    parsed = urlsplit(redirect)
    shown_host = f"[{request_host}]" if ":" in request_host else request_host
    netloc = shown_host + (f":{port}" if port is not None else "")
    replacement = parsed._replace(netloc=netloc).geturl()
    old_value = url[span[0]:span[1]]
    # Preserve the style the producer used: upmpdcli leaves the URL readable;
    # qobuzconnect2mpd percent-encodes it.
    encoded = quote(replacement, safe="" if "%" in old_value else ":/")
    return url[:span[0]] + encoded + url[span[1]:]


def _oauth_candidates(output: str, request_host: str) -> list[dict]:
    """The sign-in URLs the script printed, plus — when the browser reached this
    panel on some other address — the same URL rewritten to that address.

    The redirect host must be reachable *from the browser*, and the script can
    only guess it from the box's default route.  The host this request arrived
    on is known-reachable, so it is offered first when it differs."""
    urls, seen = [], set()
    url_pattern = r"https://(?:www\.)?qobuz\.com/signin/oauth[^\s\x1b\"'<>]*"
    for url in re.findall(url_pattern, output):
        host, port, _, _ = _oauth_redirect(url)
        if host in seen:
            continue
        seen.add(host)
        local = host in ("localhost", "127.0.0.1", "::1")
        urls.append({
            "url":   url,
            "host":  host,
            "port":  port,
            "label": "browser running on this host" if local
                     else f"browser on another device ({host})",
            "local": local,
        })
    network = next((u for u in urls if not u["local"]), None)
    if network and request_host and request_host not in seen:
        rewritten = _rewrite_oauth_redirect(network["url"], request_host)
        urls.insert(0, {
            "url":   rewritten,
            "host":  request_host,
            "port":  network["port"],
            "label": f"the address you are using now ({request_host})",
            "local": False,
        })
    for url in urls:
        url["primary"] = False
    primary = next((u for u in urls if not u["local"]), urls[0] if urls else None)
    if primary:
        primary["primary"] = True
    return urls


def _browser_request_host() -> str:
    """Hostname (without the panel port) that this browser used."""
    try:
        return urlsplit(f"//{request.host}").hostname or ""
    except ValueError:
        return request.host.split(":", 1)[0]


# ── qobuzconnect2mpd OAuth bootstrap ────────────────────────────────────────

def _qconnect_oauth_binary_path() -> str | None:
    if os.path.isabs(QCONNECT_OAUTH_BINARY):
        return QCONNECT_OAUTH_BINARY if os.path.isfile(QCONNECT_OAUTH_BINARY) else None
    return shutil.which(QCONNECT_OAUTH_BINARY)


def _qconnect_oauth_config_path() -> str | None:
    if QCONNECT_OAUTH_CONFIG:
        return QCONNECT_OAUTH_CONFIG if os.path.isfile(QCONNECT_OAUTH_CONFIG) else None
    prefix = os.environ.get("PREFIX", "/usr/local")
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    for path in (os.path.join(config_home, "qobuzconnect2mpd", "qobuzconnect2mpd.conf"),
                 os.path.join(prefix, "etc", "qobuzconnect2mpd.conf"),
                 "/etc/qobuzconnect2mpd.conf"):
        if os.path.isfile(path):
            return path
    return None


def _qconnect_oauth_command(binary: str, config: str) -> list[str]:
    command = [binary, "-c", config, "-L"]
    try:
        current_user = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        current_user = ""
    if QCONNECT_OAUTH_USER and QCONNECT_OAUTH_USER != current_user:
        # -n makes a missing sudoers grant fail immediately instead of leaving a
        # password prompt attached to a web request that nobody can answer.
        command = ["sudo", "-n", "-u", QCONNECT_OAUTH_USER, *command]
    return command


def _qconnect_oauth_active() -> bool:
    with _QCONNECT_OAUTH_LOCK:
        return (_QCONNECT_OAUTH_SESSION.get("phase") == "starting" or
                (_QCONNECT_OAUTH_PROCESS is not None and
                 _QCONNECT_OAUTH_PROCESS.poll() is None))


def _qconnect_oauth_snapshot(request_host: str) -> dict:
    with _QCONNECT_OAUTH_LOCK:
        state = dict(_QCONNECT_OAUTH_SESSION)
        process = _QCONNECT_OAUTH_PROCESS
    urls = _oauth_candidates(state.get("output", ""), request_host)
    phase = state.get("phase", "idle")
    oauth_running = process is not None and process.poll() is None
    return {
        "ok": phase != "error",
        "phase": phase,
        "running": oauth_running,
        "connected": phase == "connected",
        "urls": urls,
        "error": state.get("error", ""),
        "output": state.get("output", ""),
        "returncode": state.get("returncode"),
        "started_at": state.get("started_at"),
        "binary": _qconnect_oauth_binary_path(),
        "config": _qconnect_oauth_config_path(),
        "run_user": QCONNECT_OAUTH_USER,
        "qobuzconnect2mpd_running": (
            not oauth_running and _service_running(QCONNECT_SERVICE)),
    }


def _qconnect_oauth_worker(process: subprocess.Popen) -> None:
    """Collect bootstrap output, then activate the renderer on OAuth success."""
    read_error = ""
    try:
        if process.stdout is not None:
            for line in process.stdout:
                with _QCONNECT_OAUTH_LOCK:
                    output = (_QCONNECT_OAUTH_SESSION.get("output", "") + line)
                    _QCONNECT_OAUTH_SESSION["output"] = output[-65_536:]
                    if _oauth_candidates(output, ""):
                        _QCONNECT_OAUTH_SESSION["phase"] = "waiting"
                    _QCONNECT_OAUTH_LOCK.notify_all()
    except (OSError, ValueError) as error:
        read_error = str(error)

    returncode = process.wait()
    with _QCONNECT_OAUTH_LOCK:
        output = _QCONNECT_OAUTH_SESSION.get("output", "")
        _QCONNECT_OAUTH_SESSION["returncode"] = returncode
        if returncode == 0:
            _QCONNECT_OAUTH_SESSION["phase"] = "activating"
        else:
            _QCONNECT_OAUTH_SESSION["phase"] = "error"
            detail = read_error or next(
                (line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
            if not _QCONNECT_OAUTH_SESSION.get("error"):
                _QCONNECT_OAUTH_SESSION["error"] = (
                    detail or f"qobuzconnect2mpd login exited with status {returncode}")
        _QCONNECT_OAUTH_LOCK.notify_all()

    if returncode != 0:
        return
    ok, error = _activate_renderer(QCONNECT_SERVICE)
    with _QCONNECT_OAUTH_LOCK:
        _QCONNECT_OAUTH_SESSION["phase"] = "connected" if ok else "error"
        _QCONNECT_OAUTH_SESSION["error"] = "" if ok else error
        _QCONNECT_OAUTH_LOCK.notify_all()


@app.route("/qconnect/oauth/status")
def qconnect_oauth_status():
    return jsonify(_qconnect_oauth_snapshot(_browser_request_host()))


@app.route("/qconnect/oauth/start", methods=["POST"])
def qconnect_oauth_start():
    """Run qobuzconnect2mpd's long-lived `-L` browser OAuth bootstrap.

    Unlike upmpdcli's URL-printing helper, this process is itself the callback
    receiver, so it stays in a background thread until the browser redirects
    back.  A successful exit means the token was persisted; the worker then
    switches to the normal renderer service automatically.
    """
    global _QCONNECT_OAUTH_PROCESS, _QCONNECT_OAUTH_SESSION

    binary = _qconnect_oauth_binary_path()
    if not binary:
        return jsonify({"ok": False, "phase": "error",
                        "error": f"OAuth program not found: {QCONNECT_OAUTH_BINARY}"})
    config = _qconnect_oauth_config_path()
    if not config:
        return jsonify({"ok": False, "phase": "error",
                        "error": "qobuzconnect2mpd.conf not found; set config in "
                                 "[qconnect_oauth]"})

    # Reserve the one callback receiver before service control/Popen. Flask may
    # serve two browsers concurrently, and both processes would bind the same
    # qconnectport if the check and spawn were not one atomic decision.
    with _QCONNECT_OAUTH_LOCK:
        if (_QCONNECT_OAUTH_SESSION.get("phase") == "starting" or
                (_QCONNECT_OAUTH_PROCESS is not None and
                 _QCONNECT_OAUTH_PROCESS.poll() is None)):
            already_running = True
        else:
            already_running = False
            _QCONNECT_OAUTH_PROCESS = None
            _QCONNECT_OAUTH_SESSION = {
                "phase": "starting", "output": "", "error": "",
                "returncode": None, "started_at": int(time.time()),
            }
    if already_running:
        return jsonify(_qconnect_oauth_snapshot(_browser_request_host()))

    try:
        # The normal daemon and -L callback receiver bind the same qconnectport.
        # Stop unconditionally: a failed authentication may be in a supervised
        # restart loop even when a point-in-time status check misses its child.
        _service_action(QCONNECT_SERVICE, "onestop")
        if _service_running(QCONNECT_SERVICE):
            raise OSError("could not stop qobuzconnect2mpd before sign-in")
        process = subprocess.Popen(
            _qconnect_oauth_command(binary, config),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=_env())
    except (OSError, subprocess.SubprocessError) as error:
        with _QCONNECT_OAUTH_LOCK:
            _QCONNECT_OAUTH_SESSION["phase"] = "error"
            _QCONNECT_OAUTH_SESSION["error"] = str(error)
            _QCONNECT_OAUTH_LOCK.notify_all()
        return jsonify(_qconnect_oauth_snapshot(_browser_request_host()))

    with _QCONNECT_OAUTH_LOCK:
        _QCONNECT_OAUTH_PROCESS = process
    threading.Thread(target=_qconnect_oauth_worker, args=(process,),
                     name="qconnect-oauth", daemon=True).start()

    # App-id discovery normally takes only a moment.  Wait here, just as the
    # upmpdcli endpoint waits for its helper, so the button can return a usable
    # URL directly; status polling still restores/finishes the long callback.
    deadline = time.monotonic() + QCONNECT_OAUTH_URL_TIMEOUT
    with _QCONNECT_OAUTH_LOCK:
        while (_QCONNECT_OAUTH_SESSION["phase"] == "starting" and
               time.monotonic() < deadline):
            remaining = max(0.0, deadline - time.monotonic())
            _QCONNECT_OAUTH_LOCK.wait(timeout=min(0.5, remaining))
        timed_out = _QCONNECT_OAUTH_SESSION["phase"] == "starting"
        if timed_out:
            _QCONNECT_OAUTH_SESSION["phase"] = "error"
            _QCONNECT_OAUTH_SESSION["error"] = (
                f"qobuzconnect2mpd produced no sign-in URL within "
                f"{QCONNECT_OAUTH_URL_TIMEOUT}s")
    if timed_out and process.poll() is None:
        process.terminate()
    return jsonify(_qconnect_oauth_snapshot(_browser_request_host()))


@app.route("/qobuz/oauth/status")
def qobuz_oauth_status():
    """Token state plus the two preconditions for the redirect to be caught."""
    return jsonify({
        "ok":               True,
        "upmpdcli_running": _service_running(UPMPDCLI_SERVICE),
        "script":           QOBUZ_OAUTH_SCRIPT,
        "script_present":   os.path.isfile(QOBUZ_OAUTH_SCRIPT),
        "upmpdcli_config":  _upmpdcli_conf_path(),
        **_qobuz_token_state(),
    })


@app.route("/qobuz/oauth/start", methods=["POST"])
def qobuz_oauth_start():
    """Run upmpdcli's qobuz-init-oauth.py and hand back the sign-in URLs.

    The script only prints them and exits, so there is nothing to keep alive
    here: the redirect is caught by upmpdcli itself, and the browser doing the
    signing in can be anywhere — which is the point of driving this from the
    panel on a headless box."""
    if not os.path.isfile(QOBUZ_OAUTH_SCRIPT):
        return jsonify({"ok": False,
                        "error": f"OAuth script not found: {QOBUZ_OAUTH_SCRIPT}"})
    conf = _upmpdcli_conf_path()
    if not conf:
        return jsonify({"ok": False, "error": "upmpdcli.conf not found; set "
                                              "upmpdcli_config in [qobuz_oauth]"})
    try:
        r = subprocess.run(["python3", QOBUZ_OAUTH_SCRIPT, "-c", conf],
                           capture_output=True, text=True,
                           timeout=QOBUZ_OAUTH_TIMEOUT, env=_env())
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": f"{os.path.basename(QOBUZ_OAUTH_SCRIPT)} "
                                              f"timed out after {QOBUZ_OAUTH_TIMEOUT}s"})
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)})

    output = (r.stdout or "") + (r.stderr or "")
    urls = _oauth_candidates(output, _browser_request_host())
    if not urls:
        return jsonify({"ok": False, "error": output.strip() or "no sign-in URL in output"})
    return jsonify({
        "ok":               True,
        "urls":             urls,
        "output":           output.strip(),
        "upmpdcli_running": _service_running(UPMPDCLI_SERVICE),
        **_qobuz_token_state(),
    })


@app.route("/renderer/restart", methods=["POST"])
def renderer_restart():
    """Restart one renderer in place, without switching to the other.  Used
    after an OAuth sign-in so upmpdcli picks the new token up."""
    target = (request.get_json(silent=True) or {}).get("target")
    if target not in SWITCHABLE_SERVICES:
        return jsonify({"ok": False, "error": "invalid target"}), 400
    try:
        _service_action(target, "onestop")
        if _service_running(target):
            return jsonify({"ok": False, "error": f"could not stop {target}"})
        r = _service_action(target, "onestart")
        if r.returncode != 0 and not _service_running(target):
            return jsonify({"ok": False,
                            "error": f"starting {target}: {(r.stderr or r.stdout).strip()}"})
        return jsonify({"ok": True, "running": _service_running(target)})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/mpd/info")
def mpd_info():
    try:
        # pgrep -x is reliable on both Linux and FreeBSD; avoids ps flag
        # incompatibilities. musicpd is the FreeBSD port binary name.
        pid = None
        for name in ("musicpd", "mpd"):
            r = subprocess.run(["pgrep", "-x", name],
                               capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                pids = r.stdout.strip().split()
                if pids:
                    pid = pids[0]
                    break

        running = pid is not None
        cpu_total = 0.0
        conf = None

        if running:
            r2 = subprocess.run(["ps", "-p", pid, "-o", "pcpu=,args="],
                                capture_output=True, text=True, timeout=3)
            for line in r2.stdout.splitlines():
                parts = line.split(None, 1)
                if not parts:
                    continue
                try:
                    cpu_total += float(parts[0])
                except ValueError:
                    pass
                if conf is None and len(parts) > 1:
                    conf = _mpd_conf_from_cmdline(parts[1].strip())

        # Fallback: probe common default config paths (Linux and FreeBSD)
        if not conf:
            for p in ("/usr/local/etc/musicpd.conf",
                      "/usr/local/etc/mpd.conf",
                      "/etc/mpd.conf",
                      os.path.expanduser("~/.config/mpd/mpd.conf"),
                      os.path.expanduser("~/.mpdconf")):
                if os.path.isfile(p):
                    conf = p
                    break

        port = _mpd_port_from_conf(conf) if conf else None
        mpc = _mpc_status(port)
        is_linux = platform.system() == "Linux"
        voss_rate = _virtual_oss_rate() if not is_linux else None
        alsa_hw   = _alsa_hw_params()   if is_linux     else None
        alsa_rate = alsa_hw["rate"] if alsa_hw else None
        bf_rate = _brutefir_rate()
        rate_status = _rate_status(mpc["sample_rate"], voss_rate, bf_rate)
        path_status = _path_status(rate_status, bf_rate is not None)
        return jsonify({
            "ok":      True,
            "running": running,
            "cpu":     round(cpu_total, 1),
            "conf":    conf  or "(unknown)",
            "port":    port  or "6600",
            "client":  mpc["client"] or "(not found)",
            "state":   mpc["state"],
            "song":    mpc["song"],
            "audio":   mpc["audio"],
            "sample_rate": mpc["sample_rate"],
            "bit_depth": mpc["bit_depth"],
            "channels": mpc["channels"],
            "mpc_error": mpc["error"],
            "is_linux": is_linux,
            "virtual_oss_rate": voss_rate,
            "alsa_rate": alsa_rate,
            "alsa": alsa_hw,
            "brutefir_rate": bf_rate,
            "rate_status": rate_status,
            "path_status": path_status,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _read_memory() -> dict:
    try:
        system = platform.system()
        if system == "Linux":
            info: dict[str, int] = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k.strip()] = int(v.strip().split()[0]) * 1024
            total     = info["MemTotal"]
            available = info["MemAvailable"]
            free      = info["MemFree"]
        elif system == "FreeBSD":
            r = subprocess.run(
                ["sysctl", "-n",
                 "hw.physmem",
                 "vm.stats.vm.v_page_size",
                 "vm.stats.vm.v_free_count",
                 "vm.stats.vm.v_inactive_count",
                 "vm.stats.vm.v_cache_count"],
                capture_output=True, text=True, timeout=5,
            )
            vals = [int(x) for x in r.stdout.split()]
            physmem, psize, v_free, v_inactive, v_cache = vals
            total     = physmem
            free      = v_free * psize
            available = (v_free + v_inactive + v_cache) * psize
        else:
            return {"ok": False, "error": f"unsupported platform: {system}"}
        used = total - available
        return {"ok": True, "total": total, "used": used, "free": free, "available": available}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/system/sndstat")
def system_sndstat():
    try:
        sys = platform.system()
        if sys == "FreeBSD":
            with open("/dev/sndstat", errors="replace") as f:
                raw = f.read()
            lines = []
            for line in raw.splitlines():
                lines.append(_decode_sndstat_fmt(line))
            return jsonify({"ok": True, "lines": lines})
        elif sys == "Linux":
            r = subprocess.run(["aplay", "-l"],
                               capture_output=True, text=True, timeout=5)
            return jsonify({"ok": True, "lines": r.stdout.splitlines()})
        else:
            return jsonify({"ok": False, "error": f"unsupported platform: {sys}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/system/advanced")
def system_advanced():
    if platform.system() != "FreeBSD":
        return jsonify({"ok": False, "error": "FreeBSD only"})

    sections = []
    for cmd in (["sysctl", f"dev.pcm.{_dac_unit()}"], ["sysctl", "hw.usb.uaudio"]):
        try:
            r = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=5, env=_env(),
            )
            output = (r.stdout + r.stderr).strip()
            sections.append({
                "title": " ".join(cmd),
                "ok": r.returncode == 0,
                "output": output or f"exit {r.returncode}",
            })
        except subprocess.TimeoutExpired:
            sections.append({
                "title": " ".join(cmd),
                "ok": False,
                "output": "timeout",
            })

    return jsonify({"ok": True, "sections": sections})


@app.route("/system/memory")
def system_memory():
    return jsonify(_read_memory())


@app.route("/system/topcpu")
def system_topcpu():
    global _TOPCPU_CACHE, _TOPCPU_CACHE_AT
    now = time.monotonic()
    if _TOPCPU_CACHE is not None and now - _TOPCPU_CACHE_AT < TOPCPU_INTERVAL:
        return jsonify(_TOPCPU_CACHE)

    try:
        procs = []
        for row in _ps_processes():
            if _hide_from_topcpu(row):
                continue
            if row["cpu"] >= TOPCPU_THRESHOLD:
                procs.append(row)
        procs.sort(key=lambda p: p["cpu"], reverse=True)
        _TOPCPU_CACHE = {
            "ok": True,
            "procs": procs,
            "threshold": TOPCPU_THRESHOLD,
            "interval": TOPCPU_INTERVAL,
        }
        _TOPCPU_CACHE_AT = now
        return jsonify(_TOPCPU_CACHE)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/spectrum/settings")
def spectrum_settings():
    seq, frame = _SPECTRUM.snapshot()
    return jsonify({
        "ok": True,
        "seq": seq,
        "frame": frame,
        **_SPECTRUM.settings(),
    })


@app.route("/spectrum/drc-delay", methods=["POST"])
def spectrum_drc_delay():
    """Set (and remember) the DRC-sync slider delta in milliseconds."""
    global SPECTRUM_DRC_DELAY_DELTA_MS
    raw = request.form.get("delta_ms", request.args.get("delta_ms"))
    if raw is None and request.is_json:
        raw = (request.get_json(silent=True) or {}).get("delta_ms")
    try:
        delta = _clamp_delta(float(raw))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "delta_ms must be a number"}), 400
    SPECTRUM_DRC_DELAY_DELTA_MS = delta
    _write_state_float(_DELTA_STATE_FILE, delta)
    base_ms = round(_drc_display_delay_seconds() * 1000.0, 1)
    return jsonify({
        "ok": True,
        "drc_delay_delta_ms": round(delta, 1),
        "drc_delay_base_ms": base_ms,
        "drc_delay_total_ms": round(max(0.0, base_ms + delta), 1),
        "drc_delay_delta_min_ms": round(SPECTRUM_DRC_DELAY_DELTA_MIN_MS, 1),
        "drc_delay_delta_max_ms": round(SPECTRUM_DRC_DELAY_DELTA_MAX_MS, 1),
    })


@app.route("/spectrum/floor", methods=["POST"])
def spectrum_floor():
    """Set (and remember) the analyzer floor in dBFS."""
    global SPECTRUM_FLOOR_DB
    raw = request.form.get("floor_db", request.args.get("floor_db"))
    if raw is None and request.is_json:
        raw = (request.get_json(silent=True) or {}).get("floor_db")
    try:
        floor = max(-90.0, min(-24.0, float(raw)))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "floor_db must be a number"}), 400
    SPECTRUM_FLOOR_DB = floor
    _write_state_float(_FLOOR_STATE_FILE, floor)
    return jsonify({"ok": True, "floor_db": round(floor, 1)})


@app.route("/spectrum/stream")
def spectrum_stream():
    if not SPECTRUM_ENABLED:
        return jsonify({"ok": False, "error": "spectrum analyzer disabled"}), 404
    mode = "precision" if request.args.get("mode") == "precision" else "music"

    def events():
        _SPECTRUM.acquire(mode)
        try:
            seq, frame = _SPECTRUM.snapshot()
            yield f"data: {json.dumps(frame, separators=(',', ':'))}\n\n"
            while True:
                seq, frame = _SPECTRUM.wait_next(seq)
                yield f"data: {json.dumps(frame, separators=(',', ':'))}\n\n"
                if _SPECTRUM.stop_event.is_set():
                    break
        except GeneratorExit:
            pass
        finally:
            _SPECTRUM.release()

    return Response(events(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _drc_script() -> str | None:
    """The drc.sh entry point, derived from the configured drc_status command —
    its sibling in every supported layout:

        run-from-repo  <repo>/drc-status.sh        -> <repo>/drc.sh
        installed      ${PREFIX}/bin/omdrc-status  -> ${PREFIX}/bin/omdrc

    In an installed tree drc.sh itself lives in libexec, not beside the status
    command, so the plain `<dir>/drc.sh` guess resolves to a file that does not
    exist; the PATH wrapper next to omdrc-status is the right target.  Each
    candidate is checked for existence, and `omdrc` on PATH is the last resort,
    so a wrong guess surfaces as "not configured" instead of a command that
    silently fails to run.  Returns None when nothing runnable is found."""
    cmd = CMD_MAP.get("drc_status")
    if cmd:
        try:
            argv0 = shlex.split(cmd["cmd"])[0]
        except (ValueError, IndexError):
            argv0 = cmd["cmd"].strip()
        directory, base = os.path.split(argv0)
        candidates = [os.path.join(directory, "drc.sh")]
        if base.endswith("-status"):
            candidates.append(os.path.join(directory, base[:-len("-status")]))
        for path in candidates:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return shutil.which("omdrc")


@app.route("/drc/status")
def drc_status_api():
    script = _drc_script()
    if not script:
        return jsonify({"ok": False, "error": "drc_status not configured"})
    try:
        r = subprocess.run(
            [script, "status"],
            capture_output=True, text=True, timeout=10, env=_env(),
        )
        rows = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or ':' not in line:
                continue
            k, _, v = line.partition(':')
            rows.append({"key": k.strip(), "value": v.strip()})
        return jsonify({"ok": True, "rows": rows})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _drc_geometries(script: str) -> list[str]:
    """Filter sets installed under configs/, as reported by drc.sh."""
    r = subprocess.run(
        [script, "geometry", "--list"],
        capture_output=True, text=True, timeout=5, env=_env(),
    )
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


# Switching filter set restarts the whole chain (virtual_oss, brutefir, the MPD
# output) and includes the DAC warm-up and its verify retries, so it can take
# tens of seconds.  Wait it out rather than returning early: the outcome — which
# set and which rate actually came up — is the whole point of the request.
_GEOMETRY_SWITCH_TIMEOUT = 120


def _run_drc_switch(script: str, arguments: list[str], action: str):
    """Run a chain-rebuilding drc.sh command without daemon pipe hangs."""
    log = tempfile.NamedTemporaryFile(
        mode="w+b", prefix=f"omdrcctrl-{action}-", suffix=".log", delete=False)
    log_path = log.name
    try:
        proc = subprocess.Popen(
            [script, *arguments], env=_env(), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    except Exception as error:
        log.close()
        _unlink_quietly(log_path)
        return {"ok": False, "error": str(error)}
    log.close()
    try:
        rc = proc.wait(timeout=_GEOMETRY_SWITCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        threading.Thread(target=_wait_and_cleanup, args=(proc, log_path), daemon=True).start()
        return {"ok": False, "error": f"timeout switching {action}"}
    output = _read_text_quietly(log_path).strip()
    _unlink_quietly(log_path)
    if rc != 0:
        return {"ok": False, "error": output or f"exit {rc}", "output": output}
    return {"ok": True, "output": output}


@app.route("/drc/geometry", methods=["GET", "POST"])
def drc_geometry():
    cmd = CMD_MAP.get("drc_status")
    script = _drc_script()
    if not cmd or not script:
        return jsonify({"ok": False, "error": "drc_status not configured"})

    if request.method == "POST":
        want = (request.get_json(silent=True) or {}).get("geometry", "")
        want = str(want).strip()
        try:
            available = _drc_geometries(script)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})
        # Only ever pass back a name drc.sh itself listed: the request body must
        # not be able to turn into an argument of its own.
        if want not in available:
            return jsonify({"ok": False, "error": f"unknown filter set: {want}"})
        result = _run_drc_switch(script, ["geometry", want], "filter set")
        if result["ok"]:
            result["geometry"] = want
        return jsonify(result)

    try:
        r = subprocess.run(
            cmd["cmd"] + " --geometry",
            shell=True, env=_env(),
            capture_output=True, text=True, timeout=5,
        )
        geo = r.stdout.strip()
        if r.returncode != 0 or not geo:
            return jsonify({"ok": False, "error": r.stderr.strip() or "empty"})
        try:
            available = _drc_geometries(script)
        except Exception:
            available = []
        # The active set always appears in the list, even if it somehow is not
        # on disk any more — the UI must be able to show what is running.
        if geo not in available:
            available.append(geo)
        return jsonify({"ok": True, "geometry": geo, "available": available})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _drc_designs(script: str) -> list[str]:
    result = subprocess.run(
        [script, "design", "--list"], capture_output=True, text=True,
        timeout=5, env=_env(),
    )
    if result.returncode:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _drc_saved_session(script: str) -> dict:
    """Read the one authoritative restore session maintained by drc.sh."""
    result = subprocess.run(
        [script, "session"], capture_output=True, text=True,
        timeout=5, env=_env(),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "cannot read saved DRC session")
    session: dict[str, object] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"geometry", "power", "mode", "rate", "design", "label"}:
            session[key] = value.strip()
    missing = {"geometry", "power", "mode", "rate", "design"} - session.keys()
    if missing:
        raise RuntimeError(f"incomplete saved DRC session: {', '.join(sorted(missing))}")
    try:
        session["rate"] = int(str(session["rate"]))
    except ValueError as error:
        raise RuntimeError("invalid rate in saved DRC session") from error
    session["auto_saved"] = True
    return session


def _active_design_identity() -> dict:
    """Describe and provenance-check the config actually loaded by BruteFIR."""
    conf_path = _active_brutefir_conf()
    if not conf_path:
        return {
            "running": False,
            "verification": {"status": "stopped", "message": "BruteFIR is not running"},
        }
    rate_from_name, selector = _design_selector_from_conf(conf_path)
    identity: dict[str, object] = {
        "running": True,
        "geometry": os.path.basename(os.path.dirname(conf_path)),
        "rate": rate_from_name,
        "design": selector,
        "config": os.path.basename(conf_path),
    }
    try:
        parsed = _parse_brutefir_conf(conf_path)
        if parsed.get("rate"):
            identity["rate"] = parsed["rate"]
        bundle, verification = _verified_filter_bundle(parsed)
        identity["verification"] = verification
        if bundle:
            manifest = bundle["manifest"]
            identity.update({
                "bundle_id": manifest["bundle_id"],
                "design_id": manifest.get("design_id", manifest["variant"]),
                "description": manifest.get(
                    "description", manifest.get("design_id", manifest["variant"])),
                "source_directory": manifest["source"]["directory"],
                "source_project": manifest["source"].get("project", {}),
                "session": manifest["source"].get("measurements", {}),
            })
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        identity["verification"] = {"status": "mismatch", "message": str(error)}
    return identity


def _session_matches_active(session: dict, active: dict) -> bool:
    if session.get("power") == "off":
        return not active.get("running")
    return bool(
        active.get("running") and
        active.get("geometry") == session.get("geometry") and
        active.get("rate") == session.get("rate") and
        active.get("design") == session.get("design")
    )


@app.route("/drc/design", methods=["GET", "POST"])
def drc_design():
    """List or switch immutable filter designs within the active geometry."""
    script = _drc_script()
    if not script:
        return jsonify({"ok": False, "error": "drc_status not configured"})
    try:
        available = _drc_designs(script)
        if request.method == "POST":
            want = str((request.get_json(silent=True) or {}).get("design", "")).strip()
            if want not in available:
                return jsonify({"ok": False, "error": f"unknown filter design: {want}"})
            before_session = _drc_saved_session(script)
            before = _active_design_identity()
            result = _run_drc_switch(script, ["design", want], "filter design")
            after_session = _drc_saved_session(script)
            after = _active_design_identity()
            result.update({
                "requested": want,
                "transition": {
                    "from": before.get("design", before_session.get("design")),
                    "to": want,
                },
                "active": after,
                "session": after_session,
            })
            if not result["ok"]:
                return jsonify(result)
            if after_session.get("power") == "off":
                if after_session.get("design") != want:
                    result.update(ok=False, error="requested design was not saved for restore")
                else:
                    result.update(design=want, state="saved")
                return jsonify(result)
            if not after.get("running") or after.get("design") != want:
                result.update(
                    ok=False,
                    error=(f"switch command completed, but active config is "
                           f"{after.get('design', 'not running')} instead of {want}"),
                )
                return jsonify(result)
            verification = after.get("verification", {})
            if want.startswith("@") and verification.get("status") != "verified":
                result.update(
                    ok=False,
                    error=("design is active, but its config/filter provenance could not be verified: "
                           f"{verification.get('message', 'unknown mismatch')}"),
                )
                return jsonify(result)
            result.update(
                design=want,
                state="active",
                assurance=verification.get("status", "unknown"),
            )
            return jsonify(result)

        session = _drc_saved_session(script)
        active = _active_design_identity()
        current = str(active.get("design") if active.get("running") else session["design"])
        if current not in available:
            available.append(current)
        session["matches_active"] = _session_matches_active(session, active)
        return jsonify({
            "ok": True,
            "design": current,
            "available": available,
            "active": active,
            "session": session,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)})


@app.route("/drc/session", methods=["GET", "POST"])
def drc_session():
    """Show or restore the persistent DRC session used at boot/hotplug."""
    script = _drc_script()
    if not script:
        return jsonify({"ok": False, "error": "drc_status not configured"})
    try:
        if request.method == "GET":
            session = _drc_saved_session(script)
            active = _active_design_identity()
            session["matches_active"] = _session_matches_active(session, active)
            return jsonify({"ok": True, "session": session, "active": active})
        action = str((request.get_json(silent=True) or {}).get("action", "")).strip()
        if action != "restore":
            return jsonify({"ok": False, "error": "unknown session action"}), 400
        before = _active_design_identity()
        result = _run_drc_switch(script, ["restore"], "saved session")
        session = _drc_saved_session(script)
        active = _active_design_identity()
        matches = _session_matches_active(session, active)
        session["matches_active"] = matches
        assurance = (
            "off" if session.get("power") == "off" else
            active.get("verification", {}).get("status", "unknown")
        )
        result.update({
            "session": session, "active": active, "before": before,
            "assurance": assurance,
        })
        if result["ok"] and not matches:
            result.update(ok=False, error="restore completed, but the active chain does not match the saved session")
        if (result["ok"] and session.get("power") == "on" and
                str(session.get("design", "")).startswith("@") and
                active.get("verification", {}).get("status") != "verified"):
            result.update(
                ok=False,
                error="saved design is active, but its config/filter provenance is not verified",
            )
        return jsonify(result)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)})


@app.route("/filter-response")
def filter_response_page():
    return render_template("filter_response.html")


@app.route("/brutefir-config")
def brutefir_config_page():
    return render_template("brutefir_config.html")


@app.route("/drc/brutefir-config")
def drc_brutefir_config():
    """Live BruteFIR command, config identity and RAW-filter headroom."""
    try:
        return jsonify(_active_brutefir_configuration())
    except FileNotFoundError as error:
        return jsonify({
            "ok": False, "running": True,
            "error": f"active BruteFIR configuration not found: {error}",
        })
    except ImportError:
        return jsonify({
            "ok": False, "running": True,
            "error": "numpy is required for live filter headroom analysis",
        })
    except Exception as error:
        return jsonify({"ok": False, "running": True, "error": str(error)})


@app.route("/drc/filter-response")
def drc_filter_response():
    """Verified room and filter curves for the *running* BruteFIR.

    Every curve is one REW text export stored in the bundle; this endpoint
    releases them only when a manifest matches the SHA-256 of both exact
    coefficient files named by the active .conf.  Nothing is calculated here:
    unverified coefficients get no graph at all, because an FFT of the live
    bytes would be a different curve than the one REW drew.
    """
    conf_path = _active_brutefir_conf()
    if not conf_path:
        return jsonify({
            "ok": False, "running": False,
            "error": "BruteFIR is not running — no active filter loaded.",
        })
    try:
        parsed = _parse_brutefir_conf(conf_path)
        rate = parsed["rate"]
        if not rate or not parsed["coeffs"]:
            return jsonify({"ok": False, "running": True,
                            "error": f"no coeff/sampling_rate in {conf_path}"})
        # geometry = the configs/<geometry>/ directory name, when present.
        geometry = os.path.basename(os.path.dirname(conf_path))
        bundle, verification = _verified_filter_bundle(parsed)
        channels = [{
            "name": _coeff_channel(c),
            "taps": _fir_taps(c["filename"], c["format"]),
            "attenuation": c["attenuation"],
            "format": c["format"],
            "file": os.path.basename(c["filename"]),
            "sha256": _sha256_file(c["filename"]),
        } for c in parsed["coeffs"]]
        result = {
            "ok": True, "running": True,
            "geometry": geometry,
            "rate": rate,
            "conf": os.path.basename(conf_path),
            "channels": channels,
            "verification": verification,
        }
        if bundle:
            manifest = bundle["manifest"]
            analysis = bundle["analysis"]
            result.update({
                "frequency_grids": analysis["frequency_grids"],
                "traces": analysis["traces"],
                "details": {
                    "bundle_id": manifest["bundle_id"],
                    "variant": manifest["variant"],
                    "design_id": manifest.get("design_id", manifest["variant"]),
                    "description": manifest.get(
                        "description", manifest.get("design_id", manifest["variant"])),
                    "manifest": bundle["manifest_file"],
                    "audited_at": manifest["verification"]["audited_at"],
                    "claims": manifest["verification"]["claims"],
                    "prediction": manifest["verification"]["prediction"],
                    "source_directory": manifest["source"]["directory"],
                    "project": manifest["source"].get("project", {}),
                    "session": manifest["source"].get("measurements", {}),
                    "aggregate": manifest["aggregate"],
                    "artifacts": manifest["source"]["artifacts"],
                    "measurements": analysis["source_headers"],
                    "validation": analysis["validation"],
                    "calculation": analysis["calculation"],
                    "runtime": manifest["runtime"]["rates"][str(rate)],
                },
            })
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({"ok": False, "running": True, "error": f"filter file not found: {e}"})
    except Exception as e:
        return jsonify({"ok": False, "running": True, "error": str(e)})


def _brutefir_procs() -> tuple[list[dict], float]:
    """Find brutefir processes and their CPU% by argv[0] basename.

    brutefir renames its main process `comm` to an internal thread name (on
    Linux this shows up as e.g. "input"), so matching the `comm` column — as
    the generic `_ps_processes()` does — misses it entirely.  Matching the real
    binary name from the full argument list fixes Linux and is also correct on
    FreeBSD, where `comm` already reads "brutefir".  Checking argv[0] (not the
    whole line) avoids false positives from editors or greps that merely
    reference a brutefir config path.
    """
    candidates = (
        ["ps", "axo", "pid,pcpu,args"],
        ["ps", "ax", "-o", "pid=", "-o", "pcpu=", "-o", "args="],
    )
    for cmd in candidates:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            continue
        procs, total = [], 0.0
        for line in r.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid, cpu_s, args = parts
            try:
                cpu = float(cpu_s)
            except ValueError:
                continue   # header row or malformed line
            try:
                argv = shlex.split(args)
            except ValueError:
                argv = args.split()
            if not argv:
                continue
            binname = os.path.basename(argv[0])
            if binname == "sudo" and len(argv) > 1:
                binname = os.path.basename(argv[1])
            if binname != "brutefir":
                continue
            procs.append({"pid": pid, "cpu": cpu})
            total += cpu
        return procs, round(total, 1)
    return [], 0.0


@app.route("/brutefir/cpu")
def brutefir_cpu():
    try:
        procs, total = _brutefir_procs()
        return jsonify({"ok": True, "procs": procs, "total": total})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/status")
def status():
    units = {}
    for c in COMMANDS:
        if "details" not in c:
            continue
        if "process" in c:
            units[c["id"]] = "active" if _process_running(c["process"]) else "inactive"
        elif "unit" in c:
            units[c["id"]] = "active" if _unit_active(c["unit"]) else "inactive"
    return jsonify({"ok": True, "units": units})


def _resolve_config_path() -> str:
    """Default commands.conf location (see doc/FREEBSD-PORT-PLAN.md 1.3):

        $OMDRCCTRL_CONF                              explicit override
        ${PREFIX}/etc/open-media-drc/commands.conf   packaged install
        <app dir>/commands.conf                      run-from-repo / CMake

    An explicit --config always wins over all of these, so the run-from-repo
    launcher (which passes --config) is unaffected.  Kept distinct from drc.sh's
    $OMDRC_CONF, which names a different file (omdrc.conf)."""
    env_conf = os.environ.get("OMDRCCTRL_CONF")
    if env_conf:
        return env_conf
    packaged = os.path.join(os.environ.get("PREFIX", "/usr/local"),
                            "etc", "open-media-drc", "commands.conf")
    if os.path.isfile(packaged):
        return packaged
    return os.path.join(_HERE, "commands.conf")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OMDRC Control web interface")
    parser.add_argument("--host",   default="0.0.0.0")
    parser.add_argument("--port",   type=int, default=9090)
    parser.add_argument("--config", default=_resolve_config_path())
    args = parser.parse_args()
    load_config(args.config)
    # Make sure the spectrum FIFO output is off until Start is pressed, even if
    # a previous run was killed mid-stream.
    _SPECTRUM.ensure_disabled()
    app.run(host=args.host, port=args.port, threaded=True)
