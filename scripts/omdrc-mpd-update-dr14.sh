#!/bin/sh
# Update MPD's index, then calculate DR14 reports in the background for
# folders containing audio that do not already have the canonical dr14.txt.
#
# OMDRC_MUSIC_DIRECTORY  scan this tree instead of MPD's music_directory
# OMDRC_SCAN_STATUS      file that gets `running <t>`, then `done <t> <n>` (n =
#                        reports calculated), for the kiosk's Local database page
set -eu

status() { [ -z "${OMDRC_SCAN_STATUS:-}" ] || echo "$*" >"$OMDRC_SCAN_STATUS" 2>/dev/null || :; }

if [ "${1:-}" = "--calculate" ]; then
	root=$2
	status "running $(date +%s)"
	# dr14_tmeter when installed, else the project's own meter (drmeter.py,
	# the same algorithm), which sits in the application directory.
	meter=
	if command -v dr14_tmeter >/dev/null 2>&1; then meter=dr14
	else
		here=$(cd "$(dirname "$0")" && pwd)
		for d in "$here/../../../lib/omdrcctrl" "$here/../omdrc-ctrl/src" /usr/local/lib/omdrcctrl; do
			[ -f "$d/drmeter.py" ] && { drmeter=$d/drmeter.py; meter=own; break; }
		done
	fi
	[ -n "$meter" ] || { status "done $(date +%s) 0"; exit 0; }
	reports() { find "$root" -type f -name dr14.txt | wc -l; }
	before=$(reports)

	find "$root" -type f \( -iname '*.flac' -o -iname '*.mp3' -o -iname '*.ogg' \
		-o -iname '*.opus' -o -iname '*.wav' -o -iname '*.m4a' -o -iname '*.ape' \
		-o -iname '*.wv' -o -iname '*.aiff' -o -iname '*.aif' \) -print |
	while IFS= read -r file; do dirname "$file"; done | sort -u |
	while IFS= read -r dir; do
		[ -f "$dir/dr14.txt" ] && continue
		if [ "$meter" = dr14 ]; then (cd "$dir" && dr14_tmeter ./ >/dev/null 2>&1) || :
		else nice -n 19 python3 "$drmeter" --album "$dir" >/dev/null 2>&1 || :
		fi
	done
	status "done $(date +%s) $(($(reports) - before))"
	exit 0
fi

conf=
for candidate in /usr/local/etc/open-media-drc/musicpd.conf \
	/usr/local/etc/musicpd.conf /usr/local/etc/mpd.conf /etc/mpd.conf \
	"${HOME:-}/.config/mpd/mpd.conf" "${HOME:-}/.mpdconf"; do
	if [ -f "$candidate" ]; then conf=$candidate; break; fi
done
[ -n "$conf" ] || [ -n "${OMDRC_MUSIC_DIRECTORY:-}" ] ||
	{ echo 'MPD configuration not found' >&2; exit 1; }
root=${OMDRC_MUSIC_DIRECTORY:-}
[ -n "$root" ] || root=$(sed -n 's/^[[:space:]]*music_directory[[:space:]]*"\([^"]*\)".*/\1/p; s/^[[:space:]]*music_directory[[:space:]]\{1\}\([^"#][^#]*\).*/\1/p' "$conf" | head -n 1)
[ -n "$root" ] || { echo 'music_directory is not configured' >&2; exit 1; }
case "$root" in
	'~') root=${HOME:-}/ ;;
	'~/'*) root=${HOME:-}/${root#~/} ;;
esac
[ -d "$root" ] || { echo "music_directory does not exist: $root" >&2; exit 1; }

port=
[ -z "$conf" ] || port=$(sed -n 's/^[[:space:]]*port[[:space:]]*"\([0-9][0-9]*\)".*/\1/p' "$conf" | head -n 1)
if [ -n "$port" ]; then mpc -p "$port" update >/dev/null
else mpc update >/dev/null
fi
if command -v nohup >/dev/null 2>&1; then
	nohup "$0" --calculate "$root" >/dev/null 2>&1 </dev/null &
else
	"$0" --calculate "$root" >/dev/null 2>&1 </dev/null &
fi
echo 'MPD database updated; DR14 scan started'
