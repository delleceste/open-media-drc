# Current configuration (2025.09.23)

## Geometry

- 120cm from front wall
- sofa at blue marks (see notebook for details)

## Filters

v.1.5.0 with two flavours

1. with max +2dB boost, peak correction with inversion, crossover correction (DRC-120.blue/120-blue-with-inversion+2dB.mdat)

2. with no boost, peak correction with inversion, crossover correction (DRC-120.blue/120-blue-with-inversion.mdat)

Revised crossover files (used in rephase): DRC-120.blue/ LR-EP-psy.txt , LR-EP-unsmoothed.txt, X801.rephase, X801.wav


### configuration

LF.0.raw -> 120.blue/FLX+0dB-192k.raw
RF.0.raw -> 120.blue/FRX+0dB-192k.raw
LF.1.raw -> 120.blue/FLX+2dB-192k.raw
RF.1.raw -> 120.blue/FRX+2dB-192k.raw



## Geometry 

- 120cm from front wall
- sofa at P6 (mic at 306cm from loudspeakers 390 from front wall)

## Filters

### v. 1.1.1 2024.11.27

- Low shelf filter has now a slope of -12dB/octave. 1.1.0 version had a (mistakenly?) -6dB/octave slope. The result leads to an improved clarity in the lowest region (< 80Hz).
- Low shelf filter cutoff at 44.50 Hz  Shape Butterworth  Slope 12 dB/oct
- Mid band EQ now has an effect compared to 1.1.0. 10ms windowing applied before EQ-ing. The bump between 300 and 700 is now tamed.

### v. 1.1.0  2024.11.25 and 2024.11.26

#### Files

1. 120cm.VBALS3dB+MF.correction.mdat
2. 2024.11.25-FVBA-LS3dB.mdat

##### Features

- crossover filters linearization (RePhase)
- no additional phase correction
- Virtual bass array filters with delayed impulse (1st mode: 55.15, second 110.3) and +3dB low shelf filter EQ
- corrective EQ until 220Hz
- corrective EQ from 221 to 693Hz (no motivation for 693, it happened, idea was to set upper limit to 700Hz) after windowing L and R to 10ms (343/3.06, being 3.06 approx speaker distance from listening position)



### files:

- 120cm.VBALS3dB+MF.correction.mdat

![Amplitude: current filter vs uncorrected](doc/current.amplitude.png)

![Phase: current filter vs uncorrected](doc/current.phase.png)

![ETC: current impulse response vs uncorrected](doc/current.impulse.png)

![ETC: current filter vs uncorrected](doc/current.etc.png)

![Clarity [C80]: current filter vs uncorrected](doc/current.clarity.c80.png)

# Description

Configuration files, scripts, filters (raw format), ... for brutefir under Linux. 

Designed and generated from one or more of the DRC-xxx github.com/delleceste folders

# Installing the audio chain

The full playback chain, from the UPnP/OpenHome control front-end down to the
speakers:

```
 UPnP / OpenHome control point (phone app, upplay, …)
        │
        ▼
 upmpdcli ──→ libupnpp ──→ libnpupnp      (built from source, this order)
        │
        ▼
 MPD (musicpd)                            (installed from the OS package)
        │  direct  │ DRC
        ▼          ▼
 OKTO DAC      loopback ──→ BruteFIR ──→ OKTO DAC
                (snd-aloop / virtual_oss)   (delleceste fork)
        ▲
        └── open-media-drc (this repo: drc.sh, configs, filters, services)
            + omdrc-ctrl (web control panel, git submodule)
```

## Dependencies

Build tools (all from-source components): a C/C++ compiler, **meson + ninja**
(upmpdcli stack), **cmake** (BruteFIR fork, omdrc-ctrl), **pkg-config**, and git.

