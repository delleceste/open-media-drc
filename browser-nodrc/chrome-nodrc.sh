#!/bin/sh
# Launch Google Chrome with the DRC chain bypassed so it plays straight to the
# DAC, then restore the previous DRC state on exit.  See lib.sh for the rationale.
#
# BROWSER_AUDIO=alsa because the Chromium family has no OSS backend at all (its
# FreeBSD backends are PulseAudio, SNDIO and ALSA).  lib.sh answers that with a
# per-run alsa-lib config pinned to the DAC at its current rate, and hands back
# the flags to use it in $BROWSER_ALSA_FLAGS (empty on a host that needs none).
SELF=$(readlink -f "$0"); HERE=$(dirname "$SELF")
. "$HERE/lib.sh"

BROWSER=$(browser_resolve google-chrome google-chrome-stable) || {
	echo "browser-nodrc: no Google Chrome found on PATH — nothing to launch." >&2
	exit 127
}
BROWSER_AUDIO=sndio

# A second Chrome invocation just hands its URL to the running instance and
# returns at once, which would toggle DRC off and back on under a browser that
# keeps playing.  If one is already up, leave DRC alone and only open the URL.
if pgrep -xi "$BROWSER" >/dev/null 2>&1; then
	echo "browser-nodrc: $BROWSER already running — DRC left unchanged." >&2
	exec "$BROWSER" "$@"
fi

drc_bypass_begin
# shellcheck disable=SC2086  # the flags are intentionally word-split
"$BROWSER" $BROWSER_ALSA_FLAGS "$@"
