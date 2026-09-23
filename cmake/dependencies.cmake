# dependencies.cmake — audit the runtime tools the stack shells out to.
#
# These are RUNTIME dependencies: not needed to build or install, so a missing
# one is a warning (you may install it afterwards), never a hard failure.
# python3 is REQUIRED separately in the top level (the launchers bake its path);
# flask / markdown / numpy are checked per web-UI subproject.

# find a runtime tool; STATUS if present, WARNING if a required one is absent,
# STATUS if an OPTIONAL one is absent.
#
# HINTS are searched BEFORE the system PATH and FALLBACK_HINTS after it, which
# is find_program's PATHS.  The distinction decides which copy of a program that
# exists twice on a host wins, so pick deliberately: HINTS for a location that
# should outrank whatever is on PATH, FALLBACK_HINTS for one that is only a last
# resort.
function(omdrc_need_tool name)
    cmake_parse_arguments(A "OPTIONAL" "NOTE" "HINTS;FALLBACK_HINTS" ${ARGN})
    string(TOUPPER "${name}" _u)
    # A cached find_program result is never re-validated, so a tool that moves
    # between configures keeps its old path — upmpdcli going from a /usr/local
    # source build to the distro package in /usr/bin, say.  Anything that bakes
    # the path into a service unit then names a binary that is not there any
    # more, and the service dies at exec.  Drop a cached path that has gone.
    if(OMDRC_TOOL_${_u} AND NOT EXISTS "${OMDRC_TOOL_${_u}}")
        message(STATUS "  ${name}: cached ${OMDRC_TOOL_${_u}} is gone — searching again")
        unset(OMDRC_TOOL_${_u} CACHE)
    endif()
    find_program(OMDRC_TOOL_${_u} "${name}"
                 HINTS ${A_HINTS} PATHS ${A_FALLBACK_HINTS})
    if(OMDRC_TOOL_${_u})
        message(STATUS "  ${name}: ${OMDRC_TOOL_${_u}}")
    elseif(A_OPTIONAL)
        message(STATUS "  ${name}: not found (optional${A_NOTE})")
    else()
        message(WARNING "runtime dependency '${name}' not found on PATH${A_NOTE} — install it before running the stack")
    endif()
endfunction()

message(STATUS "open-media-drc: checking runtime dependencies")
omdrc_need_tool(brutefir  NOTE " (DRC convolution engine)")
omdrc_need_tool(mpc       NOTE " (MPD client; drc.sh and omdrcctrl drive MPD through it)")
omdrc_need_tool(upmpdcli  NOTE " (UPnP/OpenHome renderer)")

# ── can upmpdcli name the release it is playing? ─────────────────────────────
#
# upmpdcli hands MusicPD what the control point sent it -- artist, album,
# title, track -- and drops the rest of the DIDL, so a queue entry names a
# recording but never the issue it came from. The panel's DR versions page
# needs the year and the label to tell a DR8 CD master from the DR13 vinyl of
# the same record, so this project carries a patch that keeps them
# (upmpdcli/patches/). Nothing breaks without it and the stack runs fine, so
# this warns and explains; it never fails the configure.
#
# The test is the DIDL property name `dc:publisher`, which the patch
# introduces and a stock build never mentions: a functional string rather than
# a marker, so a local build of the patch and an upstream release that merges
# it both answer yes. It reads the binary that find_program resolved, which is
# the one the rendered service unit will exec -- but that is a configure-time
# answer about a file, not about whatever is running right now. The panel
# knows the runtime truth: a current song with no Date tag.
option(OMDRC_WARN_UNPATCHED_UPMPDCLI
       "Warn when the upmpdcli found cannot publish release year and label" ON)

