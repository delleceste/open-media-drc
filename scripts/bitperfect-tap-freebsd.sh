#!/usr/bin/env bash
set -euo pipefail

# bitperfect-tap-freebsd.sh — play a WAV to the USB DAC and record the EXACT
# bytes sent to it on the USB wire, producing artifacts comparable
# byte-by-byte with a Linux run of bitperfect-tap-linux.sh on the same
# input file (compare with scripts/bitperfect-compare.py).
#
# Usage (sudo is asked internally, only for usbdump):
#     ./bitperfect-tap-freebsd.sh [--dev /dev/dspN] [--out PREFIX] INPUT.wav
#
#   INPUT.wav    16/24/32-bit PCM WAV, any rate the DAC supports.
#   --dev DEV    OSS play device (default /dev/dsp0 — the direct DAC node;
#                run `./drc.sh off` first so virtual_oss/brutefir release
#                it, and stop any renderer holding it).
#   --out PREFIX output path prefix (default: bp-results/<input>-freebsd).
#
# Outputs (same convention as the Linux twin):
#   PREFIX.wav       tapped wire bytes, source-aligned and source-length,
#                    as a WAV.  For a 32-bit INPUT.wav a bit-perfect chain
#                    makes this byte-identical to it (same sha256); for a
#                    16/24-bit input it cannot be, since this file carries
#                    the promoted 32-bit container — compare the payload
#                    sha256 reported on the `tap wav` line instead.
#   PREFIX.wire.raw  full untrimmed wire stream
#   PREFIX.txt       report naming and hashing every stage — `input file`
#                    (the source as it sits on disk), `ref bytes` (promoted
#                    reference payload), `wire raw` (untrimmed capture),
#                    `tap wav` (aligned payload) — plus the verdict.
#                    `wire raw` varies run to run (priming, pad, capture
#                    boundaries): provenance only, never a comparison key.
#
# Exit codes: 0 bit-perfect, 1 mismatch (see PREFIX.txt), 2 setup/capture
# problem.
#
# Preconditions (see doc/BIT-PERFECT-VERIFICATION.md):
#   dev.pcm.N.bitperfect=1      first opener's format = hardware format
#   dev.pcm.N.play.vchans=0     no vchan mixer/resampler before the DAC
# The embedded OSS writer double-checks this at runtime: it ABORTS if the
# kernel coerces format/channels/rate, because a coercion means a
# converting feeder would be inserted — i.e. NOT bit-perfect.
#
# How it works (mirrors the Linux twin step by step)
# --------------------------------------------------
# 1. PREP    bitperfect-lib.py `prep` extracts the WAV's PCM payload and
#            promotes 16/24-bit input LOSSLESSLY to the S32_LE wire
#            container (low bytes zero-filled) — identical code and thus
#            identical reference bytes on both OSes.
# 2. TAP     usbdump(8) captures every transfer of the DAC's USB device
#            address to a pcap; after playback, `usbdump -r -vv` text is
#            decoded by bitperfect-lib.py `decode-usbdump`, which keeps
#            the isochronous OUT submissions to endpoint 0x01 and
#            concatenates their payloads into the contiguous wire stream.
# 3. PLAY    the embedded C writer opens the dsp node, sets S32_LE/2ch/rate
#            with post-ioctl verification (the format guard above), then
#            write()s the reference flat-out.  No pacing is needed: the
#            kernel blocks the writes in lockstep with the DAC's own clock
#            (async feedback), so the producer cannot over- or underrun.
#            What is played is the reference plus a TRAILING SILENCE PAD
#            (see PAD_MS below); the reference used for comparison is
#            unchanged.
# 4. VERDICT bitperfect-lib.py `finalize` aligns the capture to the
#            reference (absorbing OSS stream-priming zeros and capture
#            lead-in), writes the artifacts and classifies any difference.
#
# Why the trailing silence pad
# ----------------------------
# usbdump writes the pcap through a buffer, and terminating it drops
# whatever has not been flushed — costing the LAST few ms of the capture.
# Against an unpadded reference that lands as a bogus `INCOMPLETE` verdict
# ("first N bytes identical but the capture ends X bytes early") even
# though every byte that was captured matched.  Playing PAD_MS of silence
# after the reference moves that loss into the pad, which `finalize`
# discards anyway: it locates the stream start by content and then takes
# exactly `len(ref)` bytes, so any padding beyond the reference is trimmed
# the same way the stream-priming lead-in already is.  The Linux twin taps
# usbmon's binary interface, does not exhibit the loss, and is unchanged.

