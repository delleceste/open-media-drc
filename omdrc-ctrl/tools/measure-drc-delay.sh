#!/usr/bin/env bash
#
# measure-drc-delay.sh — measure the REAL MPD-FIFO -> DAC delay of the DRC chain
# by cross-correlating a test sweep played through MPD.
#
# Why this exists
# ---------------
# omdrc-ctrl's spectrum/VU analyzer taps MPD's pre-DRC fifo and must hold its
# display back by the chain delay to stay in sync with the audible (post-BruteFIR)
# sound.  The analytic model in app.py counts only BruteFIR (group delay + one
# partition); it ignores MPD's output buffer and virtual_oss's -s loopback
# buffering, so the real delay is much longer.  This script measures it directly.
#
# How
# ---
#   MPD ─► /dev/dsp.play ─►[virtual_oss #1]─► /dev/dsp.loop ─► BruteFIR ─┐
#                                                                         │
#   (calibration interposer, because /dev/dsp0 is PLAY-ONLY and cannot    │
#    be read back to observe BruteFIR's output)                           ▼
#                            real DAC ◄─[virtual_oss #2]◄─ /dev/dsp.tap ◄─┘
#                                              │
#                                  /dev/dsp.tap.loop  (recordable tap)
#
# We play a short log-sweep train through MPD with BOTH the DRC output and the
# "OMDRC Spectrum" fifo enabled, record the fifo tap (analyzer input) and the
# dsp.tap.loop tap (post-BruteFIR output), and cross-correlate.  The lag is the
# delay the analyzer must apply.  virtual_oss #2 adds its own small buffer; run
# `tap-overhead` once to measure it and pass it back via SUBTRACT_MS.
#
# Usage
#   tools/measure-drc-delay.sh                 # resamp@192k (the box's real mode)
#   MODE=native RATE=48000 tools/measure-drc-delay.sh
#   SUBTRACT_MS=NN tools/measure-drc-delay.sh  # subtract measured tap overhead
#   tools/measure-drc-delay.sh tap-overhead    # measure virtual_oss #2 latency
#
# Needs root for virtual_oss (uses sudo as drc.sh does), plus sox, mpc, brutefir,
# numpy.  Restores the previous DRC state on exit (trap).
set -u

# ── config (env-overridable) ─────────────────────────────────────────────────
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # omdrc-ctrl/
REPO="$(cd "$BASE/.." && pwd)"                            # open-media-drc/
GEOMETRY="${GEOMETRY:-120.blue}"
MODE="${MODE:-resamp}"            # resamp -> 192k via DRC-resamp; native -> RATE via DRC-native
VARIANT="${VARIANT:-}"           # matches configs/<geom>/brutefir-<rate><variant>.conf
case "$MODE" in
  resamp) RATE="${RATE:-192000}"; MPD_DRC_OUT="DRC-resamp" ;;
  native) RATE="${RATE:-48000}";  MPD_DRC_OUT="DRC-native" ;;
  *) echo "MODE must be 'resamp' or 'native'" >&2; exit 2 ;;