| Component | Library / runtime deps | FreeBSD pkg | Arch pacman |
|---|---|---|---|
| **libnpupnp** 6.3.0 | libcurl, libmicrohttpd, expat | `curl libmicrohttpd expat2` | `curl libmicrohttpd expat` |
| **libupnpp** 1.0.4 | libnpupnp, libcurl, expat | (above) | (above) |
| **upmpdcli** 1.9.17 | libupnpp, libcurl, libmicrohttpd, jsoncpp, libmpdclient | `jsoncpp libmpdclient` | `jsoncpp libmpdclient` |
| upmpdcli **Qobuz** plugin | python3 + `requests` | `python3 py311-requests` | `python python-requests` |
| **MPD** | from package; needs **soxr** resampler + ALSA (Linux) / OSS (FreeBSD) output | `musicpd` | `mpd` |
| **BruteFIR** (fork) | FFTW3 single+double (`-lfftw3 -lfftw3f`), ALSA (Linux); OSS built-in (FreeBSD) | `fftw3 fftw3-float` | `fftw alsa-lib` |
| FreeBSD loopback | `virtual_oss` (+ `cuse`) — Linux uses the `snd-aloop` kernel module | `virtual_oss` | (kernel module) |
| **omdrc-ctrl** | python3, flask≥2.3, markdown≥3.5, numpy≥1.21 (optional) | `python3 py311-flask py311-Markdown py311-numpy` | `python python-flask python-markdown python-numpy` |

Common build tools: `meson ninja pkgconf cmake git` (Arch) / `meson ninja
pkgconf cmake git` (FreeBSD).

> upmpdcli, libupnpp and libnpupnp are also available prebuilt (FreeBSD ports
> `upmpdcli`, Arch AUR) — building from source is used here to track upstream.

## Build & install order

**1. upmpdcli stack** (bottom-up; each is a standard meson project):

```sh
for p in libnpupnp-6.3.0 libupnpp-1.0.4 upmpdcli-1.9.17; do
  cd ~/Downloads/$p
  meson setup build --prefix=/usr/local
  ninja -C build
  sudo ninja -C build install
done
sudo ldconfig 2>/dev/null || true   # Linux: refresh the linker cache
```

**2. MPD** — from the OS package (recommended; same as a stock Arch/FreeBSD
install). Make sure the **soxr** resampler and the ALSA (Linux) / OSS (FreeBSD)
outputs are enabled in the package:

```sh
sudo pkg install musicpd      # FreeBSD
sudo pacman -S mpd            # Arch
```

> **⚠️ Linux (Arch) — MPD `User=` drop-in caveat**
>
> The Arch `mpd` package ships a systemd drop-in at
> `/usr/lib/systemd/system/mpd.service.d/00-arch.conf` that sets `User=mpd`.
> Systemd drop-ins always apply **on top of** the main unit file — so a full
> unit override placed at `/etc/systemd/system/mpd.service` cannot override
> that `User=` setting; it will silently lose to the package drop-in.
>
> This repo therefore ships a **counter-drop-in** instead of a full unit
> override: `etc/systemd/system/mpd.service.d/open-media-drc.conf` (generated
> by `install.sh` from `config.env`).  It sets `User=@AUDIO_USER@` and
> the repo config path.  A drop-in in `/etc/systemd/system/` takes precedence
> over one in `/usr/lib/systemd/system/`, so this correctly wins.
>
> The deploy commands printed by `install.sh` handle this — they copy the
> drop-in to `/etc/systemd/system/mpd.service.d/` rather than a full unit
> file.  **Do not** copy or create a full
> `/etc/systemd/system/mpd.service` — it will not help and will only add
> confusion.

**3. BruteFIR** — built from the fork **`github.com/delleceste/brutefir`**
(adds FreeBSD OSS fixes — `bfio_oss` fragment-size fix, `brutefir_loopback`
`-L` loopback fix, passthrough-config default). The classic upstream is
`torger/brutefir`.

```sh
git clone https://github.com/delleceste/brutefir ~/Downloads/brutefir
cd ~/Downloads/brutefir
cmake -B build && cmake --build build         # or: make -f Makefile.dist
sudo cmake --install build                    # installs modules to /usr/local/lib/brutefir
```

**4. open-media-drc (this repo) + omdrc-ctrl**:

```sh
git clone --recursive https://github.com/delleceste/open-media-drc ~/DRC/open-media-drc
cd ~/DRC/open-media-drc
$EDITOR config.env        # set AUDIO_USER, AUDIO_HOME, PREFIX, MUSIC_DIR, QOBUZ_USER
./install.sh              # renders every *.in from config.env; prints the deploy steps
```

`install.sh` generates the live configs/service files (MPD, upmpdcli, BruteFIR,
rc.d / systemd units) from the templates and prints the OS-specific commands to
link them into place. omdrc-ctrl builds with cmake (`cmake -B build && cmake
--build build` from `omdrc-ctrl/`) and renders its own `commands.conf` with
`OMDRC_REPO_DIR` defaulting to this checkout — see `omdrc-ctrl/README.md`.

