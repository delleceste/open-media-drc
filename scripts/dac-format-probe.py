#!/usr/bin/env python3
"""Probe the sample format and byte alignment the audio chain hands the DAC.

Runs on Linux and FreeBSD and emits the SAME JSON schema on both, so two runs
can be diffed field by field:

    # on each host
    ./scripts/dac-format-probe.py --out dac-format-<os>.json

    # then, with both files on one machine
    ./scripts/dac-format-probe.py --compare dac-format-linux.json \
                                            dac-format-freebsd.json

The question it answers is narrow and physical: which USB alt-setting is the
DAC running, how many bytes per sample are on the wire, how many of those bits
are real, and does every software hop above it agree.  See
doc/DAC-FORMAT-CROSS-OS-PROCEDURE.md for what a difference in each field means.

Nothing here writes to a device, changes a sysctl or restarts a service: it is
safe to run while music is playing, and it is most useful when it is.
"""

import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

SCHEMA = "omdrc-dac-format-probe/1"

# USB descriptor types
DT_CONFIG = 0x02
DT_INTERFACE = 0x04
DT_ENDPOINT = 0x05
DT_CS_INTERFACE = 0x24

# Audio class
AUDIO_CLASS = 0x01
SUBCLASS_AUDIOSTREAMING = 0x02
PROTOCOL_UAC2 = 0x20
AS_SUBTYPE_FORMAT_TYPE = 0x02
FORMAT_TYPE_I = 0x01


