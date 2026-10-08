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

Only Now, Qobuz and DRC (in that order) are reached by swiping or the arrow keys;
a left swipe past the last one cycles back to Now.  Every other page opens from the page
menu (the wide-screen tab bar lists only the three), and does not swipe.

| Page | What it is |
|---|---|
| **Now** | Level meters (needles / bars / bars + spectrum), DR bar, channel balance, track + time, and one line saying which DRC is applied |
| **Cover** | *Optional* (Config → Cover art → Cover page): the album cover, square and as large as the height allows, the track beside it, and optionally a narrow vertical DR or level column (top bar: DR / Lvl) where there is room |
| **Qobuz** | *Optional* (`[qobuz_search] enabled`): album search with label check boxes and a release-date window (year spinners: drag, or tap for a picker); unfiltered, the results are Qobuz's own list in Qobuz's order ("Newest first" is a choice), read on as the list is scrolled; each with ▶ (replace upmpdcli's queue and play) and **+** (append), a tap shows the tracks, and a player strip (previous, play/pause, stop, next, seek) that opens into a full-screen player with the cover and the queue. The search field completes from a classical word list and from what was played. The form is full width; the filters fold into a one-line summary once results arrive. The magnifier in the top bar opens this page. − on a result lowers it (see the panel README). Greyed out while upmpdcli is not the running renderer |
| **Video** | *Optional* while the local `omdrcvideo` service answers on port 9080: its media browser and playback remote inside the kiosk. The kiosk checks again every 30 seconds. |
| **DRC** | Applied state, sample-rate presets, filter set and design, attenuation, BruteFIR peak/RTI, `drc.sh status` |
| **Dynamic range → Albums by DR** | Stored album DR ranking; tap an album to see its tracks, then use Back to return. Includes filters and local dr14.txt rescan |
| **Dynamic range → Recent DR** | Latest saved track DR values on this box, grouped by album when multiple tracks were saved; 20 album/song rows at first, with more rows and a lookback up to seven days |
| **Dynamic range → Configure** | Rolling DR estimate, DR log switch, measure this record, compare masters |
| **Source** | Renderer switch/restart/activity, MPD state, CD input |
| **Local** | The local collection MPD indexes: what it is, the music directory (MPD's `music_directory`, read from its configuration), and Rescan, which updates MPD and calculates missing DR14 reports in the background (`libexec/omdrc/scripts/omdrc-mpd-update-dr14.sh`, status in `local-scan.txt`). Local albums show in the Qobuz search with ⌂ and their `dr14.txt` DR value |
| **Chain** | The audio path from renderer to DAC, and who holds each device |
| **System** | BruteFIR CPU, memory, busiest processes, sound devices, system/application buttons |
| **Logs** | Recognised alerts and a tail of every configured log |
| **Config** | This screen's own settings, and the desktop pages opened inside the kiosk (configuration, bit-perfect, filter response, manual) |

`/k/?embed=<page>` (for example `?embed=qobuz`) shows that page alone, for
embedding it in something else such as the KDE Plasma widget: no top bar, tab
bar, Qobuz player strip or screen saver.  A page the box does not offer says
so instead.

Long press the DR history bar on Now or DR to open its time-axis window. Drag the
centre four-arrow grip to move the whole window, drag either edge grip to change
only that edge, or use −/+ to move by one minute. Extend left/right buttons grow
the window one minute per tap, including when it is too small to grab. Reset
moves the visible start to now and new audio fills the bar again. Full window
follows all blocks in the analyzer's 90-minute memory as new audio arrives.
Cancel restores the window from before the popup opened; Done keeps the live
selection. These view controls leave the live estimate and the local DR log
unchanged. The Albums by DR button opens the stored album ranking directly.

Recent DR reads the same local DR database as the album ranking. It shows the
latest saved measurement for each track, including tracks on albums that do not
yet have an album DR figure. Replaying a track may leave its older, better
measurement in place, so this is a list of saved values rather than every play.
An album with multiple recent tracks has one cover and album DR badge. Tap it
to see its tracks, then use Back to return; a lone track stays on the first
screen. Album badges use the stored exact or estimated figure, or show a dash
until enough audio has been heard. The list reads at most the latest 500 saved
tracks in the selected period.

Tap a DR history segment on Now or DR to show a short song title and the
segment's start and end as offsets from the latest block. Tap it again to hide
the label; the full measurement detail remains in the segment tooltip.

## Light and dark

Config -> **Theme**: *Automatic* (the default) follows the device's own light or
dark setting and switches when it does; *Dark* and *Light* fix it for that screen
(kept in its browser storage, so the panel behind the amplifier can stay dark
whatever its system says).  The shell's first script sets it before the first
paint.  `kiosk.css` has both palettes (`:root` dark, `:root[data-theme=light]`),
and whatever is drawn on canvases (meters, spectrum, the Cover page's column)
reads its colours from them through `K.css()` and redraws on a switch; in light,
the needles get a cream face with dark ink.  Inside the Android app, the app's
own theme follows the phone and is what the WebView reports to the page.

## Level display, upright and in landscape

The level display is chosen per orientation (Config → Now page, or the level
button on Now, which changes the one on screen): upright it is **Bars** unless
needles are picked while upright, in landscape **Needles** unless something
else is picked there.  Turning the phone switches between the two at once.

The Now page's View menu offers needles, bars, bars with a combined or separate
L/R spectrum, a circular spectrum, bars with a circular spectrum, and Off.
Circular spectrum uses outer low-frequency rings and inner high-frequency rings;
each ring fills its left and right semicircles from the bottom according to the
respective channel's level. Hold the circular display to set its band count
(4–24, default 12). The center shows the current L/R peak levels. The choice
and band count are saved on this device. Spectrum bars use a green/orange/red
level ramp, with a warmer red tint on R.

Each channel shows a blinking orange **CLIP?** near the 0 dB end when its peak
reaches -1 dBFS; this means the detector is checking that channel. A detected
flat top turns the mark into a steady red **CLIP**, latched independently for
Left and Right. The marks follow each full-scale needle's angle or sit vertically
at the right end of each bar. Tap a red mark to reset its channel. The PCM
check runs only while the Now page's meters are active and a channel peaks near
full scale. The tap is before DRC, so this does not measure clipping at the
DAC. A limiter can make a flat top without audible distortion, so the mark
indicates possible source clipping, not a definitive diagnosis.

## Now, upright

Upright, the cover takes a third of the height at the left with title, artist
and album beside it (the whole column theirs); play state and time sit on a row
of their own under the cover, with no progress bar.  The meters span the full
width, with DR and balance under them, then a short DR history strip and the DRC
line.  Seeking takes two steps, so a stray touch never moves the music: the
first touch on the cover (tap or slide) only brings up a ring inscribed in it,
12 o'clock the start of the track, drawn with a thin pen; a slide that then
*starts on the ring* moves its knob round (the pen thickens while the finger is
on it) and lifting the finger seeks there.  A touch elsewhere on the cover puts
the ring away; untouched, it goes after 4 s.  The search field lets go of the
focus on any touch outside the search box and when the keyboard closes, so the
keyboard never comes back by itself.  With the Qobuz search
enabled a search bar sits at the bottom; a tap goes straight to the Qobuz page
with its field focused (the keyboard would cover a field at the bottom), and
**Filters** opens that page's filters.

## Level and spectrum frames under network lag

The box always sends the newest frame, stamped with when it was sent (`sent`).  On
the screen, level and spectrum frames are drawn at most once per display refresh,
the newest one only.  If frames keep arriving more than 1.2 s later than the best
seen on the connection, they are coming from a backlog queued in the network, and
the stream is reopened to drop it (the new one opens before the old one closes, so
the analyzer never loses its last listener).  DR frames are exempt: each counts.

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
only while the Now page is on screen *and* there has been sound in the last 30 s
(`setPageWantsScreenOn`); still meters hand the screen back to the phone's own
timeout.

Orientation: with the phone's **auto-rotate on** (app API 6, `autoRotate`) the
app follows the phone like any other app and the pages adapt to how it is held
(on Now, needles in landscape and bars upright); the rotate button is hidden.
With auto-rotate off, the top bar's rotate button selects one orientation for
all app pages and remembers it across launches (`setUserOrientation` and
`forcedOrientation`, app API 7). Its icon shows the target orientation. In a browser
the same happens only in fullscreen, where the orientation can be locked; the 7"
panel never turns.  Config shows an "App settings" button (`openSettings`).

Starting: the app starts in the orientation last asked for.  **Back** leaves it
like Home, so the page stays alive and comes back as it was.  After the system
has dropped the app, its last screen (a picture per orientation, taken when it
went to the background) is shown at once while the page loads; the black splash
with its ring comes up only if that takes more than 1.2 s, and stays until the
kiosk calls `pageReady` (its first page painted).  The kiosk's scripts are asked
for with `?v=<asset version>` and cached for a year, so such a load is short.

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
  running rate so the DRC chain is not rebuilt; `/k/api/clicktest`).  *Start* runs
  two numbered steps, and the status line always names the one in progress: step 1
  plays it with the current delay, measures and applies the delay (a failure stops
  here and leaves the delay alone); step 2 plays it again timing the frames when
  they are *drawn*, delay included, so the result is what is left over.  *Check
  again* repeats step 2 and corrects beyond ±30 ms.  Meters found arriving
  *after* the sound cannot be fixed by waiting, so the calibration raises the box's
  margin instead (`POST /spectrum/margin`, kept in the state dir and shared by every
  screen): the frames leave that much sooner and this screen waits the last 20 ms.
  *ⓘ How it works* (on the card and on the sheet) opens
  `static/help/meter-timing.html`, the whole procedure with timing diagrams.  Onsets are matched as events, detected relative to the
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
  off hides the DR value and bar on Now and stops the stream.  The one exception
  is the **DR log** (DR page), a server-side switch that keeps the meter running
  with no page open to store every track's DR; the Audio chain always shows it
  as its own *DR log* consumer of MPD's FIFO.  The screensaver hides the current
  page.
* **Nothing destructive on a bare tap.**  Reboot/power-off and any command marked
  `confirm = yes` in `commands.conf` ask first; filter/design switches and MPD
  restart confirm too.  Chain-rebuilding actions hold a modal spinner until the
  server reports the result.
* **No command lines reach the browser.**  `/k/api/config` returns each command's
  id, label, group, type, confirm text — never `cmd`.

## Layout of the package

```
kiosk/__init__.py            blueprint, /k/, /k/api/config, /k/api/transport, /k/api/player, /k/api/queue
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
