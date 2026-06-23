#!/bin/sh
# Launch Firefox with the DRC chain bypassed so it plays straight to the DAC,
# then restore the previous DRC state on exit.  See lib.sh for the rationale.
#
# --no-remote forces a fresh foreground instance: without it a second Firefox
# would hand its URL to an already-running one and return immediately, defeating
# the "run in the foreground, restore DRC on exit" contract.  (Chrome/Chromium
# can't do --no-remote the same way, so those launchers guard on pgrep instead.)
SELF=$(readlink -f "$0"); HERE=$(dirname "$SELF")
. "$HERE/lib.sh"

drc_bypass_begin
firefox --no-remote "$@"
