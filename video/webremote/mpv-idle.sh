#!/bin/sh
# Start the persistent, idle mpv that the web remote drives over its JSON IPC
# socket. Autostarted by the KDE/Plasma session (SDDM autologin) so it owns the
# projector display; the web app only sends loadfile / set_property to the socket.
#
# Hidden while idle: with --idle=yes and NO --force-window, mpv shows no window
# until a file is loaded, and tears it down again when playback ends and it
# returns to idle. (keep-open stays at its default 'no' so end-of-file -> idle ->
# window hides, instead of freezing on the last frame.) See ../ARCHITECTURE.md.
#
# Audio routing is DRC-correct and set ONCE here, because video always runs in
# resamp mode: virtual_oss/brutefir resample to 192 kHz (correct speed + room
# correction) and the video is delayed to match the audio-path latency.

export PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin:$PATH

SELF=$(readlink -f "$0"); HERE=$(dirname "$SELF")
SOCKET="${MPV_SOCKET:-/tmp/mpv-socket}"

# Reuse the shared DRC-aware audio selection (sets AUDIO_DEVICE/DELAY/SUB_DELAY,
# and switches the DRC chain into resamp mode if needed). It lives in ../lib.
DRC_LIB="$HERE/../lib/drc-audio.sh"
if [ -r "$DRC_LIB" ]; then
    # drc-audio.sh expects HERE = the video/ dir (it sources ../drc.sh from there).
    HERE="$HERE/.." . "$DRC_LIB"
else
    AUDIO_DEVICE="oss//dev/dsp.play"; AUDIO_DELAY="-0.67"; SUB_DELAY="0"
fi

# Don't start a second one.
if [ -S "$SOCKET" ] && mpv_running=$(pgrep -f -- "--input-ipc-server=$SOCKET") && [ -n "$mpv_running" ]; then
    echo "idle mpv already running (pid $mpv_running)"
    exit 0
fi

exec mpv --idle=yes --fs \
    --input-ipc-server="$SOCKET" \
    --ao=oss --audio-device="$AUDIO_DEVICE" \
    --audio-channels=stereo \
    --audio-delay="$AUDIO_DELAY" \
    --sub-delay="$SUB_DELAY"
