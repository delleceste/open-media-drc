cmake_minimum_required(VERSION 3.16)

# omdrcctrl — the web control panel (:9090).
#
# Installed only as part of the top-level open-media-drc project, which owns
# the host configuration and supplies AUDIO_USER / AUDIO_HOME /
# OMDRC_SERVICE_MANAGER / PYTHON3.  There is deliberately no second,
# checkout-backed installation mode: an installed panel always shares the
# installed core's site data and state.

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(omdrcctrl VERSION 1.0.0 LANGUAGES NONE)
    get_filename_component(_OMDRC_TOP_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
    message(FATAL_ERROR
        "omdrcctrl has no standalone installation mode. A standalone install "
        "would split the panel from the core's site data and state.\n\n"
        "Configure and install open-media-drc from the repository root instead:\n"
        "  cmake -S ${_OMDRC_TOP_SOURCE} -B ${_OMDRC_TOP_SOURCE}/build -C ${_OMDRC_TOP_SOURCE}/host.cmake\n"
        "  cmake --build ${_OMDRC_TOP_SOURCE}/build\n"
        "  sudo cmake --install ${_OMDRC_TOP_SOURCE}/build\n\n"
        "For development, configure the top-level build without installing, then "
        "run src/app.py with build/omdrc-ctrl/commands.conf.")
endif()

# Inherit the single host/runtime model from the top-level project.
set(OMDRCCTRL_SERVICE_USER    "${AUDIO_USER}")
set(OMDRCCTRL_AUDIO_HOME      "${AUDIO_HOME}")
set(OMDRCCTRL_SERVICE_MANAGER "${OMDRC_SERVICE_MANAGER}")

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

# commands.conf: the panel's buttons always use the installed omdrc /
# omdrc-status wrappers supplied by core-drc in the same top-level build.
message(STATUS "Rendering commands.conf from src/commands.conf.in")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/src/commands.conf.in" _cc)
string(REPLACE "@OMDRC_DRC_STATUS@" "${CMAKE_INSTALL_PREFIX}/bin/omdrc-status" _cc "${_cc}")
string(REPLACE "@OMDRC_DRC@"        "${CMAKE_INSTALL_PREFIX}/bin/omdrc"        _cc "${_cc}")
set(_OMDRC_CONFIG_SITE_ROOT "${CMAKE_INSTALL_PREFIX}/etc/open-media-drc")
set(_OMDRC_CONFIG_DESIGN_ROOT "${OMDRC_FILTER_DESIGN_ROOT}")
set(_OMDRC_FILTER_TOOLS_ROOT "${CMAKE_INSTALL_PREFIX}/share/omdrc/filter-tools")
set(_OMDRC_CONFIG_HELPER "${CMAKE_INSTALL_PREFIX}/libexec/omdrc/omdrc-config-helper")
set(_OMDRC_CONFIG_STATE_ROOT "${OMDRCCTRL_AUDIO_HOME}/.local/state/omdrc/configuration")
# The tap suite ships beside the other privileged-adjacent scripts; its
# artifacts are large, so they land in /var/tmp rather than the site tree.
set(_OMDRC_BP_TOOLS_ROOT "${CMAKE_INSTALL_PREFIX}/libexec/omdrc/scripts")
set(_OMDRC_BP_GENERATOR "${CMAKE_INSTALL_PREFIX}/libexec/omdrc/scripts/gen-bitperfect-wav.py")
set(_OMDRC_BP_STATE_ROOT "${OMDRCCTRL_AUDIO_HOME}/.local/state/omdrc/bitperfect")
set(_OMDRC_BP_RESULTS_ROOT "/var/tmp/omdrc-bitperfect/results")
set(_OMDRC_BP_ASSETS_ROOT "/var/tmp/omdrc-bitperfect/assets")
string(REPLACE "@OMDRC_CONFIG_SITE_ROOT@" "${_OMDRC_CONFIG_SITE_ROOT}" _cc "${_cc}")
string(REPLACE "@OMDRC_CONFIG_DESIGN_ROOT@" "${_OMDRC_CONFIG_DESIGN_ROOT}" _cc "${_cc}")
string(REPLACE "@OMDRC_FILTER_TOOLS_ROOT@" "${_OMDRC_FILTER_TOOLS_ROOT}" _cc "${_cc}")
string(REPLACE "@OMDRC_CONFIG_HELPER@" "${_OMDRC_CONFIG_HELPER}" _cc "${_cc}")
string(REPLACE "@OMDRC_CONFIG_STATE_ROOT@" "${_OMDRC_CONFIG_STATE_ROOT}" _cc "${_cc}")
string(REPLACE "@OMDRC_BITPERFECT_TOOLS_ROOT@" "${_OMDRC_BP_TOOLS_ROOT}" _cc "${_cc}")
string(REPLACE "@OMDRC_BITPERFECT_GENERATOR@" "${_OMDRC_BP_GENERATOR}" _cc "${_cc}")
string(REPLACE "@OMDRC_BITPERFECT_RESULTS_ROOT@" "${_OMDRC_BP_RESULTS_ROOT}" _cc "${_cc}")
string(REPLACE "@OMDRC_BITPERFECT_ASSETS_ROOT@" "${_OMDRC_BP_ASSETS_ROOT}" _cc "${_cc}")
string(REPLACE "@OMDRC_BITPERFECT_STATE_ROOT@" "${_OMDRC_BP_STATE_ROOT}" _cc "${_cc}")
string(REPLACE "@OMDRC_BITPERFECT_MUSIC_ROOT@" "${OMDRC_MUSIC_DIR}" _cc "${_cc}")
if(NOT OMDRCCTRL_AUDIO_HOME)
    set(OMDRCCTRL_AUDIO_HOME "/home/${OMDRCCTRL_SERVICE_USER}")
endif()
string(REPLACE "@AUDIO_HOME@" "${OMDRCCTRL_AUDIO_HOME}" _cc "${_cc}")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/commands.conf" "${_cc}")

if(OMDRCCTRL_SERVICE_MANAGER STREQUAL "systemd")
    configure_file(
        systemd/omdrcctrl.service.in
        ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.service
        @ONLY
    )
elseif(OMDRCCTRL_SERVICE_MANAGER STREQUAL "rc.d")
    configure_file(
        rc.d/omdrcctrl.in
        ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.rc
        @ONLY
    )
endif()

# ── install targets ────────────────────────────────────────────────────────

# Python application + docs + helper scripts
install(FILES src/app.py src/configuration.py src/bitperfect.py
              README.md SPECTRUM_ANALYZER.md
        DESTINATION lib/omdrcctrl)

# The bit-perfect tap suite.  These must travel together: the tap scripts
# resolve bitperfect-lib.py next to $0, so shipping one without the others
# leaves a /bitperfect page that cannot run anything.
install(PROGRAMS
        ../scripts/bitperfect-lib.py
        ../scripts/bitperfect_runner.py
        ../scripts/bitperfect_material.py
        ../scripts/bitperfect-compare.py
        ../scripts/bitperfect-tap-linux.sh
        ../scripts/bitperfect-tap-freebsd.sh
        ../tests/gen-bitperfect-wav.py
        DESTINATION libexec/omdrc/scripts)

install(FILES
        ../scripts/new_filter_design.py
        ../scripts/deploy_filter.py
        ../scripts/remove_filter_design.py
        ../scripts/verify_filter_bundle.py
        ../scripts/console_ui.py
        ../scripts/filter_workflow_next.py
        DESTINATION share/omdrc/filter-tools)
install(PROGRAMS ../scripts/REW2raw.sh
        DESTINATION share/omdrc/filter-tools)
install(PROGRAMS ../scripts/omdrc-config-helper.py
        DESTINATION libexec/omdrc RENAME omdrc-config-helper)

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
    install(FILES ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.service
            DESTINATION lib/systemd/system)
elseif(OMDRCCTRL_SERVICE_MANAGER STREQUAL "rc.d")
    install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl.rc
            DESTINATION etc/rc.d
            RENAME omdrcctrl)
endif()

# ── post-install hints ─────────────────────────────────────────────────────
if(OMDRCCTRL_SERVICE_MANAGER STREQUAL "systemd")
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
