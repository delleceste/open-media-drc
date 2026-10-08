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
  or `cdrom` group).
* Decryption now depends on a closed binary that must be kept up to date.

`KEYDB.cfg` stays useful: on a box without MakeMKV, libbluray loads stock
libaacs and the key file as before.

## The MakeMKV beta key must be renewed

The free Linux MakeMKV runs only with a **beta registration key**. Each key
carries an expiry date, typically about a month or two ahead. When it expires,
`makemkvcon` refuses to work, `libmmbd` returns no keys and every Blu-ray stops
playing again. The symptom is the same black screen or libbluray AACS error as
without MakeMKV.

The current key is published by the MakeMKV author on the forum thread
"MakeMKV is free while in beta":
<https://forum.makemkv.com/forum/viewtopic.php?t=1053>. The post states the
date until which the key is valid, for example *"The current beta key is T-...
and is valid until end of October 2026."* A new key appears before the old one
expires.

The key belongs to **the account that runs the player**, because `libmmbd`
reads that account's MakeMKV settings, `~/.MakeMKV/settings.conf`, line
`app_Key = "T-..."`. On these boxes that is the desktop user who runs the idle
mpv, Kodi and the `omdrcvideo` web remote.

### Renewing from the video web remote

The web remote looks the key up and installs it; installing is always a click,
never automatic.

* **Blu-ray check** (menu, top right). Its first entry is the **MakeMKV beta
  key**. Every time the check opens, it fetches the forum page and shows the
  progress in that entry, then:
  * the installed key and its expiry date, with the days left;
  * the key currently published and its expiry;
  * what `makemkvcon` itself says, when it complains about an expired key or
    a MakeMKV release that is too old (not asked while a disc is playing).

  When the installed key is missing, expired, within 2 days of its expiry, or
  the page publishes a different key with a later expiry, the entry offers
  **Download and apply the new key**. **Check again** repeats the lookup.
* **Update MakeMKV key** (menu, top right) fetches the current key and applies
  it at once, showing the result.
* A **banner on the video page** appears from **7 days before the installed
  key expires** (and when no key is installed), on boxes whose players use
  `libmmbd`. Tapping it opens the Blu-ray check.

How it works (`webremote/src/lib/makemkv_key.py`):

* The page is read with regular expressions, so a small change of wording is
  tolerated: the key is the `T-...` string (preferably inside the post's code
  box), and the expiry is the phrase after *valid until / till / through*.
  Accepted dates: *end of October 2026* and *October 2026* (the last day of
  that month), *October 31, 2026*, *31st of October 2026*, *2026-10-31*. If the
  key is found but the date is not understood, the entry says so and quotes
  the phrase; if no key is found, the lookup fails with a message.
* **Every lookup saves the expiry of the key it saw**, by key fingerprint, in
  `makemkv-key.json` in the web remote's cache directory (`[thumbs]
  cache_dir`, default `~/.cache/omdrc-video`). The installed key's expiry is
  therefore known later without the network, which is what the banner uses.
* A later lookup that finds a **different key with a later date** means a new
  key is out: the entry offers it. Once applied, the installed key is the new
  one, so its recorded date becomes the next expiry.
* A key installed by hand and never seen by a lookup has an unknown expiry;
  the entry says so and offers the published key when it differs.
* **Applying** rewrites only the `app_Key` line of `settings.conf` (adding it
  or creating the file if needed), atomically, mode `0600`, keeping the old
  file as `settings.conf.previous`.

API: `GET /api/makemkv-key` (stored state only); `POST /api/makemkv-key` with
`{"op": "check"}` (look up and record) or `{"op": "apply"}` (look up, record,
install).

### By hand

* MakeMKV GUI as that user: **Help -> Register**, paste the key; or
* edit `~/.MakeMKV/settings.conf` of that user: `app_Key = "T-..."`.

`makemkvcon -r info disc:9999`, run as the same user, prints MakeMKV's
version and registration state among its first messages.

### MakeMKV itself ages out

An old MakeMKV build can refuse to run until it is updated, independently of
the key. When a new key does not help, update the MakeMKV package (Arch: the
`makemkv` AUR package).

Purchasing a MakeMKV licence key makes the registration permanent; updating
MakeMKV is still needed when a release ages out.

## How the players are told to use libmmbd

`lib/makemkv-env.sh` holds the switch, shared by every player launcher:

```sh
if [ "$(uname)" = "Linux" ] && [ -z "${LIBAACS_PATH:-}" ]; then
    for _omdrc_mmbd in /usr/lib/libmmbd.so.0 /usr/local/lib/libmmbd.so.0; do
        if [ -e "$_omdrc_mmbd" ]; then
            export LIBAACS_PATH=libmmbd LIBBDPLUS_PATH=libmmbd
            break
        fi
    done
    unset _omdrc_mmbd