**5. BruteFIR defaults** — BruteFIR reads its general/I/O defaults (float precision,
partition size, and the ALSA/OSS input+output devices) from
`~/.config/BruteFIR/brutefir_defaults.conf`. The per-rate configs in `configs/`
deliberately leave their `input`/`output` blocks empty and inherit the devices from
here, so this file **must** be deployed:

```sh
mkdir -p ~/.config/BruteFIR
cp brutefir_defaults.linux.conf ~/.config/BruteFIR/brutefir_defaults.conf   # Linux / ALSA
# FreeBSD / OSS: cp brutefir_defaults.conf ~/.config/BruteFIR/brutefir_defaults.conf
```

> ⚠️ If this file is missing, BruteFIR (≥ 1.1) silently auto-generates a fallback
> `~/.brutefir_defaults` that uses a `file` I/O module with no path. Every start then
> fails with *"Parse error: path not set … module 'file'"*, and `drc.sh` rolls back to
> direct DAC output — so DRC never comes up at boot. Deploying the file above (and
> leaving the auto-generated `~/.brutefir_defaults`, if any, deleted) prevents this.

# configs/ directory

Per-geometry, per-rate brutefir configuration files live under `configs/<geometry>/`.

Each file sets `sampling_rate`, points to the matching filter files in `filters/<geometry>/<rate>/`,
and is selected automatically by `drc.sh` based on the active geometry and rate.

Variant configs (e.g. `+2dB`) live alongside the default:
`configs/120.blue/brutefir-192000.conf` (default), `configs/120.blue/brutefir-192000+2dB.conf`, etc.

# The filters/ directory

Contains filter raw files under `filters/<geometry>/<rate>/L.raw` and `R.raw`.
Variants live one level deeper: `filters/<geometry>/<rate>/<variant>/L.raw`.

See `FILTERS_AND_DRC.md` for full documentation of the filter layout, REW2raw conversion,
and how to add new rates or geometries.

#  The old.pos/ directory
Configuration files referring to older speaker / listening positions shall be moved here to avoid cluttering the main directory

# scripts/headroom_calc.py

Calculates the minimum `attenuation:` value to set in each brutefir `.conf` file for a given set of filter files, in order to prevent clipping while maximising dynamics.

## How it works

brutefir processes audio entirely in float64 (effectively infinite dynamic range).
The risk of clipping arises only at the output boundary when the filter has gain > 0 dB at some frequency.

For each filter file the script:
1. Reads the raw impulse response samples (supports `FLOAT64_LE` / `S32_LE` formats)
2. Computes the FFT — each bin gives the filter's complex gain at that frequency
3. Takes `max |FFT(h)|` — the worst-case gain across all frequencies
4. Converts to dB: `headroom = 20 × log10(peak_gain)`
5. Adds a configurable safety margin (default 1 dB) and rounds up to one decimal place

Because brutefir applies **one `attenuation:` value per coeff block to both channels**, the script groups filters into L/R pairs and uses the channel with the higher peak gain to determine the pair's attenuation.

Note: minimising attenuation does **not** improve audio quality. In float64 attenuation is lossless; the only goal is to avoid clipping.

## Usage

```bash
python3 scripts/headroom_calc.py
```

Edit the `FILTER_PAIRS` list at the top of the script to add or change filter files and their formats.
Edit `SAFETY_MARGIN_DB` to adjust the margin (default: 1.0 dB).

## Results for filters/120.blue

Output of `headroom_calc.py` as of v1.5.0:

```
Pair                 Channel file                                      Peak gain Limiting ch  Suggested
──────────────────── ────────────────────────────────────────────────       (dB)            atten (dB)
+0dB float64         FLX+0dB-192k_sox_upsample_float64.raw                +1.071   ← limits        2.1
                     FRX+0dB-192k_sox_upsample_float64.raw                -0.038

+2dB float64         FLX+2dB-192k_sox_upsample_float64.raw                +3.060   ← limits        4.1
                     FRX+2dB-192k_sox_upsample_float64.raw                +1.954

+2dB trimmed S32     FLX+2dB-trimmed-192k.raw                             +3.191   ← limits        4.2
                     FRX+2dB-trimmed-192k.raw                             +2.137

Safety margin applied: 1.0 dB
```

The left channel limits in all pairs. Set `attenuation:` in the `.conf` file as follows:

| Filter pair | `attenuation:` (both channels) |
|---|---|
| `+0dB` float64 | **2.1** |
| `+2dB` float64 | **4.1** |
| `+2dB` trimmed S32 | **4.2** |

# The drc.sh script

`drc.sh` is the single control point for the DRC pipeline. It uses `/usr/bin/env bash`
so it works with Bash in `/usr/bin` on Linux and `/usr/local/bin` on FreeBSD.

Signature: `drc.sh <rate>|resamp|restore|off [variant]`

- `<rate>` — start brutefir at the given sample rate (44100, 48000, 88200, 96000, 192000);
  restarts virtual_oss at the same rate; switches MPD to `DRC-native`
- `resamp` — restarts everything at 192000 Hz; switches MPD to `DRC-resamp` (MPD resamples)
- `restore` — re-applies the state the system was in at shutdown; used by all service
  files on start. It honours the two state files below: if DRC was **off** it stays
  off, otherwise it restores the last sample rate (falling back to 192000 when no rate
  was ever saved)
- `off` — stops brutefir and virtual_oss; switches MPD back to output 1; records the
  off state (but keeps the remembered rate)
- `variant` — optional second argument, e.g. `+2dB`, selects an alternate filter set

State is split across two files in the repo root so the on/off state and the
remembered rate stay independent:

- `last_arg` — the last *active rate* and optional variant (e.g. `192000`, `resamp`,
  `192000 +2dB`). Written on each successful rate/`resamp` run and **never erased by
  `off`**, so turning DRC back on restores the rate you last used. Geometry is not part
  of it — it is hardcoded at the top of the script (`GEOMETRY="120.blue"`).
- `last_power` — `on` or `off`. A rate/`resamp` run writes `on`; `off` writes `off`.
  This is what makes `off` survive a reboot: `restore` reads `last_power` first and
  stays off when it is `off`.

So `drc.sh off` followed by a reboot leaves DRC off (direct DAC); a reboot while DRC is
running brings it back at the same rate. Both files are runtime state (git-ignored).

## MPD native DRC output format

`mpd/musicpd.conf` has a single native DRC output named `DRC-native`.  It uses:

```conf
format "*:*:*"
```

MPD's `format` setting is `sample_rate:bits:channels`.  An asterisk means that
the corresponding attribute is not enforced, so `*:*:*` tells MPD not to force
sample rate, bit depth, or channel count.  This is intentional: native DRC mode
requires selecting the `drc.sh` rate that matches the source track, while MPD
passes the source format through unchanged.

The separate `DRC-resamp` output keeps `format "192000:24:2"` because that mode
explicitly asks MPD to resample everything to 192 kHz. `drc.sh 192000` and
`drc.sh resamp` both use the 192 kHz BruteFIR config, but they are distinct
active configs: native 192 kHz playback is shown as `Flat 192 kHz`, while the
MPD-forced resampling path is shown as `Flat auto-resample`.

# The doc/ directory
It shall contain at least two plots (PNG format), each one with two curves: uncorrected and corrected:
- current.amplitude.png: amplitude
- current.phase.png: phase 

# USB DAC hotplug automation

Plugging in the USB DAC automatically starts brutefir and switches MPD to the DRC output.
This is implemented with a udev rule and two systemd system services.

## Event chain

```
USB DAC plugged in
  └─ udev: ACTION==add, SUBSYSTEM==sound, KERNEL==controlC*, SUBSYSTEMS==usb
       └─ SYSTEMD_WANTS=drc-usb-audio.service  (no-op if already active)
            └─ systemd starts drc-usb-audio.service
                 └─ ExecStartPre: sleep 1  (USB settle time)
                 └─ ExecStart: drc.sh restore
                      ├─ reads last_arg state file
                      ├─ restarts virtual_oss at saved rate
                      ├─ starts brutefir with saved config
                      └─ switches MPD to saved output (DRC-native or DRC-resamp)

USB DAC unplugged
  └─ udev: ACTION==remove, SUBSYSTEM==sound, KERNEL==controlC*, SUBSYSTEMS==usb
       └─ RUN: systemctl stop drc-usb-audio.service
            └─ ExecStop: drc.sh off
                 ├─ stops brutefir
                 ├─ stops virtual_oss
                 └─ switches MPD back to output 1
```

`RemainAfterExit=yes` on `drc-usb-audio.service` prevents the multiple `controlC*` add
events from a single plug-in from starting duplicate brutefir instances. The `remove` rule
resets the service to inactive so the next plug-in works correctly.

