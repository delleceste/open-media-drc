#!/usr/bin/env bash
set -euo pipefail

# ── configuration ─────────────────────────────────────────────────────────────
# Two ways to run, decided by where this script lives:
#  * run-from-repo: a config.env sits next to the script (repo checkout).
#    Site data (configs/, filters/) and state (last_arg, drc.log, …) live in
#    the checkout, exactly as before.
#  * installed (package/port): no config.env beside the script; settings come
#    from ${PREFIX}/etc/open-media-drc/omdrc.conf, site data defaults to
#    ${PREFIX}/etc/open-media-drc and state to /var/db/omdrc (root) or
#    ~/.local/state/omdrc.
# $OMDRC_CONF overrides the config file location in both modes.  The config
# file may set GEOMETRY, OMDRC_SITE_DIR, OMDRC_STATE_DIR (and the DAC_* knobs
# below, which stay env-overridable).
base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-/usr/local}"

OMDRC_REPO_MODE=false
OMDRC_CONF_FILE=""
if [ -n "${OMDRC_CONF:-}" ] && [ -f "$OMDRC_CONF" ]; then
  OMDRC_CONF_FILE="$OMDRC_CONF"
elif [ -f "$base_dir/config.env" ]; then
  OMDRC_CONF_FILE="$base_dir/config.env"
  OMDRC_REPO_MODE=true
elif [ -f "$PREFIX/etc/open-media-drc/omdrc.conf" ]; then
  OMDRC_CONF_FILE="$PREFIX/etc/open-media-drc/omdrc.conf"
fi
if [ -n "$OMDRC_CONF_FILE" ]; then
  # shellcheck disable=SC1090
  . "$OMDRC_CONF_FILE"
fi

# Speaker geometry / filter set.  The repo ships "flat" (dirac-pulse identity
# filters, no correction); set GEOMETRY in the config file to select a real
# filter set under $SITE_DIR/configs/<GEOMETRY>/.
GEOMETRY="${GEOMETRY:-flat}"

# Where configs/<GEOMETRY>/ and filters/<GEOMETRY>/ live.
if $OMDRC_REPO_MODE; then
  SITE_DIR="${OMDRC_SITE_DIR:-$base_dir}"
else
  SITE_DIR="${OMDRC_SITE_DIR:-$PREFIX/etc/open-media-drc}"
fi

# DAC warm-up before the MPD output is enabled.  The OKTO routes silence on a
# *cold* open until its clock relocks, and the host does not wait for that relock
# (see OKTO-DAC8-silent-first-open.md).  brutefir feeds the DAC zeros from the
# moment it starts, so we hold that silent stream open until the clock has locked
# before letting real audio flow — what used to require running drc.sh twice.
#
# Two distinct parts (measured on this box, see tests/dac-lock-latency notes):
#  * relock wait — ADAPTIVE: poll until the clock reaches the target rate, up to
#    DAC_WARMUP_SECS.  A rate change relocks in ~0.7–1.0 s; an unchanged rate is
#    already locked (the clock is retained across an off period) and returns
#    immediately, so no time is wasted when none is needed.  This is the cap.
#  * post-lock dwell — DAC_SETTLE_SECS of extra silence *after* lock.  The clock
#    being locked does not prove the OKTO is routing audio yet; this dwell guards
#    that device-side quirk and is the knob to tune by ear if silence recurs.
DAC_WARMUP_SECS=${DAC_WARMUP_SECS:-3}
DAC_SETTLE_SECS=${DAC_SETTLE_SECS:-1}

# Closed-loop verification (mitigation #3).  After warm-up, confirm the DAC is
# actually streaming at the requested rate before trusting the chain; if not,
# tear down and retry up to DAC_VERIFY_RETRIES extra times.  On FreeBSD the
# check reads the DAC's USB async feedback (dev.pcm.0.feedback_rate), which
# tracks the live stream rate (~96002 while playing 96 kHz, ~48001 idle); on
# Linux it reads the ALSA hw_params rate.  Note: this verifies the *clock locked
# at the right rate*, not that audio is audible — the OKTO can still route
# silence with a healthy host stream (see OKTO-DAC8-silent-first-open.md) — but
# it catches EBUSY, a dead chain, and a clock that never switched family.
DAC_VERIFY_RETRIES=2
DAC_VERIFY_TOL=100      # Hz tolerance around the requested rate

