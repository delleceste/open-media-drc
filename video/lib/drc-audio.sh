# Shared DRC-aware audio selection for the media-box mpv launchers.
#
# Source this (POSIX sh). The caller MUST set HERE to its own installed
# directory (play-bluray.sh / play-media.sh do: HERE=$(dirname "$(readlink -f
# "$0")")) -- this script and the webremote's src/ tree install as siblings
# under it (CMakeLists.txt), which is also where this script finds videodelay.py.
# It sets these variables for the caller's mpv command:
#   AO            -- mpv --ao value (the audio output API this box actually has)
#   AO_OPTS       -- extra mpv options for this box's audio/disc path; expand UNQUOTED
#   AUDIO_DEVICE  -- mpv --audio-device value
#   AUDIO_DELAY   -- mpv --audio-delay value (negative delays the VIDEO)
#   SUB_DELAY     -- mpv --sub-delay value

# --- MakeMKV decryption for libbluray ---------------------------------------
# libbluray would otherwise load the stock libaacs, whose host certificate the
# USB drive rejects ("has been revoked by your drive"), leaving AACS discs
# unreadable.  MakeMKV's libmmbd is a drop-in for libaacs and libbdplus.  Only
# set when MakeMKV is installed and the caller has not chosen otherwise.
if [ -z "${LIBAACS_PATH:-}" ] && [ -e /usr/lib/libmmbd.so.0 ]; then
    export LIBAACS_PATH=libmmbd LIBBDPLUS_PATH=libmmbd
fi

DRC_SH="$(command -v omdrc 2>/dev/null || true)"
DRC_STATUS_SH="$(command -v omdrc-status 2>/dev/null || true)"

# --- ensure the DRC chain is in resamp mode before playback ----------------
# Movie audio is 48/96 kHz. The direct DAC runs bit-perfect (no resampling), so a
# 48 kHz track on a DAC clocked higher plays FAST (measured 2x). Routing through
# the DRC chain in *resamp* mode makes virtual_oss/brutefir resample everything to
# 192 kHz -- correct speed AND room correction. Idempotent: only switch if we're
# not already auto-resampling. Export DRC_SKIP_RESAMP=1 to bypass (e.g. to watch
# on the bare DAC anyway).
if [ -z "${DRC_SKIP_RESAMP:-}" ] && [ -x "$DRC_SH" ] && [ -x "$DRC_STATUS_SH" ]; then
    if "$DRC_STATUS_SH" 2>/dev/null | grep -qi 'auto-resample'; then
        echo "DRC: already in resamp mode"
    else
        echo "DRC: not auto-resampling -> switching ($DRC_SH resamp) ..."
        "$DRC_SH" resamp || echo "DRC: 'resamp' failed; audio rate may be wrong"
    fi
fi

IS_LINUX=false
[ "$(uname)" = "Linux" ] && IS_LINUX=true

# DRC-on / DRC-off audio devices differ by OS:
#   FreeBSD: virtual_oss exposes /dev/dsp.play (DRC) and the raw DAC is
#            /dev/dsp.dac (the omdrc_audio role link; /dev/dsp0 without it).
#   Linux  : the DRC chain is MPD/mpv -> snd-aloop loopback hw:1,0 -> brutefir
#            (captures hw:1,1) -> USB DAC hw:0,0.  Feeding the loopback hw:1,0 is
#            what routes audio through brutefir (room correction + resample),
#            exactly as MPD does; the raw DAC is hw:0,0 (DRC off, bit-perfect).
if $IS_LINUX; then
    AO="alsa"                       # this box is ALSA-only on Linux (no Pulse/PipeWire)
    DAC_DEVICE="alsa/hw:0,0"        # direct USB DAC (DRC off)
    DRC_DEVICE="alsa/hw:1,0"        # snd-aloop loopback feeding brutefir (DRC on)
    # mpv's default ALSA buffer at 192 kHz is a single 32768-frame period (the
    # loopback's maximum).  A one-period buffer never drains through
    # snd-aloop -> brutefir: playback freezes on the first frame, silent and
    # black.  Several periods keep it flowing.
    AO_OPTS="--alsa-buffer-time=800000 --alsa-periods=8"
    # The loopback pair shares brutefir's 32768-frame period (~0.17 s at 192 kHz),
    # so mpv's audio clock advances in coarse steps and video-sync=audio then
    # drops about a third of the frames.  autosync smooths the audio clock.
    # The rest is the Blu-ray read-ahead cache (the USB drive delivers ~3.5 MB/s
    # in bursts; mpv's 1 s default leaves no cushion) and hardware decode.
    AO_OPTS="$AO_OPTS --autosync=30 --hwdec=auto-safe --stream-buffer-size=4MiB"
    AO_OPTS="$AO_OPTS --cache=yes --demuxer-max-bytes=512MiB --demuxer-max-back-bytes=64MiB"
    AO_OPTS="$AO_OPTS --demuxer-readahead-secs=600 --cache-secs=600"
