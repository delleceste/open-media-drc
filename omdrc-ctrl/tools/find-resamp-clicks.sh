#!/usr/bin/env bash
#
# find-resamp-clicks.sh — hunt for clicks/glitches introduced at the MPD+soxr
# resampling stage of the DRC chain (the `drc.sh resamp` / `drc.sh 192` path).
#
# WHAT IT DOES
#   Plays a long, steady sine tone through MPD whose SOURCE rate differs from the
#   DRC rate, so MPD's soxr resampler is actively working (default: a 44.1 kHz
#   tone -> 192 kHz, the real `resamp` case, which crosses crystal families and
#   is a non-integer ratio — the most artifact-prone).  It taps MPD's resampled
#   output through a dedicated FIFO output ("OMDRC Clicktap" @ 192000:32:2) that
#   sits JUST AFTER soxr and BEFORE virtual_oss/brutefir, and inspects the actual
#   PCM sample-accurately for discontinuities.
#
#   This brackets the chain against glitch-usbtap.sh (which taps the USB endpoint
#   downstream of everything but is BLIND to silence-insertion underruns):
#     * clicks seen HERE  -> born in MPD / soxr (the resampler or its output).
#     * clean HERE but glitchy at the USB tap -> born in virtual_oss/brutefir/DAC.
#
#   The audible DRC-resamp -> brutefir -> DAC path keeps running throughout, so
#   MPD is paced at REAL TIME (a FIFO has natural back-pressure; tapping it does
#   not perturb the audible path).  That is what lets this reproduce timing /
#   underrun clicks, not just deterministic soxr artifacts.
#
# REQUIREMENTS
#   The realtime resamp chain must already be UP (brutefir + virtual_oss + the
#   DRC-resamp MPD output).  Bring it up DETACHED so virtual_oss survives:
#       daemon -f -o /tmp/drc-resamp.out -- ./drc.sh resamp
#   (running drc.sh from a controlling terminal and letting it exit kills the
#    backgrounded virtual_oss — use daemon(8), as the rc service does.)
#
# USAGE
#   omdrc-ctrl/tools/find-resamp-clicks.sh                 # 4x 90s @ 44.1k->192k
#   DUR=120 REPEATS=6 find-resamp-clicks.sh                # longer / more passes
#   SRC_RATE=48000 find-resamp-clicks.sh                   # 48k->192k (4x, integer)
#   SRC_RATE=88200 find-resamp-clicks.sh                   # 88.2k->192k (fractional)
#   F0=1000 THRESH_REL=1e-4 find-resamp-clicks.sh
#
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOOLS="$REPO/omdrc-ctrl/tools"

RATE="${RATE:-192000}"            # DRC / resampled rate (the FIFO tap rate)
SRC_RATE="${SRC_RATE:-44100}"     # SOURCE tone rate -> forces soxr to resample
F0="${F0:-997}"                   # tone frequency (Hz); 997 is non-harmonic
DUR="${DUR:-90}"                  # seconds of audio to capture per pass
REPEATS="${REPEATS:-4}"           # number of passes (clicks are 'random')
THRESH_REL="${THRESH_REL:-3e-4}"  # click if |residual| > THRESH_REL * tone_peak
GAIN_DB="${GAIN_DB:--6}"          # tone level (dBFS)
FIFO="${FIFO:-/tmp/omdrc-clicktap.fifo}"
OUT_NAME="${OUT_NAME:-OMDRC Clicktap}"
DRC_OUT="${DRC_OUT:-DRC-resamp}"

