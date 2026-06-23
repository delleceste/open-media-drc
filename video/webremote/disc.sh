#!/bin/sh
# gcache (read-ahead cache) up/down for the physical USB Blu-ray drive, for the
# web remote. The slow raw /dev/cd0 needs a 1 MB-block GEOM cache in front of it
# or Blu-ray playback stalls (see ../README.md and ../play-bluray.sh, which uses
# the same lifecycle). play-bluray.sh ALSO launches its own mpv; here we ONLY
# manage the cache, because the web remote loads the disc into the persistent
# idle mpv over IPC. See webremote/ARCHITECTURE.md.
#
# Needs passwordless sudo for kldload/gcache (same requirement as play-bluray.sh).
#
#   disc.sh up    -> create the cache, print its device path (/dev/cache/<cache>)
#   disc.sh down  -> destroy the cache (frees /dev/cd0 so the disc can eject)
set -e
export PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin:$PATH

DEV="${DISC_DEV:-cd0}"
CACHE="${DISC_CACHE:-bd}"
BS="${DISC_BLOCK:-1048576}"      # 1 MB blocks (capped by kern.maxphys)
SZ="${DISC_SIZE:-268435456}"     # 256 MB cache

case "${1:-}" in
    up)
        sudo kldload -n geom_cache
        sudo gcache destroy "$CACHE" 2>/dev/null || true
        sudo gcache create -b "$BS" -s "$SZ" "$CACHE" "$DEV"
        echo "/dev/cache/$CACHE"
        ;;
    down)
        sudo gcache destroy "$CACHE" 2>/dev/null || true
        ;;
    *)
        echo "usage: $0 up|down" >&2
        exit 2
        ;;
esac
