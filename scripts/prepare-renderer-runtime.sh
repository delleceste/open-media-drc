#!/bin/sh
# Converge writable renderer/player state on the AUDIO_USER selected by
# open-media-drc.  Run as root during the system install, before services are
# started.  Existing data is retained; only ownership and top-level directory
# modes are normalized.
set -eu

usage()
{
	echo "usage: $0 AUDIO_USER AUDIO_HOME QCONNECT_STATE_DIR [RUNTIME_DIR]" >&2
	exit 64
}

[ "$#" -ge 3 ] && [ "$#" -le 4 ] || usage

audio_user=$1
audio_home=$2
qconnect_state_dir=$3
runtime_dir=${4:-/tmp}

case "$audio_home" in
	/*) ;;
	*) echo "$0: AUDIO_HOME must be an absolute path: $audio_home" >&2; exit 64 ;;
esac
case "$qconnect_state_dir" in
	/*) ;;
	*) echo "$0: QCONNECT_STATE_DIR must be an absolute path: $qconnect_state_dir" >&2; exit 64 ;;
esac
case "$runtime_dir" in
	/*) ;;
	*) echo "$0: RUNTIME_DIR must be an absolute path: $runtime_dir" >&2; exit 64 ;;
esac

# Refuse paths broad enough to turn the recursive ownership repair into a
# system-wide chown if a host configuration is malformed.
for path in "$audio_home" "$qconnect_state_dir"; do
	case "$path" in
		/|/home|/usr|/usr/local|/var|/var/db)
			echo "$0: refusing unsafe state root: $path" >&2
			exit 64
			;;
	esac
done

ID=$(command -v id)
INSTALL=$(command -v install)
CHOWN=$(command -v chown)
CHMOD=$(command -v chmod)

if ! "$ID" "$audio_user" >/dev/null 2>&1; then
	echo "$0: AUDIO_USER does not exist: $audio_user" >&2
	exit 67
fi
audio_group=$("$ID" -gn "$audio_user")

prepare_tree()
{
	path=$1
	mode=$2
	"$INSTALL" -d -o "$audio_user" -g "$audio_group" -m "$mode" "$path"
	"$CHOWN" -R "$audio_user:$audio_group" "$path"
	"$CHMOD" "$mode" "$path"
}

# musicpd writes its database, state, pid and log here.  upmpdcli writes its
# pid, streaming-service credentials and cache below its cache directory.
prepare_tree "$audio_home/.local/share/mpd" 0755
prepare_tree "$audio_home/.cache/mpd" 0755
prepare_tree "$audio_home/.cache/upmpdcli" 0755

# qobuzconnect2mpd's FreeBSD package initially owns this as its dedicated
# service account.  open-media-drc intentionally retargets the rc service to
# AUDIO_USER, so migrate the token/cache tree as part of every install.
prepare_tree "$qconnect_state_dir" 0700

# /tmp normally starts empty and each daemon creates its own files.  Repair
# files left by an earlier service identity, but do not create empty logs and
# do not follow an unexpected symlink from a world-writable directory.
for name in upmpdcli.log upmpdcli-console.log \
	qconnect2mpd.log qconnect2mpd-status.txt; do
	path="$runtime_dir/$name"
	if [ -L "$path" ]; then
		echo "$0: refusing to chown runtime symlink: $path" >&2
		exit 73
	fi
	if [ -e "$path" ]; then
		"$CHOWN" "$audio_user:$audio_group" "$path"
	fi
done

echo "renderer runtime: writable state belongs to $audio_user:$audio_group"
