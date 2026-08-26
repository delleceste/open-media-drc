#!/usr/bin/env python3
"""
Decide, from USB descriptors alone, whether a device is affected by the
uaudio(4) shared-clock / feedback follow-up patches -- and how.

The point of this tool is that neither patch is device-gated: they key off
descriptor facts, not VID/PID.  So "does this fix only my DAC?" is answerable
for ANY device, including one you do not own, from a descriptor dump.

It re-implements the driver's own clock discovery (uaudio20_mixer_find_clocks_sub)
so the answer matches what uaudio(4) will actually do.

Usage:
  uaudio-affects.py                      # every USB device on this host (needs sudo for usbconfig)
  uaudio-affects.py -d ugen0.3           # one device
  uaudio-affects.py --dump FILE          # a saved raw config descriptor (hex or binary)
  uaudio-affects.py -d ugen0.3 --save-dump okto.bin
  uaudio-affects.py --probe -d ugen0.3   # also probe GET_CUR/RANGE on the clocks (live device)
"""

import argparse
import re
import subprocess
import sys

# --- descriptor constants -------------------------------------------------
UDESC_INTERFACE, UDESC_ENDPOINT = 0x04, 0x05
UDESC_CS_INTERFACE, UDESC_CS_ENDPOINT = 0x24, 0x25
AC_HEADER, AC_INPUT_TERMINAL, AC_OUTPUT_TERMINAL = 0x01, 0x02, 0x03
AC_CLOCK_SRC, AC_CLOCK_SEL, AC_CLOCK_MUL = 0x0A, 0x0B, 0x0C
AS_GENERAL, AS_FORMAT_TYPE = 0x01, 0x02
UICLASS_AUDIO = 0x01
UISUBCLASS_AUDIOCONTROL, UISUBCLASS_AUDIOSTREAM = 0x01, 0x02

UE_XFERTYPE, UE_ISOCHRONOUS = 0x03, 0x01
UE_ISO_TYPE, UE_ISO_ASYNC = 0x0C, 0x04
UE_ISO_USAGE, UE_ISO_USAGE_FEEDBACK = 0x30, 0x10


def run_usbconfig(dev, *args):
    cmd = ["usbconfig"] + (["-d", dev] if dev else []) + list(args)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        sys.exit("usbconfig not found")
    if out.returncode != 0 or "lack of permissions" in out.stdout:
        cmd = ["sudo", "-n"] + cmd
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return out.stdout


def do_request(dev, bmreq, breq, wval, widx, wlen):
    txt = run_usbconfig(dev, "do_request", hex(bmreq), hex(breq),
                        hex(wval), hex(widx), str(wlen))
    m = re.search(r"REQUEST = <([^>]*)>", txt)
    if not m or not m.group(1).strip():
        return None
    return bytes(int(x, 16) for x in m.group(1).split())


def fetch_config(dev):
    head = do_request(dev, 0x80, 0x06, 0x0200, 0x0000, 9)
    if not head or len(head) < 9:
        return None
    total = head[2] | (head[3] << 8)
    return do_request(dev, 0x80, 0x06, 0x0200, 0x0000, total)


def walk(buf):
    i = 0
    while i + 1 < len(buf):
        ln = buf[i]
        if ln < 2 or i + ln > len(buf):
            break
        yield buf[i:i + ln]
        i += ln


