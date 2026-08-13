cmake_minimum_required(VERSION 3.16)
project(open-media-drc VERSION 0.91.0 LANGUAGES NONE)

# Top-level superproject.  Classical flow:
#     cmake -B build -C host.cmake && cmake --build build && sudo cmake --install build
#
# Host values live in host.cmake (copied from host.cmake.sample) and are the
# single source of box-specific config, rendered into each subproject via
# configure_file(*.in @ONLY) — the CMake equivalent of the old config.env +
# install.sh render step.

# ── host values (config.env successor) ──────────────────────────────────────
set(AUDIO_USER    "$ENV{USER}"          CACHE STRING "Login user that owns the services and brutefir")
if(NOT AUDIO_USER)
  set(AUDIO_USER "nobody")
endif()
set(AUDIO_HOME    "/home/${AUDIO_USER}" CACHE PATH   "Home directory of AUDIO_USER")
set(GEOMETRY      "flat"                CACHE STRING "Default speaker geometry (configs/<GEOMETRY>, filters/<GEOMETRY>)")
# Extra filter sets to install alongside GEOMETRY.  GEOMETRY stays the *default*
# (it is what omdrc.conf records); every set listed here is installed too, so
# `drc.sh geometry <name>` and the web remote's picker have something to switch
# between at runtime.  Semicolon-separated, e.g. -DGEOMETRIES="120.blue;185;flat".
set(GEOMETRIES    ""                    CACHE STRING "Extra filter sets to install besides GEOMETRY (semicolon-separated)")
set(MUSIC_DIR     ""                    CACHE PATH   "Music library root (MPD / upmpdcli)")
set(VIDEO_DIR     ""                    CACHE PATH   "Video library root (omdrcvideo)")
set(QOBUZ_USER    ""                    CACHE STRING "Qobuz account username for upmpdcli (blank disables)")
set(FRIENDLY_NAME ""                    CACHE STRING "UPnP renderer friendly name (blank = hostname)")
set(OMDB_API_KEY  ""                    CACHE STRING "OMDb API key for omdrcvideo (blank disables IMDb lookups)")
set(DISC_DEVICE   "cd0"                 CACHE STRING "Optical disc device for omdrcvideo Blu-ray support")

get_filename_component(REPO_DIR "${CMAKE_CURRENT_SOURCE_DIR}" ABSOLUTE)