# Rate-change prime (mitigation, confirmed by listening tests 2026-06).  The OKTO
# cold-opens SILENT after its clock PLL relocks for a new rate, and only starts
# routing audio after SEVERAL open/close cycles.  First seen across crystal
# families (its 44.1k family 44.1/88.2/176.4/352.8 and 48k family 48/96/192/384
# use different master crystals: e.g. idle→44.1k, 44.1k→`drc.sh resamp`), but it
# also happens WITHIN a family (observed 48k→192k silent).  So the trigger is a
# rate *change* of any kind — NOT just a crystal switch, and NOT 44.1k-only.
# Confirmed empirically: 1 cold open at a changed rate = silent, ~3 opens =
# audible.  So on any rate change (see the prime block below, which reads the
# current rate from dev.pcm.0.feedback_rate) we "prime" with DAC_PRIME_CYCLES
# extra open/close bounces before the real open (total opens = cycles + 1); only
# a same-rate re-open is skipped.  This cannot be auto-verified — the silence is a
# DAC-side routing quirk and the USB feedback / UAC2 clock-valid both look healthy
# either way (see OKTO-DAC8-silent-first-open.md) — so it is a fixed recipe.
# Default is 0: the kernel-side fix (uaudio clock-before-alt reorder, see
# freebsd-uaudio-patch/uaudio-clock-before-alt.md) is installed and is meant to
# make this prime unnecessary. Still env-overridable in case the fix proves
# incomplete during testing (e.g. DAC_PRIME_CYCLES=2 drc.sh 44100).
DAC_PRIME_CYCLES=${DAC_PRIME_CYCLES:-0}

VIRTUAL_OSS_PID=/tmp/virtual_oss.pid
VIRTUAL_OSS_ARGS="-i 8 -C 2 -c 2 -b 32 -s 200ms -f /dev/null -a 0 -d dsp.play -L dsp.loop"

IS_LINUX=false
[ "$(uname)" = "Linux" ] && IS_LINUX=true

# ── paths / state ─────────────────────────────────────────────────────────────
# Repo mode keeps state beside the script (as always).  Installed mode must not
# write into packaged paths (pkg check -s flags modified files), so state goes
# to /var/db/omdrc when root, else ~/.local/state/omdrc.  Services and
# interactive runs that must share state should pin OMDRC_STATE_DIR in the
# config file.
if [ -n "${OMDRC_STATE_DIR:-}" ]; then
  STATE_DIR="$OMDRC_STATE_DIR"
elif $OMDRC_REPO_MODE; then
  STATE_DIR="$base_dir"
elif [ "$(id -u)" -eq 0 ]; then
  STATE_DIR="/var/db/omdrc"
else
  STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/omdrc"
fi
mkdir -p "$STATE_DIR" 2>/dev/null || true

STATE_FILE="$STATE_DIR/last_arg"
# Power state (on/off) kept separately from the rate: `off` records "off" here
# but leaves STATE_FILE (the remembered rate) intact, so `restore` stays off
# across a reboot yet can bring DRC back at the last rate when turned on.
POWER_FILE="$STATE_DIR/last_power"
# Runtime filter-set (geometry) override, written by `drc.sh geometry <name>`
# and by the web remote.  The config file's GEOMETRY is the *default*; this file
# is the *current choice*.  Kept in the state dir beside last_arg / last_power so
# it survives a reboot and is honoured by restore and by the devd/udev
# re-attach path, exactly like the remembered rate.  A stale name (filter set
# removed since it was chosen) is ignored, so the config default wins again
# rather than every run failing on a missing config.
GEOMETRY_FILE="$STATE_DIR/last_geometry"
if [ -f "$GEOMETRY_FILE" ]; then
  _geo=$(cat "$GEOMETRY_FILE" 2>/dev/null || true)
  if [ -n "$_geo" ] && [ -d "$SITE_DIR/configs/$_geo" ]; then
    GEOMETRY="$_geo"
  fi
  unset _geo
fi

# Persistent operations log (survives reboots — lives beside last_arg, NOT in
# /tmp).  Each run appends machine-parseable `key=value` lines so the cost and
# necessity of each step can be mined later: how often brutefir needs attempt
# 2/3, how often verification fails and a retry is needed, whether warm-up
# correlates with fewer failures.  The goal is to drop steps that prove useless.
# Mine it e.g. with:  grep 'event=run_result' "$STATE_DIR/drc.log"
LOG_FILE="$STATE_DIR/drc.log"
RUN_ID="$$-$(date +%s 2>/dev/null || echo 0)"

