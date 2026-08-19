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
    src/log.c
    src/ossdev.c
    src/ring.c
)

target_compile_options(omdrc-cdin PRIVATE
    -Wall -Wextra -Wshadow -Wpointer-arith -Wcast-qual -Wstrict-prototypes
)

find_package(Threads REQUIRED)
target_link_libraries(omdrc-cdin PRIVATE Threads::Threads)

install(TARGETS omdrc-cdin RUNTIME DESTINATION bin)
install(FILES README.md DESTINATION share/doc/omdrc-cdin)

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
endif()