## Files

| File | Installed to | Purpose |
|---|---|---|
| `99-usb-audio-drc.rules` | `/etc/udev/rules.d/` | udev rule: triggers the service on DAC plug-in/unplug |
| `etc/systemd/system/mpd.service.d/open-media-drc.conf` | `/etc/systemd/system/mpd.service.d/` | MPD drop-in: run as AUDIO_USER, read config from checkout |
| `etc/systemd/system/drc-usb-audio.service` | `/etc/systemd/system/` | Starts/stops DRC on USB DAC attach/detach |

## The udev rule (`99-usb-audio-drc.rules`)

```
ACTION=="add", SUBSYSTEM=="sound", KERNEL=="controlC*", SUBSYSTEMS=="usb",
    TAG+="systemd", ENV{SYSTEMD_WANTS}="drc-usb-audio.service"
```

- Matches any USB sound card control device (`controlC*`), regardless of DAC model.
- `TAG+="systemd"` hands the event to systemd.
- `SYSTEMD_WANTS` tells systemd to start `drc-usb-audio.service` if it is not already active.

## The service unit

`drc-usb-audio.service` uses `Type=oneshot` with `RemainAfterExit=yes`. It calls
`drc.sh restore` on start (with a 1-second `ExecStartPre` settle delay for USB) and
`drc.sh off` on stop. `RemainAfterExit=yes` keeps the service "active" after ExecStart
completes so repeated udev events (one USB device generates several `controlC*` events)
are ignored and do not launch duplicate brutefir instances.

Because udev synthesizes ADD events for already-present devices at boot, this single
service covers both the boot case (DAC already connected) and the hotplug case (DAC
switched on later) — no separate boot service is needed.

## Installation

Use the Makefile at the root of the repository:

```bash
make install          # copy all files and reload udev + systemd
make install-systemd  # copy service files and reload systemd only
make install-udev     # copy udev rule and reload udev only
```

`make install` requires sudo (prompted once per target that needs it).

## Manual control

```bash
# Stop DRC (stops brutefir, switches MPD back to direct output)
sudo systemctl stop drc-usb-audio.service

# Start DRC (restores last saved rate/variant, or defaults to 192000)
sudo systemctl start drc-usb-audio.service

# Check status
systemctl status drc-usb-audio.service

# Follow logs
journalctl -fu drc-usb-audio.service
```

`drc.sh` continues to work for manual invocation outside of systemd, including direct
rate/variant selection: `drc.sh 192000`, `drc.sh 192000 +2dB`, `drc.sh resamp`, `drc.sh off`.

## FreeBSD rc.d scripts

FreeBSD equivalents are provided under `etc/rc.d/`, with an optional `devd`
rule under `etc/devd/`.  (The directory names — `rc.d`/`devd` vs the Linux
`systemd`/`modules-load.d` — already imply the OS, so there is no extra
`FreeBSD/` level.)

| File | Installed to | Purpose |
|---|---|---|
| `etc/rc.d/brutefir_drc` | `/usr/local/etc/rc.d/` | Manages the BruteFIR process |
| `etc/rc.d/drc_usb_audio` | `/usr/local/etc/rc.d/` | Starts/stops BruteFIR and switches MPD outputs |
| `etc/devd/usb-audio-drc.conf` | `/usr/local/etc/devd/` | Triggers routing on USB audio attach/detach |

Install on FreeBSD with:

```sh
make install-freebsd
```

The `devd` rule calls `service ... onestart/onestop`, so no `rc.conf` enable flags
are required for hotplug operation. Use `/etc/rc.conf` for local overrides:

```sh
brutefir_drc_drcsh="/home/giacomo/DRC/open-media-drc/drc.sh"
drc_usb_audio_start_delay="1"
```

Enable **only** `drc_usb_audio` at boot:

```sh
drc_usb_audio_enable="YES"
```

`drc_usb_audio` is the single entry point. At boot it **probes for the DAC**
(`/dev/dsp0`): if the DAC is on it brings DRC up once; if not, it does nothing and
lets `devd` start DRC when the DAC is switched on later. Do **not** also enable
`brutefir_drc` — it is the worker invoked by `drc_usb_audio` (it runs
`drc.sh restore`) and must stay symlinked but unenabled, otherwise boot starts the
chain twice and the two runs race. `drc.sh` itself now serializes mutating runs
under a lock (`lockf` on FreeBSD, `flock` on Linux), so overlapping triggers are
safe, and a failed BruteFIR start rolls back to direct DAC output instead of
leaving `virtual_oss` orphaned.

