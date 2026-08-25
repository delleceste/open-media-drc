# Current configuration (2025.09.23)

## Geometry

- 120cm from front wall
- sofa at blue marks (see notebook for details)

## Filters

v.1.5.0, with no boost, peak correction with inversion, crossover correction
(DRC-120.blue/120-blue-with-inversion.mdat)

Revised crossover files (used in rephase): DRC-120.blue/ LR-EP-psy.txt , LR-EP-unsmoothed.txt, X801.rephase, X801.wav


### configuration

LF.0.raw -> 120.blue/FLX+0dB-192k.raw
RF.0.raw -> 120.blue/FRX+0dB-192k.raw



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

The playback paths, from the network renderer or physical CD transport down to
the speakers:

```
 UPnP / OpenHome control point (phone app, upplay, …)
        │
        ▼
 upmpdcli ──→ libupnpp ──→ libnpupnp      (built from source, this order)
        │
        ▼
 MPD (musicpd) ───── direct ──────────────────→ OKTO DAC

 DRC sources ──→ loopback ──→ BruteFIR ──→ OKTO DAC
              (snd-aloop / virtual_oss)   (delleceste fork)
   ├── MPD's DRC output
   └── CD player ──S/PDIF──→ ESI U24 XL ──→ omdrc-cdin
```

