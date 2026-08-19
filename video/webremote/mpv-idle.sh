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
# resamp mode: virtual_oss (FreeBSD) / brutefir+snd-aloop (Linux) resample to
# 192 kHz (correct speed + room correction) and the video is delayed to match the
# audio-path latency.

export PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin:$PATH

SELF=$(readlink -f "$0"); HERE=$(dirname "$SELF")
SOCKET="${MPV_SOCKET:-/tmp/mpv-socket}"

# mpv audio output API per OS: this box is ALSA-only (no Pulse/PipeWire/OSS);
# FreeBSD uses OSS.  The --audio-device value itself comes from drc-audio.sh.
if [ "$(uname)" = "Linux" ]; then
    AO="alsa"; FALLBACK_DEVICE="alsa/hw:1,0"
else
    AO="oss"; FALLBACK_DEVICE="oss//dev/dsp.play"
fi

# Reuse the shared DRC-aware audio selection (sets AUDIO_DEVICE/DELAY/SUB_DELAY,
# and switches the DRC chain into resamp mode if needed). It lives in ../lib.
# drc-audio.sh sits next to this script when installed, or in ../lib run-from-repo.
DRC_LIB=""
for _cand in "$HERE/drc-audio.sh" "$HERE/../lib/drc-audio.sh"; do
    [ -r "$_cand" ] && { DRC_LIB="$_cand"; break; }
done
if [ -n "$DRC_LIB" ]; then
    # drc-audio.sh derives REPO from HERE (= the dir above it) for its run-from-repo
    # drc.sh fallback; when installed it prefers the omdrc wrapper on PATH.
    #
    # DRC_SKIP_RESAMP: READ the DRC state here, never write it.  drc-audio.sh
    # also switches the chain to resamp when it is not already auto-resampling,
    # which is right for play-media.sh / play-bluray.sh -- those are starting a
    # movie -- but wrong here: this script runs from the session autostart and
    # plays nothing (--idle=yes, no window).  Letting it switch meant every
    # login silently rewrote whatever the listener had chosen at the panel:
    # `omdrc off` came back auto-resampling, and so did a deliberate 44.1/96 kHz
    # native setting.  The saved DRC state must outlive a reboot, so the idle
    # launcher only reads it and binds mpv to whatever device matches.
    DRC_SKIP_RESAMP=1 HERE="$(dirname "$DRC_LIB")/.." . "$DRC_LIB"

    # Bind the DRC path unconditionally, whatever the chain is doing right now.
    # Video always plays through DRC (see the header), and the web remote puts
    # the chain in resamp before it loads a file -- mpv opens the audio device
    # lazily, at playback, so the device only has to exist by then, not now.
    # Letting the *current* DRC state pick the device is what tied this launcher
    # to the chain: it had to switch DRC on at login to be sure of binding
    # /dev/dsp.play, and that is what overwrote the listener's saved setting.
    # Fixed delay also keeps the A/V trim's baseline stable across logins (see
    # src/lib/avsync.py, which anchors on mpv's launch --audio-delay).
    AUDIO_DEVICE="$DRC_DEVICE"
    AUDIO_DELAY="-$DRC_VIDEO_DELAY"
    SUB_DELAY="$DRC_SUB_DELAY"
    echo "idle mpv bound to $AUDIO_DEVICE (video delayed ${DRC_VIDEO_DELAY}s);" \
         "the web remote switches DRC to resamp when it loads a file"
else
    AUDIO_DEVICE="$FALLBACK_DEVICE"; AUDIO_DELAY="-0.67"; SUB_DELAY="0"
fi

# Don't start a second one.
if [ -S "$SOCKET" ] && mpv_running=$(pgrep -f -- "--input-ipc-server=$SOCKET") && [ -n "$mpv_running" ]; then
    echo "idle mpv already running (pid $mpv_running)"
    exit 0
fi

exec mpv --idle=yes --fs \
    --input-ipc-server="$SOCKET" \
    --ao="$AO" --audio-device="$AUDIO_DEVICE" \
    --audio-channels=stereo \
    --audio-delay="$AUDIO_DELAY" \
    --sub-delay="$SUB_DELAY"
