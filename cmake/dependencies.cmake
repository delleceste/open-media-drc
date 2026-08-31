# dependencies.cmake — audit the runtime tools the stack shells out to.
#
# These are RUNTIME dependencies: not needed to build or install, so a missing
# one is a warning (you may install it afterwards), never a hard failure.
# python3 is REQUIRED separately in the top level (the launchers bake its path);
# flask / markdown / numpy are checked per web-UI subproject.

# find a runtime tool; STATUS if present, WARNING if a required one is absent,
# STATUS if an OPTIONAL one is absent.  HINTS extends the search path.
function(omdrc_need_tool name)
    cmake_parse_arguments(A "OPTIONAL" "NOTE" "HINTS" ${ARGN})
    string(TOUPPER "${name}" _u)
    find_program(OMDRC_TOOL_${_u} "${name}" HINTS ${A_HINTS})
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
omdrc_need_tool(qobuzconnect2mpd OPTIONAL NOTE ", Qobuz Connect renderer" HINTS "$ENV{HOME}/.local/bin")

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