HERE="$(cd "$(dirname "$0")" && pwd)"
LIB="$HERE/bitperfect-lib.py"

PLAY_DEV="/dev/dsp0"
PREFIX=""
INPUT=""
PAD_MS=500           # trailing silence played after the reference (see above)
while [ $# -gt 0 ]; do
  case "$1" in
    --dev)  PLAY_DEV="$2"; shift 2;;
    --out)  PREFIX="$2"; shift 2;;
    -h|--help) sed -n '3,30p' "$0"; exit 0;;
    *) INPUT="$1"; shift;;
  esac
done
[ -n "$INPUT" ] && [ -f "$INPUT" ] || { echo "input WAV required" >&2; exit 2; }
if [ -z "$PREFIX" ]; then
  mkdir -p bp-results
  PREFIX="bp-results/$(basename "${INPUT%.wav}")-freebsd"
fi

say() { printf '\033[1m%s\033[0m\n' "$*"; }

[ -c "$PLAY_DEV" ] || { echo "play device $PLAY_DEV not found" >&2; exit 2; }
# Single-opener device: refuse to run while something else holds it.
if fuser "$PLAY_DEV" 2>/dev/null | grep -q '[0-9]'; then
  echo "$PLAY_DEV is busy (./drc.sh off and stop the renderer first)" >&2; exit 2
fi

TMP="$(mktemp -d /tmp/bptap.XXXXXX)"
trap 'sudo kill -INT "$TAPPID" 2>/dev/null || true; rm -rf "$TMP"' EXIT
TAPPID=""

# ── 1. PREP: reference PCM in the S32_LE wire container ──────────────────────
# prep prints "RATE CH BITS FRAMES" of the original WAV; ref.raw holds the
# promoted stream (identical to the WAV payload when it is already 32-bit).
read -r RATE CH BITS FRAMES < <(python3 "$LIB" prep "$INPUT" "$TMP/ref.raw")
say "Input: $FRAMES frames, ${BITS}-bit, ${CH}ch @ ${RATE} Hz -> S32_LE wire container"

# play.raw = ref.raw + PAD_MS of silence.  ONLY this padded copy is played;
# ref.raw stays the comparison reference, so the verdict is unaffected.
PAD_BYTES=$(( RATE * CH * 4 * PAD_MS / 1000 ))
cp "$TMP/ref.raw" "$TMP/play.raw"
dd if=/dev/zero bs="$PAD_BYTES" count=1 status=none >> "$TMP/play.raw"

# ── format-guarded OSS writer (same as in verify-bitperfect.sh) ──────────────
# Each ioctl result is CHECKED: OSS reports back the format/channels/rate it
# will actually use, and any deviation from what we asked means a converting
# feeder chain — the writer then exits 3 instead of playing altered audio.
cc -O2 -o "$TMP/bpwrite" -x c - <<'C'
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/soundcard.h>
#include <sys/ioctl.h>
/* usage: bpwrite /dev/dspN rate channels file.raw
 * Plays raw S32_LE flat-out; the kernel's back-pressure paces the writes
 * at the DAC clock. SNDCTL_DSP_SYNC blocks until the last sample has
 * drained so the caller knows playback is complete on return. */
