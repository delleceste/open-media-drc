# The runtime role helper renders this template whenever /configuration changes
# devices or hotplug reconciles them. CMake never caches a physical card name.
option(OMDRC_INSTALL_BROWSER_ALSA "Install the audio user's Linux ALSA default" ON)
if(NOT OMDRC_INSTALL_BROWSER_ALSA)
    return()
endif()
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/browser-nodrc/asoundrc.linux.conf.in"
        DESTINATION share/open-media-drc)
configure_file("${CMAKE_CURRENT_SOURCE_DIR}/cmake/install-browser-alsa.cmake.in"
               "${CMAKE_CURRENT_BINARY_DIR}/install-browser-alsa.cmake" @ONLY)
install(SCRIPT "${CMAKE_CURRENT_BINARY_DIR}/install-browser-alsa.cmake")
message(STATUS "browser-audio: Linux ALSA defaults will follow the configured audio roles")
