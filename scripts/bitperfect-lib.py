#!/usr/bin/env python3
"""Shared engine for the cross-OS USB bit-perfect tap suite.

Overview
========
The suite proves that the PCM bytes of an input WAV arrive at the USB DAC
bit-for-bit unchanged, and lets the capture from one OS be compared
byte-by-byte with the capture from the other:

    INPUT.wav --prep--> ref.raw (S32_LE wire container)
        |                    |
        | player             | (reference for alignment + verdict)
        v                    v
    USB DAC  <--wire-tap-- cap.raw --finalize--> PREFIX.wav / .txt verdict

This module is imported^W executed by both `bitperfect-tap-linux.sh` and
`bitperfect-tap-freebsd.sh` so that the two platforms share one
implementation of everything that must NOT drift apart: how the input is
promoted to the wire container, how a capture is decoded into a contiguous
byte stream, how that stream is aligned against the reference, and how the
verdict/artifacts are produced.  The only per-OS parts left in the shell
scripts are device discovery, the player, and the capture mechanism
(usbmon vs usbdump).

Subcommands
===========
prep IN.wav OUT.raw
    Extract the WAV's PCM payload and promote it LOSSLESSLY to the S32_LE
    wire container (see cmd_prep for the exact byte mapping).  Prints
    "RATE CH BITS FRAMES" (of the ORIGINAL wav) on stdout for the caller.

tap-usbmon BUS DEVNUM OUT.raw          (Linux, root)
    Live usbmon reader: filters isochronous OUT submissions to endpoint
    0x01 of USB device DEVNUM on bus BUS and appends the concatenated
    iso-packet payloads — the exact bytes the host controller sends to the
    DAC — to OUT.raw until SIGTERM/SIGINT.  Statistics go to stderr.

decode-usbdump OUT.raw                 (FreeBSD)
    Parse `usbdump -r cap.pcap -vv` text on stdin and write the
    concatenated endpoint-0x01 OUT payloads to OUT.raw.

finalize REF.raw CAP.raw RATE CH PREFIX OS INPUT_PATH
    Align the captured wire stream against the reference, write
    PREFIX.wav + PREFIX.txt, print the verdict.
    Exit 0 = bit-perfect, 1 = mismatch, 2 = capture unusable.

Wire container note
===================
Everything here assumes the DAC negotiates a 4-byte (S32_LE) sample
container — true for both the OKTO DAC8 and the Cambridge DacMagic 100,
whose USB descriptors expose only 32-bit altsettings.  A DAC using a
3-byte-packed container (S24_3LE) would put the same 24 audio bits on the
wire WITHOUT the pad byte; its capture could not be byte-compared against
a 4-byte-container capture without first stripping/inserting the pad
bytes.  No such normalization is implemented because no such DAC is in
use here.
"""
import hashlib
import os
import re
import struct
import sys
import wave


def sha256(b):
    return hashlib.sha256(b).hexdigest()


# ═════════════════════════════════════════════════════════════════════════════
# prep — WAV -> S32_LE wire-container reference stream
# ═════════════════════════════════════════════════════════════════════════════
#
# Both DACs accept only 32-bit sample containers on the USB wire, so any
# player delivering 16- or 24-bit material bit-perfectly MUST widen each
# sample to 4 bytes.  The one and only lossless widening is a left shift
# (zero-fill of the newly created low bits); doing it here, identically on
# both OSes, removes the players' format layers from the equation entirely:
# the players are then always fed S32_LE and never convert anything.
#
# Exact byte mappings (little-endian, per sample; b0 = LSB of the source):
#
#   32-bit  b0 b1 b2 b3   ->  b0 b1 b2 b3      (verbatim)
#   24-bit  b0 b1 b2      ->  00 b0 b1 b2      (value << 8)
#   16-bit  b0 b1         ->  00 00 b0 b1      (value << 16)
#
# The promotions are pure byte moves (no arithmetic), implemented with
# slice assignments: e.g. for 24-bit, target byte 4k+1 receives source byte
# 3k, 4k+2 receives 3k+1, 4k+3 receives 3k+2, and 4k stays 0.

