#!/usr/bin/env bash
set -euo pipefail

# ── local configuration ───────────────────────────────────────────────────────
GEOMETRY="120.blue"   # speaker geometry / filter set to use

VIRTUAL_OSS_PID=/tmp/virtual_oss.pid
VIRTUAL_OSS_ARGS="-i 8 -C 2 -c 2 -b 32 -s 200ms -f /dev/null -a 0 -d dsp1 -a 0 -l dsp.loop"

IS_LINUX=false
[ "$(uname)" = "Linux" ] && IS_LINUX=true

# ── paths ─────────────────────────────────────────────────────────────────────
# Resolve the directory this script lives in, so the tool is portable: it works
# from any checkout location and for any user, with no hardcoded $HOME or path.
base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="$base_dir/last_arg"
# Power state (on/off) kept separately from the rate: `off` records "off" here
# but leaves STATE_FILE (the remembered rate) intact, so `restore` stays off
# across a reboot yet can bring DRC back at the last rate when turned on.
POWER_FILE="$base_dir/last_power"

# Skip sudo when already root (service files run as root); avoids the sudo
# parent+monitor process tree that results in multiple processes in ps.
_sudo() { [ "$(id -u)" -eq 0 ] && "$@" || sudo "$@"; }

state_to_args() {
  local state="$1"
  case "$state" in
    ""|"off")
      printf '%s\n' "${state:-off}"
      return
      ;;
  esac

  # Backward compatibility: older last_arg files stored "GEOMETRY rate [variant]".
  set -- $state
  if [ "${1:-}" != "resamp" ] && ! [[ "${1:-}" =~ ^[0-9]+$ ]]; then
    shift
  fi

  if [ "$#" -eq 0 ]; then
    printf 'off\n'
    return
  fi

  printf '%s' "$1"
  shift
  if [ "$#" -gt 0 ]; then
    printf ' %s' "$@"
  fi
  printf '\n'
}

format_rate() {
  case "$1" in
    44100)  printf '44.1k\n' ;;
    48000)  printf '48k\n' ;;
    88200)  printf '88.2k\n' ;;
    96000)  printf '96k\n' ;;
    192000) printf '192k\n' ;;
    *)      printf '%s Hz\n' "$1" ;;
  esac
}

# Accept shorthand sample-rate inputs and map them to the canonical Hz value:
# bare kHz (96, 88, 44.1) and an optional "k" suffix (96k, 44.1k, 192k).
# 88 / 88.2 both mean 88200 (88.2 kHz).  Unrecognised input is passed
# through unchanged so the later config-existence check reports it.
normalize_rate() {
  local r="${1%[kK]}"            # strip a trailing k/K: 96k -> 96, 44.1k -> 44.1
  case "$r" in
    44|44.1|44100) printf '44100\n' ;;
    48|48000)      printf '48000\n' ;;
    88|88.2|88200) printf '88200\n' ;;
    96|96000)      printf '96000\n' ;;
    192|192000)    printf '192000\n' ;;
    *)             printf '%s\n' "$1" ;;
  esac
}

state_label() {
  local state mode variant profile
  state=$(state_to_args "$1")
  case "$state" in
    ""|"off")
      printf 'off\n'
      return
      ;;
  esac

  set -- $state
  mode="${1:-}"
  variant="${2:-}"
  profile="${variant:-Flat}"

  if [ "$mode" = "resamp" ]; then
    printf '%s auto-resample\n' "$profile"
  elif [[ "$mode" =~ ^[0-9]+$ ]]; then
    printf '%s %s\n' "$profile" "$(format_rate "$mode")"
  else
    printf '%s\n' "$state"
  fi
}

# Map a saved state string to the actual brutefir/DAC sample rate, or "" for
# off/unknown.  Used to detect a rate change (which needs the OKTO DAC prime).
state_to_rate() {
  local s
  s=$(state_to_args "$1")
  # shellcheck disable=SC2086
  set -- $s
  case "${1:-}" in
    resamp) printf '192000\n' ;;
    [0-9]*) printf '%s\n' "$1" ;;
    *)      printf '\n' ;;
  esac
}

