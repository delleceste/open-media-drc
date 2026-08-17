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
GROUP_LABELS = {
    "drc":    "Digital Room Correction",
    "apps":   "Applications",
    "system": "System",
}

COMMANDS: list[dict] = []
CMD_MAP:  dict[str, dict] = {}


def load_config(path: str) -> None:
    global COMMANDS, CMD_MAP, QCONNECT_STATUS_FILE, QCONNECT_LOG_FILE
    global TOPCPU_THRESHOLD, MONITOR_INTERVAL, TOPCPU_INTERVAL
    global SNDSTAT_INTERVAL, BRUTEFIR_INTERVAL
    global SPECTRUM_ENABLED, SPECTRUM_OUTPUT_NAME, SPECTRUM_FIFO
    global SPECTRUM_RATE, SPECTRUM_BITS, SPECTRUM_CHANNELS
    global SPECTRUM_REFRESH_HZ, SPECTRUM_FFT_SIZE, SPECTRUM_PRECISION_FFT_SIZE, SPECTRUM_BANDS
    global SPECTRUM_VU_MODE, SPECTRUM_FLOOR_DB, SPECTRUM_MIN_FREQ
    global SPECTRUM_DRC_DELAY_TRIM_MS, SPECTRUM_DRC_DELAY_DELTA_MS
    global SPECTRUM_DRC_DELAY_DELTA_MIN_MS, SPECTRUM_DRC_DELAY_DELTA_MAX_MS
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

    _RESERVED = {"qconnect", "monitor", "spectrum"}
    COMMANDS = []
    for sid in cfg.sections():
        if sid in _RESERVED:
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
    identity = {
        "schema": manifest["schema"],
        "geometry": manifest["geometry"],
        "variant": manifest["variant"],
        "design_id": manifest.get("design_id", manifest["variant"]),
        "source_repository": manifest["source"]["repository"],
        "source_commit": manifest["source"]["repository_head"],
        "source_release": manifest["source"].get("release", {}),
        "source_provenance_sha256": _canonical_hash(manifest["source"]),
        "project_sha256": manifest["source"].get("project", {}).get("sha256", ""),
        "source_declaration_sha256": manifest["source"].get("declaration", {}).get("sha256", ""),
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
    if "description" in manifest:
        identity["description"] = manifest["description"]
    return identity


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
                if not manifest.get("bundle_id") or manifest.get("schema") != 1:
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
    attenuation = float(manifest["runtime"]["rates"][str(rate)]["attenuation_db"])
    traces = []
    for stored in analysis["traces"]:
        item = dict(stored)
        if item.get("group") in ("Filter", "Predicted"):
            item["magnitude_db"] = [round(float(value) - attenuation, 3)
                                    for value in item["magnitude_db"]]
        traces.append(item)
    analysis = dict(analysis)
    analysis["traces"] = traces
    release = manifest["source"].get("release", {})
    if release.get("kind") == "annotated_tag":
        anchor = (f"annotated source tag {release['name']} "
                  f"(tag object {release['tag_object']}) -> commit {release['commit']}")
    else:
        anchor = f"source commit {manifest['source']['repository_head']}"
    return {
        "manifest": manifest,
        "analysis": analysis,
        "manifest_file": os.path.basename(manifest_path),
        "active": active,
    }, {
        "status": manifest["verification"]["status"],
        "message": (
            "Active L/R bytes, config and graph dependencies match the manifest; " + anchor),
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


def _fir_response(filename: str, fmt: str, rate: int,
                  npoints: int = 700, fmin: float = 10.0,
                  fmax: float = 20_000.0) -> dict:
    """FFT of a raw FIR impulse response -> magnitude/phase/group-delay.

    Correction FIRs include a large bulk delay because the impulse is placed
    well inside the coefficient window.  Plotting wrapped raw FFT phase makes
    that delay dominate the graph, so estimate it from passband group delay and
    show the delay-compensated correction phase instead.
    """
    import numpy as np
    dtype = _RAW_DTYPES.get(fmt.upper(), "<f8")
    ir = np.fromfile(filename, dtype=dtype)
    if ir.size == 0:
        raise ValueError(f"empty or unreadable filter: {filename}")
    if np.issubdtype(np.dtype(dtype), np.integer):
        ir = ir.astype(np.float64) / np.iinfo(np.dtype(dtype)).max
    else:
        ir = ir.astype(np.float64)

    n = ir.size
    spec  = np.fft.rfft(ir)
    freqs = np.fft.rfftfreq(n, d=1.0 / rate)
    omega = 2.0 * np.pi * freqs
    mag_abs = np.abs(spec)
    mag = 20.0 * np.log10(mag_abs + 1e-12)
    angle = np.angle(spec)

    # Group delay (seconds) = -d(phase)/d(omega), using unwrapped raw phase.
    raw_unwrapped = np.unwrap(angle)
    gd = np.zeros_like(raw_unwrapped)
    if n > 2:
        gd[1:] = -np.gradient(raw_unwrapped, omega)[1:]

    fmax = min(rate / 2.0, fmax)
    delay_band = (
        (freqs >= max(fmin, 20.0)) &
        (freqs <= fmax) &
        (mag_abs > (mag_abs.max() * 1e-6)) &
        np.isfinite(gd)
    )
    if np.count_nonzero(delay_band) >= 3:
        bulk_delay = float(np.median(gd[delay_band]))
    else:
        bulk_delay = float(np.argmax(np.abs(ir)) / rate)

    compensated = spec * np.exp(1j * omega * bulk_delay)
    phase = np.angle(compensated)
    gd_ms = (gd - bulk_delay) * 1000.0

    lo = max(1, int(np.searchsorted(freqs, fmin)))
    hi = min(len(freqs) - 1, int(np.searchsorted(freqs, fmax)))
    if hi <= lo:
        hi = len(freqs) - 1
    targets = np.logspace(np.log10(freqs[lo]), np.log10(fmax), npoints)
    idx = np.unique(np.clip(np.searchsorted(freqs, targets), lo, hi))

    return {
        "taps":  int(n),
        "delay_ms": round(bulk_delay * 1000.0, 4),
        "fmax": round(float(fmax), 3),
        "freqs": [round(float(freqs[i]), 3) for i in idx],
        "mag":   [round(float(mag[i]),   3) for i in idx],
        "phase": [round(float(np.degrees(phase[i])), 2) for i in idx],
        "gd":    [round(float(gd_ms[i]), 4) for i in idx],
    }


def _format_read_output(cmd_id: str, output: str) -> str:
    if cmd_id == "drc_status" and output:
        parts = output.split()
        if not parts:
            return output
        if parts[-1].lower() == "off":
            return "Off"
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
        })
    except FileNotFoundError:
        return jsonify({"ok": False, "line1": "", "line2": ""})
    except OSError as e:
        return jsonify({"ok": False, "line1": "", "line2": "", "error": str(e)})