fi
```

* **Linux only.** FreeBSD keeps the stock environment and `KEYDB.cfg`, by
  design (see *FreeBSD* below).
* **Only when MakeMKV is installed** (`/usr/lib/libmmbd.so.0`, where the Arch
  package puts it, or under `/usr/local`). Without it, libbluray loads stock
  libaacs and `KEYDB.cfg`.
* **Never over an explicit choice:** a `LIBAACS_PATH` already in the
  environment is kept, so `LIBAACS_PATH=libaacs play-bluray.sh` still tests the
  stock path.

### mpv

`lib/drc-audio.sh` sources `makemkv-env.sh`, and is itself sourced by all
three mpv launchers (`play-bluray.sh`, `play-media.sh`,
`webremote/mpv-idle.sh`) before mpv starts. The idle mpv inherits the
variables at startup, so a disc started from the web remote is decrypted by
`libmmbd` too. Restart the idle mpv after installing MakeMKV.

### Kodi

The panel's **Kodi** button runs `kodi.sh` (installed as
`<prefix>/lib/omdrcvideo/kodi.sh`; `commands.conf` section `[kodi]`), which
sources `makemkv-env.sh` and then executes `kodi`. On FreeBSD, or without
MakeMKV, it is plain `kodi`.

Kodi started another way (application menu, autostart) does not go through
the wrapper. For those, either start `kodi.sh` instead, or set the variables
for the whole Plasma session: put
`export LIBAACS_PATH=libmmbd LIBBDPLUS_PATH=libmmbd` in
`~/.config/plasma-workspace/env/makemkv.sh` of the desktop user and log in
again.

Avoid replacing `libaacs.so.0` / `libbdplus.so.0` with symlinks to
`libmmbd.so.0`. It is a common recipe, but it changes every program on the
box and a package update silently undoes it.

To confirm that Kodi uses MakeMKV, play a disc missing from `KEYDB.cfg` (for
example *Pulse*): it plays only through `libmmbd`.

### FreeBSD: KEYDB.cfg, by design

FreeBSD decrypts with stock libaacs and `KEYDB.cfg`, and nothing in the project
switches it to MakeMKV:

* `lib/makemkv-env.sh` exports nothing unless `uname` is `Linux`, so mpv and
  `kodi.sh` keep libaacs.
* The web remote's MakeMKV key support is Linux-only: on FreeBSD the key entry
  and **Update MakeMKV key** are hidden, `POST /api/makemkv-key` is refused,
  nothing is fetched and `makemkvcon` is never run. The Blu-ray check reports
  *Decryption: libaacs + KEYDB.cfg* and checks `KEYDB.cfg` as the primary path.

Why: MakeMKV does exist for FreeBSD (`multimedia/makemkv`), but the port builds
it with the Linux toolchain and runs it under the Linux ABI layer. Its
`/usr/local/lib/makemkv/libmmbd.so.0` is a **Linux** library, which the native
FreeBSD libbluray in mpv and Kodi cannot load, so the `LIBAACS_PATH` switch
cannot work there. MakeMKV on FreeBSD is only usable as `makemkvcon` itself
(ripping, decrypted backup, or its streaming server), which the project does
not use.

The consequence is that FreeBSD keeps both limits of libaacs: discs missing
from FindVUK do not decrypt, and a drive that rejects libaacs's host
certificate reads no AACS disc. Keep `KEYDB.cfg` current with the Blu-ray
check's upload.

## The Blu-ray check and KEYDB.cfg

Where the players use `libmmbd`, the Blu-ray check reports **MakeMKV
(libmmbd)** as the decryption path, no longer requires `libaacs` and
`libbdplus`, and shows `KEYDB.cfg` as **AACS keys (fallback)**: its problems
are listed but do not count, and the KEYDB.cfg links and upload move into a
collapsed *Fallback* section. On Linux without MakeMKV the check warns that
decryption falls back to libaacs + `KEYDB.cfg`. On FreeBSD `KEYDB.cfg` is the
decryption path and is checked as before, and the MakeMKV key entry is not
shown.
