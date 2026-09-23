# renderers.cmake — MPD + upmpdcli config and service integration.
#
# MPD is a headless system service.  On Linux the switchable renderer layer
# (upmpdcli, external qobuzconnect2mpd, and the omdrc-renderer boot restorer)
# are ALSO system-scope units with User=@AUDIO_USER@ — the same shape as
# mpd.service's own /etc drop-in and omdrcctrl.service, not systemd --user.
# A --user unit's network-online.target is silently a no-op (see each unit's
# own header comment for what that broke in practice), and system scope needs
# no user-session bus for omdrcctrl/omdrc-renderer to start or stop them —
# just the scoped sudoers grant documented in omdrc-ctrl/README.md, which
# mirrors the rc.d NOPASSWD grant FreeBSD already needs for the same job.
# FreeBSD drives the renderers with rc.d directly.  Configs are rendered from
# host.cmake and install-guarded.

set(_etc     "etc/open-media-drc")
set(_siteetc "${CMAKE_INSTALL_PREFIX}/etc/open-media-drc")

# The rendered services all write as AUDIO_USER.  Normalize their persistent
# state and any existing /tmp logs during every real host install, including
# migration from qobuzconnect2mpd's standalone dedicated service account.
# Keep this ahead of the config/unit installs so a permission failure aborts
# the install instead of leaving a deceptively successful handoff checklist.
configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/cmake/install-renderer-runtime.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/install-renderer-runtime.cmake"
    @ONLY)
install(PROGRAMS scripts/prepare-renderer-runtime.sh
        DESTINATION libexec/omdrc/scripts)
install(SCRIPT "${CMAKE_CURRENT_BINARY_DIR}/install-renderer-runtime.cmake")

# Absolute path to the upmpdcli binary for the rendered service unit.  Falls
# back to the install prefix when find_program came up empty (upmpdcli may be
# installed after this configure), which is what the unit used to hardcode.
if(OMDRC_TOOL_UPMPDCLI)
    set(_upmpdcli_bin "${OMDRC_TOOL_UPMPDCLI}")
else()
    set(_upmpdcli_bin "${CMAKE_INSTALL_PREFIX}/bin/upmpdcli")
endif()

# Substitute the common host @VARS@ in-place on the named variable.
macro(_omdrc_common var)
    string(REPLACE "@AUDIO_USER@"    "${AUDIO_USER}"           ${var} "${${var}}")
    string(REPLACE "@AUDIO_HOME@"    "${AUDIO_HOME}"           ${var} "${${var}}")
    string(REPLACE "@MUSIC_DIR@"     "${MUSIC_DIR}"            ${var} "${${var}}")
    string(REPLACE "@FRIENDLY_NAME@" "${FRIENDLY_NAME}"        ${var} "${${var}}")
    string(REPLACE "@QOBUZ_USER@"    "${QOBUZ_USER}"           ${var} "${${var}}")
    string(REPLACE "@PREFIX@"        "${CMAKE_INSTALL_PREFIX}" ${var} "${${var}}")
endmacro()