Manual control:

```sh
service drc_usb_audio onestart  # restore last DRC state + switch MPD
service drc_usb_audio onestop   # stop BruteFIR + switch MPD to output 1
service brutefir_drc onestart   # restore last DRC state (no USB settle delay)
service brutefir_drc onestop    # stop BruteFIR + virtual_oss + switch MPD
```

The `devd` attach rule matches USB audio interface class `0x01`. The detach rule
stops DRC on USB device removal; add DAC-specific `vendor`/`product` matches if the
host has other USB devices whose removal should not stop DRC.

# Startup, shutdown, and the DAC presence model

DRC only means anything when the DAC is connected: BruteFIR convolves into the DAC,
and `virtual_oss` (FreeBSD) / the ALSA loopback (Linux) exists only to feed it. The
whole startup design follows from one rule:

> **The DAC's presence is the single condition that drives DRC.**
> DAC present → DRC up, replaying the last saved rate/variant.
> DAC absent → DRC down, MPD playing straight to the DAC's direct output.

Everything below is how that rule is made to hold whether the DAC is already powered
at boot or switched on hours later.

## Two ways the DAC appears — and why both need handling

A USB DAC can become available in two ways, and both have to be caught:

1. **Already on at boot.** The kernel enumerates the DAC during device probe, well
   before the service manager runs. By the time rc.d starts, the OSS node
   (`/dev/dsp0`) already exists.
2. **Switched on later.** The DAC is powered up (or plugged in) on a running system.
   The kernel attaches it and emits a hotplug event — `devd` on FreeBSD, `udev` on
   Linux.

Hotplug events only cover case 2. On FreeBSD, `devd` does **not** reliably receive an
attach that happened before it opened `/dev/devctl`, so a DAC that was already on at
boot would never produce an event `devd` can act on. Relying on the hotplug edge
alone would miss the most common case — a box that boots with the DAC on.

So each case uses the kind of trigger that fits it:

- **Boot:** a one-shot **presence probe** — a *level* check that asks "is the DAC here
  right now?" and acts if so.
- **Hotplug:** the **attach event** — an *edge* trigger that fires when the DAC
  appears later.

Both funnel into the same start path, so there is exactly one way DRC comes up.

## The single entry point

On FreeBSD that entry point is the `drc_usb_audio` service, the only DRC service
enabled at boot:

```sh
# /etc/rc.conf
drc_usb_audio_enable="YES"
```

Its `start` does three things, in order:

1. **Skip if already running.** If `/var/run/drc_usb_audio.active` exists, DRC is
   already up — do nothing. The marker lives on tmpfs, so it is correctly absent on a
   fresh boot and present once DRC is up; that makes repeated triggers idempotent.
2. **Settle, then probe.** Sleep `drc_usb_audio_start_delay` (default 1 s) so a
   freshly-attached DAC's OSS node has time to appear, then check for `/dev/dsp0`.
   **If the node is absent, do nothing** and return — `devd` will start DRC when the
   DAC shows up. This is the level check that covers the boot case and harmlessly
   no-ops when the DAC is off.
3. **Bring DRC up.** Call the worker `brutefir_drc onestart`, which runs
   `drc.sh restore`, then write the `.active` marker.

`brutefir_drc` is just that worker — it runs `drc.sh restore` / `drc.sh off`. It is
**symlinked but not enabled**: `drc_usb_audio` calls it on demand, so it has to
resolve as a service, but it must not start on its own at boot.

`devd` drives the same two verbs on hotplug:

```
DAC attached (USB intclass 0x01)  → service drc_usb_audio onestart
USB device detached               → service drc_usb_audio onestop
```

## What `drc.sh restore` does

`restore` brings the system back to the state it was in at shutdown. It first reads
`last_power`: if that is `off`, it re-execs `drc.sh off` and stops there — DRC stays
disabled across the reboot. Otherwise it replays the **desired** state recorded in
`last_arg` (e.g. `resamp`, `192000`, `192000 +2dB`). It re-execs `drc.sh` with those
arguments, and that run rebuilds the chain:

1. Stop any running BruteFIR and wait for it to release the DAC.
2. Disable all MPD outputs so the DAC and the loopback are free.
3. (FreeBSD) Restart `virtual_oss` at the target rate and wait for `/dev/dsp.loop`.
4. Prime the DAC if the rate changed (see below).
5. Start BruteFIR and **verify it stays up** — it forks before opening the audio
   devices and can exit a moment later if it cannot open them.
