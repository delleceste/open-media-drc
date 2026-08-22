cmake_minimum_required(VERSION 3.16)

# omdrcctrl — the web control panel (:9090).
#
# Builds standalone (cmake omdrc-ctrl) or as a subproject of the top-level
# open-media-drc superproject, which supplies AUDIO_USER / REPO_DIR /
# OMDRC_SERVICE_MANAGER / PYTHON3.  As a headless service it defaults to a
# system unit running as the audio user (no desktop required); the --user mode
# (USER_INSTALL) is a standalone-only, no-root convenience — a package cannot
# enable a --user unit, so the superproject always uses the system unit.

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(omdrcctrl VERSION 1.0.0 LANGUAGES NONE)

    option(USER_INSTALL "Install as a systemd --user service on Linux (no root required)" OFF)
    set(OMDRCCTRL_SERVICE_USER "$ENV{USER}" CACHE STRING "User for the system service")
    if(NOT OMDRCCTRL_SERVICE_USER)
        set(OMDRCCTRL_SERVICE_USER "nobody")
    endif()
    get_filename_component(_OMDRC_REPO_DIR_DEFAULT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
    set(OMDRC_REPO_DIR "${_OMDRC_REPO_DIR_DEFAULT}" CACHE PATH "Path to the open-media-drc checkout used by the default commands.conf")
    # Home of the user the panel runs as: commands.conf reads MPD's log out of
    # it.  Standalone builds have no AUDIO_USER, so take the builder's own home.
    set(OMDRCCTRL_AUDIO_HOME "$ENV{HOME}" CACHE PATH "Home directory of the audio user (used by commands.conf)")

    if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
        set(OMDRCCTRL_SERVICE_MANAGER "systemd")
    elseif(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
        set(OMDRCCTRL_SERVICE_MANAGER "rc.d")
        if(USER_INSTALL)
            message(FATAL_ERROR "USER_INSTALL is only supported on Linux with systemd --user services")
        endif()
    else()
        message(FATAL_ERROR "Unsupported system: ${CMAKE_SYSTEM_NAME}. Supported systems: Linux, FreeBSD")
    endif()

    if(USER_INSTALL)
        set(CMAKE_INSTALL_PREFIX "$ENV{HOME}/.local" CACHE PATH "Installation prefix" FORCE)
    endif()

    find_program(PYTHON3 python3 REQUIRED)
else()
    # Subproject of the superproject: inherit host values from the top level.
    set(USER_INSTALL OFF)
    set(OMDRCCTRL_SERVICE_USER    "${AUDIO_USER}")
    set(OMDRC_REPO_DIR            "${REPO_DIR}")
    set(OMDRCCTRL_AUDIO_HOME      "${AUDIO_HOME}")
    set(OMDRCCTRL_SERVICE_MANAGER "${OMDRC_SERVICE_MANAGER}")
    set(_OMDRC_SUPERPROJECT TRUE)   # packaged install: point commands.conf at the wrappers
endif()

if(NOT EXISTS "${OMDRC_REPO_DIR}/drc.sh")
    message(WARNING "OMDRC_REPO_DIR does not contain drc.sh: ${OMDRC_REPO_DIR}")
endif()

# Which drc.sh the panel's buttons call, for a STANDALONE build (the
# superproject always uses its own wrappers).  This is not cosmetic: the two
# copies are the same script, but drc.sh decides run-from-repo vs installed mode
# by whether a config.env sits next to it, and that decision picks BOTH the site
# data (configs/<geometry>/, filters/) and the state directory.  Pointed at the
# checkout on a box that has the packaged core installed, the panel looks for
# filter sets inside the checkout -- which ships only "flat" -- so every real
# geometry disappears from the UI, and its state lands in the checkout instead
# of the shared OMDRC_STATE_DIR that the boot path and the desktop use.
# AUTO therefore prefers the installed wrappers whenever they exist, which is
# exactly the case of rebuilding just the panel from omdrc-ctrl/build.
if(NOT _OMDRC_SUPERPROJECT)
    set(OMDRC_PANEL_DRC "AUTO" CACHE STRING
        "drc.sh the panel calls: AUTO (installed wrappers if present, else the checkout), INSTALLED, REPO")
    if(OMDRC_PANEL_DRC STREQUAL "INSTALLED" OR
       (OMDRC_PANEL_DRC STREQUAL "AUTO" AND EXISTS "${CMAKE_INSTALL_PREFIX}/bin/omdrc"))
        set(_OMDRC_USE_INSTALLED TRUE)
        message(STATUS "commands.conf: using the installed wrappers "
                       "(${CMAKE_INSTALL_PREFIX}/bin/omdrc); "
                       "set -DOMDRC_PANEL_DRC=REPO to call the checkout instead")
    else()
        message(STATUS "commands.conf: using the checkout (${OMDRC_REPO_DIR}/drc.sh); "
                       "site data and state come from the checkout, not ${CMAKE_INSTALL_PREFIX}/etc/open-media-drc")
    endif()
endif()

execute_process(
    COMMAND ${PYTHON3} -c "import flask"
    RESULT_VARIABLE _flask_rc
    OUTPUT_QUIET ERROR_QUIET
)
if(NOT _flask_rc EQUAL 0)
    message(WARNING "Flask is not installed. Run:  pip install flask")
endif()

# ── configure files ────────────────────────────────────────────────────────
configure_file(
    src/omdrcctrl.sh.in
    ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl
    @ONLY
)

# commands.conf: the panel's buttons shell out to drc.sh / drc-status.sh.
#   superproject (packaged):    the installed omdrc / omdrc-status wrappers
#                               (core-drc installs them in ${PREFIX}/bin).
#   standalone:                 those same wrappers when they exist, else the
#                               checkout -- see OMDRC_PANEL_DRC above.
message(STATUS "Rendering commands.conf from src/commands.conf.in")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/src/commands.conf.in" _cc)
if(_OMDRC_SUPERPROJECT OR _OMDRC_USE_INSTALLED)
    string(REPLACE "@OMDRC_REPO_DIR@/drc-status.sh" "${CMAKE_INSTALL_PREFIX}/bin/omdrc-status" _cc "${_cc}")
    string(REPLACE "@OMDRC_REPO_DIR@/drc.sh"        "${CMAKE_INSTALL_PREFIX}/bin/omdrc"        _cc "${_cc}")
endif()
string(REPLACE "@OMDRC_REPO_DIR@" "${OMDRC_REPO_DIR}" _cc "${_cc}")
if(NOT OMDRCCTRL_AUDIO_HOME)
    set(OMDRCCTRL_AUDIO_HOME "/home/${OMDRCCTRL_SERVICE_USER}")
endif()
string(REPLACE "@AUDIO_HOME@" "${OMDRCCTRL_AUDIO_HOME}" _cc "${_cc}")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/commands.conf" "${_cc}")

if(OMDRCCTRL_SERVICE_MANAGER STREQUAL "systemd")
    if(USER_INSTALL)
        configure_file(
            systemd/omdrcctrl-user.service.in
            ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.service
            @ONLY
        )
    else()
        configure_file(
            systemd/omdrcctrl.service.in
            ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.service
            @ONLY
        )
    endif()
elseif(OMDRCCTRL_SERVICE_MANAGER STREQUAL "rc.d")
    configure_file(
        rc.d/omdrcctrl.in
        ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.rc
        @ONLY
    )
endif()

# ── install targets ────────────────────────────────────────────────────────

# Python application + docs + helper scripts
install(FILES src/app.py README.md SPECTRUM_ANALYZER.md
        DESTINATION lib/omdrcctrl)

# Jinja2 templates
install(DIRECTORY src/templates
        DESTINATION lib/omdrcctrl)

# Static assets (vendored Chart.js for the filter-response page)
install(DIRECTORY src/static
        DESTINATION lib/omdrcctrl)

# Default config
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/commands.conf
        DESTINATION etc/omdrcctrl
        RENAME commands.conf)

# Launcher script
install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl
        DESTINATION bin)

