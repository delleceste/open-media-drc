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

...and *which route to the DAC* they play through (--route):

    direct    MPD's OKTO-DAC output, straight to the raw device, chain down.
              This is what every result in doc/BIT-PERFECT-VERIFICATION.md
              and bp-results/ was taken through.
    drc       MPD's DRC-native output: MPD -> loopback -> BruteFIR -> DAC.
              The path music actually takes, and the one the direct route
              cannot see, because it requires the chain to be torn down first.

The DRC route exists because "direct is bit-perfect" does not generalise to
the chain, and the chain is not the same on both operating systems: on Linux
the loopback is snd-aloop, a kernel ring buffer, while on FreeBSD it is
virtual_oss, a userspace mixer with its own format conversion and resampler.
Those elements sit in the audible path and have never been byte-verified.

A DRC-route verdict is only meaningful when the convolver is a pass-through,
so the route refuses to run unless the loaded filter is a dirac pulse at 0 dB
(the shipped `flat` geometry) and every rate in the chain matches the
material.  It then re-reads the chain while audio is flowing and records
whether MPD's output rate and the loopback's rate agreed.

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
import hashlib
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
ROUTES = ("direct", "drc")
DIRECT_OUTPUT = "OKTO-DAC"          # MPD's bit-perfect output (mpd/mpd.conf.in)
DRC_OUTPUT = "DRC-native"           # MPD -> loopback -> brutefir -> DAC


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

def discover_linux(card: str | None = None) -> dict:
    """ALSA card -> USB bus/device, from /proc/asound/cardN/usbbus.

    A box with more than one USB audio device has no defensible default: taking
    the lowest-numbered card silently taps whichever one happens to have
    enumerated first, which on this appliance is the ESI U24XL rather than the
    DAC the chain feeds.  A capture of the wrong endpoint looks exactly like a
    capture of the right one, so refuse to guess and make the caller name it.
    """
    found = []
    for d in sorted(Path("/proc/asound").glob("card[0-9]*")):
        usbbus = d / "usbbus"
        if not usbbus.is_file():
            continue
        bus, devnum = usbbus.read_text().strip().split("/")
        n = d.name[len("card"):]
        name = (d / "id").read_text().strip() if (d / "id").is_file() else "?"
        found.append({"os": "linux", "card": n, "bus": int(bus),
                      "devnum": int(devnum), "alsa": f"hw:{n},0",
                      "pcm_node": f"/dev/snd/pcmC{n}D0p", "name": name})
    if not found:
        raise RuntimeError("no USB audio card found under /proc/asound")
    if card is not None:
        for f in found:
            if f["card"] == str(card):
                return f
        raise RuntimeError(
            "card %s is not a USB audio card; found: %s"
            % (card, ", ".join("%s (%s)" % (f["card"], f["name"]) for f in found)))
    if len(found) > 1:
        raise RuntimeError(
            "more than one USB audio card present, so the tap target is "
            "ambiguous — pass --card N.  Found: %s"
            % ", ".join("%s (%s)" % (f["card"], f["name"]) for f in found))
    return found[0]


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


