# Plan: from run-from-repo to a real FreeBSD port

Status: Phases 1 and 2 are implemented on the `freebsd-port` branch (flat
default geometry, runtime config/state resolution, install Makefile,
`.gitattributes` tarball hygiene, draft port skeleton under
`freebsd/audio/open-media-drc/` — see `freebsd/README.md` for its blockers).
The `CTRL` option (omdrc-ctrl web UI) is now wired up: `make install-ctrl`
installs it under `share/omdrc-ctrl/` with an `@sample` config and no baked
build-host path, and omdrc-ctrl resolves its config and runtime state the same
way `drc.sh` does (§1.3/§1.4).  Run-from-repo keeps its CMake flow untouched.
Phase 0 (upstreaming) proceeds independently. Linux packaging is explicitly
out of scope for now (one OS at a time).

## Why the repo cannot be ported as-is

A FreeBSD port is a recipe that installs *identical, immutable* files on every
machine, under hier(7) paths, from a versioned release tarball. The
run-from-repo model violates that on every axis — deliberately, because it
optimizes for a different thing (zero-config personal appliance):

1. **Files are rendered per-host.** install.sh bakes `config.env` values
   (`@AUDIO_USER@`, `@AUDIO_HOME@`, `@REPO_DIR@`) into the live files. A
   package must install the same bytes everywhere; configuration happens
   *after* install, at runtime.
2. **The tree is written at runtime.** drc.sh keeps `last_arg`, `last_power`,
   `drc.log` beside itself. `pkg check -s` flags any modified packaged file;
   state must live in `/var/db/` (or XDG state for user mode).
3. **Room-specific data is mixed with software.** `configs/120.blue`,
   `filters/*` (~50 MB) are personal measurement products, not software. A port
   must ship neutral defaults.
4. **rc.d scripts shadow other ports.** `etc/rc.d/musicpd` and `upmpdcli`
   replace scripts owned by audio/musicpd and net/upmpdcli. A port may not
   override another port's rc script — the stock scripts' rc.conf knobs
   (`musicpd_config`, `upmpdcli_config`, …) must be used instead.
5. **Dependency on a personal BruteFIR fork.** RUN_DEPENDS must resolve to
   ports; `github.com/delleceste/brutefir` is not one.
6. **Kernel/userland patches.** A port cannot patch the base system (uaudio,
   cuse) and should not carry patches for another port (virtual_oss). These
   must land upstream first.
7. **Missing packaging basics.** No LICENSE file, no tagged releases, and the
   tarball would ship debugging journals and kernel patches.

None of this means run-from-repo has to die. The standard shape is:

> The repo becomes a normal upstream project that *also* supports
> `make install PREFIX=... DESTDIR=...`. Run-from-repo remains the
> development/appliance mode; the port is a thin consumer of tagged releases.

## Phase 0 — Upstream the out-of-tree pieces (prerequisite, in flight)

The port can only depend on what exists in base/ports:

- **uaudio patches** (shared-clock fix, clock-before-alt) → FreeBSD base.
  Already in progress: bug 295933 / PR 2323.
- **cuse refleak fix** → base. In progress: bug 296291.
- **virtual_oss SETTRIGGER deadlock patch** → upstream
  `hselasky/virtual_oss`, so audio/virtual_oss inherits it.
- **BruteFIR fork**: decide its fate. Options, in order of preference:
  a. Upstream the delta to Anders Torger (upstream is dormant — unlikely);
  b. Submit the delta as `files/` patches to the existing audio/brutefir port
     (viable if the delta is small and FreeBSD-relevant, e.g. OSS fixes);
  c. Release the fork as its own project and add a port
     (audio/brutefir-omdrc) — most work, only if (b) is refused.
- **kodi-virtual-oss-patch**: not shippable by this port. Upstream to Kodi or
  to multimedia/kodi's `files/`, or drop from the release tarball.

## Phase 1 — Make the repo package-friendly (engine / site-data split)

