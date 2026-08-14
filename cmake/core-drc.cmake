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

# ── site data: brutefir configs + filters ─────────────────────────────────────
# GEOMETRY is the default set (it is what omdrc.conf records); GEOMETRIES lists
# any additional sets to ship.  Installing more than one is what makes runtime
# switching possible at all — `drc.sh geometry <name>` and the web remote's
# picker can only offer sets that are present under $SITE_DIR/configs/.
set(_geos ${GEOMETRY} ${GEOMETRIES})
list(REMOVE_DUPLICATES _geos)

foreach(_geo ${_geos})
    if(_geo STREQUAL "flat")
        file(GLOB _flat_confs "${CMAKE_CURRENT_SOURCE_DIR}/configs/flat/brutefir-*.conf")
        install(FILES ${_flat_confs} DESTINATION ${_etc}/configs/flat)
    else()
        # Render each brutefir-*.conf.in so its coeff filenames point at the
        # installed filters ($SITE_DIR/filters/<geo>/<rate>/*.raw): @REPO_DIR@ ->
        # $SITE_DIR.  The rendered files go to a per-set build subdirectory:
        # the basenames (brutefir-192000.conf) repeat across sets.
        file(GLOB _geo_ins CONFIGURE_DEPENDS
             "${CMAKE_CURRENT_SOURCE_DIR}/configs/${_geo}/brutefir-*.conf.in")
        if(NOT _geo_ins)
            message(WARNING "core-drc: no brutefir-*.conf.in under configs/${_geo}")
        endif()
        # Immutable @design configs are deployable only with their matching
        # provenance manifest. Legacy suffixes such as +2dB remain supported,
        # but the web UI deliberately treats them as unverified.
        foreach(_in ${_geo_ins})
            get_filename_component(_design_conf "${_in}" NAME)
            if(_design_conf MATCHES "@(.+)\\.conf\\.in$")
                set(_design_manifest
                    "${CMAKE_CURRENT_SOURCE_DIR}/filters/${_geo}/provenance/${CMAKE_MATCH_1}.json")
                if(NOT EXISTS "${_design_manifest}")
                    message(FATAL_ERROR
                        "core-drc: ${_design_conf} has no matching verified manifest: ${_design_manifest}")
                endif()
            endif()
        endforeach()

        file(GLOB _geo_manifests CONFIGURE_DEPENDS
             "${CMAKE_CURRENT_SOURCE_DIR}/filters/${_geo}/provenance/*.json")
        list(FILTER _geo_manifests EXCLUDE REGEX "\\.source\\.json$")
        if(_geo_manifests)
            execute_process(
                COMMAND "${PYTHON3}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/verify_filter_bundle.py"
                        --require-sources --no-next ${_geo_manifests}
                WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
                RESULT_VARIABLE _verify_result
                OUTPUT_VARIABLE _verify_output
                ERROR_VARIABLE _verify_error)
            if(NOT _verify_result EQUAL 0)
                message(FATAL_ERROR
                    "core-drc: filter bundle verification failed for ${_geo}: ${_verify_error}")
            endif()
            string(STRIP "${_verify_output}" _verify_output)
            message(STATUS "core-drc: ${_verify_output}")
        else()
            message(WARNING
                "core-drc: ${_geo} has no provenance manifest; response plots will be unverified")
        endif()
        foreach(_in ${_geo_ins})
            get_filename_component(_name "${_in}" NAME)
            string(REGEX REPLACE "\\.in$" "" _name "${_name}")      # brutefir-192000.conf
            file(READ "${_in}" _c)
            string(REPLACE "@REPO_DIR@" "${_site}" _c "${_c}")
            file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/configs/${_geo}/${_name}" "${_c}")
            install(FILES "${CMAKE_CURRENT_BINARY_DIR}/configs/${_geo}/${_name}"
                    DESTINATION ${_etc}/configs/${_geo})
        endforeach()

        # Impulse-response filters (skip the rew/ source measurements).
        # Warn rather than fail when a set has configs but no matching filter
        # directory: install(DIRECTORY) on a missing source aborts the whole
        # install, and one incomplete set must not block the others.
        if(IS_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/filters/${_geo}")
            install(DIRECTORY filters/${_geo}/
                    DESTINATION ${_etc}/filters/${_geo}
                    FILES_MATCHING
                    PATTERN "rew" EXCLUDE
                    PATTERN "source" EXCLUDE
                    PATTERN "analysis" EXCLUDE
                    PATTERN "provenance" EXCLUDE
                    PATTERN "*.raw")
            # Runtime trust metadata and precomputed graph data. Development
            # sources and the source-repository recipe stay out of packages.
            if(IS_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/filters/${_geo}/provenance")
                install(DIRECTORY filters/${_geo}/provenance/
                        DESTINATION ${_etc}/filters/${_geo}/provenance
                        FILES_MATCHING
                        PATTERN "*.json"
                        PATTERN "*.source.json" EXCLUDE)
            endif()
            if(IS_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/filters/${_geo}/analysis")
                install(DIRECTORY filters/${_geo}/analysis/
                        DESTINATION ${_etc}/filters/${_geo}/analysis
                        FILES_MATCHING PATTERN "*.json")
            endif()
        else()
            message(WARNING "core-drc: filter set '${_geo}' has configs but no filters/${_geo}/ — "
                            "its brutefir configs will not find their coefficients")
        endif()
    endif()
endforeach()

string(REPLACE ";" " " _geos_msg "${_geos}")
install(CODE "message(STATUS \"core-drc: engine + filter sets [${_geos_msg}] (default ${GEOMETRY}) installed under ${CMAKE_INSTALL_PREFIX}\")")
