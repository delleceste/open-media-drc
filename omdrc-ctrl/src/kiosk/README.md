# omdrcctrl kiosk — the 7" touch UI

Served at **`http://<box>:9090/k/`** (and `/?view=mini` redirects there).  Built
for a 1024×600 landscape screen; 800×480 is the same layout scaled, and a phone
in portrait stacks the columns.

It is a separate package and a pure client of the panel's existing routes, so
the desktop UI (`app.py`, `templates/index.html`) and the kiosk never touch each
other.  The only seam is `kiosk.init_app(app, commands=…, features=…)` at the end
of `app.py`, wrapped in `try/except` so a broken kiosk cannot take the panel
down.

## Pages (swipe, or tap the tab bar; `#drc` etc. deep-links)

| Page | What it is |
|---|---|
| **Now** | Level meters (needles / bars / bars + spectrum), DR bar, channel balance, track + time, and one line saying which DRC is applied |
| **Cover** | *Optional* (Config → Cover art → Cover page): the album cover, square and as large as the height allows, the track beside it, and optionally a narrow vertical DR or level column (top bar: DR / Lvl) where there is room |
| **DRC** | Applied state, sample-rate presets, filter set and design, attenuation, BruteFIR peak/RTI, `drc.sh status` |
| **DR** | Rolling DR estimate (off until enabled), measure this record, compare masters |
| **Source** | Renderer switch/restart/activity, MPD state, CD input |
| **Chain** | The audio path from renderer to DAC, and who holds each device |
| **System** | BruteFIR CPU, memory, busiest processes, sound devices, system/application buttons |
| **Logs** | Recognised alerts and a tail of every configured log |
| **Config** | This screen's own settings, and the desktop pages opened inside the kiosk (configuration, bit-perfect, filter response, manual) |

## Level display off

Config → Now page → Level display has a fourth choice, **Off**.  Nothing is
computed for the level meters: no `vu`/`music` analyzer stream is opened, balance
(which is derived from those frames) is unavailable, and the Now page shows the
audio chain as a horizontal row of blocks instead, with the FIFO listeners hanging
under MPD.  Everything else compacts and the DR bar takes the freed height.  Use
it to keep the box's analyzer idle.

## Cover art

Off by default; with it off, every page is laid out exactly as without the
feature (only the top bar gains the **Art** chip).  Switched on (**Art** in the
top bar on Now, or Config → Cover art):

* **With meters** (needles, bars, bars + spectrum): the cover fills the meter area
  behind them, anchored at its top left and slid so its visual weight is in view.
  `widgets/cover.js` measures that weight in the browser (the cover is served by
  this panel, same origin): the cover shrunk to 48x48, each pixel weighted by its
  distance from the border's median colour plus local contrast, and the weighted
  centroid taken.  The meters are drawn see-through on top.  A **diagonal drag**
  on the meters (top left to bottom right) makes them more opaque, up to hiding
  the cover; the other way lighter.  A horizontal swipe still turns the page.
* **Level display off**: the whole cover, square, takes the audio chain's place;
  DR, balance and both splitters work as usual.  With DR and balance off too, the
  cover alone fills the area, centred.
* A track without a cover shows the layout as with the feature off.

## Inside the Android app

`android/omdrc-app` opens `/k/` by default.  When `window.OmdrcApp` exists the
kiosk hides its own fullscreen button (the app owns the gear button), skips the
browser keep-awake fallback, and tells the app whether the screen should stay on:
only while the Now page is on screen (`setPageWantsScreenOn`).  Config shows an
"App settings" button (`openSettings`).

## Meter timing and calibration

The box delays level/spectrum frames by its chain (DRC, or with DRC off the DAC's
own buffer). On top of that each screen has its own extra delay (Config → Meter
timing), kept in that device's browser storage. In the Android app it can be
measured with the phone's microphone:

* **Calibrate on the music**: 10 s of the mic's peak envelope is correlated with the
  arrival of the level frames (up to 3 attempts; weak or ambiguous matches rejected).
