# OMDRC Monitor: KDE Plasma widget

A Plasma 6 widget showing what an Open Media DRC box is playing: level meters
(LED-style bars or analogue needles), the spectrum analyzer, and the cover, in
any combination.  Point at it and prev / play-pause / next appear over it.

It works both ways Plasma offers:

* **In a panel** it is a strip as thick as the panel, its length following the
  panes you chose (in a vertical panel they stack).  The tooltip names the
  track; a click opens a larger view in a popup, with title and artist.
* **On the desktop** it shows the large view directly, resizable.  Wide, the
  panes sit side by side; tall, they stack.

Like the kiosk and the Android app it is only a client of `omdrcctrl`; nothing
runs on the desktop besides the widget.

| What | Endpoint |
|---|---|
| levels (and bands) | `/spectrum/stream?mode=vu` or `mode=music`, server-sent events |
| spectrum floor | `/spectrum/settings` |
| state and track, polled | `/k/api/player` |
| prev / play / pause / next | `/k/api/transport` |
| cover | `/qconnect/art` |

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
| Spectrum | the band analyzer beside the meters |
| Cover | hidden, beside the meters, or behind them (the meters go translucent) |
| Track | title and artist under the large view |
| Analyzer | keep streaming while paused or stopped (off by default) |

With meters and spectrum both off the widget shows the cover.

## The box's side

The widget needs an `omdrcctrl` that supports bounded analyzer streams
(`max_s`, see `omdrc-ctrl/SPECTRUM_ANALYZER.md`).  QML has no `EventSource`, so
the stream is read with `XMLHttpRequest`, whose `abort()` leaves the socket
open: an unbounded stream would keep the box's analyzer, and MPD's FIFO output,
running after every pause.  Against an older box the widget says so instead of
showing meters; the cover and the transport buttons still work.

While nothing plays it closes its stream, so the box can switch the analyzer
off (within 20 s).  Transport goes to MPD through the kiosk's endpoint, the
same as the kiosk's own buttons.

## Files

```
package/metadata.json              the applet's identity (org.omdrc.monitor)
package/contents/config/main.xml   settings and their defaults
package/contents/ui/main.qml       data: polling, the stream, transport
package/contents/ui/Display.qml    layout of the panes, track, hover controls
package/contents/ui/Meters.qml     bars and needles (canvas)
package/contents/ui/Spectrum.qml   band spectrum (canvas)
package/contents/ui/SseStream.qml  server-sent events over XMLHttpRequest
package/contents/ui/levels.js      scales and ballistics, as the kiosk's
```
