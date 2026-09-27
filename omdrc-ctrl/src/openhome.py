"""A minimal OpenHome Playlist client: what the panel needs to queue and play.

upmpdcli is an OpenHome renderer: its playlist is the MPD queue, and a
control point (BubbleUPnP, Kazoo, the panel) edits it with SOAP calls on the
Playlist service -- DeleteAll, Insert(AfterId, Uri, Metadata), SeekId.  Going
through upmpdcli rather than writing to MPD directly keeps every control
point's view of the playlist in step, and lets the track metadata travel: the
DIDL sent with each Insert is what upmpdcli turns into MPD tags (Title,
Artist, Album, and, with this project's upmpdcli patch, Date and Label).

The renderer is found by SSDP, and only a renderer whose address belongs to
this machine is ever used: another upmpdcli on the LAN must not start playing
because the panel on this box was asked to.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import re
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from xml.etree import ElementTree
from xml.sax.saxutils import escape

PLAYLIST = "urn:av-openhome-org:service:Playlist:1"
SSDP_ADDR = ("239.255.255.250", 1900)

_DEVICE_NS = "{urn:schemas-upnp-org:device-1-0}"
_SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"


class OpenHomeError(RuntimeError):
    """The renderer could not be found, reached, or refused an action."""


@dataclass(frozen=True)
class Renderer:
    location: str          # its description.xml
    name: str              # friendlyName
    control_url: str       # the Playlist service's control URL

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.location).hostname or ""


# ── discovery ───────────────────────────────────────────────────────────────

def is_local_address(host: str) -> bool:
    """True if `host` is one of this machine's addresses: only then can a
    socket be bound to it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind((host, 0))
        return True
    except OSError:
        return False


def ssdp_search(st: str = PLAYLIST, timeout: float = 1.5) -> list[str]:
    """The LOCATIONs of the devices answering an M-SEARCH for `st`.  Asked on
    the default interface and on loopback, so a renderer bound to either is
    heard; the answers of the same device on both are merged."""
    message = ("M-SEARCH * HTTP/1.1\r\n"
               f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
               'MAN: "ssdp:discover"\r\n'
               "MX: 1\r\n"
               f"ST: {st}\r\n\r\n").encode()
    locations: list[str] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(0.2)
        for interface in (None, "127.0.0.1"):
            try:
                if interface:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                 socket.inet_aton(interface))
                s.sendto(message, SSDP_ADDR)
            except OSError:
                continue
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, _ = s.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            match = re.search(rb"^location:\s*(\S+)", data, re.I | re.M)
            if match:
                location = match.group(1).decode("ascii", "replace")
                if location not in locations:
                    locations.append(location)
    return locations


def read_description(location: str, timeout: float = 3.0,
                     service: str = PLAYLIST) -> Renderer | None:
    """The renderer at `location`, or None when it has no `service`."""
    try:
        with urllib.request.urlopen(location, timeout=timeout) as response:
            root = ElementTree.fromstring(response.read(1024 * 1024))
    except (urllib.error.URLError, OSError, ElementTree.ParseError):
        return None
    base = root.findtext(f"{_DEVICE_NS}URLBase") or location
    for device in root.iter(f"{_DEVICE_NS}device"):
        for svc in device.iter(f"{_DEVICE_NS}service"):
            if (svc.findtext(f"{_DEVICE_NS}serviceType") or "").strip() == service:
                control = (svc.findtext(f"{_DEVICE_NS}controlURL") or "").strip()
                name = (device.findtext(f"{_DEVICE_NS}friendlyName") or "").strip()
                return Renderer(location, name, urllib.parse.urljoin(base, control))
    return None


def find_local_renderer(name: str = "", timeout: float = 1.5, search=ssdp_search,
                        describe=read_description, local=is_local_address) -> Renderer:
    """This machine's OpenHome renderer; `name` (upmpdcli's friendlyname)
    picks one when several run here."""
    candidates = []
    for location in search(PLAYLIST, timeout):
        host = urllib.parse.urlsplit(location).hostname or ""
        if not local(host):
            continue
        renderer = describe(location)
        if renderer:
            candidates.append(renderer)
    if name:
        named = [r for r in candidates if r.name.casefold() == name.casefold()]
        candidates = named or candidates
    if not candidates:
        raise OpenHomeError("upmpdcli's OpenHome playlist was not found on this "
                            "machine (is upmpdcli running with openhome = 1?)")
    return candidates[0]


# ── metadata ────────────────────────────────────────────────────────────────

