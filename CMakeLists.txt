cmake_minimum_required(VERSION 3.16)
project(open-media-drc VERSION 0.93.0 LANGUAGES NONE)

# Top-level superproject.  Classical Unix Makefiles flow:
#     mkdir -p build && cd build
#     cmake .. -C ../host.cmake && make && sudo make install
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
# Where configs/<geo> and filters/<geo> are looked up, in order; first match wins.
# The default is this checkout, i.e. the historical single-repository layout.
# Room measurements and filters are personal data with no value to anyone else,
# so they can live in a separate (private) checkout instead — list it here and
# the public repo keeps shipping only the generic `flat` set:
#     -DOMDRC_SITE_DATA_DIRS="${CMAKE_SOURCE_DIR};/home/me/devel/omdrc-site"
set(OMDRC_SITE_DATA_DIRS "${CMAKE_CURRENT_SOURCE_DIR}" CACHE STRING
    "Search path for configs/<geo> + filters/<geo> (semicolon-separated, first match wins)")
set(MUSIC_DIR     ""                    CACHE PATH   "Music library root (MPD / upmpdcli)")
set(VIDEO_DIR     ""                    CACHE PATH   "Video library root (omdrcvideo)")
set(QOBUZ_USER    ""                    CACHE STRING "Qobuz account username for upmpdcli (blank disables)")
set(FRIENDLY_NAME ""                    CACHE STRING "UPnP renderer friendly name (blank = hostname)")
set(OMDB_API_KEY  ""                    CACHE STRING "OMDb API key for omdrcvideo (blank disables IMDb lookups)")
set(DISC_DEVICE   "cd0"                 CACHE STRING "Optical disc device for omdrcvideo Blu-ray support")

get_filename_component(REPO_DIR "${CMAKE_CURRENT_SOURCE_DIR}" ABSOLUTE)

include(cmake/console.cmake)          # OMDRC_* ANSI colours for our own output

# ── host.cmake sanity ────────────────────────────────────────────────────────
# host.cmake is read only by `cmake -C host.cmake ..`, and CMake skips it for
# cache entries that already exist — so a plain `cmake ..` silently configures
# everything from the built-in defaults, and a later -C cannot repair it.
# host.cmake sets OMDRC_HOST_CMAKE; its absence is the whole detection.
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/host.cmake" AND NOT OMDRC_HOST_CMAKE)
    message(WARNING
        "host.cmake exists but this build directory was not initialised from it, "
        "so the built-in defaults are in effect.  Adding -C now will not help; "
        "start a fresh build directory:\n"
        "    rm -rf build && mkdir build && cd build && cmake -C ../host.cmake ..")
endif()

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
if(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
  add_subdirectory(cdin)               # omdrc-cdin — CD/S-PDIF capture bridge (OSS only)
endif()

# ── `make user-install`: the per-user steps a root install cannot do ─────────
# Run as the audio user after the system install: it deploys the --user services,
# the desktop autostart, and the per-user BruteFIR defaults.
configure_file(cmake/user-install.sh.in "${CMAKE_BINARY_DIR}/user-install.sh" @ONLY)
add_custom_target(user-install
    COMMAND /bin/sh "${CMAKE_BINARY_DIR}/user-install.sh"
    USES_TERMINAL
    COMMENT "open-media-drc: per-user setup (run as the audio user, after the system install)")

# ── consolidated post-install checklist (guaranteed to print last) ───────────
# Subprojects have their own local hints.  This generated script is registered
# after every add_subdirectory(), so `make install` finishes with one coherent
# host checklist plus the exact geometry/design/rate and provenance inventory.
configure_file(
    cmake/install-summary.cmake.in
    "${CMAKE_CURRENT_BINARY_DIR}/install-summary.cmake"
    @ONLY)
install(SCRIPT "${CMAKE_CURRENT_BINARY_DIR}/install-summary.cmake")