def _service_running(name: str) -> bool:
    """True if renderer service `name` is currently running.  Linux: systemd
    --user is-active.  FreeBSD: rc `onestatus` (works whether or not the service
    is enabled in rc.conf), falling back to a process check.

    `service <name> onestatus` is unreliable here when run unprivileged: rc.subr
    reads the pidfile, and a renderer that runs under its own service account
    keeps that pidfile inside a 0700 home directory the panel user cannot
    traverse (qobuzconnect2mpd puts it under /var/db/qobuzconnect2mpd), so
    onestatus reports "not running" for a service that is running.  Start/stop
    go through sudo and do not have that blind spot, so believing onestatus
    would let both mutually-exclusive renderers drive MPD at once.

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
    web UI toggle in sync with reality.  `remembered` is the one the boot
    service will bring up after a reboot (None until the toggle is first used,
    where the boot service falls back to its own default)."""
    return jsonify({
        "ok":               True,
        "qobuzconnect2mpd": _service_running(QCONNECT_SERVICE),
        "upmpdcli":         _service_running(UPMPDCLI_SERVICE),
        "remembered":       _read_state_str(_RENDERER_STATE_FILE),
    })


@app.route("/qconnect/switch", methods=["POST"])
def qconnect_switch():
    """Switch the active renderer: stop the other service, then start the
    target.  Body: {"target": "qobuzconnect2mpd"|"upmpdcli"}."""
    data   = request.get_json(silent=True) or {}
    target = data.get("target")
    if target not in SWITCHABLE_SERVICES:
        return jsonify({"ok": False, "error": "invalid target"}), 400
    other = UPMPDCLI_SERVICE if target == QCONNECT_SERVICE else QCONNECT_SERVICE
    try:
        # Stop the active one before starting the next.  The stop is issued
        # unconditionally rather than gated on _service_running(): a status
        # check that wrongly reports "not running" would otherwise skip it and
        # leave both renderers driving MPD.  onestop on an already-stopped
        # service exits non-zero, so what counts is the state afterwards.
        _service_action(other, "onestop")
        if _service_running(other):
            return jsonify({"ok": False, "error": f"could not stop {other}"})
        # Leave MPD in a clean state for the incoming renderer.
        _mpc_quiesce()
        if not _service_running(target):
            r = _service_action(target, "onestart")
            if r.returncode != 0:
                return jsonify({"ok": False,
                                "error": f"starting {target}: {(r.stderr or r.stdout).strip()}"})
        # Remember the choice for the next boot.  Written only once the switch
        # has actually succeeded, so a failed switch does not arm the boot
        # service with a renderer that would not start.
        _write_state_str(_RENDERER_STATE_FILE, target)
        return jsonify({"ok": True, "active": target})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)})


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
    for cmd in (["sysctl", "dev.pcm.0"], ["sysctl", "hw.usb.uaudio"]):
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
                "source_commit": manifest["source"]["repository_head"],
                "release": manifest["source"].get("release"),
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
    """Verified room/filter/prediction data for the *running* BruteFIR.

    The active .conf carries absolute paths to its coeff (.raw) files and the
    sampling rate. Stored measurements are released only when their manifest
    matches the SHA-256 of both exact coefficient files in that config.
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
        channels = []
        palette = {"Left": "#388bfd", "Right": "#d29922"}
        for c in parsed["coeffs"]:
            ch = _coeff_channel(c)
            resp = _fir_response(c["filename"], c["format"], rate)
            # BruteFIR attenuation is part of the audible transfer function.
            # Phase/group delay are unaffected; magnitude is reduced in dB.
            resp["mag"] = [round(value - c["attenuation"], 3) for value in resp["mag"]]
            resp.update({
                "name": ch,
                "color": palette.get(ch, "#3fb950"),
                "attenuation": c["attenuation"],
                "format": c["format"],
                "file": os.path.basename(c["filename"]),
                "sha256": _sha256_file(c["filename"]),
            })
            channels.append(resp)
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
                "frequencies_hz": analysis["frequencies_hz"],
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
                    "source_repository": manifest["source"]["repository"],
                    "source_commit": manifest["source"]["repository_head"],
                    "source_ref": manifest["source"].get("source_ref"),
                    "release": manifest["source"].get("release"),
                    "source_declaration": manifest["source"].get("declaration"),
                    "project": manifest["source"].get("project"),
                    "measurements": analysis["source_headers"],
                    "lineage": manifest["source"]["lineage"],
                    "validation": analysis["validation"],
                    "calculation": analysis["calculation"],
                    "runtime": manifest["runtime"]["rates"][str(rate)],
                },
            })
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({"ok": False, "running": True, "error": f"filter file not found: {e}"})
    except ImportError:
        return jsonify({"ok": False, "running": True, "error": "numpy is required for filter analysis"})
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
