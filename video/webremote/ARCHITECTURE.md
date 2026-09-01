# video webremote — architecture

A phone-driven web remote for **mpv-only** media playback on the headless FreeBSD
media box. Browse the media drives, tap a movie / file / Blu-ray-rip folder to
play it, and control playback (pause / seek / volume / tracks) — all from an
Android browser on the LAN, with **no app to install** and **nothing exposed to
the internet**.

This is the plan for moving day-to-day video playback **away from Kodi to mpv**.
It is the design document only; no code is written yet.

---

## Why this, and why it fits what's already here

The repo already establishes every pattern this needs:

- **`omdrc-ctrl/`** — a Flask web control panel served from this host on the LAN
  (`0.0.0.0:9090`), mobile-first dark UI, **no app install**, deployed as a
  FreeBSD `rc.d` service (`daemon(8)`), run-from-repo. Its own README states the
  security model plainly: *trusted local network only, never bind to a public
  interface.* The video remote reuses this skeleton verbatim.
- **`video/play-media.sh` / `video/play-bluray.sh`** — already launch mpv with
  **DRC-correct audio** (resamp mode, `oss//dev/dsp.play`, `-0.67 s` video
  delay), the gcache read-ahead for physical discs, and the longest-title probe.
- **mpv already exposes a JSON IPC socket** at `/tmp/mpv-socket`
  (`input-ipc-server` in `video/mpv/mpv.conf`), *and* mpv-mpris for KDE Connect.

So the controller skeleton, the LAN security model, the DRC audio routing, and a
control surface (IPC socket) **already exist**. The new work is a **file
browser** plus a thin **play/transport bridge** to mpv.

> KDE Connect (the current remote, per `video/README.md`) gives play / pause /
> seek / volume / metadata for *whatever is already playing* — but it cannot
> **browse the drives and start a title**. That gap is exactly what this app
> fills. KDE Connect / MPRIS remain available as a fallback transport.

---

## Decisions locked in

| Decision | Choice | Rationale |
|---|---|---|
| How the app drives mpv | **One persistent idle mpv, driven over the JSON IPC socket** | Instant loads, reliable transport & status from a single known socket, no process-spawn races. DRC audio device + `-0.67 s` delay are set **once** at startup because video always runs in resamp mode. Even physical discs load into this same mpv — only their gcache read-ahead lifecycle is managed alongside (`disc.sh` up/down). |
| Where it lives | **Separate Flask app under `video/webremote/`**, its own port, linked from `omdrc-ctrl` | Keeps video concerns in `video/`, independent update cadence, reuses the `rc.d` / run-from-repo skeleton. |
| Browser richness (v1) | **Folder browsing + file/rip detection + thumbnails + metadata + transport/live status** | Thumbnails/metadata are cached on disk so the headless box isn't hammered on every browse. |
| Whitelisted media roots | **`/media/USBHD2/video` only** (for now) | Single root keeps the safe-path surface minimal; more roots are a one-line config addition later. |