This phase is worth doing even if the port never happens: it makes the repo
usable by anyone, not just this box/room.

### 1.1 Split engine from site data

- **Engine** (shipped): drc.sh, drc-status.sh, omdrc-ctrl, video/webremote,
  browser-nodrc, rc.d/devd for *our own* services, mpd/upmpdcli *sample
  snippets*, docs.
- **Site data** (not shipped): `configs/120.blue`, `filters/120.blue`,
  `filters/185-green`, the room README sections, the `doc/current.*.png`
  measurement plots. The seam for this now exists: `OMDRC_SITE_DATA_DIRS`
  (CMake) and `OMDRC_SITE_ROOT` (the design scripts) resolve
  `configs/<geo>` + `filters/<geo>` in a separate checkout beside the engine —
  see *Keeping room data out of the engine repository* in `scripts/README.md`.
  The name is deliberately not `OMDRC_SITE_DIR`, which drc.sh already uses at
  runtime for the installed `etc/open-media-drc`. Git history keeps them either way.

### 1.2 FLAT default filters (do this first — it is independent and small)

Ship a `configs/flat/` geometry as the default (`GEOMETRY=flat`), with one
brutefir conf per rate (44100…192000). No binary filter files are needed:
BruteFIR has a built-in identity coefficient —

```
coeff "c-l" { filename: "dirac pulse"; attenuation: 0.0; };
```

- `attenuation: 0.0` (not the 3.0 dB the room filters use): a flat filter has
  no boost, so no headroom is required, and 0 dB lets
  `scripts/verify-bitperfect.sh` pass through the flat chain — a nice
  self-test that the plumbing is transparent.
- Keep `filter_length` and I/O identical to the room configs so swapping in
  real filters is a filename change only.
- README: replace the room-log preamble with "ships flat; to add correction,
  drop `L.raw`/`R.raw` per rate under `filters/<name>/<rate>/` and set
  `GEOMETRY=<name>`" — FILTERS_AND_DRC.md already documents generation.

### 1.3 Runtime configuration instead of render-time baking

- drc.sh and friends read a config file at *runtime*, searched in order:
  `$OMDRC_CONF` → `${PREFIX}/etc/open-media-drc/omdrc.conf` →
  `<script-dir>/config.env` (this last keeps run-from-repo working
  unchanged).
- The values that today are `@VARS@`: `AUDIO_USER`, `AUDIO_HOME`,
  `MUSIC_DIR`, `GEOMETRY`, `SITE_DIR` (where configs/filters live),
  `FRIENDLY_NAME`, `QOBUZ_USER`.
- rc.d scripts: already rc.conf-overridable — flip the defaults so they point
  at installed paths (`%%PREFIX%%/etc/open-media-drc/...`) instead of
  `@REPO_DIR@`. install.sh keeps rendering `@REPO_DIR@` defaults for the
  run-from-repo mode; the port substitutes `%%PREFIX%%` via `SUB_FILES`.
- brutefir conf templates: drc.sh already picks the conf per rate; make it
  render `@REPO_DIR@`/`@SITE_DIR@` on the fly to a state-dir tempfile (or
  switch configs to relative includes) so packaged confs are host-neutral.

### 1.4 Move state out of the tree

`last_arg`, `last_power`, `drc.log` →
- installed mode: `/var/db/omdrc/` (services run as root today);
- run-from-repo mode: keep beside the script, as now (fallback when the
  state dir is absent/unwritable).
`/tmp/brutefir.out`, `/tmp/virtual_oss.pid` → same state/run dir.

### 1.5 Install target

Add `make install` (POSIX makefile or extend install.sh with
`DESTDIR`/`PREFIX`):