* **Tune with clicks**: a sheet with live level bars (the level stream runs only
  while it is open) and the delay's ± buttons.  The box stops playback and MPD plays
  an 11 s click track (`/k/api/clicks.wav`, 14 irregularly spaced 8 ms 1 kHz tone
  bursts at -30 dBFS, quiet enough for any normal listening volume, made at the
  running rate so the DRC chain is not rebuilt; `/k/api/clicktest`).  *Start* plays
  it with the current delay ("before"), measures and applies the delay, then plays
  it again ("after") timing the frames when they are *drawn*, delay included, so the
  result is what is left over.  *Play again* repeats that check and offers the
  correction beyond ±30 ms.  Onsets are matched as events, detected relative to the
  room's own noise floor rather than a fixed level.  Without the app's microphone
  the clicks just play, for setting the delay by eye.
  The clicks play in a temporary MPD partition (`omdrc-cal`) that borrows the
  enabled outputs and hands them back afterwards: the main queue is never touched,
  because Qobuz Connect (qobuzconnect2mpd) owns it and reacts to any foreign edit.
* **Automatic** (off by default): at a track start or when playback resumes after
  silence, listen 8 s and fold confident results in (median of 3), at most every 2 min.
  A blue light blinks in the Now page's track header while any calibration runs.

Only a loudness envelope ever leaves the app's recorder.

Every run writes a plain-text **calibration log**, shown live in a sheet while it
runs and afterwards under Config → Meter timing → *Calibration log* (the last one
is kept on the device), with **Copy** and **Select all**: each step with its time,
the box's delay model (`/spectrum/settings`), what the click test reported, frame
arrival (or drawing, when verifying) and level statistics, the detector's onsets, candidates and correlation
peaks, the verdict, and the raw level frames and microphone envelope.  It is meant
to be pasted into a bug report as is.

## Rules the code keeps

* **No computation without visible feedback.**  A page starts its streams and
  pollers in `show()` and closes them in `hide()`; the pager only calls `show()`
  once a swipe has settled.  Leaving *Now* closes both analyzer streams.  The DR
  estimate follows one remembered switch, **Estimate** on the DR page (on by default):
  off hides the DR value and bar on Now and stops the stream.  The screensaver hides
  the current page.
* **Nothing destructive on a bare tap.**  Reboot/power-off and any command marked
  `confirm = yes` in `commands.conf` ask first; filter/design switches and MPD
  restart confirm too.  Chain-rebuilding actions hold a modal spinner until the
  server reports the result.
* **No command lines reach the browser.**  `/k/api/config` returns each command's
  id, label, group, type, confirm text — never `cmd`.

## Layout of the package

```
kiosk/__init__.py            blueprint, /k/, /k/api/config
kiosk/templates/kiosk_shell.html
kiosk/static/core.js         DOM helper, API client, prefs, overlays, Poller, shared SSE streams
kiosk/static/main.js         boot, pager, top bar, alerts, screensaver, wake lock
kiosk/static/widgets/        vu (needles/bars), spectrum, dr (maths, estimator, bar, gauge),
                             balance, drcstate (shared DRC poll), track
kiosk/static/pages/          one file per page: K.registerPage({id, label, mount, show, hide})
kiosk/static/kiosk.css
```

Adding a page is one file in `pages/` plus one entry in the script list of
`kiosk_shell.html`.  Preferences (level style, screensaver, …) are per browser,
in `localStorage` under `omdrc-kiosk.*`.

## Switching the Pi's display off

With `omdrc-display-helper` running on the Pi and the kiosk opened once with
`?display=127.0.0.1:9097`, the top-right button becomes ⏻: the page goes black,
stops its streams, and the helper switches the screen off; a tap wakes both.
Install guide: `omdrc-ctrl/kiosk-pi/README.md`.

## Running it on the screen

Any browser in kiosk mode; for example on a Raspberry Pi behind the panel:

```
chromium --kiosk --noerrdialogs --disable-infobars --touch-events=enabled \
         --overscroll-history-navigation=0 http://<box>:9090/k/
```

## Try it from the checkout

```
python3 omdrc-ctrl/src/app.py --port 9191 --config /usr/local/etc/omdrcctrl/commands.conf
# then open http://localhost:9191/k/   (browser dev tools → 1024×600, touch emulation)
python3 -m unittest tests.test_kiosk
```
