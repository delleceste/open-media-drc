# video — Blu-ray / DVD playback on the FreeBSD media box

Companion to the audio DRC side of open-media-drc. This covers playing **physical
Blu-ray discs and DVDs**, **local files**, and **network/stream URLs** on FreeBSD,
with audio routed through the same virtual_oss/brutefir DRC chain and remote
control from your phone via KDE Connect.

Same philosophy as the rest of the repo: **run-from-repo**. The live files
(`~/play-bluray.sh`, `~/play-media.sh`, `~/.config/mpv/*`) are symlinks into this
directory, so a `git pull` is the whole update path.

> **Browse-and-play from the phone:** for a full **web remote** that browses the
> media drives, shows thumbnails + IMDb info, and drives a persistent mpv (the
> mpv-only Kodi replacement), see [`webremote/`](webremote/README.md). The
> KDE Connect remote described below controls *whatever is already playing*; the
> web remote is what *starts* a title.

---

## TL;DR

```sh
~/play-bluray.sh                # disc: auto-selects & plays the longest title
~/play-bluray.sh bd://mpls/31   # disc: a specific title/playlist
~/play-media.sh movie.mkv       # a local file
~/play-media.sh "https://…/stream.m3u8"   # a network / stream URL
```
or launch **Play Blu-ray** from the KDE menu, or right-click a file →
**Open With → Play (DRC audio)**. **Remote:** control whatever is playing from the
**KDE Connect** app on your phone (play/pause/seek/volume/metadata) — exposed via
mpv-mpris on D-Bus. No web page; nothing to install beyond KDE Connect.

---

## Why mpv and not Kodi (for *physical* Blu-ray)

Kodi's internal player **cannot** read a physical Blu-ray on FreeBSD:

- `/dev/cd0` is a raw character device: it only accepts **2048-byte sector-aligned
  reads**, but Kodi's UDF VFS issues byte-granular reads → `index.bdmv` fails to
  open → playback aborts.
- The kernel can't mount the disc either: Blu-ray video is **UDF 2.50** (metadata
  partition), which FreeBSD's `mount_udf` rejects (`Invalid argument`).
- Ripping to an ISO to sidestep both isn't viable (BD-50 = ~45 GB).

mpv + **libbluray** read the raw device directly (libbluray does its own
sector-aligned UDF parsing + AACS), which is the only thing that works here.

> **Local Blu-ray *files* on a mounted disk (NTFS USB, etc.) are different** —
> there Kodi's internal player works fine (normal file I/O, no alignment limit;
> AACS decrypted via `~/.config/aacs/KEYDB.cfg`). This whole song-and-dance is
> only for *physical* discs.

## The slow-drive fix: gcache read-ahead

