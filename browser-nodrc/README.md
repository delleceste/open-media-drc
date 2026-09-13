# Browser audio on Linux

`make install` installs the ALSA template and adds a managed include to the
configured audio user's ALSA settings. The browser output follows the DAC chosen
on `/configuration`; capture follows the selected input. No card IDs belong in
`host.cmake`, and changing devices requires no CMake rebuild or reinstall.

The same configuration helper that updates MPD and BruteFIR resolves USB
identities to ALSA card IDs and atomically writes
`$PREFIX/etc/open-media-drc/browser-alsa.conf`. It updates that file on device
selection and hotplug reconciliation. Installation seeds it from the saved
selection without restarting audio services. If no selected DAC is attached,
the generated output is unavailable rather than falling back to card zero.
Without a selected capture interface, capture returns silence through ALSA null.

The installer preserves existing settings and appends its include to `~/.asoundrc`
(or `~/.config/alsa/asoundrc` if that later-loaded file exists). It keeps a one-time
`.omdrc-before-browser-alsa` backup. Repeated installs update the managed block
without adding duplicates. A `DESTDIR` package install stages the template without
touching a live home; run a live install on the target to attach the user include.
`OMDRC_INSTALL_BROWSER_ALSA=OFF` skips these installation steps; it does not remove
an include from a previous installation.

Playback uses `plug` over `dmix` at 48 kHz/S32_LE, allowing multiple browser
streams to share the DAC. These settings require a DAC supporting stereo S32_LE
at 48 kHz. MPD and BruteFIR use explicit hardware devices and bypass this mixer.
See the [ALSA plugin documentation](https://www.alsa-project.org/alsa-doc/alsa-lib/pcm_plugins.html).

Stop music playback and use the **No DRC** browser launcher to release the DAC.
`dmix` shares browser streams, but cannot share the DAC with MPD or BruteFIR when
they have opened the raw hardware device. The launchers' sndio handling is
FreeBSD-only; Linux uses ALSA.

Fully restart browsers after changing audio devices or installing these settings:
existing audio streams may retain their old device. Chromium can explicitly
select this PCM with `--alsa-output-device=default` when using its ALSA backend.
For Firefox builds with ALSA support, `media.cubeb.backend=alsa` in `about:config`
selects ALSA if the browser otherwise tries an unavailable sound server.

To verify, play browser audio and inspect `/proc/asound/cards` and
`/proc/asound/<selected-DAC-ID>/pcm0p/sub0/hw_params`. The selected DAC should show
48 kHz stereo playback. Test two playing tabs and repeat after selecting another
DAC on `/configuration`. The generated `browser-alsa.conf` should now name the
new device. Close and reopen playback after USB reconnection as well.
