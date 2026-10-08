#!/bin/sh
# Kodi with the project's Blu-ray decryption: libbluray loads MakeMKV's libmmbd
# instead of libaacs when MakeMKV is installed (lib/makemkv-env.sh, installed
# beside this script).  The panel's Kodi button runs this.
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -r "$HERE/makemkv-env.sh" ] && . "$HERE/makemkv-env.sh"
exec kodi "$@"
