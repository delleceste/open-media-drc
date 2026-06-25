#!/usr/bin/env bash
# glitch-debug.sh — the global switch for DRC-chain glitch detection.
#
# One toggle drives the whole subsystem.  Turning it ON drops a state file
# (.glitch-debug.enabled) and launches the lightweight glitch-monitor.sh daemon;
# turning it OFF removes the state file (the monitor self-terminates) and kills
# the daemon.  The omdrc-ctrl web UI's Debug card calls this script.
#
# Subcommands:
#   on            enable monitoring (idempotent)
#   off           disable monitoring (idempotent)
#   status        human-readable state
#   enabled       exit 0 if ON, 1 if OFF  (for scripts/UI)
#   tail [N]      last N glitch.log lines (default 40)
#   analyze [..]  run glitch-analyze.py over glitch.log (passes extra args)
#   clear         truncate glitch.log
#   usbtap [SEC]  one bounded USB last-hop capture (default 180s; needs sudo)
#
set -u

base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="$base_dir/.glitch-debug.enabled"
LOG_FILE="$base_dir/glitch.log"
PIDFILE="/tmp/glitch-monitor.pid"
MONITOR="$base_dir/glitch-monitor.sh"
ANALYZER="$base_dir/glitch-analyze.py"
USBTAP="$base_dir/glitch-usbtap.sh"

export GLITCH_LOG="$LOG_FILE"
export GLITCH_STATE="$STATE_FILE"

monitor_pid() { [ -f "$PIDFILE" ] && cat "$PIDFILE" 2>/dev/null; }
monitor_running() {
  local p; p="$(monitor_pid)"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

start_monitor() {
  monitor_running && return 0
  nohup "$MONITOR" >/dev/null 2>&1 &
  echo $! > "$PIDFILE"
}

stop_monitor() {
  local p; p="$(monitor_pid)"
  [ -n "$p" ] && kill "$p" 2>/dev/null || true
  pkill -f "glitch-monitor.sh" 2>/dev/null || true
  rm -f "$PIDFILE"
}

case "${1:-status}" in
  on)
    : > "$STATE_FILE".tmp 2>/dev/null && mv "$STATE_FILE".tmp "$STATE_FILE" \
      || touch "$STATE_FILE"
    start_monitor
    echo "glitch debug: ON  (monitor pid $(monitor_pid))"
    ;;
  off)
    rm -f "$STATE_FILE"
    stop_monitor
    echo "glitch debug: OFF"
    ;;
  enabled)
    [ -f "$STATE_FILE" ] && exit 0 || exit 1
    ;;
  status)
    if [ -f "$STATE_FILE" ]; then state=ON; else state=OFF; fi
    if monitor_running; then mon="running (pid $(monitor_pid))"; else mon="stopped"; fi
    n=0; [ -f "$LOG_FILE" ] && n=$(grep -c 'stage=' "$LOG_FILE" 2>/dev/null || echo 0)
    echo "state:   $state"
    echo "monitor: $mon"
    echo "events:  $n"
    echo "log:     $LOG_FILE"
    ;;
  tail)
    n="${2:-40}"
    [ -f "$LOG_FILE" ] && tail -n "$n" "$LOG_FILE" || echo "(no log yet)"
    ;;
  analyze)
    shift
    python3 "$ANALYZER" "$LOG_FILE" "$@"
    ;;
  clear)
    : > "$LOG_FILE"
    echo "glitch.log cleared"
    ;;
  usbtap)
    "$USBTAP" "${2:-180}" "$LOG_FILE"
    ;;
  *)
    echo "usage: $0 {on|off|status|enabled|tail [N]|analyze [args]|clear|usbtap [sec]}" >&2
    exit 2
    ;;
esac
