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

**What it does.** Fills `genre`, `dcdate` and a new `publisher` in
`dirObjToUpSong()` from the DIDL properties libupnpp has already parsed;
sends them to MPD as `Date`, `Genre` and `Label`; reads `Date` and `Label`
back in `mapSong()`; emits `dc:publisher` from `UpSong::didl()` so the round
trip closes. The publisher is read from **`dc:publisher`** — the DIDL-Lite
standard, and what control points actually send — falling back to the
non-standard `upnp:publisher` some servers use. Empty values are never sent,
and `Label` sits behind `LIBMPDCLIENT_CHECK_VERSION(2,17,0)`.

It also patches upmpdcli's own **Qobuz plugin**, which serves a record's
label nowhere: `Album` gains a `label`, `_parse_album()` takes it from the
API's `album.label.name`, and `trackentries()` emits `dc:publisher` beside
the `dc:date` it already writes. So the label arrives whether it comes from
the control point or from the plugin.

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
strings "$(command -v upmpdcli)" | grep -c dc:publisher     # 0 = stock, >0 = patched
printf 'currentsong\nclose\n' | nc localhost 6600            # expect Date:, Label:, Genre:
```

`Label` appears when the control point sent `dc:publisher` — many do, and
upmpdcli's metacache is the place to check (`grep -o '<dc:publisher>[^<]*'
~/.cache/upmpdcli/metacache`) — or when the track came from the patched Qobuz
plugin.

### Upstreaming

upmpdcli is developed on **Framagit**, not GitHub:
<https://framagit.org/medoc92/upmpdcli> — so this goes as a *merge request*,
not a pull request. The patch is a `git format-patch` export of a single
commit made against `master`, so it can be applied with `git am` and pushed
to a fork as-is. The commit message is written as the merge-request body:
it argues from upstream's own asymmetry rather than from this project's
needs, and mentions no part of open-media-drc.
