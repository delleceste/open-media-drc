cmake_minimum_required(VERSION 3.16)

# omdrcvideo — the video web remote (Flask, :9080).
#
# On Linux it is a systemd --user service: it drives the persistent idle mpv
# started by the desktop session autostart, over that session's JSON IPC socket,
# so it must live in the graphical user scope (this is the one component that
# genuinely requires --user).  On FreeBSD it is an rc.d service that runs as the
# audio user (the app talks to mpv over a unix socket, so it needs no DISPLAY).
#
# Builds standalone (cmake video/webremote) or as a subproject of the top-level
# open-media-drc CMake, which supplies the host values below.

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(omdrcvideo VERSION 1.0.0 LANGUAGES NONE)
    set(AUDIO_USER   "$ENV{USER}" CACHE STRING "Login user that owns the video service")
    if(NOT AUDIO_USER)
        set(AUDIO_USER "nobody")
    endif()
    set(VIDEO_DIR    ""    CACHE PATH   "Video library root")
    set(OMDB_API_KEY ""    CACHE STRING "OMDb API key (blank disables IMDb lookups)")
    set(DISC_DEVICE  "cd0" CACHE STRING "Optical disc device for Blu-ray support")
    if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
        set(OMDRC_SERVICE_MANAGER "systemd")
    elseif(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
        set(OMDRC_SERVICE_MANAGER "rc.d")
    else()
        message(FATAL_ERROR "Unsupported system: ${CMAKE_SYSTEM_NAME}")
    endif()
    find_program(PYTHON3 python3 REQUIRED)
endif()

set(OMDRCVIDEO_SERVICE_USER "${AUDIO_USER}")

execute_process(COMMAND ${PYTHON3} -c "import flask"
                RESULT_VARIABLE _flask_rc OUTPUT_QUIET ERROR_QUIET)
if(NOT _flask_rc EQUAL 0)
    message(WARNING "Flask is not installed.  Run:  pip install flask")
endif()

# ── render config, launcher, service unit ────────────────────────────────────
configure_file(webremote.conf.in    ${CMAKE_CURRENT_BINARY_DIR}/webremote.conf @ONLY)
configure_file(src/omdrcvideo.sh.in  ${CMAKE_CURRENT_BINARY_DIR}/omdrcvideo     @ONLY)

if(OMDRC_SERVICE_MANAGER STREQUAL "systemd")
    configure_file(systemd/omdrcvideo.service.in  ${CMAKE_CURRENT_BINARY_DIR}/omdrcvideo.service @ONLY)
    configure_file(autostart/mpv-idle.desktop.in  ${CMAKE_CURRENT_BINARY_DIR}/mpv-idle.desktop   @ONLY)
else()
    configure_file(rc.d/omdrcvideo.in             ${CMAKE_CURRENT_BINARY_DIR}/omdrcvideo.rc       @ONLY)
endif()

# ── install: application (preserve the src/ nesting app.py assumes) ───────────
# app.py resolves ../disc.sh and templates/ relative to its own dir, so it must
# stay under .../lib/omdrcvideo/src/ with the helpers one level up.
install(DIRECTORY src/
        DESTINATION lib/omdrcvideo/src
        FILES_MATCHING
        PATTERN "*.py"
        PATTERN "*.html")

# helper scripts, one level above src/ (app.py calls ../disc.sh; mpv-idle.sh
# finds drc-audio.sh next to itself).  drc-audio.sh is the shared video lib.
install(PROGRAMS disc.sh mpv-idle.sh ../lib/drc-audio.sh
        DESTINATION lib/omdrcvideo)

install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/omdrcvideo
        DESTINATION bin)

# config — guarded so a reinstall never clobbers the user's edited copy / OMDb key.
# The EXISTS test needs DESTDIR prepended manually; file(INSTALL) adds it itself.
install(CODE "
  set(_cfg \"\$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/etc/omdrcvideo/webremote.conf\")
  if(EXISTS \"\${_cfg}\")
    message(STATUS \"omdrcvideo: keeping existing config \${_cfg}\")
  else()
    file(INSTALL \"${CMAKE_CURRENT_BINARY_DIR}/webremote.conf\"
         DESTINATION \"${CMAKE_INSTALL_PREFIX}/etc/omdrcvideo\")
  endif()
")

# ── service integration ──────────────────────────────────────────────────────
if(OMDRC_SERVICE_MANAGER STREQUAL "systemd")
    install(FILES    ${CMAKE_CURRENT_BINARY_DIR}/omdrcvideo.service DESTINATION lib/systemd/user)
    install(FILES    ${CMAKE_CURRENT_BINARY_DIR}/mpv-idle.desktop   DESTINATION share/omdrcvideo/autostart)
else()
    install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/omdrcvideo.rc      DESTINATION etc/rc.d RENAME omdrcvideo)
endif()

# ── post-install hints ────────────────────────────────────────────────────────
if(OMDRC_SERVICE_MANAGER STREQUAL "systemd")
    install(CODE "
message(STATUS \"\")
message(STATUS \"omdrcvideo installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"  config : ${CMAKE_INSTALL_PREFIX}/etc/omdrcvideo/webremote.conf\")
message(STATUS \"\")
message(STATUS \"It is a systemd --user service (it drives the desktop idle mpv).\")
message(STATUS \"As ${OMDRCVIDEO_SERVICE_USER}, inside the graphical session:\")
message(STATUS \"  mkdir -p ~/.config/systemd/user ~/.config/autostart\")
message(STATUS \"  ln -sf ${CMAKE_INSTALL_PREFIX}/lib/systemd/user/omdrcvideo.service ~/.config/systemd/user/\")
message(STATUS \"  ln -sf ${CMAKE_INSTALL_PREFIX}/share/omdrcvideo/autostart/mpv-idle.desktop ~/.config/autostart/\")
message(STATUS \"  systemctl --user daemon-reload && systemctl --user enable --now omdrcvideo\")
message(STATUS \"  sudo loginctl enable-linger ${OMDRCVIDEO_SERVICE_USER}   # survive logout\")
message(STATUS \"  Open: http://<hostname>:9080\")
")
else()
    install(CODE "
message(STATUS \"\")
message(STATUS \"omdrcvideo installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"  config : ${CMAKE_INSTALL_PREFIX}/etc/omdrcvideo/webremote.conf\")
message(STATUS \"\")
message(STATUS \"Enable the rc.d service:\")
message(STATUS \"  sysrc omdrcvideo_enable=YES\")
message(STATUS \"  sysrc omdrcvideo_user=${OMDRCVIDEO_SERVICE_USER}\")
message(STATUS \"  service omdrcvideo start          # :9080\")
message(STATUS \"  (the idle mpv it drives autostarts in the desktop session)\")
")
endif()
