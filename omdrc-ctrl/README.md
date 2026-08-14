# omdrcctrl

A lightweight web-based remote control panel for a Linux or FreeBSD desktop.
Commands are defined in a plain-text INI config file; the server renders a
mobile-friendly interface that can be opened in any browser on the local
network — phone, tablet, or another computer.

Originally a replacement for KDE Connect's "Run command" plugin, which
provides no feedback. Every command here either shows its output directly
(READ) or gives a clear visual confirmation on the button (WRITE).

---

## Features

- **READ widgets** — run a shell command and display its output next to a
  label; optional auto-refresh on a configurable interval.
- **WRITE widgets** — a labelled button that fires a command; button turns
  green on success, red on failure, with an optional confirmation dialog for
  destructive actions.
- **Dynamic details** — a READ widget can expose a **Details** button that
  appears automatically when a Markdown file is found under
  `{details_root}/{output}/README.md` (or `INDEX.md`). The file and any
  relative images are served on demand, so each configuration value can have
  its own documentation page without any static wiring.
- **Static details** — a WRITE widget can link to a fixed Markdown file;
  the Details button appears when a nominated systemctl unit is active.
- **Dark, touch-friendly UI** — works well on a phone home screen without
  installing any app.
- **Audio health monitoring** — purpose-built for a *headless* music server:
  the MPD panel reports the full chain (MPD → BruteFIR → ALSA/virtual_oss →
  DAC), the exact stream the DAC is being fed (ALSA `hw_params`: format, rate,
  channels, period/buffer), a sample-rate match check, and a plain-language
  **bit-perfect / no-resampling verdict** so you can confirm everything is
  correct without a screen attached.
