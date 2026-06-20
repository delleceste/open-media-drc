#!/bin/sh
# Play the Blu-ray in the USB drive (/dev/cd0) on FreeBSD.
#
# Why this script exists:
#   FreeBSD's raw optical device /dev/cd0 has no kernel read-ahead, so
#   libbluray's small (2 KB) reads only sustain ~3.6 MB/s -- too slow for
#   Blu-ray, which is what makes mpv stall/re-buffer. We front the drive with
#   a GEOM read-ahead cache (gcache, 1 MB blocks) that turns those tiny reads
#   into 1 MB device reads, raising the effective rate to ~9 MB/s. mpv's own
#   config (~/.config/mpv/mpv.conf) adds a large RAM buffer on top.
#   (On Linux this is unnecessary: /dev/sr0 is a block device with read-ahead.)
#
# Audio routing (DRC-aware) is handled by lib/drc-audio.sh; remote control
# (KDE Connect / playerctl / Plasma) comes free via mpv-mpris on D-Bus/MPRIS.
#
# Usage:
#   ./play-bluray.sh              # auto-select & play the longest title
#   ./play-bluray.sh bd://mpls/31 # play a specific playlist/title

set -e

# Robust PATH so this works from a terminal, a .desktop launcher, or a keybind.
export PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin:$PATH

DEV=cd0
CACHE=bd

# Resolve our real path (this file is normally the symlink ~/play-bluray.sh ->
# the repo) so we can source the shared helper that sits next to it.
SELF=$(readlink -f "$0"); HERE=$(dirname "$SELF")

# --- DRC-aware audio routing: sets AUDIO_DEVICE / AUDIO_DELAY / SUB_DELAY -----
. "$HERE/lib/drc-audio.sh"

# --- read-ahead cache in front of the slow optical drive -------------------
sudo kldload -n geom_cache
sudo gcache destroy "$CACHE" 2>/dev/null || true
sudo gcache create -b 1048576 -s 268435456 "$CACHE" "$DEV"
cleanup() { sudo gcache destroy "$CACHE" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# --- choose what to play ---------------------------------------------------
# An explicit argument (e.g. "bd://mpls/20", "bd://1") always wins. Otherwise
# auto-select the LONGEST title by duration -- the main feature on virtually
# every disc. We can't rely on mpv's bd:// or bd://longest: both just play the
# disc's *default* playlist, which is often a short intro or the wrong title.
# libbluray's bd_list_titles reports each title's duration and .mpls id; we pick
# the longest and select it precisely with bd://mpls/<n>.
TARGET="${1:-}"
if [ -z "$TARGET" ]; then
    mpls=$(bd_list_titles /dev/cache/"$CACHE" 2>/dev/null | awk '
      /duration:/ {
        dur=0; pl="";
        for (i=1;i<=NF;i++) {
          if ($i=="duration:") { split($(i+1),t,":"); dur=t[1]*3600+t[2]*60+t[3] }
          if ($i ~ /\.mpls/)   { p=$i; gsub(/[()]/,"",p); sub(/\.mpls.*/,"",p); pl=p }
        }
        if (dur>maxd) { maxd=dur; best=pl }
      }
      END { if (best!="") print best+0 }')   # +0 strips leading zeros (00020 -> 20)
    if [ -n "$mpls" ]; then
        TARGET="bd://mpls/$mpls"
        echo "auto-selected longest title -> $TARGET"
    else
        TARGET="bd://"                        # fallback if the probe failed
        echo "title probe failed; falling back to $TARGET"
    fi
fi

# --- play ------------------------------------------------------------------
mpv --fs \
    --bluray-device=/dev/cache/"$CACHE" \
    --ao=oss --audio-device="$AUDIO_DEVICE" \
    --audio-delay="$AUDIO_DELAY" \
    --sub-delay="$SUB_DELAY" \
    "$TARGET"