# service integration
if(OMDRCCTRL_SERVICE_MANAGER STREQUAL "systemd")
    if(USER_INSTALL)
        install(FILES ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.service
                DESTINATION share/systemd/user)
    else()
        install(FILES ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.service
                DESTINATION lib/systemd/system)
    endif()
elseif(OMDRCCTRL_SERVICE_MANAGER STREQUAL "rc.d")
    install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.rc
            DESTINATION etc/rc.d
            RENAME omdrcctrl)
endif()

# ── post-install hints ─────────────────────────────────────────────────────
if(OMDRCCTRL_SERVICE_MANAGER STREQUAL "systemd" AND USER_INSTALL)
    install(CODE "
message(STATUS \"\")
message(STATUS \"omdrcctrl installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"\")
message(STATUS \"Edit commands:  ${CMAKE_INSTALL_PREFIX}/etc/omdrcctrl/commands.conf\")
message(STATUS \"\")
message(STATUS \"Enable service (no sudo required):\")
message(STATUS \"  systemctl --user daemon-reload\")
message(STATUS \"  systemctl --user enable --now omdrcctrl\")
message(STATUS \"\")
message(STATUS \"Open in browser: http://<hostname>:9090\")
")
elseif(OMDRCCTRL_SERVICE_MANAGER STREQUAL "systemd")
    install(CODE "
message(STATUS \"\")
message(STATUS \"omdrcctrl installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"\")
message(STATUS \"Edit commands:  ${CMAKE_INSTALL_PREFIX}/etc/omdrcctrl/commands.conf\")
message(STATUS \"\")
message(STATUS \"Enable service:\")
message(STATUS \"  sudo systemctl daemon-reload\")
message(STATUS \"  sudo systemctl enable --now omdrcctrl\")
message(STATUS \"\")
message(STATUS \"Renderer switching drives the qobuzconnect2mpd / upmpdcli\")
message(STATUS \"'systemctl --user' services, which needs /run/user/${OMDRCCTRL_SERVICE_USER}\")
message(STATUS \"to exist. If ${OMDRCCTRL_SERVICE_USER} is not always logged in, enable\")
message(STATUS \"lingering so the runtime dir (and thus the toggle) survives logout:\")
message(STATUS \"  sudo loginctl enable-linger ${OMDRCCTRL_SERVICE_USER}\")
message(STATUS \"\")
message(STATUS \"Open in browser: http://<hostname>:9090\")
")
elseif(OMDRCCTRL_SERVICE_MANAGER STREQUAL "rc.d")
    install(CODE "
message(STATUS \"\")
message(STATUS \"omdrcctrl installed to ${CMAKE_INSTALL_PREFIX}\")
message(STATUS \"\")
message(STATUS \"Edit commands:  ${CMAKE_INSTALL_PREFIX}/etc/omdrcctrl/commands.conf\")
message(STATUS \"\")
message(STATUS \"Enable service:\")
message(STATUS \"  sudo sysrc omdrcctrl_enable=YES\")
message(STATUS \"  sudo service omdrcctrl start\")
message(STATUS \"\")
message(STATUS \"Optional service settings:\")
message(STATUS \"  sudo sysrc omdrcctrl_user=${OMDRCCTRL_SERVICE_USER}\")
message(STATUS \"  sudo sysrc omdrcctrl_env=DISPLAY=:0\")
message(STATUS \"\")
message(STATUS \"Open in browser: http://<hostname>:9090\")
")
endif()
