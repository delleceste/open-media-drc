#!/bin/sh
# Per-user setup for open-media-drc.  Run AS THE AUDIO USER after the system
# install:
#     sudo cmake --install build      # (or sudo make install)
#     cmake --build build --target user-install   # (or: make user-install)
#
# It does only what a root/system install cannot: the systemd --user services,
# the desktop autostart symlink, and the per-user BruteFIR defaults.  Idempotent,
# and it never uses sudo itself — the two steps that need root (linger, the
# audio group) are printed for you to run.  Rendered by CMake from host.cmake.
set -u

PREFIX="@CMAKE_INSTALL_PREFIX@"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}"

if [ "$(id -u)" -eq 0 ]; then
    echo "user-install: run this as the audio user, not root." >&2
    exit 1
fi

# --- BruteFIR runtime defaults (OS-specific source; required or brutefir fails) ---
mkdir -p "$CFG/BruteFIR"
if [ "$(uname)" = "Linux" ]; then
    _bf="$PREFIX/share/omdrc/brutefir_defaults.linux.conf"
else
    _bf="$PREFIX/share/omdrc/brutefir_defaults.conf"
fi
if [ -e "$CFG/BruteFIR/brutefir_defaults.conf" ]; then
    echo "user-install: keeping existing $CFG/BruteFIR/brutefir_defaults.conf"
elif [ -f "$_bf" ]; then
    cp "$_bf" "$CFG/BruteFIR/brutefir_defaults.conf" && \
        echo "user-install: installed BruteFIR defaults"
fi

# --- omdrcvideo idle-mpv autostart (desktop session, both OSes) ---
if [ -f "$PREFIX/share/omdrcvideo/autostart/mpv-idle.desktop" ]; then
    mkdir -p "$CFG/autostart"
    ln -sf "$PREFIX/share/omdrcvideo/autostart/mpv-idle.desktop" \
           "$CFG/autostart/mpv-idle.desktop"
    echo "user-install: linked mpv-idle autostart"
fi

# --- omdrcvideo --user service (Linux; FreeBSD serves the web UI from rc.d) ---
if [ "$(uname)" = "Linux" ] && [ -f "$PREFIX/lib/systemd/user/omdrcvideo.service" ]; then
    mkdir -p "$CFG/systemd/user"
    ln -sf "$PREFIX/lib/systemd/user/omdrcvideo.service" \
           "$CFG/systemd/user/omdrcvideo.service"
    systemctl --user daemon-reload 2>/dev/null || true
    if systemctl --user enable --now omdrcvideo.service 2>/dev/null; then
        echo "user-install: enabled omdrcvideo (:9080)"
    else
        echo "user-install: enable omdrcvideo later: systemctl --user enable --now omdrcvideo"
    fi
    loginctl enable-linger "$(id -un)" 2>/dev/null \
        || echo "user-install: to survive logout, run: sudo loginctl enable-linger $(id -un)"
fi

# --- remembered renderer restore (Linux user scope) --------------------------
# Both switchable renderers must be in the same --user scope.  Enable the
# restore unit, never one renderer directly, or two front-ends can drive MPD.
if [ "$(uname)" = "Linux" ] && \
        [ -f "$PREFIX/lib/systemd/user/upmpdcli.service" ] && \
        [ -f "$PREFIX/lib/systemd/user/omdrc-renderer.service" ]; then
    mkdir -p "$CFG/systemd/user"
    ln -sf "$PREFIX/lib/systemd/user/upmpdcli.service" \
           "$CFG/systemd/user/upmpdcli.service"
    ln -sf "$PREFIX/lib/systemd/user/omdrc-renderer.service" \
           "$CFG/systemd/user/omdrc-renderer.service"
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user disable upmpdcli.service qobuzconnect2mpd.service \
        2>/dev/null || true
    if systemctl --user enable --now omdrc-renderer.service 2>/dev/null; then
        echo "user-install: enabled remembered renderer restore"
    else
        echo "user-install: enable renderer later: systemctl --user enable --now omdrc-renderer"
    fi
fi

# --- ALSA hw access needs the 'audio' group (root; advise only) ---
if [ "$(uname)" = "Linux" ]; then
    if ! id -nG 2>/dev/null | tr ' ' '\n' | grep -qx audio; then
        echo "user-install: for ALSA hw access run: sudo usermod -aG audio $(id -un)  (then re-login)"
    fi
fi

echo "user-install: done."