MUSIC_DIR="$(grep -E '^[[:space:]]*music_directory' "$REPO/mpd/musicpd.conf" | sed -E 's/.*"([^"]+)".*/\1/' | head -1)"
SUB="_omdrc_clicktest"
FILELEN=$(( DUR + 8 )); [ "$FILELEN" -lt 60 ] && FILELEN=60
WORK="$(mktemp -d "${TMPDIR:-/tmp}/omdrc-clicks.XXXXXX")"
TONE="$MUSIC_DIR/$SUB/tone_${F0}hz_${SRC_RATE}.wav"
EVENTS="$WORK/events.log"

log() { printf '\033[36m[clicks]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[clicks] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v sox >/dev/null 2>&1 || die "sox not found"
command -v mpc >/dev/null 2>&1 || die "mpc not found"
python3 -c 'import numpy' 2>/dev/null || die "python3 numpy not available"
[ -n "$MUSIC_DIR" ] || die "could not read music_directory from mpd/musicpd.conf"

# ── preconditions: the realtime resamp chain must be live ────────────────────
pgrep -f '(^|/)brutefir .*-daemon' >/dev/null 2>&1 || \
  die "brutefir not running — bring the chain up:  daemon -f -o /tmp/drc-resamp.out -- $REPO/drc.sh resamp"
pgrep -f 'virtual_oss .*dsp.loop' >/dev/null 2>&1 || \
  die "virtual_oss not running — bring the chain up (see above)"
mpc outputs 2>/dev/null | grep -q "($DRC_OUT) is enabled" || \
  log "WARNING: MPD output '$DRC_OUT' is not enabled — realtime pacing may be off"
mpc outputs 2>/dev/null | grep -q "($OUT_NAME)" || \
  die "MPD output '$OUT_NAME' missing — was musicpd.conf updated + MPD restarted?"

# ── teardown ─────────────────────────────────────────────────────────────────
CAP_PID=""
cleanup() {
  [ -n "$CAP_PID" ] && kill "$CAP_PID" 2>/dev/null || true
  mpc stop >/dev/null 2>&1 || true
  mpc disable "$OUT_NAME" >/dev/null 2>&1 || true
  rm -rf "$MUSIC_DIR/$SUB" 2>/dev/null || true
  mpc update "$SUB" >/dev/null 2>&1 || true
  rm -rf "$WORK"
  log "cleanup done (DRC chain left untouched: $DRC_OUT still enabled)"
}
trap cleanup EXIT INT TERM

# ── build the test tone ──────────────────────────────────────────────────────
mkdir -p "$MUSIC_DIR/$SUB" || die "cannot write $MUSIC_DIR/$SUB"
log "building ${FILELEN}s ${F0}Hz sine @ ${SRC_RATE}Hz (gain ${GAIN_DB}dB) -> $TONE"
sox -n -r "$SRC_RATE" -b 16 -c 2 "$TONE" synth "$FILELEN" sine "$F0" gain "$GAIN_DB" \
  || die "sox tone build failed"
mpc update "$SUB" >/dev/null 2>&1
for _ in $(seq 1 40); do
  mpc listall "$SUB/$(basename "$TONE")" 2>/dev/null | grep -q "$(basename "$TONE")" && break
  sleep 0.3
done
mpc listall "$SUB/$(basename "$TONE")" 2>/dev/null | grep -q "$(basename "$TONE")" \
  || die "MPD did not index the tone under $SUB (music_directory mismatch?)"

# Pre-create the FIFO so BOTH ends open an existing pipe (MPD writes on play, the
# reader opens read-only non-blocking before play).  measure-drc-delay.sh idiom.
[ -p "$FIFO" ] || { rm -f "$FIFO"; mkfifo "$FIFO"; }

mpc repeat off >/dev/null 2>&1 || true
mpc single on  >/dev/null 2>&1 || true   # stop at end of the tone, don't roll on
mpc consume off >/dev/null 2>&1 || true

# ── repeated passes ──────────────────────────────────────────────────────────
log "tap point: '$OUT_NAME' FIFO @ ${RATE}:32:2 (MPD soxr output, before virtual_oss/brutefir)"
log "running $REPEATS pass(es) of ${DUR}s each — ${SRC_RATE}Hz -> ${RATE}Hz"
tot_clicks=0; tot_drop=0
for n in $(seq 1 "$REPEATS"); do
  log "── pass $n/$REPEATS ──"
  mpc enable "$OUT_NAME" >/dev/null 2>&1 || die "could not enable '$OUT_NAME'"
  mpc clear >/dev/null; mpc add "$SUB/$(basename "$TONE")" >/dev/null

  READY="$WORK/ready.$n"; rm -f "$READY"
  RES="$WORK/result.$n"
  python3 "$TOOLS/clicktap-stream.py" "$FIFO" \
      --rate "$RATE" --channels 2 --fmt s32le --f0 "$F0" \
      --thresh-rel "$THRESH_REL" --dur "$DUR" \
      --ready-file "$READY" --events-file "$EVENTS" --label "pass$n" > "$RES" 2>&1 &
  CAP_PID=$!

  for _ in $(seq 1 50); do [ -f "$READY" ] && break; sleep 0.1; done
  [ -f "$READY" ] || { kill "$CAP_PID" 2>/dev/null; die "capture reader did not become ready"; }
  mpc play >/dev/null
  wait "$CAP_PID" 2>/dev/null; CAP_PID=""
  mpc stop >/dev/null 2>&1 || true
  mpc disable "$OUT_NAME" >/dev/null 2>&1 || true

  sed -n '1,200p' "$RES"
  c=$(sed -n 's/.*clicks=\([0-9]*\).*/\1/p' "$RES" | tail -1); c=${c:-0}
  d=$(sed -n 's/.*dropouts=\([0-9]*\).*/\1/p' "$RES" | tail -1); d=${d:-0}
  tot_clicks=$(( tot_clicks + c )); tot_drop=$(( tot_drop + d ))
  echo
done

echo "============================================================"
log "SUMMARY over $REPEATS pass(es), ${SRC_RATE}Hz -> ${RATE}Hz, ${DUR}s each:"
log "   total clicks=$tot_clicks  total dropouts=$tot_drop"
if [ "$tot_clicks" -eq 0 ] && [ "$tot_drop" -eq 0 ]; then
  log "   -> MPD/soxr output is CLEAN at this tap; glitches (if heard) are"
  log "      downstream (virtual_oss/brutefir/DAC). Cross-check: ./glitch-usbtap.sh"
else
  log "   -> glitches present in MPD's resampled output -> MPD/soxr stage."
  log "   event log: $EVENTS"
  cp "$EVENTS" "$REPO/resamp-clicks-events.log" 2>/dev/null && \
    log "   (copied to $REPO/resamp-clicks-events.log)"
fi
echo "============================================================"
