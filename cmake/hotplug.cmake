# hotplug.cmake — DAC-hotplug + brutefir service glue (extends core-drc).
#
# Linux : a udev rule starts a system oneshot (User=<audiouser>) that runs
#         `omdrc restore` (which starts brutefir) on USB-DAC attach and
#         `omdrc stop` on detach.  The udev rule is the one file that cannot live
#         under $PREFIX (udev scans /etc/udev and /usr/lib/udev only), so it
#         installs to $PREFIX/lib/udev/rules.d with a documented copy into /etc.
# FreeBSD: one omdrc_audio rc service owns card-role links and the DRC lifecycle.
#         A pcm devd rule launches its detached, idempotent reconcile command.
#         Root role resolution completes before su -l enters the audio user's
#         DRC reconciler, so the two bounded locks are never nested.
#
# The root -> user-owned-brutefir seam is handled by User=/su -l (not a --user
# unit); interactive and service runs share state via OMDRC_STATE_DIR pinned in
# omdrc.conf (core-drc).  A --user drc.service used to duplicate this unit's
# `drc.sh restore`; it was never installed and has been deleted.

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

    file(READ "${CMAKE_CURRENT_SOURCE_DIR}/etc/systemd/system/omdrc-audio-roles.service.in" _roles_svc)
    string(REPLACE "@PREFIX@" "${CMAKE_INSTALL_PREFIX}" _roles_svc "${_roles_svc}")
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/omdrc-audio-roles.service" "${_roles_svc}")
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/omdrc-audio-roles.service"
            DESTINATION lib/systemd/system)

    # udev rule — one of the /etc seams (no @VARS@ to render).
    install(FILES 99-usb-audio-drc.rules DESTINATION lib/udev/rules.d)

    # snd-aloop: the Linux equivalent of virtual_oss, and the two files that
    # decide HOW it is created.  Same seam as the udev rule above — modprobe and
    # systemd-modules-load read /etc and /lib (i.e. /usr/lib), never
    # /usr/local/lib — so these install under the prefix and the final checklist
    # carries the /etc copy.  They are not optional detail: without the
    # modprobe.d options line the loopback takes a free-running hrtimer instead
    # of the DAC's clock (a second drift pair that ignore_xrun hides rather than
    # reports) and its card index follows attach order, breaking the hw:1,1 that
    # brutefir_defaults.linux.conf and mpd.conf name.  See the files themselves.
    install(FILES etc/modules-load.d/snd-aloop.conf DESTINATION lib/modules-load.d)
    install(FILES etc/modprobe.d/omdrc-snd-aloop.conf DESTINATION lib/modprobe.d)

    install(CODE "message(STATUS \"hotplug: drc-usb-audio.service + udev rule + snd-aloop module config installed (see the final checklist for the /etc copies)\")")
else()  # FreeBSD rc.d
    file(READ "${CMAKE_CURRENT_SOURCE_DIR}/freebsd/audio/open-media-drc/files/omdrc_audio.in" _s)
    string(REPLACE "%%PREFIX%%" "${CMAKE_INSTALL_PREFIX}" _s "${_s}")
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/omdrc_audio" "${_s}")
    install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/omdrc_audio" DESTINATION etc/rc.d)

    install(FILES etc/devd/omdrc-audio.conf DESTINATION etc/devd)

    # rc.subr permits rc.conf.d/musicpd to be either one file or a directory.
    # Prepare the directory form before installing our independent post-start
    # fragment; the helper preserves a pre-existing file as 00-local.conf.
    configure_file(
        "${CMAKE_CURRENT_SOURCE_DIR}/cmake/install-musicpd-hook.cmake.in"
        "${CMAKE_CURRENT_BINARY_DIR}/install-musicpd-hook.cmake"
        @ONLY)
    install(SCRIPT "${CMAKE_CURRENT_BINARY_DIR}/install-musicpd-hook.cmake")
    install(FILES etc/rc.conf.d/musicpd/omdrc_audio
            DESTINATION etc/rc.conf.d/musicpd)

    install(CODE "message(STATUS \"hotplug: omdrc_audio rc.d + pcm devd rule + musicpd reconcile hook installed (see the final checklist to enable)\")")
endif()