def discover(card: str | None = None) -> dict:
    if sys.platform.startswith("freebsd"):
        return discover_freebsd()
    return discover_linux(card)


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

    def wait_until_done(self, expected: float, margin: float = 8.0,
                        on_playing=None) -> None:
        """Block until playback has actually started AND then finished.

        `on_playing` runs once, the moment MPD reports playing.  The chain's
        rates are only fully observable then: MPD's output rate does not exist
        until it has opened the device, so a precondition check before play
        cannot see it and a check after play sees a closed device.

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
        if on_playing is not None:
            on_playing()
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
# The DRC chain — is it up, and is it mathematically a pass-through?
# ═══════════════════════════════════════════════════════════════════════════

def drc_script() -> str | None:
    """The drc.sh entry point, in either supported layout.

    The installed `omdrc` wrapper is preferred over the checkout's drc.sh,
    and that order matters: on a box running the installed copies, the
    checkout's drc.sh reads the checkout's OWN state files, which are stale
    the moment the box is driven by anything else.  Asked here it answered
    "Geometry: flat / Active config: Flat 44.1k" while the running brutefir
    was 120.blue @multi.pt at 192 kHz.  Only the rates in that report come
    from ps(1) and were right; the geometry did not.  Hence also
    geometry_from_conf(), which never consults a state file at all."""
    installed = shutil.which("omdrc")
    if installed:
        return installed
    repo = HERE.parent / "drc.sh"
    if repo.is_file() and os.access(repo, os.X_OK):
        return str(repo)
    return None


def geometry_from_conf(conf: Path) -> str | None:
    """The geometry actually loaded, read from the config path.

    configs/<geometry>/brutefir-<rate>[@variant].conf — the directory names
    the filter set, so the running process itself says which one is in the
    path.  No state file can disagree with this."""
    parent = conf.parent.name
    return parent if parent and parent != "configs" else None


def chain_state() -> dict:
    """`drc.sh status`, parsed.

    Deliberately reuses drc.sh rather than re-deriving the chain state here:
    it already resolves the loopback sink per OS (virtual_oss on FreeBSD, the
    ALSA stream on Linux) and already decides whether MPD's output rate and
    the sink's rate agree.  A second implementation would be a second thing
    to keep correct — and the point of this route is to trust one answer."""
    state: dict = {"script": drc_script(), "geometry": None, "sink": None,
                   "sink_rate": None, "brutefir_rate": None, "mpd_rate": None,
                   "mpd_format": None, "rate_verdict": None}
    if not state["script"]:
        return state
    try:
        r = subprocess.run([state["script"], "status"], capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return state

    def hz(text: str) -> int | None:
        m = re.search(r"(\d+)\s*Hz", text)
        return int(m.group(1)) if m else None

    for line in r.stdout.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "Geometry":
            state["geometry"] = value
        elif key in ("virtual_oss", "ALSA"):
            state["sink"], state["sink_rate"] = key, hz(value)
        elif key == "brutefir":
            state["brutefir_rate"] = hz(value)
        elif key == "Output audio":          # MPD's "rate:bits:channels"
            state["mpd_format"] = value
            m = re.match(r"(\d+)", value)
            state["mpd_rate"] = int(m.group(1)) if m else None
        elif key == "Rate":
            state["rate_verdict"] = ("match" if "[match]" in value else
                                     "mismatch" if "MISMATCH" in value else None)
    return state


def running_brutefir_conf() -> Path | None:
    """The config the running convolver was actually started with.

    Read from the command line rather than guessed from GEOMETRY and rate:
    what is loaded is what shapes the samples, and a stale STATE_FILE or a
    hand-started brutefir would make a guess lie.  Same match as drc.sh's
    status block."""
    try:
        out = subprocess.run(["ps", "-ax", "-o", "args="], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        fields = line.split()
        if not fields or Path(fields[0]).name != "brutefir":
            continue
        for field in fields[1:]:
            if field.endswith(".conf"):
                return Path(field)
    return None


def conf_is_identity(conf: Path) -> tuple[bool, list[str]]:
    """Is this brutefir config a mathematical pass-through?

    Only a dirac pulse at 0 dB is: convolution with a unit impulse leaves
    every sample exactly as it arrived, so a byte comparison against the
    source still means something.  A real room filter changes every sample
    BY DESIGN — running the DRC route through one does not produce a lenient
    verdict, it produces a meaningless one, which is why this refuses."""
    try:
        text = conf.read_text()
    except OSError as error:
        return False, [f"{conf}: {error}"]
    reasons: list[str] = []
    blocks = re.findall(r'coeff\s+"[^"]*"\s*\{(.*?)\}', text, re.S)
    if not blocks:
        return False, [f"{conf}: no coeff blocks — cannot tell what it convolves"]
    for block in blocks:
        name = re.search(r'filename:\s*"([^"]*)"', block)
        if not name or name.group(1) != "dirac pulse":
            reasons.append(f'{conf.name}: coeff filename is '
                           f'{name.group(1) if name else "unset"!r}, '
                           f'not "dirac pulse"')
        att = re.search(r"attenuation:\s*(-?[\d.]+)", block)
        if att and float(att.group(1)) != 0.0:
            reasons.append(f"{conf.name}: coeff attenuation {att.group(1)} dB, not 0.0")
    return not reasons, reasons


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def brutefir_defaults_path() -> Path | None:
    """The defaults file BruteFIR >= 1.1 actually reads."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    path = Path(base) / "BruteFIR" / "brutefir_defaults.conf"
    return path if path.is_file() else None