# Append one structured event line.  Best-effort: never let logging failure abort
# a run (the log dir is normally writable, but be safe under `set -e`).
log_event() {
  local ts
  ts=$(date +%Y-%m-%dT%H:%M:%S 2>/dev/null || echo '?')
  printf '%s run=%s %s\n' "$ts" "$RUN_ID" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

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

# Extract just the sample rate (in Hz) from a saved state, for rate-change
# detection.  resamp always runs the DAC at 192000; an off/empty state yields
# an empty string so the prime logic treats the next start as a rate change.
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
  profile="${variant#@}"
  profile="${profile:-Flat}"

  if [ "$mode" = "resamp" ]; then
    printf '%s auto-resample\n' "$profile"
  elif [[ "$mode" =~ ^[0-9]+$ ]]; then
    printf '%s %s\n' "$profile" "$(format_rate "$mode")"
  else
    printf '%s\n' "$state"
  fi
}

# Filter sets installed under $SITE_DIR/configs/ — one line per name.  A
# directory only counts as a filter set once it holds at least one brutefir
# config, so a leftover/empty directory is never offered as a choice.
list_geometries() {
  local dir name conf
  for dir in "$SITE_DIR"/configs/*; do
    [ -d "$dir" ] || continue
    name=${dir##*/}
    for conf in "$dir"/brutefir-*.conf; do
      [ -f "$conf" ] || continue
      printf '%s\n' "$name"
      break
    done
  done
}

# Sample rates a filter set can serve, ascending.  Variant configs
# (brutefir-192000<variant>.conf) collapse onto their base rate.
geometry_rates() {
  local conf r
  for conf in "$SITE_DIR/configs/$1"/brutefir-*.conf; do
    [ -f "$conf" ] || continue
    r=${conf##*/brutefir-}
    r=${r%.conf}
    r=${r%%[!0-9]*}
    [ -n "$r" ] && printf '%s\n' "$r"
  done | sort -un
}

# Runtime filter designs available for one geometry/rate. The empty config
# suffix is the geometry's default design; @name is an immutable audited design
# deployed by new_filter_design.py. Legacy <variant> suffixes remain valid.
geometry_designs() {
  local geo="$1" rate="$2" conf key suffix
  for conf in "$SITE_DIR/configs/$geo"/brutefir-"$rate"*.conf; do
    [ -f "$conf" ] || continue
    key=${conf##*/brutefir-}
    key=${key%.conf}
    suffix=${key#"$rate"}
    [ -n "$suffix" ] && printf '%s\n' "$suffix" || printf 'default\n'
  done | sort -u
}

valid_variant() {
  case "$1" in
    "") return 0 ;;
    +[A-Za-z0-9]*|@[A-Za-z0-9]*)
      [[ "$1" =~ ^[+@][A-Za-z0-9][A-Za-z0-9._+-]*$ ]]
      ;;
    *) return 1 ;;
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
  echo "Usage: $0 <rate>|resamp|restore|off|stop|status|session|geometry|design [variant]"
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
  echo "  session  : print the exact persistent state used by restore as key=value"
  echo "             lines (geometry, power, mode/rate, design and label)"
  echo "  geometry : print the active filter set; 'geometry --list' lists the"
  echo "             installed ones, 'geometry <name>' switches to one and"
  echo "             reloads brutefir with that set's config"
  echo "  design   : print/list/switch designs within the active geometry;"
  echo "             deployed designs use @name, default selects the base config"
  echo "  variant  : optional runtime selector, e.g. @flx8-2026-08"
  echo
  echo "  Geometry: $GEOMETRY  (config: ${OMDRC_CONF_FILE:-built-in defaults})"
  echo "  GEOMETRY in the config file is the default; 'geometry <name>' records"
  echo "  a runtime choice that overrides it (see $GEOMETRY_FILE)."
  echo "  'flat' (identity filters, no correction) is the shipped default."
  echo
  echo "Examples:"
  echo "  $0 192000"
  echo "  $0 resamp"
  echo "  $0 restore"
  echo "  $0 status"
  echo "  $0 session"
  echo "  $0 geometry --list"
  echo "  $0 geometry 120.blue"
  echo "  $0 design --list"
  echo "  $0 design @flx8-2026-08"
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
  restore_power="on"
  [ -f "$POWER_FILE" ] && restore_power=$(cat "$POWER_FILE" 2>/dev/null || echo on)
  restore_state=""
  [ -f "$STATE_FILE" ] && restore_state=$(cat "$STATE_FILE" 2>/dev/null || true)
  # Log what restore actually read, so a boot that ignores the saved state can
  # be told apart from a saved state that was never written in the first place.
  log_event "event=restore power=${restore_power:-unset} last_arg=${restore_state:-unset} state_dir=${STATE_DIR}"
  if [ "$restore_power" = "off" ]; then
    echo "Last power state was off — leaving DRC disabled (direct DAC)"
    exec "$0" off
  fi
  state="$restore_state"
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

# ── session: expose the exact persistent restore state, without mutation ───────
# This is deliberately a view over last_arg / last_power / last_geometry, not a
# second session store. The web remote consumes these stable key=value lines.
if [ $# -eq 1 ] && [ "$1" = "session" ]; then
  session_state=""
  [ -f "$STATE_FILE" ] && session_state=$(state_to_args "$(cat "$STATE_FILE")")
  case "$session_state" in ""|off) session_state="192000" ;; esac
  # shellcheck disable=SC2086
  set -- $session_state
  session_mode="${1:-192000}"
  session_design="${2:-default}"
  session_rate="$session_mode"
  [ "$session_rate" = "resamp" ] && session_rate=192000
  session_power="on"
  [ -f "$POWER_FILE" ] && session_power=$(cat "$POWER_FILE")
  case "$session_power" in on|off) ;; *) session_power="on" ;; esac
  printf 'geometry=%s\n' "$GEOMETRY"
  printf 'power=%s\n' "$session_power"
  printf 'mode=%s\n' "$session_mode"
  printf 'rate=%s\n' "$session_rate"
  printf 'design=%s\n' "$session_design"
  printf 'label=%s\n' "$(state_label "$session_state")"
  exit 0
