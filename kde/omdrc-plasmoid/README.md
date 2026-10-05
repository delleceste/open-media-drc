# OMDRC Monitor: KDE Plasma widget

A Plasma 6 widget showing what an Open Media DRC box is playing: level meters
(LED-style bars or analogue needles), the spectrum analyzer, live DR and
channel balance meters, and the cover, in any combination. Hover the cover for
playback, stop, search and queue controls; on the desktop and in the popup a
ring around the cover seeks (drag it clockwise from twelve o'clock).  It also
browses Qobuz and the play queue, both played through upmpdcli on the box.
Every icon-only button has a tooltip.

It works both ways Plasma offers:

* **In a panel** it is a strip as thick as the panel, its length following the
  panes you chose (in a vertical panel they stack). Hovering shows the
  transport, search and queue buttons beside the cover. A click opens a larger
  view in a popup, with title and artist.
* **The panel tooltip**, while a track is loaded, shows the cover on the left
  and on the right the track, artist, album, label, year, format, genre,
  composer, performers, awards and as much of Qobuz's album description as
  fits (a tooltip cannot scroll, so the description is cut where the space
  ends).  The album details come from `/qobuz/track/<id>` and
  `/qobuz/album/<id>`, fetched once per track; a track not from Qobuz shows
  what the player reports.  The big tooltip stays away while the pointer is on
  the panel buttons or the quick search, so their own tooltips show.
* **On the desktop** it shows the large view directly, resizable.  Wide, the
  panes sit side by side; tall, they stack.  With the cover pane, meters and
  spectrum all on, the desktop and popup use a grid instead: cover and track
  next to the meters, the spectrum across the full width below, and DR (wider)
  and balance underneath.

Control-click a pane to move it to the next position, or Control-drag it to a
chosen position. The order is saved for each widget. In Plasma panel edit mode,
drag the whole widget to move it among the panel's other widgets.

After playback stops for a couple of seconds, the desktop view opens Qobuz
search, and the panel widget turns into a quick-search field with Search, AI,
Filters and Back buttons.  A Plasma panel normally takes no keyboard focus, so
the widget asks Plasma for it (`AcceptingInputStatus`) only when the field is
clicked, never when it opens by itself; Escape, leaving the field or closing
the search gives the focus back.  A plain search opens the full results popup
when the response arrives.

The popup's **Qobuz** page has the kiosk's catalog tools: recently played,
search results, discover by genre and awarded albums, filters for release
date, order, label groups, awarded and Hi-Res, and the AI recommender (with
its provider settings).  Each album card plays or appends the album and opens
its track list.  The **Queue** page lists the play queue, removes tracks and
clears it.

Like the kiosk and the Android app it is only a client of `omdrcctrl`; nothing
runs on the desktop besides the widget.

| What | Endpoint |
|---|---|
| levels (and bands) | `/spectrum/stream?mode=vu` or `mode=music`, server-sent events |
| spectrum floor and delay estimate | `/spectrum/settings` |
| live DR | `/spectrum/stream?mode=dr` |
| state and track, polled | `/k/api/player`, `/cdin/status` |
| prev / play / pause / next / stop / seek | `/k/api/transport` |
| play queue | `/k/api/queue` |
| cover | `/qconnect/art` |
| Qobuz browser, album details, AI | `/qobuz/...` (see `omdrc-ctrl/README.md`) |

## Install

From a configured build directory (see the main README):

```sh
cmake --build build --target plasmoid-install    # or: make -C build plasmoid-install
```

or without CMake, from any checkout:

```sh
kde/omdrc-plasmoid/install.sh
```

Both run the same script: `kpackagetool6 --type Plasma/Applet` for the user
running it (no root), installing the widget or upgrading it in place.  It is
not part of `make install` or `make user-install`, because it is a desktop
client: any KDE Plasma 6 desktop on the LAN may want it, and a headless box
has none.  Once installed, though, `make user-install` keeps it upgraded.

Then add **OMDRC Monitor** from *Add Widgets...* to a panel or the desktop,
open its settings and enter the box's address (`localhost` when the desktop is
the box; port 9090 unless changed).  After an upgrade, restart Plasma
(`plasmashell --replace &`, or log out and in) for a running widget to load
the new code.

To remove it: `kpackagetool6 --type Plasma/Applet --remove org.omdrc.monitor`.

## Settings