def cmd_prep(inwav, outraw):
    w = wave.open(inwav, "rb")
    ch, sw, rate, n = (w.getnchannels(), w.getsampwidth(),
                       w.getframerate(), w.getnframes())
    pcm = w.readframes(n)               # raw interleaved PCM, header stripped
    w.close()
    if sw == 4:                         # already the wire container
        out = pcm
    elif sw == 3:                       # S24_3LE -> 24-in-32 (value << 8)
        s = len(pcm) // 3               # total sample count (all channels)
        out = bytearray(s * 4)          # zero-initialized -> pad bytes = 0x00
        out[1::4] = pcm[0::3]
        out[2::4] = pcm[1::3]
        out[3::4] = pcm[2::3]
    elif sw == 2:                       # S16_LE -> 16-in-32 (value << 16)
        s = len(pcm) // 2
        out = bytearray(s * 4)
        out[2::4] = pcm[0::2]
        out[3::4] = pcm[1::2]
    else:
        sys.exit(f"unsupported sample width: {sw * 8} bit")
    with open(outraw, "wb") as f:
        f.write(bytes(out))
    print(rate, ch, sw * 8, n)          # parsed by the calling shell script
    return 0


# ═════════════════════════════════════════════════════════════════════════════
# tap-usbmon — live capture of the DAC's isochronous OUT payloads (Linux)
# ═════════════════════════════════════════════════════════════════════════════
#
# usbmon (Documentation/usb/usbmon.rst) mirrors every URB on a bus to
# /dev/usbmon<bus>.  A plain read() returns one EVENT at a time in the
# original "API0" binary framing (verified empirically on this kernel:
# a 48-byte header, then exactly `len_cap` bytes of captured data):
#
#   struct mon_bin_hdr (48 bytes, little-endian, host word size irrelevant):
#     off  size  field       meaning
#      0     8   id          URB kernel address (tag pairing S/C events)
#      8     1   type        'S' submission, 'C' callback, 'E' error
#      9     1   xfer_type   0 = ISO, 1 = interrupt, 2 = control, 3 = bulk
#     10     1   epnum       endpoint | 0x80 direction bit (0x01 = EP1 OUT)
#     11     1   devnum      USB device address on the bus
#     12     2   busnum      bus number
#     14     1   flag_setup  '\0' = setup packet present (control only)
#     15     1   flag_data   '\0' = data present; anything else = no data
#     16     8   ts_sec      timestamp (unused here)
#     24     4   ts_usec
#     28     4   status
#     32     4   len_urb     length of the URB transfer buffer
#     36     4   len_cap     bytes of data following this header
#     40     8   union: setup[8] (control) | { s32 error_count;
#                                              s32 numdesc } (isochronous)
#
# For an ISOCHRONOUS event whose data was captured, the data area is:
#
#     [numdesc * 16 bytes of iso descriptors][the URB transfer buffer]
#
#   struct mon_bin_isodesc (16 bytes each):
#     s32 status; u32 offset; u32 length; u32 pad
#
# `offset`/`length` locate each iso packet's payload INSIDE the transfer
# buffer (i.e. relative to the byte right after the descriptor table).
# Concatenating those slices in order yields the contiguous byte stream the
# host controller puts on the wire — audio-wise, the interleaved S32_LE
# sample stream, with packet boundaries erased.
#
# What we keep: type 'S' + xfer ISO + epnum 0x01 + our devnum + data
# present.  OUT data rides on the SUBMISSION ('S') event — the completion
# ('C') carries no data for OUT transfers.  Everything else on the bus
# (feedback EP 0x81, control EP 0x00/0x80, other devices such as mice on
# the same bus) is skipped.
#
# Reliability: the kernel buffers events in a ring; if userspace lags, it
# DROPS whole events and counts them.  We (a) enlarge the ring to its
# 1200 KiB maximum, (b) verify per-event that the captured data covers the
# whole transfer buffer ("truncated" counter), and (c) read the kernel's
# own dropped-events counter at exit — so a lossy capture can never
# masquerade as a clean one (a gap would also show up in `finalize` as a
# discontinuity).
#
# ioctl request codes (x86_64 _IO/_IOR encoding of usbmon's magic 0x92):

MON_HDR = struct.Struct("<QBBBBHccqiiII8s")
MON_IOCT_RING_SIZE = 0x9204     # _IO (0x92, 4)           arg = bytes (int)
MON_IOCG_STATS = 0x80089203     # _IOR(0x92, 3, 8 bytes)  -> {u32 queued, u32 dropped}
XFER_ISO = 0


