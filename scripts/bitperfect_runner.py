#!/usr/bin/env python3
"""Run a bit-perfect USB tap through a chosen playback path.

What this adds to the existing suite
====================================
`bitperfect-tap-linux.sh` / `bitperfect-tap-freebsd.sh` prove the segment
*host → USB wire*: they play with aplay (or an OSS writer) straight to the
raw device.  That is the right control experiment, but it is not how this box
plays music.  Music arrives through **upmpdcli** or **qobuzconnect2mpd**, both
of which drive **MPD**, and every one of those layers can break bit-perfection
in a way the aplay test would never see — MPD resampling, a replaygain or
volume setting a renderer applied, a decoder promoting samples differently.

This runner keeps the tap and the verdict exactly as they are and varies only
*who plays*:

    aplay     the existing per-OS script, delegated to unchanged (control)
    mpd       MPD plays a local file      -> MPD -> DAC
    mpd-http  MPD plays an HTTP URL       -> MPD's curl input plugin + decoder,
                                             structurally the Qobuz stream path
    upnp      upmpdcli is driven over OpenHome, and IT tells MPD what to play
    live      nothing is played by us; a real Qobuz stream is tapped and
              compared against the file the renderer itself buffered

Progress grammar
================
Machine-readable lines on stdout, the same `@@` convention `glitch-usbtap.sh`
already uses, so the web page can drive a phase strip and live counters while
the human-readable log scrolls underneath:

    @@PHASE  <prep|tap|play|drain|align|verdict>  [detail]
    @@STAT   key=value ...
    @@INFO   free text worth surfacing (resolved buffer, MPD state, ...)
    @@RESULT verdict=... exit=N prefix=...

Exit codes are the suite's: 0 bit-perfect, 1 judged and wrong, 2 could not
judge.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
LIB = HERE / "bitperfect-lib.py"
MATERIAL = HERE / "bitperfect_material.py"

SOURCES = ("aplay", "mpd", "mpd-http", "upnp", "live")
DIRECT_OUTPUT = "OKTO-DAC"          # MPD's bit-perfect output (mpd/mpd.conf.in)


def emit(kind: str, text: str) -> None:
    print(f"@@{kind} {text}", flush=True)


def say(text: str) -> None:
    print(text, flush=True)


def run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    return subprocess.run(argv, **kw)


# ═══════════════════════════════════════════════════════════════════════════
# DAC discovery — never hard-coded, because the address moves across replugs
# ═══════════════════════════════════════════════════════════════════════════

def discover_linux() -> dict:
    """ALSA card -> USB bus/device, from /proc/asound/cardN/usbbus."""
    for d in sorted(Path("/proc/asound").glob("card[0-9]*")):
        usbbus = d / "usbbus"
        if not usbbus.is_file():
            continue
        bus, devnum = usbbus.read_text().strip().split("/")
        card = d.name[len("card"):]
        return {"os": "linux", "card": card, "bus": int(bus),
                "devnum": int(devnum), "alsa": f"hw:{card},0",
                "pcm_node": f"/dev/snd/pcmC{card}D0p"}
    raise RuntimeError("no USB audio card found under /proc/asound")


def discover_freebsd() -> dict:
    """/dev/dsp.dac -> pcm unit -> uaudio parent -> bus/devaddr.

    Same chain as glitch-usbtap.sh: the pcm unit and its uaudio parent move
    with USB attach order, so resolving from the stable device name is the
    only thing that survives a replug."""
    link = ""
    try:
        link = os.readlink("/dev/dsp.dac")
    except OSError:
        pass
    m = re.match(r"dsp(\d+)", link or "")
    unit = m.group(1) if m else "0"
    parent = run(["sysctl", "-n", f"dev.pcm.{unit}.%parent"]).stdout.strip()
    pm = re.match(r"uaudio(\d+)", parent or "")
    parent_unit = pm.group(1) if pm else "0"
    loc = run(["sysctl", "-n", f"dev.uaudio.{parent_unit}.%location"]).stdout.strip()
    bus = re.search(r"bus=(\d+)", loc)
    daddr = re.search(r"devaddr=(\d+)", loc)
    if not (bus and daddr):
        raise RuntimeError(f"uaudio{parent_unit} not found — is the DAC attached?")
    return {"os": "freebsd", "unit": unit, "bus": int(bus.group(1)),
            "devaddr": int(daddr.group(1)), "usbus": f"usbus{bus.group(1)}",
            "dsp": f"/dev/dsp{unit}"}


def discover() -> dict:
    return discover_freebsd() if sys.platform.startswith("freebsd") else discover_linux()


# ═══════════════════════════════════════════════════════════════════════════
# The tap
# ═══════════════════════════════════════════════════════════════════════════

class Tap:
    """The USB wire tap, started before any audio flows and stopped after it
    drains.  Both OSes end with the same thing: cap.raw, the concatenated
    isochronous OUT payloads of endpoint 0x01."""

    def __init__(self, dac: dict, tmp: Path):
        self.dac, self.tmp = dac, tmp
        self.cap = tmp / "cap.raw"
        self.proc: subprocess.Popen | None = None
        self.pcap = tmp / "cap.pcap"
        self.log = tmp / "tap.log"

    def start(self) -> None:
        if self.dac["os"] == "linux":
            run(["sudo", "-n", "modprobe", "usbmon"])
            cmd = ["sudo", "-n", sys.executable, str(LIB), "tap-usbmon",
                   str(self.dac["bus"]), str(self.dac["devnum"]), str(self.cap)]
        else:
            cmd = ["sudo", "-n", "usbdump", "-i", self.dac["usbus"],
                   "-f", str(self.dac["devaddr"]), "-s", "65536",
                   "-w", str(self.pcap)]
        emit("PHASE", "tap starting")
        self.logf = open(self.log, "w")
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=self.logf, start_new_session=True)
        time.sleep(0.8)                     # let it attach before audio starts
        if self.proc.poll() is not None:
            raise RuntimeError(
                "the USB tap would not start (needs root: "
                + ("usbmon" if self.dac["os"] == "linux" else "usbdump")
                + "). " + self.log.read_text().strip()[:300])
        emit("PHASE", "tap attached")

    def stop(self) -> Path:
        if self.proc and self.proc.poll() is None:
            # SIGINT, not SIGTERM: usbdump buffers its pcap and only flushes on
            # a clean interrupt — the difference between a complete capture and
            # an INCOMPLETE verdict (doc/BIT-PERFECT-VERIFICATION.md step 3).
            run(["sudo", "-n", "kill", "-INT", str(self.proc.pid)])
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                run(["sudo", "-n", "kill", "-KILL", str(self.proc.pid)])
        self.logf.close()
        stats = self.log.read_text().strip()
        if stats:
            for line in stats.splitlines():
                emit("STAT", line.strip())
        if self.dac["os"] != "linux":
            emit("PHASE", "decoding capture")
            run(["sudo", "-n", "chmod", "a+r", str(self.pcap)])
            if not self.pcap.exists() or not self.pcap.stat().st_size:
                raise RuntimeError("no capture written (permission? wrong bus?)")
            text = run(["sudo", "-n", "usbdump", "-r", str(self.pcap), "-vv"])
            proc = subprocess.run(
                [sys.executable, str(LIB), "decode-usbdump", str(self.cap)],
                input=text.stdout, text=True)
            if proc.returncode:
                raise RuntimeError("usbdump decode failed")
        if not self.cap.exists() or not self.cap.stat().st_size:
            raise RuntimeError("nothing was captured on the USB wire")
        emit("STAT", f"wire_bytes={self.cap.stat().st_size}")
        return self.cap


# ═══════════════════════════════════════════════════════════════════════════
# MPD control — reused by the mpd, mpd-http and upnp sources
# ═══════════════════════════════════════════════════════════════════════════

class Mpd:
    """Thin mpc wrapper that puts the queue, outputs and gain settings back.

    Same contract as verify-bitperfect.sh's feed_mpd/restore_mpd: a
    verification run must not cost the user their playlist."""

    def __init__(self, port: str | None = None):
        self.client = shutil.which("mpc") or shutil.which("musicpc")
        if not self.client:
            raise RuntimeError("mpc/musicpc not found — cannot drive MPD")
        self.port = port
        self.saved_playlist: str | None = None
        self.saved_outputs: list[tuple[str, bool]] = []
        self.saved_replaygain: str | None = None
        self.saved_volume: str | None = None

    def __call__(self, *args: str, check: bool = False) -> str:
        argv = [self.client]
        if self.port:
            argv += ["-p", str(self.port)]
        r = run(argv + list(args), timeout=20)
        if check and r.returncode:
            raise RuntimeError(f"mpc {' '.join(args)}: "
                               f"{(r.stderr or r.stdout).strip()}")
        return r.stdout

    def outputs(self) -> list[tuple[str, bool]]:
        found = []
        for line in self("outputs").splitlines():
            m = re.match(r"Output\s+\d+\s+\((.+)\)\s+is\s+(enabled|disabled)", line.strip())
            if m:
                found.append((m.group(1), m.group(2) == "enabled"))
        return found

    def state(self) -> dict:
        """The settings that silently break bit-perfection if they drift."""
        status = self("status")
        volume = re.search(r"volume:\s*(\S+)", status)
        # `mpc replaygain` answers "replay_gain_mode: off", not "off".
        gain = (self("replaygain") or "").strip()
        return {
            "volume": volume.group(1) if volume else "?",
            "replaygain": gain.split(":", 1)[1].strip() if ":" in gain else (gain or "?"),
            "outputs": [n for n, on in self.outputs() if on],
        }

    def snapshot(self) -> None:
        """Put the user's queue somewhere we can put it back from.

        Not via `mpc save`: this MPD answers "Stored playlists are disabled",
        so a saved-playlist backup silently does nothing and the restore then
        fails with "No such playlist".  The queue's URIs are held in memory
        instead, which works on any MPD."""
        self.saved_outputs = self.outputs()
        state = self.state()
        self.saved_replaygain = state["replaygain"]
        self.saved_volume = state["volume"]
        self.saved_queue = [line for line in
                            self("--format", "%file%", "playlist").splitlines()
                            if line.strip()]
        if self.saved_queue:
            say(f"saved {len(self.saved_queue)} queued item(s) to restore afterwards")

    def restore(self) -> None:
        try:
            self("stop")
            self("clear")
            for uri in getattr(self, "saved_queue", []):
                self("add", uri)
            for name, enabled in self.saved_outputs:
                self("enable" if enabled else "disable", name)
            if self.saved_replaygain not in (None, "", "?"):
                self("replaygain", self.saved_replaygain)
            volume = re.fullmatch(r"(\d+)%?", self.saved_volume or "")
            if volume:
                self("volume", volume.group(1))
        except Exception as error:
            say(f"WARNING: could not fully restore MPD: {error}")

    def music_directory(self) -> Path | None:
        """MPD's music_directory, read from the config the process was given.

        Not available over the client protocol, so it comes from the running
        command line — the same trick app.py's _mpd_conf_from_cmdline uses."""
        pid = run(["pgrep", "-x", "musicpd"]).stdout.split() or \
            run(["pgrep", "-x", "mpd"]).stdout.split()
        if not pid:
            return None
        cmdline = run(["ps", "-o", "command=", "-p", pid[0]]).stdout.strip()
        for token in reversed(cmdline.split()):
            if token.endswith(".conf") and Path(token).is_file():
                for line in Path(token).read_text().splitlines():
                    m = re.match(r'\s*music_directory\s+"(.+)"', line)
                    if m:
                        return Path(m.group(1))
        return None

    def stage_locally(self, source: Path) -> str | None:
        """Put `source` where MPD can open it as a LOCAL FILE, if we can.

        MPD refuses `file://` absolute URIs from a TCP client ("Access to
        local files via TCP is not allowed") and this MPD has no Unix socket,
        so the only way to exercise the local-file input plugin is to place
        the material inside music_directory and add it by relative path.
        Returns the relative URI, or None when the library is not writable —
        in which case the caller falls back to HTTP and says so."""
        root = self.music_directory()
        if root is None or not root.is_dir() or not os.access(root, os.W_OK):
            return None
        staging = root / ".omdrc-bitperfect"
        try:
            staging.mkdir(exist_ok=True)
            target = staging / source.name
            if not target.exists() or target.stat().st_size != source.stat().st_size:
                shutil.copyfile(source, target)
        except OSError:
            return None
        self.staged = target
        relative = f".omdrc-bitperfect/{source.name}"
        self("update", relative, check=False)
        # `mpc update --wait` needs the daemon to finish scanning before the
        # path resolves; without it the add races the update and fails.
        for _ in range(60):
            if "updating_db" not in self("status"):
                break
            time.sleep(0.5)
        return relative

    def unstage(self) -> None:
        target = getattr(self, "staged", None)
        if target is None:
            return
        try:
            target.unlink()
            self("update", ".omdrc-bitperfect", check=False)
        except OSError:
            pass

    def play_only(self, uri: str, output: str = DIRECT_OUTPUT) -> None:
        self("stop")
        self("clear")
        self("enable", "only", output, check=True)
        self("add", uri, check=True)
        self("play", check=True)

    def wait_until_done(self, expected: float, margin: float = 8.0) -> None:
        """Block until playback has actually started AND then finished.

        Waiting only for "not playing" would return instantly when MPD (or
        upmpdcli, which has a URL to fetch first) has not started yet — the tap
        would be stopped before a single sample reached the wire."""
        start_deadline = time.monotonic() + 20.0
        while time.monotonic() < start_deadline:
            if "[playing]" in self("status"):
                break
            time.sleep(0.25)
        else:
            say("WARNING: MPD never reported playing — nothing may have been sent")
            return
        emit("PHASE", "play started")
        deadline = time.monotonic() + expected + margin
        last = ""
        # An HTTP stream makes MPD drop out of [playing] for a moment while it
        # buffers, and treating the first such sample as "finished" cut a 10 s
        # run off after 0.27 s.  Only a sustained absence means the end.
        idle = 0
        while time.monotonic() < deadline:
            status = self("status")
            if "[playing]" in status:
                idle = 0
                m = re.search(r"(\d+:\d+)/(\d+:\d+)", status)
                if m and m.group(0) != last:
                    last = m.group(0)
                    emit("STAT", f"mpd_position={m.group(1)} of {m.group(2)}")
            else:
                idle += 1
                if idle >= 6:                # ~3 s of genuinely not playing
                    return
            time.sleep(0.5)
        say("WARNING: MPD did not report the track finished in time")


