#!/bin/sh
#
# install.sh — generate the live config/service files from *.in templates,
# substituting the host-specific values from config.env (@VAR@ syntax, the same
# convention omdrc-ctrl uses for its CMake templates).
#
# This is the portability layer for the run-from-repo model: the generated files
# are read directly from this checkout (no copying into /usr/local/etc), so a
# `git pull` followed by `./install.sh` is the whole update path.  To retarget to
# another user/box, edit config.env and re-run.
#
# Rendering needs no privileges.  Deploying the generated rc.d / systemd units
# into place (and creating ~/.local/share/mpd, ~/.cache/mpd) is a separate,
# OS-specific step printed at the end.
#
set -eu

REPO_DIR=$(cd "$(dirname "$0")" && pwd)

if [ ! -f "$REPO_DIR/config.env" ]; then
	echo "error: $REPO_DIR/config.env not found" >&2
	exit 1
fi
# shellcheck disable=SC1091
. "$REPO_DIR/config.env"

# REPO_DIR is auto-detected above; config.env may override it.
: "${AUDIO_USER:?set AUDIO_USER in config.env}"
: "${AUDIO_HOME:?set AUDIO_HOME in config.env}"
: "${PREFIX:=/usr/local}"
: "${MUSIC_DIR:?set MUSIC_DIR in config.env}"
: "${QOBUZ_USER:=}"
: "${FRIENDLY_NAME:=$(hostname)}"

render() {
	tpl=$1
	out=${tpl%.in}
	sed -e "s|@AUDIO_USER@|${AUDIO_USER}|g" \
	    -e "s|@AUDIO_HOME@|${AUDIO_HOME}|g" \
	    -e "s|@REPO_DIR@|${REPO_DIR}|g" \
	    -e "s|@PREFIX@|${PREFIX}|g" \
	    -e "s|@MUSIC_DIR@|${MUSIC_DIR}|g" \
	    -e "s|@QOBUZ_USER@|${QOBUZ_USER}|g" \
	    -e "s|@FRIENDLY_NAME@|${FRIENDLY_NAME}|g" \
	    "$tpl" > "$out"
	# Preserve the executable bit — sed output does not. Needed by rc.d scripts
	# AND by KDE/Plasma autostart .desktop files: Plasma refuses to launch an
	# autostart entry that is neither root-owned nor executable ("Access ...
	# denied, not owned by root and executable flag not set"). Keep the matching
	# .in marked executable so the rendered file inherits the trust bit.
	[ -x "$tpl" ] && chmod +x "$out"
	echo "  rendered ${out#"$REPO_DIR"/}"
}

echo "Rendering templates from config.env (AUDIO_USER=${AUDIO_USER}, AUDIO_HOME=${AUDIO_HOME}):"
# Skip .git and the omdrc-ctrl submodule — it ships its own *.in templates that
# are rendered by its CMake build (different @VARS@), not by this script.
find "$REPO_DIR" -name '*.in' -not -path '*/.git/*' -not -path '*/omdrc-ctrl/*' | while read -r tpl; do
	render "$tpl"
done

echo
echo "Done. Rendered files live in this checkout."
echo
echo "Deploy rule — copy vs symlink depends on WHEN the file is read:"
echo "  * Early-boot artifacts (systemd PID1 loading units/drop-ins, udevd loading"
echo "    rules, systemd-modules-load; FreeBSD rc/devd) are read BEFORE local"
echo "    filesystems are mounted. If \$HOME / this repo is on a separate mount, a"
echo "    symlink into the checkout is dangling at that moment and the file is"
echo "    SILENTLY SKIPPED. These must be COPIED into the system path."
echo "  * Files read later / at runtime (BruteFIR defaults under ~/.config) may stay"
echo "    symlinked. Re-run this deploy step after a 'git pull' to refresh the copies."
echo
echo "Deploy reminder (needs root for the system paths):"
echo "  state dirs : mkdir -p \"${AUDIO_HOME}/.local/share/mpd\" \"${AUDIO_HOME}/.cache/mpd\" \"${AUDIO_HOME}/.cache/upmpdcli\""
echo "  video remote (idle mpv autostart, KDE/Plasma): mkdir -p \"${AUDIO_HOME}/.config/autostart\" &&"
echo "                 ln -sf \"${REPO_DIR}/video/webremote/autostart/mpv-idle.desktop\" \"${AUDIO_HOME}/.config/autostart/mpv-idle.desktop\""
echo "  browser launchers (No-DRC Firefox/Chrome/Chromium in the KDE menu):"
echo "                 mkdir -p \"${AUDIO_HOME}/.local/share/applications\" &&"
echo "                 for b in firefox chromium chrome; do"
echo "                   ln -sf \"${REPO_DIR}/browser-nodrc/\$b-nodrc.desktop\" \\"
echo "                          \"${AUDIO_HOME}/.local/share/applications/\$b-nodrc.desktop\" ; done"
echo "                 update-desktop-database \"${AUDIO_HOME}/.local/share/applications\" 2>/dev/null || true"