def run(cmd, timeout=15):
    """Run a command, return stdout (never raises)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout
    except Exception:
        return ""


# ── USB descriptor parsing (identical logic on both OSes) ────────────────────

def parse_descriptors(blob):
    """Walk a raw USB configuration-descriptor blob.

    Returns the list of AudioStreaming alt-settings with the two fields that
    decide byte alignment: the subslot size (bytes per sample ON THE WIRE) and
    the bit resolution (how many of those bits the DAC actually converts).

    UAC1 and UAC2 lay FORMAT_TYPE_I out differently, so the interface's
    bInterfaceProtocol selects the field offsets.
    """
    alts = []
    cur = None
    # Ordinal, not bConfigurationValue: some firmware (the OKTO DAC8STEREO
    # among them) reports the SAME bConfigurationValue for two distinct
    # configuration descriptors, so the value cannot tell them apart.
    config = 0
    config_value = None
    i = 0
    n = len(blob)
    while i + 1 < n:
        blen = blob[i]
        btype = blob[i + 1]
        if blen < 2:
            break
        d = blob[i:i + blen]

        if btype == DT_CONFIG and blen >= 9:
            if cur is not None:
                alts.append(cur)
                cur = None
            config += 1
            config_value = d[5]

        elif btype == DT_INTERFACE and blen >= 9:
            if cur is not None:
                alts.append(cur)
                cur = None
            iclass, isub, iproto = d[5], d[6], d[7]
            if iclass == AUDIO_CLASS and isub == SUBCLASS_AUDIOSTREAMING:
                cur = {
                    "config": config,
                    "config_value": config_value,
                    "interface": d[2],
                    "alt": d[3],
                    "uac": 2 if iproto == PROTOCOL_UAC2 else 1,
                    "endpoints": [],
                }

        elif btype == DT_CS_INTERFACE and cur is not None and blen >= 4:
            if d[2] == AS_SUBTYPE_FORMAT_TYPE and d[3] == FORMAT_TYPE_I:
                if cur["uac"] == 2 and blen >= 6:
                    # UAC2: bFormatType, bSubslotSize, bBitResolution
                    cur["subslot_bytes"] = d[4]
                    cur["bit_resolution"] = d[5]
                elif blen >= 7:
                    # UAC1: bFormatType, bNrChannels, bSubframeSize, bBitResolution
                    cur["channels"] = d[4]
                    cur["subslot_bytes"] = d[5]
                    cur["bit_resolution"] = d[6]
            elif d[2] == 0x01 and cur["uac"] == 2 and blen >= 16:
                # UAC2 AS_GENERAL: bTerminalLink, bmControls, bFormatType,
                # bmFormats[4], bNrChannels, bmChannelConfig[4], iChannelNames
                cur["bm_formats"] = int.from_bytes(d[6:10], "little")
                cur["channels"] = d[10]

        elif btype == DT_ENDPOINT and cur is not None and blen >= 7:
            cur["endpoints"].append({
                "address": "0x%02x" % d[2],
                "dir": "IN" if d[2] & 0x80 else "OUT",
                "attributes": "0x%02x" % d[3],
                "max_packet_size": d[4] | (d[5] << 8),
            })

        i += blen

    if cur is not None:
        alts.append(cur)

    for a in alts:
        a["container"] = container_name(a.get("subslot_bytes"))
    return alts


def container_name(subslot):
    """The ALSA/host name for a wire container of N bytes per sample."""
    return {1: "S8", 2: "S16_LE", 3: "S24_3LE", 4: "S32_LE"}.get(subslot, "unknown")


# ── Linux ────────────────────────────────────────────────────────────────────

def linux_usb_devices():
    """Every USB device with an audio interface, with its raw descriptors."""
    out = []
    for d in sorted(glob.glob("/sys/bus/usb/devices/*/")):
        desc = os.path.join(d, "descriptors")
        if not os.path.exists(desc):
            continue
        try:
            with open(desc, "rb") as f:
                blob = f.read()
        except OSError:
            continue
        alts = parse_descriptors(blob)
        if not alts:
            continue

        def rd(name):
            try:
                with open(os.path.join(d, name)) as f:
                    return f.read().strip()
            except OSError:
                return None

        out.append({
            "usb_id": "%s:%s" % (rd("idVendor"), rd("idProduct")),
            "product": rd("product"),
            "manufacturer": rd("manufacturer"),
            "serial": rd("serial"),
            "bcd_device": rd("bcdDevice"),
            "speed": rd("speed"),
            "alt_settings": alts,
        })
    return out


def linux_cards():
    """ALSA cards, the format each open substream negotiated, and USB alt."""
    cards = []
    try:
        with open("/proc/asound/cards") as f:
            text = f.read()
    except OSError:
        return cards

    for m in re.finditer(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s*(.+)$", text, re.M):
        idx, cid, desc = int(m.group(1)), m.group(2), m.group(3).strip()
        card = {"index": idx, "id": cid, "description": desc, "streams": []}

        for hp in sorted(glob.glob("/proc/asound/card%d/pcm*/sub*/hw_params" % idx)):
            try:
                with open(hp) as f:
                    body = f.read().strip()
            except OSError:
                continue
            if body == "closed" or not body:
                continue
            kv = {}
            for line in body.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    kv[k.strip()] = v.strip()
            node = hp.replace("/proc/asound/", "").replace("/hw_params", "")
            st = {}
            try:
                with open(hp.replace("hw_params", "status")) as f:
                    for line in f:
                        if line.startswith("owner_pid"):
                            st["owner_pid"] = int(line.split(":", 1)[1])
                        elif line.startswith("state"):
                            st["state"] = line.split(":", 1)[1].strip()
            except OSError:
                pass
            rate = kv.get("rate", "")
            card["streams"].append({
                "node": node,
                "direction": "playback" if "p/sub" in node else "capture",
                "format": kv.get("format"),
                "channels": int(kv["channels"]) if kv.get("channels", "").isdigit() else kv.get("channels"),
                "rate": int(rate.split()[0]) if rate.split() and rate.split()[0].isdigit() else rate,
                "access": kv.get("access"),
                "period_size": kv.get("period_size"),
                "buffer_size": kv.get("buffer_size"),
                "state": st.get("state"),
                "owner_pid": st.get("owner_pid"),
                "owner_cmd": pid_cmd(st.get("owner_pid")),
            })

        # USB alt-setting actually selected, from the usb-audio stream file
        for sf in sorted(glob.glob("/proc/asound/card%d/stream*" % idx)):
            try:
                with open(sf) as f:
                    body = f.read()
            except OSError:
                continue
            sel = {}
            mm = re.search(r"Interface\s*=\s*(\d+)", body)
            if mm:
                sel["interface"] = int(mm.group(1))
            mm = re.search(r"Altset\s*=\s*(\d+)", body)
            if mm:
                sel["alt"] = int(mm.group(1))
            mm = re.search(r"Packet Size\s*=\s*(\d+)", body)
            if mm:
                sel["packet_size"] = int(mm.group(1))
            mm = re.search(r"Momentary freq\s*=\s*(\d+)", body)
            if mm:
                sel["momentary_freq_hz"] = int(mm.group(1))
            if sel:
                sel["source"] = os.path.basename(sf)
                card.setdefault("usb_selected", []).append(sel)

        cards.append(card)
    return cards


# ── FreeBSD ──────────────────────────────────────────────────────────────────

AFMT = {
    0x00000010: "S16_LE", 0x00000020: "S16_BE",
    0x00001000: "S32_LE", 0x00002000: "S32_BE",
    0x00010000: "S24_LE", 0x00020000: "S24_BE",
    0x00040000: "S24_PACKED",
    0x00000008: "U8", 0x00000040: "S8",
}


# sys/dev/sound/pcm/sound.h, verified against 15.1-RELEASE-p2:
#     AFMT_ENCODING_MASK   0xf00fffff
#     AFMT_CHANNEL_MASK    0x07f00000   AFMT_CHANNEL_SHIFT    20
#     AFMT_EXTCHANNEL_MASK 0x08000000   AFMT_EXTCHANNEL_SHIFT 27
# The channel count sits at bits 20-26, NOT 24-28: a stereo S32_LE stream is
# 0x00201000, and shifting by 24 decodes it as zero channels.
AFMT_ENCODING_MASK = 0xF00FFFFF
AFMT_CHANNEL_MASK = 0x07F00000
AFMT_CHANNEL_SHIFT = 20
AFMT_EXTCHANNEL_MASK = 0x08000000
AFMT_EXTCHANNEL_SHIFT = 27


def afmt_decode(v):
    """FreeBSD packs the channel count into the middle bits of an AFMT word."""
    enc = v & AFMT_ENCODING_MASK
    ch = (v & AFMT_CHANNEL_MASK) >> AFMT_CHANNEL_SHIFT
    ch += (v & AFMT_EXTCHANNEL_MASK) >> AFMT_EXTCHANNEL_SHIFT
    return {
        "raw": "0x%08x" % v,
        "encoding": AFMT.get(enc, "0x%08x" % enc),
        "channels": ch or None,
        "wire_bytes": {"S16_LE": 2, "S16_BE": 2, "S24_PACKED": 3,
                       "S24_LE": 3, "S24_BE": 3,
                       "S32_LE": 4, "S32_BE": 4}.get(AFMT.get(enc, ""), None),
    }


def usbconfig(args):
    """usbconfig(8), which needs root to see any device.

    Unprivileged it does not fail loudly — it prints "No device match or lack
    of permissions." to stdout and exits 0, so the descriptor walk silently
    produced an empty alt table and `selected_alt: null` looked like a FreeBSD
    limitation rather than a missing sudo.  Try direct first (in case this is
    already root), then non-interactive sudo."""
    out = run(["usbconfig"] + args)
    if out and "lack of permissions" not in out:
        return out
    if os.geteuid() != 0 and shutil.which("sudo"):
        out = run(["sudo", "-n", "usbconfig"] + args)
        if out and "lack of permissions" not in out:
            return out
    return ""


def freebsd_usb_devices():
    """Raw descriptors via usbconfig, parsed by the same walker as Linux."""
    out = []
    if not shutil.which("usbconfig"):
        return out
    listing = usbconfig(["list"]) or usbconfig([])
    if not listing:
        return [{"error": "usbconfig returned nothing — it needs root; "
                          "run the probe with sudo, or allow "
                          "`sudo -n usbconfig`"}]
    for line in listing.splitlines():
        m = re.match(r"(ugen\S+):\s*<(.*?)>", line)
        if not m:
            continue
        dev, label = m.group(1), m.group(2)
        dump = usbconfig(["-d", dev, "dump_all_desc"])
        if not dump:
            continue
        blob = usbconfig_raw_bytes(dump)
        alts = parse_descriptors(blob) if blob else []
        if not alts:
            continue
        entry = {
            "ugen": dev,
            "product": label,
            "alt_settings": alts,
            "raw_dump_bytes": len(blob),
        }
        m2 = re.search(r"idVendor\s*=\s*0x([0-9a-fA-F]{4})", dump)
        m3 = re.search(r"idProduct\s*=\s*0x([0-9a-fA-F]{4})", dump)
        if m2 and m3:
            entry["usb_id"] = "%s:%s" % (m2.group(1).lower(), m3.group(1).lower())
        m4 = re.search(r"bcdDevice\s*=\s*0x([0-9a-fA-F]+)", dump)
        if m4:
            v = int(m4.group(1), 16)
            entry["bcd_device"] = "%x.%02x" % (v >> 8, v & 0xFF)
        out.append(entry)
    return out


# usbconfig(8) prints a descriptor one of two ways, and the walker needs both:
#
#   * class-specific ("Additional Descriptor") ones come with a `RAW dump:`
#     block holding their exact bytes;
#   * STANDARD ones — device, configuration, interface, endpoint — are printed
#     only as decoded `name = 0xVALUE` fields, with no hex at all.
#
# Concatenating just the RAW blocks therefore yields a blob with every
# interface and endpoint descriptor missing.  The walker never sees an
# AudioStreaming interface to open an alt-setting on, returns nothing, and the
# probe reports `alt_settings: []` as if FreeBSD could not read descriptors —
# which is what it did before this.  So the standard ones are re-encoded from
# their printed fields and spliced back in, in order, giving the same blob
# Linux hands over in `/sys/bus/usb/devices/*/descriptors`.
#
# Field widths follow USB naming: `w`/`bcd`/`id` are 16-bit little-endian,
# everything else ("b", "bm", "i") is one byte.  Each descriptor is then cut or
# zero-padded to its own bLength, because usbconfig prints trailing fields that
# a short descriptor does not actually carry — a 7-byte endpoint descriptor is
# printed with bRefresh and bSynchAddress, and emitting those 9 bytes would
# desynchronise every descriptor after it.

_FIELD = re.compile(r"^\s*([A-Za-z]\w*)\s*=\s*0x([0-9a-fA-F]+)")


def _encode_fields(fields):
    """Re-encode one standard descriptor from its printed fields."""
    if not fields or fields[0][0] != "bLength":
        return b""
    out = bytearray()
    for name, value in fields:
        width = 2 if (name.startswith("w") or name.startswith("bcd")
                      or name.startswith("id")) else 1
        out.extend(int(value, 16).to_bytes(width, "little", signed=False)
                   if int(value, 16) < (1 << (8 * width))
                   else (int(value, 16) & ((1 << (8 * width)) - 1)
                         ).to_bytes(width, "little"))
    blen = int(fields[0][1], 16)
    if blen < 2:
        return b""
    return bytes(out[:blen].ljust(blen, b"\x00"))


def usbconfig_raw_bytes(dump):
    """Rebuild the configuration blob from `usbconfig -d ugenX.Y dump_all_desc`.

    Walks the output once, emitting each descriptor in the order printed:
    the bytes of a `RAW dump:` block where one is given, and otherwise the
    re-encoded fields of the standard descriptor that precedes it.
    """
    data = bytearray()
    fields = []
    in_raw = False

    def flush():
        nonlocal fields
        if fields:
            data.extend(_encode_fields(fields))
            fields = []

    for line in dump.splitlines():
        if "RAW dump" in line:
            # The raw bytes supersede the fields printed just above them.
            fields = []
            in_raw = True
            continue
        if in_raw:
            body = line.split("|", 1)[1] if "|" in line else line
            hexes = re.findall(r"0x([0-9a-fA-F]{2})\b", body)
            if hexes:
                data.extend(int(h, 16) for h in hexes)
                continue
            in_raw = False
            # fall through: this line may already start the next descriptor
        m = _FIELD.match(line)
        if m:
            if m.group(1) == "bLength":
                flush()
            fields.append((m.group(1), m.group(2)))
        elif line.strip() and not line.startswith(" " * 2):
            flush()
    flush()
    return bytes(data)


def freebsd_pcm_state():
    """Open streams and their negotiated format, from sndstat + sysctl."""
    state = {"sndstat": None, "verbose_hint": None, "devices": [], "streams": []}
    try:
        with open("/dev/sndstat") as f:
            state["sndstat"] = f.read()
    except OSError as e:
        state["sndstat"] = "unreadable: %s" % e

    text = state["sndstat"] or ""
    if "spd " not in text:
        state["verbose_hint"] = (
            "sndstat carries no per-channel detail; run "
            "`sysctl hw.snd.verbose=2` and probe again")

    for m in re.finditer(r"^(pcm\d+):\s*<(.+?)>(.*)$", text, re.M):
        state["devices"].append({
            "unit": m.group(1), "description": m.group(2),
            "flags": m.group(3).strip(),
        })

    # Verbose sndstat channel lines.  Two spellings are in the wild and both
    # appear here, because the file is a debugging aid with no stable format:
    #
    #   [pcm0:play:dsp0.p0]: spd 192000/192000, fmt 0x02001000/0x02001000, ...
    #   [dsp1.play.0]: spd 192000, fmt 0x00201000, flags ..., pid 2067 (brutefir)
    #
    # The second is what 15.1-RELEASE emits, and it does not name its pcm unit
    # on the channel line — the enclosing `pcmN:` header does — so the unit is
    # carried down rather than matched per line.
    #
    # The feeder chain that follows each channel is the point of reading this
    # at all.  A bit-perfect path is
    #     {userland} -> feeder_root(0x00201000) -> {hardware}
    # while a converted one names every stage it inserts
    #     ... feeder_root -> feeder_format(...) -> feeder_volume(...) -> ...
    # No amount of format agreement proves transparency if a feeder sits in
    # the path, so the chain is recorded verbatim alongside the format.
    unit = None
    pending = None

    def flush(entry):
        if entry:
            state["streams"].append(entry)

    for line in text.splitlines():
        head = re.match(r"^(pcm\d+):", line)
        if head:
            unit = head.group(1)
        m = re.search(
            r"\[(?:(pcm\d+):(play|rec):(\S+?)|(\S+?)\.(play|rec(?:ord)?)\.(\d+))\]:"
            r"\s*spd\s*(\d+)(?:/(\d+))?,\s*"
            r"fmt\s*0x([0-9a-fA-F]+)(?:/0x([0-9a-fA-F]+))?", line)
        if m:
            flush(pending)
            if m.group(1):
                owner_unit, direction, node = m.group(1), m.group(2), m.group(3)
            else:
                owner_unit = unit
                direction = m.group(5)
                node = "%s.%s.%s" % (m.group(4), m.group(5), m.group(6))
            pending = {
                "unit": owner_unit,
                "direction": "playback" if direction == "play" else "capture",
                "node": node,
                "rate": int(m.group(7)),
                "hw_rate": int(m.group(8)) if m.group(8) else None,
                "format": afmt_decode(int(m.group(9), 16)),
                "hw_format": (afmt_decode(int(m.group(10), 16))
                              if m.group(10) else None),
            }
            pid = re.search(r"pid\s+(\d+)\s*\(([^)]*)\)", line)
            if pid:
                pending["owner_pid"] = int(pid.group(1))
                pending["owner"] = pid.group(2)
            continue
        if pending is None:
            continue
        if ("{userland}" in line or "{hardware}" in line) \
                and "feeder_chain" not in pending:
            chain = line.strip()
            pending["feeder_chain"] = chain
            stages = re.findall(r"(feeder_\w+)", chain)
            pending["feeders"] = stages
            # feeder_root is the channel itself, not a conversion stage.
            pending["converting_feeders"] = [f for f in stages
                                             if f != "feeder_root"]
            pending["transparent"] = not pending["converting_feeders"]
        elif "channel flags=" in line and "channel_flags" not in pending:
            cf = re.search(r"channel flags=0x[0-9a-fA-F]+<([^>]*)>", line)
            if cf:
                pending["channel_flags"] = cf.group(1).split(",")
        elif line.strip().startswith("interrupts"):
            st = re.search(r"interrupts\s+(\d+),\s*(underruns|overruns)\s+(\d+)",
                           line)
            if st:
                pending["interrupts"] = int(st.group(1))
                pending[st.group(2)] = int(st.group(3))
    flush(pending)

    for unit in {d["unit"] for d in state["devices"]}:
        knobs = {}
        for knob in ("bitperfect", "play.vchans", "rec.vchans",
                     "feedback_rate", "play.vchanformat", "play.vchanrate"):
            v = run(["sysctl", "-n", "dev.%s.%s" % (unit, knob)]).strip()
            if v:
                knobs[knob] = v
        if knobs:
            state.setdefault("sysctl", {})[unit] = knobs

    return state


def freebsd_uaudio_attach():
    """The uaudio attach lines say which format uaudio locked in."""
    lines = [l for l in run(["dmesg"]).splitlines()
             if re.search(r"uaudio|pcm\d+:", l)]
    return lines[-60:]


# ── shared ───────────────────────────────────────────────────────────────────

def pid_cmd(pid):
    if not pid:
        return None
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return f.read().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        pass
    out = run(["ps", "-o", "args=", "-p", str(pid)]).strip()
    return out or None


def audio_processes():
    out = run(["ps", "-axo", "pid,user,args"]) or run(["ps", "-eo", "pid,user,args"])
    keep = []
    for line in out.splitlines():
        if re.search(r"brutefir|virtual_oss|\bmpd\b|musicpd|mpv|upmpdcli", line) \
                and "grep" not in line and "dac-format-probe" not in line:
            keep.append(line.strip())
    return keep


def brutefir_settings():
    """The three brutefir facts that decide alignment: I/O device and sample
    format on each side, plus whether dither is on when it narrows the word."""
    paths = [
        os.path.expanduser("~/.config/BruteFIR/brutefir_defaults.conf"),
        os.path.expanduser("~/.brutefir_defaults"),
    ]
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                text = f.read()
        except OSError:
            continue
        clean = re.sub(r"#.*", "", text)

        def block(name):
            m = re.search(r"\b%s\s*\{(.*?)\n\}\s*;" % name, clean, re.S)
            return m.group(1) if m else ""

        res = {"path": p}
        for side in ("input", "output"):
            b = block(side)
            dev = re.search(r'device:\s*"(\w+)"\s*\{\s*device:\s*"([^"]+)"', b)
            smp = re.search(r'sample:\s*"([^"]+)"', b)
            res[side] = {
                "io_module": dev.group(1) if dev else None,
                "device": dev.group(2) if dev else None,
                "sample": smp.group(1) if smp else None,
            }
        d = re.search(r"dither:\s*(true|false)", block("output"))
        res["output"]["dither"] = d.group(1) if d else None
        for key in ("float_bits", "filter_length", "sampling_rate", "safety_limit"):
            m = re.search(r"\b%s:\s*([^;]+);" % key, clean)
            if m:
                res[key] = m.group(1).strip()
        return res
    return {"path": None, "note": "no brutefir defaults file found"}


def probe():
    sysname = platform.system()
    data = {
        "schema": SCHEMA,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "os": {
            "name": sysname,
            "release": platform.release(),
            "version": platform.version(),
        },
        "hostname": platform.node(),
        "audio_processes": audio_processes(),
        "brutefir": brutefir_settings(),
    }

    if sysname == "Linux":
        data["usb_audio_devices"] = linux_usb_devices()
        data["alsa_cards"] = linux_cards()
    elif sysname == "FreeBSD":
        data["usb_audio_devices"] = freebsd_usb_devices()
        data["oss"] = freebsd_pcm_state()
        data["uaudio_dmesg"] = freebsd_uaudio_attach()
    else:
        data["error"] = "unsupported OS: %s" % sysname

    data["summary"] = summarize(data)
    return data


def summarize(data):
    """The handful of fields that must agree across the two operating systems."""
    s = {"dacs": []}
    for dev in data.get("usb_audio_devices", []):
        alts = [a for a in dev.get("alt_settings", [])
                if a.get("subslot_bytes") and
                any(e["dir"] == "OUT" for e in a.get("endpoints", []))]
        if not alts:
            continue
        # A device may expose several configurations carrying the same
        # streaming alts; summarise the lowest-numbered one only.
        cfgs = {a.get("config") for a in alts}
        if len(cfgs) > 1:
            keep = min(c for c in cfgs if c is not None)
            alts = [a for a in alts if a.get("config") == keep]
        s["dacs"].append({
            "usb_id": dev.get("usb_id"),
            "product": dev.get("product"),
            "playback_alts": [
                {"alt": a["alt"], "subslot_bytes": a["subslot_bytes"],
                 "bit_resolution": a["bit_resolution"],
                 "container": a["container"],
                 "bm_formats": ("0x%08x" % a["bm_formats"]) if "bm_formats" in a else None}
                for a in sorted(alts, key=lambda x: x["alt"])
            ],
        })

    sel = None
    if data["os"]["name"] == "Linux":
        for card in data.get("alsa_cards", []):
            for u in card.get("usb_selected", []):
                if "alt" in u:
                    sel = {"alt": u["alt"], "interface": u.get("interface"),
                           "card": card["id"]}
        fmts = []
        for card in data.get("alsa_cards", []):
            for st in card.get("streams", []):
                fmts.append({"where": "%s/%s" % (card["id"], st["node"]),
                             "format": st["format"], "rate": st["rate"],
                             "channels": st["channels"],
                             "owner": (st.get("owner_cmd") or "")[:60]})
        s["open_streams"] = fmts
    else:
        # Every pcm device lists ALL its channels in verbose sndstat, open or
        # not, each carrying the format it would default to (48 kHz S16_LE on
        # an idle one).  Counting those as open streams made the second sound
        # card — an idle ESI U24XL sitting at its default — look like a rate
        # and format disagreement inside the playback chain, which is the
        # suite's hard-stop signal for "a resampler is running".  A channel is
        # open only if the kernel says it is: an owner pid, or RUNNING/BUSY in
        # its channel flags.  The rest are recorded, but not as open streams.
        def is_open(st):
            flags = set(st.get("channel_flags") or [])
            return bool(st.get("owner_pid")) or bool(flags & {"RUNNING", "BUSY"})

        def describe(st):
            entry = {"where": "%s/%s" % (st["unit"], st["node"]),
                     "format": st["format"]["encoding"],
                     "wire_bytes": st["format"]["wire_bytes"],
                     "rate": st["rate"],
                     "channels": st["format"]["channels"],
                     "direction": st.get("direction")}
            for key in ("owner", "owner_pid", "feeder_chain",
                        "converting_feeders", "transparent", "channel_flags"):
                if st.get(key) is not None:
                    entry[key] = st[key]
            return entry

        streams = data.get("oss", {}).get("streams", [])
        s["open_streams"] = [describe(st) for st in streams if is_open(st)]
        s["idle_channels"] = [describe(st) for st in streams if not is_open(st)]
    s["selected_alt"] = sel

    rates = {st.get("rate") for st in s["open_streams"] if st.get("rate")}
    s["all_open_streams_same_rate"] = (len(rates) <= 1)
    s["open_stream_rates"] = sorted(r for r in rates if isinstance(r, int))
    containers = {st.get("format") for st in s["open_streams"] if st.get("format")}
    s["open_stream_formats"] = sorted(c for c in containers if c)
    s["all_open_streams_same_format"] = (len(containers) <= 1)
    return s


# ── comparison ───────────────────────────────────────────────────────────────

# Fields that describe the DEVICE and must be identical on both hosts, versus
# fields that describe the HOST and are expected to differ.
def compare(a_path, b_path):
    with open(a_path) as f:
        a = json.load(f)
    with open(b_path) as f:
        b = json.load(f)

    def tag(d):
        return "%s/%s" % (d["os"]["name"].lower(), d["os"]["release"])

    ta, tb = tag(a), tag(b)
    print("A = %-28s %s" % (ta, a_path))
    print("B = %-28s %s" % (tb, b_path))
    print()

    problems = []
    notes = []

    da = {d["usb_id"]: d for d in a["summary"]["dacs"] if d.get("usb_id")}
    db = {d["usb_id"]: d for d in b["summary"]["dacs"] if d.get("usb_id")}
    common = sorted(set(da) & set(db))
    if not common:
        problems.append("no USB audio device in common — compare the same DAC")

    for uid in common:
        print("── DAC %s (%s)" % (uid, da[uid].get("product") or "?"))
        pa = {x["alt"]: x for x in da[uid]["playback_alts"]}
        pb = {x["alt"]: x for x in db[uid]["playback_alts"]}
        for alt in sorted(set(pa) | set(pb)):
            xa, xb = pa.get(alt), pb.get(alt)
            if xa != xb:
                problems.append(
                    "alt %s descriptors differ: A=%s B=%s" % (alt, xa, xb))
                print("   alt %-2s  DIFFER  A=%s  B=%s" % (alt, xa, xb))
            else:
                print("   alt %-2s  same    %s bytes/sample, %s valid bits (%s)"
                      % (alt, xa["subslot_bytes"], xa["bit_resolution"],
                         xa["container"]))
        print()

    sa = a["summary"].get("selected_alt")
    sb = b["summary"].get("selected_alt")
    print("── selected alt-setting")
    print("   A: %s" % (sa or "not reported by this OS"))
    print("   B: %s" % (sb or "not reported by this OS"))
    if sa and sb and sa.get("alt") != sb.get("alt"):
        problems.append("different alt-setting selected: A=%s B=%s"
                        % (sa.get("alt"), sb.get("alt")))
    elif not (sa and sb):
        notes.append("alt-setting not directly reported on one host — confirm "
                     "from the uaudio_dmesg attach line (see the procedure doc)")
    print()

    print("── open streams (host-specific node names are expected to differ)")
    for label, d in (("A", a), ("B", b)):
        print("   %s: rates=%s formats=%s" % (
            label, d["summary"]["open_stream_rates"],
            d["summary"]["open_stream_formats"]))
        for st in d["summary"]["open_streams"]:
            print("      %-34s %-10s %-8s %sch" % (
                st.get("where"), st.get("format"), st.get("rate"),
                st.get("channels")))
    print()

    for label, d in (("A", a), ("B", b)):
        if not d["summary"]["all_open_streams_same_rate"]:
            problems.append("%s: open streams disagree on rate %s — a resampler "
                            "is running somewhere in the chain"
                            % (label, d["summary"]["open_stream_rates"]))
        if not d["summary"]["all_open_streams_same_format"]:
            problems.append("%s: open streams disagree on format %s — a "
                            "converter is running somewhere in the chain"
                            % (label, d["summary"]["open_stream_formats"]))

    fa = set(a["summary"]["open_stream_formats"])
    fb = set(b["summary"]["open_stream_formats"])
    wire = {"S32_LE": 4, "S24_LE": 3, "S24_3LE": 3, "S24_PACKED": 3,
            "S16_LE": 2}
    wa = {wire.get(x) for x in fa}
    wb = {wire.get(x) for x in fb}
    print("── wire container width")
    print("   A: %s -> %s bytes/sample" % (sorted(fa), sorted(x for x in wa if x)))
    print("   B: %s -> %s bytes/sample" % (sorted(fb), sorted(x for x in wb if x)))
    if wa and wb and wa != wb:
        problems.append(
            "the two hosts are using DIFFERENT sample widths (%s vs %s bytes). "
            "This is the byte-alignment failure mode: a stride mismatch shifts "
            "every sample after the first." % (sorted(wa), sorted(wb)))
    print()

    for label, d in (("A", a), ("B", b)):
        bf = d.get("brutefir", {})
        if bf.get("path"):
            print("── brutefir (%s)" % label)
            print("   in  %-6s %-14s %s" % (bf["input"]["io_module"],
                                            bf["input"]["device"],
                                            bf["input"]["sample"]))
            print("   out %-6s %-14s %s  dither=%s" % (
                bf["output"]["io_module"], bf["output"]["device"],
                bf["output"]["sample"], bf["output"].get("dither")))
            print("   float_bits=%s filter_length=%s" % (
                bf.get("float_bits"), bf.get("filter_length")))
    print()

    sa_ = a["brutefir"].get("input", {}).get("sample")
    sb_ = b["brutefir"].get("input", {}).get("sample")
    oa = a["brutefir"].get("output", {}).get("sample")
    ob = b["brutefir"].get("output", {}).get("sample")
    if sa_ and sb_ and sa_ != sb_:
        problems.append("brutefir input sample format differs: A=%s B=%s"
                        % (sa_, sb_))
    if oa and ob and oa != ob:
        problems.append("brutefir output sample format differs: A=%s B=%s"
                        % (oa, ob))

    print("═" * 66)
    if problems:
        print("MISMATCH — %d finding(s):" % len(problems))
        for p in problems:
            print("  * %s" % p)
    else:
        print("MATCH — both hosts present the DAC the same alt-setting, the "
              "same wire container and a self-consistent chain.")
    for n in notes:
        print("  note: %s" % n)
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser(
        description="Probe / compare the sample format handed to the DAC.")
    ap.add_argument("--out", help="write JSON here (default: stdout)")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"),
                    help="compare two probe files instead of probing")
    args = ap.parse_args()

    if args.compare:
        return compare(*args.compare)

    data = probe()
    text = json.dumps(data, indent=2, sort_keys=False)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text + "\n")
        print("wrote %s (%d bytes)" % (args.out, len(text) + 1), file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
