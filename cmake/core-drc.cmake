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
#
# Each set is looked up along OMDRC_SITE_DATA_DIRS rather than assumed to sit in
# this checkout, so a private site repository can supply the personal sets while
# the public one still ships `flat`.  The default search path is this checkout
# alone, which is exactly the old behaviour.
set(_geos ${GEOMETRY} ${GEOMETRIES})
list(REMOVE_DUPLICATES _geos)

# Build an install-time inventory from the exact config files selected below.
# The top-level post-install script prints these pre-rendered message() calls at
# the very end of `make install`, after every subdirectory has installed.
set(OMDRC_INSTALLED_GEOMETRIES "")
set(OMDRC_INSTALL_CONFIGURATION_MESSAGES "")
set(OMDRC_INSTALL_PROVENANCE_MESSAGES "")
set(_omdrc_manifest_identity_python [=[
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    manifest = json.load(stream)
release = manifest.get("source", {}).get("release") or {}
values = (
    manifest.get("design_id", manifest.get("variant", "default")),
    release.get("name", ""),
    manifest.get("source", {}).get("repository_head", ""),
    manifest.get("bundle_id", ""),
)
print("\t".join(values))
]=])

# Resolve one filter set to the first search directory that defines it.
function(_omdrc_site_dir_for _geo _out)
    foreach(_candidate ${OMDRC_SITE_DATA_DIRS})
        if(IS_DIRECTORY "${_candidate}/configs/${_geo}")
            set(${_out} "${_candidate}" PARENT_SCOPE)
            return()
        endif()
    endforeach()
    set(${_out} "" PARENT_SCOPE)
endfunction()

# Report the search path and, per set, exactly which directory won.  Where the
# coefficients come from is the one thing that silently changes when the room
# data moves out of this checkout, so it is always printed, never inferred.
message(STATUS "${OMDRC_BOLD}core-drc: filter set search path${OMDRC_RESET}")
foreach(_candidate ${OMDRC_SITE_DATA_DIRS})
    if(_candidate STREQUAL "${CMAKE_CURRENT_SOURCE_DIR}")
        message(STATUS "    ${_candidate} ${OMDRC_DIM}(this checkout)${OMDRC_RESET}")
    else()
        message(STATUS "    ${_candidate}")
    endif()
endforeach()