- **Filter-set switching** — the DRC card title carries a picker listing every
  filter set installed under `configs/`. Choosing one switches to it: BruteFIR
  is restarted on that set's config (it loads its coefficients once, at start,
  so a reload is the only way), at the same sample rate, and the picker stays
  busy until the new set is actually running. See
  [`POST /drc/geometry`](#post-drcgeometry).
- **DRC filter analysis** — a **Filter response** page renders the live
  frequency-magnitude, phase, and group-delay of the BruteFIR FIR filters
  (`L.raw` / `R.raw`), computed on demand by FFT. See
  [DRC filter response](#drc-filter-response).
- **Live spectrum analyzer** — optional MPD FIFO tap (Linux and FreeBSD) with
  left/right FFT graphs and VU bars/needles. The card is collapsible (revealed on
  Start), the Floor slider drives graphs and meters together, and because the tap
  is pre-DRC the display is automatically delayed to stay in sync with the
  audible, post-BruteFIR sound. It starts only while the browser stream is
  visible, shares one capture across clients, and stops when the page is hidden or
  closed. See [Live Spectrum Analyzer](SPECTRUM_ANALYZER.md).
- **No hard-coded commands** — everything lives in `commands.conf`; restart
  the service to pick up changes.
- **CMake install** — single `cmake --install` copies all files and installs
  the matching service integration: systemd on Linux, rc.d on FreeBSD.

---

## Requirements

| Dependency | Notes |
|---|---|
| Python ≥ 3.9 | `list[dict]` type hints |
| Flask ≥ 2.3 | `pip install flask` |
| Markdown ≥ 3.5 | `pip install markdown` — renders details pages |
| NumPy ≥ 1.21 | `pip install numpy` — FFT for the filter-response page and optional live spectrum analyzer. *Optional:* if absent, every other feature works and FFT pages report that NumPy is required. |
| CMake ≥ 3.16 | build / install only |
| systemd or rc.d | service management: systemd on Linux, rc.d on FreeBSD |

---

## Project layout

```
omdrcctrl/
├── CMakeLists.txt
├── README.md
├── SPECTRUM_ANALYZER.md
├── requirements.txt
├── src/
│   ├── app.py               # Flask application
│   ├── commands.conf.in     # command definitions template
│   ├── omdrcctrl.sh.in       # launcher script template
│   ├── templates/
│   │   ├── index.html            # Jinja2 + vanilla-JS control panel
│   │   ├── details.html          # markdown details page
│   │   └── filter_response.html  # DRC filter-response charts page
│   └── static/
│       └── chart.umd.min.js # vendored Chart.js (filter-response charts)
├── rc.d/
│   └── omdrcctrl.in          # FreeBSD rc.d script template
└── systemd/
    ├── omdrcctrl.service.in       # Linux system service template
    └── omdrcctrl-user.service.in  # Linux user service template
```

---

## Build and install

```bash
# 1. install Python dependencies
pip install flask markdown

# 2. configure
mkdir build && cd build

# system-wide install (default prefix /usr/local, requires sudo for install)
cmake ..

# Linux only: user install with systemd --user (no root required, prefix defaults to ~/.local)
cmake .. -DUSER_INSTALL=ON

# override the prefix explicitly (only meaningful WITHOUT -DUSER_INSTALL; see note below)
cmake .. -DCMAKE_INSTALL_PREFIX=/opt/omdrcctrl

# system services run as the configuring user by default; override if needed
cmake .. -DOMDRCCTRL_SERVICE_USER=myuser

# default command paths point to the parent open-media-drc checkout; override if needed
cmake .. -DOMDRC_REPO_DIR=/path/to/open-media-drc

# 3. install
sudo cmake --install .        # system install
cmake --install .             # Linux user install (no sudo)
```

> **`-DUSER_INSTALL=ON` forces the prefix to `$HOME/.local`** (it overrides any
> `-DCMAKE_INSTALL_PREFIX` you pass). So **configure and install as the target
> user, without `sudo`** — running it under `sudo`/root makes `$HOME=/root` and
> the files land in `/root/.local`, where the user's `systemctl --user` service
> can't find them. If a build tree was already configured with the wrong prefix,
> wipe `build/` and reconfigure as the user. User install lands the unit in
> `~/.local/share/systemd/user/`; enable it with `systemctl --user enable --now
> omdrcctrl`.

On Linux, CMake installs systemd units. On FreeBSD, CMake installs an rc.d
script and rejects `-DUSER_INSTALL=ON` because systemd user services are not
available there. The installed `commands.conf` is generated from
`src/commands.conf.in`; `OMDRC_REPO_DIR` defaults to the parent directory, which
matches the normal git-submodule layout inside `open-media-drc`.

### Linux system install paths (prefix `/usr/local`)

| Path | Contents |
|---|---|
| `/usr/local/bin/omdrcctrl` | launcher shell script |
| `/usr/local/lib/omdrcctrl/app.py` | Flask application |
| `/usr/local/lib/omdrcctrl/README.md` | this file (served at `/readme`) |
| `/usr/local/lib/omdrcctrl/SPECTRUM_ANALYZER.md` | live spectrum analyzer documentation |
| `/usr/local/lib/omdrcctrl/templates/` | HTML templates |
| `/usr/local/lib/omdrcctrl/static/` | vendored Chart.js |
| `/usr/local/etc/omdrcctrl/commands.conf` | command definitions |
| `/usr/local/lib/systemd/system/omdrcctrl.service` | systemd system unit |

### Linux user install paths (prefix `~/.local`)

| Path | Contents |
|---|---|
| `~/.local/bin/omdrcctrl` | launcher shell script |
| `~/.local/lib/omdrcctrl/app.py` | Flask application |
| `~/.local/lib/omdrcctrl/README.md` | this file (served at `/readme`) |
| `~/.local/lib/omdrcctrl/SPECTRUM_ANALYZER.md` | live spectrum analyzer documentation |
| `~/.local/lib/omdrcctrl/templates/` | HTML templates |
| `~/.local/lib/omdrcctrl/static/` | vendored Chart.js |
| `~/.local/etc/omdrcctrl/commands.conf` | command definitions |
| `~/.local/share/systemd/user/omdrcctrl.service` | systemd user unit |

### FreeBSD system install paths (prefix `/usr/local`)

| Path | Contents |
|---|---|
| `/usr/local/bin/omdrcctrl` | launcher shell script |
| `/usr/local/lib/omdrcctrl/app.py` | Flask application |
| `/usr/local/lib/omdrcctrl/README.md` | this file (served at `/readme`) |
| `/usr/local/lib/omdrcctrl/SPECTRUM_ANALYZER.md` | live spectrum analyzer documentation |
| `/usr/local/lib/omdrcctrl/templates/` | HTML templates |
| `/usr/local/lib/omdrcctrl/static/` | vendored Chart.js |
| `/usr/local/etc/omdrcctrl/commands.conf` | command definitions |
| `/usr/local/etc/rc.d/omdrcctrl` | FreeBSD rc.d service script |

---

## Running as a service

### Linux systemd system service

The service runs as `OMDRCCTRL_SERVICE_USER`, which defaults to the user that
configured the CMake build. Override it during configure when needed:

```bash
cmake .. -DOMDRCCTRL_SERVICE_USER=myuser
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now omdrcctrl
sudo systemctl status omdrcctrl
```

Renderer switching drives the `qobuzconnect2mpd` / `upmpdcli` `systemctl --user`
services. The system service reaches that user bus by deriving
`XDG_RUNTIME_DIR` from the service user's uid, which requires
`/run/user/<uid>` to exist. If `OMDRCCTRL_SERVICE_USER` is not always logged
in, enable lingering once so the runtime dir (and thus the toggle) survives
logout:

```bash
sudo loginctl enable-linger "$OMDRCCTRL_SERVICE_USER"
```

### Linux systemd user service (`-DUSER_INSTALL=ON`)

No root required. The service runs as your own user automatically.

```bash
systemctl --user daemon-reload
systemctl --user enable --now omdrcctrl
systemctl --user status omdrcctrl
```

To keep the service running after logout (e.g. on a headless machine), enable linger once:

```bash
loginctl enable-linger $USER
```

### Restarting after config changes

```bash
sudo systemctl restart omdrcctrl          # system
systemctl --user restart omdrcctrl        # user
```

### FreeBSD rc.d service

The rc.d script uses `daemon(8)` and is installed to
`/usr/local/etc/rc.d/omdrcctrl`.

```bash
sudo sysrc omdrcctrl_enable=YES
sudo service omdrcctrl start
sudo service omdrcctrl status
```

The service user defaults to `OMDRCCTRL_SERVICE_USER`, which is set when CMake is
configured. The following rc.conf variables can be overridden with `sysrc`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `omdrcctrl_user` | `OMDRCCTRL_SERVICE_USER` (set at CMake time) | User the daemon runs as. rc.subr drops privileges to this user via `su(1)` when started as root. |
| `omdrcctrl_env` | `PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin DISPLAY=:0` | Environment applied via `env(1)`. Includes `/usr/local/{s,}bin` on `PATH` (rc starts with a minimal `PATH`) and `DISPLAY` for X11 access. |
| `omdrcctrl_pidfile` | `/var/run/omdrcctrl/omdrcctrl.pid` (root) or `${TMPDIR:-/tmp}/omdrcctrl-<user>.pid` (unprivileged) | Location of the pidfile. See note below. |
| `omdrcctrl_logfile` | `/var/run/omdrcctrl/omdrcctrl.log` (root) or `${TMPDIR:-/tmp}/omdrcctrl-<user>.log` (unprivileged) | Captures the app's stdout/stderr (`daemon -o`); check here if the service starts but no process runs. |

```bash
sudo sysrc omdrcctrl_user=myuser
sudo sysrc omdrcctrl_env='PATH=/usr/local/bin:/usr/bin:/bin DISPLAY=:0'
sudo sysrc omdrcctrl_pidfile=/var/run/omdrcctrl/omdrcctrl.pid
```

> **Privilege dropping:** `omdrcctrl_user` and `omdrcctrl_env` are the standard
> rc.subr `${name}_user` / `${name}_env` variables — rc.subr drops privileges
> with `su(1)` and applies the environment with `env(1)`. The script does **not**
> pass `daemon -u`; combining `${name}_user` with `daemon -u` runs
> `setusercontext()` a second time as the already-dropped user and fails with
> *"daemon: failed to set user environment"*.

> **Why the pidfile lives in a subdirectory:** the daemon writes its `-p`
> pidfile (and `-o` logfile) *after* rc.subr drops to `omdrcctrl_user`, so their
> directory must be writable by that user. `/var/run` itself is root-only, which
> is why a plain `/var/run/omdrcctrl.pid` fails with *permission denied* even
> under `sudo`. The script's `start_precmd` (which runs as root) creates
> `/var/run/omdrcctrl` owned by `omdrcctrl_user` so the unprivileged daemon can
> write there.

#### Running without root

When started by an unprivileged user the script clears `omdrcctrl_user` (so
rc.subr does not try to `su` and prompt for a password) and defaults the
pidfile/logfile to `${TMPDIR:-/tmp}/omdrcctrl-<user>.*`. Use `onestart` to bypass
the `omdrcctrl_enable` rcvar check:

```bash
/usr/local/etc/rc.d/omdrcctrl onestart
```

Restart after config changes:

```bash
sudo service omdrcctrl restart
```

---

## Running manually (development)

```bash
cd build
python3 ../src/app.py --config commands.conf        # listens on 0.0.0.0:9090
python3 ../src/app.py --config commands.conf --port 8080
python3 ../src/app.py --config /path/to/commands.conf
```

Open `http://<hostname>:9090` in a browser.

---

## Configuring commands

All commands are defined in the installed `commands.conf` (INI format, parsed
by Python's `configparser`). The default file is generated from
`src/commands.conf.in` at CMake configure time. Lines starting with `#` are
comments.

Each `[section]` is one command. The section name is the internal id and must
be unique and contain no spaces.

### Reserved section: `[qconnect]`

`[qconnect]` is not a command — it configures the Qobuz Connect integration.
Both keys are optional; the defaults match the qobuzconnect2mpd defaults.

```ini
[qconnect]
status_file = /tmp/qconnect2mpd-status.txt
log_file    = /tmp/qconnect2mpd.log
```

These paths are consumed by the `/qconnect/status` and `/qconnect/log` API
endpoints.  Change them only if you set non-default paths in qobuzconnect2mpd's
config (`qconnectstatusfile` / `qconnectlogfile`).

### Reserved section: `[spectrum]`

`[spectrum]` configures the optional live analyzer card. It is disabled by
default and uses an MPD FIFO output as a passive stream copy:

```ini
[spectrum]
enabled = no
mpd_output_name = OMDRC Spectrum
fifo_path = /tmp/omdrc-spectrum.fifo
sample_rate = 48000
bits = 32
channels = 2
refresh_hz = 10
fft_size = 16384
precision_fft_size = 65536
bands = 24
min_frequency = 31.5
floor_db = -40
vu_mode = bars
drc_delay_trim_ms = 0
```

See [Live Spectrum Analyzer](SPECTRUM_ANALYZER.md) for the matching MPD output
block, lifecycle, CPU cost, and limitations.

`sample_rate` must match the MPD FIFO output's `format` rate. The FIFO is raw
PCM, so the analyzer cannot discover the rate from the stream itself.

`drc_delay_trim_ms` is an optional fine-tune (default `0`) added to the
automatically measured DRC sync delay; see
[Live Spectrum Analyzer → DRC Sync](SPECTRUM_ANALYZER.md#drc-sync).

### Keys common to all commands

| Key | Required | Description |
|---|---|---|
| `what` | yes | Label text shown in the UI |
| `group` | yes | Card/section name (`drc`, `apps`, `system`, or any custom name) |
| `type` | yes | `READ`, `WRITE`, or `LINK` |
| `cmd` | READ / WRITE | Shell command to execute |

### WRITE-only keys

| Key | Required | Description |
|---|---|---|
| `button` | yes | Text on the action button |
| `confirm` | no | `yes` — show a confirmation dialog before running (default: `no`) |

### READ-only keys

| Key | Required | Description |
|---|---|---|
| `refresh` | no | Auto-refresh interval in seconds; `0` or omitted = manual only |
| `details_root` | no | Root directory for dynamic details lookup (see below) |

### LINK-only keys

| Key | Required | Description |
|---|---|---|
| `url` | yes | URL opened in a new tab when the user taps **Open ↗** |

### Optional detail/status keys (READ or WRITE)

Any command can expose a **Details** button. Three mutually exclusive approaches:

| Key | Description |
|---|---|
| `process` | Process name checked with `pgrep -x`; Details button appears when the process is running. Prefer over `unit` for portable configs and to avoid systemd side-effects. |
| `unit` | Linux/systemd unit name; Details button appears when `systemctl is-active <unit>` exits 0. |
| `details_link` | External URL for a Details button that is *always* visible (not conditional on process/unit). |
| `details` | Absolute path to a `.md` file rendered on the details page (required with `process` or `unit`). |

`/status` is polled every 5 seconds for all commands that carry `process` or
`unit` together with `details`.

### Dynamic details (READ commands)

A READ command with `details_root` gets a **Details** button that appears
whenever the command's current output matches a directory containing
`README.md` or `INDEX.md` inside the root:

```
{details_root}/{output}/README.md   ← checked first
{details_root}/{output}/INDEX.md    ← fallback
```

Every time `/read/<id>` is called, the server checks whether the file exists
and includes `details_url` in the JSON response if it does. The frontend shows
or hides the button accordingly.

Example — DRC active-config status widget:

```ini
[drc_status]
what         = Active config
group        = drc
type         = READ
refresh      = 5
cmd          = ps -C brutefir -o args= 2>/dev/null \
               | sed -n 's|.*brutefir-\([^ ]*\)\.conf.*|\1|p' \
               | grep . || echo off
details_root = /path/to/filter-docs
```

When brutefir is running with `brutefir-120.blue+0dB.conf` the command
outputs `120.blue+0dB`. The server then looks for
`/path/to/filter-docs/120.blue+0dB/README.md`. If found, the Details button
appears and opens that file rendered as HTML.

Markdown files may use **relative paths** for images and links; they are served
from the same directory as the `.md` file via
`/details-dyn-asset/<id>/<config>/<path>`. Example layout:

```
/path/to/filter-docs/
├── 120.blue+0dB/
│   ├── README.md
│   └── img/
│       └── freq_response.png
└── 120.blue+2dB/
    ├── README.md
    └── img/
        └── freq_response.png
```

### Widget behaviour

**WRITE** — clicking the button fires the command via `POST /run/<id>`.
The server uses `Popen` and waits up to 5 seconds for the process to exit.
If it exits within that window the button turns green (success) or red
(failure, stderr shown in a toast). If it does not exit within 5 seconds
(e.g. a GUI app that keeps running) it is assumed to have launched
successfully and the button turns green.

**READ** — on page load the UI calls `GET /read/<id>`, runs the command
synchronously (10-second timeout), and displays the combined stdout+stderr
next to the label. The `↻` button triggers a manual re-fetch. With
`refresh = N` the output is also polled automatically every N seconds.

### Group ordering

The groups `drc`, `apps`, and `system` always appear in that order.
Any additional group names appear after them in the order they are first
encountered in the config file.

### Example: adding a READ status widget

```ini
[cpu_temp]
what    = CPU temperature
group   = system
type    = READ
refresh = 10
cmd     = sensors | awk '/Core 0/{print $3}'
```

### Example: a custom app launcher

```ini
[vlc]
what   = VLC media player
group  = apps
type   = WRITE
button = Launch
cmd    = vlc
```

### Example: a destructive action with confirmation

```ini
[stop_jack]
what    = Stop JACK audio server
group   = system
type    = WRITE
button  = Stop
confirm = yes
cmd     = systemctl --user stop jack
```

---

## HTTP API

All responses are JSON unless noted.

### `GET /`

Returns the rendered HTML control panel.

---

### `POST /run/<id>`

Execute a WRITE command.

```json
{ "ok": true }
{ "ok": false, "error": "stderr output or description" }
```

---

### `GET /read/<id>`

Execute a READ command and return its output. When the command has
`details_root` set and a matching Markdown file is found, `details_url` is
included.

```json
{ "ok": true,  "output": "120.blue+0dB", "details_url": "/details-dyn/drc_status/120.blue+0dB" }
{ "ok": true,  "output": "off" }
{ "ok": false, "output": "command not found: sensors" }
```

---

### `GET /status`

Server health check and status query. For every command that has both
`process` and `details` configured the server checks `pgrep -x <process>`.
For Linux/systemd commands that have both `unit` and `details` configured, the
server runs `systemctl is-active --quiet <unit>`. The browser polls this every
5 seconds.

```json
{ "ok": true, "units": { "drc_flat": "active", "drc_2db": "inactive" } }
```

---

### `GET /details/<id>`

Renders the static `details` Markdown file for command `<id>` as HTML.
Returns `404` if the command has no `details` key or the file is missing.

---

### `GET /details-asset/<id>/<path>`

Serves files relative to the static `details` Markdown file directory.

---

### `GET /details-dyn/<id>/<config>`

Renders `{details_root}/{config}/README.md` (or `INDEX.md`) as HTML for
a READ command with `details_root`. Returns `404` if no file is found.

---

### `GET /details-dyn-asset/<id>/<config>/<path>`

Serves files relative to the dynamic Markdown file directory (images, etc.).

---

### `GET /readme`

Renders this README as an HTML page.

---

### `GET /qconnect/status`

Reads the qobuzconnect2mpd status file and returns the two display lines.

```json
{ "ok": true, "line1": "[playing] Artist - Title  [1:23 / 4:56]", "line2": "FLAC 16 bit 44.1 kHz" }
{ "ok": false, "line1": "", "line2": "" }
```

---

### `GET /qconnect/log`

Returns the full content of the qobuzconnect2mpd log file as a string.

```json
{ "ok": true, "content": "2026-05-15 14:32:01 [OUT] ..." }
```

---

### `POST /qconnect/restart`

Restarts qobuzconnect2mpd: on Linux via `systemctl --user stop`+`start`, on
FreeBSD via `sudo service qobuzconnect2mpd onestop`+`onestart`. Enabled in the
web UI only while qobuzconnect2mpd is the active renderer.

```json
{ "ok": true }
{ "ok": false, "error": "..." }
```

---

### `GET /qconnect/services`

Returns the running state of the two mutually-exclusive renderers, polled by the
web UI to keep the toggle in sync with reality (Linux: `systemctl --user
is-active <name>`; FreeBSD: `service <name> onestatus`).

`remembered` is the renderer recorded by the last successful switch — the one
the boot service restores after a reboot (see `POST /qconnect/switch`). It is
`null` until the toggle has been used at least once.

```json
{ "ok": true, "qobuzconnect2mpd": true, "upmpdcli": false,
  "remembered": "qobuzconnect2mpd" }
```

---

### `POST /qconnect/switch`

Switches the active renderer. qobuzconnect2mpd and upmpdcli must never run at the
same time, so the currently-running service is stopped first, MPD playback is
stopped and its queue cleared (`mpc stop` / `mpc clear`), then the target is
started. On Linux this is done with `systemctl --user start|stop` (both
renderers are systemd `--user` services, so no privileges are needed); on
FreeBSD with `sudo service <name> onestart|onestop`.

```json
// request
{ "target": "upmpdcli" }
// response
{ "ok": true, "active": "upmpdcli" }
{ "ok": false, "error": "..." }
```

**Linux:** both `qobuzconnect2mpd` and `upmpdcli` must be installed as systemd
`--user` services and omdrcctrl itself must run in that same user session (the
`-DUSER_INSTALL=ON` user service, or any process with the user bus available);
no `sudoers` entry is required.

**FreeBSD:** the service user must be able to run, password-free, the relevant
commands — for example in `sudoers`:

```
omdrcctrl ALL=(root) NOPASSWD: /usr/sbin/service qobuzconnect2mpd onestart, \
    /usr/sbin/service qobuzconnect2mpd onestop, \
    /usr/sbin/service upmpdcli onestart, /usr/sbin/service upmpdcli onestop
```

No entry is needed for `onestatus`: a renderer running under its own service
account keeps its pidfile in a `0700` home directory (qobuzconnect2mpd uses
`/var/db/qobuzconnect2mpd`), so the unprivileged `onestatus` reports "not
running" for a service that is running.  omdrcctrl therefore falls back to
matching the running binary by `argv[0]`, which needs no privilege.

**Remembered across reboots.** A successful switch writes the target name to
`last_renderer` in the shared state directory (beside `drc.sh`'s `last_arg` —
the repo checkout in run-from-repo mode, `/var/db/omdrc` or
`$XDG_STATE_HOME/omdrc` when installed). At boot `scripts/omdrc-renderer start`
reads it and brings that renderer up, so the box returns to the renderer it was
left on:

| | boot service | enable |
|---|---|---|
| FreeBSD | `etc/rc.d/omdrc_renderer` | `sysrc omdrc_renderer_enable=YES` |
| Linux | `etc/systemd/user/omdrc-renderer.service` | `systemctl --user enable omdrc-renderer.service` |

Both renderers must then be left **disabled** in `rc.conf` / the systemd user
default target — one enabled there would start at boot behind the restore
service, leaving two front-ends driving MPD at once. Switching uses the `one`
verbs (`service upmpdcli onestart`), which ignore the rcvar, so disabling costs
nothing.

If omdrcctrl and the boot service resolve *different* state directories — an
installed setup where the panel runs as an unprivileged service user
(`~/.local/state/omdrc`) and the rc script runs as root (`/var/db/omdrc`) — pin
`OMDRC_STATE_DIR` in `omdrc.conf` so both read the same file. Run-from-repo
needs nothing: both land on the checkout.

The helper is also usable by hand:

```bash
scripts/omdrc-renderer status          # remembered + what is running
scripts/omdrc-renderer show            # just the remembered name
scripts/omdrc-renderer set upmpdcli    # record without starting anything
scripts/omdrc-renderer start           # start the remembered one
```

---

### `GET /brutefir/cpu`

Returns per-process CPU usage for all running `brutefir` instances, plus the
sum. Uses a BSD/Linux-compatible `ps axo pid,pcpu,args` parser and matches the
process by its **`argv[0]` basename** (`brutefir`), so it works even on Linux
where brutefir renames its `comm` to an internal thread name; editors/greps that
merely mention a brutefir path are excluded.

```json
{
  "ok": true,
  "procs": [
    { "pid": "12345", "cpu": 24.5 },
    { "pid": "12346", "cpu": 23.8 }
  ],
  "total": 48.3
}
```

`procs` is empty (not an error) when brutefir is not running.

---

### `GET /mpd/info`

Full audio-chain snapshot used by the MPD panel. Locates the MPD/musicpd
daemon (`pgrep -x`), reads its config to find the control port, queries it for
the playing stream, and inspects the downstream stages.

```json
{
  "ok": true,
  "running": true,
  "cpu": 3.1,
  "conf": "/etc/mpd.conf",
  "port": "6600",
  "client": "mpc",
  "state": "playing",
  "song": "Artist - Title",
  "audio": "192000:24:2",
  "sample_rate": 192000,
  "bit_depth": 24,
  "channels": 2,
  "is_linux": true,
  "virtual_oss_rate": null,
  "alsa_rate": 192000,
  "alsa": { "card": 0, "device": 0, "format": "S32_LE", "rate": 192000,
            "channels": 2, "period_size": 8192, "buffer_size": 32768 },
  "brutefir_rate": 192000,
  "rate_status": { "kind": "match", "text": "SAMPLE RATE MATCH" },
  "path_status": { "kind": "drc", "text": "Full-resolution DRC · no resampling",
                   "detail": "BruteFIR applies room correction at the native rate …" }
}
```

- **`audio` / `sample_rate` / `bit_depth` / `channels`** — the stream MPD
  reports. When modern `mpc` omits the `audio:` line, the value is read
  directly over the MPD protocol (`status` command on the control port).
- **`alsa`** (Linux) — parsed from `/proc/asound/card*/pcm*p/sub*/hw_params`;
  this is exactly what the DAC is being fed *right now*. `null` when no stream
  is open. On FreeBSD `virtual_oss_rate` is reported instead.
- **`rate_status`** — `kind` is `match` / `mismatch` / `unknown`; compares MPD
  against virtual_oss + BruteFIR.
- **`path_status`** — plain-language verdict for the bit-perfect hint:
  - `match` → **Bit-perfect passthrough** (DRC off, all rates equal)
  - `drc` → **Full-resolution DRC · no resampling** (BruteFIR engaged at native rate, 64-bit float)
  - `mismatch` → **Resampling active**
  - `unknown` → not enough information (e.g. nothing playing)

---

### `GET /filter-response`

Renders the verified **DRC response** page: one selectable magnitude/phase
chart, checksum verdict, curve checkboxes, and provenance details. Linked from
the DRC card on the main page.

---

### `GET /drc/geometry`

Returns the active filter set (geometry) and every set installed under
`configs/`, as reported by `drc-status.sh --geometry` and `drc.sh geometry
--list`. Feeds the picker in the DRC card title. The active set is always
included in `available`, even if it is no longer on disk, so the UI can always
show what is running.

```json
{ "ok": true, "geometry": "120.blue", "available": ["120.blue", "185", "flat"] }
{ "ok": false, "error": "drc_status not configured" }
```

---

### `POST /drc/geometry`

Switches the active filter set: `{"geometry": "<name>"}`. The name must be one
that `drc.sh geometry --list` reported — the request body never becomes an
argument of its own — and the command is run as an argument vector, not through
a shell.

BruteFIR loads its coefficients from the `.conf` it was started with, so this is
**not** a live operation: `drc.sh geometry <name>` records the choice and
re-applies the current state, which stops BruteFIR and brings the chain back up
on the new set's config. The sample rate does not change, so the DAC keeps its
clock lock. The request therefore blocks until the new set is actually up (or
has failed) — up to 120 s, since the restart includes the DAC warm-up and its
verify retries — and `output` carries `drc.sh`'s report, including notes about
any degradation (a set without the current variant falls back to its plain
filter; a set without the current rate falls back to that set's highest rate).
When DRC is off only the choice is recorded; it applies when DRC is next turned
on.

```json
{ "ok": true,  "geometry": "185", "output": "filter set 185 has no 44100 Hz config — switching to 192k\n…" }
{ "ok": false, "error": "unknown filter set: bogus" }
```

---

### `GET /drc/filter-response`

Returns the FFT analysis of the FIR filters loaded by the **running** BruteFIR
and, only after provenance verification, measured and predicted room curves.
The active `.conf` is located from BruteFIR's command line; it carries the
absolute paths to its coeff (`.raw`) files, their sample format, and the
sampling rate — so no extra configuration is required. When BruteFIR is not
running there is no active filter and the endpoint says so.

```json
{
  "ok": true, "running": true,
  "geometry": "120.blue", "rate": 192000, "conf": "brutefir-192000.conf",
  "verification": {"status": "verified", "bundle_id": "6e84c487…"},
  "channels": [
    { "name": "Left", "color": "#388bfd", "file": "L.raw", "format": "FLOAT64_LE",
      "attenuation": 3.0, "taps": 524288,
      "delay_ms": 500.01,
      "freqs": [10.0, …], "mag": [-1.2, …], "phase": [-43.1, …], "gd": [12.4, …] }
  ]
}
{ "ok": false, "running": false, "error": "BruteFIR is not running — no active filter loaded." }
```

Each channel's impulse response is read with NumPy, transformed with `rfft`,
and reduced to ~700 log-spaced points in the audio correction band. `mag` is
the magnitude in dB (the raw filter transfer function, including its
`attenuation`), `phase` is the wrapped phase after removing the estimated bulk
FIR delay, and `gd` is residual group delay in milliseconds. **The filter files
are never modified** — they are generated externally with REW + SoX and only
read here.

The endpoint hashes the exact active L/R files and matches path, SHA-256,
format, rate and attenuation against a manifest. It then verifies the manifest
bundle ID, analysis SHA-256, and every analysis input hash. A match adds
`frequencies_hz`, selectable `traces`, and `details`. On any mismatch, those
stored measurement/prediction fields are withheld and `channels` remains only
as a live diagnostic.

---

### `GET /spectrum/settings`

Returns the configured analyzer settings and the latest server-side frame
state. The feature may be present but disabled:

```json
{
  "ok": true,
  "enabled": false,
  "output_name": "OMDRC Spectrum",
  "fifo": "/tmp/omdrc-spectrum.fifo",
  "sample_rate": 48000,
  "refresh_hz": 5,
  "frame": { "ok": false, "state": "idle", "error": "not started" }
}
```

### `GET /spectrum/stream`

Server-Sent Events stream used by the Spectrum card. Opening this endpoint
starts the FIFO reader and enables the matching MPD output; closing it disables
the MPD output again when no clients remain. The optional `mode` query parameter
(`music` | `precision`) selects the FFT window. Multiple clients share one
capture thread.

Each event carries one JSON frame. While running it also reports `mode`,
`fft_size`, and `drc_delay` — the DRC sync delay in seconds currently applied to
keep the display aligned with the audible (post-BruteFIR) signal (`0` when DRC
is bypassed):

```json
{
  "ok": true,
  "state": "running",
  "rate": 48000,
  "mode": "music",
  "fft_size": 16384,
  "drc_delay": 0.671,
  "bands": [
    { "freq": 31.5, "label": "31.5", "lo": 28.1, "hi": 35.5 }
  ],
  "left": [-62.4],
  "right": [-60.8],
  "vu": {
    "left_rms": -21.2,
    "right_rms": -20.7,
    "left_peak": -7.4,
    "right_peak": -7.1
  }
}
```

---

### `GET /system/advanced`

FreeBSD-only diagnostic endpoint. Returns the outputs of:

```sh
sysctl dev.pcm.0
sysctl hw.usb.uaudio
```

```json
{
  "ok": true,
  "sections": [
    { "title": "sysctl dev.pcm.0", "ok": true, "output": "..." },
    { "title": "sysctl hw.usb.uaudio", "ok": true, "output": "..." }
  ]
}
```

---

## Built-in monitoring panels

In addition to the configurable command cards, fixed monitoring panels always
appear at the bottom of the page.

Auto-refreshing panels show a client-side countdown such as `refresh: 5s`.
The circular arrow button in each panel header refreshes that panel immediately
and resets the countdown.

### Qobuz Connect

Shows the track currently playing via qobuzconnect2mpd, updated every second:

- Line 1: playback state + artist/title + position/duration
  (`[playing] Artist - Title  [1:23 / 4:56]`)
- Line 2: audio format (`FLAC 24 bit, stereo, 96.0 kHz`)

Two buttons in the panel header:
- **Restart** — calls `POST /qconnect/restart`; shows a toast on success/failure
- **Log** — toggles a scrollable log viewer (auto-refreshed every 5 s while
  open) with colour-coded lines: red for `[ERR]`, green for `[OUT]`

File paths are configured via the `[qconnect]` section in `commands.conf`.

### MPD

The central audio-health panel for a headless server. Backed by
[`GET /mpd/info`](#get-mpdinfo), it shows:

- Daemon state and the portable client used (`musicpc` on FreeBSD, `mpc` on Linux)
- Playback state and current song
- The stream MPD reports — sample rate, bit depth, channels
- **DAC feed** — what the DAC is actually receiving *now*: ALSA `hw_params`
  format/channels and the period/buffer sizes (Linux), or the `virtual_oss`
  rate (FreeBSD). This is read straight from `/proc/asound` and reflects the
  real hardware stream, not just what was requested.
- BruteFIR rate, and a **SAMPLE RATE MATCH** (green) / **RESAMPLING** (red)
  comparison across MPD, `virtual_oss`/ALSA, and BruteFIR
- A highlighted **path verdict** giving a plain-language bit-perfect assessment
  (`Bit-perfect passthrough`, `Full-resolution DRC · no resampling`, or
  `Resampling active`) plus a one-line explanation
- A static **signal path** reminder (`MPD → BruteFIR → ALSA/virtual_oss → DAC`)

A small **DRC status** sub-section (refreshed manually) lists the BruteFIR
`drc.sh status` rows beneath the panel.

### DRC filter response

The DRC card header carries a **Filter response ↗** button that opens a
dedicated page ([`GET /filter-response`](#get-filter-response)). A single
log-frequency Chart.js plot toggles between magnitude and wrapped phase. Its
checkbox legend offers original L/R, independently measured L+R, coherent
calculated L+R, FLX/FRX, corrected L/R and corrected L+R. The coherent original
and corrected sums are selected by default.

The green **Verified** badge means the current coefficient bytes match the
hash-bound bundle. For newly declared designs its always-visible text includes
the annotated source tag, immutable tag-object ID and exact source commit.
Runtime attenuation is included in filter and predicted curves.
A mismatch produces a red badge and withholds stored room data instead of
guessing from a geometry name. An expandable panel reports the bundle, hashes,
source declaration, optional project archive, measurement headers, lineage,
validation and headroom. Chart.js is vendored locally, so the page works offline.

### Spectrum

The optional Spectrum card shows interactive Chart.js band equalizers from the
MPD FIFO analyzer. Left and Right are rendered as separate plots. In portrait
they are stacked; in landscape or on a wide screen they move side by side. Each
plot has a low-to-high color gradient, clean labels starting at 31.5 Hz, and
tooltips showing the band range and dBFS value. The Music/Precision toggle
switches between `fft_size` for lively music display and `precision_fft_size`
for narrow test-tone display.

**Collapsible.** At rest the card shows only its title and the Start button; the
graphs, VU meters and floor slider are revealed when Start is pressed and
collapse again on Stop.

**Floor.** `[spectrum] floor_db` controls visual sensitivity and is also
adjustable from the card's Floor slider. It sets the **bottom** of the scale;
the top is always pinned at 0 dBFS, and the same floor drives the band graphs
*and* both VU styles, so they share one dynamic range. The default `-40` hides
very quiet band residue. When the music stops, every display drops to its
bottom (effectively −∞) regardless of the floor.

**VU.** A Bars/Needles toggle sits below the equalizer (`[spectrum] vu_mode`
only chooses the startup default). RMS/peak are measured over a short ~50 ms
trailing window so the meters keep pace with the music rather than lagging by
the FFT length. The needle dials scale with the Floor slider and use a tall,
wide arc for plenty of travel.

**DRC sync.** The FIFO tap is *pre-DRC*, so while BruteFIR is running the
analyzer holds its window back by the measured BruteFIR path delay to match the
audible sound; the active value is shown in the status line as `DRC sync +Xs`.
See [Live Spectrum Analyzer → DRC Sync](SPECTRUM_ANALYZER.md#drc-sync).

The Start button opens `/spectrum/stream`; Stop closes it. The browser also
closes the stream automatically when the page is hidden or unloaded, which
stops FIFO reading, FFT work, and the MPD analyzer output. Multiple browsers
share one capture over SSE; the FIFO output is enabled while at least one client
is streaming and disabled when the last disconnects.

The card is disabled until `[spectrum] enabled = yes` is set and MPD has the
matching `OMDRC Spectrum` FIFO output (Linux and FreeBSD). The **Details**
button opens the dedicated analyzer documentation installed with the app.

### Brutefir CPU

Shows per-process CPU usage for every running `brutefir` process, refreshed
every `brutefir_interval` seconds from the `[monitor]` section of
`commands.conf`. When more than one process is detected a highlighted **Total**
line is appended. Displays "not running" when brutefir is not active.

brutefir processes are matched by their **`argv[0]` binary name**, not by the
`comm` column: on Linux brutefir renames its main process `comm` to an internal
thread name (e.g. `input`), so a `comm`/`pgrep -x` match would find nothing
while it is clearly running. Matching the command line fixes Linux and stays
correct on FreeBSD; an editor or grep that merely references a brutefir config
path is excluded because only `argv[0]` is checked.

### Audio Devices

Shows `/dev/sndstat` on FreeBSD and decodes `fmt 0x...` bitfields to
`AFMT_*` / `PCM_CAP_*` labels. Refreshed every `sndstat_interval` seconds from
the `[monitor]` section of `commands.conf`.

The card is **collapsible**: click its title (the chevron toggles `▾`/`▸`) to
hide the body. While collapsed its auto-refresh is paused — no polling and no
server-side `sndstat` work happens until it is expanded again, at which point it
refreshes immediately.

### Advanced

FreeBSD-only diagnostic panel that gathers `sysctl dev.pcm.0` and
`sysctl hw.usb.uaudio`. It is refreshed manually with the panel button.

### Top CPU

Shows processes above `topcpu_threshold`, refreshed every `topcpu_interval`
seconds from the `[monitor]` section of `commands.conf`. The server caches this
result for the same interval so multiple browser clients do not run extra `ps`
commands.

---

## Security note

The server executes arbitrary shell commands from `commands.conf` as the
service user. It should only be exposed on a trusted local network.
Do not bind it to a public interface or use it without a firewall.
The `--host` argument defaults to `0.0.0.0` (all interfaces); pass
`--host 127.0.0.1` if you want to restrict it to localhost and proxy
through nginx or similar.
