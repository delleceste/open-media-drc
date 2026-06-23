#!/bin/sh
# Launch Google Chrome with the DRC chain bypassed so it plays straight to the
# DAC, then restore the previous DRC state on exit.  See lib.sh for the rationale.
SELF=$(readlink -f "$0"); HERE=$(dirname "$SELF")
. "$HERE/lib.sh"

BROWSER=google-chrome

# A second Chrome invocation just hands its URL to the running instance and
# returns at once, which would toggle DRC off and back on under a browser that
# keeps playing.  If one is already up, leave DRC alone and only open the URL.
if pgrep -xi "$BROWSER" >/dev/null 2>&1; then
	echo "browser-nodrc: $BROWSER already running — DRC left unchanged." >&2
	exec "$BROWSER" "$@"
fi

drc_bypass_begin
"$BROWSER" "$@"
