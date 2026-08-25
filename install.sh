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
# Skip .git and the CMake-owned subprojects — they ship their own *.in templates
# rendered by CMake (different @VARS@: @CMAKE_INSTALL_PREFIX@ etc.), not by this
# script: omdrc-ctrl (omdrcctrl), video/webremote (omdrcvideo), and cdin
# (omdrc-cdin).  Rendering a CMake template here leaves its CMake-only tokens
# literal; copying that output into rc.d then makes the service try to execute
# a path such as "@CMAKE_INSTALL_PREFIX@/bin/omdrc-cdin".
find "$REPO_DIR" -name '*.in' -not -path '*/.git/*' \
     -not -path '*/omdrc-ctrl/*' -not -path '*/video/webremote/*' \
     -not -path '*/cdin/*' | while read -r tpl; do
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
echo "  video remote (idle mpv autostart, KDE/Plasma): CMake-owned, like the rest of"
echo "                 video/webremote — 'sudo make install' renders and installs the"
echo "                 entry, 'make user-install' links it. By hand, after the install:"
echo "                 mkdir -p \"${AUDIO_HOME}/.config/autostart\" &&"
echo "                 ln -sf \"${PREFIX}/share/omdrcvideo/autostart/mpv-idle.desktop\" \"${AUDIO_HOME}/.config/autostart/mpv-idle.desktop\""
echo "  browser launchers (No-DRC Firefox/Chrome/Chromium in the KDE menu):"
echo "                 mkdir -p \"${AUDIO_HOME}/.local/share/applications\" &&"
echo "                 for b in firefox chromium chrome; do"
echo "                   ln -sf \"${REPO_DIR}/browser-nodrc/\$b-nodrc.desktop\" \\"
echo "                          \"${AUDIO_HOME}/.local/share/applications/\$b-nodrc.desktop\" ; done"
echo "                 update-desktop-database \"${AUDIO_HOME}/.local/share/applications\" 2>/dev/null || true"