fi

# ── design: show / list / switch within the active physical geometry ─────────
if [ $# -ge 1 ] && [ "$1" = "design" ]; then
  design_request="${2:-}"
  design_state=""
  [ -f "$STATE_FILE" ] && design_state=$(state_to_args "$(cat "$STATE_FILE")")
  case "$design_state" in ""|off) design_state="192000" ;; esac
  # shellcheck disable=SC2086
  set -- $design_state
  design_mode="$1"
  design_selector="${2:-}"
  design_rate="$design_mode"
  [ "$design_rate" = "resamp" ] && design_rate=192000

  case "$design_request" in
    "")
      [ -n "$design_selector" ] && echo "$design_selector" || echo "default"
      exit 0
      ;;
    --list|-l)
      geometry_designs "$GEOMETRY" "$design_rate"
      exit 0
      ;;
  esac

  wanted="$design_request"
  [ "$wanted" = "default" ] && wanted=""
  if ! valid_variant "$wanted"; then
    echo "invalid design selector: $design_request" >&2
    exit 1
  fi
  listed="${wanted:-default}"
  if ! geometry_designs "$GEOMETRY" "$design_rate" | grep -qxF -- "$listed"; then
    echo "design not available for $GEOMETRY at ${design_rate} Hz: $design_request" >&2
    echo "available: $(geometry_designs "$GEOMETRY" "$design_rate" | tr '\n' ' ')" >&2
    exit 1
  fi

  previous="${design_selector:-default}"
  if [ "$previous" = "$listed" ]; then
    echo "design already selected: $listed"
    exit 0
  fi
  log_event "event=design_switch_request geometry=${GEOMETRY} rate=${design_rate} from=${previous} to=${listed}"
  if [ -f "$POWER_FILE" ] && [ "$(cat "$POWER_FILE")" = "off" ]; then
    printf '%s%s\n' "$design_mode" "${wanted:+ $wanted}" > "$STATE_FILE"
    chmod 644 "$STATE_FILE" 2>/dev/null || true
    log_event "event=design_switch_saved geometry=${GEOMETRY} rate=${design_rate} from=${previous} to=${listed} power=off"
    echo "saved filter design: $previous -> $listed (DRC is off; restore respects off, so it applies on the next activation)"
    exit 0
  fi
  echo "switching filter design: $previous -> $listed in geometry $GEOMETRY at ${design_rate} Hz"
  export OMDRC_SWITCH_FROM="$previous" OMDRC_SWITCH_TO="$listed"
  exec "$0" "$design_mode" ${wanted:+"$wanted"}
fi