class Device:
    def __init__(self, buf):
        self.buf = buf
        self.uac = None            # 0x00 = UAC1, 0x20 = UAC2, 0x30 = UAC3
        self.clock_srcs = {}       # id -> bmControls
        self.selectors = {}        # id -> [source ids]
        self.multipliers = {}      # id -> source id
        self.terminals = []        # (kind, id, wTerminalType, bCSourceID)
        self.streams = []          # dicts describing each AS alt-setting
        self._parse()

    def _parse(self):
        cur_if = None
        alt = None
        for d in walk(self.buf):
            t = d[1]
            if t == UDESC_INTERFACE and len(d) >= 9:
                cls, sub, proto = d[5], d[6], d[7]
                cur_if = (d[2], sub, proto)          # (ifnum, subclass, protocol)
                alt = None
                if cls == UICLASS_AUDIO and sub == UISUBCLASS_AUDIOCONTROL:
                    self.uac = proto
                if cls == UICLASS_AUDIO and sub == UISUBCLASS_AUDIOSTREAM:
                    alt = {"iface": d[2], "alt": d[3], "neps": d[4],
                           "data_ep": None, "async": False, "sync_ep": None,
                           "channels": None, "bits": None, "terminal": None}
                    if d[3] != 0:
                        self.streams.append(alt)
            elif t == UDESC_CS_INTERFACE and len(d) >= 3:
                st = d[2]
                if cur_if and cur_if[1] == UISUBCLASS_AUDIOCONTROL:
                    if st == AC_CLOCK_SRC and len(d) >= 6:
                        self.clock_srcs[d[3]] = d[5]
                    elif st == AC_CLOCK_SEL and len(d) >= 5:
                        n = d[4]
                        self.selectors[d[3]] = list(d[5:5 + n])
                    elif st == AC_CLOCK_MUL and len(d) >= 5:
                        self.multipliers[d[3]] = d[4]
                    elif st == AC_INPUT_TERMINAL and len(d) >= 8:
                        self.terminals.append(("in", d[3], d[4] | (d[5] << 8), d[7]))
                    elif st == AC_OUTPUT_TERMINAL and len(d) >= 9:
                        self.terminals.append(("out", d[3], d[4] | (d[5] << 8), d[8]))
                elif alt is not None:
                    if st == AS_GENERAL and len(d) >= 12:
                        alt["terminal"] = d[3]
                        alt["channels"] = d[11]
                    elif st == AS_FORMAT_TYPE and len(d) >= 6:
                        alt["bits"] = d[5]
            elif t == UDESC_ENDPOINT and len(d) >= 7 and alt is not None:
                attr, addr = d[3], d[2]
                if attr & UE_XFERTYPE != UE_ISOCHRONOUS:
                    continue
                if alt["data_ep"] is None:
                    alt["data_ep"] = addr
                    alt["async"] = (attr & UE_ISO_TYPE) == UE_ISO_ASYNC
                elif (attr & UE_ISO_USAGE) == UE_ISO_USAGE_FEEDBACK and \
                        (addr & 0x80) != (alt["data_ep"] & 0x80):
                    alt["sync_ep"] = addr

    # --- driver-equivalent clock discovery --------------------------------
    def _resolve(self, cid, seen=None):
        """Expand a clock id to the set of Clock Source ids behind it."""
        seen = seen or set()
        if cid in seen:
            return set()
        seen.add(cid)
        if cid in self.clock_srcs:
            return {cid}
        out = set()
        for s in self.selectors.get(cid, []):
            out |= self._resolve(s, seen)
        if cid in self.multipliers:
            out |= self._resolve(self.multipliers[cid], seen)
        return out

    def clock_bitmaps(self):
        """Return (playback clock ids, capture clock ids), as uaudio computes them.

        uaudio keys the direction off the terminal kind: an OUTPUT terminal
        feeds bit_output (playback), an INPUT terminal feeds bit_input.
        """
        out, inp = set(), set()
        for kind, _tid, _ttype, csrc in self.terminals:
            (out if kind == "out" else inp).update(self._resolve(csrc))
        return out, inp

    def play_alts(self):
        return [a for a in self.streams if a["data_ep"] is not None
                and not (a["data_ep"] & 0x80)]

    def rec_alts(self):
        return [a for a in self.streams if a["data_ep"] is not None
                and (a["data_ep"] & 0x80)]


