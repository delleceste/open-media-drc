# console — ANSI colouring for open-media-drc's own configure output.
#
# CMake cannot tell whether its output is a terminal, so this mirrors the
# convention the Python tooling already uses (scripts/filter_workflow_next.py):
# honour NO_COLOR, and treat an unset or "dumb" TERM as "not a terminal".
# Force it either way with -DOMDRC_COLOR=ON/OFF; the value is cached, so a
# reconfigure keeps whatever the first configure decided.
#
# Only this project's own messages are coloured.  CMake's own diagnostics and
# any message(WARNING/FATAL_ERROR) prefixes are left alone.

if(NOT DEFINED OMDRC_COLOR)
    set(_omdrc_color ON)
    if(DEFINED ENV{NO_COLOR} OR "$ENV{TERM}" STREQUAL "" OR "$ENV{TERM}" STREQUAL "dumb")
        set(_omdrc_color OFF)
    endif()
    set(OMDRC_COLOR ${_omdrc_color} CACHE BOOL
        "Colourise open-media-drc's own configure messages")
endif()

if(OMDRC_COLOR)
    string(ASCII 27 _esc)
    set(OMDRC_RESET  "${_esc}[0m")
    set(OMDRC_BOLD   "${_esc}[1m")
    set(OMDRC_DIM    "${_esc}[2m")
    set(OMDRC_RED    "${_esc}[1;31m")
    set(OMDRC_GREEN  "${_esc}[1;32m")
    set(OMDRC_YELLOW "${_esc}[1;33m")
    set(OMDRC_BLUE   "${_esc}[1;34m")
    set(OMDRC_CYAN   "${_esc}[1;36m")
else()
    set(OMDRC_RESET  "")
    set(OMDRC_BOLD   "")
    set(OMDRC_DIM    "")
    set(OMDRC_RED    "")
    set(OMDRC_GREEN  "")
    set(OMDRC_YELLOW "")
    set(OMDRC_BLUE   "")
    set(OMDRC_CYAN   "")
endif()