# Guarded config install: never clobber a user-edited copy on reinstall.
function(_omdrc_install_config src destdir)
    get_filename_component(_bn "${src}" NAME)
    install(CODE "
      set(_cfg \"\$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/${destdir}/${_bn}\")
      if(EXISTS \"\${_cfg}\")
        message(STATUS \"renderers: keeping existing \${_cfg}\")
      else()
        file(INSTALL \"${src}\" DESTINATION \"${CMAKE_INSTALL_PREFIX}/${destdir}\")
      endif()
    ")
endfunction()

# radiolist (read-only data referenced by upmpdcli.conf)
install(FILES upmpdcli/radio_scripts/radiolist.conf DESTINATION share/omdrc/upmpdcli)
# The upmpdcli patch, installed next to the radiolist so the procedure the
# dependency check prints names a path that exists on the running host, not
# one inside a checkout that may be long gone.
install(DIRECTORY upmpdcli/patches DESTINATION share/omdrc/upmpdcli)

# Renderer restore helper — starts the renderer recorded in $STATE_DIR/
# last_renderer by omdrcctrl's toggle.  Shared by both boot services below, and
# usable by hand (omdrc-renderer status|set).  Config-free: it resolves the
# state dir itself, the same way drc.sh does.
install(PROGRAMS scripts/omdrc-renderer DESTINATION libexec/omdrc/scripts)
set(_renderer_helper "${CMAKE_INSTALL_PREFIX}/libexec/omdrc/scripts/omdrc-renderer")

# ── upmpdcli.conf (both OSes) ────────────────────────────────────────────────
file(READ upmpdcli/upmpdcli.conf.in _u)
string(REPLACE "@REPO_DIR@/upmpdcli/radio_scripts/radiolist.conf"
               "${CMAKE_INSTALL_PREFIX}/share/omdrc/upmpdcli/radiolist.conf" _u "${_u}")
_omdrc_common(_u)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/upmpdcli.conf" "${_u}")
_omdrc_install_config("${CMAKE_CURRENT_BINARY_DIR}/upmpdcli.conf" "${_etc}")

if(OMDRC_SERVICE_MANAGER STREQUAL "systemd")
    # mpd.conf
    file(READ mpd/mpd.conf.in _m)
    _omdrc_common(_m)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/mpd.conf" "${_m}")
    _omdrc_install_config("${CMAKE_CURRENT_BINARY_DIR}/mpd.conf" "${_etc}")

    # MPD drop-in: install beside the other system units.  /usr/local/lib has
    # higher systemd load-path priority than the distro's /usr/lib, so this
    # overrides User=mpd without a separate copy into /etc.
    file(READ etc/systemd/system/mpd.service.d/open-media-drc.conf.in _d)
    string(REPLACE "@REPO_DIR@/mpd/mpd.conf" "${_siteetc}/mpd.conf" _d "${_d}")
    _omdrc_common(_d)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/mpd-omdrc-dropin.conf" "${_d}")
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/mpd-omdrc-dropin.conf"
            DESTINATION lib/systemd/system/mpd.service.d RENAME open-media-drc.conf)

    # upmpdcli system unit (User=@AUDIO_USER@ — see the template's own header
    # for why this moved off systemd --user).
    set(_upmpdcli_runner "${CMAKE_INSTALL_PREFIX}/libexec/omdrc/run-upmpdcli")
    install(PROGRAMS scripts/run-upmpdcli.sh
            DESTINATION libexec/omdrc RENAME run-upmpdcli)
    file(READ etc/systemd/system/upmpdcli.service.in _s)
    string(REPLACE "@REPO_DIR@/upmpdcli/upmpdcli.conf" "${_siteetc}/upmpdcli.conf" _s "${_s}")
    # ExecStart points at wherever upmpdcli actually is (dependencies.cmake
    # found it), not at this project's prefix: it is packaged by the distro on
    # Arch (/usr/bin) and built into /usr/local/bin from source, and a unit that
    # names the wrong one fails at exec on every start.
    string(REPLACE "@UPMPDCLI_BIN@" "${_upmpdcli_bin}" _s "${_s}")
    string(REPLACE "@UPMPDCLI_RUNNER@" "${_upmpdcli_runner}" _s "${_s}")
    _omdrc_common(_s)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/upmpdcli.service" "${_s}")
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/upmpdcli.service" DESTINATION lib/systemd/system)

    # omdrc-renderer system unit: enable THIS instead of a renderer, so the box
    # comes back on the renderer it was left on (see the unit's comment).
    file(READ etc/systemd/system/omdrc-renderer.service.in _r)
    string(REPLACE "@REPO_DIR@/scripts/omdrc-renderer" "${_renderer_helper}" _r "${_r}")
    _omdrc_common(_r)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/omdrc-renderer.service" "${_r}")
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/omdrc-renderer.service" DESTINATION lib/systemd/system)

    # qobuzconnect2mpd system unit — only if the (optional, separately built)
    # binary was actually found; a unit whose ExecStart cannot resolve is worse
    # than no unit (see browser-audio.cmake's identical reasoning for launchers).
    if(OMDRC_TOOL_QOBUZCONNECT2MPD)
        file(READ etc/systemd/system/qobuzconnect2mpd.service.in _q)
        string(REPLACE "@QOBUZCONNECT2MPD_BIN@" "${OMDRC_TOOL_QOBUZCONNECT2MPD}" _q "${_q}")
        _omdrc_common(_q)
        file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/qobuzconnect2mpd.service" "${_q}")
        install(FILES "${CMAKE_CURRENT_BINARY_DIR}/qobuzconnect2mpd.service"
                DESTINATION lib/systemd/system)
        message(STATUS "  ${OMDRC_GREEN}qobuzconnect2mpd.service${OMDRC_RESET} <- ${OMDRC_TOOL_QOBUZCONNECT2MPD}")
    else()
        message(STATUS "  ${OMDRC_DIM}qobuzconnect2mpd.service <- qobuzconnect2mpd not found, skipped${OMDRC_RESET}")
    endif()

    # sudoers snippet for omdrcctrl's web toggle and omdrc-renderer (both run as
    # AUDIO_USER, both need root to start/stop the two system units above).
    # Staged under the prefix, like the udev rule / mpd drop-in below it — never
    # written into /etc/sudoers.d directly, which is host state a packaged or
    # DESTDIR build must not touch (see the unit's own header comment). Syntax-
    # checked with visudo now so a bad substitution fails the install loudly
    # instead of silently shipping a snippet that would break sudo entirely.
    file(READ etc/sudoers.d/omdrcctrl-renderer.in _sd)
    string(REPLACE "@PREFIX@" "${CMAKE_INSTALL_PREFIX}" _sd "${_sd}")
    _omdrc_common(_sd)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl-renderer.sudoers" "${_sd}")
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/omdrcctrl-renderer.sudoers"
            DESTINATION share/omdrc/sudoers.d
            RENAME omdrcctrl-renderer
            PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ)
    find_program(OMDRC_VISUDO visudo)
    if(OMDRC_VISUDO)
        install(CODE "
          execute_process(
              COMMAND \"${OMDRC_VISUDO}\" -cf \"\$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/share/omdrc/sudoers.d/omdrcctrl-renderer\"
              RESULT_VARIABLE _omdrc_vc OUTPUT_VARIABLE _omdrc_vo ERROR_VARIABLE _omdrc_ve)
          if(NOT _omdrc_vc EQUAL 0)
              message(FATAL_ERROR \"renderers: rendered sudoers snippet failed visudo -c: \${_omdrc_vo}\${_omdrc_ve}\")
          endif()
        ")
    else()
        message(STATUS "  ${OMDRC_DIM}renderers: visudo not found, skipping sudoers syntax check${OMDRC_RESET}")
    endif()

    install(CODE "message(STATUS \"renderers: mpd.conf + system-scope upmpdcli/omdrc-renderer/qobuzconnect2mpd units + MPD drop-in + sudoers snippet installed (see the final checklist)\")")
else()  # FreeBSD
    # musicpd.conf (FreeBSD MPD package == musicpd)
    file(READ mpd/musicpd.conf.in _m)
    _omdrc_common(_m)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/musicpd.conf" "${_m}")
    _omdrc_install_config("${CMAKE_CURRENT_BINARY_DIR}/musicpd.conf" "${_etc}")

    # upmpdcli rc.d
    file(READ etc/rc.d/upmpdcli.in _s)
    string(REPLACE "@REPO_DIR@/upmpdcli/upmpdcli.conf" "${_siteetc}/upmpdcli.conf" _s "${_s}")
    _omdrc_common(_s)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/upmpdcli.rc" "${_s}")
    install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/upmpdcli.rc" DESTINATION etc/rc.d RENAME upmpdcli)

    # omdrc_renderer rc.d: enable THIS instead of upmpdcli/qobuzconnect2mpd, so
    # the box comes back on the renderer it was left on (see the script's
    # comment for the rc.conf lines).
    file(READ etc/rc.d/omdrc_renderer.in _r)
    string(REPLACE "@REPO_DIR@/scripts/omdrc-renderer" "${_renderer_helper}" _r "${_r}")
    _omdrc_common(_r)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/omdrc_renderer.rc" "${_r}")
    install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/omdrc_renderer.rc"
            DESTINATION etc/rc.d RENAME omdrc_renderer)

    install(CODE "message(STATUS \"renderers: musicpd.conf + upmpdcli.conf/rc.d + omdrc_renderer rc.d installed (see the final checklist to enable)\")")
endif()
