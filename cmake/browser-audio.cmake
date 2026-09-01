# browser-audio — keep the browser off the DRC chain, and keep PulseAudio off
# the DAC.  FreeBSD only (see the guard below).
#
# The browser is the one player here that must NOT go through BruteFIR: its
# audio is already lossy streaming material, and the chain adds ~0.67 s of
# latency (AV-SYNC-DELAY.md) for no benefit.  browser-nodrc/*.sh therefore drop
# DRC, hand the DAC to the browser, and restore the exact prior state on exit.
#
# Two host-level obstacles stand in the way of that, and both are handled here
# because both live outside this project's own tree:
#
#  1. Chromium has no OSS backend at all — its FreeBSD backends are PulseAudio,
#     SNDIO and ALSA.  On FreeBSD "ALSA" is not a layer: there is no kernel
#     component, alsa-lib is a userland shim and the only PCM plugin installed
#     is libasound_module_pcm_oss.so, which opens /dev/dspN.  It is visible only
#     to processes that link libasound — Chromium does, MPD/BruteFIR/cdin do
#     not — so the shim cannot leak into the bit-perfect path even in principle.
#     browser-nodrc/lib.sh generates that shim's config per run and exports
#     ALSA_CONFIG_PATH for the browser alone; no global ALSA file is touched.
#
#  2. PulseAudio would fight the chain for the DAC, so it must never run.  With
#     hw.snd.vchans_enable=0 and dev.pcm.<unit>.bitperfect=1 the DAC is
#     single-open, and a pulse server takes hw.snd.default_unit — the DAC.  Two
#     defaults can start one: the package's XDG autostart entry, and
#     `autospawn = yes` in client.conf, under which any libpulse client spawns a
#     server on demand — including Chromium's own backend probe.  Both are
#     disabled below, by files rather than edits: the pulse drop-in is additive,
#     and the autostart entry is masked by a same-named user-level override.
#
# Deleting the package instead would drag out plasma6-plasma-pa,
# plasma6-kinfocenter, qt6-multimedia and speech-dispatcher.

# ── the launchers and their menu entries ─────────────────────────────────────
# Both parts install on every OS; the PulseAudio guard further down is FreeBSD
# only.  The scripts go under libexec beside the engine, and the .desktop entries
# into share/applications — the standard XDG application directory, which is what
# a menu entry is.  Nothing here writes into a home directory, so THE INSTALL
# PREFIX ALONE decides the scope:
#
#   -DCMAKE_INSTALL_PREFIX=/usr/local  + sudo make install -> every user
#   -DCMAKE_INSTALL_PREFIX=$HOME/.local +     make install -> just that user
#
# ~/.local/share/applications is exactly the per-user counterpart of
# /usr/local/share/applications in the XDG data-directory search path, so a
# home-prefix install is a first-class case and needs no separate rule.
set(OMDRC_BROWSER_NODRC_DIR "${CMAKE_INSTALL_PREFIX}/libexec/omdrc/browser-nodrc")

install(FILES    browser-nodrc/lib.sh DESTINATION libexec/omdrc/browser-nodrc)
install(PROGRAMS browser-nodrc/firefox-nodrc.sh
                 browser-nodrc/chromium-nodrc.sh
                 browser-nodrc/chrome-nodrc.sh
        DESTINATION libexec/omdrc/browser-nodrc)

# One menu entry per browser that is actually present.  An entry whose Exec
# cannot resolve is worse than no entry: it shows up in the launcher and fails
# silently when clicked.  The candidate lists mirror browser_resolve() in
# lib.sh — keep them in step.  A browser installed later needs a reconfigure,
# which is what the "not found" line below is telling you.
# "<entry name>@<candidate>|<candidate>…" — the candidates are |-separated, not
# ;-separated: a semicolon IS the CMake list separator, so an inner list would
# flatten into the outer foreach and each candidate would be walked as if it
# were an entry of its own.
set(_omdrc_browsers
    "firefox@firefox|firefox-esr"
    "chromium@chromium|chromium-browser|chrome"
    "chrome@google-chrome|google-chrome-stable")

