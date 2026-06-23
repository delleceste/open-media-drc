#!/usr/bin/env bash
# glitch-monitor.sh — lightweight, always-on glitch poller for the DRC chain.
#
# Runs as a daemon (started by `glitch-debug.sh on`).  Every GLITCH_INTERVAL
# seconds it samples several independent signals and appends one structured line
# to glitch.log for every *new* anomaly.  It is deliberately best-effort: each
# source is optional and a missing/quiet source simply produces no events.
#
# Sources (FreeBSD focus — see glitch-debug.sh for the rationale):
#   brutefir  new warning/under-/over-run lines on brutefir's stdout
#             (/tmp/brutefir.out — the daemon's whole-life stdout, see drc.sh)
#   dmesg     new uaudio / USB error lines in the kernel ring buffer
#   mpd       new error/underrun lines in the MPD log
#   pcm       any dev.pcm.* counter whose name carries under/over/err/xrun and
#             that increased since the previous poll (generic, name-agnostic)
#
# Resolution is the poll interval (default 1 s) — fine for periods of seconds to
# minutes.  For sub-second / sample-accurate timing use glitch-usbtap.sh, which
# taps the USB endpoint directly.
#
# Log line format (one event per line, machine- and human-readable):
#   2026-06-23T12:34:56 epoch=1750000000 stage=brutefir kind=warn msg=...
#
# Implementation note: read_new / dmesg_new persist their offsets to small files
# under STATE_DIR rather than shell variables, because they are invoked through
# process substitution (a subshell) where variable updates would not survive.
#
set -u

base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${GLITCH_LOG:-$base_dir/glitch.log}"
STATE_FILE="${GLITCH_STATE:-$base_dir/.glitch-debug.enabled}"
INTERVAL="${GLITCH_INTERVAL:-1}"
BRUTEFIR_OUT="${GLITCH_BRUTEFIR_OUT:-/tmp/brutefir.out}"
STATE_DIR="${TMPDIR:-/tmp}/glitch-monitor.$(id -u)"

# Regexes that mark a line as an anomaly worth logging.
BF_RE='xrun|under|over|skip|drop|clip|fail|error|warn'
DMESG_RE='uaudio|usb_err|USB_ERR|ehci|xhci|timeout|babble|stall'
MPD_RE='error|fail|problem|under-?run|over-?run|exception'

# stat that works on FreeBSD (-f%z) and Linux (-c%s) alike.
fsize() { stat -f%z "$1" 2>/dev/null || stat -c%s "$1" 2>/dev/null || echo 0; }

now_ts()    { date +%Y-%m-%dT%H:%M:%S 2>/dev/null || echo '?'; }
now_epoch() { date +%s 2>/dev/null || echo 0; }

# Append one event.  msg is %q-quoted so it always stays on a single line.
log_ev() { # stage kind msg...
  local stage="$1" kind="$2"; shift 2
  printf '%s epoch=%s stage=%s kind=%s msg=%q\n' \
    "$(now_ts)" "$(now_epoch)" "$stage" "$kind" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

# disk-backed key for a watched path (survives the subshell of <(...))
keyfile() { printf '%s/%s' "$STATE_DIR" "$(printf '%s' "$1" | tr '/.' '__')"; }

# ── per-file tail-from-offset (handles truncation / rotation) ────────────────
read_new() { # path  -> prints bytes appended since last poll
  local p="$1" sz cur of
  [ -f "$p" ] || return 0
  sz=$(fsize "$p")
  of="$(keyfile "$p").off"
  cur=$(cat "$of" 2>/dev/null || echo "")
  if [ -z "$cur" ]; then echo "$sz" > "$of"; return 0; fi   # first sight: skip backlog
  [ "$sz" -lt "$cur" ] && cur=0                              # truncated → re-read all
  if [ "$sz" -gt "$cur" ]; then
    tail -c +$((cur + 1)) "$p" 2>/dev/null
    echo "$sz" > "$of"
  fi
}

# ── dmesg: emit lines appended since the last-seen line ──────────────────────
dmesg_new() {
  local cur lastf last
  cur="$(dmesg 2>/dev/null)" || return 0
  lastf="$STATE_DIR/dmesg.last"
  if [ ! -f "$lastf" ]; then
    printf '%s\n' "$cur" | tail -1 > "$lastf"; return 0
  fi
  last="$(cat "$lastf" 2>/dev/null)"
  printf '%s\n' "$cur" | awk -v last="$last" 'f{print} $0==last{f=1}'
  printf '%s\n' "$cur" | tail -1 > "$lastf"
}

# ── dev.pcm.* counters: log any monotonically-increasing error counter ───────
# Called directly (not via a subshell), so the PCM associative array persists.
declare -A PCM
pcm_new() {
  local line key val prev
  while IFS= read -r line; do
    key="${line%%:*}"; val="${line#*: }"
    case "$val" in ''|*[!0-9]*) continue ;; esac
    prev="${PCM[$key]:-}"
    if [ -n "$prev" ] && [ "$val" -gt "$prev" ]; then
      log_ev pcm counter "$key +$((val - prev)) (=$val)"
    fi
    PCM[$key]="$val"
  done < <(sysctl dev.pcm 2>/dev/null | grep -iE '(under|over|err|xrun)')
}

# ── locate the MPD log once (best effort) ────────────────────────────────────
find_mpd_log() {
  [ -n "${GLITCH_MPD_LOG:-}" ] && { echo "$GLITCH_MPD_LOG"; return; }
  local conf
  conf=$(ps -ax -o args= 2>/dev/null \
         | awk '/[m]usicpd|[m]pd /{for(i=1;i<=NF;i++) if($i ~ /\.conf$/){print $i; exit}}')
  [ -n "$conf" ] && [ -f "$conf" ] || return 0
  awk -F'"' '/^[[:space:]]*log_file/{print $2; exit}' "$conf" 2>/dev/null
}

# ── scan one source's new lines against a regex, log matches ─────────────────
scan() { # stage regex < newlines
  local stage="$1" re="$2" line
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    printf '%s\n' "$line" | grep -qiE "$re" && log_ev "$stage" warn "$line"
  done
}

cleanup() { log_ev monitor stop "glitch-monitor exiting"; exit 0; }
trap cleanup TERM INT

# Fresh offset state on every (re)start so we skip pre-existing backlog.
rm -rf "$STATE_DIR" 2>/dev/null; mkdir -p "$STATE_DIR"
MPD_LOG="$(find_mpd_log)"
log_ev monitor start "glitch-monitor up (interval=${INTERVAL}s, mpd_log=${MPD_LOG:-none})"

while :; do
  # Self-terminate when the global switch is turned off.
  [ -f "$STATE_FILE" ] || cleanup

  scan brutefir "$BF_RE"    < <(read_new "$BRUTEFIR_OUT")
  scan dmesg    "$DMESG_RE" < <(dmesg_new)
  [ -n "$MPD_LOG" ] && scan mpd "$MPD_RE" < <(read_new "$MPD_LOG")
  pcm_new

  sleep "$INTERVAL"
done