stop_virtual_oss() {
  local pid
  pid=$(_sudo cat "$VIRTUAL_OSS_PID" 2>/dev/null) && _sudo kill "$pid" 2>/dev/null || true
  _sudo killall virtual_oss 2>/dev/null || true
  _sudo rm -f "$VIRTUAL_OSS_PID"
  # pgrep needs no root; escalate to SIGKILL after ~3 s if still alive
  local i=0
  while pgrep -q virtual_oss 2>/dev/null; do
    if [ "$i" -ge 15 ]; then
      _sudo killall -KILL virtual_oss 2>/dev/null || true
      break
    fi
    sleep 0.2
    i=$((i + 1))
  done
}

usage() {
  echo "Usage: $0 <rate>|resamp|restore|off|stop|status [variant]"
  echo "  rate     : 44100 | 48000 | 88200 | 96000 | 192000"
  echo "             shorthand ok: 44.1 48 88.2 96 192, optional k (96k, 44.1k)"
  echo "             native mode: select the rate matching the source track;"
  echo "             MPD uses DRC-native format *:*:* and does not resample"
  echo "  resamp   : MPD resamples everything to 192000 Hz"
  echo "  restore  : re-apply the last saved state (reads last_arg file);"
  echo "             falls back to 192000 if no previous active state exists"
  echo "  off      : stop brutefir and DRC; enable direct DAC output; record"
  echo "             the off state so a reboot stays off"
  echo "  stop     : like off but transient — does NOT record the off state."
  echo "             Used by the service stop-paths (shutdown, USB unplug) so a"
  echo "             reboot of a running system is restored, not left off"
  echo "  status   : show DRC state, virtual_oss rate, brutefir, and MPD output"
  echo "             (also the default when no argument is given)"
  echo "  variant  : optional filter variant, e.g. +2dB (default: none)"
  echo
  echo "  Geometry is fixed to: $GEOMETRY"
  echo "  Edit GEOMETRY at the top of this script to change it."
  echo
  echo "Examples:"
  echo "  $0 192000"
  echo "  $0 192000 +2dB"
  echo "  $0 resamp"
  echo "  $0 restore"
  echo "  $0 status"
  echo "  $0 off"
}

# ── default action: with no arguments, report status ─────────────────────────
# A bare `drc.sh` is a query, not a mutation — fall through to the status block
# below (which runs lock-free) rather than printing usage and exiting non-zero.
[ $# -eq 0 ] && set -- status

# ── restore: re-apply the last saved state ───────────────────────────────────
if [ $# -eq 1 ] && [ "$1" = "restore" ]; then
  # Honour the on/off state recorded at shutdown.  If DRC was off, stay off;
  # otherwise fall through and restore the last sample rate.  Kept separate from
  # STATE_FILE so turning off never erases the remembered rate.
  if [ -f "$POWER_FILE" ] && [ "$(cat "$POWER_FILE")" = "off" ]; then
    echo "Last power state was off — leaving DRC disabled (direct DAC)"
    exec "$0" off
  fi
  state=""
  [ -f "$STATE_FILE" ] && state=$(cat "$STATE_FILE")
  args=$(state_to_args "$state")
  case "$args" in
    off|"")
      echo "No previous active state — starting at default 192000 Hz"
      exec "$0" 192000
      ;;
    *)
      echo "Restoring last state: $(state_label "$args")"
      # shellcheck disable=SC2086
      exec "$0" $args
      ;;
  esac
fi