The USB BD drive sustains ~9.5 MB/s **only when read in ~1 MB chunks**. libbluray
reads in small pieces, and FreeBSD's raw `/dev/cd0` has **no kernel read-ahead**
(unlike Linux's `/dev/sr0` block device), so those small reads crawl at ~3 MB/s
and playback stutters. `play-bluray.sh` fronts the drive with a GEOM read-ahead
cache:

```sh
gcache create -b 1048576 -s 268435456 bd cd0   # -> /dev/cache/bd, 1 MB blocks
mpv --bluray-device=/dev/cache/bd ...
```

This turns libbluray's tiny reads into 1 MB device reads → ~6–9 MB/s effective,
plenty for Blu-ray. The script sets the cache up and tears it down on exit.
(gcache blocks are capped at 1 MB by `kern.maxphys`; raise that in
`/boot/loader.conf` + reboot if you ever need more.)

## Title selection

mpv has **no Blu-ray menu support** (`bd://menu` fails), and `bd://longest` just
plays the disc's *default* playlist (often the wrong/short one). So the launcher
probes `bd_list_titles` and plays the **genuinely longest** title via
`bd://mpls/<n>`. Override with an argument. At runtime, `e` / `E`
(see `mpv/input.conf`) cycle through all titles.

## Audio (DRC-aware)

Both launchers share `lib/drc-audio.sh`. Before playing it **ensures the DRC chain
is in `resamp` mode** (`drc.sh resamp`, only if `drc-status.sh` isn't already
reporting auto-resample). This is deliberate: the direct DAC is **bit-perfect**
(`bitperfect=1` on the DAC, no resampling), so a 48 kHz movie on a higher-clocked
DAC plays **~2× fast**. Routing through virtual_oss/brutefir in resamp mode
resamples everything to 192 kHz — correct speed *and* room correction. Export
`DRC_SKIP_RESAMP=1` to bypass and use the bare DAC. The helper then picks:

| state | mpv audio device | video delay |
| ----- | ---------------- | ----------- |
| `virtual_oss` running + `/dev/dsp.play` exists | `oss//dev/dsp.play` | `-0.67 s` (delays video to match the DRC audio-path latency) |
| otherwise | `oss//dev/dsp.dac` (direct DAC; `/dev/dsp0` without `omdrc_sndlink`) | none |

`DRC_VIDEO_DELAY` (top of the script) defaults to **0.67 s**, derived not guessed:

- **Filter group delay = exactly 0.500 s.** The impulse peak in
  `filters/120.blue/192000/{L,R}.raw` (524 288 × FLOAT64 taps) is at sample
  **96000** → 96000 / 192000 = 0.5000 s. Recompute for any filter set with the
  one-liner below.
- **+ brutefir partition latency = 0.171 s** (one `filter_length` partition,
  32768 smp @ 192 kHz) — unavoidable in uniform partitioned convolution.
- **+ virtual_oss buffering** (`-s 200ms` loop) adds a little more.

Drop to `0.5` to compensate the filter only; fine-tune live with mpv `z` / `Z`.
virtual_oss handles sample-rate conversion, so mpv outputs the disc's native rate.

> **Full derivation — theory + code, from first principles:**
> [**AV-SYNC-DELAY.md**](AV-SYNC-DELAY.md). Explains FIR impulse response, why the
> coefficient peak is the group delay, the Python line by line, and the brutefir
> partition / virtual_oss latencies.

```sh
# group delay of a brutefir FLOAT64 filter, in seconds:
python3 -c 'import array;a=array.array("d");a.frombytes(open("filters/120.blue/192000/L.raw","rb").read());i=max(range(len(a)),key=lambda k:abs(a[k]));print(i,"samples =",i/192000,"s")'
```

**Subtitles** stay in sync automatically: mpv references both `--audio-delay` and
`--sub-delay` to the *video*, and the DRC trick shifts only the audio — so subs
ride with the delayed picture. Hence `DRC_SUB_DELAY=0` (the congruent value).
It's exposed as a knob only for tuning; live-nudge with mpv's `z` / `Z` if needed.

## Local files & URL streams

`play-media.sh` plays anything mpv can open — local files, playlists, and network
URLs (`http(s)`, `m3u8`, and most streaming sites via `yt-dlp`) — with the **same
DRC audio routing** as the disc launcher but **no gcache** (that's only for the
raw optical disc):

```sh
~/play-media.sh /path/movie.mkv
~/play-media.sh ~/Videos/*.mkv                  # several files = a playlist
~/play-media.sh "https://host/live/stream.m3u8"
~/play-media.sh "https://www.youtube.com/watch?v=…"   # needs yt-dlp
```

Prefer a **GUI**? **Haruna** (KDE's Qt mpv player, built on `mpvqt`) gives an
open-file dialog, open-URL, and playlists, with its own MPRIS (so KDE Connect
controls it too). For DRC-correct audio in Haruna, set its audio output device to
the OSS `dsp.play` device and an audio delay of `-0.67 s` in its settings — or use
`play-media.sh`, which does that automatically. Haruna is best when DRC is off /
for casual browsing; `play-media.sh` is the DRC-correct path.

## DVDs

Unlike Blu-ray, DVDs **do** mount on FreeBSD (UDF 1.x/ISO9660) and are low-bitrate,
so the read-speed problem doesn't apply. Play with:

```sh
mpv dvd:// --dvd-device=/dev/cd0     # mpv auto-selects the longest title
```

Commercial (CSS) DVDs need **`libdvdcss`** (see dependencies). Local DVD files
(VIDEO_TS / ISO) play in Kodi too, with menus (libdvdnav), once `libdvdcss` is in.

## Remote control: mpv-mpris + KDE Connect

The `mpv-mpris` package drops `mpris.so` into mpv's system script dir
(`/usr/local/etc/mpv/scripts/`), which mpv **auto-loads** — so every mpv launched
here (disc, file, URL) registers `org.mpris.MediaPlayer2.mpv` on the D-Bus session
bus. Nothing to add to `mpv.conf`.

From there you get remote control for free:

- **KDE Connect** (already installed) — its Android app's *Media control* drives
  any MPRIS player over the LAN: play/pause/seek/volume/track metadata.
- the **Plasma** media widget / lock-screen controls, and `playerctl` from a shell.

Verify it's live while something plays:

```sh
qdbus6 org.mpris.MediaPlayer2.mpv /org/mpris/MediaPlayer2 \
       org.mpris.MediaPlayer2.Player.PlaybackStatus      # -> Playing
```

> History: an earlier attempt used the JS Android app "mpv-remote" (this mpv has
> no JavaScript) and then the Lua `simple-mpv-webui` browser remote (worked, but
> ugly). Both were dropped in favour of mpv-mpris once it was confirmed this mpv
> **does** support C plugins.

---

## Dependencies

| Package / asset | Used for | Status |
| --- | --- | --- |
| `mpv` (libbluray, luajit, dvdnav) | playback | installed |
| `libbluray` `libaacs` `libbdplus` `libudfread` | BD read + AACS | installed |
| `~/.config/aacs/KEYDB.cfg` | AACS decryption keys | present |
| `geom_cache` (kld) | read-ahead cache | base system |
| `mpv-mpris` | remote control (MPRIS/D-Bus) | installed |
| `kdeconnect-kde` | phone remote front-end | installed |
| `haruna` + `mpvqt` | optional GUI player (files/URLs) | installed |
| `libdvdcss` | commercial DVD decryption | **install (for DVDs)** |

```sh
pkg install mpv-mpris haruna libdvdcss   # libdvdcss only needed for commercial DVDs
```

## Deploy (run-from-repo)

`install.sh` (repo root) renders `play-bluray.desktop.in` and `play-media.desktop.in`.
Live links:

```sh
H=$HOME
ln -sf "$H/open-media-drc/video/play-bluray.sh"      "$H/play-bluray.sh"
ln -sf "$H/open-media-drc/video/play-media.sh"       "$H/play-media.sh"
ln -sf "$H/open-media-drc/video/mpv/mpv.conf"        "$H/.config/mpv/mpv.conf"
ln -sf "$H/open-media-drc/video/mpv/input.conf"      "$H/.config/mpv/input.conf"
ln -sf "$H/open-media-drc/video/play-bluray.desktop" "$H/.local/share/applications/play-bluray.desktop"
ln -sf "$H/open-media-drc/video/play-media.desktop"  "$H/.local/share/applications/play-media.desktop"
```

`mpv-mpris` needs no linking — mpv auto-loads `mpris.so` from
`/usr/local/etc/mpv/scripts/` (installed by the package).

## Files

```
video/
  play-bluray.sh            disc launcher: gcache + DRC audio + longest-title + mpv
  play-media.sh             file/URL launcher: DRC audio + mpv (no gcache)
  play-bluray.desktop.in    KDE launcher template (rendered by ../install.sh)
  play-media.desktop.in     KDE "Open With" template (rendered by ../install.sh)
  lib/
    drc-audio.sh            shared DRC-aware audio routing (sourced by both)
  mpv/
    mpv.conf                vo=gpu+opengl, large cache/read buffer, IPC socket
    input.conf              e / E cycle Blu-ray titles (editions)
  AV-SYNC-DELAY.md          theory + code behind the DRC video-delay figure
```
