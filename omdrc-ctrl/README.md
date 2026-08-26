# omdrcctrl

## Configuration page

`/configuration` installs and removes audited room-correction designs and pins
physical audio cards by USB identity. The browser uploads one REW `.txts`
directory and its matching `.mdat`; the existing `new_filter_design.py` audit
remains authoritative and its combined output is streamed into the page. Live
installs archive the complete `.mdat` and required TXT/WAV inputs. Installation
never activates a design, and a running or saved design must be switched away
before removal.

The web process runs as the audio user. Root-owned live sites and hardware
configuration need the fixed-purpose helper installed with the panel:

```sudoers
<AUDIO_USER> ALL=(root) NOPASSWD: /usr/local/libexec/omdrc/omdrc-config-helper *
```

The helper validates USB identities, selectors, manifests, hashes, and
destination roots. On FreeBSD Apply updates the two `omdrc_audio_*` role keys,
reconciles the service, and verifies `/dev/dspX`. On Linux it resolves the
saved identity to the current ALSA card before every hotplug restore. Linux
capture selection remains disabled until the CD bridge has an ALSA backend.

There is no new login layer: mutations use same-origin and CSRF checks but keep
the control panel's trusted-LAN model. Never expose port 9090 to an untrusted
network.

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
- **Audio chain diagram** — a block diagram of the chain as the *operating
  system* sees it, with a conditional capture LED, an output LED on the DAC,
  and compact LEDs beside `dsp.play` and `dsp.loop` in the virtual audio block. The blocks
  are the programs actually holding the sound devices right
  now, found with `fstat(1)` (FreeBSD) / `fuser(1)` (Linux) rather than from
  anything this project writes down, so a process from **outside** the chain
  squatting a device — a stray PulseAudio, a leftover `mpv`, a second BruteFIR
  from a half-finished restart — shows up the moment it happens, named and in
  red. See [`[chain]`](#reserved-section-chain).
- **Filter-set switching** — the DRC card title carries a picker listing every
  filter set installed under `configs/`. Choosing one switches to it: BruteFIR
  is restarted on that set's config (it loads its coefficients once, at start,
  so a reload is the only way), at the same sample rate, and the picker stays
  busy until the new set is actually running. See
  [`POST /drc/geometry`](#post-drcgeometry).
- **DRC filter analysis** — a **Filter response** page renders magnitude and
  phase for the hash-bound REW exports: the measured room, the FIR filters and
  the corrected result, each drawn exactly as REW wrote it, with display-only
  smoothing. See [DRC filter response](#drc-filter-response).
- **Live BruteFIR configuration** — a **Configuration ↗** button opens the
  exact running command line and config, identifies the loaded geometry/design
  and lists every coefficient file. Configured attenuation is shown beside a
  clipping-safe value recalculated from the current RAW bytes on every refresh.
- **Live spectrum analyzer** — optional MPD FIFO tap (Linux and FreeBSD) with
  left/right FFT graphs and VU bars/needles. The card is collapsible (revealed on
  Start), the Floor slider drives graphs and meters together, and because the tap
  is pre-DRC the display is automatically delayed to stay in sync with the
  audible, post-BruteFIR sound. It starts only while the browser stream is
  visible, shares one capture across clients, and stops when the page is hidden or
  closed. See [Live Spectrum Analyzer](SPECTRUM_ANALYZER.md).
- **Log viewer and log alerts** — a **Logs** card shows any log listed in
  `[logs]` (MPD, the two upmpdcli logs, qobuzconnect2mpd and BruteFIR by
  default), and
  `[alert:*]` rules watch those same logs for things nothing else in the UI
  would surface. A match raises a dismissible banner at the top of the page —
  Qobuz losing its OAuth token being the case shipped by default — while `ok`
  rules report the good outcome as a quiet line under the renderer buttons. See
  [Reserved section: `[logs]`](#reserved-section-logs).
- **Qobuz OAuth sign-in from the panel** — when OAuth is invalid, the warning
  and its *sign in* action appear at the top of the page and the renderer card
  reveals a **Qobuz sign-in** button. They drive that renderer's own flow, so a
  headless server needs no keyboard. For upmpdcli the panel runs
  `qobuz-init-oauth.py` and offers a restart after the token arrives. For
  qobuzconnect2mpd it keeps `-L` alive to receive the remote-browser redirect,
  then starts and remembers the authenticated renderer automatically. See
  [Reserved section: `[qobuz_oauth]`](#reserved-section-qobuz_oauth) and
  [Reserved section: `[qconnect_oauth]`](#reserved-section-qconnect_oauth).
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
| NumPy ≥ 1.21 | `pip install numpy` — FFT for live filter headroom, the filter-response page and optional spectrum analyzer. *Optional:* if absent, every other feature works and FFT pages report that NumPy is required. |
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
│   │   ├── brutefir_config.html  # live config and filter headroom page
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
| `omdrcctrl_pidfile` | `/var/run/omdrcctrl/omdrcctrl.pid` | Canonical supervisor pidfile for boot, status, and manual service operations. |
| `omdrcctrl_logfile` | `/var/run/omdrcctrl/omdrcctrl.log` | Captures the app's stdout/stderr (`daemon -o`); check here if the service starts but no process runs. |

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

#### Status and manual startup

The pidfile path never changes with the UID invoking `service(8)`. Therefore a
non-root status probe by the configured service user sees the same boot
instance, and `onestart` cannot evade duplicate detection through a private
`/tmp` pidfile:

```bash
service omdrcctrl status
sudo service omdrcctrl onestart
```

FreeBSD rc.d is the system-service interface; use `sudo` for lifecycle
operations. For an independent development instance, run the application
wrapper directly on a different port instead of giving the same rc service a
second identity.

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

### Reserved section: `[logs]`

`[logs]` lists the logs the **Logs** card can show, one `<id> = <path>` per
line, in display order. `<id>.label` renames a source in the picker;
`tail_bytes`, `scan_bytes` and `alert_interval` are reserved key names rather
than sources:

```ini
[logs]
mpd                      = @AUDIO_HOME@/.local/share/mpd/mpd.log
mpd.label                = MPD
upmpdcli               = /tmp/upmpdcli.log
upmpdcli-console       = /tmp/upmpdcli-console.log
upmpdcli-console.label = upmpdcli (plugins)
qobuzconnect2mpd       = /tmp/qconnect2mpd.log
brutefir               = /tmp/brutefir.out
brutefir.label         = BruteFIR
omdrc-cdin             = /tmp/omdrc-cdin.log
omdrc-cdin.label       = CD input
tail_bytes     = 200000   ; most a single log view reads back
scan_bytes     = 65536    ; tail of each source the alert rules see
alert_interval = 20       ; seconds between browser alert polls
```

Omit the section entirely and these same sources are used. Only files
readable by the service user can be shown; a missing file is reported as "not
written yet" rather than as an error.

**upmpdcli writes two logs.** Its own goes to `logfilename` in `upmpdcli.conf`;
its cdplugin subprocesses (Qobuz among them) log to the stderr they inherit,
which is where the Qobuz login verdict appears. The upmpdcli service scripts
installed by the superproject redirect that stderr to
`/tmp/upmpdcli-console.log` — on FreeBSD with `daemon -o` (override with
`upmpdcli_logfile` in `/etc/rc.conf.d/upmpdcli`), on Linux with
`StandardError=append:`. Without that redirection the plugin output goes to
`/dev/null` and the Qobuz rules below have nothing to match.

### Reserved sections: `[alert:<id>]`

Each `[alert:<id>]` section watches the logs for one condition and reports it in
the web UI: `error` / `warn` / `info` as a dismissible banner at the top of the
page, `ok` as a status line under the renderer buttons.

| Key | Required | Description |
|---|---|---|
| `pattern` | yes | Python regex; a matching line raises the alert |
| `clears` | no | Regex whose match *newer* than the pattern's cancels it |
| `message` | no | What the UI shows (defaults to the rule id) |
| `hint` | no | Second line: what to do about it |
| `severity` | no | `error`, `warn`, `info` or `ok` (default `warn`) |
| `sources` | no | Comma-separated source ids (default: all of them) |
| `service` | no | The program the rule speaks for; while it is stopped the alert drops to `info` and `ok` rules stay silent |
| `clears_file` | no | Path whose contents can settle the rule; `@qobuz_token@` = the Qobuz token file |
| `clears_file_pattern` | no | Regex the `clears_file` must match for the rule to stay down |
| `action` | no | UI action button: `qobuz-oauth` for upmpdcli or `qconnect-oauth` for qobuzconnect2mpd |

A rule is active when the last line matching `pattern` is newer than the last
line matching `clears`, so a failure a later restart fixed stops being reported
without the app storing any state. Keep a rule and its `clears` on one source —
"newer than" is only meaningful within a single log. A literal `%` must be
written `%%` (configparser interpolation).

**`service` keeps a stopped renderer quiet.** A log outlives the program that
wrote it, so without this the panel warns about a renderer that is not running —
demanding a renderer switch to fix a login you were not using. Naming the service
makes a stale failure informational (`info`) instead of a warning, annotates it in
the UI with *"… is not running, so this is what its log last said"*, and silences
its `ok` rules: nothing is connected while nothing is running.

`clears_file` is a state check, not an ordering one: while the file matches, the
rule is not raised at all. Use it only where the file *is* the subject of the log
line — a stored Qobuz token settles "OAuth initialisation not done" even if a
stale tail still ends on it, but it does **not** settle a token Qobuz refused,
which is why those are two rules.

**Match a failure sense, not a topic.** A completed sign-in logs `session:
init_oauth: auth_code …`, `Qobuz: trackuri: OAuth initialisation` and `OAuth: got
auth_code …` — every one names both "qobuz" and "oauth" while meaning the
opposite of the failure. A pattern like `qobuz.*oauth` therefore reports a
*working* login as broken, and keeps reporting it, quoting the line that proves
it works.

The three rules shipped by default are each other's inverse, because upmpdcli's
Qobuz plugin has no success line to match: `qobuz-app.py` logs `Qobuz running`
and then logs in silently, printing `oauth initialisation not done` (no token)
or `/user/login returns …` (token refused) only on failure. So a startup or a
completed sign-in with no failure after it is reported as connected:

```ini
[alert:qobuz_oauth]
severity = warn
service  = upmpdcli
sources  = upmpdcli-console
action   = qobuz-oauth
pattern  = (?i)(oauth\s+initiali\w*\s+not\s+done|oauth.*\bnot\s+done\b|\bnot\s+done\b.*oauth|oauth\s+initiali\w*\s+missing|(qobuz|oauth).*\bno\s+token\b)
clears   = (?i)(qobuz.*running|got\s+auth_code|init_oauth:\s*auth_code|trackuri.*oauth\s+initiali)
clears_file         = @qobuz_token@
clears_file_pattern = user_auth_token\s*=\s*\S
message  = upmpdcli: Qobuz OAuth initialisation not done
hint     = Press Qobuz sign-in: the panel runs the OAuth script and shows the URL to open in this browser.  upmpdcli must be running to receive the redirect.

# A token exists but Qobuz would not take it — deliberately no clears_file:
# this is exactly the case a stored token does not settle.
[alert:qobuz_login]
severity = warn
service  = upmpdcli
sources  = upmpdcli-console
action   = qobuz-oauth
pattern  = (?i)(/user/login\s+returns|tried login but failed|qobuz.*login.*fail)
clears   = (?i)(qobuz.*running|got\s+auth_code|init_oauth:\s*auth_code|trackuri.*oauth\s+initiali)
message  = upmpdcli: Qobuz refused the stored login
hint     = The token is there but was not accepted — sign in again.

[alert:qobuz_ok]
severity = ok
service  = upmpdcli
sources  = upmpdcli-console
pattern  = (?i)(qobuz.*running|got\s+auth_code|init_oauth:\s*auth_code|trackuri.*oauth\s+initiali)
clears   = (?i)((oauth\s+initiali\w*\s+not\s+done|oauth.*\bnot\s+done\b|\bnot\s+done\b.*oauth|oauth\s+initiali\w*\s+missing|(qobuz|oauth).*\bno\s+token\b)|(/user/login\s+returns|tried login but failed|qobuz.*login.*fail))
message  = upmpdcli: Qobuz plugin connected
hint     = Started or signed in, and reported no login failure afterwards.

[alert:upmpdcli_mpd_ok]
severity = ok
service  = upmpdcli
sources  = upmpdcli
pattern  = (?i)mpdcli::openconn:\s*mpd connected ok
clears   = (?i)(mpd connection failed|mpdcli::openconn:.*failed|mpdcli::eventloop:\s*could not open connection)
message  = upmpdcli: MPD connected
hint     = The renderer established its local MPD control connection.

# qobuzconnect2mpd is a different program with a different login and its own
# OAuth flow. Its action runs `-L` as AUDIO_USER and starts the normal renderer
# after the token is cached. No clears_file: its separate startup proves auth.
[alert:qconnect_auth]
severity = warn
service  = qobuzconnect2mpd
sources  = qobuzconnect2mpd
action   = qconnect-oauth
pattern  = (?i)(not authenticated|no auth token|cannot stream until it is authenticated|waiting for .*oauth login|login not completed within the timeout|oauth (code exchange|token exchange|callback) failed|token could not be persisted)
clears   = (?i)qobuzconnect2mpd:\s*qobuz plugin connected
message  = qobuzconnect2mpd is not signed in to Qobuz
hint     = Press sign in: the panel starts qobuzconnect2mpd's own OAuth bootstrap and opens its remote-browser redirect flow.

[alert:qconnect_mpd_ok]
severity = ok
service  = qobuzconnect2mpd
sources  = qobuzconnect2mpd
pattern  = (?i)qconnect2mpd:\s*mpd connected ok
clears   = (?i)qconnect2mpd:\s*mpd connect failed
message  = qobuzconnect2mpd: MPD connected
hint     = The renderer established its local MPD control connection.

[alert:qconnect_ok]
severity = ok
service  = qobuzconnect2mpd
sources  = qobuzconnect2mpd
pattern  = (?i)qobuzconnect2mpd:\s*qobuz plugin connected
clears   = (?i)(not authenticated|no auth token|cannot stream until it is authenticated|waiting for .*oauth login|login not completed within the timeout|oauth (code exchange|token exchange|callback) failed|token could not be persisted)
message  = qobuzconnect2mpd: Qobuz plugin connected
hint     = Qobuz accepted its OAuth-backed cloud session.
```

The patterns match the sense rather than the exact upmpdcli 1.9 wording, so a
reworded message in a later release still trips them. Dismissing a banner hides
that exact line and repeat count; a fresh occurrence raises it again.

### Reserved section: `[qobuz_oauth]`

`[qobuz_oauth]` configures the conditional **Qobuz sign-in** button. It is
hidden unless an OAuth alert is active. All keys are optional:

```ini
[qobuz_oauth]
script = /usr/local/share/upmpdcli/cdplugins/qobuz/qobuz-init-oauth.py
upmpdcli_config =   ; empty: search ${PREFIX}/etc/open-media-drc, ${PREFIX}/etc, /etc
cache_config =      ; empty: <cachedir>/qobuz/config, cachedir from upmpdcli.conf
timeout = 45        ; the script fetches the Qobuz app id over the network
```

**How the flow works.** upmpdcli's `qobuz-init-oauth.py` does not serve
anything: it prints two sign-in URLs and exits. Signing in at qobuz.com
redirects the browser to `http://<host>:<plgmicrohttpport>/qobuz/oauth/?code=…`,
which is **upmpdcli's own media-server port**; its Qobuz plugin exchanges the
code and writes `user_auth_token` / `user_id` into `<cachedir>/qobuz/config`.

Two consequences the panel surfaces for you:

- **upmpdcli must be running** to catch the redirect — its port, on its own
  HTTP server. qobuzconnect2mpd is an unrelated program and cannot receive it;
  the reason a switch is needed at all is only that the panel treats the two
  renderers as mutually exclusive (both drive MPD). Rather than telling you to go
  and do it, the panel offers a **Switch to upmpdcli and sign in** button and
  re-runs the script once the port is listening. Switching back afterwards costs
  nothing: the token is on disk, and it is upmpdcli's alone.

- **the redirect host must be reachable from the browser.** The script can only
  guess it from the box's default route, so the panel also offers the address
  this page was reached on — which is provably reachable — and puts it first.
  The `localhost` variant is kept for a browser running on the box itself.

The panel then polls [`GET /qobuz/oauth/status`](#get-qobuzoauthstatus) and, when
the token file changes, offers the upmpdcli restart that makes the plugin pick it
up. Nothing is stored by omdrcctrl itself: the token file is written by upmpdcli,
and the panel only reads whether it is there.

### Reserved section: `[qconnect_oauth]`

`[qconnect_oauth]` configures qobuzconnect2mpd's separate sign-in action:

```ini
[qconnect_oauth]
binary = qobuzconnect2mpd
config =             ; empty: ~/.config/qobuzconnect2mpd, ${PREFIX}/etc, /etc
run_user =           ; empty: same AUDIO_USER as omdrcctrl, on both OSes
url_timeout = 45     ; app-id discovery / URL only; callback lives for 5 minutes
```

Here `-L` is both URL producer and callback receiver. The panel stops the normal
qobuzconnect2mpd service so the bootstrap can bind `qconnectport`, starts
`<binary> -c <config> -L`, captures its stdout URL (including its percent-encoded
redirect), and substitutes the hostname this browser used. The process remains
alive while the browser signs in. A zero exit means the mode-0600 token was
persisted; omdrcctrl then performs a normal renderer switch to
qobuzconnect2mpd, records it for the next boot, and shows
**qobuzconnect2mpd: Qobuz plugin connected**.

The same green statuses appear when the remembered renderer is restored at
boot. They deliberately report two independent facts:

- `qconnect2mpd: MPD connected OK` proves the local MPD control connection.
- `qobuzconnect2mpd: Qobuz plugin connected` is emitted only after Qobuz
  accepts the OAuth-backed cloud session, rather than merely finding a cached
  token.

Both are always written to the configured qobuzconnect2mpd log without
requiring optional info logging.

`run_user` is empty by default on both OSes because omdrcctrl, upmpdcli and
qobuzconnect2mpd all run as `AUDIO_USER`. The OAuth child therefore writes the
token with exactly the identity the normal renderer uses, with no impersonation
or extra sudo rule. A non-empty override remains available for non-standard
installations and uses non-interactive `sudo -u` when it differs from the
controller's user.

> **The two renderers do not share Qobuz credentials.** upmpdcli's token lives
> in `<cachedir>/qobuz/config` as `user_auth_token` / `user_id`.
> qobuzconnect2mpd keeps its own token under `qconnectstatedir` (normally
> `/var/db/qobuzconnect2mpd/user_token`). Each alert action selects the matching
> flow; signing into one renderer does not alter the other's credential.

### Reserved section: `[cdin]`

`[cdin]` points the **CD input** card at `omdrc-cdin`, the S/PDIF capture
bridge. It configures nothing about the daemon itself — the daemon is
configured in `/etc/rc.conf` — only where to read it from:

```ini
[cdin]
enabled = yes
# Must match omdrc_cdin_logfile in /etc/rc.conf: this file IS the card.
log_file = /tmp/omdrc-cdin.log
# Checked with pgrep -x, to tell "stopped" from "broken".
process = omdrc-cdin
# The rc.d service name.  Shown in the UI, and what the card's Start/Stop
# button runs: `service omdrc_cdin onestart|onestop`.
service = omdrc_cdin
# Whether that button exists at all.  Watching needs no privilege; starting and
# stopping needs the rc.d grant in sudoers (below).
control = yes
refresh = 5
# Past failures the card keeps.  Errors accumulate and stay; the healthy status
# line is replaced, so a quiet week does not push last night's dropout out.
max_events = 20
```

`control = no` keeps the card and removes the Start/Stop button — the right
setting on a box without the sudoers grant, where the button could only ever
fail. `enabled = no` removes the card entirely. `process` is what separates a
daemon that is *stopped* from one that is *failing*: a log file outlives the program
that wrote it, and without the process check a box whose CD bridge was switched
off last month would show a red light for whatever that log happened to end on.

### Reserved section: `[chain]`

`[chain]` configures the **Audio chain** card: the block diagram of who is
holding the sound devices and the LEDs on its external and virtual endpoints.

```ini
[chain]
enabled = yes
refresh = 4
# Re-ask through `sudo -n` when omdrcctrl is not root (see below).
privileged = yes
# The four roles, in flow order.  Omit a key to take the platform default;
# set it empty to drop that role, and its LED, from the diagram.
capture = /dev/dsp.capture
bridge  = /dev/dsp.play
loop    = /dev/dsp.loop
dac     = /dev/dsp.dac
```

Defaults per platform:

| role      | FreeBSD             | Linux     | what it is                                  |
|-----------|---------------------|-----------|---------------------------------------------|
| `capture` | `/dev/dsp.capture`  | *(unset)* | the CD / S-PDIF input card, read by `omdrc-cdin` |
| `bridge`  | `/dev/dsp.play`     | `hw:1,0`  | what the players write into                 |
| `loop`    | `/dev/dsp.loop`     | `hw:1,1`  | the other side of that bridge, read by BruteFIR |
| `dac`     | `/dev/dsp.dac`      | `hw:0,0`  | the DAC everything ends up at               |

On FreeBSD these are the role symlinks
[`omdrc_audio`](../etc/rc.d/omdrc_audio) keeps pointed at the right cards,
plus the two cuse nodes `virtual_oss` creates. On Linux they are ALSA specs
written the way BruteFIR writes them (`hw:1,1`, `plughw:0,0`, `hw:Loopback,1`)
and translated to the `/dev/snd/pcmC<card>D<device>{p,c}` node `fuser` is asked
about. `capture` has no conventional number on Linux, so it stays off until you
name yours. A role left empty is not drawn: a box with no CD input sets
`capture =` and the input block disappears rather than sitting there
permanently grey.

The capture input and `omdrc-cdin` are one optional lane: neither its LED nor
either block is drawn unless the capture interface is attached **and**
`omdrc-cdin` is running. The virtual audio block shows a smaller LED directly
beside each of `dsp.play` and `dsp.loop` (or their configured Linux equivalents).
Hovering either LED names the device and its current holder. The compact button
under the diagram hides or restores the LED legend and remembers that choice.

**What the LEDs mean.** Brightness is one axis only — how open the device is:

| LED                | meaning                                                    |
|--------------------|------------------------------------------------------------|
| grey               | the device is not there (card unplugged, `virtual_oss` down) |
| dim red / green    | the device exists and **nobody has it open** — free to acquire |
| lit                | a process is holding it                                     |
| lit and breathing  | audio is actually going through it                          |

The last two are deliberately separate. BruteFIR opens the DAC the moment it
starts and holds it until it exits, and MPD keeps its output open while paused,
so a light driven by the descriptor alone would be green all day and would tell
you nothing. Whether audio is *flowing* comes from the source instead — MPD over
its own protocol, `omdrc-cdin` from the state line in its log. A holder we
cannot ask (an `mpv`, a squatter) counts as producing: we cannot prove it
silent, and it is the one to go and kill either way.

**Role assignment.** On FreeBSD the card also reads
`/var/run/omdrc_audio.roles`, which
[`omdrc_audio`](../etc/rc.d/omdrc_audio) writes on every scan. That is the
only way the panel can tell you the DAC role was a **guess** — two
playback-capable cards and no `omdrc_audio_dac` in `rc.conf`. A wrong guess
is invisible otherwise: the chain comes up, the DAC lights green, and every
byte goes to the wrong card. The card reports it as a warning; `service
omdrc_audio status` prints the candidates with the line to paste.

**Privilege.** `fstat` and `fuser` can only report descriptors of processes the
caller is allowed to debug. omdrcctrl runs as `AUDIO_USER`, which covers the
whole chain — BruteFIR, MPD and `omdrc-cdin` all run as that user — but *not* a
root process holding a device, which is half the point of the card. With
`privileged = yes` and a sudoers grant, one escalated call per poll closes that
hole:

```
<AUDIO_USER> ALL=(root) NOPASSWD: /usr/bin/fstat        # FreeBSD
<AUDIO_USER> ALL=(root) NOPASSWD: /usr/bin/fuser        # Linux
```

Without a grant the first `sudo -n` fails, is never retried (it would only fill
the auth log), and the card adds a line saying the listing is incomplete rather
than quietly showing a half-truth.

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
└── 120.blue@rscreen-20260812/
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

Reads the qobuzconnect2mpd status file and returns its display lines plus the
renderer's activity: `state` is the current phase (`NEW PLAYLIST RECEIVED`,
`RESOLVING STREAM`, `LOADING SEGMENT`, `BUFFERING`, `PLAYING`, `ERROR`, or
empty when nothing is in progress) and `events` is the daemon's activity ring,
oldest first — what it is doing while nothing is audible yet, or the error that
stopped it.  `line3` is kept as the newest ring entry for older readers.

`line1` is empty when the status file carries only a playback-state tag: the
controller has replaced or cleared the queue and there is no track to name.

```json
{ "ok": true, "line1": "[playing] Artist - Title  [1:23 / 4:56]", "line2": "FLAC 16 bit 44.1 kHz",
  "state": "LOADING SEGMENT",
  "events": ["11:24:03 queue received: 14 tracks, starting at item 0",
             "11:24:04 resolving stream URL 1/14 (7%)",
             "11:24:07 segment 7/52 (13%)"],
  "line3": "11:24:07 segment 7/52 (13%)" }
{ "ok": false, "line1": "", "line2": "", "state": "", "events": [], "line3": "" }
```

A daemon too old to report activity simply returns an empty `state` and no
`events`, and the card looks exactly as it did before.

---

### `GET /qconnect/log`

Returns the full content of the **active renderer's** log as a string, with the
source it came from.  `?renderer=qobuzconnect2mpd|upmpdcli` picks one
explicitly; anything else falls back to the renderer that is running, or the one
the boot service would start.

For upmpdcli the console log (`upmpdcli-console`) is preferred over its own log
file: a renderer that dies during startup reports why on the stderr its service
script captures, not in a log file it never got as far as writing.

```json
{ "ok": true, "content": "2026-05-15 14:32:01 [OUT] ...",
  "renderer": "upmpdcli", "source": "upmpdcli-console",
  "label": "upmpdcli (plugins)", "path": "/tmp/upmpdcli-console.log" }
```

---

### `GET /logs/sources`

Lists the logs configured in `[logs]`, with the state of each file.

```json
{ "ok": true, "sources": [
  { "id": "upmpdcli-console", "label": "upmpdcli (plugins)",
    "path": "/tmp/upmpdcli-console.log", "exists": true,
    "size": 4213, "mtime": 1787035072 } ] }
```

---

### `GET /logs/tail`

Tail of one configured log. `source` is a source id from `[logs]` (an unknown
id is a 404 — arbitrary paths cannot be read). `bytes` overrides how much is
read, clamped to `[4096, tail_bytes]`.

`truncated` says the tail starts mid-file (the partial first line is dropped);
`matches` holds the indices of the returned lines that trip a non-`ok` alert
rule, which the viewer marks.

```
GET /logs/tail?source=upmpdcli-console&bytes=65536
```

```json
{ "ok": true, "id": "upmpdcli-console", "label": "upmpdcli (plugins)",
  "path": "/tmp/upmpdcli-console.log", "content": "…", "truncated": false,
  "matches": [2], "exists": true, "size": 4213, "mtime": 1787035072 }
```

---

### `GET /logs/alerts`

Evaluates every `[alert:<id>]` rule against the tail of each source it applies
to and returns what is currently true, most severe first. Polled by the web UI
every `alert_interval` seconds. `key` changes whenever the matched line or its
repeat count does, so a dismissed alert reappears on a fresh occurrence.

```json
{ "ok": true,
  "alerts": [ { "id": "qobuz_oauth", "severity": "warn",
                "message": "Qobuz login: OAuth initialisation not done",
                "hint": "Run …/qobuz-init-oauth.py as the audio user, …",
                "source": "upmpdcli-console", "source_label": "upmpdcli (plugins)",
                "line": "0$qobuz$: Qobuz login: oauth initialisation not done",
                "count": 1, "at": 1787035072, "key": "8f1c2ad0b3e4f567" } ],
  "sources": [ { "id": "upmpdcli-console", "…": "as in /logs/sources" } ],
  "status_sources": [ { "id": "upmpdcli-console", "…": "watched by ok rules" } ] }
```

`status_sources` is the subset of `sources` that `ok` rules read. With no `ok`
alert active, the UI uses it to say whether the connection status is merely
quiet or the log it would come from has not been written yet.

---

### `GET /cdin/status`

What the CD input card paints: the reduction of `omdrc-cdin`'s log to a verdict,
two device states, the latest stats line and the event list.

```json
{ "ok": true, "enabled": true, "running": true,
  "process": "omdrc-cdin", "service": "omdrc_cdin",
  "log": { "path": "/tmp/omdrc-cdin.log", "exists": true,
           "size": 48213, "mtime": 1787035072 },
  "led": "green", "summary": "idle — waiting for audio, output released",
  "state": "idle", "state_why": "digital silence long enough to release the output",
  "capture": { "kind": "capture", "label": "capture", "path": "/dev/dsp.capture",
               "available": true, "error": "", "held": false,
               "at": "2026-08-20 08:01:44" },
  "output":  { "kind": "playback", "label": "output", "path": "/dev/dsp.play",
               "available": true, "error": "", "held": false,
               "at": "2026-08-20 08:59:31" },
  "stats": "lead 1962 ms (min 1955, max 1972)  drift +1.2 ppm …  starves 0 …",
  "stats_at": "2026-08-20 08:12:12",
  "stats_fields": { "lead_ms": 1962, "lead_min_ms": 1955, "drift_ppm": 1.2,
                    "horizon_h": 46, "drops_bytes": 0, "starves": 0,
                    "silence": "0%", "up_s": 125 },
  "metrics": [ { "key": "lead", "label": "buffer", "value": "1962 ms (min 1955)",
                 "level": "ok" },
               { "key": "starves", "label": "underruns", "value": "0", "level": "ok" } ],
  "problems": [],
  "last_error": { "at": "2026-08-20 08:00:01", "severity": "error",
                  "text": "playback /dev/dsp.play: unavailable — …" },
  "active": true, "control": true,
  "events": [ { "at": "2026-08-20 08:00:01", "severity": "error",
                "text": "playback /dev/dsp.play: unavailable — No such file or directory (retrying every 2 s)" },
              { "at": "2026-08-20 08:59:31", "severity": "ok",
                "text": "state idle: digital silence long enough to release the output" } ],
  "truncated": false }
```

`led` is one of `green`, `red`, `idle` and follows device **availability**
only — never whether the output happens to be `held` at this instant, which
swings back and forth all day in normal operation.

`active` is narrower than `running`: it is true only while a disc is playing
*through* the bridge, and it is what decides the card's size. `metrics` and
`problems` are the `[stats]` line reduced — a chip is a measurement, a problem
is a measurement that has already cost something audible (an underrun is a
dropout that happened, and nothing else in the UI would ever mention it again).
`stats_fields` carries the same numbers unformatted; a field the line does not
carry is **absent rather than zero**, because a capture-only run has no lead at
all and `lead 0` would read as a fault. `last_error` is the newest failure in
the window, kept after the condition clears.

`available` is `true`, `false`, or `null` for "not known yet": a daemon that has
just started, or whose log was rotated out from under it, has said nothing about
that end, and that is not the same answer as "broken". Only `false` is a red
light.

`events` keeps every `error`/`warn` in the scanned window (newest
`max_events`, with `truncated` set when older ones were dropped) plus the single
most recent healthy line, in log order. `running` is false whenever the process
is absent, and `led` is then `idle` regardless of what the log says.

---

### `POST /cdin/control`

```json
{ "action": "start" }        // or "stop"
```

Runs `service omdrc_cdin onestart|onestop` and answers with a fresh
`/cdin/status` under `status`, so the card repaints from the truth instead of
from the poll that was already in flight.

**The rc verb's exit status is not the answer.** `onestart` forks a `daemon(8)`
and returns immediately, so it can report success while the daemon dies a
moment later on a device that is not there; `onestop` returns before the
process has finished closing its devices. So the process itself is polled
(`pgrep -x`) until it agrees, for up to `CDIN_SETTLE_SECONDS`, and that is what
`ok` reports. A failure whose output mentions sudo gets the missing-grant hint
appended. Returns 403 when `control = no`, 404 when the card is disabled.

---

### `GET /qobuz/oauth/status`

Whether the Qobuz plugin holds a token, plus the two preconditions for a sign-in
to succeed. `token` is true only when both `user_auth_token` and `user_id` are
present; `token_mtime` is what the UI watches to detect the redirect landing.

```json
{ "ok": true, "token": false, "user_id": "", "token_mtime": 1786979798,
  "cache_config": "/home/giacomo/.cache/upmpdcli/qobuz/config",
  "upmpdcli_running": true, "script_present": true,
  "script": "/usr/local/share/upmpdcli/cdplugins/qobuz/qobuz-init-oauth.py",
  "upmpdcli_config": "/usr/local/etc/open-media-drc/upmpdcli.conf" }
```

---

### `POST /qobuz/oauth/start`

Runs `qobuz-init-oauth.py` (a few seconds — it fetches the Qobuz app id) and
returns the sign-in URLs it printed. The script exits immediately; the redirect
is caught by upmpdcli, so there is no session to keep alive here.

`urls` is ordered best-first, with exactly one `primary`: the address this
request arrived on when it differs from the script's guess, otherwise the
script's network URL. `local` marks the `localhost` variant, which is never
primary while a network URL exists.

```json
{ "ok": true, "upmpdcli_running": true, "token": false,
  "urls": [ { "url": "https://www.qobuz.com/signin/oauth?ext_app_id=…&redirect_url=http://192.168.1.9:49149/qobuz/oauth/",
              "host": "192.168.1.9", "port": 49149, "local": false, "primary": true,
              "label": "the address you are using now (192.168.1.9)" } ],
  "output": "This script must run on the same machine as upmpdcli\n…" }
{ "ok": false, "error": "upmpdcli.conf not found; set upmpdcli_config in [qobuz_oauth]" }
```

---

### `GET /qconnect/oauth/status`

Returns the tracked qobuzconnect2mpd bootstrap session. `phase` is `idle`,
`starting`, `waiting`, `activating`, `connected`, or `error`; a page reload can
therefore restore a sign-in that is still waiting for its redirect.

```json
{ "ok": true, "phase": "waiting", "running": true, "connected": false,
  "binary": "/usr/local/bin/qobuzconnect2mpd",
  "config": "/usr/local/etc/qobuzconnect2mpd.conf",
  "run_user": "",
  "urls": [ { "url": "https://www.qobuz.com/signin/oauth?ext_app_id=…&redirect_url=http%3A%2F%2F192.168.1.9%3A9093%2Foauth%2Fcallback%2F…",
              "host": "192.168.1.9", "port": 9093, "primary": true } ] }
```

---

### `POST /qconnect/oauth/start`

Stops the normal qobuzconnect2mpd service, starts its configured `-L` bootstrap
as `AUDIO_USER`, and waits up to `url_timeout` for the URL. The callback
process continues in the background. When it exits successfully the endpoint's
worker switches to qobuzconnect2mpd and records that renderer for boot; poll the
status endpoint for `phase: connected`.

Calling start while the same bootstrap is active returns its existing session
instead of starting a second callback receiver.

```json
{ "ok": true, "phase": "waiting", "running": true,
  "urls": [ { "url": "https://www.qobuz.com/signin/oauth?…",
              "primary": true } ] }
{ "ok": true, "phase": "connected", "running": false, "connected": true,
  "qobuzconnect2mpd_running": true }
{ "ok": false, "phase": "error",
  "error": "qobuzconnect2mpd.conf not found; set config in [qconnect_oauth]" }
```

---

### `POST /renderer/restart`

Restarts one renderer in place, without switching to the other. Body:
`{"target": "upmpdcli"|"qobuzconnect2mpd"}`; anything else is a 400. Used by the
OAuth panel so upmpdcli picks up a freshly stored token.

```json
{ "ok": true, "running": true }
{ "ok": false, "error": "could not stop upmpdcli" }
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
{ "ok": false, "error": "upmpdcli exited immediately after starting",
  "renderer": "upmpdcli",
  "detail": "ld-elf.so.1: Shared object \"libnpupnp.so.13\" not found ...",
  "log_source": "upmpdcli-console", "log_label": "upmpdcli (plugins)",
  "log_path": "/tmp/upmpdcli-console.log" }
```

A zero exit from the service script is not proof of a running renderer: both are
launched through `daemon(8)`, which forks and returns before the binary has done
anything.  The switch therefore waits (up to `RENDERER_START_TIMEOUT`, 5 s) for
the target to actually appear, and reports a renderer that died on startup as a
failure — with the tail of its own log in `detail`, which the card shows in
place rather than in a toast.  A failed switch is not remembered for the next
boot.

**Linux:** both `qobuzconnect2mpd` and `upmpdcli` must be installed as systemd
`--user` services and omdrcctrl itself must run in that same user session (the
`-DUSER_INSTALL=ON` user service, or any process with the user bus available);
no `sudoers` entry is required.

**FreeBSD:** the service user must be able to run, password-free, the relevant
commands. First align qobuzconnect2mpd with the same `AUDIO_USER` used by
upmpdcli and omdrcctrl:

```sh
sudo sysrc qobuzconnect2mpd_user=AUDIO_USER
sudo sysrc qobuzconnect2mpd_group=AUDIO_USER_PRIMARY_GROUP
sudo sysrc qobuzconnect2mpd_homedir=/var/db/qobuzconnect2mpd
```

Keep `qconnectstatedir = /var/db/qobuzconnect2mpd` in
`/usr/local/etc/qobuzconnect2mpd.conf`. When migrating an existing installation,
stop that renderer and transfer its private state once before restarting it:

```sh
sudo service qobuzconnect2mpd onestop
sudo chown -R AUDIO_USER:AUDIO_USER_PRIMARY_GROUP /var/db/qobuzconnect2mpd
```

The dedicated `qobuzconnect2mpd` account can remain present but is no longer
used. Renderer start/stop still needs the following rc.d grant in `sudoers`:

```
omdrcctrl ALL=(root) NOPASSWD: /usr/sbin/service qobuzconnect2mpd onestart, \
    /usr/sbin/service qobuzconnect2mpd onestop, \
    /usr/sbin/service upmpdcli onestart, /usr/sbin/service upmpdcli onestop
```

The **CD input** card's Start/Stop button needs the same shape of grant for its
own service; without it, set `control = no` in `[cdin]` rather than leaving a
button that can only fail:

```
omdrcctrl ALL=(root) NOPASSWD: /usr/sbin/service omdrc_cdin onestart, \
    /usr/sbin/service omdrc_cdin onestop
```

The qobuzconnect2mpd **sign in** action needs no additional grant: its OAuth
bootstrap runs directly as the same `AUDIO_USER` as omdrcctrl and both
renderers.

No entry is needed for `onestatus`. omdrcctrl also retains its unprivileged
process check as a compatibility fallback for older rc scripts whose pidfiles
were not traversable.

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

### `GET /brutefir-config`

Renders the live **BruteFIR configuration** page, opened in a new tab by the
DRC card's **Configuration ↗** button. Its browser and visible page titles name
the geometry and filter design actually loaded.

### `GET /drc/brutefir-config`

Finds the real running BruteFIR process by `argv[0]`, reports its complete
command line, follows the `.conf` argument, and lists the coefficient filenames,
formats and `attenuation:` values declared there. Every readable `.raw` is
decoded in its declared format and FFT-analysed on demand. The safe attenuation
is the worst filter peak gain plus a 1 dB margin, rounded upward to 0.1 dB; it is
not read from a stored manifest.

```json
{
  "ok": true, "running": true,
  "geometry": "120.blue", "design_id": "rscreen.v2",
  "command_line": "brutefir /.../brutefir-192000@rscreen.v2.conf -daemon",
  "config_path": "/.../brutefir-192000@rscreen.v2.conf",
  "configured_attenuation_db": 3.0,
  "safe_attenuation_db": 2.7,
  "headroom_safe": true,
  "filters": [
    {"channel": "Left", "filename": "/.../L.raw", "format": "FLOAT64_LE",
     "peak_gain_db": 1.62, "configured_attenuation_db": 3.0,
     "safe_attenuation_db": 2.7, "safe": true}
  ]
}
```

When BruteFIR is stopped, `running` is false and no saved or guessed config is
substituted. Built-in coefficients such as `dirac pulse`, missing files and
non-RAW filenames remain visible but are explicitly marked as not analysable.

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
appear on the page. The order is by how often a panel is worth a look, not by
kind: **Digital Room Correction** first, then **Renderer**, **CD input**,
**Spectrum** and **MPD**, then the remaining command groups (**Applications**, plus any custom
group), the diagnostic panels below them (Brutefir CPU, RAM, Audio devices,
Advanced, Top CPU), and finally **System** and the **Logs** card.

Auto-refreshing panels show a client-side countdown such as `refresh: 5s`.
The circular arrow button in each panel header refreshes that panel immediately
and resets the countdown.

### CD input

Reports `omdrc-cdin`, the S/PDIF capture bridge that feeds a CD transport into
the DRC chain. The panel **watches** it rather than driving it: the daemon runs
continuously from boot (`omdrc_cdin_enable="YES"`) and manages its own devices,
holding `/dev/dsp.play` only while audio is actually on the wire and releasing
it after a run of digital silence. Nothing needs pressing for a disc to play.

**The card is the size of its news.** A bridge with no disc in the player has
one line's worth, and it takes one line:

| | the card | why |
|---|---|---|
| **playing** | full size, opened by itself | the only state with anything to watch |
| **idle** / no carrier | the status line and a `▸` | still polling, so a disc opens it again |
| **stopped** | the status line and **Start** | nothing to report and one thing to do |

Expanding or collapsing by hand sticks until the daemon's own state changes —
what the bridge started doing is more interesting than the click before it.
Polling continues while collapsed, which is what lets the card open itself.

Everything shown is parsed from the daemon's log (`log_file` in `[cdin]`, which
must match `omdrc_cdin_logfile` in `rc.conf`):

- an **LED** and a one-line summary, green while the bridge is doing its job —
  `playing — audio on the wire`, `idle — waiting for audio, output released` —
  and red when an end cannot be opened: `capture unavailable — No such file or
  directory`;
- under it, in red, the **last failure**, kept there after the condition
  clears, because that is the thing a live status line can never say — with a
  **dismiss** button, since the only one who can know when that line has
  outlived its usefulness is the person reading it. Dismissing writes a
  watermark (`cdin_error_ack` in the state directory), so it is a note about
  the reader, not about the daemon: failures *newer* than the dismissed one
  still appear, the event list below keeps the history, and nothing is deleted.
  Clearing the log would be the obvious alternative and is the wrong one — the
  card reads the daemon's current state, device rows and stats out of those
  same lines, so it would go blind to hide one sentence;
- both **device paths** with their state: `capture /dev/dsp.capture free`,
  `output /dev/dsp.play held`;
- the `[stats]` line as **chips** — `buffer 1962 ms (min 1955)`,
  `underruns 0`, `dropped 0 B`, `drift +1.2 ppm, fills in 46 h`, `silence`,
  `up` — with anything that already cost something audible spelled out in a
  sentence below them (`3 underruns — the output ran dry, which is an audible
  dropout`). The raw line is kept underneath, as the daemon wrote it;
- a scrolling **event list**.

Three buttons: **↻** refreshes now, **Log** opens the whole `omdrc-cdin` log in
the Logs card, and **Stop** / **Start** runs the rc service. Stop asks first:
it hands `/dev/dsp.play` back to MPD and mpv, and anything playing from the CD
input stops. It is not part of normal use — the daemon is meant to be left
running — but it is the one thing watching cannot do, from a box with no
keyboard. `control = no` in `[cdin]` removes it.

Two rules decide what goes where, and they are the whole design of the card:

- **The LED follows device availability only.** Red means an end could not be
  opened, i.e. no disc could play right now. A *released* output device is the
  daemon working correctly and never colours anything — a light that went amber
  every time the music stopped would be a light nobody reads.
- **Failures are kept; health is replaced.** Every error stays in the list, in
  red, in chronological order, even after the condition clears. The healthy
  status line is replaced rather than accumulated, so it is normally the last
  line — but not when something has gone wrong since. A live status line can
  never say "the output was missing for ten minutes this morning", and that is
  exactly the thing worth saying.

A daemon that is **not running** is reported as idle, not broken, however
alarming the tail of its log is — the same rule the `[alert:*]` rules apply to a
stopped renderer. Its log outlives it, and nothing is unavailable when nothing
is trying to open it. If the daemon has never run *and* left no log, the card
hides itself: on a box with no CD player there is nothing to report.

The raw log is also available in the **Logs** card as `CD input`.

### Qobuz Connect

Shows the track currently playing via qobuzconnect2mpd, updated every second:

- Line 1: playback state + artist/title + position/duration
  (`[playing] Artist - Title  [1:23 / 4:56]`) — **or**, while the renderer is
  working, the phase it is in, in the same large type: `[ NEW PLAYLIST
  RECEIVED ]`, `[ RESOLVING STREAM ]`, `[ LOADING SEGMENT ]`, `[ BUFFERING ]`,
  `[ ERROR ]` (red).  The track name is not kept there during the wait on
  purpose: once the controller replaces the queue the old `[paused] …` line is
  stale, and a stale track name is exactly what made a busy renderer look like
  a stalled one.
- Line 2: audio format (`FLAC 24 bit, stereo, 96.0 kHz`)
- Below them the **activity lines**, shown with a pulsing dot only while there
  is something to report.  Pressing play on the phone starts a sequence (Qobuz
  URL resolution, then segment-by-segment reconstruction of the track, then
  MusicPD opening its output) that can take tens of seconds — occasionally
  minutes — and used to give no sign of life at all.  Entries are timestamped
  and read e.g. `11:24:03 queue received: 14 tracks, starting at item 0`,
  `11:24:07 segment 7/52 (13%)`, `11:24:22 queue handed to MusicPD, waiting
  for it to start`, and turn red for `download failed: …`.  Only the track
  playback is waiting on narrates itself: prefetch of upcoming tracks stays
  quiet.  See the status-file section of the qobuzconnect2mpd README for the
  full vocabulary.

A renderer that refuses to start is reported in place, under the toggle: the
failure, the tail of that renderer's own log, and which log it came from.  It
stays until dismissed or until a switch succeeds — a toast is gone before it
can be read, and a button silently flipping back is not an explanation.

The panel header always has three buttons, plus a conditional fourth:
- **Restart** — calls `POST /qconnect/restart`; shows a toast on success/failure
- **Log** — toggles a scrollable log viewer (auto-refreshed every 5 s while
  open) with colour-coded lines: red for `[ERR]`, green for `[OUT]`.  It shows
  the log of the renderer the card is about, named above the text, and after a
  failed switch it stays on the renderer that would not start
- **☰ / —** — how much activity to show: the last three entries (☰) or only
  the current one (—).  Three is the default; the choice is remembered in
  `localStorage` under `omdrcctrl.qc.activityLines`
- **Qobuz sign-in** — appears only while an OAuth-invalid message is active and
  opens the matching renderer's flow

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

### Live BruteFIR configuration

The DRC card header's **Configuration ↗** button opens a separate tab tied only
to the running process. The title reports **Geometry** and **Filter design**;
the page then shows the full command line, the configuration path it points to,
and the exact coefficient filenames (expected `.raw`) found in that file.

Two large values deliberately distinguish the `attenuation:` configured in
BruteFIR from the safe attenuation calculated at refresh time. For each active
RAW filter the server measures the worst FFT gain of its current bytes, adds a
1 dB clipping margin, and rounds upward to 0.1 dB. The table exposes the
per-filter peak, configured/safe values and pass/fail result, while the headline
safe value is the worst requirement across all loaded filters.

### DRC filter response

The DRC card header carries a **Filter response ↗** button that opens a
dedicated page ([`GET /filter-response`](#get-filter-response)). A single
log-frequency Chart.js plot toggles between magnitude and phase. Its checkbox
legend offers the eight stored exports: measured L/R, the measured aggregate,
FLX/FRX, corrected L/R and the corrected aggregate. The two aggregates are
selected by default. Every curve is one REW text export plotted as exported —
nothing between REW and the browser averages, sums, convolves, interpolates or
subtracts the runtime attenuation.

A smoothing dropdown offers **Unsmoothed**, **Variable**, **Psychoacoustic**,
**1/3 octave** and **1/6 octave** views and defaults to unsmoothed, so what
loads is what REW exported. Smoothing is calculated in the browser on a copy,
for viewing only; it neither rewrites the verified response arrays nor changes
any provenance hash, and it is always named under the graph. It is also the only
smoothing in the system: a bundle whose exports carried REW smoothing, or whose
measurements reached past 24 kHz (a measurement above 48 kHz), cannot be
deployed at all, so the arrays behind the selector are always unsmoothed
in-band data.

The green **Verified** badge means the current coefficient bytes match the
hash-bound bundle. A mismatch produces a red badge, withholds stored room data
instead of guessing from a geometry name, and draws no graph at all. An
expandable panel reports the bundle, active hashes, the export directory, the
source project and commit, the REW `.mdat` behind the measurements, the
aggregate convention, every plotted export with its hash, measurement headers
and REW smoothing, validation and headroom. Chart.js is vendored locally, so the
page works offline.

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

This card, RAM, Audio devices, Advanced, and Top CPU are collapsible. Their
collapsed state is remembered in the browser. Automatic refresh is paused
while a card is collapsed and resumes with an immediate refresh when expanded.

### Audio Devices

Shows `/dev/sndstat` on FreeBSD and decodes `fmt 0x...` bitfields to
`AFMT_*` / `PCM_CAP_*` labels. Refreshed every `sndstat_interval` seconds from
the `[monitor]` section of `commands.conf`.

Click the title (the chevron toggles `▾`/`▸`) to hide or restore the body.

### Advanced

FreeBSD-only diagnostic panel that gathers `sysctl dev.pcm.0` and
`sysctl hw.usb.uaudio`. It is refreshed manually with the panel button and
when a collapsed card is expanded.

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
