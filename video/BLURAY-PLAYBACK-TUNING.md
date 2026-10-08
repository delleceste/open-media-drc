# Blu-ray playback tuning: Linux findings, FreeBSD to-do

Measured on the Linux box (Arch, Samsung SE-506CB USB drive, DRC chain:
mpv -> snd-aloop Loopback -> BruteFIR 192 kHz -> DAC), 2026-10-06. Everything
under "Linux" was verified there. Everything under "FreeBSD" is an untested
hypothesis to check when tuning that side.

## Linux: what was wrong and what fixed it

| Symptom | Cause | Fix (in `lib/drc-audio.sh` / `webremote/disc.sh`) |
|---|---|---|
| Black screen, `time-pos` stuck at the first frame, even for plain files | mpv's default ALSA buffer at 192 kHz is one 32768-frame period (the Loopback maximum); it never drains through snd-aloop -> BruteFIR | `--alsa-buffer-time=800000 --alsa-periods=8` |
| ~6-8 dropped frames/s, `decoder-frame-drop-count` 0, CPU low | the Loopback pair shares BruteFIR's 32768-frame period (~0.17 s), so mpv's audio clock advances in coarse steps and `video-sync=audio` drops frames | `--autosync=30` (0 drops). Renderer, scalers, hwdec and `video-sync=display-resample` made no difference |
| Rebuffering, cache stays at 0 s, drive 2-3.5 MB/s | the drive sat at a low read speed: a plain `dd` from `/dev/sr0` gave 3.7 MB/s | `eject -x 0 /dev/sr0` (max speed): 9-10 MB/s, cache fills to ~2 min. Done in `disc.sh up` |
| Disc black/stalled, `Host key / Certificate ... has been revoked by your drive` | stock libaacs host certificate is rejected by the drive, so bus encryption fails | `LIBAACS_PATH=libmmbd LIBBDPLUS_PATH=libmmbd` (MakeMKV's `libmmbd`) |
| Read-ahead of only ~1 s | mpv defaults | 4 MiB stream buffer, 512 MiB demuxer cache, 600 s read-ahead |

How to tell the three stalls apart: `time-pos` frozen = audio path;
`frame-drop-count` rising with low CPU = audio clock jitter;
`paused-for-cache` / `demuxer-cache-state` near 0 s = drive speed.
Isolate with `ao=null`: if drops vanish, it is the audio clock.

## FreeBSD: things to check, in this order

1. **Raw drive speed.** `dd if=/dev/cd0 of=/dev/null bs=1m count=200` at a
   few offsets (the gcache read-ahead from `disc.sh` matters, so also test
   `/dev/cache/bd`). If it is far below ~9 MB/s, try raising the drive speed
   with `camcontrol` (the Linux equivalent was `eject -x 0`). Re-apply it after
   every disc change, since drives often reset it.
2. **Frame drops with the audio chain.** If drops appear with
   `frame-drop-count` rising and low CPU, try `--autosync=30` first. The
   32768-frame loopback period is Linux snd-aloop/BruteFIR specific; FreeBSD
   goes through `virtual_oss`, so the one-period buffer deadlock probably does
   not apply, but confirm that `time-pos` advances.
3. **Decryption.** The same drive may reject the stock libaacs certificate.
   Check the mpv log for `has been revoked by your drive`. If so, try MakeMKV's
   `libmmbd` with `LIBAACS_PATH` / `LIBBDPLUS_PATH` as above (needs the MakeMKV
   port and `sg`-style access to the drive: on FreeBSD that is `pass(4)`).
   Background, the beta-key renewal and Kodi: `BLURAY-DECRYPTION.md`.
4. **Cache.** `mpv/mpv.conf` already carries the large-read and read-ahead
   settings; compare with the values above.

Keep the Linux and FreeBSD launchers in step: `lib/drc-audio.sh` sets
`AO_OPTS` only for Linux today.
