# Kodi OSS sink: expose userspace (virtual_oss) devices

Patch for Kodi's FreeBSD OSS audio sink so that **userspace OSS devices created
by `virtual_oss` (cuse) appear in the audio-device list** and can be selected and
persisted — letting Kodi feed the DRC chain (`/dev/dsp.play` → BruteFIR → DAC)
instead of only the raw hardware device (`/dev/dsp0` / `pcm0`).

- Patch: [`patch-xbmc_cores_AudioEngine_Sinks_AESinkOSS.cpp`](patch-xbmc_cores_AudioEngine_Sinks_AESinkOSS.cpp)
- Target: `multimedia/kodi` 22.0a3 (Piers), file
  `xbmc/cores/AudioEngine/Sinks/AESinkOSS.cpp`

## The problem

Kodi's audio settings only ever offered `pcm0` (the hardware DAC, `/dev/dsp0`).
Selecting the virtual_oss playback node (`/dev/dsp.play`, the entry point of the
DRC loopback) was impossible, and hand-editing `guisettings.xml` did **not**
stick — the value reverted to `/dev/dsp0` on every launch.

Two pieces of Kodi source explain this completely:

1. **Enumeration** — `CAESinkOSS::EnumerateDevicesEx()`
   (`AESinkOSS.cpp`) discovers devices by asking the kernel mixer how many sound
   cards exist (`SNDCTL_SYSINFO` → `numcards`) and then probing `/dev/dsp0 …
   /dev/dsp{numcards-1}` with `SNDCTL_CARDINFO`.
   `virtual_oss` devices are **cuse userspace** character devices, *not* kernel
   PCM cards, so they are not counted by `SNDCTL_SYSINFO` and never enumerated.
   Only `/dev/mixer0` exists → `numcards == 1` → only `/dev/dsp0` is listed.

2. **Validation / reset** — `CActiveAESettings::SettingOptionsAudioDevicesFillerGeneral()`
   (`Engines/ActiveAE/ActiveAESettings.cpp`):

   ```cpp
   current = <value from guisettings.xml>;
   EnumerateOutputDevices(sinkList, passthrough);
   for (sink : sinkList)
     if (EqualsNoCase(current, sink->second)) foundValue = true;
   if (!foundValue)
     current = firstDevice;        // <-- silently resets to /dev/dsp0
   ```

   Because the hand-set `/dev/dsp.play` is never in the enumerated list, `foundValue`
   stays false and the setting is overwritten with the first device. This is why
   editing the XML by hand is futile: the fix has to happen at **enumeration**.

The device string format (see `CAEDeviceInfo::ToDeviceString()`) is
`driver:m_deviceName|friendlyName`, e.g. `OSS:/dev/dsp.play|dsp.play virtual_oss device`.

## The fix

After the kernel-card loop, parse the **`Installed devices from userspace:`**
section of `/dev/sndstat` — the only place cuse OSS devices are advertised — and
add each node to the device list. Every node is probed exactly like a kernel
card so the reported formats/channels/rates are real, not assumed:

- open the node `O_WRONLY | O_NONBLOCK` (non-destructive enumeration);
- query `SNDCTL_ENGINEINFO` (`oss_audioinfo`) for `oformats`, `max_channels`,
  `min_rate`, `max_rate`; fall back to `SNDCTL_DSP_GETFMTS` and then to
  conservative S16/stereo defaults if the device is busy or cannot be queried —
  so a busy device is still listed rather than hidden;
- skip any node already advertised as a kernel card (dedup);
- friendly name taken from the `<…>` description in `/dev/sndstat`.

The block is generic: **any** userspace OSS device is listed (no virtual_oss- or
DRC-specific filtering). It lives inside the existing
`#if defined(SNDCTL_SYSINFO) && defined(SNDCTL_CARDINFO)` guard and is a no-op on
systems whose `/dev/sndstat` has no userspace section.

### Result

With the patch and `virtual_oss` running, Settings → System → Audio → *Audio
output device* lists, alongside the hardware DAC:

```
dsp.play virtual_oss device
```

Selecting it stores `OSS:/dev/dsp.play|dsp.play virtual_oss device` and it now
persists. `virtual_oss` (i.e. `drc.sh <rate>`) must be running so `/dev/dsp.play`
exists when Kodi initialises audio.

## Building only Kodi from the patched port

`kodi-22.0.a3` is already installed on this box, so **all library/runtime
dependencies are already satisfied** — there is nothing new to `pkg install` for
a default-options rebuild. The dependency list below is the authoritative
reference (from `multimedia/kodi/Makefile`) in case of a clean machine.

```sh
# 1. Drop the patch into the port's files/ dir (named so `make patch` finds it)
sudo cp kodi-virtual-oss-patch/patch-xbmc_cores_AudioEngine_Sinks_AESinkOSS.cpp \
        /usr/ports/multimedia/kodi/files/

cd /usr/ports/multimedia/kodi

# 2. Keep the currently-installed option set (don't pull new platform deps)
make config        # leave options as they are, or just accept

# 3. Apply patches (extracts source, applies files/patch-* incl. ours)
make patch

# 4. Compile Kodi only (deps already installed -> no dependency builds)
make build         # the slow step; uses all installed deps

# 5. Replace the installed package.  The version is unchanged (22.0.a3), so
#    `make reinstall` alone is a no-op -- deinstall first, then install.
sudo make deinstall
sudo make install
# Verify the live binary was actually replaced (must print 1):
strings /usr/local/lib/kodi/kodi.bin | grep -c "from userspace"

# 6. Clean the work tree when satisfied
make clean
```