message(STATUS "${OMDRC_BOLD}browser-audio: No-DRC launcher entries${OMDRC_RESET}")
set(_omdrc_desktop_installed FALSE)
foreach(_entry ${_omdrc_browsers})
    string(REGEX REPLACE "@.*$" "" _name "${_entry}")
    string(REGEX REPLACE "^[^@]*@" "" _candidates "${_entry}")
    string(REPLACE "|" ";" _candidates "${_candidates}")

    set(_found "")
    foreach(_cand ${_candidates})
        find_program(_omdrc_prog_${_cand} NAMES "${_cand}")
        if(_omdrc_prog_${_cand})
            set(_found "${_omdrc_prog_${_cand}}")
            break()
        endif()
    endforeach()

    string(REPLACE ";" ", " _cand_msg "${_candidates}")
    if(NOT _found)
        message(STATUS "  ${OMDRC_DIM}${_name}-nodrc${OMDRC_RESET} "
                       "${OMDRC_DIM}<- none of [${_cand_msg}] on PATH, skipped${OMDRC_RESET}")
        continue()
    endif()
    message(STATUS "  ${OMDRC_GREEN}${_name}-nodrc${OMDRC_RESET} <- ${_found}")

    configure_file(
        "${CMAKE_CURRENT_SOURCE_DIR}/browser-nodrc/${_name}-nodrc.desktop.in"
        "${CMAKE_CURRENT_BINARY_DIR}/browser-nodrc/${_name}-nodrc.desktop"
        @ONLY)
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/browser-nodrc/${_name}-nodrc.desktop"
            DESTINATION share/applications)
    set(_omdrc_desktop_installed TRUE)
endforeach()

# The menu only picks up a new entry once the cache is rebuilt.  Skipped under
# DESTDIR: the staged tree is not a live data directory, and the package manager
# runs this itself.
if(_omdrc_desktop_installed)
    find_program(UPDATE_DESKTOP_DATABASE update-desktop-database)
    if(UPDATE_DESKTOP_DATABASE)
        install(CODE "
          if(\"\$ENV{DESTDIR}\" STREQUAL \"\")
            execute_process(COMMAND \"${UPDATE_DESKTOP_DATABASE}\"
                            \"${CMAKE_INSTALL_PREFIX}/share/applications\"
                            RESULT_VARIABLE _udd ERROR_QUIET)
          endif()
        ")
    endif()
    install(CODE "message(STATUS \"${OMDRC_GREEN}browser-audio${OMDRC_RESET}: No-DRC launcher entries installed to ${CMAKE_INSTALL_PREFIX}/share/applications\")")
endif()

if(NOT CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
    # The PulseAudio guard stops here: Linux has a real ALSA stack (Chromium
    # talks to it natively) and pulse/pipewire is a distro-level concern there.
    return()
endif()

# ── where PulseAudio reads client.conf.d from ────────────────────────────────
# Pulse looks under ITS OWN sysconfdir, which is not necessarily ours.  Probe
# for an installed pulse tree first; fall back to this prefix so the guard is
# pre-armed even on a box where pulse is not (yet) installed.
set(OMDRC_PULSE_ETC_DIR "" CACHE PATH
    "PulseAudio sysconfdir that holds client.conf.d (blank = autodetect)")
if(NOT OMDRC_PULSE_ETC_DIR)
    foreach(_candidate "${CMAKE_INSTALL_PREFIX}/etc/pulse" "/usr/local/etc/pulse" "/etc/pulse")
        if(IS_DIRECTORY "${_candidate}")
            set(OMDRC_PULSE_ETC_DIR "${_candidate}" CACHE PATH "" FORCE)
            break()
        endif()
    endforeach()
endif()
if(NOT OMDRC_PULSE_ETC_DIR)
    set(OMDRC_PULSE_ETC_DIR "${CMAKE_INSTALL_PREFIX}/etc/pulse" CACHE PATH "" FORCE)
    set(_pulse_note " ${OMDRC_DIM}(pulse not installed; guard pre-armed)${OMDRC_RESET}")
else()
    set(_pulse_note "")
endif()

message(STATUS "${OMDRC_BOLD}browser-audio: PulseAudio guard${OMDRC_RESET}")
message(STATUS "  ${OMDRC_GREEN}autospawn = no${OMDRC_RESET} -> ${OMDRC_PULSE_ETC_DIR}/client.conf.d/${_pulse_note}")
message(STATUS "  ${OMDRC_GREEN}Hidden=true${OMDRC_RESET} -> ${AUDIO_HOME}/.config/autostart/pulseaudio.desktop")

install(FILES etc/pulse/client.conf.d/10-omdrc-no-autospawn.conf
        DESTINATION "${OMDRC_PULSE_ETC_DIR}/client.conf.d")

# The autostart mask is per-user by necessity: the system-wide entry is a
# package-owned file, so overwriting it would lose on the next pkg upgrade and
# break `pkg check -s`.  A root install can still place the user-level override
# (it knows AUDIO_HOME/AUDIO_USER), so it lands with `make install` rather than
# waiting for `make user-install`.
configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/cmake/install-pulse-autostart-mask.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/install-pulse-autostart-mask.cmake"
    @ONLY)
install(SCRIPT "${CMAKE_CURRENT_BINARY_DIR}/install-pulse-autostart-mask.cmake")

install(CODE "message(STATUS \"${OMDRC_GREEN}browser-audio${OMDRC_RESET}: PulseAudio autospawn disabled (${OMDRC_PULSE_ETC_DIR}/client.conf.d/10-omdrc-no-autospawn.conf)\")")