function(omdrc_check_upmpdcli_edition_tags)
    if(NOT OMDRC_TOOL_UPMPDCLI OR NOT OMDRC_WARN_UNPATCHED_UPMPDCLI)
        return()
    endif()
    execute_process(
        COMMAND grep -a -c "dc:publisher" "${OMDRC_TOOL_UPMPDCLI}"
        OUTPUT_VARIABLE _hits OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET RESULT_VARIABLE _rc)
    if(NOT _rc EQUAL 0 AND NOT _rc EQUAL 1)
        # No grep, unreadable binary: say nothing rather than guess.
        return()
    endif()
    if(_hits GREATER 0)
        message(STATUS "  upmpdcli: publishes release year and label")
        return()
    endif()

    # Which copy is this? A packaged binary cannot simply be rebuilt over:
    # the next upgrade would put the stock one back.
    set(_origin "a local build")
    if(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
        set(_owner_cmd pkg which -q "${OMDRC_TOOL_UPMPDCLI}")
    elseif(EXISTS "/usr/bin/pacman")
        set(_owner_cmd pacman -Qoq "${OMDRC_TOOL_UPMPDCLI}")
    else()
        set(_owner_cmd dpkg -S "${OMDRC_TOOL_UPMPDCLI}")
    endif()
    execute_process(COMMAND ${_owner_cmd} OUTPUT_VARIABLE _owner
                    OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET
                    RESULT_VARIABLE _owner_rc)
    if(_owner_rc EQUAL 0 AND _owner)
        string(REGEX REPLACE "\n.*" "" _owner "${_owner}")
        set(_origin "installed by the package manager as ${_owner}")
    endif()

    # An existing open-media-drc config is kept on install. When Linux moves
    # from a distro upmpdcli to a /usr/local source build, its pkgdatadir can
    # still point at /usr/share/upmpdcli. The process then starts but cannot
    # load description.xml, so control points never discover the renderer.
    set(_linux_source_migration_note "")
    if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND _owner_rc EQUAL 0 AND _owner)
        string(CONCAT _linux_source_migration_note
"\n  After installing the source build, check the preserved renderer config:"
"\n    ${CMAKE_INSTALL_PREFIX}/etc/open-media-drc/upmpdcli.conf"
"\n  Set pkgdatadir to the source build's data directory (normally"
"\n  /usr/local/share/upmpdcli, not /usr/share/upmpdcli). Confirm it contains"
"\n  description.xml, then restart upmpdcli. A stale path lets the service"
"\n  run while leaving the renderer invisible to UPnP/OpenHome apps.\n")
    endif()

    # message(WARNING) re-wraps its text, which would fold the commands below
    # into a paragraph nobody can paste. So: a one-line warning for the fact,
    # and the procedure as NOTICE, which prints verbatim.
    message(WARNING
        "upmpdcli at ${OMDRC_TOOL_UPMPDCLI} publishes no release year or "
        "label (${_origin}, without this project's patch)")
    message(NOTICE
"\n  MusicPD's queue will carry artist, album, title and track. Those name a"
"\n  recording, not the issue it came from, so the DR versions page can rank"
"\n  every pressing of a record but has little to go on when saying which one"
"\n  you are hearing. Nothing else in the stack is affected."
"\n"
"\n  To fix it (full notes in upmpdcli/patches/README.md):"
"\n"
"\n    ver=1.9.18"
"\n    curl -O https://www.lesbonscomptes.com/upmpdcli/downloads/upmpdcli-$ver.tar.gz"
"\n    tar xf upmpdcli-$ver.tar.gz && cd upmpdcli-$ver"
"\n    patch -p1 < ${CMAKE_SOURCE_DIR}/upmpdcli/patches/0001-carry-date-genre-and-publisher-tags.patch"
"\n    meson setup build && ninja -C build && sudo ninja -C build install"
"\n"
"\n  then restart the renderer and re-run cmake, which resolves the binary"
"\n  again for this check and for the service unit it renders."
"${_linux_source_migration_note}"
"\n  Silence this with -DOMDRC_WARN_UNPATCHED_UPMPDCLI=OFF.\n")
endfunction()

omdrc_check_upmpdcli_edition_tags()
# qobuzconnect2mpd is built and installed separately.  HINTS are searched BEFORE
# the system PATH, so a per-user build left in ~/.local/bin used to win over a
# later system-wide install of the same program — and go on winning long after
# it had been superseded, since nothing re-validates a stale binary that still
# exists.  Look where the system install puts it first, and keep ~/.local/bin
# only as the fallback for a host that has no system-wide copy.
omdrc_need_tool(qobuzconnect2mpd OPTIONAL NOTE ", Qobuz Connect renderer"
                HINTS "${CMAKE_INSTALL_PREFIX}/bin" "/usr/local/bin"
                FALLBACK_HINTS "$ENV{HOME}/.local/bin")

# Video web remote (omdrcvideo) thumbnails / disc info.
omdrc_need_tool(ffmpeg  OPTIONAL NOTE ", omdrcvideo thumbnails")
omdrc_need_tool(ffprobe OPTIONAL NOTE ", omdrcvideo thumbnails")

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    omdrc_need_tool(mpd)
    omdrc_need_tool(pgrep NOTE " (process checks)")
    # The loopback on Linux is the snd-aloop KERNEL MODULE, not a PATH binary;
    # it cannot be probed here — ensure it is loaded (modules-load.d) at runtime.
else()  # FreeBSD
    omdrc_need_tool(musicpd     NOTE " (MPD is packaged as musicpd on FreeBSD)")
    omdrc_need_tool(virtual_oss NOTE " (userland OSS loopback)")
    omdrc_need_tool(pgrep NOTE " (process checks)")

    # The browser ALSA shim (browser-nodrc/lib.sh, see cmake/browser-audio.cmake).
    # These are FILES, not PATH binaries: on FreeBSD "ALSA" is alsa-lib plus its
    # OSS PCM plugin, with no kernel part.  Chromium/Chrome have no OSS backend
    # at all, so without them the No-DRC launchers run the browser silently —
    # a failure that is easy to misread as a DAC problem, hence the check here.
    foreach(_alsa_bit
            "/usr/local/lib/libasound.so.2@audio/alsa-lib@the ALSA API library"
            "/usr/local/share/alsa/alsa.conf@audio/alsa-lib@the base config the shim includes"
            "/usr/local/lib/alsa-lib/libasound_module_pcm_oss.so@audio/alsa-plugins@the OSS PCM plugin — the shim IS this file")
        string(REGEX REPLACE "@.*$" "" _f "${_alsa_bit}")
        string(REGEX REPLACE "^[^@]*@([^@]*)@.*$" "\\1" _pkg "${_alsa_bit}")
        string(REGEX REPLACE "^.*@" "" _why "${_alsa_bit}")
        if(EXISTS "${_f}")
            message(STATUS "  ${_pkg}: ${_f}")
        else()
            message(WARNING
                "browser ALSA shim: ${_f} is missing (${_why}).\n"
                "  Install it:  pkg install ${_pkg}\n"
                "  Without it Chromium/Chrome play SILENTLY through browser-nodrc "
                "(they have no OSS backend); Firefox is unaffected.")
        endif()
    endforeach()
endif()
