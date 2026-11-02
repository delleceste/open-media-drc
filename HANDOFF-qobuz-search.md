# Handoff: Qobuz album search with label/date filters (branch `qobuz-search`)

Written 2026-09-27 at the end of the session that built steps 1 and 2. Load
this into a new session to continue. Repo: `~/devel/open-media-drc`.
**This file is a session note, not project documentation: don't commit it.**

## 1. The goal (the user's words, condensed)

Qobuz's app and web player cannot filter a search the way classical listening
needs. The user wants, in the **omdrcctrl web UI and the Android app**:

- **One text field**, plus filters:
  - **Label**, with several allowed ("Pentatone OR Decca"). Favourite labels are
    configurable and addable, shown as check boxes.
  - **Year**: "last N years" (slider) or a from–to span, e.g. 2021 to today.
- **Results sorted by date**, relevant ones first: a compact list of album
  cards (small cover, title, artist, performers, label, year). On the phone a
  scrolling list is fine; the matches are usually few.
- **Buttons per result:**
  - **▶** replaces the upmpdcli/MPD queue and plays.
  - **+** appends to the queue.
- **A small player strip**: prev / play-pause / stop / next and a seek slider,
  like BubbleUPnP or Qobuz.
- **Most important: playback goes through upmpdcli on the box**, not through
  the phone.

## 2. Decisions the user made (don't reopen)

- **Paging:** page through Qobuz's results like the Qobuz app does. No fixed
  cap that silently misses "Beethoven 5 on Pentatone" ranked deep. Implemented
  as progressive scan plus "Load more" (below).
- **Label matching:** a label is a *keyword*. "Decca" must match "Decca Music
  Group Ltd.", "Decca (UMO)" and "Decca Classics". Implemented as whole-word,
  case-insensitive matching.
- **Reissue dates:** Qobuz's release date is that release's own date, so a
  reissue carries the reissue's date. Accepted; nothing to do.
- **Renderer:** upmpdcli will be the active renderer. **The Qobuz search UI
  must be disabled when upmpdcli isn't running** (qobuzconnect2mpd is the other,
  mutually exclusive renderer).
- **Unofficial API:** it's the same API upmpdcli uses. Accepted.
- **Keep `app.py` clean:** new features go in separate modules. `app.py` only
  gets the config section and an `init_app` call, the same pattern as the
  kiosk.
- **Played albums:** albums the user actually plays can be cached for future
  use. Implemented as `qobuz-played.json`.

## 3. User preferences that matter here

- **The home box (the "NUC", reached as `bee` in the prompt) is not this dev
  box (`dal`, FreeBSD 15.1).** Draw no runtime conclusions from dal. For
  anything on the box, **give the user commands with explanations to run
  themselves** (read-only inspection is fine). Don't try to run sysadmin
  commands there.
- **Commits:** on this branch the user expects each finished change to be
  committed *and pushed* ("yes of course commit and push"), still asking
  once per change. Pushed so far: `7b130ee`, `83f4950`, `25207c2`.
- (Earlier note:) **Commit only when asked.** Steps 1 and 2 are **uncommitted** on branch
  `qobuz-search` (branched from master `42de1c1`). The user was asked whether to
  commit before step 3 and hadn't answered.
- **Write in the house style:** plain comments that explain *why*, the same
  density as `drdb.py`, and tests with docstrings that say what they pin.

## 4. Facts verified on the real box (via the user)

- **upmpdcli's Qobuz plugin works on the box.**
  `curl -sI http://localhost:49149/qobuz/track/version/1/trackId/4636613` →
  `302 Found`, with a Location on `streaming-qobuz-std.akamaized.net/...`. The
  plugin port is 49149, the path is `/qobuz/track/version/1/trackId/<id>`, and
  the app id is `798273057`.
  - That redirect said `fmt=6` (CD quality) although upmpdcli.conf has
    `qobuzformatid = 27`. Possibly that track is CD-only. **Open question:**
    check with a known hi-res album.
- **The user's UPnP port** was reported as 40321. The dev box's scratch
  upmpdcli used 49152, so don't hard-code it: discovery handles it.
- **BubbleUPnP proxies through the phone.** The user's BubbleUPnP was using
  *its own* Qobuz library, so MPD's `file:` was
  `http://192.168.1.4:57645/proxy/qobuz/<hash>.flac` (the phone). This is why
  "play through the box" matters.