| Setting | |
|---|---|
| Box, Port | where `omdrcctrl` listens |
| Meters | level bars, VU needles, or none |
| Channel layout | meter channels automatic, side by side, or stacked |
| Spectrum | the band analyzer beside the meters, or below them in the desktop view and popup |
| DR, balance | the live DR meter with its history, and the channel balance meter (desktop and popup put them in a strip at the bottom) |
| Meter and spectrum timing | follow the box's live remaining-delay estimate, or set a per-widget delay from 0 to 3000 ms with a 1 ms slider and editable number |
| Cover | hidden, beside the meters, or behind them (the meters go translucent) |
| Length in panel | automatic (follows the panes) or a fixed length in pixels: a panel has no resize handle, so this is how to make it longer or shorter |
| Panel edge margin | pixels left between the widget content and the panel edge (0 by default) |
| Panel outer margins | pixels kept clear from the panel's top/left and bottom/right edges, where the widget reaches into the panel's own padding (0 and 2 by default) |
| Track | title and artist below the cover in the large view |
| Background | Plasma's default, none, or a custom color with alpha |

With meters and spectrum both off the widget shows the cover.

### Meter and spectrum timing

The box estimates its FIR/convolver (or direct-output) delay and reports the
effective remaining margin at `/spectrum/settings`. **Use the box's live delay
estimate** applies that value as this plasmoid's local wait and refreshes it
while connected. The configuration page shows the estimated wait and the
box's frame hold-back. This estimate does not measure network transit; it is a
useful starting point when the plasmoid runs on the audio box itself.

For manual alignment, turn Auto off and enter the web UI's **Applied screen
delay** in milliseconds. The number field accepts pasted values; the slider
moves in 1 ms steps. The override belongs to this plasmoid and does not change
the controller's shared margin, the web browser's profile, or the Android
phone's profile. On a box without a microphone, use the web UI's **Config ->
Meter timing -> Tune with clicks** and adjust the plasmoid while listening to
the click train. The click test does not require a microphone when you adjust
by eye.

The **Box chain-delay margin** below it is box-wide: one value shared by every
screen. Like every other setting it is sent to the box with Apply or OK; the
page shows what the box uses now and what is pending. With the live estimate on,
this plasmoid absorbs a margin change (the box sends the frames earlier and the
plasmoid waits that much longer), so its own timing does not move.

Frames are never drawn late. Each one carries the time the box produced and sent
it; the plasmoid schedules it from that, not from when the shell got round to
reading it, and drops any frame more than 100 ms past due, so a network backlog
or a busy Plasma shell cannot make the meters fall behind. A burst read in one
go is drawn as its newest frame only.

## The box's side

The widget needs an `omdrcctrl` that supports bounded analyzer streams
(`max_s`, see `omdrc-ctrl/SPECTRUM_ANALYZER.md`).  QML has no `EventSource`, so
the stream is read with `XMLHttpRequest`, whose `abort()` leaves the socket
open: an unbounded stream would keep the box's analyzer, and MPD's FIFO output,
running after every pause.  Against an older box the widget says so instead of
showing meters; the cover and the transport buttons still work.

It streams levels only while something plays: MPD playing, or a disc on the
CD / S-PDIF input (`/cdin/status`), which plays past MPD.  Otherwise it closes
its stream, so the box can switch the analyzer off (within 20 s).  Transport goes to MPD through the kiosk's endpoint, the
same as the kiosk's own buttons.

## Files

```
package/metadata.json              the applet's identity (org.omdrc.monitor)
package/contents/config/main.xml   settings and their defaults
package/contents/ui/main.qml       data: polling, the stream, transport
package/contents/ui/Display.qml    layout of the panes, track, hover controls, quick search
package/contents/ui/AlbumTip.qml   the panel tooltip: cover and album details
package/contents/ui/QobuzView.qml  the Qobuz browser page
package/contents/ui/AlbumCard.qml  one album in it: play, append, tracks
package/contents/ui/QueueView.qml  the play queue page
package/contents/ui/SeekRing.qml   seek ring around the cover
package/contents/ui/DrView.qml     live DR meter and history
package/contents/ui/BalanceView.qml channel balance meter
package/contents/ui/Meters.qml     bars and needles (canvas)
package/contents/ui/Spectrum.qml   band spectrum (canvas)
package/contents/ui/SseStream.qml  server-sent events over XMLHttpRequest
package/contents/ui/levels.js      scales and ballistics, as the kiosk's
```