if [ "$(uname)" = "FreeBSD" ]; then
	cat <<EOF
  FreeBSD:
    rc.d  : for s in musicpd omdrc_audio upmpdcli omdrc_renderer; do
              cp "${REPO_DIR}/etc/rc.d/\$s" /usr/local/etc/rc.d/\$s   # COPY: read at early boot
            done
            # Copies (not symlinks): rc.d scripts are read by rc at early boot,
            # before a separately-mounted \$HOME/repo is available. omdrc_audio
            # owns role links, the master rcvar and the su -l user transition.
    MPD hook: sh "${REPO_DIR}/scripts/prepare-musicpd-rc-conf-dir.sh" /usr/local/etc/rc.conf.d/musicpd
              cp "${REPO_DIR}/etc/rc.conf.d/musicpd/omdrc_audio" \\
                 /usr/local/etc/rc.conf.d/musicpd/omdrc_audio          # COPY
            # If musicpd was already one file, the helper preserves it as
            # 00-local.conf before creating the directory; no setting is lost.
            # After a successful MPD start/restart, this makes one bounded
            # reconcile request. It has no lock, sleep, poller or reverse edge.
    devd  : cp "${REPO_DIR}/etc/devd/omdrc-audio.conf" \\
               /usr/local/etc/devd/omdrc-audio.conf                       # COPY
            service devd restart
            # The pcm rules start a detached 'service omdrc_audio reconcile'.
            # Do NOT match USB attach instead: the
            # kernel emits one event PER INTERFACE and a UAC2 DAC has several,
            # and unrelated USB detach events must never touch audio.
    enable: add to /etc/rc.conf — musicpd_enable=YES omdrc_renderer_enable=YES \\
            omdrc_audio_enable=YES omdrc_audio_user=${AUDIO_USER}
            # omdrc_audio keeps /dev/dsp.dac pointed at the DAC — at boot from
            # rc, afterwards from devd reconciliation — so brutefir, MPD and the
            # scripts never name a pcm unit number.  FreeBSD numbers pcm units
            # in USB attach order, which is not stable across boots or replugs.
            # With one sound card it needs no configuration at all.
            # For the CD input, name the capture card; that is what creates
            # /dev/dsp.capture, and leaving it empty is how the feature stays
            # out of the way on a box that has no capture device:
            #   sysrc omdrc_audio_capture="ESI U24XL"     # or 0xVID:0xPID
            # Its digital input is then selected automatically on every attach
            # (omdrc_audio_capture_recsrc, default "auto").
            # Remove obsolete drc_usb_audio_*, brutefir_drc_* and
            # omdrc_sndlink_* rc.conf keys after copying their values.
            # Remove any hard-coded hw.snd.default_unit=N from sysctl.conf:
            # omdrc_audio discovers the DAC role and owns that setting.
    renderer: omdrc_renderer starts the renderer that was active last —
            qobuzconnect2mpd or upmpdcli, whichever omdrcctrl's toggle recorded
            in ${REPO_DIR}/last_renderer — so a reboot comes back as you left it.
            Run both renderers and omdrcctrl as the same AUDIO_USER on FreeBSD,
            matching the Linux user-service model:
              sysrc qobuzconnect2mpd_user=${AUDIO_USER} qobuzconnect2mpd_group=$(id -gn "${AUDIO_USER}")
              sysrc qobuzconnect2mpd_homedir=/var/db/qobuzconnect2mpd
            Keep qconnectstatedir=/var/db/qobuzconnect2mpd in its config. For an
            existing dedicated-user installation, migrate that private state once:
              chown -R ${AUDIO_USER}:$(id -gn "${AUDIO_USER}") /var/db/qobuzconnect2mpd
            Keep BOTH renderers' own rcvars off, or rc would start one behind
            its back and two front-ends would drive MPD at once:
              sysrc upmpdcli_enable=NO qobuzconnect2mpd_enable=NO
              sysrc omdrc_renderer_enable=YES
            # Query/override by hand:  ${REPO_DIR}/scripts/omdrc-renderer status
            #                          ${REPO_DIR}/scripts/omdrc-renderer set upmpdcli
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
    # Scope split: early-boot / hotplug audio infra stays in the SYSTEM scope
    # (mpd, drc-usb-audio, brutefir-drc); the app/renderer layer runs as systemd
    # --user units (upmpdcli, omdrcctrl, qobuzconnect2mpd, omdrcvideo, drc) so the
    # omdrcctrl renderer toggle can drive them with \`systemctl --user\` (no sudo)
    # and both renderers share one scope. Enable lingering so the user units start
    # at boot and survive logout.
    systemd system : sudo cp "${REPO_DIR}/etc/systemd/system/drc-usb-audio.service" /etc/systemd/system/
                     sudo mkdir -p /etc/systemd/system/mpd.service.d
                     sudo cp "${REPO_DIR}/etc/systemd/system/mpd.service.d/open-media-drc.conf" /etc/systemd/system/mpd.service.d/omdrc.conf
                     sudo systemctl disable --now mpd.socket  # not used: we bypass socket activation
                     # Retire any system-scope renderer/UI units from earlier installs
                     # so they don't double-instance the --user ones below.
                     sudo systemctl disable --now upmpdcli.service omdrcctrl.service 2>/dev/null || true
                     sudo systemctl daemon-reload
                     sudo systemctl restart mpd.service
                     # drc-usb-audio probes the DAC at boot and is re-triggered by the
                     # udev rule on hotplug; it runs 'drc.sh restore', which starts
                     # BruteFIR — so do the 'brutefir' step below FIRST (its defaults
                     # file must exist) before enabling this.
                     sudo systemctl enable --now drc-usb-audio.service
    systemd --user : loginctl enable-linger "${AUDIO_USER}"   # user units start at boot
                     mkdir -p "${AUDIO_HOME}/.config/systemd/user"
                     # upmpdcli renderer (toggled against qobuzconnect2mpd):
                     ln -sf "${REPO_DIR}/etc/systemd/user/upmpdcli.service" \\
                            "${AUDIO_HOME}/.config/systemd/user/upmpdcli.service"
                     # omdrc-renderer.service: oneshot that starts whichever renderer
                     # was active last (recorded in ${REPO_DIR}/last_renderer by the
                     # omdrcctrl toggle), so a reboot comes back as you left it.
                     ln -sf "${REPO_DIR}/etc/systemd/user/omdrc-renderer.service" \\
                            "${AUDIO_HOME}/.config/systemd/user/omdrc-renderer.service"
                     # drc.service: restores the last DRC profile on login (oneshot).
                     ln -sf "${REPO_DIR}/drc.service" \\
                            "${AUDIO_HOME}/.config/systemd/user/drc.service"
                     systemctl --user daemon-reload
                     systemctl --user enable --now omdrc-renderer.service drc.service
                     # Enable omdrc-renderer INSTEAD of the renderers themselves: a
                     # renderer left enabled would start at boot behind it, and both
                     # front-ends would drive MPD at once. The units still have to
                     # exist (above) — the toggle and omdrc-renderer start them.
                     systemctl --user disable upmpdcli.service qobuzconnect2mpd.service 2>/dev/null || true
                     # NOTE: qobuzconnect2mpd is installed separately (not shipped here);
                     # if used, it must also be a --user unit so the toggle can reach it.
    omdrcctrl UI   : # audio web UI (:9090) — built and --user-installed by its own
                     # CMake flow (renders its @VARS@, lands in ~/.local/share/systemd/user):
                     ( cd "${REPO_DIR}/omdrc-ctrl" && cmake -S . -B build -DUSER_INSTALL=ON && \\
                       cmake --build build && cmake --install build )
                     systemctl --user daemon-reload
                     systemctl --user enable --now omdrcctrl
    udev (USB DAC) : sudo cp "${REPO_DIR}/99-usb-audio-drc.rules" /etc/udev/rules.d/
                     sudo udevadm control --reload-rules
    webremote      : ln -sf "${REPO_DIR}/video/webremote/systemd/omdrcvideo.service" \\
                            "${AUDIO_HOME}/.config/systemd/user/omdrcvideo.service"
                     systemctl --user daemon-reload
                     systemctl --user enable --now omdrcvideo.service   # :9080
                     # The idle mpv it drives autostarts in the KDE session (see above).
    brutefir       : mkdir -p "${AUDIO_HOME}/.config/BruteFIR"
                     cp "${REPO_DIR}/brutefir_defaults.linux.conf" "${AUDIO_HOME}/.config/BruteFIR/brutefir_defaults.conf"
                     # Required: BruteFIR inherits its I/O devices from this file. If
                     # it is missing, BruteFIR auto-generates a broken
                     # ~/.brutefir_defaults ("file" I/O module) and fails to start.
EOF
fi