# ── service manager per OS ───────────────────────────────────────────────────
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  set(OMDRC_SERVICE_MANAGER "systemd")
elseif(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
  set(OMDRC_SERVICE_MANAGER "rc.d")
else()
  message(FATAL_ERROR "Unsupported system: ${CMAKE_SYSTEM_NAME} (supported: Linux, FreeBSD)")
endif()
message(STATUS "open-media-drc: service manager = ${OMDRC_SERVICE_MANAGER}, audio user = ${AUDIO_USER}")

find_program(PYTHON3 python3 REQUIRED)

# ── runtime dependency audit (warns; never fatal) ────────────────────────────
include(cmake/dependencies.cmake)

# ── subprojects ──────────────────────────────────────────────────────────────
include(cmake/core-drc.cmake)          # drc.sh + configs/filters + omdrc/omdrc-status wrappers
include(cmake/hotplug.cmake)           # DAC-hotplug + brutefir services (udev/devd + rc.d/systemd)
include(cmake/renderers.cmake)         # mpd config/drop-in + upmpdcli config/service
add_subdirectory(omdrc-ctrl)           # omdrcctrl  — control panel (:9090)
add_subdirectory(video/webremote)      # omdrcvideo — video web remote (:9080)

# ── `make user-install`: the per-user steps a root install cannot do ─────────
# Run as the audio user after the system install: it deploys the --user service,
# the desktop autostart, and the per-user BruteFIR defaults.
configure_file(cmake/user-install.sh.in "${CMAKE_BINARY_DIR}/user-install.sh" @ONLY)
add_custom_target(user-install
    COMMAND /bin/sh "${CMAKE_BINARY_DIR}/user-install.sh"
    USES_TERMINAL
    COMMENT "open-media-drc: per-user setup (run as the audio user, after the system install)")

# ── consolidated post-install checklist (printed last) ───────────────────────
# make install is root/system-level, so it cannot enable per-user (--user)
# services, symlink into a user's ~/.config, or add the user to a group; those
# steps are listed here so the operator is aware.  The /etc copies exist because
# udev / systemd-modules-load / the mpd drop-in are read from /etc, not $PREFIX.
if(OMDRC_SERVICE_MANAGER STREQUAL "systemd")
    install(CODE "
message(STATUS \"\")
message(STATUS \"============================================================\")
message(STATUS \" open-media-drc installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \" Remaining manual steps (per-user ones run as ${AUDIO_USER}):\")
message(STATUS \"\")
message(STATUS \" 1. Copy the files read from /etc (not scanned under the prefix):\")
message(STATUS \"      sudo cp ${CMAKE_INSTALL_PREFIX}/lib/udev/rules.d/99-usb-audio-drc.rules /etc/udev/rules.d/\")
message(STATUS \"      sudo udevadm control --reload\")
message(STATUS \"      sudo mkdir -p /etc/systemd/system/mpd.service.d\")
message(STATUS \"      sudo cp ${CMAKE_INSTALL_PREFIX}/share/omdrc/mpd.service.d/open-media-drc.conf /etc/systemd/system/mpd.service.d/\")
message(STATUS \"      echo snd-aloop | sudo tee /etc/modules-load.d/snd-aloop.conf\")
message(STATUS \"\")
message(STATUS \" 2. Enable the system services:\")
message(STATUS \"      sudo systemctl daemon-reload\")
message(STATUS \"      sudo systemctl enable --now mpd upmpdcli omdrcctrl\")
message(STATUS \"      (drc-usb-audio is pulled in by the udev rule on DAC plug)\")
message(STATUS \"\")
message(STATUS \" 3. Per-user setup, as ${AUDIO_USER} in the desktop session:\")
message(STATUS \"      make user-install          # (or: cmake --build build --target user-install)\")
message(STATUS \"    Deploys the omdrcvideo --user service, the mpv-idle autostart\")
message(STATUS \"    and the BruteFIR defaults; it prints the two steps that need\")
message(STATUS \"    root (loginctl enable-linger, and joining the 'audio' group).\")
message(STATUS \"============================================================\")
")
else()
    install(CODE "
message(STATUS \"\")
message(STATUS \"============================================================\")
message(STATUS \" open-media-drc installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \" Remaining manual steps:\")
message(STATUS \"\")
message(STATUS \" 1. Enable the services (rc):\")
message(STATUS \"      sysrc musicpd_enable=YES musicpd_config=${CMAKE_INSTALL_PREFIX}/etc/open-media-drc/musicpd.conf\")
message(STATUS \"      sysrc omdrc_renderer_enable=YES omdrcctrl_enable=YES\")
message(STATUS \"      sysrc upmpdcli_enable=NO qobuzconnect2mpd_enable=NO\")
message(STATUS \"      # omdrc_renderer starts the renderer last selected in the panel;\")
message(STATUS \"      # leaving a renderer enabled too would start both against MPD.\")
message(STATUS \"      sysrc drc_usb_audio_enable=YES brutefir_drc_user=${AUDIO_USER}\")
message(STATUS \"      service devd restart   # pick up the DAC attach/detach rule\")
message(STATUS \"\")
message(STATUS \" 2. Per-user setup, as ${AUDIO_USER} in the desktop session:\")
message(STATUS \"      make user-install          # (or: cmake --build build --target user-install)\")
message(STATUS \"    Deploys the BruteFIR defaults (~/.config/BruteFIR) and links the\")
message(STATUS \"    mpv-idle autostart entry into ~/.config/autostart.\")
message(STATUS \"\")
message(STATUS \" 3. Once, as ${AUDIO_USER}: passwordless sudo for virtual_oss.\")
message(STATUS \"============================================================\")
")
endif()
