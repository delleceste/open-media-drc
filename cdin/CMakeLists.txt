cmake_minimum_required(VERSION 3.16)

# omdrc-cdin — S/PDIF capture bridge into the DRC chain.
#
# Builds standalone (cmake cdin) or as a subproject of the open-media-drc
# superproject, which supplies AUDIO_USER / REPO_DIR / OMDRC_SERVICE_MANAGER.
#
# FreeBSD only: the daemon talks OSS directly.  On Linux the same job is done
# by alsaloop(1) from alsa-utils, which already ships the control loop.

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(omdrc-cdin VERSION 0.1.0 LANGUAGES C)
    # Standalone: the superproject would have supplied this.  It is the user
    # the rc.d service drops to — the same one that owns brutefir and MPD,
    # because they all take turns on the same devices.
    set(AUDIO_USER "$ENV{USER}" CACHE STRING "Login user the service runs as")
    if(NOT AUDIO_USER)
        set(AUDIO_USER "nobody")
    endif()
else()
    enable_language(C)
endif()

if(NOT CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
    message(WARNING "omdrc-cdin targets FreeBSD/OSS; on Linux use alsaloop(1) instead. Skipping.")
    return()
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

add_executable(omdrc-cdin
    src/main.c
    src/convert.c
    src/filesrc.c
    src/gate.c
    src/log.c
    src/ossdev.c
    src/ring.c
)

target_compile_options(omdrc-cdin PRIVATE
    -Wall -Wextra -Wshadow -Wpointer-arith -Wcast-qual -Wstrict-prototypes
)

find_package(Threads REQUIRED)
target_link_libraries(omdrc-cdin PRIVATE Threads::Threads m)

install(TARGETS omdrc-cdin RUNTIME DESTINATION bin)
install(FILES README.md AUDIT-FIXES.md DESTINATION share/doc/omdrc-cdin)

# ── service integration ────────────────────────────────────────────────────
# FreeBSD only, so rc.d is the only service manager there is here.  The daemon
# is meant to run continuously: it releases the output device after a run of
# digital silence, so leaving it up costs MPD and mpv nothing.  Enable with
# omdrc_cdin_enable="YES" in /etc/rc.conf.
configure_file(rc.d/omdrc_cdin.in ${CMAKE_CURRENT_BINARY_DIR}/omdrc_cdin.rc @ONLY)
install(PROGRAMS ${CMAKE_CURRENT_BINARY_DIR}/omdrc_cdin.rc
        DESTINATION etc/rc.d
        RENAME omdrc_cdin)

# ── tests ──────────────────────────────────────────────────────────────────
# ctest --test-dir build
include(CTest)
if(BUILD_TESTING)
    add_executable(test_ring tests/test_ring.c src/ring.c)
    target_include_directories(test_ring PRIVATE src)
    target_link_libraries(test_ring PRIVATE Threads::Threads)
    add_test(NAME ring COMMAND test_ring)

    add_executable(test_convert tests/test_convert.c src/convert.c)
    target_include_directories(test_convert PRIVATE src)
    add_test(NAME convert COMMAND test_convert)

    add_executable(test_gate tests/test_gate.c src/gate.c)
    target_include_directories(test_gate PRIVATE src)
    add_test(NAME gate COMMAND test_gate)

    add_executable(test_ossdev tests/test_ossdev.c src/ossdev.c src/log.c)
    target_include_directories(test_ossdev PRIVATE src)
    target_link_libraries(test_ossdev PRIVATE Threads::Threads)
    add_test(NAME ossdev COMMAND test_ossdev)
endif()