def cmd_tap_usbmon(bus, devnum, outpath):
    import fcntl
    import signal

    fd = os.open(f"/dev/usbmon{bus}", os.O_RDONLY)
    try:                                # best effort: default ring is ~300 KiB
        fcntl.ioctl(fd, MON_IOCT_RING_SIZE, 1200 * 1024)
    except OSError:
        pass

    # A handler that RAISES makes the blocking os.read() abort immediately
    # (PEP 475 would otherwise silently retry the read after EINTR, and on
    # an idle bus the read could then block forever).
    def onterm(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, onterm)
    signal.signal(signal.SIGINT, onterm)

    out = open(outpath, "wb")
    urbs = payload = truncated = 0
    try:
        while True:
            hdr = os.read(fd, 48)
            if len(hdr) != 48:
                continue
            (_, typ, xt, ep, dn, _, _, fdata, _, _, _,
             len_urb, len_cap, s) = MON_HDR.unpack(hdr)
            # The event's data must be consumed even for events we skip:
            # read() serves one event contiguously (header, then data).
            data = b""
            while len(data) < len_cap:
                chunk = os.read(fd, len_cap - len(data))
                if not chunk:
                    break
                data += chunk
            if (dn != devnum or xt != XFER_ISO or typ != ord("S")
                    or ep != 0x01 or fdata != b"\x00" or not data):
                continue
            ndesc = struct.unpack("<ii", s)[1]          # union -> iso.numdesc
            if len(data) < ndesc * 16 + len_urb:
                truncated += 1          # ring too small for this event
            buf = data[ndesc * 16:]     # the URB transfer buffer
            for i in range(ndesc):
                _, off, ilen, _ = struct.unpack_from("<iIII", data, i * 16)
                out.write(buf[off:off + ilen])
                payload += ilen
            urbs += 1
    finally:
        out.close()
        stats = bytearray(8)
        dropped = "?"
        try:
            fcntl.ioctl(fd, MON_IOCG_STATS, stats, True)
            dropped = struct.unpack("<II", stats)[1]
        except OSError:
            pass
        print(f"usbmon tap: {urbs} URBs, {payload} payload bytes, "
              f"{truncated} truncated events, {dropped} events dropped by kernel",
              file=sys.stderr)
    return 0


# ═════════════════════════════════════════════════════════════════════════════
# decode-usbdump — FreeBSD usbdump -vv text -> contiguous wire byte stream
# ═════════════════════════════════════════════════════════════════════════════
#
# Identical logic to the decoder embedded in scripts/verify-bitperfect.sh
# (validated there against the OKTO on FreeBSD).  `usbdump -r cap.pcap -vv`
# prints one record per transfer; the ones we want look like:
#
#   HH:MM:SS.uuuuuu usbusB.D SUBM-ISOC-EP=00000001,SPD=HIGH,NFR=64,...
#    frame[0] WRITE 40 bytes
#    0000  00 00 00 00 00 00 00 00  01 00 00 00 37 9e 00 00  |......7...|
#    ...
#    flags 0 <0>
#
# Parsing rules:
#   * a line matching EP=XXXXXXXX starts a new record; we are "inside" the
#     audio stream only when it is SUBM-ISOC-EP=00000001 (the submissions
#     to OUT endpoint 0x01 — feedback 0x81 and control 0x80 are skipped);
#   * "frame[N] WRITE ..." opens a hex payload block; "frame[N] READ ..."
#     or a "flags ..." line closes it;
#   * inside a block, hex-dump lines are recognized by their leading
#     " NNNN  " offset column; the trailing |ascii| column is cut at the
#     first '|' and "--" placeholders (bytes beyond the frame length) are
#     ignored; the remaining two-digit hex tokens are the payload bytes.

def cmd_decode_usbdump(outpath):
    out = bytearray()
    in_out = in_frame = False
    hdr = re.compile(r"\bEP=([0-9A-Fa-f]{8})")
    hexline = re.compile(r"^ [0-9A-Fa-f]{4}  (.*)$")
    for line in sys.stdin:
        if hdr.search(line):
            in_out = "SUBM-ISOC-EP=00000001" in line
            in_frame = False
            continue
        if not in_out:
            continue
        if "WRITE" in line and "frame[" in line:
            in_frame = True
            continue
        if ("READ" in line and "frame[" in line) or line.lstrip().startswith("flags"):
            in_frame = False
            continue
        if in_frame:
            m = hexline.match(line)
            if m:
                for t in m.group(1).split("|", 1)[0].split():
                    if len(t) == 2 and t != "--":
                        out.append(int(t, 16))
    with open(outpath, "wb") as f:
        f.write(bytes(out))
    return 0


# ═════════════════════════════════════════════════════════════════════════════
# finalize — align capture to reference, verdict, artifacts
# ═════════════════════════════════════════════════════════════════════════════