- **The upmpdcli patch works on the box.** `currentsong` there showed
  `Label: Pink Floyd Records`: the `dc:publisher` → MPD `Label` patch
  (`upmpdcli/patches/0001-...`) is live.
- **Qobuz search needs the user token.** `catalog/search` with only the app id
  answers **401**, so the search can't be tried live from dal. The token lives
  on the box, in upmpdcli's cache: `<cachedir>/qobuz/config`, keys
  `user_auth_token` and `user_id`.

## 5. What is built

The branch has 9 changed or new files:

```
M omdrc-ctrl/CMakeLists.txt       install list: + qobuz_search.py qobuz_web.py openhome.py
M omdrc-ctrl/README.md            "Qobuz album search" section, [qobuz_search] reference, layout
M omdrc-ctrl/src/app.py           +~27 lines only (see below)
M omdrc-ctrl/src/commands.conf.in [qobuz_search] section with documented keys + label list
?? omdrc-ctrl/src/qobuz_search.py core: Qobuz client, filtering, paging, PlayedAlbums
?? omdrc-ctrl/src/qobuz_web.py    Flask blueprint /qobuz/*, credentials, renderer, play
?? omdrc-ctrl/src/openhome.py     OpenHome Playlist client (SSDP, SOAP, DIDL)
?? tests/test_qobuz_search.py     40 tests
?? tests/test_qobuz_play.py       25 tests (fake Playlist SOAP server over real HTTP)
```

### `app.py` changes

- Imports `Settings` and `settings_from_section` from `qobuz_search`.
- Adds the global `QOBUZ_SEARCH`.
- `load_config` parses `[qobuz_search]`, which is also added to `_RESERVED`.
- The kiosk `features` gain `"qobuz_search"`.
- Next to the kiosk init, `qobuz_web.init_app(...)` is called inside
  try/except and receives `_upmpdcli_conf_path`, `_upmpdcli_options`,
  `_qobuz_cache_config`, the plugin dir (the dirname of `QOBUZ_OAUTH_SCRIPT`),
  `_service_running(UPMPDCLI_SERVICE)` and `_STATE_DIR`.

### `qobuz_search.py`

**Qobuz calls:**
- `catalog/search?type=albums` in pages of 50, and `album/get`, both through
  urllib with the `X-App-Id` and `X-User-Auth-Token` headers.
- Credentials are injected as a callable. Tests inject `fetch(endpoint, params)`.

**Search:**
- Queries: the plain text, plus one "`<text> <label>`" query per ticked label.
  With only labels ticked, just the label names.
- Merge: each album keeps its best position across the queries.
- Filters: `streamable != False`, the date window, and the label groups.
- Sort: `date` (default, newest first) or `relevance`.

**Paging:**
- Read `scan` (250) albums per query first.
- While fewer than `want` (20) pass the filters, double the depth, up to
  `auto_scan` (1000).
- The response then carries `more` and `next_scan`, and the caller re-asks
  with `scan=`. The hard cap is `MAX_SCAN` = 10000.
- Pages are cached one by one, so "Load more" only fetches new pages.
- A failure on a first page fails the search. A later page's failure only
  ends that query, with `error` set in `queries[]`.

**Metadata:**
- `release_date_original`, else `released_at`.
- Performers come from `album/get` tracks' `performers` string, with technical
  roles dropped. Cards are enriched for the first `max_enrich` (30) results.

**Labels and dates:**
- Label groups are lines of the form `Name` or `Name: kw, kw`. Matching is
  whole-word and case-insensitive.
- `labels_seen` lists the label names met, in Qobuz's spelling.
- `last=N` counts rolling years back from today. `from`/`to` are calendar
  years.

**`PlayedAlbums`** stores JSON, newest first:
- Entries hold `item` (a compact album object), `performers`, `count` and
  `last`.
- `matching(text)` needs every word of the text to appear in the title,
  version, artist, composer, label or performers.
- Search adds matching played albums as a pseudo-query `"(played before)"` and
  marks cards with `played: <count>`.
- `catalog.record_played(id)` records an album.

### `qobuz_web.py` endpoints

| endpoint | what it does |
|---|---|
| `GET /qobuz/status` | `enabled`, `renderer` (upmpdcli running, cached 5 s), `token` |
| `GET /qobuz/labels` | the configured label groups |
| `GET /qobuz/search` | `q`, `label` (repeatable or comma-separated), `last` or `from`/`to`, `sort`, `scan`, `enrich=0`. **409 if upmpdcli isn't running.** |
| `GET /qobuz/album/<id>` | the album card plus `track_list`, `performers`, `description` |
| `POST /qobuz/play` | `{"album_id", "mode": "replace"\|"append", "start": "<qobuz track id>"}` |
| `GET /qobuz/played` | the played list, newest first |