def classify(dev_name, d, probe_dev=None):
    L = []
    uacname = {0x00: "UAC1", 0x20: "UAC2", 0x30: "UAC3"}.get(d.uac, "unknown(0x%02x)" % (d.uac or 0))
    play, rec = d.play_alts(), d.rec_alts()
    L.append("  audio class      : %s" % uacname)
    L.append("  streaming        : %d playback alt(s), %d capture alt(s)" % (len(play), len(rec)))

    if not play and not rec:
        L.append("  VERDICT          : not an audio streaming device -- unaffected")
        return L, {}

    out_clk, in_clk = d.clock_bitmaps()
    shared = sorted(out_clk & in_clk)
    L.append("  clock sources    : %s" % (sorted(d.clock_srcs) or "none"))
    L.append("  playback clocks  : %s" % (sorted(out_clk) or "none"))
    L.append("  capture clocks   : %s" % (sorted(in_clk) or "none"))

    async_play = [a for a in play if a["async"]]
    fb = [a for a in play if a["sync_ep"] is not None]
    L.append("  async playback   : %s" % ("yes (%d alt(s))" % len(async_play) if async_play else "no"))
    L.append("  feedback endpoint: %s" % ("yes (0x%02x)" % fb[0]["sync_ep"] if fb else "no"))

    flags = {}
    # --- patch 0001 --------------------------------------------------------
    p1_guard = bool(shared) and bool(play) and bool(rec)
    p1_readback = d.uac == 0x20
    flags["p1_guard"], flags["p1_readback"] = p1_guard, p1_readback
    if p1_guard:
        L.append("  [0001] guard     : ACTIVE -- clock(s) %s shared between both directions;" % shared)
        L.append("                     a second stream will no longer rewrite them")
    elif d.uac == 0x20 and play and rec:
        L.append("  [0001] guard     : inert -- directions use separate clocks, guard never fires")
    else:
        L.append("  [0001] guard     : inert")
    if p1_readback:
        L.append("  [0001] read-back : ACTIVE -- GET_CUR before every SET_CUR (hw.usb.uaudio.clock_readback)")
    else:
        L.append("  [0001] read-back : inert -- %s has no UAC2 clock entity" % uacname)

    # --- patch 0002 --------------------------------------------------------
    p2 = bool(async_play) and bool(fb) and bool(rec)
    flags["p2"] = p2
    if p2:
        L.append("  [0002] feedback  : ACTIVE -- capture stream will no longer be auto-started;")
        L.append("                     playback follows endpoint 0x%02x (hw.usb.uaudio.prefer_feedback)" % fb[0]["sync_ep"])
    elif async_play and rec and not fb:
        L.append("  [0002] feedback  : inert -- no feedback endpoint, capture stays the jitter source")
    elif async_play and not rec:
        L.append("  [0002] feedback  : inert -- no capture interface, nothing was being started")
    else:
        L.append("  [0002] feedback  : inert")

    # --- the exact failure this audit is about -----------------------------
    if p1_guard and bool(async_play) and rec:
        L.append("  >> EXPOSED to the duplicate SET_CUR: shared clock + async playback +")
        L.append("     auto-started capture.  This is the OKTO failure mode, regardless of vendor.")
        flags["exposed"] = True
    else:
        flags["exposed"] = False

    if probe_dev and d.uac == 0x20:
        for cid in sorted(d.clock_srcs):
            ctl = d.clock_srcs[cid]
            wr = "read/write" if (ctl & 0x03) == 0x03 else ("read-only" if (ctl & 0x03) == 0x01 else "none")
            cur = do_request(probe_dev, 0xA1, 0x01, 0x0100, (cid << 8), 4)
            rng = do_request(probe_dev, 0xA1, 0x02, 0x0100, (cid << 8), 254)
            curv = int.from_bytes(cur, "little") if cur and len(cur) == 4 else None
            nsub = (rng[0] | (rng[1] << 8)) if rng and len(rng) >= 2 else None
            rates = []
            if rng and nsub:
                for k in range(nsub):
                    o = 2 + k * 12
                    if o + 4 <= len(rng):
                        rates.append(int.from_bytes(rng[o:o + 4], "little"))
            L.append("  probe clock %-3d  : freq control %s, GET_CUR=%s, %d rate(s) %s"
                     % (cid, wr, curv, len(rates), rates))
            if curv is None:
                L.append("                     !! GET_CUR unsupported -> read-back inert, writes unchanged")
    return L, flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-d", "--dev", help="ugenB.D (default: scan all)")
    ap.add_argument("--dump", help="read a saved raw config descriptor instead")
    ap.add_argument("--save-dump", help="write the raw config descriptor here")
    ap.add_argument("--probe", action="store_true", help="also query the clocks live")
    a = ap.parse_args()

    targets = []
    if a.dump:
        raw = open(a.dump, "rb").read()
        if not raw[:1] or raw[0] != 9 or (len(raw) > 1 and raw[1] != 2):
            txt = raw.decode("utf-8", "replace")
            raw = bytes(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", txt))
        targets.append((a.dump, raw, None))
    elif a.dev:
        raw = fetch_config(a.dev)
        targets.append((a.dev, raw, a.dev if a.probe else None))
    else:
        listing = run_usbconfig(None, "list") or run_usbconfig(None)
        for line in listing.splitlines():
            m = re.match(r"(ugen\d+\.\d+):", line)
            if m:
                dv = m.group(1)
                raw = fetch_config(dv)
                targets.append((dv + "  " + line.split(":", 1)[1].strip()[:60],
                                raw, dv if a.probe else None))

    any_exposed = False
    for name, raw, probe in targets:
        if not raw:
            continue
        d = Device(raw)
        if d.uac is None and not d.streams:
            continue
        print("=" * 72)
        print(name)
        if a.save_dump:
            open(a.save_dump, "wb").write(raw)
            print("  (raw config descriptor saved to %s)" % a.save_dump)
        lines, flags = classify(name, d, probe)
        print("\n".join(lines))
        any_exposed |= flags.get("exposed", False)
    print("=" * 72)
    return 0 if not any_exposed else 0


if __name__ == "__main__":
    sys.exit(main())