# ═══════════════════════════════════════════════════════════════════════════
# Serving the material over HTTP (mpd-http and upnp both need a URL)
# ═══════════════════════════════════════════════════════════════════════════

class FileServer:
    """A one-file HTTP server bound to the LAN address.

    MPD's curl input plugin and upmpdcli both fetch by URL, and the URL has to
    be reachable from those processes — which is why it binds the routable
    address rather than localhost."""

    def __init__(self, path: Path):
        import http.server
        import threading
        self.path = path
        directory = str(path.parent)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=directory, **kw)

            def log_message(self, *a):       # keep the job log readable
                pass

        self.server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://{local_address()}:{self.port}/{self.path.name}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def local_address() -> str:
    """The address other processes on this box (and upmpdcli) can reach us on."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))          # TEST-NET-1: never actually sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ═══════════════════════════════════════════════════════════════════════════
# upmpdcli over OpenHome
# ═══════════════════════════════════════════════════════════════════════════

SSDP_ADDR, SSDP_PORT = "239.255.255.250", 1900


def ssdp_find(target: str, timeout: float = 4.0) -> list[str]:
    """M-SEARCH for `target`, returning the LOCATION URLs that answered."""
    message = ("M-SEARCH * HTTP/1.1\r\n"
               f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
               'MAN: "ssdp:discover"\r\n'
               "MX: 2\r\n"
               f"ST: {target}\r\n\r\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(timeout)
    locations: list[str] = []
    try:
        s.sendto(message, (SSDP_ADDR, SSDP_PORT))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _ = s.recvfrom(65535)
            except socket.timeout:
                break
            m = re.search(rb"LOCATION:\s*(\S+)", data, re.I)
            if m:
                url = m.group(1).decode()
                if url not in locations:
                    locations.append(url)
    finally:
        s.close()
    return locations


def openhome_playlist(friendly: str | None = None) -> tuple[str, str]:
    """Locate upmpdcli's OpenHome Playlist service.

    Returns (control_url, service_type).  upmpdcli runs openhome=1/upnpav=0
    here, so the Playlist service — not AVTransport — is what accepts a URI."""
    service_type = "urn:av-openhome-org:service:Playlist:1"
    locations = ssdp_find(service_type) or ssdp_find("upnp:rootdevice")
    for location in locations:
        try:
            with urllib.request.urlopen(location, timeout=5) as r:
                xml = r.read().decode("utf-8", "replace")
        except Exception:
            continue
        if friendly and friendly.lower() not in xml.lower():
            continue
        # find the Playlist service block and its controlURL
        for block in re.findall(r"<service>(.*?)</service>", xml, re.S):
            if "Playlist:1" not in block:
                continue
            m = re.search(r"<controlURL>(.*?)</controlURL>", block, re.S)
            if not m:
                continue
            control = m.group(1).strip()
            base = re.match(r"(https?://[^/]+)", location).group(1)
            if not control.startswith("http"):
                control = base + ("" if control.startswith("/") else "/") + control
            return control, service_type
    raise RuntimeError(
        "no OpenHome Playlist service answered SSDP — is upmpdcli running "
        "with openhome=1, and is multicast reaching it?")


def soap(control_url: str, service_type: str, action: str, body: str) -> str:
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{service_type}">{body}</u:{action}>'
        "</s:Body></s:Envelope>").encode()
    request = urllib.request.Request(control_url, data=envelope, headers={
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f'"{service_type}#{action}"'})
    with urllib.request.urlopen(request, timeout=15) as r:
        return r.read().decode("utf-8", "replace")


def didl(url: str, name: str) -> str:
    """Minimal DIDL-Lite metadata.

    upmpdcli runs with checkcontentformat=0, so the protocolInfo is not
    policed; the item still has to be well-formed to be accepted."""
    return (
        "&lt;DIDL-Lite "
        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"&gt;'
        '&lt;item id="bp" parentID="0" restricted="1"&gt;'
        f"&lt;dc:title&gt;{name}&lt;/dc:title&gt;"
        "&lt;upnp:class&gt;object.item.audioItem.musicTrack&lt;/upnp:class&gt;"
        '&lt;res protocolInfo="http-get:*:audio/wav:*"&gt;'
        f"{url}&lt;/res&gt;&lt;/item&gt;&lt;/DIDL-Lite&gt;")


def upnp_play(url: str, name: str, friendly: str | None) -> None:
    control, service_type = openhome_playlist(friendly)
    emit("INFO", f"OpenHome Playlist control: {control}")
    soap(control, service_type, "DeleteAll", "")
    reply = soap(control, service_type, "Insert",
                 f"<AfterId>0</AfterId><Uri>{url}</Uri>"
                 f"<Metadata>{didl(url, name)}</Metadata>")
    m = re.search(r"<NewId>(\d+)</NewId>", reply)
    if m:
        soap(control, service_type, "SeekId", f"<Value>{m.group(1)}</Value>")
    soap(control, service_type, "Play", "")


# ═══════════════════════════════════════════════════════════════════════════
# Chain preconditions
# ═══════════════════════════════════════════════════════════════════════════

def brutefir_running() -> bool:
    return run(["pgrep", "-x", "brutefir"]).returncode == 0


RENDERERS = ("qobuzconnect2mpd", "upmpdcli")


def running_renderer() -> str | None:
    for name in RENDERERS:
        if run(["pgrep", "-x", name]).returncode == 0:
            return name
    return None


class RendererArbiter:
    """Keeps exactly one thing driving MPD for the duration of a run.

    qobuzconnect2mpd and upmpdcli are mutually exclusive front-ends, and an
    active one does not sit still: it watches MPD and re-queues its own track.
    That is not hypothetical — it truncated a 10 s run to 2.2 s here, MPD's log
    showing our WAV replaced by a Qobuz stream mid-playback.  So:

        mpd / mpd-http   nothing else may drive MPD -> stop the renderer
        upnp             upmpdcli must be the one running
        live             qobuzconnect2mpd must be running; touch nothing

    Whatever was running is started again afterwards, and `omdrc-renderer stop`
    deliberately leaves the remembered choice alone so that restart is exact.
    """

    def __init__(self, source: str):
        self.source = source
        self.was = running_renderer()
        self.stopped = False

    def __enter__(self):
        if self.source == "live":
            if self.was != "qobuzconnect2mpd":
                raise SystemExit(
                    "--source live needs qobuzconnect2mpd running "
                    f"(currently: {self.was or 'no renderer'}).")
            return self
        if self.source == "upnp":
            if self.was != "upmpdcli":
                raise SystemExit(
                    "--source upnp needs upmpdcli running "
                    f"(currently: {self.was or 'no renderer'}). "
                    "Switch with: scripts/omdrc-renderer set upmpdcli && "
                    "scripts/omdrc-renderer restart")
            return self
        if self.was:
            say(f"stopping {self.was} for the duration of the run — an active "
                "renderer re-queues its own track and would truncate the tap")
            run([str(HERE / "omdrc-renderer"), "stop"], timeout=60)
            for _ in range(40):
                if running_renderer() is None:
                    break
                time.sleep(0.25)
            if running_renderer() is not None:
                raise RuntimeError(
                    f"could not stop {self.was}; it would interfere with the test")
            self.stopped = True
        return self

    def __exit__(self, *exc):
        if self.stopped:
            emit("PHASE", f"restarting {self.was}")
            run([str(HERE / "omdrc-renderer"), "start"], timeout=60)
        return False


def dac_busy(dac: dict) -> str:
    """Who holds the DAC — it is a single-opener device on both OSes."""
    node = dac.get("pcm_node") or dac.get("dsp")
    if not node or not Path(node).exists():
        return ""
    try:
        r = run(["fuser", node])
    except OSError:
        return "unknown (fuser unavailable)"
    return (r.stdout + r.stderr).strip() if r.returncode == 0 else ""


# ═══════════════════════════════════════════════════════════════════════════
# The run
# ═══════════════════════════════════════════════════════════════════════════

def pad_for_play(source: Path, out_dir: Path, seconds: float = 3.0) -> Path:
    """A copy of `source` with digital silence appended, for PLAYBACK only.

    MPD does not drain its output buffer on close: measured here, a 10 s WAV
    reached the wire 129744 bytes (0.74 s) short, identically across runs and
    regardless of how long the tap kept recording afterwards — so it is the
    player discarding its tail, not the tap missing it.  The direct OSS writer
    has no such gap because it SNDCTL_DSP_SYNCs first.

    This is the same technique bitperfect-tap-freebsd.sh already uses (its
    PAD_MS): play reference + silence, compare against the unpadded reference,
    and the truncation lands in the silence where nothing depends on it.  The
    pad cannot mask a defect — the comparison window is still exactly
    len(ref) bytes, so every reference byte is still checked.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"padded-{source.name}"
    if source.suffix.lower() == ".wav":
        import wave as wavemod
        with wavemod.open(str(source), "rb") as r:
            params = r.getparams()
            frames = r.readframes(r.getnframes())
        silence = b"\0" * int(seconds * params.framerate
                              * params.nchannels * params.sampwidth)
        with wavemod.open(str(target), "wb") as w:
            w.setparams(params)
            w.writeframes(frames + silence)
        return target
    # Anything else keeps its container, so the decoder under test is still
    # the one that will decode the real thing.
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        say("WARNING: ffmpeg not found, cannot pad — expect an INCOMPLETE "
            "verdict from the player truncating its tail")
        return source
    target = out_dir / f"padded-{source.stem}{source.suffix}"
    r = run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source),
             "-af", f"apad=pad_dur={seconds}", str(target)])
    if r.returncode != 0:
        say(f"WARNING: could not pad {source.name}: "
            f"{(r.stderr or '').strip()[:200]}")
        return source
    return target


