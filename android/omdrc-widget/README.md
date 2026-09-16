# omdrc status widget

A small, standalone Android app whose only job is a home-screen widget
showing the status of an [open-media-drc](../../README.md) box: DRC on/off,
active filter geometry/design, effective attenuation + headroom-safe state,
and (on the larger widget size) what MPD is playing. Tapping the widget, or
the app's own launcher icon, opens the box's existing web dashboard
(`omdrc-ctrl`) in an in-app WebView — a single reused window, unlike Chrome's
"Add to Home screen" shortcut, which opens a new tab every time for a
plain-HTTP LAN site like this one.

Read-only: no DRC on/off control, no attenuation slider, no other panels
from the web UI. See the box's own dashboard for that.

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

1. Install a JDK 17+ and the Android SDK command-line tools (or Android
   Studio, which bundles both). Point `ANDROID_HOME`/`local.properties` at
   your SDK if building from the command line.
2. The Gradle wrapper jar isn't checked in (binary file); generate it once
   with a system Gradle install:
   ```sh
   cd android/omdrc-widget
   gradle wrapper --gradle-version 8.9
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

1. Long-press the home screen → Widgets → "omdrc status" → drag it on.
2. Enter the box's IP/hostname and port (default `9090`) when prompted.
3. The widget refreshes itself roughly every 5 minutes (via an inexact
   `AlarmManager` alarm — see `AlarmScheduler.kt`), with a 15-minute
   `WorkManager` periodic job as a resilience fallback in case some OEM's
   battery manager kills background alarms. Tap the small refresh icon
   inside the widget for an immediate check; this is a "glance" widget, not
   a live view like the web dashboard's own 5s polling.
4. Tap the widget body, or launch the app itself, to open the full
   dashboard in a single reused WebView window.
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
