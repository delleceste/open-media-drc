# Shared DRC-aware audio selection for the media-box mpv launchers.
#
# Source this (POSIX sh). The caller MUST set HERE to the video/ directory
# (play-bluray.sh / play-media.sh do: HERE=$(dirname "$(readlink -f "$0")")).
# It sets three variables for the caller's mpv command:
#   AUDIO_DEVICE  -- mpv --audio-device value
#   AUDIO_DELAY   -- mpv --audio-delay value (negative delays the VIDEO)
#   SUB_DELAY     -- mpv --sub-delay value

REPO="$HERE/.."   # open-media-drc repo root (drc.sh / drc-status.sh live here)

# --- ensure the DRC chain is in resamp mode before playback ----------------
# Movie audio is 48/96 kHz. The direct DAC runs bit-perfect (no resampling), so a
# 48 kHz track on a DAC clocked higher plays FAST (measured 2x). Routing through
# the DRC chain in *resamp* mode makes virtual_oss/brutefir resample everything to
# 192 kHz -- correct speed AND room correction. Idempotent: only switch if we're
# not already auto-resampling. Export DRC_SKIP_RESAMP=1 to bypass (e.g. to watch
# on the bare DAC anyway).
if [ -z "${DRC_SKIP_RESAMP:-}" ] && [ -x "$REPO/drc.sh" ] && [ -x "$REPO/drc-status.sh" ]; then
    if "$REPO/drc-status.sh" 2>/dev/null | grep -qi 'auto-resample'; then
        echo "DRC: already in resamp mode"
    else
        echo "DRC: not auto-resampling -> switching ($REPO/drc.sh resamp) ..."
        "$REPO/drc.sh" resamp || echo "DRC: 'resamp' failed; audio rate may be wrong"
    fi
fi

IS_LINUX=false
[ "$(uname)" = "Linux" ] && IS_LINUX=true

# DRC-on / DRC-off audio devices differ by OS:
#   FreeBSD: virtual_oss exposes /dev/dsp.play (DRC) and the raw DAC is /dev/dsp0.
#   Linux  : the DRC chain is MPD/mpv -> snd-aloop loopback hw:1,0 -> brutefir
#            (captures hw:1,1) -> USB DAC hw:0,0.  Feeding the loopback hw:1,0 is
#            what routes audio through brutefir (room correction + resample),
#            exactly as MPD does; the raw DAC is hw:0,0 (DRC off, bit-perfect).
if $IS_LINUX; then
    DAC_DEVICE="alsa/hw:0,0"        # direct USB DAC (DRC off)
    DRC_DEVICE="alsa/hw:1,0"        # snd-aloop loopback feeding brutefir (DRC on)
else
    DAC_DEVICE="oss//dev/dsp0"      # direct DAC (DRC off)
    DRC_DEVICE="oss//dev/dsp.play"  # virtual_oss client device (DRC on)
fi

# Audio-path latency to hide by delaying the video, in seconds.
#   filter group delay  = 0.500 s  (EXACT: impulse peak at sample 96000 / 192000
#                                    in filters/120.blue/192000/{L,R}.raw)
# + brutefir partition  = 0.171 s  (one filter_length partition, 32768 / 192000)
# + virtual_oss / snd-aloop buffer ~ a little more
#   ~= 0.67 s            -> DRC_VIDEO_DELAY     (full derivation: ../AV-SYNC-DELAY.md)
#   The filters are identical on Linux, so the same delay applies there too.
: "${DRC_VIDEO_DELAY:=0.67}"
# Subtitles track the VIDEO in mpv and we shift only the audio -> no offset.
: "${DRC_SUB_DELAY:=0}"

# DRC-active detection differs by OS:
#   FreeBSD: virtual_oss running and its client node /dev/dsp.play present.
#   Linux  : brutefir running and the snd-aloop loopback present.  The filter
#            group delay is identical (same filters), so the 0.67s video delay
#            applies unchanged.
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
