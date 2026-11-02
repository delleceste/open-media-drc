# Handoff: a bigger OMDRC widget size, and tile polish (branch `master`)

Written 2026-10-07 at the end of a session that reworked the 1×1 widget tile
and the live-updates notification. Load this into a new session to continue.
Repo: `~/devel/open-media-drc`. **This file is a session note, not project
documentation: don't commit it.**

## 1. Where things stand (all pushed to `master`)

The 1×1 widget tile (`WidgetSize.TINY`, `RemoteViewsBuilder.kt`) now:
- Matches the launcher's own icon size/position (Pixel-measured `PIXEL_GRIDS`,
  a proportional fallback elsewhere), tunable by sliders + a font picker in
  `WidgetConfigureActivity` (icon size, label size, vertical offset, font,
  an outline-the-widget-area toggle). Commits `3db5909`..`34f733a`.
- Shows the cover art, kept across a transient "box unreachable" (commit
  `3db5909`/`0464394`), and follows the box the dashboard last used on the
  **current network** (`ServerNetwork.kt`, commit `0464394`) - this is what
  fixed covers never showing at home (the tile was polling the office box).
- Has an enlarged play/pause chip, positioned to clear the "OMDRC" label by
  measuring its real ink bounds from the font (commits `82f53b8`, `eb2e9ba`).
- Shows two small badges above the icon, each on its own translucent
  rounded backdrop (same color as the chip, `widget_chip_backdrop`,
  `#40000000`): **"DRC"** in green (`status_safe`/`widget_fmt_green`) at the
  icon's **left** edge when DRC is running, and the **source format**
  ("96/24") at the icon's **right** edge, colored like the kiosk page's own
  track strip (`kiosk/static/widgets/track.js K.formatColor`): hi-res
  (24-bit/88kHz+) in blue (`widget_fmt_hires`, `#4FC3FF`, brightened off the
  app's own accent blue for contrast), 24-bit/176kHz+ in green, else white.
  Commits `4d09655`, `c710d87`, `fc36f9b`, `b9e3949`.
  - **Not yet visually confirmed by the user** - the phone kept re-locking/
    going to its always-on display during the session, so screenshots came
    back black. The widget's own debug log confirmed no crash and the right
    text/color/geometry each time, but **check it on the actual screen
    first thing** next session.

New box endpoint `/now` (`omdrc-ctrl/src/app.py`, commit `cae8d29`) long-polls
(`?since=<token>&wait=<s>`, up to 30s) and answers one bundle: `music` (state,
title, artist, album, line1, edition, art, rate, bits, renderer), `drc`
(running, geometry, description, effective_attenuation_db, headroom_safe),
and `video` (the film the video remote - `video/webremote/src/app.py`'s new
`/api/now` + `/api/now/art`, commit same `cae8d29` - is playing: title, year,
director, runtime, genre, rating, paused, art). The video side reads the
IMDb record the library browser already cached (`cached_only=True` - never an
OMDb request from the polled path). **Deployed and restarted on dal only**;
the user's home box (192.168.1.9) does not have this endpoint yet.

Android: an optional **"Instant updates (permanent notification)"** setting
(`AppPrefs.instantUpdates`, `MainActivity.setInstantUpdates`) keeps
`LiveStatusService` on a long-poll loop against `/now` (falls back to
polling a box without it); its notification shows the cover/poster, track,
edition, format, renderer and the DRC line, or the film's details. Its
**Close app** action (`LiveStatusService.closeApp`) sets `AppPrefs.closed`,
cancels the alarm/periodic worker, and broadcasts `ACTION_CLOSE_APP` so
`MainActivity` finishes - nothing touches the network again until the app is
reopened (`LiveStatusService.reopenApp`, called from `onCreate`/`onStart`).
Commit `a44fa2f`.

Tests: `tests/test_now_playing.py` (the `/now` and video `/api/now` logic).

## 2. The open question this file is for

The user asked: **if we resize the widget larger, what else is worth
showing?** My answer, not yet built - repeated here so it isn't lost:

**Already fetched, not shown on any widget size today:**
- Cover art - only TINY has it; `widget_large.xml`/`widget_small.xml` are
  text-only. This is the obvious anchor for anything bigger.
- DRC detail beyond on/off: geometry, filter design, effective attenuation
  dB, whether it's a saved value or the configuration default.
- RTI%/peak dB (already in LARGE as one small text line - could be clearer).
- Which renderer is active (folded into one text line today).

**Not fetched by the Android client today, cheap to add:**
- Playback position/queue - `/k/api/player` (elapsed, duration, queue
  position, queue length) is never called from the app. Needed for a
  progress bar / "track 3 of 12".
- `edition` (label · year) - already in `/now`'s `music.edition`, just not
  pulled into `WidgetSnapshot`/`RemoteViewsBuilder` yet.

**New capability, needs real work:**
- Real transport controls (prev/play-pause/next, not just TINY's single
  toggle) - `/k/api/transport` already supports all three actions.
- "Film mode" - when a movie is playing, switch the widget to `/now`'s
  `video` block (poster, title, director, runtime, genre, rating) the way
  the notification already does.
- A safety-margin gauge, mirroring the dashboard's own headroom gauge
  concept (`DRC_GAUGE_RATES` in `omdrc-ctrl/src/templates/index.html`)
  instead of just a safe/unsafe icon.

**My recommendation**, offered but not decided on: a 4×2 or 4×4
media-style `WidgetSize` - cover on one side, title/artist/edition + a thin
progress bar, DRC detail and the safety margin, prev/play/next across the
bottom. Reuses everything above; only new server-side need is pulling
elapsed/duration/queue into the Android client's fetch (the data already
exists at `/k/api/player`).

**Not yet decided:** whether to build that new size, or fold pieces of this
into the existing SMALL/LARGE layouts first. Ask the user which before
starting.

## 3. First things to do next session

1. **Check the two badges and the lightened backdrop on the actual screen**
   (phone, not a screenshot while locked) - confirm DRC-left/format-right,
   green/blue colors, vertical centering, before doing anything else.
2. Get a decision on §2's open question before building a larger size.
3. If/when building it: deploy `/now` to the home box too (192.168.1.9) -
   today instant updates there silently fall back to plain polling.