# ── status: show DRC state, virtual_oss rate, brutefir, and MPD output ───────
if [ $# -eq 1 ] && [ "$1" = "status" ]; then
  _st_drc="off"
  [ -f "$STATE_FILE" ] && _st_drc=$(state_to_args "$(cat "$STATE_FILE")")

  # virtual_oss: find the -r argument in the running process command line
  _st_voss_rate=""
  if ! $IS_LINUX; then
    _st_voss_rate=$(ps -ax -o args= 2>/dev/null \
      | awk '($1=="virtual_oss" || $1~/\/virtual_oss$/) && /-r/ {for(i=1;i<=NF;i++) if($i=="-r"){print $(i+1); exit}}')
  fi

  # Linux: sample rate of the active ALSA playback stream (DAC output).
  # hw_params says "closed" when the stream is idle.
  # Avoid nextfile (gawk-only); read each file individually instead.
  _st_alsa_rate=""
  if $IS_LINUX; then
    for _f in /proc/asound/card*/pcm*p/sub*/hw_params; do
      [ -f "$_f" ] || continue
      read -r _first < "$_f" 2>/dev/null || continue
      [ "$_first" = "closed" ] && continue
      _st_alsa_rate=$(awk '/^rate:/{print $2; exit}' "$_f")
      [ -n "$_st_alsa_rate" ] && break
    done
    unset _f _first
  fi

  # brutefir: extract rate and optional variant from the running conf path
  _st_bf_args=$(ps -ax -o args= 2>/dev/null | awk '($1=="brutefir" || $1~/\/brutefir$/) && /\.conf/{print; exit}')
  _st_bf_conf=$(echo "$_st_bf_args" | grep -o 'brutefir-[0-9][^ /]*\.conf' | head -1) || true
  _st_bf_rate=$(echo "$_st_bf_conf" | sed 's/brutefir-\([0-9]*\).*/\1/')
  _st_bf_var=$(echo "$_st_bf_conf"  | sed 's/brutefir-[0-9]*//;s/\.conf//')

  # MPD via mpc (mpc exits non-zero when MPD is unreachable)
  _st_mpc=$(mpc status 2>/dev/null) || _st_mpc=""
  _st_mpc_state=$(echo "$_st_mpc" | sed -n 's/.*\[\(playing\|paused\|stopped\)\].*/\1/p')
  [ -z "$_st_mpc_state" ] && _st_mpc_state="stopped"
  # audio: and bitrate: lines only appear when playing/paused; grep returns 1 on no match
  _st_mpc_audio=$(echo "$_st_mpc" | grep -i 'audio:'   | sed 's/^[^:]*:[[:space:]]*//') || true
  _st_mpc_br=$(echo    "$_st_mpc" | grep -i 'bitrate:' | sed 's/^[^:]*:[[:space:]]*//')  || true
  _st_mpc_song=$(mpc current 2>/dev/null) || _st_mpc_song=""

  # Active config reflects what is actually processing now, not STATE_FILE
  [ -z "$_st_bf_rate" ] && _st_drc="off"

  printf "%-17s %s\n" "Geometry:"    "$GEOMETRY"
  printf "%-17s %s\n" "Active config:" "$(state_label "$_st_drc")"
  if $IS_LINUX; then
    if [ -n "$_st_alsa_rate" ]; then
      printf "%-17s running  %s Hz\n" "ALSA:"  "$_st_alsa_rate"
    else
      printf "%-17s not running\n"    "ALSA:"
    fi
  elif [ -n "$_st_voss_rate" ]; then
    printf "%-17s running  %s Hz\n"  "virtual_oss:"  "$_st_voss_rate"
  else
    printf "%-17s not running\n"     "virtual_oss:"
  fi
  if [ -n "$_st_bf_rate" ]; then
    printf "%-17s running  %s Hz%s\n" "brutefir:" \
      "$_st_bf_rate" "${_st_bf_var:+  $_st_bf_var}"
  else
    printf "%-17s not running\n" "brutefir:"
  fi
  echo ""
  printf "%-17s %s\n" "MPD:"         "$_st_mpc_state"
  [ -n "$_st_mpc_song" ]  && printf "%-17s %s\n" "Song:"         "$_st_mpc_song"
  [ -n "$_st_mpc_audio" ] && printf "%-17s %s\n" "Output audio:" "$_st_mpc_audio"
  [ -n "$_st_mpc_br"    ] && printf "%-17s %s\n" "Bitrate:"      "$_st_mpc_br"

  # Rate comparison: MPD output rate vs audio sink rate.
  # Linux: compare against brutefir conf rate (always available when bf runs);
  # ALSA hw rate is shown as a bonus suffix when it can be detected.
  if $IS_LINUX; then
    if [ -n "$_st_bf_rate" ] && [ -n "$_st_mpc_audio" ]; then
      _st_mpd_rate=$(echo "$_st_mpc_audio" | cut -d: -f1)
      _st_alsa_suffix=${_st_alsa_rate:+  [ALSA: ${_st_alsa_rate} Hz]}
      echo ""
      if [ "$_st_mpd_rate" = "$_st_bf_rate" ]; then
        printf "%-17s MPD %s Hz = brutefir %s Hz  [match]%s\n" \
          "Rate:" "$_st_mpd_rate" "$_st_bf_rate" "$_st_alsa_suffix"
      else
        printf "%-17s MPD %s Hz != brutefir %s Hz  [MISMATCH]%s\n" \
          "Rate:" "$_st_mpd_rate" "$_st_bf_rate" "$_st_alsa_suffix"
      fi
    fi
  elif [ -n "$_st_voss_rate" ] && [ -n "$_st_mpc_audio" ]; then
    _st_mpd_rate=$(echo "$_st_mpc_audio" | cut -d: -f1)
    echo ""
    if [ "$_st_mpd_rate" = "$_st_voss_rate" ]; then
      printf "%-17s MPD %s Hz = virtual_oss %s Hz  [match]\n" \
        "Rate:" "$_st_mpd_rate" "$_st_voss_rate"
    else
      printf "%-17s MPD %s Hz != virtual_oss %s Hz  [MISMATCH]\n" \
        "Rate:" "$_st_mpd_rate" "$_st_voss_rate"
    fi
  fi
  exit 0
fi

# ── serialize mutating runs ──────────────────────────────────────────────────
# Boot presence-probe, devd ATTACH/DETACH and interactive runs can otherwise
# overlap: one run's stop_brutefir kills another's freshly-started brutefir and
# its virtual_oss teardown yanks /dev/dsp.loop out from under it, leaving
# virtual_oss orphaned with brutefir down (the "off + virtual_oss running"
# state).  Re-exec under a lock so only one mutating run proceeds at a time.
# Portable: lockf(1) on FreeBSD, flock(1) on Linux; if neither is present we
# proceed unlocked rather than fail.  restore/status above run lock-free —
# restore re-execs into a rate/off run, which lands here and takes the lock.
if [ -z "${DRC_LOCKED:-}" ]; then
  export DRC_LOCKED=1
  LOCK_FILE="${TMPDIR:-/tmp}/drc.lock"
  if command -v lockf >/dev/null 2>&1; then
    exec lockf -s -t 30 "$LOCK_FILE" "$0" "$@"
  elif command -v flock >/dev/null 2>&1; then
    # Linux: acquire the lock on an explicit fd we control (9) instead of the
    # command form `flock FILE "$0" "$@"`.  The command form leaves the lock fd
    # open *without* close-on-exec, so the brutefir daemon spawned later
    # inherits it and keeps the advisory lock for its entire lifetime.  The next
    # mutating run then deadlocks — most visibly `drc.sh off`: it must hold the
    # lock to run stop_brutefir, but the lock is not released until brutefir
    # (the very process off needs to kill) exits, so flock waits the full 30 s
    # and the run silently does nothing.  Holding the lock on a known fd lets us
    # close it in the child when launching brutefir (see start_brutefir: 9>&-).
    # FreeBSD's lockf above does not leak the fd into the daemon, so it keeps
    # the plain re-exec form.
    exec 9>"$LOCK_FILE"
    if ! flock -w 30 9; then
      echo "drc.sh: another run holds $LOCK_FILE; aborting" >&2
      exit 1
    fi
  fi
fi

# ── argument parsing ──────────────────────────────────────────────────────────
if [ $# -eq 1 ] && { [ "$1" = "off" ] || [ "$1" = "stop" ]; }; then
  # off  = user intent to disable DRC — records the off state below.
  # stop = transient teardown used by the service stop-paths (shutdown, USB
  #        unplug); identical teardown but does NOT touch the saved state, so a
  #        clean reboot of a running system restores it instead of staying off.
  mode="$1"
  rate=""
  variant=""
elif [ $# -eq 1 ] || [ $# -eq 2 ]; then
  rate="$1"
  variant="${2:-}"
  if [ "$rate" = "resamp" ]; then
    mode="resamp"
    actual_rate=192000
  else
    mode="normal"
    # Canonicalise shorthand (96, 44.1k, 192 …) to Hz so the config path,
    # STATE_FILE, restore and rate-change detection all use the full value.
    rate="$(normalize_rate "$rate")"
    actual_rate="$rate"
  fi
else
  usage
  exit 1
fi

# Detect a sample-rate change.  The OKTO DAC stays silent on the first stream
# opened at a new rate (it shows "play" and provides USB feedback but routes no
# audio); a second open at the same rate fixes it.  When the rate changes we
# prime the DAC below so a single drc.sh run no longer has to be issued twice.
prev_rate=""
[ -f "$STATE_FILE" ] && prev_rate=$(state_to_rate "$(cat "$STATE_FILE")")
prime=""
if [ "$mode" != "off" ] && [ "$mode" != "stop" ] && [ "$prev_rate" != "$actual_rate" ]; then
  prime=1
fi

# brutefir renames its forked worker processes via prctl(PR_SET_NAME) on Linux
# (compat.c: set_thread_name -> "input"/"output"/...), so their `comm` is no
# longer "brutefir" and `pgrep -x` / `killall` by name match nothing.  On
# FreeBSD set_thread_name is a no-op, which is why name matching worked there.
# Match the full command line instead so detection and teardown work on both.
bf_pattern='(^|/)brutefir .*-daemon'
bf_running() { pgrep -f "$bf_pattern" > /dev/null 2>&1; }

stop_brutefir() {
  if bf_running; then
    echo "stopping brutefir"
    pkill -f "$bf_pattern" 2>/dev/null || true
    # Wait for the process to actually exit so it releases the DAC
    # (/dev/dsp0) and the loopback before we restart.  A bare "sleep 1"
    # is not enough when the (USB) DAC is slow to release — that race is
    # what made the new brutefir silently fail to open the device on the
    # first run.  Escalate to SIGKILL after ~5 s.
    local i=0
    while bf_running; do
      if [ "$i" -ge 25 ]; then
        pkill -KILL -f "$bf_pattern" 2>/dev/null || true
        sleep 0.5
        break
      fi
      sleep 0.2
      i=$((i + 1))
    done
  else
    echo "brutefir not running"
  fi
}

start_brutefir() {
  local attempt i
  for attempt in 1 2 3; do
    echo "starting brutefir (attempt $attempt): $conf_file"
    # 9>&- closes the inherited flock lock fd (Linux) in the daemon so it does
    # not hold the lock for its whole lifetime and deadlock the next run; a
    # no-op on FreeBSD, where fd 9 is not the lock.  See the lock block above.
    brutefir "$conf_file" -daemon 9>&- > /tmp/brutefir.out 2>&1 || true
    # brutefir -daemon forks and the parent returns 0 immediately, before
    # the daemon has opened the audio devices.  Poll until the daemon shows
    # up, then confirm it *stays* up — it exits a moment later if it cannot
    # open the DAC / loopback.  This is the verification that was missing.
    i=0
    while [ "$i" -lt 10 ]; do
      sleep 0.3
      if bf_running; then
        break
      fi
      i=$((i + 1))
    done
    if bf_running; then
      sleep 0.5
      if bf_running; then
        echo "brutefir running"
        return 0
      fi
    fi
    echo "brutefir did not stay up; last output:"
    tail -n 5 /tmp/brutefir.out 2>/dev/null | sed 's/^/  /' || true
    pkill -f "$bf_pattern" 2>/dev/null || true
    sleep 1
  done
  return 1
}

# ── stop brutefir ────────────────────────────────────────────────────────────
stop_brutefir

# ── off / stop: re-enable direct DAC, stop virtual_oss ───────────────────────
if [ "$mode" = "off" ] || [ "$mode" = "stop" ]; then
  # Tear down the DRC chain first so /dev/dsp0 is free before the direct
  # output opens it (the DAC is single-open: vchans off / bit-perfect).
  if ! $IS_LINUX; then
    echo "stopping virtual_oss"
    stop_virtual_oss
  fi
  # Enable ONLY the direct DAC output — this disables every other output.
  # NB: mpc has no "disable all" keyword (it errors "all: no such output");
  # "enable only <name>" is the correct idiom: it enables the named output
  # and disables all others atomically.
  mpc enable only "OKTO-DAC"
  # Only an explicit `off` records the off state (so a reboot/restore stays
  # off).  `stop` is a transient teardown (service shutdown / USB unplug) and
  # leaves last_power untouched, so a running system that simply reboots is
  # restored rather than left off.  STATE_FILE (the rate) is never touched here.
  if [ "$mode" = "off" ]; then
    echo "off" > "$POWER_FILE"
    chmod 644 "$POWER_FILE" 2>/dev/null || true
  fi
  echo "DRC stopped"
  exit 0
fi

# ── validate config ──────────────────────────────────────────────────────────
conf_file="$base_dir/configs/$GEOMETRY/brutefir-${actual_rate}${variant}.conf"
if [ ! -f "$conf_file" ]; then
  echo "config not found: $conf_file"
  exit 1
fi

# ── free the audio devices before rebuilding the chain ───────────────────────
# brutefir opens /dev/dsp0 (the single-open DAC); MPD's direct output holds it
# while playing, and the DRC outputs hold /dev/dsp1.  Disable all MPD
# outputs now so brutefir is guaranteed a free DAC and virtual_oss a free
# loopback — then re-enable the right one once the chain is confirmed up.
# Disabling first also forces the later "enable only" to genuinely reopen the
# output instead of being a no-op on an already-enabled (but stale) output.
# stdout silenced: each "mpc disable" echoes the full output list, which is
# just noise here — the post-start "enable only" below prints the final state.
mpc disable "OKTO-DAC"   >/dev/null 2>&1 || true
mpc disable "DRC-native" >/dev/null 2>&1 || true
mpc disable "DRC-resamp" >/dev/null 2>&1 || true
# "mpc disable" returns before MPD's player thread has actually closed the
# device; give it a moment so MPD releases /dev/dsp1 (and the DAC) before
# we tear down virtual_oss underneath it.  Yanking the backend out from under
# an open MPD output is what produced "exception: Failed to open audio output"
# and forced a second run.
sleep 0.5

# ── restart virtual_oss at the required sample rate ──────────────────────────
if ! $IS_LINUX; then
  echo "stopping virtual_oss"
  stop_virtual_oss
  echo "starting virtual_oss at ${actual_rate} Hz"
  # shellcheck disable=SC2086
  _sudo virtual_oss -D "$VIRTUAL_OSS_PID" -r "$actual_rate" $VIRTUAL_OSS_ARGS &
  # Wait until virtual_oss is actually up and the loopback node exists;
  # brutefir's input opens /dev/dsp.loop and fails outright if it is not
  # ready yet.  Fall back after ~5 s rather than blocking forever.
  _vo=0
  while [ "$_vo" -lt 25 ]; do
    if pgrep -q virtual_oss 2>/dev/null && [ -e /dev/dsp.loop ]; then
      break
    fi
    sleep 0.2
    _vo=$((_vo + 1))
  done
fi

# ── prime the OKTO DAC on a rate change ──────────────────────────────────────
# The DAC routes silence on the first open at a new rate, so open brutefir once
# at the new rate, tear it back down, then fall through to the real start.  The
# real (final) open is then the "second" open at the same rate the DAC needs to
# actually output — automating what used to require running drc.sh twice.
if [ -n "$prime" ]; then
  echo "priming DAC at ${actual_rate} Hz (rate changed from ${prev_rate:-off})"
  if start_brutefir; then
    sleep 1
    stop_brutefir
    sleep 0.5
  fi
fi

# ── start brutefir (verified, with retry) ────────────────────────────────────
if ! start_brutefir; then
  echo "ERROR: brutefir failed to start after 3 attempts (see /tmp/brutefir.out)" >&2
  # Roll back to a defined state instead of leaving the chain half-built.  At
  # this point all mpc outputs are disabled and virtual_oss may be running; if
  # we just exit, the box is silent with virtual_oss orphaned (the inconsistent
  # "off + virtual_oss running" state).  Tear the chain down and re-enable the
  # direct DAC so there is always a working output.
  echo "rolling back to direct DAC output (off)" >&2
  if ! $IS_LINUX; then
    stop_virtual_oss
  fi
  mpc enable only "OKTO-DAC" 2>/dev/null || true
  # last_arg is left unchanged on purpose: it records the *desired* state, so the
  # next trigger (devd ATTACH / drc.sh restore) retries this config rather than
  # silently staying off after a transient failure.
  exit 1
fi

# ── enable the matching MPD output ───────────────────────────────────────────
if [ "$mode" = "resamp" ]; then
  mpd_output="DRC-resamp"
else
  mpd_output="DRC-native"
fi
# Enable ONLY the selected DRC output (disables the direct + the other DRC
# output). "mpc disable all" is not valid in mpc — use "enable only <name>".
mpc enable only "$mpd_output"

# ── record state ─────────────────────────────────────────────────────────────
state_args="${rate}${variant:+ ${variant}}"
echo "$state_args" > "$STATE_FILE"
chmod 644 "$STATE_FILE" 2>/dev/null || true
# DRC is now running — record the on state alongside the rate so `restore`
# brings it back (the off path above writes "off" here instead).
echo "on" > "$POWER_FILE"
chmod 644 "$POWER_FILE" 2>/dev/null || true

echo "DRC active: $(state_label "$state_args") (MPD output: ${mpd_output})"
