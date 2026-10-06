#!/bin/sh
# gcache (read-ahead cache) up/down for the physical USB Blu-ray drive, for the
# web remote. FreeBSD's raw /dev/cd0 needs a 1 MB-block GEOM cache in front of
# it or Blu-ray playback stalls (see ../README.md and ../play-bluray.sh, which
# uses the same lifecycle). On Linux /dev/sr0 already read-aheads as an
# ordinary block device, so "up" there is just printing its path -- no sudo, no
# cache to tear down. play-bluray.sh ALSO launches its own mpv; here we ONLY
# manage the cache, because the web remote loads the disc into the persistent
# idle mpv over IPC. See webremote/ARCHITECTURE.md.
#
# Needs passwordless sudo for kldload/gcache on FreeBSD (same requirement as
# play-bluray.sh); none on Linux.
#
#   disc.sh up    -> ready the drive, print its device path
#                    (FreeBSD: creates the cache, prints /dev/cache/<cache>;
#                     Linux: prints /dev/<dev> directly)
#   disc.sh down  -> release it (FreeBSD: destroys the cache so /dev/cd0 can
#                    eject; Linux: a no-op)
set -e
export PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin:$PATH

IS_LINUX=false
[ "$(uname)" = "Linux" ] && IS_LINUX=true

if $IS_LINUX; then
    DEV="${DISC_DEV:-sr0}"
else
    DEV="${DISC_DEV:-cd0}"
fi
CACHE="${DISC_CACHE:-bd}"
BS="${DISC_BLOCK:-1048576}"      # 1 MB blocks (capped by kern.maxphys)
SZ="${DISC_SIZE:-268435456}"     # 256 MB cache

case "${1:-}" in
    up)
        if $IS_LINUX; then
            [ -e "/dev/$DEV" ] || { echo "no disc in /dev/$DEV" >&2; exit 1; }
            echo "/dev/$DEV"
        else
            sudo kldload -n geom_cache
            sudo gcache destroy "$CACHE" 2>/dev/null || true
            sudo gcache create -b "$BS" -s "$SZ" "$CACHE" "$DEV"
            echo "/dev/cache/$CACHE"
        fi
        ;;
    down)
        $IS_LINUX || sudo gcache destroy "$CACHE" 2>/dev/null || true
        ;;
    *)
        echo "usage: $0 up|down" >&2
        exit 2
        ;;
esac
