# Blu-ray decryption: libaacs + KEYDB.cfg, or MakeMKV's libmmbd

A commercial Blu-ray is AACS-encrypted, and some titles add BD+. Neither mpv
nor Kodi decrypts anything itself. Both read discs through **libbluray**, and
libbluray loads the decryption code at runtime as two plugin libraries:

| Plugin | Job | Stock library | Overridden by |
|---|---|---|---|
| AACS | drive authentication, disc keys, title decryption | `libaacs` | `LIBAACS_PATH` |
| BD+ | BD+ virtual-machine fix-ups on some titles | `libbdplus` | `LIBBDPLUS_PATH` |

The variables name the library to `dlopen()` (`libmmbd` is enough; the loader
finds `libmmbd.so.0`). They are read by libbluray, not by the player, so they
work the same way for mpv, Kodi, `bd_list_titles` and anything else built on
libbluray.

## Why the stock libaacs is not enough

With the stock plugins, decryption depends on `~/.config/aacs/KEYDB.cfg` (the
FindVUK database). Two separate limits make that insufficient on this setup:

1. **Bus encryption: the drive rejects libaacs's host certificate.** Before a
   drive hands over the disc's volume ID, the host must authenticate with an
   AACS host certificate. The certificates available to libaacs are public and
   have been revoked; drives with a recent firmware MKB refuse them. The mpv
   log then says `Host key / Certificate ... has been revoked by your drive`,
   and the disc stays black **even when `KEYDB.cfg` has its key**. This is the
   failure measured on 2026-10-06 with the Samsung SE-506CB USB drive on the
   Linux box (`BLURAY-PLAYBACK-TUNING.md`).
2. **Key coverage: KEYDB.cfg only knows discs someone has submitted.** For a
   recent disc there is no public processing key for its MKB version, so
   libaacs needs that disc's own VUK in `KEYDB.cfg`. A title nobody has added
   to FindVUK does not decrypt. Example: *Pink Floyd --- Pulse* (Blu-ray) is not
   in the current database. Refreshing `KEYDB.cfg` does not help until someone
   submits it.

**MakeMKV's `libmmbd`** removes both limits. It exports the libaacs and
libbdplus interfaces, so libbluray can load it in place of either. Behind them
it runs MakeMKV's own engine (`makemkvcon`), which authenticates to the drive
in a way current firmware accepts and derives the disc keys itself instead of
looking them up in `KEYDB.cfg`. It also covers BD+.

For **physical discs on this drive** MakeMKV is therefore required, not a
convenience. Without it, problem 1 blocks every AACS disc. Even on a drive
that accepted the libaacs certificate, problem 2 would block every disc missing
from FindVUK.

Costs, all accepted:

* MakeMKV is proprietary. The Linux build is free while it is "in beta", but
  only with a **beta key that expires** (next section).
* `libmmbd` needs raw SCSI access to the drive: the `sg` device on Linux, so
  the playing user must be able to open `/dev/sg*` (normally the `optical`
  or `cdrom` group); `pass(4)` on FreeBSD.
* Decryption now depends on a closed binary that must be kept up to date.

`KEYDB.cfg` stays useful: on a box without MakeMKV, libbluray loads stock
libaacs and the key file as before.

## The MakeMKV beta key must be renewed

The free Linux MakeMKV runs only with a **beta registration key**. Each key
carries an expiry date, typically about a month or two ahead. When it expires,
`makemkvcon` refuses to work, `libmmbd` returns no keys and every Blu-ray stops
playing again. The symptom is the same black screen or libbluray AACS error as
without MakeMKV. Nothing on the box renews the key automatically.

The current key is published by the MakeMKV author on the forum thread
"MakeMKV is free while in beta":
<https://forum.makemkv.com/forum/viewtopic.php?t=1053>. The post states the
date until which the key is valid; a new key appears before the old one
expires.

The key belongs to **the account that runs the player**, because `libmmbd`
reads that account's MakeMKV settings. On these boxes that is the desktop user
who runs the idle mpv and Kodi, the same account whose `KEYDB.cfg` the
web remote checks. To install a new key, either:

* open the MakeMKV GUI as that user, **Help -> Register**, and paste the key; or
* edit `~/.MakeMKV/settings.conf` of that user and set the line

  ```
  app_Key = "T-...the key from the forum..."
  ```

  creating the file if it does not exist.

Check it by running, as the same user, `makemkvcon -r info disc:9999`. The
first messages give MakeMKV's version and registration state; an expired key or
a MakeMKV release that is too old is reported there.

MakeMKV releases also age out: an old build can refuse to run until it is
updated, independently of the key. When a key renewal does not help, update
the MakeMKV package (Arch: the `makemkv` AUR package).

A renewal therefore belongs to the box's routine maintenance, roughly monthly:

1. Check the expiry date of the installed key (forum post, or `makemkvcon`
   output).
2. Before it expires, install the new key for the player account.
3. Occasionally update MakeMKV itself.

Purchasing a MakeMKV licence key makes the registration permanent and removes
step 2; updating MakeMKV is still needed when a release ages out.

## How the players are told to use libmmbd

### mpv (done)

`lib/drc-audio.sh` is sourced by all three mpv launchers (`play-bluray.sh`,
`play-media.sh`, `webremote/mpv-idle.sh`) before mpv starts, and exports:

```sh
if $IS_LINUX && [ -z "${LIBAACS_PATH:-}" ] && [ -e /usr/lib/libmmbd.so.0 ]; then
    export LIBAACS_PATH=libmmbd LIBBDPLUS_PATH=libmmbd
fi
```

* **Linux only.** FreeBSD keeps the stock environment until MakeMKV is tested
  there.
* **Only when MakeMKV is installed** (`/usr/lib/libmmbd.so.0`, where the Arch
  package puts it). Without it, libbluray loads stock libaacs and `KEYDB.cfg`.
* **Never over an explicit choice:** a `LIBAACS_PATH` already in the
  environment is kept, so `LIBAACS_PATH=libaacs play-bluray.sh` still tests the
  stock path.

Because the idle mpv inherits the variables at startup, a disc started from
the web remote is decrypted by `libmmbd` too. Restart the idle mpv after
installing MakeMKV.

### Kodi (not wired by the install)

Kodi uses the same libbluray, so the same two variables switch it to
`libmmbd`. The project does not set them for Kodi yet: the panel launches it as
plain `kodi` (`omdrc-ctrl/src/commands.conf.in`, section `[kodi]`). Until it
does, Kodi uses stock libaacs and `KEYDB.cfg`, with both limits above. Give
Kodi the variables in one of these ways:

* **Panel launcher (preferred).** Change the `[kodi]` command to

  ```ini
  cmd    = env LIBAACS_PATH=libmmbd LIBBDPLUS_PATH=libmmbd kodi
  ```

* **Whole Plasma session.** Put the two `export` lines in
  `~/.config/plasma-workspace/env/makemkv.sh` of the desktop user. Every
  program started from the session gets them, including Kodi started from the
  application menu. Log out and in again.
* **systemd unit**, if Kodi runs as a service:
  `Environment=LIBAACS_PATH=libmmbd LIBBDPLUS_PATH=libmmbd`.

Avoid replacing `libaacs.so.0` / `libbdplus.so.0` with symlinks to
`libmmbd.so.0`. It is a common recipe, but it changes every program on the
box and a package update silently undoes it.

To confirm that Kodi uses MakeMKV, play a disc missing from `KEYDB.cfg` (for
example *Pulse*): it plays only through `libmmbd`.

### FreeBSD

Untested. `/dev/cd0` must be read through libbluray in mpv anyway (Kodi cannot
read a physical Blu-ray on FreeBSD, see `README.md`). If the drive reports the
revoked certificate there too, MakeMKV would need a FreeBSD build of
`makemkvcon`/`libmmbd` and `pass(4)` access to the drive; the guard in
`lib/drc-audio.sh` would then be extended to the library's FreeBSD path.
Until then FreeBSD uses stock libaacs and `KEYDB.cfg`.

## Known gap: the web remote's Blu-ray check

**Blu-ray check** in the video web remote inspects `KEYDB.cfg` and the stock
libraries only. It does not know about `libmmbd` or the MakeMKV key, so on a
MakeMKV box it can warn about an old `KEYDB.cfg` that no longer matters, and
it does not warn when the beta key is about to expire.