def find_probe_offset(ref):
    """Pick where in the reference to take the 4 KiB alignment probe.

    The probe must not be dominated by zero bytes: real material may start
    with silence (and even the counter signal starts with small values
    whose high bytes are zero), and a mostly-zero probe could match the
    priming zeros the audio stack emits before the first real sample.
    Scan forward in 4 KiB steps until a window contains at least 256
    non-zero bytes, i.e. is unmistakably "signal".  Falls back to offset 0
    for a pathologically silent file (alignment will then be reported as
    failed rather than wrong, since a zero probe matches at position 0 of
    the zero run)."""
    po = 0
    while po + 8192 < len(ref):
        if sum(1 for b in ref[po:po + 4096] if b) >= 256:
            return po
        po += 4096
    return 0


def walk(ref, cap):
    """Slip-tolerant stream comparison (same algorithm as the one proven in
    scripts/verify-bitperfect.sh).

    Used only AFTER plain equality has already failed, to CLASSIFY the
    difference.  Walks both streams in lockstep:

    * bytes equal        -> advance both (counted in `matched`);
    * bytes differ       -> try to RESYNC: search the next 256 reference
      bytes within the following 8 KiB of the capture (capture contains
      EXTRA bytes -> a duplicated/inserted region -> advance capture), and
      symmetrically the next 256 capture bytes within the reference
      (capture LOST bytes -> advance reference).  The shorter jump wins;
      either one is a "timing slip": sample VALUES survived but the byte
      COUNT drifted (a free-running-clock artifact, not data corruption);
    * no resync possible -> if the capture is silent from here on it is an
      underrun tail (producer stopped feeding); otherwise it is genuine
      VALUE CORRUPTION and the position is returned for the hex dump.

    Returns (matched, slips, corrupt_ref_idx, corrupt_cap_idx, underrun)
    with corrupt indices -1 when no corruption was found."""
    si = ci = 0
    matched = slips = 0
    WIN = 8192
    while si < len(ref) - 256 and ci < len(cap) - 256:
        if ref[si] == cap[ci]:
            si += 1; ci += 1; matched += 1
            continue
        needle = ref[si:si + 256]
        j = cap[ci:ci + WIN].find(needle)            # capture has extra bytes
        k = ref[si:si + WIN].find(cap[ci:ci + 256])  # capture dropped bytes
        if j > 0 and (k < 0 or j <= k):
            ci += j; slips += 1
            continue
        if k > 0:
            si += k; slips += 1
            continue
        if not any(cap[ci:ci + 4096]):
            return matched, slips, -1, -1, True
        return matched, slips, si, ci, False
    return matched, slips, -1, -1, False