**App id:** `[qobuz_search] app_id`, else upmpdcli.conf `qobuzappid`, else
the plugin's own `bundle.py`, run in a subprocess once per run. That fallback
was verified on dal: it returns `798273057`.

**Play (`POST /qobuz/play`):**
- Finds the renderer (cached; rediscovered once if unreachable).
- `replace`: DeleteAll, Insert every streamable track, then SeekId on the first
  track or on `start`. SeekId starts playback.
- `append`: Insert after the last id.
- Track URL: `http://<plgmicrohttphost or renderer host>:<plgmicrohttpport or 49149>/qobuz/track/version/1/trackId/<id>`.
- Both modes record the album as played.
- A partial failure reports "queued n of m tracks".

### `openhome.py`

**`find_local_renderer(name)`:**
- Sends an SSDP M-SEARCH for `urn:av-openhome-org:service:Playlist:1`, on the
  default interface and on loopback.
- **Keeps only renderers whose host is a local address.** The test is binding
  a UDP socket to that address.
- Reads the description.xml and picks by upmpdcli.conf's `friendlyname`.

**`Playlist`:** SOAP calls `IdArray` (base64 of big-endian uint32 ids),
`Insert`, `DeleteAll`, `SeekId`, `Read`, `Play` and `TransportState`.

**Retry quirk:**
- upmpdcli answers the **first action after an idle spell with "501 Action
  Failed"**, because MPD dropped its idle connection (log:
  `mpd_run_clear(m_conn) failed: Connection reset by peer`).
- Repeatable actions are retried once.
- A failed Insert is retried only after `Read`-ing the id that follows
  `AfterId`. If that track already has our URI, the insert went through: it
  must never go in twice.

**`didl(...)`** carries title, artist, album, `dc:date`, `dc:publisher`, genre,
cover art, track and disc number, duration, and `res` with
`http-get:*:application/flac:*`.
- **It sends no composer and no album-artist role.** upmpdcli folds every
  `upnp:artist` into MPD's single `Artist` ("X, Bruckner (Composer)"), which
  would break the DR page's artist search.

## 6. How it was tested