esac
DUR="${DUR:-7}"                  # seconds to record
TAP_S="${TAP_S:-50ms}"          # virtual_oss #2 buffer (small = less added latency)
SUBTRACT_MS="${SUBTRACT_MS:-0}" # virtual_oss #2 self-latency to remove (see tap-overhead)
FIFO="${FIFO:-/tmp/omdrc-spectrum.fifo}"
FIFO_RATE="${FIFO_RATE:-48000}" # must match mpd.conf "OMDRC Spectrum" format rate
# The real DAC, by role: /dev/dsp.dac is the symlink omdrc_audio keeps on the
# right card (pcm unit numbers follow USB attach order), /dev/dsp0 without it.
DAC_DEV="${DAC_DEV:-$([ -e /dev/dsp.dac ] && echo /dev/dsp.dac || echo /dev/dsp0)}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/omdrc-delay.XXXXXX")"
# The MPD config, from the MPD that is actually running: it is started with its
# config file as an argument, so the command line is the authority.  (This used
# to read $REPO/mpd/musicpd.conf — a file rendered into the checkout by the old
# install.sh, which no longer exists; the installed copy is what MPD reads.)
_mpd_conf() {
	# musicpd(FreeBSD) / mpd(Linux), invoked as: <binary> [opts] <config>
	for _c in $(pgrep -fl 'musicpd|mpd' 2>/dev/null | sed -n 's/.*[[:space:]]\(\/[^[:space:]]*\.conf\)$/\1/p'); do
		[ -f "$_c" ] && { echo "$_c"; return 0; }
	done
	for _c in "$PREFIX/etc/open-media-drc/musicpd.conf" \
	          "$PREFIX/etc/open-media-drc/mpd.conf"; do
		[ -f "$_c" ] && { echo "$_c"; return 0; }
	done
	return 1
}
PREFIX="${PREFIX:-/usr/local}"
MPD_CONF="${MPD_CONF:-$(_mpd_conf || true)}"
MUSIC_DIR=""
[ -n "$MPD_CONF" ] && MUSIC_DIR="$(grep -E '^[[:space:]]*music_directory' "$MPD_CONF" \
             | sed -E 's/.*"([^"]+)".*/\1/' | head -1)"
CAL_SUBDIR="_omdrc_cal"
CHIRP="$MUSIC_DIR/$CAL_SUBDIR/chirp.wav"
BF_CONF="$WORK/brutefir-cal.conf"
VOSS1_PID="$WORK/voss1.pid"
VOSS2_PID="$WORK/voss2.pid"

_sudo() { [ "$(id -u)" -eq 0 ] && "$@" || sudo "$@"; }
log() { printf '\033[36m[delay]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[delay] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }
need sox; need mpc; need brutefir; need virtual_oss
python3 -c 'import numpy' 2>/dev/null || die "python3 numpy not available"
[ -n "$MUSIC_DIR" ] || die "could not read music_directory from ${MPD_CONF:-any MPD config (is MPD running? try MPD_CONF=/path/to/mpd.conf)}"

# ── teardown (always restores the box) ───────────────────────────────────────
READER_PIDS=()
cleanup() {
  log "cleanup…"
  for p in "${READER_PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  mpc stop >/dev/null 2>&1 || true
  # stop our brutefir + both virtual_oss; drc.sh restore rebuilds the real chain
  pkill -f "brutefir .*$BF_CONF" 2>/dev/null || true
  _sudo killall virtual_oss 2>/dev/null || true   # pidfiles are root-owned; killall is simplest
  rm -rf "$MUSIC_DIR/$CAL_SUBDIR" 2>/dev/null || true
  mpc update "$CAL_SUBDIR" >/dev/null 2>&1 || true
  log "restoring previous DRC state via drc.sh restore"
  "$REPO/drc.sh" restore >/dev/null 2>&1 || \
    { mpc enable only "OKTO-DAC" >/dev/null 2>&1; mpc disable "OMDRC Spectrum" >/dev/null 2>&1; }
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

# ── make the test signal: a train of log sweeps in silence ───────────────────
make_chirp() {
  mkdir -p "$MUSIC_DIR/$CAL_SUBDIR" || die "cannot write $MUSIC_DIR/$CAL_SUBDIR"
  RAW="$WORK/chirp.raw"
  python3 - "$RAW" <<'PY'
import sys, numpy as np
# SINGLE sweep in a file LONGER than the capture window: with mpc repeat on this
# never loops during a (<=20s) capture, so the cross-correlation has exactly ONE
# peak — no periodic-train aliasing (which made the lag jump to a wrong D±k*gap).
rate=48000; dur=20.0; f0=200.0; f1=10000.0; swlen=0.08
# Aperiodic sweep offsets (s): no common period -> one unambiguous xcorr peak,
# while 3 sweeps give redundancy so a timing miss on one still correlates. File
# is longer than the capture window so mpc repeat never loops it back in.
offsets=[1.0, 2.3, 4.1]
x=np.zeros(int(dur*rate))
tt=np.arange(int(swlen*rate))/rate
k=(f1/f0)**(1/swlen)
sweep=0.7*np.sin(2*np.pi*f0*(k**tt-1)/np.log(k))
sweep*=np.hanning(sweep.size)
for off in offsets:
    s=int(off*rate)
    if s+sweep.size<x.size: x[s:s+sweep.size]+=sweep
xi=(np.clip(x,-1,1)*32767).astype('<i2')
np.repeat(xi,2).tofile(sys.argv[1])   # stereo, both channels identical
PY
  sox -t raw -e signed -b 16 -r 48000 -c 2 "$RAW" "$CHIRP" || die "sox chirp build failed"
  log "chirp -> $CHIRP"
  mpc update "$CAL_SUBDIR" >/dev/null
  for _ in $(seq 1 30); do
    mpc listall "$CAL_SUBDIR/chirp.wav" 2>/dev/null | grep -q chirp && break
    sleep 0.3
  done
  mpc listall "$CAL_SUBDIR/chirp.wav" 2>/dev/null | grep -q chirp \
    || die "MPD did not index the chirp under $CAL_SUBDIR (music_directory mismatch?)"
}

clean_chain() {   # kill any brutefir/virtual_oss and wait for ALL stale cuse nodes to go
  _sudo killall brutefir virtual_oss 2>/dev/null || true
  for _ in $(seq 1 40); do
    pgrep -x virtual_oss >/dev/null 2>&1 || pgrep -x brutefir >/dev/null 2>&1 || break
    sleep 0.2
  done
  _sudo killall -KILL virtual_oss brutefir 2>/dev/null || true
  # The cuse nodes (dsp.play/dsp.loop from a prior virtual_oss, dsp.tap*) must be
  # GONE before we recreate them, or virtual_oss fails with "Could not create
  # CUSE DSP device" and leaves a stale dsp.loop that brutefir can't open.
  for _ in $(seq 1 40); do
    [ -e /dev/dsp.play ] || [ -e /dev/dsp.loop ] || [ -e /dev/dsp.tap ] || [ -e /dev/dsp.tap.loop ] || break
    sleep 0.2
  done
  for n in /dev/dsp.play /dev/dsp.loop /dev/dsp.tap /dev/dsp.tap.loop; do
    [ -e "$n" ] && die "stale cuse node $n won't clear (zombie virtual_oss holding it?)"
  done
}

start_voss1() {   # MPD -> dsp.play -> dsp.loop  (mirror drc.sh exactly)
  log "virtual_oss #1 (dsp.play<->dsp.loop) @ ${RATE}Hz"
  _sudo virtual_oss -D "$VOSS1_PID" -r "$RATE" \
        -i 8 -C 2 -c 2 -b 32 -s 200ms -f /dev/null -a 0 -d dsp.play -L dsp.loop &
  # require the PROCESS alive (not just the node — a stale node would mask failure)
  for _ in $(seq 1 50); do
    pgrep -f "dsp.play" >/dev/null 2>&1 && [ -e /dev/dsp.loop ] && [ -e /dev/dsp.play ] \
      && { sleep 0.4; pgrep -f "dsp.play" >/dev/null 2>&1 && return 0; }
    sleep 0.1
  done
  die "virtual_oss #1 did not come up / stay up (dsp.play/dsp.loop)"
}

start_voss2() {   # BruteFIR -> dsp.tap -> the real DAC, tap on dsp.tap.loop
  # The interposer node is called dsp.tap, NOT dsp.dac: /dev/dsp.dac is the
  # symlink omdrc_audio keeps on the real card, and a cuse node of that name
  # would collide with it.
  # The DAC is PLAY-ONLY -> make it a playback master with -O + -R /dev/null
  # (the man-page idiom for a no-capture physical device; -f would open duplex
  # and fail).  -d creates the virtual device BruteFIR plays into; -l a loopback
  # tap of it.  Format flags must precede the device-creating flags.
  log "virtual_oss #2 (dsp.tap -> ${DAC_DEV}, tap dsp.tap.loop) @ ${RATE}Hz s=${TAP_S}"
  _sudo virtual_oss -D "$VOSS2_PID" -S -r "$RATE" -b 32 -c 2 -C 2 -s "$TAP_S" \
        -R /dev/null -O "$DAC_DEV" -d dsp.tap -l dsp.tap.loop &
  # require the voss2 PROCESS alive (matches "dsp.tap" in its argv), not just a
  # possibly-stale node, so a CUSE-create failure is caught instead of masked.
  for _ in $(seq 1 50); do
    pgrep -f "dsp.tap" >/dev/null 2>&1 && [ -e /dev/dsp.tap.loop ] && [ -e /dev/dsp.tap ] \
      && { sleep 0.4; pgrep -f "dsp.tap" >/dev/null 2>&1 && return 0; }
    sleep 0.1
  done
  die "virtual_oss #2 did not come up / stay up (CUSE-create failed? stale virtual_oss holding dsp.tap?)"
}

write_bf_conf() { # per-rate conf, but output -> /dev/dsp.tap and input -> /dev/dsp.loop
  SRC="$REPO/configs/$GEOMETRY/brutefir-${RATE}${VARIANT}.conf"
  [ -f "$SRC" ] || die "no brutefir conf: $SRC"
  perl -0pe '
    s/input\s+"left_in",\s*"right_in"\s*\{\s*\}/input "left_in", "right_in" {\n  device: "oss" { device: "\/dev\/dsp.loop"; };\n  sample: "S32_LE";\n  channels: 2\/0,1;\n}/s;
    s/output\s+"left_out",\s*"right_out"\s*\{\s*\}/output "left_out", "right_out" {\n  device: "oss" { device: "\/dev\/dsp.tap"; };\n  sample: "S32_LE";\n  channels: 2\/0,1;\n}/s;
  ' "$SRC" > "$BF_CONF"
  grep -q '/dev/dsp.tap' "$BF_CONF" || die "failed to inject dsp.tap output into $BF_CONF"
  log "brutefir cal conf -> $BF_CONF"
}

start_brutefir() {
  # brutefir -daemon forks and returns immediately, then exits a moment later if
  # it cannot open the loopback/DAC.  The first open often loses a race ("Invalid
  # argument" on /dev/dsp.loop), so retry up to 3x and confirm it STAYS up — the
  # same recipe drc.sh's start_brutefir uses.
  local attempt i
  for attempt in 1 2 3; do
    log "brutefir (cal) attempt $attempt -> /tmp/brutefir-cal.out"
    brutefir "$BF_CONF" -daemon >/tmp/brutefir-cal.out 2>&1 &
    i=0
    while [ "$i" -lt 12 ]; do
      sleep 0.3
      pgrep -f "brutefir .*$BF_CONF" >/dev/null 2>&1 && break
      i=$((i + 1))
    done
    if pgrep -f "brutefir .*$BF_CONF" >/dev/null 2>&1; then
      sleep 0.7
      pgrep -f "brutefir .*$BF_CONF" >/dev/null 2>&1 && { log "brutefir up"; return 0; }
    fi
    log "brutefir attempt $attempt failed:"; tail -3 /tmp/brutefir-cal.out 2>/dev/null | sed 's/^/    /'
    pkill -f "brutefir .*$BF_CONF" 2>/dev/null || true
    sleep 1
  done
  die "brutefir did not stay up after 3 attempts; see /tmp/brutefir-cal.out"
}

# ── main measurement ─────────────────────────────────────────────────────────
log "MODE=$MODE RATE=$RATE GEOMETRY=$GEOMETRY VARIANT='${VARIANT:-}' DUR=${DUR}s"
"$REPO/drc.sh" stop >/dev/null 2>&1 || true   # clean baseline (chain down, OKTO direct)
clean_chain                                   # ensure no stale virtual_oss/brutefir or cuse nodes
make_chirp
start_voss1
start_voss2
write_bf_conf
start_brutefir

# MPD outputs: DRC path + spectrum fifo on, direct DAC off
mpc disable "OKTO-DAC"     >/dev/null 2>&1 || true
mpc enable  "$MPD_DRC_OUT" >/dev/null 2>&1 || die "no MPD output '$MPD_DRC_OUT'"
[ -p "$FIFO" ] || { rm -f "$FIFO"; mkfifo "$FIFO"; }
mpc enable  "OMDRC Spectrum" >/dev/null 2>&1 || die "no MPD output 'OMDRC Spectrum'"

# Queue the chirp on repeat, but do NOT play yet: capture-taps.py opens both taps
# first (so the FIFO's first sample == playback start) and signals via ready-file.
mpc repeat on >/dev/null 2>&1 || true
mpc single off >/dev/null 2>&1 || true
mpc clear >/dev/null
mpc add "$CAL_SUBDIR/chirp.wav" >/dev/null || die "mpc add failed"

READY="$WORK/ready"; rm -f "$READY"
log "capturing fifo tap + DAC tap on one clock (DUR=${DUR}s)…"
python3 "$BASE/tools/capture-taps.py" \
    --fifo "$FIFO" --fifo-rate "$FIFO_RATE" \
    --tap /dev/dsp.tap.loop --tap-rate "$RATE" \
    --fmt s32le --dur "$DUR" --subtract-ms "$SUBTRACT_MS" \
    --ready-file "$READY" > "$WORK/result.txt" 2>&1 &
CAP_PID=$!; READER_PIDS+=($CAP_PID)

for _ in $(seq 1 50); do [ -f "$READY" ] && break; sleep 0.1; done
[ -f "$READY" ] || die "capture-taps did not become ready"
log "playing chirp through MPD…"
mpc play >/dev/null
wait "$CAP_PID" 2>/dev/null || true
mpc stop >/dev/null 2>&1 || true

echo "------------------------------------------------------------"
cat "$WORK/result.txt"
echo "------------------------------------------------------------"
log "raw lag = real MPD-FIFO -> DAC delay for MODE=$MODE @ ${RATE}Hz"
log "(includes virtual_oss #2's ~${TAP_S} tap buffer in lieu of the real /dev/dsp0"
log " output buffer; the model in app.py counts only BruteFIR group+partition)"