# ── geometry: show / list / switch the active filter set ─────────────────────
# brutefir reads its .conf (and loads the coefficients it names) once, at start,
# so switching filter sets is not a live operation: the chain has to come back
# up on the new set's config.  Rather than duplicate that teardown here, record
# the choice and re-exec the ordinary rate run, which already stops brutefir,
# rebuilds the chain and re-enables the right MPD output.  The rate itself does
# not change, so the DAC keeps its clock lock and no cold-open prime is needed.
if [ $# -ge 1 ] && [ "$1" = "geometry" ]; then
  case "${2:-}" in
    "")
      echo "$GEOMETRY"
      exit 0
      ;;
    --list|-l)
      list_geometries
      exit 0
      ;;
    -*)
      echo "unknown geometry option: $2" >&2
      exit 1
      ;;
  esac

  new_geo="$2"
  if ! list_geometries | grep -qxF -- "$new_geo"; then
    echo "unknown filter set: $new_geo" >&2
    echo "installed: $(list_geometries | tr '\n' ' ')" >&2
    exit 1
  fi

  echo "$new_geo" > "$GEOMETRY_FILE"
  chmod 644 "$GEOMETRY_FILE" 2>/dev/null || true
  GEOMETRY="$new_geo"
  log_event "event=geometry set=${new_geo}"

  # DRC off: record the choice only.  Turning DRC back on (or `restore`) picks
  # the new set up from GEOMETRY_FILE, so there is nothing to reload now.
  if [ -f "$POWER_FILE" ] && [ "$(cat "$POWER_FILE")" = "off" ]; then
    echo "filter set: $new_geo (DRC is off — it applies when DRC is turned on)"
    exit 0
  fi

  geo_state=""
  [ -f "$STATE_FILE" ] && geo_state=$(state_to_args "$(cat "$STATE_FILE")")
  case "$geo_state" in ""|off) geo_state="192000" ;; esac
  # shellcheck disable=SC2086
  set -- $geo_state
  want_rate="$1"
  want_variant="${2:-}"

  # Filter sets are per-rate and not every set covers every rate (a set measured
  # only at 192 kHz is normal), so the requested rate may simply not exist in the
  # new set.  Degrade instead of failing: drop the variant first, then fall back
  # to the set's highest rate — and say so, since the audible rate changes.
  need_rate="$want_rate"
  [ "$want_rate" = "resamp" ] && need_rate=192000
  if [ ! -f "$SITE_DIR/configs/$new_geo/brutefir-${need_rate}${want_variant}.conf" ]; then
    if [ -n "$want_variant" ] && \
       [ -f "$SITE_DIR/configs/$new_geo/brutefir-${need_rate}.conf" ]; then
      echo "filter set $new_geo has no ${want_variant} variant at ${need_rate} Hz — using the plain filter"
      want_variant=""
    else
      fallback_rate=$(geometry_rates "$new_geo" | tail -n 1)
      if [ -z "$fallback_rate" ]; then
        echo "filter set $new_geo has no usable brutefir config" >&2
        exit 1
      fi
      echo "filter set $new_geo has no ${need_rate} Hz config — switching to $(format_rate "$fallback_rate")"
      want_rate="$fallback_rate"
      want_variant=""
    fi
  fi

  echo "switching to filter set $new_geo"
  exec "$0" "$want_rate" ${want_variant:+"$want_variant"}
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
  if ! valid_variant "$variant"; then
    echo "invalid filter variant: $variant" >&2
    exit 1
  fi
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

# ── validate config ──────────────────────────────────────────────────────────
# Before anything is torn down: a bad argument (a typo, `--help`, or a rate the
# active filter set simply does not cover) must not cost the listener the chain
# that is currently playing.  Validated here, the run aborts with the box still
# making sound; validated after stop_brutefir — where this check used to live —
# it left brutefir dead, MPD still pointed at the DRC output, and silence.
if [ "$mode" != "off" ] && [ "$mode" != "stop" ]; then
  conf_file="$SITE_DIR/configs/$GEOMETRY/brutefir-${actual_rate}${variant}.conf"
  if [ ! -f "$conf_file" ]; then
    echo "config not found: $conf_file" >&2
    exit 1
  fi
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