foreach(_geo ${_geos})
    _omdrc_site_dir_for("${_geo}" _src)
    set(_default_tag "")
    if(_geo STREQUAL "${GEOMETRY}")
        set(_default_tag " ${OMDRC_DIM}(default)${OMDRC_RESET}")
    endif()
    if(NOT _src)
        string(REPLACE ";" "\n      " _search_msg "${OMDRC_SITE_DATA_DIRS}")
        # A missing *extra* set must not block the others, but a missing default
        # yields an install whose omdrc.conf names a geometry that has no configs
        # at all — brutefir would fail at runtime, long after the cause.
        if(_geo STREQUAL "${GEOMETRY}")
            message(FATAL_ERROR
                "core-drc: the DEFAULT filter set GEOMETRY='${_geo}' has no configs/${_geo} "
                "under any of\n      ${_search_msg}\n"
                "  Installing would record GEOMETRY=${_geo} in omdrc.conf with no configs "
                "to match it.  If the set lives in a separate checkout, add it to "
                "OMDRC_SITE_DATA_DIRS.")
        endif()
        message(WARNING
            "core-drc: filter set '${_geo}' has no configs/${_geo} under any of\n"
            "      ${_search_msg}\n"
            "  — skipping it; the other sets are unaffected.")
        message(STATUS "  ${OMDRC_RED}${_geo}${OMDRC_RESET}${_default_tag} "
                       "${OMDRC_DIM}<- not found, skipped${OMDRC_RESET}")
        continue()
    endif()
    message(STATUS "  ${OMDRC_GREEN}${_geo}${OMDRC_RESET}${_default_tag} <- ${_src}")
    list(APPEND OMDRC_INSTALLED_GEOMETRIES "${_geo}")
    if(_geo STREQUAL "flat")
        file(GLOB _flat_confs "${_src}/configs/flat/brutefir-*.conf")
        set(_geo_config_files ${_flat_confs})
        set(_geo_manifests "")
        install(FILES ${_flat_confs} DESTINATION ${_etc}/configs/flat)
    else()
        # Render each brutefir-*.conf.in so its coeff filenames point at the
        # installed filters ($SITE_DIR/filters/<geo>/<rate>/*.raw): @REPO_DIR@ ->
        # $SITE_DIR.  The rendered files go to a per-set build subdirectory:
        # the basenames (brutefir-192000.conf) repeat across sets.
        file(GLOB _geo_ins CONFIGURE_DEPENDS
             "${_src}/configs/${_geo}/brutefir-*.conf.in")
        set(_geo_config_files ${_geo_ins})
        if(NOT _geo_ins)
            message(WARNING "core-drc: no brutefir-*.conf.in under configs/${_geo}")
        endif()
        # Immutable @design configs are deployable only with their matching
        # provenance manifest. Legacy <variant> suffixes remain supported,
        # but the web UI deliberately treats them as unverified.
        foreach(_in ${_geo_ins})
            get_filename_component(_design_conf "${_in}" NAME)
            if(_design_conf MATCHES "@(.+)\\.conf\\.in$")
                set(_design_manifest
                    "${_src}/filters/${_geo}/provenance/${CMAKE_MATCH_1}.json")
                if(NOT EXISTS "${_design_manifest}")
                    message(FATAL_ERROR
                        "core-drc: ${_design_conf} has no matching verified manifest: ${_design_manifest}")
                endif()
            endif()
        endforeach()

        file(GLOB _geo_manifests CONFIGURE_DEPENDS
             "${_src}/filters/${_geo}/provenance/*.json")
        list(FILTER _geo_manifests EXCLUDE REGEX "\\.source\\.json$")
        if(_geo_manifests)
            # --site-root is what makes each manifest's recorded configs/<geo>/…
            # path resolve in the checkout the set actually came from.
            execute_process(
                COMMAND "${PYTHON3}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/verify_filter_bundle.py"
                        --require-sources --no-next --site-root "${_src}" ${_geo_manifests}
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
        if(IS_DIRECTORY "${_src}/filters/${_geo}")
            install(DIRECTORY "${_src}/filters/${_geo}/"
                    DESTINATION ${_etc}/filters/${_geo}
                    FILES_MATCHING
                    PATTERN "rew" EXCLUDE
                    PATTERN "source" EXCLUDE
                    PATTERN "analysis" EXCLUDE
                    PATTERN "provenance" EXCLUDE
                    PATTERN "*.raw")
            # Runtime trust metadata and precomputed graph data. Development
            # sources and the source-repository recipe stay out of packages.
            if(IS_DIRECTORY "${_src}/filters/${_geo}/provenance")
                install(DIRECTORY "${_src}/filters/${_geo}/provenance/"
                        DESTINATION ${_etc}/filters/${_geo}/provenance
                        FILES_MATCHING
                        PATTERN "*.json"
                        PATTERN "*.source.json" EXCLUDE)
            endif()
            if(IS_DIRECTORY "${_src}/filters/${_geo}/analysis")
                install(DIRECTORY "${_src}/filters/${_geo}/analysis/"
                        DESTINATION ${_etc}/filters/${_geo}/analysis
                        FILES_MATCHING PATTERN "*.json")
            endif()
        else()
            message(WARNING "core-drc: filter set '${_geo}' has configs but no filters/${_geo}/ — "
                            "its brutefir configs will not find their coefficients")
        endif()
    endif()

    # Group brutefir-<rate><selector>.conf[.in] files by selector.  "default"
    # is the empty filename suffix, @name is an immutable design, and legacy
    # <variant> suffixes remain visible but deliberately unverified.
    set(_geo_selectors "")
    foreach(_conf ${_geo_config_files})
        get_filename_component(_conf_name "${_conf}" NAME)
        if(_conf_name MATCHES "^brutefir-([0-9]+)(.*)\\.conf(\\.in)?$")
            set(_rate "${CMAKE_MATCH_1}")
            set(_selector "${CMAKE_MATCH_2}")
            if(_selector STREQUAL "")
                set(_selector "default")
            endif()
            string(MD5 _selector_key "${_geo}:${_selector}")
            set(_rates_var "_omdrc_rates_${_selector_key}")
            if(NOT _selector IN_LIST _geo_selectors)
                list(APPEND _geo_selectors "${_selector}")
                set(${_rates_var} "")
            endif()
            list(APPEND ${_rates_var} "${_rate}")
        endif()
    endforeach()

    set(_ordered_selectors "")
    if("default" IN_LIST _geo_selectors)
        list(APPEND _ordered_selectors "default")
    endif()
    foreach(_selector ${_geo_selectors})
        if(NOT _selector STREQUAL "default")
            list(APPEND _ordered_selectors "${_selector}")
        endif()
    endforeach()

    if(_geo STREQUAL "${GEOMETRY}")
        set(_default_note " (default geometry)")
    else()
        set(_default_note "")
    endif()
    string(APPEND OMDRC_INSTALL_CONFIGURATION_MESSAGES
           "message(STATUS \"  ${_geo}${_default_note}\")\n")
    foreach(_selector ${_ordered_selectors})
        string(MD5 _selector_key "${_geo}:${_selector}")
        set(_rates_var "_omdrc_rates_${_selector_key}")
        set(_display_rates "")
        foreach(_standard_rate 44100 48000 88200 96000 192000)
            if("${_standard_rate}" IN_LIST ${_rates_var})
                list(APPEND _display_rates "${_standard_rate}")
            endif()
        endforeach()
        foreach(_rate ${${_rates_var}})
            if(NOT "${_rate}" IN_LIST _display_rates)
                list(APPEND _display_rates "${_rate}")
            endif()
        endforeach()
        string(REPLACE ";" ", " _rates_text "${_display_rates}")

        if(_geo STREQUAL "flat")
            set(_trust "built-in identity")
        elseif(_selector STREQUAL "default")
            set(_selector_manifest "${_src}/filters/${_geo}/provenance/default.json")
            if(EXISTS "${_selector_manifest}")
                set(_trust "verified provenance")
            else()
                set(_trust "unverified")
            endif()
        elseif(_selector MATCHES "^@(.+)$")
            set(_selector_manifest
                "${_src}/filters/${_geo}/provenance/${CMAKE_MATCH_1}.json")
            if(EXISTS "${_selector_manifest}")
                set(_trust "verified provenance")
            else()
                set(_trust "unverified")
            endif()
        else()
            set(_trust "unverified legacy selector")
        endif()
        string(APPEND OMDRC_INSTALL_CONFIGURATION_MESSAGES
               "message(STATUS \"    ${_selector} [${_trust}]: ${_rates_text} Hz\")\n")
    endforeach()

    # Copy the identity fields users see at the end of new_filter_design.py into
    # the final CMake install output.  Only manifests whose selectors are
    # actually installed are listed.
    foreach(_manifest ${_geo_manifests})
        execute_process(
            COMMAND "${PYTHON3}" -c "${_omdrc_manifest_identity_python}" "${_manifest}"
            RESULT_VARIABLE _identity_result
            OUTPUT_VARIABLE _identity_output
            ERROR_VARIABLE _identity_error
            OUTPUT_STRIP_TRAILING_WHITESPACE)
        if(NOT _identity_result EQUAL 0)
            message(FATAL_ERROR
                "core-drc: cannot read install identity from ${_manifest}: ${_identity_error}")
        endif()
        string(REPLACE "\t" ";" _identity "${_identity_output}")
        list(LENGTH _identity _identity_length)
        if(NOT _identity_length EQUAL 4)
            message(FATAL_ERROR "core-drc: malformed install identity from ${_manifest}")
        endif()
        list(GET _identity 0 _design_id)
        list(GET _identity 1 _tag)
        list(GET _identity 2 _source_commit)
        list(GET _identity 3 _bundle_id)
        if(_design_id STREQUAL "default")
            set(_runtime_selector "default")
        else()
            set(_runtime_selector "@${_design_id}")
        endif()
        if(_runtime_selector IN_LIST _geo_selectors)
            string(APPEND OMDRC_INSTALL_PROVENANCE_MESSAGES
                   "message(STATUS \"  ${_geo}/${_runtime_selector}\")\n")
            if(_tag STREQUAL "")
                string(APPEND OMDRC_INSTALL_PROVENANCE_MESSAGES
                       "message(STATUS \"    annotated tag: (legacy bundle; none recorded)\")\n")
            else()
                string(APPEND OMDRC_INSTALL_PROVENANCE_MESSAGES
                       "message(STATUS \"    annotated tag: ${_tag}\")\n")
            endif()
            string(APPEND OMDRC_INSTALL_PROVENANCE_MESSAGES
                   "message(STATUS \"    source commit: ${_source_commit}\")\n"
                   "message(STATUS \"    bundle ID: ${_bundle_id}\")\n"
                   "message(STATUS \"    select: ${CMAKE_INSTALL_PREFIX}/bin/omdrc geometry ${_geo}\")\n"
                   "message(STATUS \"            ${CMAKE_INSTALL_PREFIX}/bin/omdrc design ${_runtime_selector}\")\n")
        endif()
    endforeach()
endforeach()

string(REPLACE ";" " " _geos_msg "${OMDRC_INSTALLED_GEOMETRIES}")
install(CODE "message(STATUS \"core-drc: engine + filter sets [${_geos_msg}] (default ${GEOMETRY}) installed under ${CMAKE_INSTALL_PREFIX}\")")
