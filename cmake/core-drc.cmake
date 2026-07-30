# core-drc — the DRC engine and site data.
#
# The engine files live at the repo root, so this is an include()d module of the
# top-level project (not an add_subdirectory).  It installs drc.sh/drc-status.sh
# behind the omdrc/omdrc-status PATH wrappers, the helper scripts, the box config
# (rendered from host.cmake), and the brutefir configs + impulse-response filters
# for the selected GEOMETRY.
#
# Not covered yet: the DAC-hotplug + brutefir services (udev/devd + rc.d/systemd)
# — that /etc-seam glue is the next core-drc step.

set(_etc  "etc/open-media-drc")
set(_site "${CMAKE_INSTALL_PREFIX}/etc/open-media-drc")   # runtime SITE_DIR

# ── engine + helper scripts + PATH wrappers ──────────────────────────────────
install(PROGRAMS drc.sh drc-status.sh DESTINATION libexec/omdrc)
install(PROGRAMS
        scripts/REW2raw.sh scripts/REW2raw-all-rates.sh
        scripts/verify-bitperfect.sh scripts/headroom_calc.py
        DESTINATION libexec/omdrc/scripts)

file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/omdrc"
     "#!/bin/sh\nexec \"${CMAKE_INSTALL_PREFIX}/libexec/omdrc/drc.sh\" \"$@\"\n")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/omdrc-status"
     "#!/bin/sh\nexec \"${CMAKE_INSTALL_PREFIX}/libexec/omdrc/drc-status.sh\" \"$@\"\n")
install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/omdrc"
                 "${CMAKE_CURRENT_BINARY_DIR}/omdrc-status"
        DESTINATION bin)

# ── box config (rendered from host.cmake; guarded so a reinstall never clobbers
#    a user-edited copy).  The EXISTS test needs DESTDIR; file(INSTALL) adds it. ─
configure_file(omdrc.conf.in "${CMAKE_CURRENT_BINARY_DIR}/omdrc.conf" @ONLY)
install(FILES omdrc.conf.sample DESTINATION ${_etc})

# BruteFIR runtime defaults — deployed per-user (into ~/.config/BruteFIR) by
# `make user-install`, so keep a stable copy under the prefix.
install(FILES brutefir_defaults.conf brutefir_defaults.linux.conf
        DESTINATION share/omdrc)
install(CODE "
  set(_cfg \"\$ENV{DESTDIR}${CMAKE_INSTALL_PREFIX}/etc/open-media-drc/omdrc.conf\")
  if(EXISTS \"\${_cfg}\")
    message(STATUS \"core-drc: keeping existing config \${_cfg}\")
  else()
    file(INSTALL \"${CMAKE_CURRENT_BINARY_DIR}/omdrc.conf\"
         DESTINATION \"${CMAKE_INSTALL_PREFIX}/etc/open-media-drc\")
  endif()
")

# ── site data: brutefir configs + filters for GEOMETRY ────────────────────────
if(GEOMETRY STREQUAL "flat")
    file(GLOB _flat_confs "${CMAKE_CURRENT_SOURCE_DIR}/configs/flat/brutefir-*.conf")
    install(FILES ${_flat_confs} DESTINATION ${_etc}/configs/flat)
else()
    # Render each brutefir-*.conf.in so its coeff filenames point at the installed
    # filters ($SITE_DIR/filters/<GEOMETRY>/<rate>/*.raw): @REPO_DIR@ -> $SITE_DIR.
    file(GLOB _geo_ins "${CMAKE_CURRENT_SOURCE_DIR}/configs/${GEOMETRY}/brutefir-*.conf.in")
    if(NOT _geo_ins)
        message(WARNING "core-drc: no brutefir-*.conf.in under configs/${GEOMETRY}")
    endif()
    foreach(_in ${_geo_ins})
        get_filename_component(_name "${_in}" NAME)
        string(REGEX REPLACE "\\.in$" "" _name "${_name}")      # brutefir-192000.conf
        file(READ "${_in}" _c)
        string(REPLACE "@REPO_DIR@" "${_site}" _c "${_c}")
        file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/${_name}" "${_c}")
        install(FILES "${CMAKE_CURRENT_BINARY_DIR}/${_name}"
                DESTINATION ${_etc}/configs/${GEOMETRY})
    endforeach()

    # Impulse-response filters (skip the rew/ source measurements).
    install(DIRECTORY filters/${GEOMETRY}/
            DESTINATION ${_etc}/filters/${GEOMETRY}
            FILES_MATCHING
            PATTERN "rew" EXCLUDE
            PATTERN "*.raw")
endif()

install(CODE "message(STATUS \"core-drc: engine + GEOMETRY=${GEOMETRY} site data installed under ${CMAKE_INSTALL_PREFIX}\")")
