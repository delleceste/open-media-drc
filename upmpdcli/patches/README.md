# upmpdcli patches

## `0001-carry-date-genre-and-publisher-tags.patch`

**What it fixes.** upmpdcli hands MusicPD the metadata a control point sent
it — artist, album, title, track number — and drops the rest of the DIDL. A
queue entry therefore names a *recording* but never the *issue* it came from,
and those are different things: a remastered CD and the vinyl reissue of one
record share artist, album, title and track number, and differ in year and
label. Without them the panel's [DR versions page](../../omdrc-ctrl/README.md#dr-versions-page)
can rank the pressings a record has but cannot say which one is playing.

Three fields upmpdcli *already models* never survive the trip:
`UpSong::dcdate` and `UpSong::genre` are declared and written by
`UpSong::didl()`, and genre is even read back from MPD in
`MPDCli::mapSong()` — but `dirObjToUpSong()` never fills either, so
`send_tag_data()` has nothing to send.

**What it does.** Fills `genre`, `dcdate` and a new `publisher`
(`upnp:publisher`) in `dirObjToUpSong()` from the DIDL properties libupnpp
has already parsed; sends them to MPD as `Date`, `Genre` and `Label`; reads
`Date` and `Label` back in `mapSong()`; emits `upnp:publisher` from
`UpSong::didl()` so the round trip closes. Empty values are never sent, and
`Label` sits behind `LIBMPDCLIENT_CHECK_VERSION(2,17,0)`.

**Applies to** 1.9.17, 1.9.18 and current master — the touched code is
identical across them.

### Applying it

```sh
ver=1.9.18                       # or whatever `upmpdcli --help` reports
curl -O https://www.lesbonscomptes.com/upmpdcli/downloads/upmpdcli-$ver.tar.gz
tar xf upmpdcli-$ver.tar.gz && cd upmpdcli-$ver
patch -p1 < /usr/local/share/omdrc/upmpdcli/patches/0001-carry-date-genre-and-publisher-tags.patch
meson setup build && ninja -C build && sudo ninja -C build install
sudo service upmpdcli onerestart          # Linux: sudo systemctl restart upmpdcli
```

On a distro that packages upmpdcli, the build above installs to
`/usr/local/bin` while the package sits in `/usr/bin`. Make sure the patched
one wins — `/usr/local/bin` ahead of `/usr/bin` on the service's PATH — and
hold the package so an upgrade does not quietly put the stock binary back
(`pacman -Qo $(command -v upmpdcli))` / `dpkg -S` tells you which you have).
Re-run `cmake` afterwards: both this check and the rendered service unit
resolve the binary again.

### Verifying

```sh
strings "$(command -v upmpdcli)" | grep -c upnp:publisher    # 0 = stock, 3 = patched
printf 'currentsong\nclose\n' | nc localhost 6600            # expect Date:, Label:, Genre:
```

`Label` only appears when the media server sent `upnp:publisher`. upmpdcli's
own Qobuz plugin does not (it sends `dc:date`, so the year arrives); a local
library server such as MinimServer does.

### Upstreaming

upmpdcli is developed on **Framagit**, not GitHub:
<https://framagit.org/medoc92/upmpdcli> — so this goes as a *merge request*,
not a pull request. The patch is a `git format-patch` export of a single
commit made against `master`, so it can be applied with `git am` and pushed
to a fork as-is. The commit message is written as the merge-request body:
it argues from upstream's own asymmetry rather than from this project's
needs, and mentions no part of open-media-drc.