`omdrc-cdin` is the FreeBSD/OSS bridge for the second source. It shares the
DRC loopback with MPD but holds it only while CD audio is present; see
[CD / S/PDIF input](#cd--spdif-input-omdrc-cdin) below.
The scripts, configs, filters, services and `omdrc-ctrl` panel that manage both
paths are supplied by this repository.

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
| **omdrc-cdin** | C11 compiler, POSIX threads, FreeBSD OSS capture/playback | base system | — (FreeBSD only) |
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
>
> **This drop-in must be a real file copied into `/etc`, NOT a symlink into the
> checkout.**  If `/home` is a separate mount (common), systemd loads
> `mpd.service` and its drop-ins during early boot *before* `/home` is mounted,
> so a symlink pointing into the checkout is dangling at that moment and the
> override is silently skipped — MPD then starts as the package default `mpd`
> and dies with `Failed to open ".../mpd.conf": Permission denied` (it cannot
> read the config under a `700` home).  `install.sh` therefore `cp`s this one
> drop-in instead of symlinking it; everything else may stay symlinked.

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

**4. open-media-drc (this repo)** — the classical CMake build:

```sh
git clone --recursive https://github.com/delleceste/open-media-drc ~/DRC/open-media-drc
cd ~/DRC/open-media-drc
cp host.cmake.sample host.cmake   # AUDIO_USER (defaults to the invoking user),
$EDITOR host.cmake                #   GEOMETRY, GEOMETRIES, OMDRC_SITE_DATA_DIRS,
                                  #   MUSIC_DIR, VIDEO_DIR, OMDB_API_KEY
mkdir build && cd build
cmake .. -C ../host.cmake
make
sudo make install                 # -> $PREFIX (default /usr/local)
```

> **`host.cmake` is read only by `-C`.** A plain `cmake ..` silently configures
> from the built-in defaults, and adding `-C` to that build directory afterwards
> does **not** fix it: CMake skips an initial-cache assignment whose entry
> already exists. The project warns when it detects this; the cure is always a
> fresh build directory —
> `rm -rf build && mkdir build && cd build && cmake -C ../host.cmake ..`

### Where the filters live

`configs/<geometry>/` and `filters/<geometry>/` are *site data* — one physical
room's measurements — and do not have to sit in this checkout. CMake resolves
each set along `OMDRC_SITE_DATA_DIRS` (a semicolon-separated search path, first
match wins), so a room can be its own repository while this one ships only the
generic `flat` set:

```cmake
set(OMDRC_SITE_DATA_DIRS "${CMAKE_SOURCE_DIR};$ENV{HOME}/devel/omdrc-801N"
    CACHE STRING "Search path for configs/<geo> + filters/<geo>")
```

Configure then reports exactly which directory supplied each set:

```text
-- core-drc: filter set search path
--     /home/giacomo/devel/open-media-drc (this checkout)
--     /home/giacomo/devel/omdrc-801N
--   120.blue (default) <- /home/giacomo/devel/omdrc-801N
--   flat <- /home/giacomo/devel/open-media-drc
```

A missing extra set warns and is skipped; a missing default `GEOMETRY` is fatal.
The design scripts use the matching `OMDRC_SITE_ROOT` (or `--site-root`) — see
*Keeping room data out of the engine repository* in
[`scripts/README.md`](scripts/README.md) and
[`doc/FILTER_PROVENANCE_AND_RESPONSE.md`](doc/FILTER_PROVENANCE_AND_RESPONSE.md).
This box's room data lives in
[omdrc-801N](https://github.com/delleceste/omdrc-801N).

`host.cmake` (the successor to `config.env`) is the single source of
box-specific values. The CMake superproject renders every config from it and
installs the DRC engine (`drc.sh` behind the `omdrc` / `omdrc-status`
wrappers), the site data (brutefir configs + filters for `GEOMETRY` and for any
extra sets listed in `GEOMETRIES` — only installed sets can be selected at
runtime, see `drc.sh geometry` below), the FreeBSD-only `omdrc-cdin` binary and
`rc.d` service, both web
UIs (omdrcctrl :9090 as a system service; omdrcvideo :9080 as a `--user`
service), and the DAC-hotplug glue. The install prints the OS-specific enable
steps and the one or two files to copy into `/etc` (the udev rule; the mpd
drop-in).

Then, **as the audio user** (not root), run the per-user setup:

```sh
make user-install     # or: cmake --build build --target user-install
```

It deploys the things a root install cannot: the mpv-idle desktop autostart
(both OSes — it is a desktop-session entry, not an init-system one), the
omdrcvideo `--user` service (Linux; FreeBSD serves :9080 from `rc.d`), and the
per-user BruteFIR defaults — and prints the two steps that still need root
(`loginctl enable-linger`, joining the `audio` group).

> **Transitional:** the older `./install.sh` (render `*.in` in place from
> `config.env`, run straight from the checkout) still works and is superseded by
> the CMake install, which now covers the whole DRC stack — engine, DAC-hotplug
> glue, both web UIs, and the MPD + upmpdcli renderer configs/units. `install.sh`
> remains only for the desktop glue not yet in CMake: the `browser-nodrc`
> `.desktop` launcher entries and the Linux `snd-aloop` module-load. (The video
> mpv-idle autostart entry moved to CMake + `make user-install`.)

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

# CD / S/PDIF input (`omdrc-cdin`)

`omdrc-cdin` is the FreeBSD/OSS CD-input bridge under [`cdin/`](cdin/). It
captures the ESI U24 XL's 44.1 kHz S/PDIF input and writes it to
`/dev/dsp.play`, the same `virtual_oss` entry point BruteFIR reads for MPD. The
path contains no resampler: captured samples are copied unchanged through a
ring whose lead absorbs the independent CD and DAC clock drift.

The daemon is intended to stay up continuously and has three states:

| State | Input | Output tenancy |
|---|---|---|
| `NO_CARRIER` | no frames; capture is closed and retried | released |
| `IDLE` | frames of exact digital zero | not held; a playing episode releases after `--idle-after` |
| `PLAYING` | non-zero audio is on the wire | acquired |

`--idle-after` defaults to 15000 ms; `0` disables the silence gate and keeps
the output open. While idle, the ring continues rolling through the silence.
When audio returns, `ring_keep_last()` trims it to one lead instead of clearing
it and pre-filling again, so playback resumes with a full lead and does not lose
the first note.

At startup the daemon probes the playback widths once, immediately releases the
device, and fixes the ring layout before any playing episode can hold it. Width
negotiation preserves the first open error, so a missing or busy device is
reported as such instead of being masked by a later sample-width candidate.

On FreeBSD, enable the CMake-installed service and name the ESI capture device:

```sh
# /etc/rc.conf
omdrc_cdin_enable="YES"
omdrc_audio_capture="ESI U24XL"  # names the capture card; that is what
                                   # creates /dev/dsp.capture for cdin
```

`service omdrc_cdin release` (or `SIGHUP`) gives `/dev/dsp.play` back without
stopping capture. The daemon then holds off reacquisition for at least six
seconds (the default is 6 s) so `drc.sh` can safely stop and recreate
`virtual_oss`. Both `drc.sh off` and its service-teardown `stop` path request
this release before touching `virtual_oss`; otherwise an open cuse client can
wedge teardown until reboot.

The `omdrc-ctrl` page at port 9090 includes a **CD input** card backed by
`GET /cdin/status`. Its LED represents device availability, not whether the
output is currently held; ordinary `acquired`/`released` events do not turn it
red. Current healthy state is replaced as it changes, while failures remain in
the red event history. The `[cdin]` section of `commands.conf` must point at the
same log as `omdrc_cdin_logfile`.

The release/reacquire path has been exercised against `/dev/dsp0` with a full
lead, `starves 0` and `drops 0`; `SIGHUP` released the output in about 0.4 s and
held it off for 6 s while the simulated disc continued. The gate and ring
behavior are covered by `test_gate` (8 cases) and four `ring_keep_last()` cases;
all three `cdin` test suites pass.

One hardware question remains: a stopped ESI source must still be observed on
the real capture device. A short read/error enters `NO_CARRIER`, and continued
zero frames enter `IDLE`; if the OSS `read()` instead blocks forever, the gate
cannot run and the daemon will need a capture-read timeout. See
[`cdin/README.md`](cdin/README.md) for lead calibration, the transport rig,
complete options, log contract, bit-perfect verification, and the hardware
test checklist.

# configs/ directory

Per-geometry, per-rate brutefir configuration files live under `configs/<geometry>/`.

Each file sets `sampling_rate`, points to the matching filter files in `filters/<geometry>/<rate>/`,
and is selected automatically by `drc.sh` based on the active geometry and rate.

Design configs (immutable `@design-id`, and legacy `<variant>` suffixes) live alongside the default:
`configs/120.blue/brutefir-192000.conf` (default),
`configs/120.blue/brutefir-192000@rscreen-20260812.conf`, etc.

# The filters/ directory

Contains filter raw files under `filters/<geometry>/<rate>/L.raw` and `R.raw`.
Variants live one level deeper: `filters/<geometry>/<rate>/<variant>/L.raw`.
New A/B designs use `@design-id`, for example
`filters/120.blue/192000/@2026-08-target-a/L.raw`; select one with
`./drc.sh design @2026-08-target-a`.

See `FILTERS_AND_DRC.md` for full documentation of the filter layout, REW2raw conversion,
and how to add new rates or geometries.
The safe source-declaration, annotated-tag, deployment and response-plot
contract is documented in
[`doc/FILTER_PROVENANCE_AND_RESPONSE.md`](doc/FILTER_PROVENANCE_AND_RESPONSE.md)
([printable PDF](doc/FILTER_PROVENANCE_AND_RESPONSE.pdf)).

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
python3 scripts/headroom_calc.py filters/120.blue
python3 scripts/headroom_calc.py filters/120.blue --variant <variant>
```

The script discovers complete `rate[/variant]/L.raw,R.raw` pairs. Use
`--format`, `--margin`, or `--json` when the defaults are not appropriate.

## Results for filters/120.blue

Current verified base bundle (1 dB margin):

```
Pair                 Channel file                                      Peak gain Limiting ch  Suggested
──────────────────── ────────────────────────────────────────────────       (dB)            atten (dB)
44100 default        L.raw                                                +1.277   ← limits        2.3
                     R.raw                                                +0.183
48000 default        L.raw                                                +1.194   ← limits        2.2
                     R.raw                                                +0.778
88200 default        L.raw                                                +1.277   ← limits        2.3
                     R.raw                                                +0.183
96000 default        L.raw                                                +1.194   ← limits        2.2
                     R.raw                                                +0.078
192000 default       L.raw                                                +1.194   ← limits        2.2
                     R.raw                                                +0.078

Safety margin applied: 1.0 dB
```

The configured 3.0 dB base attenuation passes every rate.

# The drc.sh script

`drc.sh` is the single control point for the DRC pipeline. It uses `/usr/bin/env bash`
so it works with Bash in `/usr/bin` on Linux and `/usr/local/bin` on FreeBSD.

Signature: `drc.sh <rate>|resamp|restore|off|stop|status|session|geometry|design [variant]`

- `<rate>` — start brutefir at the given sample rate (44100, 48000, 88200, 96000, 192000);
  restarts virtual_oss at the same rate; switches MPD to `DRC-native`
- `resamp` — restarts everything at 192000 Hz; switches MPD to `DRC-resamp` (MPD resamples)
- `restore` — re-applies the state the system was in at shutdown; used by all service
  files on start. It honours the two state files below: if DRC was **off** it stays
  off, otherwise it restores the last sample rate (falling back to 192000 when no rate
  was ever saved)
- `off` — stops brutefir and virtual_oss; switches MPD back to output 1; **records the
  off state** (but keeps the remembered rate), so a reboot stays off. This is the
  user-facing disable (interactive and the web UI button). On FreeBSD it first asks
  `omdrc-cdin` to release `/dev/dsp.play`
- `stop` — identical teardown to `off` but **does not record the off state**. Used by
  the service stop-paths (systemd `ExecStop`, FreeBSD rc.d stop, devd/udev unplug) so a
  clean reboot of a *running* system is restored rather than left off. It uses the
  same CD-input release handshake before stopping `virtual_oss`
- `session` — read-only, machine-friendly view of the exact persistent tuple used
  by `restore`: geometry, power, mode/rate, design selector and display label
- `geometry` — filter-set control. Bare `drc.sh geometry` prints the active set,
  `drc.sh geometry --list` lists the ones installed under `configs/`, and
  `drc.sh geometry <name>` switches to one. Switching is not a live operation —
  brutefir reads its `.conf` (and loads the coefficients it names) once, at start — so
  the switch records the choice and then re-applies the current state, which stops
  brutefir and brings the chain back up on the new set's config. The rate is unchanged,
  so the DAC keeps its clock lock and no cold-open prime is needed. Two degradations
  are possible and are printed when they happen: a set without the current variant
  falls back to its plain filter, and a set without the current rate at all (a set
  measured only at 192 kHz is normal) falls back to that set's highest rate. When DRC
  is off only the choice is recorded — it applies the next time DRC is turned on
- `design` — bare `drc.sh design` prints the remembered selector,
  `drc.sh design --list` lists the selectors available for the current
  geometry/rate, and `drc.sh design @design-id` performs an A/B switch. The
  control page reports `from -> to`, then independently checks the config and
  RAW hashes actually loaded before marking an immutable design verified.
- `variant` — optional second argument (a config-filename suffix) selects an alternate
  filter set; superseded by `design`, and none is currently shipped

State is split across three files (repo root in run-from-repo mode; `/var/db/omdrc`
or `~/.local/state/omdrc` — override with `OMDRC_STATE_DIR` — when installed)
so the on/off state and the remembered rate stay independent:

- `last_arg` — the last *active rate* and optional variant (e.g. `192000`, `resamp`,
  `192000 <variant>`). Written on each successful rate/`resamp` run and **never erased by
  `off`**, so turning DRC back on restores the rate you last used. The geometry is not
  part of it — it lives in `last_geometry` below.
- `last_geometry` — the filter set chosen at runtime, written by `drc.sh geometry <name>`
  and by the web remote's filter-set picker. `GEOMETRY` in the config file (`config.env`
  in this repo; default `flat` = shipped identity filters, this box sets `120.blue`) is
  the **default**; this file, when present and naming a set that still exists under
  `configs/`, is the **current choice** and wins. A stale name (set removed since) is
  ignored, so the config default takes over again instead of every run failing on a
  missing config. Read by `drc-status.sh --geometry` too, so the status label and the
  web UI agree with what brutefir actually loaded.
- `last_power` — `on` or `off`. A rate/`resamp` run writes `on`; an explicit `off`
  writes `off`. The service teardown verb `stop` deliberately does **not** write it, so
  only a real user action changes it. `restore` reads `last_power` first and stays off
  when it is `off`.
  `off` writes the file **before** tearing anything down, and the teardown that follows
  can no longer abort the run: the file records what was *asked for*, exactly as
  `last_arg` does. It used to be written last, so a single failing `mpc` — which is
  precisely what a wedged MPD produces when `virtual_oss` is pulled out from under an
  open output — aborted the run under `set -e` with the user's choice unrecorded, and
  the next boot cheerfully brought DRC back up. `drc.log` now records both halves:
  `event=power_saved` when the intent is stored, and `event=restore` with the
  `power` / `last_arg` / `state_dir` that a boot actually read.

So `drc.sh off` followed by a reboot leaves DRC off (direct DAC); a reboot while DRC is
running brings it back at the same rate (the shutdown teardown runs `stop`, which leaves
`last_power` untouched). All three files are runtime state (git-ignored).

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

# Browser audio without DRC (`browser-nodrc/`)

Web browsers (Firefox, Chrome, Chromium) cannot play through the DRC chain. While
DRC is on, **BruteFIR holds the DAC (`/dev/dsp.dac`) single-open**, and a browser
that tries to open the default audio device gets silence — or refuses to play.
Unlike MPD or mpv, browsers have no easy hook to route into `/dev/dsp.play`
(the `virtual_oss` entry point), and their audio is rarely critical-listening
material anyway, so the pragmatic answer is to **temporarily bypass DRC** for the
duration of a browsing session and give the browser the DAC directly.

`browser-nodrc/` provides one launcher per browser that does exactly this:

```
browser-nodrc/
  lib.sh                  # shared snapshot/disable/restore helper (sourced)
  firefox-nodrc.sh        # firefox --no-remote, DRC bypassed
  chromium-nodrc.sh       # chromium, DRC bypassed
  chrome-nodrc.sh         # google-chrome, DRC bypassed
  *-nodrc.desktop.in      # KDE/Plasma launcher entries (rendered by install.sh)
```

## What a launcher does

1. **Snapshots** the current DRC state by reading `drc.sh`'s own persisted state
   files — `last_power` (`on`/`off`) and `last_arg` (the remembered rate/variant).
2. Runs `drc.sh off`, freeing the DAC so the browser plays straight to it.
3. Runs the browser **in the foreground**.
4. On exit **restores the exact pre-launch state** — re-applying the saved rate if
   DRC was on, or leaving it off if it was off. This runs from an `EXIT`/`INT`/`TERM`
   trap, so the state is restored even if the browser crashes or the launcher is
   killed.

> **Why not `drc.sh restore`?** `drc.sh off` records `off` in `last_power`, and
> `drc.sh restore` honours that (by design, so an explicit `off` survives a reboot
> — see *What `drc.sh restore` does*). Calling `restore` after the browser quit
> would therefore leave DRC **off**. The launchers instead snapshot the state up
> front and re-apply it directly, so the DRC status you had before is the status
> you get back.

Firefox is launched with `--no-remote` so it always starts a fresh foreground
instance (otherwise a second invocation hands its URL to a running Firefox and
returns immediately, restoring DRC out from under the still-open browser).
Chrome/Chromium can't use `--no-remote` the same way, so those launchers instead
detect an already-running instance with `pgrep` and, if found, just open the URL
without touching DRC.

## Install / deploy

The `.desktop` entries are rendered from their `.in` templates by `./install.sh`
(`@AUDIO_HOME@` from `config.env`). To make them appear in the KDE launcher,
symlink the rendered files into `~/.local/share/applications/` — `install.sh`
prints the exact commands in its deploy reminder:

```sh
mkdir -p ~/.local/share/applications
for b in firefox chromium chrome; do
  ln -sf ~/open-media-drc/browser-nodrc/$b-nodrc.desktop \
         ~/.local/share/applications/$b-nodrc.desktop
done
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

The launcher scripts can also be run directly from a terminal:
`browser-nodrc/firefox-nodrc.sh https://example.com`.

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
            └─ ExecStop: drc.sh stop   (transient teardown — does not record off)
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
| `etc/systemd/system/mpd.service.d/open-media-drc.conf` | `/etc/systemd/system/mpd.service.d/omdrc.conf` | MPD drop-in: run as AUDIO_USER, read config from checkout |
| `etc/systemd/system/drc-usb-audio.service` | `/etc/systemd/system/` | Starts/stops DRC on USB DAC attach/detach |

All three are **copied** into the system path (not symlinked from the checkout): each
is parsed by systemd/udevd at early boot, before `/home` is mounted — see Installation.

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
`drc.sh stop` on stop (transient teardown that does not record the off state, so a
reboot of a running system is restored). `RemainAfterExit=yes` keeps the service "active" after ExecStart
completes so repeated udev events (one USB device generates several `controlC*` events)
are ignored and do not launch duplicate brutefir instances.

Because udev synthesizes ADD events for already-present devices at boot, this single
service covers both the boot case (DAC already connected) and the hotplug case (DAC
switched on later) — no separate boot service is needed.

## Installation

Render the host-specific files from `config.env`, then deploy them:

```bash
$EDITOR config.env     # AUDIO_USER, AUDIO_HOME, PREFIX, MUSIC_DIR, ...
./install.sh           # renders *.in; prints the exact OS-specific deploy commands
```

`install.sh` only renders templates; it then prints the deploy commands (run as root
for the system paths). **Whether a file is copied or symlinked follows one rule — by
*when* it is read:**

- **Deploy glue parsed at early boot** — the systemd `.service` units, the `mpd`
  drop-in, the udev rule, `modules-load.d` (on FreeBSD: the `rc.d` scripts and `devd`
  rule) — is **copied** into the system path. systemd/udevd/rc parse these *before*
  `/home` (a separate mount) is available, so a symlink into the checkout would be
  dangling at parse time and silently skipped — e.g. MPD would fall back to the
  package `User=mpd` and fail to read its config under a `700` home. (Ordering the
  service `After=local-fs.target` does **not** help: that defers *start*, not the
  *parse*. Equivalently the glue could be symlinked from the *root* fs, just not from
  `/home`.)
- **Payload read at runtime** — `mpd.conf`, `drc.sh`, the filters, and BruteFIR's
  `~/.config/BruteFIR/brutefir_defaults.conf` — stays in the checkout/home: it is read
  only after `local-fs.target`, and the services are ordered after the mount, so no
  copy is needed. Re-run the deploy step after a `git pull` to refresh the copied glue.

> A `make install` (CMake) front-end that performs this deploy in one step — instead
> of copy-pasting the printed commands — is planned.

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
rate/variant selection: `drc.sh 192000`, `drc.sh resamp`, `drc.sh off`.

## FreeBSD audio lifecycle

FreeBSD has one project-owned lifecycle service, one devd configuration, and
one MPD lifecycle hook:

| File | Installed to | Purpose |
|---|---|---|
| `etc/rc.d/omdrc_audio` | `/usr/local/etc/rc.d/` | Resolves sound-card roles, owns the master rcvar, enters the audio user's login environment, and reconciles DRC |
| `etc/devd/omdrc-audio.conf` | `/usr/local/etc/devd/` | On `pcmN` attach/detach, starts a detached `service omdrc_audio reconcile` |
| `etc/rc.conf.d/musicpd/omdrc_audio` | `/usr/local/etc/rc.conf.d/musicpd/` | After a successful MPD start/restart, retries one bounded reconcile so pending output routing converges |
| `cdin/rc.d/omdrc_cdin.in` | `/usr/local/etc/rc.d/omdrc_cdin` | Runs the optional CD/S/PDIF bridge after `omdrc_audio` |

The naming convention is deliberate: FreeBSD service names, rc.d filenames and
rc.conf variables use underscores (`omdrc_audio`, `omdrc_audio_enable`);
configuration filenames use hyphens (`omdrc-audio.conf`).

Install the MPD hook as a regular early-boot copy:

```sh
sh scripts/prepare-musicpd-rc-conf-dir.sh \
  /usr/local/etc/rc.conf.d/musicpd
install -m 644 etc/rc.conf.d/musicpd/omdrc_audio \
  /usr/local/etc/rc.conf.d/musicpd/omdrc_audio
```

FreeBSD allows `rc.conf.d/musicpd` to be either one file or a directory. If a
pre-existing installation uses the file form, the preparation helper converts
it to the directory form and moves the original file, unchanged (including its
mode), to `musicpd/00-local.conf`. It is idempotent. The direct Make installer,
the CMake installer and the FreeBSD package pre-install step all perform this
same migration before installing the hook.

The hook is deliberately one-way: a successful `musicpd` start invokes
`service omdrc_audio reconcile`; `omdrc_audio` never starts or restarts
`musicpd`. It adds no lock, sleep, background watcher, or readiness gate. MPD
failure therefore cannot block the physical chain, while a late MPD start is a
specific event that repairs a previously pending output switch.

Configure `/etc/rc.conf`:

```sh
musicpd_enable="YES"
omdrc_audio_enable="YES"
omdrc_audio_user="giacomo"
omdrc_audio_dac="0x152a:0x88c5"        # recommended with more than one card
omdrc_audio_capture="ESI U24XL"        # only for CD input
omdrc_audio_capture_recsrc="auto"
```

Do not set `hw.snd.default_unit=N` in `/etc/sysctl.conf`. The number is assigned
by USB attach order, so an early hard-coded value can select the capture card.
`omdrc_audio` sets it from the current DAC role during each serialized role
transaction, verifies the kernel readback, logs changes/failures, and reports a
mismatch from `service omdrc_audio status`. Other global sound tunables remain
in `/etc/sysctl.conf` as usual.

The old `drc_usb_audio_*`, `brutefir_drc_*`, and `omdrc_sndlink_*`
keys are migration inputs only. Copy local values to the corresponding
`omdrc_audio_*` keys, remove the old entries, and do not enable the removed
services.

`omdrc_audio` has two non-nested critical sections:

1. As root, `roles` takes `/var/run/omdrc/device.lock`, discovers cards by
   identity, updates the DSP/mixer role links, applies role settings, publishes
   `/var/run/omdrc/audio.roles`, and releases the lock.
2. It then uses `su -l` to run `drc.sh reconcile` as the audio user. This
   preserves HOME, PATH and user ownership of BruteFIR. `drc.sh` alone owns
   the chain-transition lock in its persistent state directory.

The reconciler reads complete state rather than interpreting an event as a
start/stop instruction. Repeating it against a correct system is a no-op. The
devd rule matches `pcmN`, not USB interfaces, so unrelated USB devices cannot
tear audio down and a multi-interface UAC2 card cannot cause several transitions.

The current FreeBSD `devd` source waits for each **direct** action with
`wait4(2)`, but `devd.conf(5)` does not specify that as a stable concurrency
contract. Our action uses `daemon -f`, so devd waits only for the launcher and
the detached reconcile workers can overlap. Removing `daemon` would serialize
those workers but could stall every device event while DRC waits on MPD, CUSE,
or BruteFIR. The two locks therefore remain necessary: `device.lock` also
covers boot, MPD-hook and administrator role passes, while `drc.lock` covers
those calls plus direct UI/shell transitions. Combining them would enlarge the
critical section and cross the root/audio-user boundary. A debounce or dirty
flag would add state without removing either protected transaction.

```sh
service omdrc_audio roles
service omdrc_audio reconcile
service omdrc_audio status
service omdrc_audio stop        # transient; desired state is retained
service omdrc_cdin release      # verified release of /dev/dsp.play
```

There is no FreeBSD `/dev/dsp0` fallback. If `/dev/dsp.dac` is absent, an
active transition fails before touching the current chain.

### CD-input state across reboot

`drc.sh cdin` records `last_source=cdin` and the 44.1 kHz intent under the
lifecycle lock. The web panel's “CD input (44.1 kHz)” action invokes it. An
ordinary rate action records the return-to-music intent before device and
configuration validation. Thus a failed transition is retried as music after
reboot instead of restoring stale CD mode. Boot reconciliation restores the
saved source and rate even when MPD is slow; MPD calls have deadlines and the
MPD successful-start hook repairs pending output routing.


## What `drc.sh restore` does

`restore` brings the system back to the state it was in at shutdown. It first reads
`last_power`: if that is `off`, it re-execs `drc.sh off` and stops there — DRC stays
disabled across the reboot. Otherwise it replays the **desired** state recorded in
`last_arg` (e.g. `resamp`, `192000`). It re-execs `drc.sh` with those
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

1. Stop BruteFIR and wait for it to release the DAC.
2. (FreeBSD) send `SIGHUP` to `omdrc-cdin`, wait for `/dev/dsp.play` to be
   released, and start its reacquisition hold-off.
3. (FreeBSD) stop `virtual_oss`.
4. `mpc enable only OKTO-DAC` — switch MPD back to the direct output.

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

# Video: mpv playback + phone web remote

Besides the audio DRC chain, this box also plays **video** (Blu-ray discs, DVDs,
local files, streams) through mpv, with the audio routed through the same
virtual_oss/brutefir DRC path. Two parts live under [`video/`](video/):

- **Playback launchers** — `play-bluray.sh` (physical USB Blu-ray: gcache
  read-ahead + longest-title + DRC-aware audio) and `play-media.sh` (files and
  network/stream URLs). See [`video/README.md`](video/README.md).
- **Phone web remote** — [`video/webremote/`](video/webremote/README.md): a
  LAN-only web app (no app to install) to **browse the media drives, tap to
  play, and control playback from an Android browser** — the mpv-only
  replacement for Kodi on this headless, keyboard-less box.

```
 Android phone (browser, LAN only)
        │  HTTP :9080
        ▼
 video webremote (Flask)  ── browse /media/USBHD2/video, thumbnails, IMDb info
        │  JSON IPC
        ▼
 persistent idle mpv  ──→ virtual_oss / brutefir ──→ DAC + projector
```

The web remote browses the whitelisted media root, shows ffmpeg thumbnails and
OMDb-verified IMDb details, lets you pin favourite folders, and drives a hidden
persistent mpv over its JSON IPC socket (play / seek / pause / volume / stop).
It mirrors the deployment model of the audio control panel
([`omdrc-ctrl`](omdrc-ctrl/README.md)): run-from-repo, LAN-only, FreeBSD rc.d
service (`omdrcvideo`), with the idle mpv autostarted by the KDE/Plasma session.

**Full details:** [`video/webremote/README.md`](video/webremote/README.md)
(install, API, config) and [`video/webremote/ARCHITECTURE.md`](video/webremote/ARCHITECTURE.md)
(design rationale).

# History and notes

![VBA filter with ALL-PASS phase filter comparison](doc/xtras/FVBA.vs.ALLPASS.md)
