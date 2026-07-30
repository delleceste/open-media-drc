# hotplug.cmake — DAC-hotplug + brutefir service glue (extends core-drc).
#
# Linux : a udev rule starts a system oneshot (User=<audiouser>) that runs
#         `omdrc restore` (which starts brutefir) on USB-DAC attach and
#         `omdrc stop` on detach.  The udev rule is the one file that cannot live
#         under $PREFIX (udev scans /etc/udev and /usr/lib/udev only), so it
#         installs to $PREFIX/lib/udev/rules.d with a documented copy into /etc.
# FreeBSD: a devd rule runs the drc_usb_audio rc.d service, which invokes the
#         brutefir_drc worker; both drop to the audio user via su(1).  devd and
#         rc.d both scan $PREFIX/etc natively, so no /etc seam there.
#
# The root -> user-owned-brutefir seam is handled by User=/su -l (not a --user
# unit); interactive and service runs share state via OMDRC_STATE_DIR pinned in
# omdrc.conf (core-drc).  The redundant --user drc.service is intentionally not
# installed by the packaged build.

if(OMDRC_SERVICE_MANAGER STREQUAL "systemd")
    # DRC hotplug oneshot: @AUDIO_USER@/@AUDIO_HOME@ from host.cmake; the engine
    # is invoked through the installed omdrc wrapper, not the checkout.
    file(READ "${CMAKE_CURRENT_SOURCE_DIR}/etc/systemd/system/drc-usb-audio.service.in" _svc)
    string(REPLACE "@REPO_DIR@/drc.sh" "${CMAKE_INSTALL_PREFIX}/bin/omdrc" _svc "${_svc}")
    string(REPLACE "@AUDIO_USER@" "${AUDIO_USER}" _svc "${_svc}")
    string(REPLACE "@AUDIO_HOME@" "${AUDIO_HOME}" _svc "${_svc}")
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/drc-usb-audio.service" "${_svc}")
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/drc-usb-audio.service"
            DESTINATION lib/systemd/system)

    # udev rule — the one /etc seam (no @VARS@ to render).
    install(FILES 99-usb-audio-drc.rules DESTINATION lib/udev/rules.d)

    install(CODE "
message(STATUS \"\")
message(STATUS \"DAC hotplug (Linux):\")
message(STATUS \"  The DRC oneshot runs as ${AUDIO_USER}; brutefir is started by\")
message(STATUS \"  'omdrc restore' on USB-DAC attach.  udev does not scan \\$PREFIX,\")
message(STATUS \"  so copy the rule into /etc and reload:\")
message(STATUS \"    sudo cp ${CMAKE_INSTALL_PREFIX}/lib/udev/rules.d/99-usb-audio-drc.rules /etc/udev/rules.d/\")
message(STATUS \"    sudo udevadm control --reload\")
message(STATUS \"  Ensure ${AUDIO_USER} is in the 'audio' group for ALSA hw access.\")
")
else()  # FreeBSD rc.d
    foreach(_svc drc_usb_audio brutefir_drc)
        file(READ "${CMAKE_CURRENT_SOURCE_DIR}/freebsd/audio/open-media-drc/files/${_svc}.in" _s)
        string(REPLACE "%%PREFIX%%" "${CMAKE_INSTALL_PREFIX}" _s "${_s}")
        file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/${_svc}" "${_s}")
        install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/${_svc}" DESTINATION etc/rc.d)
    endforeach()

    install(FILES etc/devd/usb-audio-drc.conf DESTINATION etc/devd)

    install(CODE "
message(STATUS \"\")
message(STATUS \"DAC hotplug (FreeBSD):\")
message(STATUS \"  sysrc drc_usb_audio_enable=YES\")
message(STATUS \"  sysrc brutefir_drc_user=${AUDIO_USER}   # REQUIRED: owns brutefir\")
message(STATUS \"  service devd restart                    # pick up the attach/detach rule\")
message(STATUS \"  brutefir escalates virtual_oss via drc.sh's internal sudo;\")
message(STATUS \"  give ${AUDIO_USER} passwordless sudo for that (see docs).\")
")
endif()