int main(int argc, char **argv){
    if(argc<5){fprintf(stderr,"usage: %s dsp rate channels raw\n",argv[0]);return 2;}
    int rate=atoi(argv[2]); int want_ch=atoi(argv[3]);
    int fd=open(argv[1],O_WRONLY); if(fd<0){perror("open dsp");return 1;}
    /* Each ioctl is checked TWICE: did the call itself fail (perror -> the
     * device/driver refused), and did OSS report back a DIFFERENT value
     * (coercion -> a converting feeder would be inserted).  Keeping the two
     * apart matters when diagnosing: "coerced 0x1000" would otherwise be
     * printed for a plain ioctl failure, where 0x1000 IS what we asked. */
    int fmt=AFMT_S32_LE;
    if(ioctl(fd,SNDCTL_DSP_SETFMT,&fmt)<0){perror("FAIL: SNDCTL_DSP_SETFMT");return 3;}
    if(fmt!=AFMT_S32_LE){fprintf(stderr,"FAIL: format coerced to 0x%x (asked S32_LE 0x%x) -> NOT bit-perfect\n",fmt,AFMT_S32_LE);return 3;}
    int ch=want_ch;
    if(ioctl(fd,SNDCTL_DSP_CHANNELS,&ch)<0){perror("FAIL: SNDCTL_DSP_CHANNELS");return 3;}
    if(ch!=want_ch){fprintf(stderr,"FAIL: channels coerced to %d (asked %d) -> NOT bit-perfect\n",ch,want_ch);return 3;}
    int sp=rate;
    if(ioctl(fd,SNDCTL_DSP_SPEED,&sp)<0){perror("FAIL: SNDCTL_DSP_SPEED");return 3;}
    if(sp!=rate){fprintf(stderr,"FAIL: rate coerced to %d (asked %d) -> resampling -> NOT bit-perfect\n",sp,rate);return 3;}
    int in=open(argv[4],O_RDONLY); if(in<0){perror("open raw");return 1;}
    char b[65536]; ssize_t n;
    while((n=read(in,b,sizeof b))>0){char*p=b;while(n>0){ssize_t w=write(fd,p,n);if(w<0){perror("write");return 1;}p+=w;n-=w;}}
    ioctl(fd,SNDCTL_DSP_SYNC,NULL); close(fd); return 0;
}
C

# ── 2. TAP: locate the DAC on the USB bus and start usbdump ──────────────────
# USB addresses change across replugs, so bus/devaddr are read from the
# uaudio driver's %location sysctl rather than hard-coded.
LOC="$(sysctl -n dev.uaudio.0.%location 2>/dev/null || true)"
[ -n "$LOC" ] || { echo "uaudio0 not found (DAC attached?)" >&2; exit 2; }
UBUS="usbus$(echo "$LOC" | sed -n 's/.*bus=\([0-9]*\).*/\1/p')"
DADDR="$(echo "$LOC" | sed -n 's/.*devaddr=\([0-9]*\).*/\1/p')"
say "DAC: $UBUS device $DADDR (tap endpoint 0x01)"

# -s 65536: snapshot length large enough to never truncate an audio URB.
sudo usbdump -i "$UBUS" -f "$DADDR" -s 65536 -w "$TMP/cap.pcap" >/dev/null 2>&1 &
TAPPID=$!
sleep 0.6            # let the tap attach so the stream head is captured

# ── 3. PLAY: flat-out write, flow-controlled by the DAC's own clock ──────────
say "Playing (flat-out, DAC-clocked) to $PLAY_DEV  [+${PAD_MS}ms silence pad]"
"$TMP/bpwrite" "$PLAY_DEV" "$RATE" "$CH" "$TMP/play.raw" || {
  echo "writer aborted (see FAIL above)" >&2; exit 2; }

# SNDCTL_DSP_SYNC has returned, so the pad is already on the wire; the sleep
# only covers URBs still queued in the controller.  SIGINT (not the default
# SIGTERM) gives usbdump the chance to flush and close the pcap cleanly.
sleep 1
sudo kill -INT "$TAPPID" 2>/dev/null || true
wait "$TAPPID" 2>/dev/null || true
TAPPID=""

# ── decode the pcap into the contiguous wire byte stream ─────────────────────
sudo usbdump -r "$TMP/cap.pcap" -vv 2>/dev/null | python3 "$LIB" decode-usbdump "$TMP/cap.raw"

# ── 4. VERDICT: align, verify, emit artifacts ────────────────────────────────
cp "$TMP/cap.raw" "$PREFIX.wire.raw"
python3 "$LIB" finalize "$TMP/ref.raw" "$TMP/cap.raw" "$RATE" "$CH" \
        "$PREFIX" "freebsd/$(uname -r)" "$INPUT"