def _duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def didl(uri: str, title: str, *, item_id: str = "", artist: str = "",
         album: str = "", date: str = "", label: str = "", genre: str = "",
         art: str = "", track_number=None, disc_number=None, duration=None,
         mime: str = "application/flac") -> str:
    """One track's DIDL-Lite, the way control points send it with Insert.

    No composer or album-artist element: upmpdcli folds every upnp:artist,
    whatever its role, into MPD's single Artist tag ("Orchestra, Bruckner
    (Composer)"), and the DR versions page searches by that tag."""
    def element(tag: str, value, attrs: str = "") -> str:
        return f"<{tag}{attrs}>{escape(str(value))}</{tag}>" if value not in (None, "") else ""

    res_attrs = f' protocolInfo="http-get:*:{escape(mime)}:*"'
    if _duration(duration):
        res_attrs += f' duration="{_duration(duration)}"'
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        f'<item id="{escape(item_id or uri, {chr(34): "&quot;"})}" parentID="-1" restricted="1">'
        + element("dc:title", title)
        + element("upnp:artist", artist)
        + element("dc:creator", artist)
        + element("upnp:album", album)
        + element("dc:date", date)
        + element("dc:publisher", label)
        + element("upnp:genre", genre)
        + element("upnp:albumArtURI", art)
        + element("upnp:originalTrackNumber", track_number)
        + element("upnp:originalDiscNumber", disc_number)
        + "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
        + f"<res{res_attrs}>{escape(uri)}</res>"
        + "</item></DIDL-Lite>")


# ── the Playlist service ────────────────────────────────────────────────────

class Playlist:
    """SOAP calls on one renderer's Playlist service.

    upmpdcli answers the first action after a quiet spell with "Action
    Failed": MPD has dropped its idle command connection and upmpdcli notices
    only by failing (log: "mpd_run_clear(m_conn) failed: Connection reset by
    peer"), reconnecting for the next call.  So an action that can be repeated
    safely is tried twice, and a failed Insert is retried only after checking
    that the track did not go in anyway -- it must never go in twice."""

    _REPEATABLE = {"DeleteAll", "SeekId", "Play", "IdArray", "Read", "TransportState"}

    def __init__(self, control_url: str, timeout: float = 5.0) -> None:
        self.control_url = control_url
        self.timeout = timeout

    def call(self, action: str, **args) -> dict[str, str]:
        try:
            return self._call(action, args)
        except OpenHomeError:
            if action not in self._REPEATABLE:
                raise
            return self._call(action, args)

    def _call(self, action: str, args: dict) -> dict[str, str]:
        body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args.items())
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="{_SOAP_NS}" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{PLAYLIST}">{body}</u:{action}>'
            "</s:Body></s:Envelope>").encode("utf-8")
        request = urllib.request.Request(self.control_url, data=envelope, headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{PLAYLIST}#{action}"',
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                reply = response.read()
        except urllib.error.HTTPError as error:
            detail = _upnp_error(error.read())
            raise OpenHomeError(f"{action} refused: {detail or f'HTTP {error.code}'}") from error
        except (urllib.error.URLError, OSError) as error:
            raise OpenHomeError(f"cannot reach upmpdcli: "
                                f"{getattr(error, 'reason', error)}") from error
        try:
            root = ElementTree.fromstring(reply)
        except ElementTree.ParseError as error:
            raise OpenHomeError(f"{action}: unreadable answer") from error
        for element in root.iter():
            if element.tag.endswith(f"{action}Response"):
                return {child.tag.split("}")[-1]: child.text or "" for child in element}
        return {}

    def ids(self) -> list[int]:
        """The playlist's track ids, in order."""
        array = self.call("IdArray").get("Array", "")
        raw = base64.b64decode(array) if array else b""
        return list(struct.unpack(f">{len(raw) // 4}I", raw[:len(raw) // 4 * 4]))

    def insert(self, after_id: int, uri: str, metadata: str) -> int:
        args = {"AfterId": after_id, "Uri": uri, "Metadata": metadata}
        try:
            answer = self._call("Insert", args)
        except OpenHomeError:
            # Did it go in anyway?  Then the track after AfterId is ours.
            ids = self.ids()
            if after_id and after_id not in ids:
                raise
            following = ids.index(after_id) + 1 if after_id else 0
            if following < len(ids) and self.call("Read", Id=ids[following]).get("Uri") == uri:
                return ids[following]
            answer = self._call("Insert", args)
        return int(answer.get("NewId", "0"))

    def delete_all(self) -> None:
        self.call("DeleteAll")

    def seek_id(self, track_id: int) -> None:
        self.call("SeekId", Value=track_id)

    def play(self) -> None:
        self.call("Play")

    def transport_state(self) -> str:
        return self.call("TransportState").get("Value", "")


def _upnp_error(body: bytes) -> str:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return ""
    code = description = ""
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        if tag == "errorCode":
            code = element.text or ""
        elif tag == "errorDescription":
            description = element.text or ""
    return " ".join(p for p in (code, description) if p)