- **Running tests** (there's no pytest on dal):
  - `python3 tests/test_qobuz_search.py` and `python3 tests/test_qobuz_play.py`
    (65 tests, all green, in either order).
  - Full suite: `python3 -m unittest discover -s tests -p 'test_*.py'`. The
    same **37 failure lines are pre-existing on master** (bitperfect_drc_route,
    drc_power_state gauge markup, browser_alsa_install, rate_mismatch_tools,
    one test_kiosk `test_config_exposes_commands...`, bitperfect_page). None
    are ours.
- **Gotcha:** `test_qobuz_play` loads `app.py` again as a separate module, and
  each load re-points the shared `qobuz_web` module at its own settings. Tests
  that read settings through `qobuz_web` patch `qobuz_web._settings`.
- **End-to-end OpenHome test on dal:** a throwaway MPD and upmpdcli ran in the
  scratchpad (`musicpd` on 127.0.0.1:16600 with a null output; `upmpdcli -c`
  with `openhome = 1`, `mpdport = 16600`, `upnpiface = lo0`,
  `friendlyname = omdrc-scratch-test`). Both are stopped now.
  - `/qobuz/play` in replace and append mode worked through real SSDP and SOAP.
  - `playlistinfo` showed the plugin URLs with Title, Artist and Album tags.
  - **dal's upmpdcli 1.9.17 is stock**, so Date and Label don't show there. The
    NUC's is patched.
- **Not yet done: a live test on the NUC.** Commands for the user to run there:
  ```sh
  curl -s localhost:9090/qobuz/status; echo
  curl -s 'localhost:9090/qobuz/search?q=bruckner+7&label=Pentatone,Decca&last=3' | python3 -c '
  import json,sys; d=json.load(sys.stdin)
  if not d["ok"]: sys.exit(d["error"])
  for c in d["results"]:
      print(c["date"], "|", c["label"], "|", c["title"], "|", c["artist"], "|", ", ".join(p["name"] for p in c.get("performers", [])))
  print("queries:", [(q["query"], q["fetched"], q["total"]) for q in d["queries"]], "more:", d["more"], "next_scan:", d["next_scan"])'
  curl -s -X POST localhost:9090/qobuz/play -H 'Content-Type: application/json' \
       -d '{"album_id": "<id from a result>", "mode": "replace"}'; echo
  printf 'currentsong\nclose\n' | nc localhost 6600 | grep -E '^(file|Title|Date|Label):'
  ```
  The `queries` line shows whether the "`<text> <label>`" queries actually
  help, since it's unknown whether Qobuz's full-text search matches label
  names.

## 6b. Progress, second session (2026-09-27)

- Steps 1 and 2 committed as `7b130ee`.
- **Step 3 built, uncommitted:** `kiosk/static/pages/qobuz.js` (optional page,
  after Cover), styles at the end of `kiosk.css`, `/k/api/transport` gains
  `next`, `prev` and `seek` (`{"action":"seek","seconds":N}` → `mpc seek
  H:MM:SS`), and a new `GET /k/api/player` (MPD `status` + `currentsong`
  over the kiosk's `_Mpd`, no subprocess). READMEs updated. `test_kiosk`:
  16 tests green alone.
- **Not seen in a browser yet:** headless Chrome on dal can't reach
  localhost from this environment. The user has to look at it on the box or
  phone.
- The "pre-existing" `test_kiosk` failure in the full run is test order:
  another module loads `app.py` again and `kiosk._commands` points at it.
  The `features` assertion in that test was ours (fixed).
- Not done from step 3: the desktop panel section (`templates/index.html`).
- Then (user request): **full-screen player** (strip ⌃ or tap on the strip:
  cover from `GET /qobuz/track/<id>`, queue from `GET /k/api/queue`, a tap
  on a queue row = transport `jump` with a 1-based `pos`), and **search
  autocomplete**: `src/qobuz_words.txt` (~2,650 entries, installed next to
  the modules), learned entries in `qobuz-words.json` (`SearchWords`),
  recorded by `POST /qobuz/play` when the body has `query` (the page sends
  it only for rows of a search). Matching is client-side in `qobuz.js`.
  All uncommitted; tests green (41 + 36 + 18).

## 7. Next steps

1. **Commit steps 1 and 2 when the user says so.** End the commit message with
   the attribution lines from the session's system reminder.
2. **Step 3: the kiosk search page.** The Android app is a WebView on `/k/`,
   so this page covers the phone too.
   - The kiosk is `omdrc-ctrl/src/kiosk/` (plain JS, no build step). Pages are
     in `static/pages/*.js` (now, drc, dr, source, chain, system, logs, config,
     cover), widgets in `static/widgets/`, shared state in `static/core.js`
     (`K.state.features.qobuz_search` is already exposed through
     `/k/api/config`). Read `kiosk/README.md` first.
   - **Page contents:** one text input; label check boxes from `/qobuz/labels`
     (plus ticking a name from `labels_seen`); a "last N years" slider or
     from–to; a sort toggle; result rows with a small cover, title, artist,
     performers, label, year and a played badge, and ▶ / + buttons; a "Load
     more" button when `more` is set.
   - **Grey out the whole page when `/qobuz/status` has `renderer: false`**,
     with a hint to switch the renderer to upmpdcli.
   - **Player strip:** `/k/api/transport` only has `play`, `pause` and `stop`
     (`_TRANSPORT` in `kiosk/__init__.py`, run through mpc). Add
     `next`/`prev` (mpc `next`/`prev`) and a seek (mpc `seek <s>`, or
     OpenHome `SeekSecondAbsolute`), with position and duration from
     `/mpd/info`.
   - **Also:** a desktop panel section is wanted too (`templates/index.html`),
     and a "Recently played" list from `/qobuz/played` would fit.
3. **Step 4: favourite labels editable from the UI.** Today they live only in
   `commands.conf`, which is packaged and read-only. Store additions in the
   state dir (JSON), merged with the configured ones, and add
   `POST /qobuz/labels`.
4. **Open questions:**
   - `fmt=6` vs `qobuzformatid=27`.
   - Whether Qobuz caps `offset` for deep scans. If a later page fails, it
     shows in `queries[].error`.
   - What happens if upmpdcli's OpenHome *source* is Radio when ▶ is pressed.
     Untested; it may need Product `SetSourceIndex`.
   - The PDF manual (`doc/pdf/build-pdf.sh`) may need regenerating once
     user-facing docs settle.
