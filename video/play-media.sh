#!/bin/sh
# Play a local file or a network/stream URL through the media box's DRC-aware
# audio. No gcache here -- that's only needed for the raw optical disc; mpv reads
# files and URLs directly (local paths, http(s)/m3u8, and most streaming sites
# via yt-dlp). Remote control comes free via mpv-mpris (KDE Connect / playerctl /
# Plasma), and the DRC audio routing / video delay is shared with play-bluray.sh.
#
# Usage:
#   ./play-media.sh /path/to/movie.mkv
#   ./play-media.sh ~/Videos/*.mkv                 # several files = a playlist
#   ./play-media.sh "https://host/live/stream.m3u8"
#   ./play-media.sh "https://www.youtube.com/watch?v=..."   # needs yt-dlp

set -e
export PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin:$PATH

if [ "$#" -eq 0 ]; then
    echo "usage: $(basename "$0") <file-or-URL> [more files/URLs ...]" >&2
    exit 2
fi

# Resolve our real path (normally the symlink ~/play-media.sh -> the repo) to
# find the shared helper next to it.
SELF=$(readlink -f "$0"); HERE=$(dirname "$SELF")

# --- DRC-aware audio routing: sets AUDIO_DEVICE / AUDIO_DELAY / SUB_DELAY -----
. "$HERE/lib/drc-audio.sh"

# --- play (-- guards files/URLs that start with '-') -----------------------
exec mpv --fs \
    --ao=oss --audio-device="$AUDIO_DEVICE" \
    --audio-delay="$AUDIO_DELAY" \
    --sub-delay="$SUB_DELAY" \
    -- "$@"