`make build` here compiles **only** Kodi. The ports framework will try to build a
missing dependency from source if one is absent; since the box already runs Kodi,
none are missing. To verify nothing is missing before the long build:

```sh
cd /usr/ports/multimedia/kodi
make missing            # lists any dependency packages not yet installed
```

Install anything `make missing` reports with `pkg install <name>` before
building.

### Dependencies (from the Makefile, default options)

Already present because Kodi is installed; listed for a clean machine. Install
the ones reported by `make missing` with `pkg`:

**Build tools** (USES / BUILD_DEPENDS):
`cmake swig flatbuffers pkgconf gmake libtool autoconf automake gettext-tools
desktop-file-utils openjdk8 (java:build) python311 sqlite3 jpeg-turbo libiconv`

**Core libraries** (LIB_DEPENDS):
`libaacs libass ffmpeg libbdplus libcdio libcrossguid curl exiv2 expat2 libfmt
freetype2 fribidi fstrcmp giflib harfbuzz lzo2 pcre2 png spdlog taglib tinyxml
tinyxml2 libudfread libuuid libxml2`

**Runtime** (RUN_DEPENDS): `py311-sqlite3` (match your Python flavor; this box
uses py311)

**Default options' libraries**
(CEC DAV1D DVD GBM GL LCMS2 LIBBLURAY UPNP VAAPI VDPAU WAYLAND WEBSERVER X11
XSLT):
`libcec p8-platform dav1d libdisplay-info libdrm libepoll-shim libinput
libxkbcommon libudev-devd mesa-dri mesa-libs lcms2 libbluray libva libvdpau
waylandpp wayland-protocols libmicrohttpd libX11 libXext libXrandr libxslt`

> The exact pkg name for a port can differ from its directory name. The reliable
> source of truth is `make missing` in the port — it prints only what is actually
> absent, already resolved to installable package names.

## Submitting the patch

The fix is not specific to this setup: it helps **any** FreeBSD Kodi user routing
through `virtual_oss` (or any cuse OSS bridge). Two independent recipients:

### A. FreeBSD port (`multimedia/kodi`) — easiest, highest acceptance

The patch is already in ports `.orig` unified-diff format and drops straight into
`files/`. Submit to the port maintainer (`MAINTAINER` in the Makefile:
`yzrh@noema.org`) via a bug report:

```sh
# Verify it still applies against the current port
cd /usr/ports/multimedia/kodi && make patch     # must apply cleanly

# Build a diff of the port tree (adds the new files/ patch)
cd /usr/ports && git diff multimedia/kodi > /tmp/kodi-oss-virtualoss.diff
# (or `svn diff` on a classic ports checkout)
```

File a PR (problem report) at <https://bugs.freebsd.org/> against
*Ports & Packages → Individual Port(s)*, assign/CC the maintainer, attach the
diff, and summarise: *"multimedia/kodi: enumerate userspace OSS (virtual_oss)
devices in the OSS sink."* Maintainer-timeout rules let it be committed even if
the maintainer is unresponsive.

### B. Kodi upstream (github.com/xbmc/xbmc, GPL-2.0)

Open a PR against `master` modifying
`xbmc/cores/AudioEngine/Sinks/AESinkOSS.cpp`. The diff body in the patch file is
the change (re-base onto current `master`, which may have drifted from 22.0a3).

Suggested commit message:

```
AESinkOSS: enumerate userspace OSS devices (FreeBSD virtual_oss)

The OSS sink enumerates only kernel PCM cards via SNDCTL_SYSINFO, so
cuse-based userspace devices (e.g. virtual_oss) are never listed. Because
the audio-device settings filler resets any value it cannot match against
the enumerated list, such a device cannot be selected or persisted at all.

Parse the "Installed devices from userspace:" section of /dev/sndstat --
the only place these devices are advertised -- and probe each node with
SNDCTL_ENGINEINFO for formats, channels and rate range, mirroring the
kernel-card path. Probing is non-blocking and falls back to safe defaults
so a busy device is still listed.
```

Pre-empt the likely review question — *"why parse /dev/sndstat text rather than
an ioctl?"* — in the PR description: userspace cuse devices are **not reachable**
through the kernel mixer ioctls (`SNDCTL_SYSINFO`/`SNDCTL_AUDIOINFO` only see
kernel PCM cards), so `/dev/sndstat` is the only enumeration source. Each node is
still probed with the proper OSSv4 ioctl once discovered.

## Status / caveats

- **Verified working** on this box against `multimedia/kodi` 22.0a3 (Piers): the
  patch applies cleanly (both hunks), compiles, and at runtime
  `dsp.play virtual_oss device` appears in Settings → System → Audio → *Audio
  output device*. Selecting it is **consistent and persistent** — the value
  survives a Kodi restart instead of reverting to `/dev/dsp0`.
- Every OSSv4 symbol used (`SNDCTL_ENGINEINFO`,
  `oss_audioinfo.{oformats,max_channels,min_rate,max_rate,dev}`) exists in
  `/usr/include/sys/soundcard.h`.
- Install gotcha: the package version is unchanged (still `22.0.a3`), so
  `make reinstall` alone is a no-op — run `make deinstall && make install` to
  actually replace the live binary. Confirm with
  `strings /usr/local/lib/kodi/kodi.bin | grep -c "from userspace"` (must be 1).
- Written against 22.0a3 (Piers); upstream `master` may need a trivial rebase.
