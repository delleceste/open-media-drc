# omdrc app

A small, standalone Android app for an [open-media-drc](../../README.md) box.
It opens the box's `omdrcctrl` panel in a single, reused in-app WebView (unlike
Chrome's "Add to Home screen" shortcut, which opens a new tab every time for a
plain-HTTP LAN site like this one), and also provides a home-screen widget
showing the box's status: DRC on/off, active filter geometry/design, effective
attenuation + headroom-safe state, and (on the larger widget size) what MPD is
playing.

**Two views**, chosen with the gear button (⚙, top right):

- **Kiosk view** (default): the small-screen UI served at `/k/` (see
  `omdrc-ctrl/src/kiosk/README.md`) — swipeable pages for Now playing, DRC, DR,
  Source, Chain, System, Logs and Config. A Video page appears when the box's
  `omdrcvideo` service is available on port 9080 and opens its remote in the
  same WebView.
- **Full web page**: the desktop dashboard at `/`.

The choice is remembered. In the kiosk view the app keeps the screen on **only
while the "Now playing" page is showing and something is playing** (30 s of
still meters let the phone sleep again); every other page, the full web page and
a loading page let the phone sleep normally. (Settings → "Keep the screen on…"
turns this off.) The kiosk page tells the app what it wants through a small
JavaScript bridge (`window.OmdrcApp`, see `MainActivity.AppBridge`).

**Orientation.** With Android's auto-rotate on, the app follows the phone like any
other app and the kiosk adapts to how it is held (on Now: needles in landscape,
bars upright). With auto-rotate off, the kiosk's rotate button selects landscape
or portrait for all pages. The choice persists across navigation and app launches.
The button shows the orientation it will switch to. The app watches the phone's
auto-rotate setting while it is open.

**Light and dark.** The dashboard follows the phone's light/dark mode: its theme
(`values` light, `values-night` dark) is what the WebView reports to the kiosk,
whose *Automatic* theme then matches, and the app's own screens (splash, the
connection error, the status bar icons) switch with it. The home-screen widget
stays dark.

The launcher name is "OMDRC". The Android application id is
`it.giacomos.omdrc.app`. Android treats it as a separate app from the former
`com.omdrc.widget` package. An existing install and its widgets remain until
the old package is removed; settings must be migrated separately. The project
folder remains `omdrc-app`.

While a page loads, a large ring in the middle of the screen shows the progress in
percent. At the bottom, an editable IP/hostname and **Connect** button let you
cancel a stalled connection immediately. The new address is saved for the current
network and reconnects using the existing port, without waiting for a timeout.
It is spared where it can be: **Back** leaves the app the way Home does,
so the page stays alive and coming back shows it at once; and when the system has
dropped the app (or it was swiped away), the kiosk's last screen, kept as a picture
per orientation (`LastPage`), is shown at once while the page loads under it, the
ring appearing only if that takes more than about a second.  The kiosk's scripts
are cached for good (their URLs change with every deploy), so that load is short.
**Swipe down to reload** the page: in the kiosk view this works whenever
the current page is scrolled to its top (the kiosk reports its own scroll position
through the bridge), and a horizontal page swipe never triggers it.

If the page cannot be loaded (box off, panel down, wrong address, or a 5xx from the
server), the WebView and its "Web page not available" page are hidden behind a
native screen in the kiosk's colours: it names the address and the reason
(refused, unknown host, timed out, ...) and retries by itself after 5, 10, 20 and
then every 30 s while the app is in the foreground, so a box that is still booting
comes back without a tap. The host and numeric port are separate fields with
**Connect**: after moving to another network the box usually has another IP,
while the port typically stays the same. Connecting saves the new address, loads
it and re-points the live status service. **Network settings** opens Android's
Wi-Fi settings. The app remembers the last
selected or successfully reached address for each network and reconnects with it when the phone changes networks, including changes
while the app was in the background. Wi-Fi names identify networks when Android
provides them; otherwise the gateway and subnet identify them (networks using the
same gateway and subnet cannot be distinguished without the Wi-Fi name).
After three consecutive page-load failures, the server configuration dialog opens
automatically once for that failure episode. Automatic retries pause while the
dialog is open or the field is being edited; **Retry now** tries the current address
at once.

Apart from the kiosk's own controls (DRC presets, filter switching, play/pause),
the app adds no controls of its own; the widget itself is read-only.

## Requirements

- The box's `omdrcctrl` panel reachable on your phone's LAN/Wi-Fi (default
  `http://<box-ip>:9090`, no authentication — same as accessing it from a
  browser today).
- Android 8.0 (API 26) or newer.
- No server-side changes needed — this app only calls two existing read-only
  endpoints (`GET /drc/brutefir-config`, `GET /mpd/info`).

## Building

This is a self-contained Gradle project, independent of the repo's
CMake/Make build (nothing here is wired into `CMakeLists.txt`/`Makefile`).
On FreeBSD, follow [the FreeBSD Android build guide](../FREEBSD-BUILD.md) for
the Linux JDK and SDK setup.

1. Install a JDK 17+ and the Android SDK command-line tools (or Android
   Studio, which bundles both). Point `ANDROID_HOME`/`local.properties` at
   your SDK if building from the command line.
2. The Gradle wrapper jar isn't checked in (binary file); generate it once
   with a system Gradle install:
   ```sh
   cd android/omdrc-app
   gradle wrapper --gradle-version 9.6.0
   ```
   From then on use `./gradlew` as usual.
3. Build and install on a device on the same LAN as the box:
   ```sh
   ./gradlew :app:assembleDebug :app:lint
   ./gradlew :app:installDebug
   ```
   Prefer a physical device over an emulator — the emulator's default NAT
   networking makes it awkward to reach a real LAN host.

## Using it

1. Long-press the home screen → Widgets → "OMDRC" → drag it on.
2. Enter the box's IP/hostname and port (default `9090`) when prompted.
3. The widget refreshes itself roughly every 5 minutes (via an inexact
   `AlarmManager` alarm — see `AlarmScheduler.kt`), with a 15-minute
   `WorkManager` periodic job as a resilience fallback in case some OEM's
   battery manager kills background alarms. Tap the small refresh icon
   inside the widget for an immediate check; this is a "glance" widget, not
   a live view like the web dashboard's own 5s polling.
4. Tap the widget body, or launch the app itself, to open the dashboard in a
   single reused WebView window (kiosk view by default; ⚙ switches to the full
   web page).
5. Expand the app's live-status notification and tap **Levels** to open the
   fast stereo LEVELS meters in a small Picture-in-Picture window. The native
   meter connects to `/spectrum/stream?mode=vu`; dismissing the PiP window
   closes that stream, allowing the server-side analyzer to stop immediately
   when it has no other listeners.

## Known gaps (deliberately out of scope for this first pass)

- No mDNS/zeroconf discovery — the box doesn't advertise itself, so host
  entry is manual (matches how the web UI is reached today).
- No Play Store packaging/signing — personal sideload only.
- No foreground-service/push-based real-time refresh.
- App icon is a placeholder vector glyph, not real artwork.