6. Enable the matching MPD output (`DRC-native` or `DRC-resamp`).
7. Record the state.

`last_arg` is the *desired* state, not the achieved one. A failed start never
rewrites it, so the next trigger retries the same configuration rather than silently
giving up.

## The sample-rate priming quirk

The OKTO DAC has a hardware quirk: the **first** stream opened at a new sample rate
routes silence. It reports "play" and provides USB feedback, but no audio comes out;
a *second* open at the same rate fixes it.

`drc.sh` automates this. When it detects a rate change (target rate ≠ previous rate)
it **primes**: it opens BruteFIR once at the new rate, tears it back down, then starts
it for real. The real start is then the "second" open the DAC needs to actually
output. Within an unchanged rate there is no priming.

## One run at a time

Boot probe, `devd` attach, a manual `drc.sh`, and a detach can all fire close
together. Each mutating run stops BruteFIR and rebuilds `virtual_oss`; if two overlap,
one run's teardown can kill the other's freshly-started BruteFIR or pull
`/dev/dsp.loop` out from under it.

To prevent that, `drc.sh` **serializes** itself: every mutating run re-execs under a
lock (`lockf` on FreeBSD, `flock` on Linux) so only one proceeds at a time and the
others wait. Read-only paths (`drc.sh status`, and `restore` before it re-execs) run
lock-free. If no locking tool is present it proceeds unlocked rather than failing.

## Always a defined state

If BruteFIR cannot be brought up — most often because the DAC is powered but not yet
ready to output — `drc.sh` does not exit half-built. It **rolls back**: it stops
`virtual_oss` and re-enables the DAC's direct output, leaving a clean, audible system
equivalent to `off`. `last_arg` is left untouched, so the next attach (or a manual
`restore`) retries the intended configuration.

This is what guarantees only two resting states exist: **DRC fully up** with BruteFIR
processing, or **direct output** with the DAC playing straight through. There is no
resting state where `virtual_oss` runs without BruteFIR.

## Shutdown

When the DAC is unplugged or powered off, `devd` detach (or a service stop) runs
`drc.sh off`:

1. Stop BruteFIR and wait for it to release `/dev/dsp0`.
2. (FreeBSD) Stop `virtual_oss`.
3. `mpc enable only OKTO-DAC` — switch MPD back to the direct output.

The chain comes down in the reverse order it went up, freeing the DAC before the
direct output reopens it (the DAC is single-open).

## Verifying the result

`drc.sh status` reports the **actual, observed** state — DRC config, `virtual_oss` /
ALSA rate, BruteFIR, and the MPD output and rate — derived from what is *running*, not
from `last_arg`. After a boot or a plug event it is the quickest way to confirm DRC
came up at the expected rate and that the MPD rate matches the BruteFIR rate.

# scripts/REW2raw.sh

Converts a REW-exported WAV impulse response to a brutefir-ready raw float64 file,
resampling to a target sample rate (default: 192 kHz).

The input files are impulse-response FIR filters. For this reason the conversion
does **not** peak-normalise the filter. Peak normalisation would make the result
depend on the largest sample in each channel, including interpolation overshoot
introduced by resampling, and would therefore alter the intended filter gain.

Instead, after resampling, the script applies one deterministic FIR coefficient
scale:

```
scale = input_sample_rate / target_sample_rate
gain_db = 20 * log10(scale)
```

This gain depends only on the sample-rate conversion ratio. It does not depend on
the absolute peak level of the filter, and it is the same for left and right
channels when both source WAVs have the same sample rate.

Theory/source: Julius O. Smith's *Physical Audio Signal Processing* writes that
sampling an impulse response can be expressed as `gamma(t) -> T gamma(nT) ->
gamma(n)`, where `T` is the sampling period. Since `T = 1/Fs`, converting FIR
coefficients from `Fs_source` to `Fs_target` requires:

```
scale = T_target / T_source = Fs_source / Fs_target
```

Reference: https://www.dsprelated.com/freebooks/pasp/Sampling_Impulse_Response.html

Examples for REW exports at 48 kHz:

| Target rate | Scale | Gain |
|---|---:|---:|
| 44100 | 1.0884353741 | +0.73605296 dB |
| 48000 | 1.0 | 0.00000000 dB |
| 96000 | 0.5 | -6.02059991 dB |
| 192000 | 0.25 | -12.04119983 dB |

