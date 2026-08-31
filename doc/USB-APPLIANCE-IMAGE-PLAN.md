# Plan: a clean, installable USB appliance image

Status: **plan only, nothing built.** Written 2026-08-26 from a walkthrough of
bee's live state plus the existing `freebsd-port` work
(`FREEBSD-PORT-PLAN.md`). Target hardware is fanless Intel/AMD-integrated
audio boxes dedicated to this appliance — **not** bee. Bee stays the
dev/measurement machine and is not being reimaged by this plan.

## Goal

A USB stick that installs a FreeBSD system with `open-media-drc` preinstalled
— no room filters, no per-geometry BruteFIR configs, no personal data, no git
history, no build artifacts — ready for a specific room's `configs/<geo>` +
`filters/<geo>` to be dropped in afterward, the same way a fresh port install
would be configured.

## Why this is a separate machine class from bee

Bee (`i3-6100U`, 4G RAM) is not the target. Checked directly on bee:

- Root is UFS (soft-updates + journaled soft-updates), **99% full** (233M
  free of 27G).
- `ada0` is a 112G disk, but FreeBSD only owns a 34G MBR slice (`ada0s4`,
  BSD-labelled 28G UFS + 5.6G swap). The other 74G is two stale
  `linux-data` partitions + linux-swap, still probed harmlessly at every boot
  (`boot-after-audit.md`'s "R/W mount denied … dirty Linux filesystems").
- Bee has already hit "Filesystem is not clean - run fsck" once
  (`boot-before-audit.md`) — the classic UFS unclean-shutdown symptom, on a
  box that in practice gets power-cycled by a wall switch/remote rather than
  a clean `shutdown`.

None of this blocks bee's current job. It's the reason the appliance image
targets its own (unspecified-yet) fanless hardware with its own filesystem
choice, rather than being "whatever bee happens to run."

## What already exists to build on

`open-media-drc` has already solved most of the hard part, mid-flight on the
`freebsd-port` branch (not yet merged to `master`; `master` is currently 4
files ahead of it uncommitted):

- **`OMDRC_SITE_DATA_DIRS` + `GEOMETRY=flat`** — a built-in generic mode.
  BruteFIR's identity coefficient (`"dirac pulse"`), no filter files needed.
  This *is* "no filters, no custom brutefir configs," already designed for.
- **`.gitattributes` `export-ignore`** already strips `filters/`,
  `configs/120.blue`, `configs/185`, every `freebsd-*-patch/`, `.claude/`,
  `config.env`, and the investigation-journal `.md` files from `git archive`
  output on a tag.
- **`make install DESTDIR=… PREFIX=…`** (Phase 1.5 of
  `FREEBSD-PORT-PLAN.md`) installs host-neutral files + `.sample` configs,
  reading config at *runtime* instead of baking `@AUDIO_USER@` at render
  time — this is what should land on the image, not bee's live CMake install
  (which bakes `host.cmake` values at configure time).
- **`omdrc-ctrl`'s phone panel already has an "app launchers" feature**
  (`commands.conf`, `[apps]` group), currently wired to `kodi` and `chrome`
  only.
- **`browser-nodrc`** (`firefox-nodrc.sh` + `.desktop`) already does the
  DRC-bypass-and-restore dance Firefox needs to get direct DAC output.

## Gaps to plan around

Phase 0 of `FREEBSD-PORT-PLAN.md` (upstreaming) hasn't landed, so the image
build has to do these itself rather than getting them from stock `pkg`:

- The patched `snd_uaudio.ko` (OKTO DAC shared-clock fix) — not in base yet.
- The `delleceste/brutefir` fork (OSS backend) — the port skeleton's
  `RUN_DEPENDS` currently points at stock `audio/brutefir`, which lacks it.
- `kodi-virtual-oss-patch` — not upstream to Kodi or `multimedia/kodi`.

## Decisions made

| | |
|---|---|
| Root fs | **ZFS**, ARC cap sized to each box's actual RAM (unlike bee, these boxes aren't fixed at 4G) |
| Desktop | **openbox and Plasma6 both installed**; SDDM session dropdown picks per box, switchable anytime by logging out. Kodi autostarts fullscreen in the openbox session with a hotkey → Konsole + a small launcher (5 apps + a file manager) for ad hoc shell/file-browsing use. Plasma session has baloo indexing and compositor effects off by default regardless of which box runs it. |
| Browser | **Firefox only** — no Chrome (not installed on bee, no Linux compat needed), no Chromium (dropped, wasn't in the original app list either). VA-API/EGL/WebRender tuning shipped via `/usr/local/lib/firefox/mozilla.cfg` + `local-settings.js` (autoconfig, applies to every profile) rather than a profile-specific `user.js`. The `media.av1.enabled=false` line is Skylake-specific (HD 520 has no AV1 hardware decode) and must be re-checked per box's actual iGPU, not copied verbatim. |
| GPU vendors | **Intel + AMD integrated only.** Fanless rules out a realistic discrete-NVIDIA target (no active cooling). Both are `drm-kmod` (`i915` / `amdgpu`) with a vendor-matched, codename-specific firmware package; Xorg stays generic (`modesetting` + `glamor`) either way, same as bee already proves for Intel. |
| Extras | Chrome/Chromium dropped (see Browser). KDE Connect kept (existing phone-side MPRIS remote for playback control; works under openbox too, not Plasma-only). |
| Test VM | **bhyve** — native to FreeBSD, and the same tool that would do real PCI passthrough later if an acceleration-layer test ever needs to borrow hardware. |

## Testing: two layers, only one is virtualizable

- **Logic layer** (fully testable in bhyve, no real GPU needed): boot, ZFS
  import, both sessions starting, rc.d services coming up clean
  (`drc_usb_audio`, `omdrcctrl`, `musicpd`, …), `commands.conf` wired
  correctly, personal data actually absent, `pkg check -s` clean. bhyve
  UEFI + `-s ...,fbuf,vnc` gives a screen over VNC with a generic
  framebuffer (no 3D) — enough to click through both sessions and confirm
  all five apps launch in software rendering.
- **Acceleration layer** (needs real hardware, not virtualizable): does
  `drm-kmod` actually load for the given vendor, does VA-API actually
  decode, does Kodi's GBM/EGL path actually hit the GPU, does WebRender
  actually use it. Needs one real Intel box and one real AMD box, once,
  before either variant ships. bhyve's `ppt` PCI-passthrough is the fallback
  if that test ever needs to run against borrowed hardware rather than a
  free-standing unit.

## Phase 1 — Freeze a clean engine build

1. Tag a release on `freebsd-port` (merge to `master` first, or tag the
   branch directly — decide once the 4 uncommitted files on `master` are
   sorted out).
2. `git archive` that tag → the tarball already stripped of site data,
   patches, journals, `.claude/`, `config.env` via existing
   `export-ignore` rules.
3. Land the Phase-0 gaps this image actually needs at build time (patched
   uaudio kmod, brutefir fork) even though they aren't upstream yet —
   documented as a known deviation from the port skeleton's `RUN_DEPENDS`,
   not a blocker.

## Phase 2 — Base OS + packages, in a reference VM

1. Fresh FreeBSD 15.1, whole-disk GPT, ZFS root, built in bhyve — never
   directly on target hardware.
2. GPU vendor detection (`pciconf -lv | grep -B3 VGA`) picks Intel or AMD,
   installs the matching `drm-kmod` submodule + codename-specific firmware
   package. Whether this runs once per box at image-customization time or
   as a first-boot script so one image serves both families unmodified is
   still open — decide when actually building this.
   - Intel firmware package pattern confirmed from bee:
     `gpu-firmware-intel-kmod-<codename>`.
   - AMD's exact port name is **not yet confirmed** — check with
     `pkg search amdgpu` rather than assuming a name.
3. `pkg install` the rest: seatd, sddm, dbus, openbox, plasma6-plasma,
   konsole, dolphin, kodi, mpv, mpv-mpris, haruna, libdvdcss, firefox,
   musicpd, libnpupnp, kdeconnect-kde.

## Phase 3 — Engine install + app wiring

1. `make install PREFIX=/usr/local GEOMETRY=flat DESTDIR=…` from the frozen
   tarball. Run `install-ctrl` too.
2. Add the missing `[apps]` entries to `commands.conf.in` (bluray,
   mpv/play-media, haruna, firefox) so the phone panel can launch all five,
   not just Kodi.
3. Desktop entries for both sessions: Kodi's own `.desktop`,
   `play-bluray.desktop`, Haruna's own, Firefox's own, and an mpv entry that
   launches `play-media.sh` — **not raw mpv**, or it bypasses DRC-correct
   routing the same way Haruna does if left at its own defaults.
4. Firefox `mozilla.cfg`/`local-settings.js` tuning (see Decisions table).

## Phase 4 — Strip personal data

No `.ssh`, `.git-credentials`, shell history, browser profiles, real
`wpa_supplicant.conf` (ship a template instead), `.claude` state, core
dumps/logs, any `.git`/`build/`/`.backup-*` directory — the last shouldn't
exist in the first place since Phase 3 is a `DESTDIR` install, not a copied
checkout.

## Phase 5 — Verify, capture, ship

1. `pkg check -s` + a full service start/stop cycle (Phase 1.4's own test
   from `FREEBSD-PORT-PLAN.md`).
2. bhyve logic-layer test (see Testing, above) before anything touches real
   hardware.
3. Snapshot the clean ZFS dataset (`zfs snapshot … @appliance-base-<date>`),
   `zfs send -R … | zstd` onto the USB stick with a small partitioning
   script.
4. Install flow for a new box: boot a minimal FreeBSD/mfsBSD USB, `gpart` +
   `zpool create` the whole target disk, `zfs receive`, `bectl activate`,
   set hostname, reboot.
5. Acceleration-layer test on one real Intel box and one real AMD box
   before either variant ships as "the image."

## Open questions

- GPU detection timing: image-customization time (per box, before shipping)
  vs. first-boot script (one image, both vendors)?
- ARC cap: no target hardware RAM figure exists yet — needs a number once a
  specific fanless box is chosen.
- AMD firmware package name: unconfirmed, needs `pkg search amdgpu` on a
  real FreeBSD box.
- `freebsd-port` branch merge: still separate from `master`, with `master`
  4 files ahead uncommitted — needs resolving before Phase 1 can tag
  anything.

## Recommended order of value

1. **Phase 1** — freezing a clean engine tarball benefits this plan and the
   port plan equally; it's shared groundwork either way.
2. **Phase 2's GPU detection** — the one piece of new engineering this plan
   adds beyond what `FREEBSD-PORT-PLAN.md` already scoped; worth building
   and testing (logic layer) before committing to real hardware purchases.
3. **Phases 3–4** — mostly wiring existing pieces (`commands.conf`,
   `.desktop` files, `mozilla.cfg`) together; low risk, do once Phase 2's
   package list is settled.
4. **Phase 5's acceleration-layer test** — gate this on actually having one
   Intel and one AMD unit in hand; don't build it speculatively.