else
    AO="oss"
    AO_OPTS=""
    # direct DAC (DRC off), by role — see omdrc_audio
    DAC_DEVICE="oss/$([ -e /dev/dsp.dac ] && echo /dev/dsp.dac || echo /dev/dsp0)"
    DRC_DEVICE="oss//dev/dsp.play"  # virtual_oss client device (DRC on)
fi

# Audio-path latency to hide by delaying the video, in seconds:
#   filter group delay   (the active filter's own impulse peak; different
#                          filters/rates give a different number)
# + brutefir partition   (one filter_length partition / rate)
# + virtual_oss / snd-aloop buffer ~ a little more, not covered below
# Computed fresh from whatever brutefir is actually running right now (we assume
# the filter does not change mid-playback) -- full derivation and the one-off
# measurement this replaces: ../AV-SYNC-DELAY.md. 0.67s (measured for
# filters/120.blue/192000) is the fallback if brutefir isn't up yet or the
# computation fails for any reason.
if [ -z "${DRC_VIDEO_DELAY:-}" ]; then
    # videodelay.py installs under the webremote's src/ tree, beside this script
    # (CMakeLists.txt: lib/omdrcvideo/{drc-audio.sh,src/lib/videodelay.py}).
    DRC_VIDEO_DELAY=$(python3 "$HERE/src/lib/videodelay.py" 2>/dev/null) || true
    case "$DRC_VIDEO_DELAY" in '' | *[!0-9.]* ) DRC_VIDEO_DELAY=0.67 ;; esac
fi
# Subtitles track the VIDEO in mpv and we shift only the audio -> no offset.
: "${DRC_SUB_DELAY:=0}"

# DRC-active detection differs by OS:
#   FreeBSD: virtual_oss running and its client node /dev/dsp.play present.
#   Linux  : brutefir running and the snd-aloop loopback present.
if $IS_LINUX; then
    drc_up=false
    if pgrep -x brutefir >/dev/null 2>&1 && [ -e /proc/asound/Loopback ]; then
        drc_up=true
    fi
else
    drc_up=false
    if pgrep -q virtual_oss && [ -e /dev/dsp.play ]; then
        drc_up=true
    fi
fi

if $drc_up; then
    AUDIO_DEVICE="$DRC_DEVICE"
    AUDIO_DELAY="-$DRC_VIDEO_DELAY"   # negative = delay the VIDEO (mpv convention)
    SUB_DELAY="$DRC_SUB_DELAY"
    echo "DRC active -> audio $DRC_DEVICE, video delayed ${DRC_VIDEO_DELAY}s, sub ${SUB_DELAY}s"
else
    AUDIO_DEVICE="$DAC_DEVICE"
    AUDIO_DELAY=0
    SUB_DELAY=0
    echo "DRC off -> direct $DAC_DEVICE (bit-perfect DAC won't resample; video may run fast)"
fi
