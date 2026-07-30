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
endif()
