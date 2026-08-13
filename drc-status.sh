#!/usr/bin/env bash
# Print the active DRC config label, or 'off'.
# Exits 1 and prints 'inconsistent' if multiple different configs are running.

# Config resolution — keep in sync with drc.sh: $OMDRC_CONF, else config.env
# beside the script (run-from-repo mode), else ${PREFIX}/etc/open-media-drc/
# omdrc.conf (installed mode).  Supplies GEOMETRY and OMDRC_STATE_DIR.
base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-/usr/local}"

OMDRC_REPO_MODE=false
if [ -n "${OMDRC_CONF:-}" ] && [ -f "$OMDRC_CONF" ]; then
    . "$OMDRC_CONF"
elif [ -f "$base_dir/config.env" ]; then
    . "$base_dir/config.env"
    OMDRC_REPO_MODE=true
elif [ -f "$PREFIX/etc/open-media-drc/omdrc.conf" ]; then
    . "$PREFIX/etc/open-media-drc/omdrc.conf"
fi
GEOMETRY="${GEOMETRY:-flat}"

# Where configs/<GEOMETRY>/ lives — needed to validate the runtime geometry
# override below.  Same resolution as drc.sh.
if $OMDRC_REPO_MODE; then
    SITE_DIR="${OMDRC_SITE_DIR:-$base_dir}"
else
    SITE_DIR="${OMDRC_SITE_DIR:-$PREFIX/etc/open-media-drc}"
fi

if [ -n "${OMDRC_STATE_DIR:-}" ]; then
    STATE_DIR="$OMDRC_STATE_DIR"
elif $OMDRC_REPO_MODE; then
    STATE_DIR="$base_dir"
elif [ "$(id -u)" -eq 0 ]; then
    STATE_DIR="/var/db/omdrc"
else
    STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/omdrc"
fi
STATE_FILE="$STATE_DIR/last_arg"

# Runtime filter-set override — keep in sync with drc.sh: the config file's
# GEOMETRY is the default, last_geometry (written by `drc.sh geometry <name>`
# and the web remote) is the current choice.  A stale name is ignored.
GEOMETRY_FILE="$STATE_DIR/last_geometry"
if [ -f "$GEOMETRY_FILE" ]; then
    _geo=$(cat "$GEOMETRY_FILE" 2>/dev/null || true)
    if [ -n "$_geo" ] && [ -d "$SITE_DIR/configs/$_geo" ]; then
        GEOMETRY="$_geo"
    fi
    unset _geo
fi

if [ "${1:-}" = "--geometry" ]; then
    echo "$GEOMETRY"
    exit 0
fi

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
        44100)  printf '44.1 kHz\n' ;;
        48000)  printf '48 kHz\n' ;;
        88200)  printf '88.2 kHz\n' ;;
        96000)  printf '96 kHz\n' ;;
        192000) printf '192 kHz\n' ;;
        *)      printf '%s Hz\n' "$1" ;;
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

state_config_key() {
    local state mode variant
    state=$(state_to_args "$1")
    set -- $state
    mode="${1:-}"
    variant="${2:-}"

    case "$mode" in
        ""|"off")
            return 1
            ;;
        resamp)
            printf '192000%s\n' "$variant"
            ;;
        *)
            printf '%s%s\n' "$mode" "$variant"
            ;;
    esac
}

running_config_to_state() {
    local config="$1"
    if [[ "$config" =~ ^([0-9]+)(.*)$ ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
        if [ -n "${BASH_REMATCH[2]}" ]; then
            printf ' %s' "${BASH_REMATCH[2]}"
        fi
        printf '\n'
    else
        printf '%s\n' "$config"
    fi
}

# ps -ax -o args= works on both Linux and FreeBSD; grep with a char class
# avoids matching the grep process itself.
configs=$(ps -ax -o args= 2>/dev/null \
    | grep -E '[b]rutefir.*\.conf' \
    | sed -n 's|.*configs/\([^/]*\)/brutefir-\([^. ]*\)\.conf.*|\2|p' \
    | sort -u)

if [ -z "$configs" ]; then
    echo "off"
    exit 0
fi

n=$(printf '%s\n' "$configs" | wc -l)
if [ "$n" -eq 1 ]; then
    state=""
    [ -f "$STATE_FILE" ] && state=$(state_to_args "$(cat "$STATE_FILE")")
    if [ -n "$state" ] && [ "$(state_config_key "$state" 2>/dev/null || true)" = "$configs" ]; then
        state_label "$state"
    else
        state_label "$(running_config_to_state "$configs")"
    fi
else
    echo "inconsistent"
    exit 1
fi