def cmd_finalize(refraw, capraw, rate, ch, prefix, osname, inputpath):
    """Compare the wire capture against the reference and emit artifacts.

    Alignment
    ---------
    The raw wire stream never starts exactly at the first source byte: the
    capture begins before playback, and the audio stack may emit priming
    zeros before the first written sample.  We locate a content-bearing
    4 KiB probe of the reference (taken at offset `po`) inside the capture
    and derive the capture offset of the stream start:

        start = find(cap, ref[po:po+4096]) - po

    `start < 0` means the tap attached AFTER playback had begun (the head
    of the stream was never captured) — reported as its own verdict since
    the comparison is then only partial.

    Artifacts (all under PREFIX*)
    -----------------------------
    PREFIX.wav      the aligned capture, trimmed to the reference length,
                    wrapped in a WAV header with the same format as the
                    reference (S32_LE / ch / rate).  When the chain is
                    bit-perfect this file is BYTE-IDENTICAL to the input
                    WAV (same sha256) — which is what makes the Linux and
                    FreeBSD outputs directly comparable.
    PREFIX.txt      this report (also printed to stdout).  It names and
                    hashes every stage, so a run can be audited from the
                    report alone:
                      input file : the source WAV on disk, sha256 of the
                                   FILE (header included) — matches what
                                   gen-bitperfect-wav.py prints
                      ref bytes  : the promoted S32_LE reference payload
                      wire raw   : PREFIX.wire.raw, the untrimmed capture
                      tap wav    : the aligned payload (what the verdict
                                   is about, and what compare.py reads)
    PREFIX.wire.raw the full untrimmed stream — copied by the calling
                    script, mentioned here for completeness.

    Verdicts and exit codes
    -----------------------
    0  BIT-PERFECT        capture == reference over its full length
    1  HEAD LOST          bytes identical but the stream head was missed
    1  INCOMPLETE         identical prefix, capture ends early
    1  VALUE CORRUPTION   a byte on the wire differs (hex context printed)
    1  TIMING SLIP(S)     values intact, byte count drifted
    1  UNDERRUN TAIL      identical, then wire goes silent early
    2  NO CAPTURE / ALIGNMENT FAILED   capture unusable
    """
    ref = open(refraw, "rb").read()
    cap = open(capraw, "rb").read()
    # The report names BOTH ends of the chain with the sha256 of each, so a
    # run is self-describing: the file that went in, and the bytes that came
    # off the USB wire.  `input file` is hashed as it sits on disk (header
    # included), which is exactly what gen-bitperfect-wav.py prints and what
    # tests/README.md tabulates — so the asset can be identified from the
    # report alone.  `wire raw` is the untrimmed capture, i.e. the content of
    # the PREFIX.wire.raw artifact the calling script has just written.
    indata = open(inputpath, "rb").read()
    lines = [f"input      : {os.path.basename(inputpath)}",
             f"input file : {inputpath}  ({len(indata)} bytes, "
             f"sha256 {sha256(indata)})",
             f"os         : {osname}",
             f"format     : S32_LE wire container, {ch} ch, {rate} Hz",
             f"ref bytes  : {len(ref)}  sha256 {sha256(ref)}",
             f"wire raw   : {prefix}.wire.raw  ({len(cap)} bytes, "
             f"sha256 {sha256(cap)})"]

    def out(verdict, code):
        lines.append(f"verdict    : {verdict}")
        with open(prefix + ".txt", "w") as f:
            f.write("\n".join(lines) + "\n")
        print("\n".join(lines))
        return code

    if not cap:
        return out("NO CAPTURE — nothing seen on the USB wire", 2)

    po = find_probe_offset(ref)
    pos = cap.find(ref[po:po + 4096])
    if pos < 0:
        return out("ALIGNMENT FAILED — reference not found in the wire stream "
                   "(gross corruption, wrong device, or capture gap)", 2)
    start = pos - po
    refskip = 0
    if start < 0:
        refskip = -start
        start = 0
        lines.append(f"warning    : capture missed the first {refskip} stream bytes")
    aligned = cap[start:start + len(ref) - refskip]

    wo = wave.open(prefix + ".wav", "wb")
    wo.setnchannels(ch)
    wo.setsampwidth(4)
    wo.setframerate(rate)
    wo.writeframes(aligned)
    wo.close()
    lines.append(f"tap wav    : {prefix}.wav  ({len(aligned)} PCM bytes, "
                 f"sha256 {sha256(aligned)})")
    lines.append(f"tap wav sha256 (file) : {sha256(open(prefix + '.wav', 'rb').read())}")

    refcmp = ref[refskip:]
    if aligned == refcmp:
        if refskip:
            return out(f"HEAD LOST — the captured {len(refcmp)} bytes are all "
                       f"identical, but the first {refskip} stream bytes were "
                       "not captured (start the tap earlier)", 1)
        return out(f"BIT-PERFECT — all {len(refcmp)} reference bytes identical "
                   "on the USB wire", 0)
    if len(aligned) < len(refcmp) and aligned == refcmp[:len(aligned)]:
        return out(f"INCOMPLETE — first {len(aligned)} bytes identical but the "
                   f"capture ends {len(refcmp) - len(aligned)} bytes early "
                   "(tap stopped before playback drained?)", 1)

    matched, slips, csi, cci, underrun = walk(refcmp, aligned)
    if csi >= 0:
        hx = lambda b, o: " ".join(f"{x:02x}" for x in b[o:o + 8])
        lines.append(f"first corruption at reference offset {refskip + csi}:")
        lines.append(f"  ref : {hx(refcmp, csi)}")
        lines.append(f"  wire: {hx(aligned, cci)}")
        return out(f"VALUE CORRUPTION — {matched} bytes matched, then the wire "
                   "diverges from the reference", 1)
    if slips:
        return out(f"VALUE-TRANSPARENT WITH {slips} TIMING SLIP(S) — sample "
                   f"values never altered ({matched} bytes matched) but the "
                   "byte count drifted", 1)
    return out(f"UNDERRUN TAIL — {matched} bytes identical, capture then goes "
               "silent", 1) if underrun else out("UNCLASSIFIED DIFFERENCE", 1)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "prep":
        return cmd_prep(a[0], a[1])
    if cmd == "tap-usbmon":
        return cmd_tap_usbmon(int(a[0]), int(a[1]), a[2])
    if cmd == "decode-usbdump":
        return cmd_decode_usbdump(a[0])
    if cmd == "finalize":
        return cmd_finalize(a[0], a[1], int(a[2]), int(a[3]), a[4], a[5], a[6])
    sys.exit(f"unknown subcommand: {cmd}\n\n{__doc__}")


if __name__ == "__main__":
    sys.exit(main())
