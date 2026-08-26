# renderers.cmake — MPD + upmpdcli config and service integration.
#
# MPD is a headless system service.  The switchable renderer layer is user scope
# on Linux (upmpdcli and external qobuzconnect2mpd must share the scope driven by
# omdrcctrl/omdrc-renderer) and rc.d on FreeBSD.  Configs are rendered from
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

    # mpd /etc drop-in (second /etc seam): override the distro unit's User=mpd.
    file(READ etc/systemd/system/mpd.service.d/open-media-drc.conf.in _d)
    string(REPLACE "@REPO_DIR@/mpd/mpd.conf" "${_siteetc}/mpd.conf" _d "${_d}")
    _omdrc_common(_d)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/mpd-omdrc-dropin.conf" "${_d}")
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/mpd-omdrc-dropin.conf"
            DESTINATION share/omdrc/mpd.service.d RENAME open-media-drc.conf)

    # upmpdcli user unit.  It must share scope with qobuzconnect2mpd because
    # omdrc-renderer and the web UI switch both through `systemctl --user`.
    file(READ etc/systemd/user/upmpdcli.service.in _s)
    string(REPLACE "@REPO_DIR@/upmpdcli/upmpdcli.conf" "${_siteetc}/upmpdcli.conf" _s "${_s}")
    _omdrc_common(_s)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/upmpdcli.service" "${_s}")
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/upmpdcli.service" DESTINATION lib/systemd/user)

    # omdrc-renderer user unit: enable THIS instead of a renderer, so the box
    # comes back on the renderer it was left on (see the unit's comment).
    file(READ etc/systemd/user/omdrc-renderer.service.in _r)
    string(REPLACE "@REPO_DIR@/scripts/omdrc-renderer" "${_renderer_helper}" _r "${_r}")
    _omdrc_common(_r)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/omdrc-renderer.service" "${_r}")
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/omdrc-renderer.service" DESTINATION lib/systemd/user)

    install(CODE "message(STATUS \"renderers: mpd.conf + user-scope upmpdcli/omdrc-renderer units + mpd /etc drop-in installed (see the final checklist)\")")
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
