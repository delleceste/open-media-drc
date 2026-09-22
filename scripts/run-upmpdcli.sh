#!/bin/sh
# Open the plugin console log as the service user, after systemd has created
# RuntimeDirectory=.  Keeping this redirection out of StandardOutput= avoids
# systemd's pre-exec 209/STDOUT failure path entirely.
set -eu

if [ "$#" -lt 3 ]; then
	echo "usage: $0 LOG_FILE UPMPDCLI_BIN UPMPDCLI_ARGS..." >&2
	exit 64
fi

log_file=$1
shift
umask 022
exec "$@" >"$log_file" 2>&1
