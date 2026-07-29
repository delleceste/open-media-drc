# video webremote

A phone-driven web remote for **mpv-only** media playback on the headless media
box. Browse the media drives, tap a movie / file / Blu-ray-rip folder to play it,
and control playback — all from an Android browser on the LAN, **no app to
install**, **nothing exposed to the internet**. The replacement for Kodi on this
box.

Design rationale lives in **[ARCHITECTURE.md](ARCHITECTURE.md)**; this file is
the practical install / run / API reference.

---

## What it does

- **Browse** the whitelisted media root(s) (default `/media/USBHD2/video`),
  classifying each entry: folder · Blu-ray rip (`BDMV/index.bdmv`) · DVD rip
  (`VIDEO_TS`) · playable file. Grid view (poster thumbnails) **switchable to a
  compact list**.
- **Thumbnails** — an ffmpeg frame grab per item, cached on disk by `path+mtime`
  (generated at most once per file). A **background prewarm** thread fills new
  files at startup so browsing is instant; bounded ffmpeg concurrency keeps the
  headless box calm.
- **IMDb info** — the title is deduced from the file/folder name and, with an
  OMDb API key, verified and enriched (year, director, cast, plot, rating) with a
  link to the real IMDb page. Without a key it falls back to a plain search link
  (no external calls).
- **Play** the selection on a **persistent idle mpv** over its JSON IPC socket
  (`loadfile`; Blu-ray rips get the genuinely-longest title; DVD rips via
  `dvd://`). The idle mpv is **hidden until something plays**.
- **Physical Blu-ray disc** — a **Play Blu-ray disc** button on the main page:
  the app sets up the gcache read-ahead in front of the slow `/dev/cd0`, picks
  the genuinely-longest title, and loads it into the **same idle mpv** over IPC
  (so the transport bar works for the disc too). A watcher tears the gcache down
  when the disc stops; **Eject** stops playback and releases the drive.
- **Transport** — a now-playing bar: seek, ±10/30 s, play/pause, mute, stop, and
  **audio / subtitle menus** (pick the audio track by language/format, choose a
  subtitle track or turn subtitles off).
- **Stereo downmix** — the DRC chain / DAC is stereo, so mpv is run with
  `audio-channels=stereo`; 5.1/7.1 sources are downmixed instead of failing to
  open a 6-channel output. (You can still select a multichannel track — it is
  downmixed for output.)
- **A/V sync fine-tune** — an **⏱ A/V sync** button in the transport bar: a
  slider centred on zero (±`avsync.range`, default ±1 s) with a linked spin box
  for an exact figure, nudged live while you watch a lip-sync scene. It trims
  mpv's **baseline** audio-delay — the DRC audio-path latency mpv was launched
  with (see [`../AV-SYNC-DELAY.md`](../AV-SYNC-DELAY.md)) — so it only moves the
  leftover buffering term. `+` delays the **audio**, `−` delays the **video**.
  The trim is remembered across restarts of mpv *and* of this app.
- **Favourites** — pin folders to the main page with a ★ toggle.

> The disc button reuses the gcache lifecycle of
> [`../play-bluray.sh`](../play-bluray.sh) (which is still the desktop/KDE-menu
> launcher) but loads into the persistent idle mpv instead of spawning its own.
> It needs the same passwordless sudo for `kldload`/`gcache`.

---

## Requirements

| Dependency | Used for |
|---|---|
| Python ≥ 3.9 + Flask | the web app |
| `mpv` (with `--input-ipc-server`, libbluray, dvdnav) | playback |
| `ffmpeg` / `ffprobe` | thumbnails |
| `bd_list_titles` (libbluray) | Blu-ray rip longest-title probe |
| OMDb API key (omdbapi.com, free) | *optional* — IMDb enrichment |

```sh
pkg install python3 py39-flask ffmpeg mpv libbluray   # FreeBSD
```

---

## Configuration — `webremote.conf`

INI, read live from this checkout (run-from-repo). Key sections:

```ini
[server]
host = 0.0.0.0          # LAN-only; never internet-exposed
port = 9080

[media]
roots = /media/USBHD2/video   # comma-separated whitelist; nothing outside is reachable

[mpv]
socket = /tmp/mpv-socket      # matches mpv-idle.sh / ../mpv/mpv.conf

[avsync]
range = 1.0              # A/V sync slider span: ±range seconds around 0
step  = 0.01             # slider / spin-box granularity

[thumbs]
cache_dir        = ~/.cache/omdrc-video
seek_percent     = 10
max_width        = 320
prewarm_interval = 900   # rescan for new files every N s (0 = startup only)
concurrency      = 2     # max simultaneous ffmpeg jobs

[disc]
enabled = yes            # show the "Play Blu-ray disc" button
device  = cd0            # raw optical device
cache   = bd             # gcache name (-> /dev/cache/bd)

[imdb]
omdb_api_key =           # set to enable verified IMDb info (titles sent to omdbapi.com)
```