# Set by start_brutefir to the number of attempts it took (for the log/stats).
BF_ATTEMPTS=0
start_brutefir() {
  local attempt i
  for attempt in 1 2 3; do
    BF_ATTEMPTS="$attempt"
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

# True when two sample rates are the same to within the few-Hz slop of the USB
# feedback reading (e.g. 192000 vs the observed 192004).  Used to tell a genuine
# rate change (needs a PLL relock → cold-open prime) from a same-rate re-open
# (lock retained → no prime).
rates_equal() {
  local d=$(( ${1:-0} - ${2:-0} )); [ "$d" -lt 0 ] && d=$(( -d ))
  [ "$d" -le 60 ]
}

# Best-effort read of the DAC's *currently programmed* rate, to tell whether a
# requested rate crosses crystal families.  FreeBSD: the USB async feedback
# (dev.pcm.0.feedback_rate) holds the live/last rate and persists while idle;
# Linux: the active ALSA hw_params rate.  Prints empty when unknown.
current_dac_rate() {
  if $IS_LINUX; then
    local f first
    for f in /proc/asound/card*/pcm*p/sub*/hw_params; do
      [ -f "$f" ] || continue
      read -r first < "$f" 2>/dev/null || continue
      [ "$first" = "closed" ] && continue
      awk '/^rate:/{print $2; exit}' "$f"; return
    done
  else
    sysctl -n dev.pcm.0.feedback_rate 2>/dev/null
  fi
}

# Prime the OKTO across a rate change: open brutefir at $conf_file, let it
# briefly hold /dev/dsp0, then tear it down — repeated $1 times.  Each bounce is
# one cold open at the target rate; the real open follows in the main loop, so
# the DAC sees (cycles + 1) opens, which is what gets the freshly-relocked clock
# to start routing audio.  Requires virtual_oss already up
# (brutefir's input).
prime_dac() {
  local n=$1 i w
  for i in $(seq 1 "$n"); do
    echo "priming DAC (rate change) cycle $i/$n at ${actual_rate} Hz"
    brutefir "$conf_file" -daemon 9>&- > /tmp/brutefir.out 2>&1 || true
    w=0; while [ "$w" -lt 15 ]; do bf_running && break; sleep 0.2; w=$((w + 1)); done
    sleep 0.8                       # hold the open so the 44.1k clock settles
    pkill -f "$bf_pattern" 2>/dev/null || true
    while bf_running; do sleep 0.1; done
    sleep 0.3                       # let the DAC release before the next open
  done
}

# Closed-loop check that the DAC is actually streaming at $actual_rate.
# Sets VERIFY_OBSERVED to the observed rate (or "" / "closed").  Returns 0 when
# the observed rate is within DAC_VERIFY_TOL of the target, 1 otherwise.
VERIFY_OBSERVED=""
verify_streaming() {
  VERIFY_OBSERVED=""
  local obs=""
  if $IS_LINUX; then
    # ALSA hw_params reports "closed" when the DAC stream is idle, else its rate.
    local f first
    for f in /proc/asound/card*/pcm*p/sub*/hw_params; do
      [ -f "$f" ] || continue
      read -r first < "$f" 2>/dev/null || continue
      [ "$first" = "closed" ] && continue
      obs=$(awk '/^rate:/{print $2; exit}' "$f")
      [ -n "$obs" ] && break
    done
    [ -z "$obs" ] && obs="closed"
  else
    # FreeBSD: the DAC's USB async feedback tracks the live stream rate.
    obs=$(sysctl -n dev.pcm.0.feedback_rate 2>/dev/null || echo "")
  fi
  VERIFY_OBSERVED="$obs"
  case "$obs" in
    ""|closed) return 1 ;;
  esac
  # |observed - target| <= tolerance ?
  local diff=$(( obs - actual_rate ))
  [ "$diff" -lt 0 ] && diff=$(( -diff ))
  [ "$diff" -le "$DAC_VERIFY_TOL" ]
}

# Hold brutefir's silent stream open until the DAC clock locks to the target
# rate, polling every 100 ms up to DAC_WARMUP_SECS.  Returns 0 once locked
# (clock at target), 1 if the cap elapsed without lock, 2 if brutefir died.
# Sets WARM_MS to the time spent (for the log/stats): an unchanged rate locks
# at ~0 ms, a real rate change at ~700–1000 ms.
WARM_MS=0
warm_until_locked() {
  local i=0 max=$(( DAC_WARMUP_SECS * 10 ))
  while :; do
    bf_running || { WARM_MS=$(( i * 100 )); return 2; }
    if verify_streaming; then WARM_MS=$(( i * 100 )); return 0; fi
    [ "$i" -ge "$max" ] && break
    sleep 0.1
    i=$(( i + 1 ))
  done
  WARM_MS=$(( i * 100 ))
  return 1
}

# Tear the half-built chain down and re-enable the direct DAC so there is always
# a working output (never leave the box silent with virtual_oss orphaned).
rollback_to_direct() {
  echo "rolling back to direct DAC output (off)" >&2
  if ! $IS_LINUX; then
    stop_virtual_oss
  fi
  mpc enable only "OKTO-DAC" 2>/dev/null || true
}