def load_material(path: Path, out_dir: Path, decoded_for_play: bool) -> dict:
    argv = [sys.executable, str(MATERIAL), "load", str(path),
            "--out-dir", str(out_dir)]
    if decoded_for_play:
        argv.append("--decoded-for-play")
    r = run(argv)
    info = json.loads(r.stdout or '{"ok": false, "error": "no output"}')
    if not info.get("ok"):
        raise RuntimeError(info.get("error", "could not load material"))
    return info


def delegate_aplay(input_path: Path, prefix: str) -> int:
    """Hand the control experiment to the existing, proven per-OS script.

    Deliberately not reimplemented: that script is what produced every result
    in doc/BIT-PERFECT-VERIFICATION.md, and a second implementation of the
    same thing would be a second thing to keep correct."""
    script = HERE / ("bitperfect-tap-freebsd.sh"
                     if sys.platform.startswith("freebsd")
                     else "bitperfect-tap-linux.sh")
    # The delegated script taps, plays, drains and compares by itself, so
    # without this translation the page's phase strip sat on one stage for the
    # whole control run and then jumped straight to the verdict.  Both per-OS
    # scripts announce the moment audio starts with a line beginning "Playing";
    # that single milestone is enough to split the run into tap and play.
    emit("PHASE", f"tap — delegating to {script.name}")
    proc = subprocess.Popen([str(script), "--out", prefix, str(input_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        text = line.rstrip()
        if text.startswith("Playing"):
            emit("PHASE", "play")
        say(text)
    code = proc.wait()
    emit("PHASE", "verdict")
    return code


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=SOURCES, default="aplay")
    p.add_argument("--input", help="WAV/FLAC to verify (not used by --source live)")
    p.add_argument("--out", required=True, help="artifact prefix")
    p.add_argument("--duration", type=float, default=30.0,
                   help="tap window for --source live")
    p.add_argument("--mpd-port", default=None)
    p.add_argument("--friendly-name", default=None,
                   help="upmpdcli friendlyname, to pick one renderer of several")
    p.add_argument("--allow-drc", action="store_true",
                   help="run even while brutefir is convolving (the DRC path "
                        "is not bit-perfect by design; the verdict will be "
                        "meaningless)")
    args = p.parse_args()

    prefix = Path(args.out)
    prefix.parent.mkdir(parents=True, exist_ok=True)

    emit("PHASE", "prep")
    dac = discover()
    emit("INFO", f"DAC: {json.dumps(dac)}")

    if brutefir_running() and not args.allow_drc:
        raise SystemExit(
            "brutefir is running: the DRC path convolves the FIR filter, so it "
            "is NOT bit-perfect by design and a verdict here would be "
            "meaningless. Run `drc.sh off` first, or pass --allow-drc.")

    if args.source == "aplay":
        if not args.input:
            raise SystemExit("--input is required for --source aplay")
        # The stock appliance normally has one renderer holding the single-open
        # DAC.  The delegated control script deliberately refuses a busy raw
        # device, so arbitrate the renderer around it just as the MPD paths do.
        arbiter = RendererArbiter(args.source)
        arbiter.__enter__()
        try:
            code = delegate_aplay(Path(args.input).resolve(), str(prefix))
        finally:
            arbiter.__exit__(None, None, None)
        report = prefix.with_suffix(".json")
        verdict = "unknown"
        if report.exists():
            data = json.loads(report.read_text())
            data["source"] = "aplay"
            report.write_text(json.dumps(data, indent=2) + "\n")
            verdict = data.get("verdict", verdict)
        emit("RESULT", f"verdict={verdict} exit={code} prefix={prefix}")
        return code

    holder = dac_busy(dac)
    if holder and args.source not in ("live",):
        say(f"note: the DAC is currently held by: {holder}")

    tmp = Path(tempfile.mkdtemp(prefix="bprun.", dir=os.environ.get("TMPDIR", "/tmp")))
    mpd = None
    server = None
    tap = Tap(dac, tmp)
    arbiter = RendererArbiter(args.source)
    emit("INFO", f"renderer running: {arbiter.was or 'none'}")
    arbiter.__enter__()          # raises SystemExit when the wrong one is up
    try:
        material = None
        if args.source != "live":
            if not args.input:
                raise SystemExit(f"--input is required for --source {args.source}")
            material = load_material(Path(args.input).resolve(), tmp, False)
            emit("INFO", f"material: {json.dumps(material)}")
            if material.get("warning"):
                say(f"WARNING: {material['warning']}")

        # `live` reads MPD too, but never writes to it: the user's real Qobuz
        # session is playing and must not be disturbed.  The settings are
        # still worth recording — a non-disabled mixer or an active replaygain
        # is a bit-perfection fault whatever the byte verdict says.
        before = after = None
        if args.source in ("mpd", "mpd-http", "upnp", "live"):
            mpd = Mpd(args.mpd_port)
            before = mpd.state()
            emit("INFO", f"mpd before: {json.dumps(before)}")
            if args.source == "live":
                mpd = None                   # read-only: no snapshot, no restore
                observer = Mpd(args.mpd_port)
            else:
                mpd.snapshot()

        tap.start()

        emit("PHASE", "play")
        if args.source == "live":
            say(f"Tapping the wire for {args.duration:.0f} s — "
                "play a track through the renderer now.")
            end = time.monotonic() + args.duration
            while time.monotonic() < end:
                time.sleep(1.0)
                emit("STAT", f"tap_seconds={int(args.duration - (end - time.monotonic()))}")
        else:
            # The REFERENCE stays unpadded; only what is played gets the pad.
            play_path = pad_for_play(Path(material["play_path"]), tmp)
            duration = float(material["seconds"]) + 3.0
            if args.source == "mpd":
                staged = mpd.stage_locally(play_path)
                if staged:
                    emit("INFO", f"staged into the music library as {staged}")
                    mpd.play_only(staged)
                else:
                    # Not a silent substitution: the path under test changes
                    # from MPD's local-file input plugin to its curl one, and
                    # the report has to say which was actually exercised.
                    say("NOTE: MPD's music_directory is not writable and MPD "
                        "refuses file:// over TCP, so this run uses an HTTP "
                        "URL — the same path as --source mpd-http. The DAC "
                        "side of the verdict is unaffected.")
                    args.source = "mpd-http"
                    server = FileServer(play_path)
                    emit("INFO", f"serving material at {server.url}")
                    mpd.play_only(server.url)
            else:
                server = FileServer(play_path)
                url = server.url
                emit("INFO", f"serving material at {url}")
                if args.source == "mpd-http":
                    mpd.play_only(url)
                else:
                    upnp_play(url, play_path.name, args.friendly_name)
            mpd.wait_until_done(duration)

        # MPD reports "stopped" when it has finished FEEDING, not when the DAC
        # has finished playing: its output buffer plus the USB stack's queued
        # URBs are still draining to the wire.  Measured here, cutting the tap
        # 1 s after MPD stopped lost the last 129744 bytes (0.74 s) and turned
        # a clean run into INCOMPLETE.  The direct writer needs none of this —
        # it SNDCTL_DSP_SYNCs before returning.
        drain = 1.0 if args.source == "live" else 4.0
        emit("PHASE", f"drain ({drain:.0f}s for the buffers to reach the wire)")
        time.sleep(drain)
        cap = tap.stop()

        if mpd or args.source == "live":
            after = (mpd or observer).state()
            emit("INFO", f"mpd after: {json.dumps(after)}")
            if after["volume"] != before["volume"] or \
               after["replaygain"] != before["replaygain"]:
                say(f"WARNING: the renderer changed MPD state during playback "
                    f"(volume {before['volume']} -> {after['volume']}, "
                    f"replaygain {before['replaygain']} -> {after['replaygain']}"
                    f") — that is a bit-perfection risk in its own right.")

        if args.source == "live":
            emit("PHASE", "resolving what the renderer streamed")
            r = run([sys.executable, str(MATERIAL), "resolve-live",
                     "--out-dir", str(tmp),
                     "--since", str(time.time() - args.duration - 30)]
                    + (["--mpd-port", str(args.mpd_port)] if args.mpd_port else []))
            material = json.loads(r.stdout or '{"ok": false, "error": "no output"}')
            if not material.get("ok"):
                raise RuntimeError(material.get("error", "could not resolve"))
            emit("INFO", f"material: {json.dumps(material)}")
            say(f"reference resolved by {material.get('resolved_by')}: "
                f"{material['name']}")
            if material.get("warning"):
                say(f"WARNING: {material['warning']}")

        emit("PHASE", "align")
        shutil.copyfile(cap, f"{prefix}.wire.raw")
        osname = f"{sys.platform}/{os.uname().release}"
        proc = subprocess.Popen(
            [sys.executable, str(LIB), "finalize", material["ref_raw"], str(cap),
             str(material["rate"]), str(material["channels"]), str(prefix),
             osname, material["source"]],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            say(line.rstrip())
        code = proc.wait()

        emit("PHASE", "verdict")
        report = Path(f"{prefix}.json")
        if report.exists():
            data = json.loads(report.read_text())
            data["source"] = args.source
            data["resolved_by"] = material.get("resolved_by", "")
            data["lossy"] = material.get("lossy", False)
            if before is not None:
                data["mpd_before"], data["mpd_after"] = before, after
            report.write_text(json.dumps(data, indent=2) + "\n")
            emit("RESULT", f"verdict={data.get('verdict')} exit={code} "
                           f"prefix={prefix}")
        return code
    finally:
        if server:
            server.close()
        if mpd:
            emit("PHASE", "restoring MPD")
            mpd.unstage()
            mpd.restore()
        arbiter.__exit__(None, None, None)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        emit("RESULT", f"verdict=ERROR exit=2 prefix=")
        sys.exit(2)
