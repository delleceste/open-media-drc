# MakeMKV decryption for libbluray (Linux).  Source this (POSIX sh).
#
# libbluray would otherwise load the stock libaacs, whose host certificate the
# USB drive rejects ("has been revoked by your drive"), and which only knows the
# discs listed in KEYDB.cfg.  MakeMKV's libmmbd is a drop-in for libaacs and
# libbdplus.  Only set when MakeMKV is installed and the caller has not chosen
# otherwise.  libmmbd needs a valid MakeMKV beta key (the video web remote's
# "Update MakeMKV key").  The FreeBSD side is untested: see
# BLURAY-DECRYPTION.md.  Sourced by drc-audio.sh (mpv) and kodi.sh (Kodi).
if [ "$(uname)" = "Linux" ] && [ -z "${LIBAACS_PATH:-}" ]; then
    for _omdrc_mmbd in /usr/lib/libmmbd.so.0 /usr/local/lib/libmmbd.so.0; do
        if [ -e "$_omdrc_mmbd" ]; then
            export LIBAACS_PATH=libmmbd LIBBDPLUS_PATH=libmmbd
            break
        fi
    done
    unset _omdrc_mmbd
fi
