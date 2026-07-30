cmake_minimum_required(VERSION 3.16)
project(open-media-drc VERSION 1.0.0 LANGUAGES NONE)

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
set(GEOMETRY      "flat"                CACHE STRING "Speaker geometry (configs/<GEOMETRY>, filters/<GEOMETRY>)")
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

# ── subprojects ──────────────────────────────────────────────────────────────
add_subdirectory(omdrc-ctrl)           # omdrcctrl  — control panel (:9090)
add_subdirectory(video/webremote)      # omdrcvideo — video web remote (:9080)

# omdrcctrl's commands.conf still points at ${REPO_DIR}/drc.sh (the checkout).
# When core-drc is folded in below it will switch to ${PREFIX}/bin/omdrc, since
# that wrapper only exists once core-drc is installed.
#
# Pending migration from install.sh / root Makefile into this superproject:
#   add_subdirectory(core-drc)          # drc.sh + configs/filters + DAC-hotplug glue
#   add_subdirectory(renderers)         # mpd drop-in + upmpdcli