> **Resolved:** the open concern with the persistent-idle model was whether the
> idle instance stays visible on the projector. It does **not** — with
> `--idle=yes` and no `--force-window`, mpv shows no window while idle (see
> [The persistent idle mpv](#the-persistent-idle-mpv)). With that settled, the
> persistent-idle model is adopted. The alternative was *launch-per-file* (each
> play spawns `play-media.sh`), which reuses the scripts as-is but makes
> transport control racier (it acts on whatever mpv is currently running).

---

## High-level architecture

```
 Android phone (Chrome, LAN only)
        │  HTTP  (no app install, no internet exposure)
        ▼
 Flask app: video/webremote  ── reads ONLY /media/USBHD2/video (whitelist + realpath containment)
        │                     ── POST /api/play → dispatch by item type
        │                     ── transport/status → mpv JSON IPC
        ├─ loadfile / set_property ───────────────► /tmp/mpv-socket
        │                                                  │
        │   (physical Blu-ray) disc.sh up/down ── gcache on /dev/cd0, then loadfile bd://
        ▼                                                  ▼
 one persistent mpv  (--idle, hidden until play, DRC audio set once at startup)
        │
        ▼
 virtual_oss → brutefir → DAC  +  HDMI → projector
```

Three pillars: **browse**, **play**, **control**.

---

## Pillar 1 — Browsing

Flask endpoints list directories **under the whitelist only**
(`/media/USBHD2/video`), never above it. Every request resolves the requested
path with `os.path.realpath` and verifies it is still inside an allowed root —
this blocks `..` traversal and symlink escapes. The file-serving nature of this
app makes that check stricter than `omdrc-ctrl`'s command model.

Each listing entry is **classified server-side** so the UI knows what a tap does:

| Detected as | Test | Play action |
|---|---|---|
| Blu-ray rip | folder contains `BDMV/index.bdmv` | `set bluray-device <folder>` + `loadfile bd://`, longest title |
| DVD rip | folder contains `VIDEO_TS` | `loadfile dvd://` with `--dvd-device=<folder>` |
| Playable file | extension in `{mkv,mp4,m2ts,ts,avi,mov,webm,…}` | `loadfile <path>` |
| Directory | anything else that is a dir | descend into it |

A `GET /api/roots` returns the configured root(s) as jump shortcuts (today just
the one).

---

## Pillar 2 — Playing

A single `POST /api/play {path}` dispatches by the classification above:

- **file** → `loadfile <path>` over IPC.
- **BDMV folder** → `set bluray-device <folder>`, then `loadfile bd://`. The
  longest-title probe (`bd_list_titles`) is reused from `play-bluray.sh` so the
  right `.mpls` is selected, not the disc default.
- **physical Blu-ray disc** → `disc.sh up` creates the gcache read-ahead in
  front of `/dev/cd0`, the longest title is probed on `/dev/cache/<cache>`, then
  `set bluray-device` + `loadfile bd://` go to the **same idle mpv** over IPC. A
  watcher thread runs `disc.sh down` once the disc stops (idle, or switched to
  another item); **eject** stops playback and tears the cache down. So the disc
  plays in the persistent mpv too — the transport bar works for it.

### The persistent idle mpv

Started at session login (the box autologins to X on the projector):

```sh
mpv --idle=yes --fs \
    --input-ipc-server=/tmp/mpv-socket \
    --ao=oss --audio-device=oss//dev/dsp.play \
    --audio-channels=stereo \
    --audio-delay=-0.67 --sub-delay=0
```

`--audio-channels=stereo` is required: the DRC chain / DAC is stereo, so without
it mpv repeatedly tries (and fails) to open a 6-channel output for 5.1/7.1
sources. Multichannel tracks are downmixed for output; the user can still pick
any audio/subtitle track from the web menus (`/api/tracks` + `/api/cmd`).

**The idle instance is hidden when nothing is playing.** With `--idle=yes` and
**no `--force-window`**, mpv keeps the process and IPC socket alive but creates
**no window** while idle — the projector shows the desktop/blank. The window
appears (fullscreen) only on `loadfile`, and is **torn down again when playback
ends and mpv returns to idle**. This is the deliberate opposite of
`--force-window=immediate`, which would paint a black mpv window on the projector
the whole time it is idle.

> **Keep `keep-open` at its default (`no`).** With `keep-open=yes` mpv pauses on
> the last frame at end-of-file instead of returning to idle, which would leave
> the window (and last frame) on the projector. The default returns to idle so
> the window hides.

The only cost versus a permanently-shown window is a fraction-of-a-second window
creation on each load — still faster than a cold mpv spawn, since the process and
config are already up.

The audio device / delay values come from the same logic as
`video/lib/drc-audio.sh` (resamp mode, `/dev/dsp.play`, `-0.67 s`). They are set
**once** here because video playback always forces resamp mode; there is no need
to recompute per file. Everything after startup is `loadfile` / `set_property`
over the socket — no respawning, so transport and status are always against one
known process.

---

## Pillar 3 — Controlling

Transport and live status use the **mpv JSON IPC socket** (`/tmp/mpv-socket`),
which is richer than MPRIS (exact seek, track lists, chapters):

- **Status** (`get_property`): `pause`, `time-pos`, `duration`, `media-title`,
  `track-list`, `chapter`, `volume`. Polled by the UI roughly every second.
- **Commands** (`set_property` / `command`): play/pause, seek (relative &
  absolute), stop, volume, audio-track, subtitle-track, chapter next/prev,
  quit-back-to-idle.

KDE Connect / MPRIS stay available as a fallback for casual control.

---

## HTTP API (planned)

| Endpoint | Purpose |
|---|---|
| `GET /` | the remote UI (mobile-first dark page, matches `omdrc-ctrl`) |
| `GET /api/roots` | configured preferred/default folders (jump shortcuts) |
| `GET /api/browse?path=` | listing under the whitelist: dirs + playable files, each folder tagged `bdmv` / `dvd` / `dir` |
| `POST /api/play {path}` | dispatch by item type (file / BDMV / DVD / disc) |
| `GET /api/status` | `get_property` snapshot (state, position, duration, title) |
| `GET /api/tracks` | audio + subtitle track lists (language/format/channels) |
| `POST /api/cmd {op,…}` | pause, seek, stop, volume, mute, audio/sub track |
| `GET /api/thumb?path=` | cached frame-grab image (404 → UI shows a type icon) |
| `GET /api/imdb?path=` | OMDb-enriched IMDb info (or search-link fallback) |
| `GET/POST/DELETE /api/favorites` | list / pin / unpin a folder on the main page |
| `POST /api/rescan` | trigger a background thumbnail prewarm pass |

All JSON except `/`, `/api/thumb` (image), and asset routes.

---

## Thumbnails & metadata

- **Thumbnail** — a frame grab (~10 % into the file) via `ffmpeg` /
  `ffmpegthumbnailer`. BDMV rips grab from the longest title's `.m2ts`.
- **Metadata** — `ffprobe -show_format -show_streams` → duration, resolution,
  video/audio codecs, audio & subtitle languages.
- **Both cached on disk**, keyed by `path + mtime` (e.g.
  `~/.cache/omdrc-video/`) — generated **at most once per file version**, never
  on every view. This matters on the headless box.
- **Background prewarm**: a daemon thread walks the roots at startup (and every
  `prewarm_interval` s; rip folders are treated as leaves) and generates only the
  *missing* thumbnails, so a freshly-opened folder is instant rather than firing
  `ffmpeg` on demand. A bounded semaphore (`concurrency`) + per-key locks keep
  the prewarm thread and live web requests from running duplicate or excessive
  `ffmpeg` jobs. `POST /api/rescan` (and the header ⟳ button) triggers a pass
  manually; it's a no-op while one is already running.

## Favourites

Folders can be pinned to the main page via a ★ toggle on any directory entry
(`POST`/`DELETE /api/favorites`, validated inside the roots whitelist). The list
persists in `<cache_dir>/favorites.json`; the main page (`/api/roots`) returns a
**Favourites** section above the media roots. Stale entries (moved/deleted, or no
longer inside a root) are filtered out on read.

---

## Configuration (`webremote.conf.in`)

Rendered at install time, same as `omdrc-ctrl`'s templates:

```ini
[server]
host = 0.0.0.0          ; LAN-only network; never internet-exposed
port = 9080

[media]
roots = /media/USBHD2/video      ; whitelist; comma-separated when more are added

[thumbs]
cache_dir   = ~/.cache/omdrc-video
seek_percent = 10
max_width    = 320
```

---

## Deployment (run-from-repo, mirrors omdrc-ctrl)

- FreeBSD `rc.d` service via `daemon(8)` (`rc.d/omdrcvideo.in`), runs as the
  desktop user with `DISPLAY=:0` and `/usr/local/{s,}bin` on `PATH` — exactly the
  pattern in `omdrc-ctrl/rc.d/omdrcctrl.in`.
- The box logs into **KDE Plasma via SDDM** (autologin). The persistent idle mpv
  is started as a **Plasma session autostart** entry
  (`~/.config/autostart/mpv-idle.desktop` → symlink to the CMake-installed
  `$PREFIX/share/omdrcvideo/autostart/mpv-idle.desktop` → runs `mpv-idle.sh`), so
  it comes up with the desktop, owns `DISPLAY=:0` / the projector, and the web
  app only talks to its socket.
- Linked from the `omdrc-ctrl` panel (a `LINK` widget to `http://<host>:9080`),
  so the phone has one entry point for both audio DRC and video.
- `git pull` is the whole update path, consistent with the rest of the repo.

---

## Security

- **LAN-only**, never internet-exposed — same rule as `omdrc-ctrl`. Bind to the
  LAN interface; rely on the existing no-inbound firewall posture.
- **Strict roots whitelist with realpath containment** on every browse / play /
  thumb / meta request — no path outside `/media/USBHD2/video` can be listed,
  played, thumbnailed, or read. This is the key hardening beyond `omdrc-ctrl`,
  because this app serves file content and metadata rather than fixed commands.
- No shell-string interpolation of user paths: play/IPC calls pass paths as
  argv / JSON values, not via `shell=True`.

---

## Build order / status

1. ✅ **Skeleton + browse** — Flask app, roots whitelist + safe-path resolver,
   `/api/browse`, `/api/roots`, classification, mobile UI.
2. ✅ **Idle mpv + play/control** — `mpv-idle.sh` (hidden idle), `lib/mpvipc.py`,
   `lib/play.py` (BD longest-title probe), `/api/play`, `/api/status`,
   `/api/cmd`; now-playing transport bar.
3. ✅ **Thumbnails + IMDb** — `lib/thumbs.py` (`ffmpeg`/`ffprobe`, disk cache),
   `/api/thumb`; grid/list toggle; `lib/titles.py` title deduction; `lib/imdb.py`
   OMDb enrichment (year/director/cast/plot/rating + verified link, cached),
   `/api/imdb`, details sheet. Needs an OMDb key for rich info; falls back to a
   search link without one.
4. ✅ **Packaging** — CMake renders and installs everything here:
   `rc.d/omdrcvideo.in` (FreeBSD service) or the
   `systemd --user` unit, and `autostart/mpv-idle.desktop.in` → the KDE/Plasma
   autostart for `mpv-idle.sh`, installed on both OSes under
   `$PREFIX/share/omdrcvideo/autostart/` and linked into `~/.config/autostart/`
   by `make user-install`. Plus `README.md` and a chapter + links in the main
   project README.

---

## Open questions to settle before coding

1. ~~Persistent idle mpv vs. launch-per-file~~ — **settled: persistent idle mpv**
   (hidden while idle, no `--force-window`).
2. ~~Port~~ — **settled: `9080`** (next to `omdrc-ctrl`'s `9090`).
3. ~~Idle-mpv autostart mechanism~~ — **settled: KDE Plasma session autostart**
   (`~/.config/autostart/mpv-idle.desktop`), since the box logs into KDE via
   SDDM. mpv comes up with the desktop and owns `DISPLAY=:0` cleanly.
</content>
</invoke>