| What | Where |
|---|---|
| drc.sh, drc-status.sh | `${PREFIX}/libexec/omdrc/` + thin `${PREFIX}/bin/omdrc` wrapper |
| omdrc.conf | `${PREFIX}/etc/open-media-drc/omdrc.conf.sample` |
| flat configs, brutefir_defaults.conf | `${PREFIX}/etc/open-media-drc/…` (`.sample`) |
| devd conf | `${PREFIX}/etc/devd/omdrc-sndlink.conf` |
| hotplug reconciler | `${PREFIX}/libexec/omdrc-hotplug` |
| mpd/upmpdcli config snippets | `${PREFIX}/share/examples/open-media-drc/` |
| omdrc-ctrl (flask app) | `${PREFIX}/share/omdrc-ctrl/` (its CMake flow already close) |
| docs | `${PREFIX}/share/doc/open-media-drc/` |

rc.d scripts for *our* services (`drc_usb_audio`, `brutefir_drc`,
`omdrcctrl`, `omdrcvideo`) are installed by the port via `USE_RC_SUBR`, not
by this makefile.

### 1.6 Housekeeping

- **LICENSE**: pick one (BSD-2-Clause fits the ecosystem). Required for any
  port (`LICENSE=` is mandatory in practice).
- **Tagged releases**: `v0.x` tags; release tarball excludes
  `freebsd-*-patch/`, investigation journals, `.claude/`, site data.
  (`git archive` respects `export-ignore` in `.gitattributes`.)
- Trim README into: user-facing quickstart (port users) vs
  `doc/DEVELOPMENT.md` (run-from-repo, debugging history).

## Phase 2 — The port itself

`audio/open-media-drc` (working name; `audio/omdrc` if renamed):

- `USE_GITHUB=yes`, `GH_ACCOUNT=delleceste`, tagged `DISTVERSION`.
- `NO_BUILD` for the shell core; `USES=python:run shebangfix` for omdrc-ctrl.
- `RUN_DEPENDS`: brutefir (per Phase 0 decision), virtual_oss, musicpd,
  sox/soxr as needed by scripts.
- `OPTIONS_DEFINE`:
  - `CTRL` (web UI): +py-flask, py-Markdown, py-numpy
  - `VIDEO` (webremote): +mpv
  - `UPNP`: +upmpdcli (renderer front-end is optional)
- `USE_RC_SUBR= drc_usb_audio omdrcctrl omdrcvideo` — **not** musicpd or
  upmpdcli; instead pkg-message documents the rc.conf lines to point the
  stock scripts at our configs
  (`musicpd_config="%%PREFIX%%/etc/open-media-drc/musicpd.conf"`, etc.).
- `@sample` entries for every config; `pkg-message` with the post-install
  checklist (edit omdrc.conf, enable services, kldload cuse, replace flat
  filters).
- Validate: `portlint -AC`, `portclippy`, `poudriere testport` on the
  supported releases, `pkg check -s` after a service run (catches leftover
  in-tree writes).

## Phase 3 — Submission and maintenance

- Submit as a Bugzilla PR (product Ports & Packages, attach `diff` created
  with `git format-patch` against the ports tree), `MAINTAINER=
  delleceste@gmail.com`. Niche integration/glue ports are accepted when they
  are clean and maintained — the bar is quality, not popularity.
- Expect review rounds on rc script style, sample handling, and the brutefir
  dependency. Having the Phase 0 fixes already merged upstream is the
  strongest argument that the stack actually works on stock FreeBSD.
- Ongoing: bump the port on each release; watch fallout from
  musicpd/upmpdcli/virtual_oss updates.

## Recommended order of value

1. **Phase 0 upstreaming** — benefits every FreeBSD USB-audio user, already
   in flight, and is a hard prerequisite anyway.
2. **Phase 1.2 (flat filters)** — small, immediate, makes the repo usable by
   others today even without a port.
3. **Rest of Phase 1** — worthwhile for the repo's own health regardless of
   the port decision.
4. **Phases 2–3** — only commit once Phase 0's brutefir question is settled
   and there is evidence of an audience; a port is a maintenance promise.