# ── stop brutefir ────────────────────────────────────────────────────────────
# Read the DAC's current crystal family *before* tearing the chain down, so the
# prime step below can tell whether the requested rate crosses crystal domains.
prev_rate="$(current_dac_rate)"
log_event "event=run_start mode=${mode} rate=${actual_rate:-} from=${prev_rate:-unknown} variant=${variant:-} warmup=${DAC_WARMUP_SECS} verify_retries=${DAC_VERIFY_RETRIES}"
stop_brutefir

# ── off / stop: re-enable direct DAC, stop virtual_oss ───────────────────────
if [ "$mode" = "off" ] || [ "$mode" = "stop" ]; then
  # Record the off INTENT first, before touching a single device.  `off` is a
  # user decision, and the teardown below talks to MPD, sudo and a cuse device
  # — any of which can fail or wedge.  Recording last after the teardown meant
  # one failed `mpc` (a wedged MPD is exactly what a virtual_oss teardown can
  # produce) aborted the run under `set -e` with the intent unrecorded, and the
  # next boot's `restore` happily brought DRC back up.  Same rule the rate path
  # already applies to last_arg: the file records what was *asked for*.
  # `stop` is a transient teardown (service shutdown / USB unplug) and leaves
  # last_power untouched, so a running system that simply reboots is restored
  # rather than left off.  STATE_FILE (the rate) is never touched here.
  if [ "$mode" = "off" ]; then
    echo "off" > "$POWER_FILE"
    chmod 644 "$POWER_FILE" 2>/dev/null || true
    log_event "event=power_saved mode=off power=off"
  fi
  # Release MPD from the DRC outputs BEFORE the backend under them disappears.
  # brutefir is already down at this point, but MPD may still hold /dev/dsp.play
  # open through DRC-native/DRC-resamp; pulling virtual_oss out from under an
  # open output is what wedges MPD (and then the "enable only" below hangs).
  # This mirrors what the chain-rebuild path does a few lines further down.
  mpc disable "DRC-native" >/dev/null 2>&1 || true
  mpc disable "DRC-resamp" >/dev/null 2>&1 || true
  sleep 0.5
  # Now tear the chain down so /dev/dsp0 is free before the direct output opens
  # it (the DAC is single-open: vchans off / bit-perfect).
  if ! $IS_LINUX; then
    echo "stopping virtual_oss"
    stop_virtual_oss
  fi
  # Enable ONLY the direct DAC output — this disables every other output.
  # NB: mpc has no "disable all" keyword (it errors "all: no such output");
  # "enable only <name>" is the correct idiom: it enables the named output
  # and disables all others atomically.  A failure here is reported, not fatal:
  # the DRC chain is already down and the state is already recorded, so aborting
  # would only hide the problem.
  if mpc enable only "OKTO-DAC"; then
    log_event "event=run_result mode=${mode} result=stopped output=OKTO-DAC"
  else
    log_event "event=run_result mode=${mode} result=stopped output=fail"
    echo "warning: could not switch MPD to the direct DAC output" >&2
  fi
  echo "DRC stopped"
  exit 0
fi

# ── free the audio devices before rebuilding the chain ───────────────────────
# brutefir opens /dev/dsp0 (the single-open DAC); MPD's direct output holds it
# while playing, and the DRC outputs hold /dev/dsp.play.  Disable all MPD
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
# device; give it a moment so MPD releases /dev/dsp.play (and the DAC) before
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

# ── prime the DAC on any rate CHANGE ─────────────────────────────────────────
# The OKTO cold-opens *silent* after the clock PLL relocks for a new rate, then
# starts routing only after several open/close cycles.  This was first seen
# across crystal families (44.1k<->48k, e.g. idle→44.1k, 44.1k→resamp) but it
# also happens WITHIN a family (observed: 48k→192k silent), so the trigger is a
# rate *change* of any kind, not just a crystal switch.  Safe rule: prime on any
# change; skip only a confirmed same-rate re-open (off→on at the same rate keeps
# its lock).  The current rate is read from dev.pcm.0.feedback_rate; when it is
# unknown (fresh boot / unreadable) we prime, since that is the coldest open.
do_prime=true
if [ -n "${prev_rate:-}" ] && [ "$prev_rate" != "0" ] && [ "$prev_rate" != "closed" ] \
   && rates_equal "$prev_rate" "$actual_rate"; then
  do_prime=false   # same rate as currently programmed → lock retained, no relock
fi
if $do_prime && [ "${DAC_PRIME_CYCLES:-0}" -gt 0 ]; then
  log_event "event=prime rate=${actual_rate} from=${prev_rate:-unknown} cycles=${DAC_PRIME_CYCLES}"
  prime_dac "$DAC_PRIME_CYCLES"
