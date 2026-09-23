# open-media-drc

**open-media-drc** is a Linux and FreeBSD playback stack for a headless music
and video appliance with digital room correction (DRC). It routes MPD,
UPnP/OpenHome streaming, and (where configured) CD/S/PDIF and video audio
through BruteFIR FIR filters to a USB DAC. DRC can be switched off at any time,
returning to the direct, bit-perfect DAC path.

The project combines the DRC lifecycle manager, per-room filter/configuration
data, MPD and renderer integration, USB-DAC hotplug handling, and LAN control
panels. Linux (Arch is the reference) uses ALSA and `snd-aloop`; FreeBSD
(15.1 is the reference) uses OSS and `virtual_oss`.

For the complete administrator and user guide, including hardware-specific
setup, troubleshooting, verification, and filter-design workflow, read the
[manual](doc/open-media-drc-manual.pdf) or its
[Markdown source](doc/pdf/open-media-drc-manual.md).

## Quick install

This is an appliance-oriented project: configure it on the audio host and use
the supplied `host.cmake` to describe that host. The build installs to
`/usr/local` by default.

1. Install the prerequisites for your platform: a C/C++ compiler, CMake,
   Meson, Ninja, pkg-config, git, MPD, FFTW3 (single and double precision),
   and the Python dependencies for the controller (`flask`, `markdown`, and
   optionally `numpy`). Linux additionally needs ALSA and `snd-aloop`;
   FreeBSD needs `virtual_oss` and `cuse`. The exact package names and the
   UPnP dependency list are in the [manual installation section](doc/pdf/open-media-drc-manual.md#installation).

2. Build the external playback components in signal order: `libnpupnp`,
   `libupnpp`, `upmpdcli`, then the
   [delleceste BruteFIR fork](https://github.com/delleceste/brutefir).
   MPD itself is normally installed from the operating-system package. The
   commands and required upmpdcli metadata patch are documented in the
   [manual](doc/pdf/open-media-drc-manual.md#build-and-install-order).

3. Clone and configure this repository. Start with a fresh build directory;
   `host.cmake` is an initial CMake cache, so it must be supplied on the first
   configure.

   ```sh
   git clone --recursive https://github.com/delleceste/open-media-drc
   cd open-media-drc
   cp host.cmake.sample host.cmake
   $EDITOR host.cmake
   cmake -S . -B build -C host.cmake
   cmake --build build
   sudo cmake --install build
   cmake --build build --target user-install  # run as AUDIO_USER, not root
   ```

   Set at least `AUDIO_USER`, `AUDIO_HOME`, `GEOMETRY`, and the media paths in
   `host.cmake`. The sample defaults to the included `flat` identity filter;
   a real room-correction set can live in a separate repository selected by
   `OMDRC_SITE_DATA_DIRS`.

4. Apply the final platform-specific checklist printed by the installer. On
   Linux this includes copying the udev rule to `/etc/udev/rules.d/`, reloading
   udev, and enabling `mpd`, `omdrcctrl`, and `omdrc-renderer`. On FreeBSD it
   includes enabling the installed rc.d services and loading `cuse`. Keep
   early-boot files as copies in system paths, not symlinks into a home
   directory. See [Linux specifics](doc/pdf/open-media-drc-manual.md#linux-specifics)
   and [FreeBSD installation](doc/pdf/open-media-drc-manual.md#freebsd-installation-and-lifecycle).

## Everyday operation

`drc.sh` is the single entry point for the audio chain. It brings up the
loopback and BruteFIR together, selects the appropriate MPD output, persists
the requested state, and rolls back to direct output if a start fails.

```sh
omdrc 192000       # native-rate DRC at 192 kHz
omdrc resamp       # DRC with MPD/soxr resampling to 192 kHz
omdrc off          # direct DAC output; records DRC as off
omdrc-status       # observed, live state of the chain
```

Use a native rate only when it matches the source. `resamp` is the convenient
choice for mixed-rate playlists. `omdrc restore` reapplies the saved intent,
and `omdrc cdin` selects the 44.1 kHz CD/S/PDIF path where that source is
configured. The full command reference is in the
[usage chapter](doc/pdf/open-media-drc-manual.md#usage-drcsh-filters-and-configuration).

## What is installed

The core engine is `drc.sh`; `configs/<geometry>/` selects a BruteFIR setup
and `filters/<geometry>/<rate>/` holds its `FLOAT64_LE` FIR coefficients.
`host.cmake` renders the MPD, renderer, service, hotplug, and controller
configuration around those data. Site data is deliberately separate from
the engine: use `OMDRC_SITE_DATA_DIRS` to keep room measurements and filter
history in their own checkout.

The `omdrcctrl` web panel listens on port 9090 by default and provides DRC
controls, health/rate monitoring, filter-response charts, a spectrum display,
and bit-perfect checks. It executes configured shell commands as the audio
user, so expose it only on a trusted LAN. `video/` supplies mpv launchers and
a companion web remote; `browser-nodrc/` provides a separate browser path
when browser audio must bypass DRC.

## Verification and updates

The repository includes tools for proving direct-path and native-rate
bit-perfect operation, plus glitch detection and filter provenance checks.
Treat a filter design as data with a recorded origin and headroom, rather than
as an interchangeable pair of raw files; see
[filter provenance and verification](doc/FILTER_PROVENANCE_AND_RESPONSE.md).

To update an installed host, pull the repository, rebuild, reinstall, and
repeat any installer checklist items that changed:

```sh
git pull --ff-only
cmake --build build
sudo cmake --install build
```

Do not edit generated files under the install prefix: change `host.cmake` or
the tracked source, reconfigure when host values change, then reinstall.