if [ "$(uname)" = "FreeBSD" ]; then
	cat <<EOF
  FreeBSD:
    rc.d  : for s in musicpd brutefir_drc drc_usb_audio upmpdcli; do
              cp "${REPO_DIR}/etc/rc.d/\$s" /usr/local/etc/rc.d/\$s   # COPY: read at early boot
            done
            # Copies (not symlinks): rc.d scripts are read by rc at early boot,
            # before a separately-mounted \$HOME/repo is available. brutefir_drc is
            # the worker invoked by drc_usb_audio (service brutefir_drc onestart) —
            # it must resolve, but is NOT enabled below.
    devd  : cp "${REPO_DIR}/etc/devd/usb-audio-drc.conf" /usr/local/etc/devd/usb-audio-drc.conf   # COPY
            service devd restart
    enable: add to /etc/rc.conf — musicpd_enable=YES upmpdcli_enable=YES \\
            drc_usb_audio_enable=YES
            # Enable ONLY drc_usb_audio for DRC: it probes for the DAC at boot
            # and is driven by devd on hotplug. Do NOT enable brutefir_drc.
    webremote: ln -sf "${REPO_DIR}/video/webremote/rc.d/omdrcvideo" /usr/local/etc/rc.d/omdrcvideo
            sysrc omdrcvideo_enable=YES
            service omdrcvideo start          # phone video web remote on :9080
            # The idle mpv it drives autostarts in the KDE session (see above).
    brutefir: mkdir -p "${AUDIO_HOME}/.config/BruteFIR"
              cp "${REPO_DIR}/brutefir_defaults.conf" "${AUDIO_HOME}/.config/BruteFIR/brutefir_defaults.conf"
              # Required: BruteFIR inherits its I/O devices from this file. If it
              # is missing, BruteFIR auto-generates a broken ~/.brutefir_defaults
              # (a "file" I/O module with no path) and fails to start.
EOF
else
	cat <<EOF
  Linux:
    # All system-path files below are COPIED (not symlinked): each is read by
    # early-boot infrastructure before /home (a separate mount) is available, so a
    # symlink into the checkout would be dangling and silently skipped.
    modules-load.d : sudo cp "${REPO_DIR}/etc/modules-load.d/snd-aloop.conf" /etc/modules-load.d/
                     sudo modprobe snd-aloop
    systemd system : for s in drc-usb-audio upmpdcli; do
                       sudo cp "${REPO_DIR}/etc/systemd/system/\$s.service" /etc/systemd/system/
                     done
                     sudo mkdir -p /etc/systemd/system/mpd.service.d
                     sudo cp "${REPO_DIR}/etc/systemd/system/mpd.service.d/open-media-drc.conf" /etc/systemd/system/mpd.service.d/omdrc.conf
                     sudo systemctl disable --now mpd.socket  # not used: we bypass socket activation
                     sudo systemctl daemon-reload
                     sudo systemctl enable --now upmpdcli.service
                     sudo systemctl restart mpd.service
    udev (USB DAC) : sudo cp "${REPO_DIR}/99-usb-audio-drc.rules" /etc/udev/rules.d/
                     sudo udevadm control --reload-rules
    webremote      : no systemd unit yet — run it directly (or add one):
                     python3 "${REPO_DIR}/video/webremote/src/app.py" \\
                       --config "${REPO_DIR}/video/webremote/webremote.conf"   # :9080
    brutefir       : mkdir -p "${AUDIO_HOME}/.config/BruteFIR"
                     cp "${REPO_DIR}/brutefir_defaults.linux.conf" "${AUDIO_HOME}/.config/BruteFIR/brutefir_defaults.conf"
                     # Required: BruteFIR inherits its I/O devices from this file. If
                     # it is missing, BruteFIR auto-generates a broken
                     # ~/.brutefir_defaults ("file" I/O module) and fails to start.
EOF
fi
