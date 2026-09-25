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

## Inside the Android app

`android/omdrc-app` opens `/k/` by default.  When `window.OmdrcApp` exists the
kiosk hides its own fullscreen button (the app owns the gear button), skips the
browser keep-awake fallback, and tells the app whether the screen should stay on:
only while the Now page is on screen (`setPageWantsScreenOn`).  Config shows an
"App settings" button (`openSettings`).

## Rules the code keeps

* **No computation without visible feedback.**  A page starts its streams and
  pollers in `show()` and closes them in `hide()`; the pager only calls `show()`
  once a swipe has settled.  Leaving *Now* closes both analyzer streams.  The DR
  estimate is off until switched on, and the screensaver hides the current page.
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
