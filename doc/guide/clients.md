# Clients: kiosk, Android app, Plasma widget, web panel

The box has no screen of its own to look after. The `omdrcctrl` service
(port 9090) serves every way to drive it, and they are all clients of the same
routes. Whatever one does, the others see.

[← Back to the README](../../README.md)

## The kiosk: `http://<box>:9090/k/`

A touch interface built for a 7" 1024×600 screen beside the amplifier. It
also works on a phone, upright or in landscape. Swipe between **Now**,
**Qobuz** and **DRC**; the menu (☰) opens the rest:

| Page | What it is |
|---|---|
| [Now](now-playing.md) | Level meters in six styles, live DR, balance, cover, transport |
| [Qobuz](qobuz.md) | Search with filters, History, Discover, Awarded, Favourites folders, the player, [AI](ai.md) |
| [Room correction](room-correction.md) | DRC state, sample rate, filters, attenuation |
| Video | The video remote, when the video service runs |
| [Dynamic range](dynamic-range.md) | Albums by DR, Recent DR, the live estimate and the DR log |
| Source, Local database | Renderers, MPD, CD input; the local collection, Rescan, DR14 tools |
| Audio chain, System, Logs | The live signal path, CPU, memory and devices, alerts and every log |
| Configuration | Theme, level display per orientation, AI settings, the desktop pages |

The top bar slides in on a tap on empty space and hides by itself. It holds
the page's own switches, the Qobuz / local search, rotation, keep-awake and
full screen. `/k/?embed=qobuz` (or any page) shows one page alone, for
embedding it elsewhere.

## The Android app

`android/omdrc-app` opens the kiosk full screen in a single reused WebView,
instead of a new browser tab each time.

- **Screen**: kept on only while Now is showing and music is playing.
- **Orientation and theme**: follows auto-rotate and the phone's light/dark mode.
- **Fast return**: the last screen is shown at once while the page reloads. Swipe down to reload.
- **Home-screen widgets**: from a 1×1 tile to a large one with the track and live meters. They show DRC on/off, the filter, attenuation and what is playing.
- **Notification**: a status notification, with *Instant updates* that wake only when the track, the playback state or the DRC change.
- **Back**: closes album details or the player first, then goes from Now to Qobuz.

## The KDE Plasma widget

**OMDRC Monitor** (`kde/omdrc-plasmoid`) puts the box on a KDE Plasma 6 panel
or desktop, on the box itself or on any desktop on the LAN:

- **What it shows**: LED bars or VU needles, the spectrum, live DR and balance, and the cover, in any combination.
- **Controls**: pointing at the cover brings up previous, play/pause, next and stop, Qobuz search and the queue. A ring round the cover seeks.
- **Install**: per user, with `make plasmoid-install`. See its [README](../../kde/omdrc-plasmoid/README.md).

## The desktop panel: `http://<box>:9090/`

The full dashboard for a computer:

- DRC controls, chain health and rate monitoring;
- the spectrum analyzer;
- filter-response charts with the stored room measurements;
- the Configuration page (filter installs, audio hardware roles);
- the bit-perfect check;
- the manual.

It runs configured shell commands as the audio user: expose it only on a
trusted LAN.

## Video (FreeBSD)

`video/` plays films and Blu-ray discs with mpv, controlled from a phone web
remote. The remote is also a page of the kiosk. While a film plays, its
sound goes through the same room correction.

More: [the web panel](../pdf/open-media-drc-manual.md#the-web-panel-omdrc-ctrl-secomdrcctrl),
[the kiosk and the Android app](../pdf/open-media-drc-manual.md#the-kiosk-and-the-android-app-seckiosk),
[the Plasma widget](../pdf/open-media-drc-manual.md#the-kde-plasma-widget-secplasmoid) and
[video](../pdf/open-media-drc-manual.md#freebsd-video-----mpv-playback-and-the-phone-web-remote-secvideo)
in the manual.
