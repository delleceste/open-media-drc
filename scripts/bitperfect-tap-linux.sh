#!/usr/bin/env bash
set -euo pipefail

# bitperfect-tap-linux.sh — play a WAV to the USB DAC and record the EXACT
# bytes sent to it on the USB wire, producing artifacts comparable
# byte-by-byte with a FreeBSD run of bitperfect-tap-freebsd.sh on the same
# input file (compare with scripts/bitperfect-compare.py).
#
# Usage (sudo is asked internally, only for the usbmon tap):
#     ./bitperfect-tap-linux.sh [--card N] [--out PREFIX] INPUT.wav
#
#   INPUT.wav    16/24/32-bit PCM WAV, any rate the DAC supports.
#   --card N     ALSA card number (default: first USB audio card found).
#   --out PREFIX output path prefix (default: bp-results/<input>-linux).
#
# Outputs:
#   PREFIX.wav       tapped wire bytes, source-aligned and source-length,
#                    as a WAV — byte-identical to INPUT.wav (same sha256)
#                    when the chain is bit-perfect
#   PREFIX.wire.raw  full untrimmed wire stream (forensics: priming bytes,
#                    tail, everything endpoint 0x01 carried)
#   PREFIX.txt       report: verdict, sizes, sha256 sums
#
# Exit codes: 0 bit-perfect, 1 mismatch (see PREFIX.txt), 2 setup/capture
# problem.
#
# How it works
# ------------
# 1. PREP    The WAV's PCM payload is extracted and, for 16/24-bit input,
#            promoted LOSSLESSLY to the S32_LE wire container (low bytes
#            zero-filled; the DAC only takes 32-bit containers, so this is
#            exactly what any bit-perfect player must send).  Done by
#            bitperfect-lib.py `prep`, identical code on both OSes.
# 2. TAP     usbmon (the in-kernel USB sniffer) is read on /dev/usbmon<bus>
#            by bitperfect-lib.py `tap-usbmon`, which keeps only the
#            isochronous OUT submissions to endpoint 0x01 of the DAC and
#            concatenates their payloads — the byte stream the host
#            controller DMA-writes to the wire.  Root-only, hence sudo.
# 3. PLAY    aplay writes the reference stream to the RAW `hw:` device.
#            A `hw:` PCM has no plug layer, so ALSA cannot resample,
#            convert, dither or attenuate — if the format were unsupported
#            aplay would fail loudly rather than convert.  The write loop
#            needs no pacing: the USB audio stack applies back-pressure at
#            the DAC's own clock (async feedback), so the producer is
#            slaved to the DAC and can neither overrun nor underrun it.
# 4. VERDICT bitperfect-lib.py `finalize` aligns the captured stream to
#            the reference (absorbing capture lead-in/priming), writes the
#            artifacts and classifies any difference (corruption vs timing
#            slip vs underrun).
#
# The tap sees the stream at the LAST host-controlled point — the URBs
# handed to the USB host controller. Only the controller's DMA engine and
# the DAC's own USB receiver sit beyond it; neither can be tapped in
# software on any OS, and neither has a mechanism to alter PCM payloads.

HERE="$(cd "$(dirname "$0")" && pwd)"
LIB="$HERE/bitperfect-lib.py"

CARD=""
PREFIX=""
INPUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --card) CARD="$2"; shift 2;;
    --out)  PREFIX="$2"; shift 2;;
    -h|--help) sed -n '3,30p' "$0"; exit 0;;
    *) INPUT="$1"; shift;;
  esac
done
[ -n "$INPUT" ] && [ -f "$INPUT" ] || { echo "input WAV required" >&2; exit 2; }
if [ -z "$PREFIX" ]; then
  mkdir -p bp-results
  PREFIX="bp-results/$(basename "${INPUT%.wav}")-linux"
fi

say() { printf '\033[1m%s\033[0m\n' "$*"; }

# ── locate the USB DAC: ALSA card number -> USB bus/device address ───────────
# Every snd-usb-audio card exposes /proc/asound/cardN/usbbus containing
# "BBB/DDD" (bus/device, zero-padded decimal). That is the address usbmon
# filters on, and it survives replugs — nothing is hard-coded.
if [ -z "$CARD" ]; then
  for d in /proc/asound/card[0-9]*; do
    [ -f "$d/usbbus" ] || continue
    CARD="${d#/proc/asound/card}"
    break
  done
fi
[ -n "$CARD" ] && [ -f "/proc/asound/card$CARD/usbbus" ] || {
  echo "no USB audio card found (override with --card N)" >&2; exit 2; }
IFS=/ read -r BUS DEVNUM < "/proc/asound/card$CARD/usbbus"
BUS=$((10#$BUS)); DEVNUM=$((10#$DEVNUM))   # strip leading zeros ("001" -> 1)
say "DAC: ALSA card $CARD = USB bus $BUS device $DEVNUM (tap endpoint 0x01)"

# The DAC is a single-opener device: refuse to fight another process (MPD,
# a browser, ...) for it — a busy device would make aplay fail mid-setup.
PCMDEV="/dev/snd/pcmC${CARD}D0p"
if fuser -s "$PCMDEV" 2>/dev/null; then
  echo "$PCMDEV is busy (stop MPD/whatever holds the DAC first)" >&2; exit 2
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/bptap.XXXXXX")"
trap 'sudo kill "$TAPPID" 2>/dev/null || true; rm -rf "$TMP"' EXIT
TAPPID=""

# ── 1. PREP: reference PCM in the S32_LE wire container ──────────────────────
# prep prints "RATE CH BITS FRAMES" of the original WAV; ref.raw holds the
# promoted stream (identical to the WAV payload when it is already 32-bit).
read -r RATE CH BITS FRAMES < <(python3 "$LIB" prep "$INPUT" "$TMP/ref.raw")
say "Input: $FRAMES frames, ${BITS}-bit, ${CH}ch @ ${RATE} Hz -> S32_LE wire container"

# ── 2. TAP: start the usbmon reader before any audio flows ───────────────────
# modprobe runs first in the foreground so sudo caches credentials there;
# the backgrounded tap then inherits them without prompting into the void.
sudo modprobe usbmon
sudo python3 "$LIB" tap-usbmon "$BUS" "$DEVNUM" "$TMP/cap.raw" 2>"$TMP/tap.log" &
TAPPID=$!
sleep 0.6            # let the tap attach so the stream head is captured

# ── 3. PLAY: flat-out write, flow-controlled by the DAC's own clock ──────────
say "Playing to hw:$CARD,0 (no ALSA conversion possible on a hw: device)"
aplay -q -t raw -f S32_LE -r "$RATE" -c "$CH" -D "hw:$CARD,0" "$TMP/ref.raw"

sleep 0.6            # let the last queued URBs drain before stopping the tap
sudo kill "$TAPPID" 2>/dev/null || true
wait "$TAPPID" 2>/dev/null || true
TAPPID=""
cat "$TMP/tap.log" >&2 || true   # URB/payload/drop statistics from the tap

# ── 4. VERDICT: align, verify, emit artifacts ────────────────────────────────
cp "$TMP/cap.raw" "$PREFIX.wire.raw"
python3 "$LIB" finalize "$TMP/ref.raw" "$TMP/cap.raw" "$RATE" "$CH" \
        "$PREFIX" "linux/$(uname -r)" "$(basename "$INPUT")"
