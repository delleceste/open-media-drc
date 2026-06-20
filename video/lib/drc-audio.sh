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

DAC_DEVICE="oss//dev/dsp0"        # direct DAC (DRC off)
DRC_DEVICE="oss//dev/dsp.play"    # virtual_oss client device (DRC on)

# Audio-path latency to hide by delaying the video, in seconds.
#   filter group delay  = 0.500 s  (EXACT: impulse peak at sample 96000 / 192000
#                                    in filters/120.blue/192000/{L,R}.raw)
# + brutefir partition  = 0.171 s  (one filter_length partition, 32768 / 192000)
# + virtual_oss buffer  ~ a little more
#   ~= 0.67 s            -> DRC_VIDEO_DELAY     (full derivation: ../AV-SYNC-DELAY.md)
: "${DRC_VIDEO_DELAY:=0.67}"
# Subtitles track the VIDEO in mpv and we shift only the audio -> no offset.
: "${DRC_SUB_DELAY:=0}"

if pgrep -q virtual_oss && [ -e /dev/dsp.play ]; then
    AUDIO_DEVICE="$DRC_DEVICE"
    AUDIO_DELAY="-$DRC_VIDEO_DELAY"   # negative = delay the VIDEO (mpv convention)
    SUB_DELAY="$DRC_SUB_DELAY"
    echo "DRC active -> audio /dev/dsp.play, video delayed ${DRC_VIDEO_DELAY}s, sub ${SUB_DELAY}s"
else
    AUDIO_DEVICE="$DAC_DEVICE"
    AUDIO_DELAY=0
    SUB_DELAY=0
    echo "DRC off -> direct /dev/dsp0 (bit-perfect DAC won't resample; video may run fast)"
fi
