#!/bin/sh
# Update MPD's index, then calculate DR14 reports in the background for
# folders containing audio that do not already have the canonical dr14.txt.
#
# OMDRC_MUSIC_DIRECTORY  scan this tree instead of MPD's music_directory
# OMDRC_SCAN_STATUS      (its directory also gets local-scan.log) file that gets `running <t> <folders done> <folders total>`, then `done <t> <n> <failed>` (n =
#                        reports written), for the kiosk's Local database page
set -eu

# The scan's log: one line per folder, next to the status file, for the Local page.
log=
[ -z "${OMDRC_SCAN_STATUS:-}" ] || log=$(dirname "$OMDRC_SCAN_STATUS")/local-scan.log
logline() { [ -z "$log" ] || printf '%s\n' "$*" >>"$log" 2>/dev/null || :; }
status() { [ -z "${OMDRC_SCAN_STATUS:-}" ] || echo "$*" >"$OMDRC_SCAN_STATUS" 2>/dev/null || :; }
fail() { logline "$*"; echo "$*" >&2; exit 1; }

if [ "${1:-}" = "--calculate" ]; then
	root=$2
	started=$(date +%s)
	# Running from the start: the walk below takes minutes on a big library on
	# a slow disk, and until then the Local page would still show the last scan.
	status "running $started"
	# The project's own meter (drmeter.py, the TT Dynamic Range algorithm) sits
	# in the application directory: next to this script's prefix, or installed.
	here=$(cd "$(dirname "$0")" && pwd)
	drmeter=
	for d in "$here/../../../lib/omdrcctrl" "$here/../omdrc-ctrl/src" /usr/local/lib/omdrcctrl; do
		[ -f "$d/drmeter.py" ] && { drmeter=$d/drmeter.py; break; }
	done
	[ -n "$drmeter" ] || { logline "drmeter.py not found: nothing measured"; status "done $(date +%s) 0"; exit 0; }

	# The folders with audio but no report, so progress can be shown as n of
	# total; one walk, with a running count in the log while it goes.
	# -L: a disk linked into the library (USBHD2 -> /media/...) is part of
	# it, as MPD sees it; find reports a link loop instead of following it.
	logline "Looking for album folders without a dr14.txt…"
	list=$(mktemp "${TMPDIR:-/tmp}/omdrc-dr14.XXXXXX") || exit 1
	trap 'rm -f "$list"' EXIT
	find -L "$root" -type f \( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.ogg' \
		-o -iname '*.opus' -o -iname '*.wav' -o -iname '*.m4a' -o -iname '*.ape' \
		-o -iname '*.wv' -o -iname '*.aiff' -o -iname '*.aif' \) -print |
	# each folder once, passed on at once (awk would hold a pipe's output back)
	awk '{ sub("/[^/]*$", "") } !seen[$0]++ { print; fflush() }' | {
		folders=0
		while IFS= read -r dir; do
			folders=$((folders + 1))
			[ -f "$dir/dr14.txt" ] || printf '%s\n' "$dir" >>"$list"
			[ $((folders % 100)) -ne 0 ] ||
				logline "[listing] $folders folders with audio so far, $(wc -l <"$list" | tr -d ' ') without a report"
		done
		logline "[listing] $folders folders with audio, $(wc -l <"$list" | tr -d ' ') without a report"
	}
	total=$(wc -l <"$list" | tr -d ' ')
	n=0
	failed=0
	status "running $started 0 $total"
	# A result line is `[n/total] DR<x>  <folder><TAB><artist><TAB><album>`; a
	# failure `[n/total] FAILED  <folder><TAB><reason>`.  The page shows each
	# folder's last line only, with the names shortened.
	while IFS= read -r dir; do
		n=$((n + 1))
		name=${dir#"$root"/}
		logline "[$n/$total] measuring $name"
		if out=$(nice -n 19 python3 "$drmeter" --album "$dir" 2>&1); then
			# stdout ends with `DR<x><TAB><artist><TAB><album>`; stderr lines are tracks that were skipped
			last=$(printf '%s\n' "$out" | tail -n 1)
			logline "[$n/$total] ${last%%	*}  $name	$(printf '%s' "$last" | cut -s -f 2-)"
		else
			failed=$((failed + 1))
			logline "[$n/$total] FAILED  $name	$(printf '%s' "$out" | tail -n 1)"
		fi
		printf '%s\n' "$out" | sed -n '/^skipped /p' | while IFS= read -r l; do logline "    $l"; done
		status "running $started $n $total"
	done <"$list"
	logline "finished: $((n - failed)) of $n folders measured, $failed failed"
	status "done $(date +%s) $((n - failed)) $failed"
	exit 0
fi

conf=
for candidate in /usr/local/etc/open-media-drc/musicpd.conf \
	/usr/local/etc/musicpd.conf /usr/local/etc/mpd.conf /etc/mpd.conf \
	"${HOME:-}/.config/mpd/mpd.conf" "${HOME:-}/.mpdconf"; do
	if [ -f "$candidate" ]; then conf=$candidate; break; fi
done
[ -n "$conf" ] || [ -n "${OMDRC_MUSIC_DIRECTORY:-}" ] ||
	fail 'MPD configuration not found'
# A new log for this rescan, with its first step in it at once.
[ -z "$log" ] || : >"$log" 2>/dev/null || :
logline "Rescan started $(date '+%Y-%m-%d %H:%M')"
root=${OMDRC_MUSIC_DIRECTORY:-}
[ -n "$root" ] || root=$(sed -n 's/^[[:space:]]*music_directory[[:space:]]*"\([^"]*\)".*/\1/p; s/^[[:space:]]*music_directory[[:space:]]\{1\}\([^"#][^#]*\).*/\1/p' "$conf" | head -n 1)
[ -n "$root" ] || fail 'music_directory is not configured'
case "$root" in
	'~') root=${HOME:-}/ ;;
	'~/'*) root=${HOME:-}/${root#~/} ;;
esac
[ -d "$root" ] || fail "music_directory does not exist: $root"

logline "Updating MPD's index…"
port=
[ -z "$conf" ] || port=$(sed -n 's/^[[:space:]]*port[[:space:]]*"\([0-9][0-9]*\)".*/\1/p' "$conf" | head -n 1)
if [ -n "$port" ]; then mpc -p "$port" update >/dev/null
else mpc update >/dev/null
fi
# Marked running before this returns, so the page's next look already sees it
# (and a second Rescan does not start another scan) however late the child starts.
status "running $(date +%s)"
logline "MPD is updating its index in the background; starting the DR14 scan"
if command -v nohup >/dev/null 2>&1; then
	nohup "$0" --calculate "$root" >/dev/null 2>&1 </dev/null &
else
	"$0" --calculate "$root" >/dev/null 2>&1 </dev/null &
fi
echo 'MPD database updated; DR14 scan started'