Favourites persist in `<cache_dir>/favorites.json`, the A/V sync trim in
`<cache_dir>/avsync.json`.

---

## Run (development)

```sh
cd video/webremote/src
python3 app.py                      # reads ../webremote.conf → 0.0.0.0:9080
python3 app.py --config /path/to/webremote.conf --port 8080
```

Open `http://<host>:9080` on the phone.

---

## Deploy (run-from-repo)

`../../install.sh` renders the `*.in` templates here from `config.env`
(`@REPO_DIR@`, `@AUDIO_USER@`). Then, on the box:

**1. The web service (FreeBSD rc.d):**

```sh
ln -sf <repo>/video/webremote/rc.d/omdrcvideo /usr/local/etc/rc.d/omdrcvideo
sudo sysrc omdrcvideo_enable=YES
sudo service omdrcvideo start            # serves :9080
# unprivileged, no enable: /usr/local/etc/rc.d/omdrcvideo onestart
```

**2. The persistent idle mpv (KDE/Plasma autostart):**

```sh
ln -sf <repo>/video/webremote/autostart/mpv-idle.desktop \
       ~/.config/autostart/mpv-idle.desktop
```

It starts with the desktop (SDDM autologin → Plasma), owns `DISPLAY=:0` / the
projector, and stays hidden until the web app loads something. Start it once now
without relogin via `video/webremote/mpv-idle.sh`.

A `git pull` is the whole update path (`service omdrcvideo restart` to pick up
app changes).

---

## HTTP API

| Endpoint | Purpose |
|---|---|
| `GET /` | the remote UI |
| `GET /api/roots` | media roots + pinned favourites (main page) |
| `GET /api/browse?path=` | directory listing (classified, enriched entries) |
| `POST /api/play {path}` | play an item on the idle mpv |
| `POST /api/disc {op}` | play / eject the physical Blu-ray disc (`op` = `play`/`eject`) |
| `GET /api/status` | live playback state (poll ~1 s) |
| `GET /api/tracks` | audio + subtitle track lists (for the menus) |
| `GET/POST /api/avsync` | A/V sync trim on mpv's baseline audio-delay (`POST {trim}`, clamped to ±`range`) |
| `POST /api/cmd {op,value?}` | `toggle`/`pause`/`play`/`stop`/`seek`/`seekto`/`volume`/`mute`/`audio`/`sub` |
| `GET /api/thumb?path=` | cached JPEG thumbnail (404 → UI shows a type icon) |
| `GET /api/imdb?path=` | IMDb info (OMDb-enriched or search-link fallback) |
| `GET/POST/DELETE /api/favorites` | list / pin / unpin a folder |
| `POST /api/rescan` | trigger a background thumbnail prewarm pass |

---

## Security

LAN-only, never internet-exposed (same posture as `omdrc-ctrl`). Every
browse / play / thumb / imdb / favourite request is confined to the configured
roots by realpath containment — no `..` or symlink escape, no path outside the
whitelist is listed, played, or read. Paths are passed to mpv/ffmpeg as argv (no
shell interpolation).

---

## Files

```
video/webremote/
  src/app.py                   Flask app (browse / play / control / thumb / imdb / favorites)
  src/lib/
    roots.py                   whitelist + realpath containment
    classify.py                dir | bdmv | dvd | file detection
    titles.py                  deduce movie title/year; IMDb URLs
    imdb.py                    OMDb lookup + disk cache (graceful without a key)
    thumbs.py                  ffmpeg frame grabs, disk cache, bounded concurrency
    mpvipc.py                  JSON IPC client for the idle mpv
    play.py                    item -> mpv loadfile commands (BD longest-title probe)
    favorites.py               pinned-folder storage
    avsync.py                  A/V sync trim on mpv's baseline audio-delay
  src/templates/index.html     mobile-first dark UI (grid/list, transport, IMDb sheet)
  webremote.conf               live configuration
  mpv-idle.sh                  starts the hidden persistent idle mpv
  disc.sh                      gcache up/down for the physical Blu-ray drive
  rc.d/omdrcvideo.in           FreeBSD service template (rendered by ../../install.sh)
  autostart/mpv-idle.desktop.in  KDE/Plasma autostart template for mpv-idle.sh
  ARCHITECTURE.md              design rationale
```