The printed peak values are diagnostics only. They are useful to inspect clipping
risk and resampling behaviour, but they do not affect the applied gain.

## Resampling quality

The SoX `rate` step uses:

| Flag | Effect |
|------|--------|
| `-v` | Very high quality: band-limited interpolation, 175 dB noise rejection |
| `-L` | Linear phase: preserves the filter's own phase response |
| `-s` | Steep filter: 99% pass-band, keeps near-Nyquist content |
| `-b 64 -e floating-point` | 64-bit float intermediate file, no precision loss before gain stage |
| `-L -t raw -e floating-point -b 64` | final output format: `FLOAT64_LE` raw |

## Usage

```bash
scripts/REW2raw.sh [options] <in.wav> [out.raw|out.wav] [raw|wav] [sample_rate]
```

All arguments after `in.wav` are optional.

Options:

| Option | Meaning |
|---|---|
| `--exact-output` | write exactly the output filename supplied by the caller |
| `--no-keep-intermediate` | remove the temporary float64 WAV after conversion |
| `--intermediate-dir DIR` | write the intermediate float64 WAV in `DIR` |

## Examples

**Explicit output name:**

```bash
scripts/REW2raw.sh FL-REW.wav filters/120.blue/FL-192k.raw
# writes filters/120.blue/FL-192k_sox_upsample_float64.raw
```

By default, `REW2raw.sh` inserts `_sox_upsample_float64` before the output
extension. Use `--exact-output` when the caller needs a stable filename such as
`L.raw` or `R.raw`.

**Exact output name, useful from wrapper scripts:**

```bash
scripts/REW2raw.sh --exact-output --no-keep-intermediate \
  filters/120.blue/rew/FLX-trimmed-48k.wav \
  filters/120.blue/96000/L.raw \
  raw 96000
```

**Keep final output as WAV (e.g. for inspection in REW or Audacity):**

```bash
scripts/REW2raw.sh FL-REW.wav FL-192k.wav wav
```

**Custom sample rate (e.g. 96 kHz):**

```bash
scripts/REW2raw.sh FL-REW.wav FL-96k.raw raw 96000
```

# scripts/REW2raw-all-rates.sh

Generates a stereo pair (`L.raw`, `R.raw`) for every numeric sample-rate directory
directly below an output filter root.

For the current `filters/120.blue` layout:

```text
filters/120.blue/
  44100/
  48000/
  88200/
  96000/
  192000/
  rew/
```

the script processes only the numeric directories and ignores `rew/`.

## Usage

```bash
scripts/REW2raw-all-rates.sh \
  -L filters/120.blue/rew/FLX-trimmed-48k.wav \
  -R filters/120.blue/rew/FRX-trimmed-48k.wav \
  -o filters/120.blue
```

Options:

| Option | Meaning |
|---|---|
| `-L FILE` | left REW-exported WAV impulse response |
| `-R FILE` | right REW-exported WAV impulse response |
| `-o DIR` | output root, e.g. `filters/120.blue` |
| `-y` | do not ask before writing each `L.raw` / `R.raw` pair |

For each numeric sample-rate directory, the script writes:

| File | Meaning |
|---|---|
| `L.raw` | left filter, raw `FLOAT64_LE` |
| `R.raw` | right filter, raw `FLOAT64_LE` |
| `sox.txt` | full conversion log: wrapper command, `REW2raw.sh` calls, SoX command lines, SoX output and measured stats |

Without `-y`, the script asks before writing each rate directory. This is important
when `filters/120.blue/192000` already contains the checked/current filters: answer
`n` for that directory if it must not be overwritten.

If a destination already contains `L.raw` or `R.raw`, the prompt explicitly lists
the existing file(s) and asks for overwrite confirmation. With `-y`, existing
outputs are overwritten automatically, but the script still prints an overwrite
warning before doing so.

After generating filters, run `python3 scripts/headroom_calc.py` to determine the
correct `attenuation:` value for the brutefir `.conf` file. Headroom calculation is
separate from REW-to-RAW conversion: `REW2raw.sh` preserves FIR gain according to
the sample-rate ratio, while `headroom_calc.py` determines the playback attenuation
needed to avoid clipping.

# History and notes

![VBA filter with ALL-PASS phase filter comparison](doc/xtras/FVBA.vs.ALLPASS.md)