fi

# ── start brutefir, warm the DAC, and verify it streams (with retry) ─────────
# Flow per attempt: start brutefir → adaptive warm-up (hold the silent stream
# until the clock locks) → post-lock dwell.  A *verify* failure (clock never
# reached the target) is what the outer retry is for, up to DAC_VERIFY_RETRIES
# extra times.  A *hard start* failure is NOT outer-retried — start_brutefir
# already retries 3× internally for the "DAC slow to release" race, and testing
# showed outer-retrying a busy device just wastes ~30 s before the inevitable
# rollback.  brutefir feeds the DAC zeros from the moment it starts, so the
# warm-up replaces the old close/reopen "prime" (which cold-reopened the DAC and
# could need priming all over again).  See OKTO-DAC8-silent-first-open.md.
chain_ok=""
total_attempts=$(( DAC_VERIFY_RETRIES + 1 ))
vattempt=0
while [ "$vattempt" -lt "$total_attempts" ]; do
  vattempt=$(( vattempt + 1 ))

  if ! start_brutefir; then
    log_event "event=bf_start attempt=$vattempt bf_attempts=${BF_ATTEMPTS} result=fail"
    echo "brutefir failed to start (see /tmp/brutefir.out)" >&2
    break   # hard start failure: already retried internally — go to rollback
  fi
  log_event "event=bf_start attempt=$vattempt bf_attempts=${BF_ATTEMPTS} result=ok"

  echo "warming up DAC at ${actual_rate} Hz (silent stream, up to ${DAC_WARMUP_SECS}s)"
  # `warm_until_locked; warm_rc=$?` would be killed by `set -e` the moment the
  # function returns non-zero — i.e. on exactly the two outcomes the branches
  # below exist to handle (2 = brutefir died, 1 = clock never locked).  The run
  # then aborted silently: no verify event, no retry, no rollback, no state
  # written, and the box was left with brutefir up and every MPD output still
  # disabled (silence).  `|| warm_rc=$?` keeps the status without tripping -e.
  warm_rc=0
  warm_until_locked || warm_rc=$?
  if [ "$warm_rc" -eq 2 ]; then
    log_event "event=warmup attempt=$vattempt result=died warm_ms=${WARM_MS}"
    echo "brutefir exited during warm-up (attempt $vattempt/$total_attempts)" >&2
  elif [ "$warm_rc" -eq 0 ]; then
    log_event "event=verify attempt=$vattempt result=ok observed=${VERIFY_OBSERVED} want=${actual_rate} warm_ms=${WARM_MS}"
    echo "DAC clock locked at ${VERIFY_OBSERVED} Hz after ${WARM_MS}ms"
    # Post-lock dwell: locked clock != audio routed yet (device quirk).
    [ "${DAC_SETTLE_SECS:-0}" != "0" ] && sleep "$DAC_SETTLE_SECS"
    if bf_running; then
      chain_ok=1
      break
    fi
    log_event "event=settle attempt=$vattempt result=died"
    echo "brutefir exited during post-lock dwell (attempt $vattempt/$total_attempts)" >&2
  else
    log_event "event=verify attempt=$vattempt result=fail observed=${VERIFY_OBSERVED} want=${actual_rate} warm_ms=${WARM_MS}"
    echo "clock did not lock: DAC at '${VERIFY_OBSERVED}', wanted ${actual_rate} Hz (attempt $vattempt/$total_attempts)" >&2
  fi

  # Verify/warm-up failed — tear brutefir down and (if attempts remain) retry.
  stop_brutefir
  [ "$vattempt" -lt "$total_attempts" ] && sleep 1
done

if [ -z "$chain_ok" ]; then
  echo "ERROR: chain did not come up after ${total_attempts} attempts (see /tmp/brutefir.out)" >&2
  rollback_to_direct
  log_event "event=run_result mode=${mode} rate=${actual_rate} result=rolled_back attempts=${vattempt} bf_attempts=${BF_ATTEMPTS} switch_from=${OMDRC_SWITCH_FROM:-} switch_to=${OMDRC_SWITCH_TO:-}"
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

log_event "event=run_result mode=${mode} rate=${actual_rate} result=active attempts=${vattempt} bf_attempts=${BF_ATTEMPTS} warm_ms=${WARM_MS} observed=${VERIFY_OBSERVED} output=${mpd_output} switch_from=${OMDRC_SWITCH_FROM:-} switch_to=${OMDRC_SWITCH_TO:-}"
echo "DRC active: $(state_label "$state_args") (MPD output: ${mpd_output})"