def chain_provenance() -> dict:
    """Everything that has to be equal for two captures to be comparable.

    A cross-OS null test compares one chain's output against another's, so it
    is only as meaningful as the sameness of what went in.  Recording this per
    run is what lets the comparison REFUSE rather than quietly null two
    different filters against each other and call the difference an OS.

    filter_length is in here for a specific reason: the shipped FreeBSD and
    Linux defaults use different partitionings (8192,64 against 32768,16).
    Uniform-partitioned overlap-save gives the same result either way in exact
    arithmetic, but the FFT sizes differ, so the floating-point rounding does
    too, and so does the convolver's latency.  Two captures taken with
    different partitionings can null to within a bit or so and still not be
    byte-identical — a difference that says nothing about either OS."""
    prov: dict = {"os": f"{sys.platform}/{os.uname().release}"}

    conf = running_brutefir_conf()
    prov["conf"] = str(conf) if conf else None
    if conf is not None:
        prov["conf_sha256"] = _sha256(conf)
        prov["geometry"] = geometry_from_conf(conf)
        name = conf.name                       # brutefir-<rate>[@variant].conf
        # A variant may contain dots ("@multi.pt"), so anchor on .conf and let
        # the variant group backtrack rather than excluding "." from it.
        m = re.match(r"brutefir-(\d+)(?:@(.+))?\.conf$", name)
        if m:
            prov["rate"] = int(m.group(1))
            prov["variant"] = m.group(2) or ""
        try:
            text = conf.read_text()
            coeffs = []
            for block in re.findall(r'coeff\s+"([^"]*)"\s*\{(.*?)\}', text, re.S):
                label, body = block
                fn = re.search(r'filename:\s*"([^"]*)"', body)
                att = re.search(r"attenuation:\s*(-?[\d.]+)", body)
                entry = {"coeff": label,
                         "filename": fn.group(1) if fn else None,
                         "attenuation": float(att.group(1)) if att else None}
                if fn and fn.group(1) != "dirac pulse":
                    entry["sha256"] = _sha256(Path(fn.group(1)))
                coeffs.append(entry)
            prov["coeffs"] = coeffs
        except OSError:
            pass

    defaults = brutefir_defaults_path()
    prov["defaults"] = str(defaults) if defaults else None
    if defaults is not None:
        text = defaults.read_text()
        for key in ("filter_length", "float_bits", "sdf_length", "safety_limit"):
            m = re.search(rf"^{key}:\s*([^;#]+);", text, re.M)
            if m:
                prov[key] = m.group(1).strip()
        for section, label in (("input", "input_sample"), ("output", "output_sample")):
            m = re.search(rf"{section}\s*\{{(.*?)^\}};", text, re.S | re.M)
            if m:
                fmt = re.search(r'sample:\s*"([^"]*)"', m.group(1))
                if fmt:
                    prov[label] = fmt.group(1)
                dither = re.search(r"dither:\s*(\w+)", m.group(1))
                if dither and section == "output":
                    prov["dither"] = dither.group(1)

    try:
        r = subprocess.run(["brutefir", "-nonexistent-flag"], capture_output=True,
                           text=True, timeout=10)
        v = re.search(r"BruteFIR\s+v?(\S+)", r.stdout + r.stderr)
        prov["brutefir_version"] = v.group(1) if v else None
    except (OSError, subprocess.TimeoutExpired):
        prov["brutefir_version"] = None

    # The loopback, named exactly as it is configured, because it is the one
    # element with no counterpart on the other operating system.
    try:
        args = subprocess.run(["ps", "-ax", "-o", "args="], capture_output=True,
                              text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        args = ""
    for line in args.splitlines():
        fields = line.split()
        if fields and Path(fields[0]).name == "virtual_oss":
            prov["loopback"] = "virtual_oss"
            prov["loopback_args"] = " ".join(fields[1:])
            # -S is what would put a resampler in the path; record its absence
            # as a fact of the run rather than an assumption about the daemon.
            prov["loopback_resampling"] = "-S" in fields
            break
    else:
        if sys.platform.startswith("linux"):
            prov["loopback"] = "snd-aloop"
            prov["loopback_resampling"] = False
    return prov


def assert_drc_route(material: dict | None, reference: str = "source") -> dict:
    """Refuse a DRC-route run that could not produce a meaningful verdict.

    Three ways it could not: the chain is not up (nothing under test), the
    loaded filter is not an identity (every sample changes by design), or a
    rate is out of step (something between MPD and the DAC is resampling, and
    the run would measure the resampler instead of the chain).

    The identity requirement applies to `reference="source"` only.  With
    `reference="capture"` the run is not judged against the source at all —
    it is one half of a cross-OS null test, where the other half is the same
    material through the same filter on the other machine.  A REAL filter is
    then not merely allowed but the point: it is what the appliance actually
    convolves, and a pass-through would not exercise it."""
    chain = chain_state()
    blocking: list[str] = []

    if chain["brutefir_rate"] is None:
        blocking.append(
            "the DRC chain is not running — start it with `drc.sh <rate>` "
            "(this route tests the chain; --route direct tests without it)")
    conf = running_brutefir_conf()
    chain["conf"] = str(conf) if conf else None
    if conf is not None:
        chain["geometry_running"] = geometry_from_conf(conf)
        chain["provenance"] = chain_provenance()
        if chain["geometry"] and chain["geometry_running"] and \
                chain["geometry"] != chain["geometry_running"]:
            # Not fatal — the identity check below reads the running config,
            # not this — but it means drc.sh and the chain disagree about what
            # is loaded, which is worth saying out loud before a verdict.
            say(f"WARNING: `drc.sh status` reports geometry "
                f"{chain['geometry']!r} but the running convolver is using "
                f"{chain['geometry_running']!r} ({conf}). The status command "
                "is reading a different state tree than the one driving the "
                "chain; trust the running config.")
        identity, reasons = conf_is_identity(conf)
        chain["identity"] = identity
        if not identity and reference == "source":
            blocking += reasons + [
                "the loaded filter is not a pass-through, so a byte verdict "
                "against the source is meaningless. Switch to the flat "
                "geometry (`drc.sh geometry flat`) and restart the chain."]
    elif chain["brutefir_rate"] is not None:
        blocking.append("brutefir is running but its config could not be read "
                        "from its command line")

    if material is not None:
        rate = int(material["rate"])
        # Applies in both reference modes.  In capture mode a rate mismatch
        # means MPD resamples with soxr on the way in, and while soxr is
        # deterministic, it is one more thing that must be identical on both
        # machines for the null to mean what it looks like.
        chain["material_rate"] = rate
        for label, value in (("brutefir", chain["brutefir_rate"]),
                             (chain["sink"] or "the loopback", chain["sink_rate"])):
            if value is not None and value != rate:
                blocking.append(
                    f"{label} is at {value} Hz but the material is {rate} Hz — "
                    f"start the chain at {rate} Hz (`drc.sh {rate}`), or "
                    "something in the path will resample and the verdict will "
                    "be about the resampler")

    if blocking:
        raise SystemExit("cannot judge the DRC route:\n  - "
                         + "\n  - ".join(blocking))
    return chain


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
    p.add_argument("--reference", choices=("source", "capture"),
                   default="source",
                   help="what the run is judged against. 'source' compares the "
                        "wire with the input bytes and needs a pass-through "
                        "filter. 'capture' emits no verdict: it records the "
                        "wire plus the chain's provenance so it can be nulled "
                        "against the same material through the same REAL "
                        "filter on the other OS (scripts/bitperfect-null.py).")
    p.add_argument("--route", choices=ROUTES, default="direct",
                   help="which path to the DAC to tap: 'direct' is MPD "
                        "straight to the raw device (the chain must be down); "
                        "'drc' is MPD -> loopback -> brutefir -> DAC, the path "
                        "music actually takes. The DRC route requires a "
                        "pass-through filter (the flat geometry) and a chain "
                        "started at the material's own rate.")
    p.add_argument("--input", help="WAV/FLAC to verify (not used by --source live)")
    p.add_argument("--out", required=True, help="artifact prefix")
    p.add_argument("--card", default=None,
                   help="ALSA card number of the DAC to tap (Linux). Required "
                        "when more than one USB audio card is present.")
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
    dac = discover(args.card)
    emit("INFO", f"DAC: {json.dumps(dac)}")

    if args.reference == "capture" and args.route != "drc":
        raise SystemExit(
            "--reference capture is for the DRC route: the direct path has "
            "the source bytes as its reference and does not need another "
            "capture to compare with. Add --route drc.")

    if args.route == "drc" and args.source == "aplay":
        raise SystemExit(
            "--route drc does not apply to --source aplay: that source "
            "delegates to the per-OS tap script, which opens the raw DAC node "
            "itself and is the direct control experiment by definition. Use "
            "--source mpd (or mpd-http/upnp) for the chain.")

    chain = None
    if args.route == "drc":
        # The whole point of this route is to judge the chain, so the chain
        # has to be up, transparent and at the right rate.  assert_drc_route
        # refuses otherwise rather than emitting a verdict nobody can read.
        # Checked twice on purpose: once here, so an unusable chain fails
        # before the renderer is stopped and the queue disturbed, and again
        # once the material is loaded and its rate is known.
        chain = assert_drc_route(None, args.reference)
        emit("INFO", f"chain: {json.dumps(chain)}")
        say(f"DRC route: brutefir {chain['brutefir_rate']} Hz through "
            f"{chain['conf']}"
            f"{' (pass-through)' if chain.get('identity') else ''}, "
            f"{chain['sink']} at {chain['sink_rate']} Hz")
        if args.reference == "capture":
            say("reference: capture — no verdict against the source. This run "
                "produces one half of a cross-OS null test; run the same "
                "material through the same filter on the other machine and "
                "compare with scripts/bitperfect-null.py.")
    elif brutefir_running() and not args.allow_drc:
        raise SystemExit(
            "brutefir is running: the DRC path convolves the FIR filter, so it "
            "is NOT bit-perfect by design and a verdict here would be "
            "meaningless. Run `drc.sh off` first, pass --route drc to test the "
            "chain deliberately, or pass --allow-drc.")

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
            if args.route == "drc":
                chain = assert_drc_route(material, args.reference)

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

        # Observed once, while audio is actually flowing.  This is the only
        # moment MPD's own output rate exists, and comparing it with the
        # loopback's rate is what says whether anything between MPD and the
        # DAC is resampling.  Recorded either way: a run that passes and a run
        # that resampled must not look the same afterwards.
        playing: dict = {}

        def probe_chain() -> None:
            playing.update(chain_state())
            emit("INFO", f"chain while playing: {json.dumps(playing)}")
            if playing.get("rate_verdict") == "mismatch":
                say(f"WARNING: MPD is feeding {playing['mpd_rate']} Hz into "
                    f"{playing['sink']} at {playing['sink_rate']} Hz — the "
                    "loopback is RESAMPLING and this verdict is about the "
                    "resampler, not the chain.")
            elif playing.get("rate_verdict") == "match":
                say(f"chain rates agree: MPD {playing['mpd_format']} into "
                    f"{playing['sink']} at {playing['sink_rate']} Hz — "
                    "no resampling in the loopback")

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
            mpd_output = DRC_OUTPUT if args.route == "drc" else DIRECT_OUTPUT
            emit("INFO", f"MPD output: {mpd_output}")
            # The REFERENCE stays unpadded; only what is played gets the pad.
            play_path = pad_for_play(Path(material["play_path"]), tmp)
            duration = float(material["seconds"]) + 3.0
            if args.source == "mpd":
                staged = mpd.stage_locally(play_path)
                if staged:
                    emit("INFO", f"staged into the music library as {staged}")
                    mpd.play_only(staged, output=mpd_output)
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
                    mpd.play_only(server.url, output=mpd_output)
            else:
                server = FileServer(play_path)
                url = server.url
                emit("INFO", f"serving material at {url}")
                if args.source == "mpd-http":
                    mpd.play_only(url, output=mpd_output)
                else:
                    # upmpdcli tells MPD what to play but not where: MPD uses
                    # whichever outputs are enabled, so the route is selected
                    # here, before the renderer is asked to start.
                    mpd("enable", "only", mpd_output, check=True)
                    upnp_play(url, play_path.name, args.friendly_name)
            mpd.wait_until_done(duration, on_playing=probe_chain)

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

        shutil.copyfile(cap, f"{prefix}.wire.raw")
        osname = f"{sys.platform}/{os.uname().release}"

        if args.reference == "capture":
            # No alignment and no verdict: with a real filter the wire is not
            # the source and never will be.  What this run owes the null test
            # is the raw wire plus enough provenance for the comparison to
            # refuse if the two halves were not actually taken through the
            # same thing.
            emit("PHASE", "recording provenance")
            report = {
                "kind": "CAPTURE", "verdict": "CAPTURED", "route": args.route,
                "reference": "capture", "source": args.source, "os": osname,
                "rate": material["rate"], "channels": material["channels"],
                "input": material.get("name") or str(args.input),
                # The input's own hash, and the hash of the promoted S32_LE
                # payload that both machines must be fed.  Two captures of
                # different material would otherwise null to noise and look
                # like an operating-system difference.
                "input_sha256": material.get("sha256"),
                "ref_raw_bytes": material.get("ref_bytes"),
                "decoder": material.get("decoder"),
                "lossy": material.get("lossy", False),
                "wire_raw": f"{prefix}.wire.raw",
                "wire_bytes": Path(cap).stat().st_size,
                "chain": chain, "chain_playing": playing or None,
                "mpd_before": before, "mpd_after": after,
            }
            Path(f"{prefix}.json").write_text(json.dumps(report, indent=2) + "\n")
            say(f"captured {report['wire_bytes']} wire bytes through "
                f"{chain['provenance'].get('geometry')}"
                f"@{chain['provenance'].get('variant')} at "
                f"{chain['provenance'].get('rate')} Hz")
            emit("RESULT", f"verdict=CAPTURED exit=0 prefix={prefix}")
            return 0

        emit("PHASE", "align")
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
            data["route"] = args.route
            if chain is not None:
                data["chain"] = chain
            if playing:
                data["chain_playing"] = playing
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
