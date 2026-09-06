# TODO — the uaudio(4) follow-up series

**Submitted. Awaiting review.**
[freebsd-src PR #2390](https://github.com/freebsd/freebsd-src/pull/2390) —
*"sound: uaudio: finish the UAC2 shared-clock fix"*, opened 2026-08-26, three
commits, **open with no reviewer assigned and no maintainer response** as of
2026-09-06. `christosmarg` was pinged in the thread; nothing back yet.

The checklist below is kept as the record of how it was sent. Steps 1–6 are
done; step 7 (the Bugzilla comment) is the one to confirm.

### Current state of the tree it targets

`main` today has **only** `755685dd665e` (2026-07-24, the shared-clock fix).
Verified against `main` on 2026-09-06 — `uaudio20_clock_is_shared()` and
`uaudio_chan_match_rate()` are present; `uaudio20_get_speed`,
`uaudio_clock_readback`, `uaudio_prefer_feedback`, `uaudio_chan_find_sync_ep`,
`sync_ep` and `clock_settle_ms` are all **absent**, and
`uaudio_chan_need_both()` is unchanged. So every defect this series describes is
still live upstream.

**Rebase risk:** `5c3bc8a` (2026-08-21, *"snd_uaudio: Use uDWord for the UAC2
sample rate"*) rewrote `uaudio20_set_speed()` to use `uDWord data` +
`USETDW(data, speed)` in place of the `uint8_t data[4]` byte-shifting. Commit
1/3 adds `uaudio20_get_speed()` immediately after that function and still uses
`uint8_t data[4]` + `UGETDW(data)`. Expect a context conflict there on rebase,
and match the new style while fixing it.

**Bugzilla cannot be scripted** — bugs.freebsd.org sits behind an Anubis JS
proof-of-work gate that blocks the REST API too. Step 7 is manual, by hand, in
a browser.

---

## Prerequisites

- [ ] A machine with **≥10 GB free** (shallow clone + object store + build)
- [ ] `gh` installed and authenticated (`gh auth login`)
- [ ] A fork of `freebsd/freebsd-src` under your account
- [ ] `git config user.email delleceste@gmail.com` (the commits are already
      authored as you — do not let a different identity rewrite them)

## Submit

- [ ] **1. Clone** (not onto a nearly-full filesystem)
      ```sh
      git clone --depth 200 https://github.com/freebsd/freebsd-src.git
      cd freebsd-src && git checkout -b uaudio-shared-clock-followup
      ```
- [ ] **2. Apply** the series
      ```sh
      git am /path/to/open-media-drc/freebsd-uaudio-patch/upstream-series/000*.patch
      git log --oneline -3
      ```
      Verified `git am`-clean against `main` after `755685dd665e`. If the base has
      moved, rebase — every hunk is local to `uaudio_configure_msg_sub()`,
      `uaudio_chan_play_sync_callback()`, `uaudio_chan_need_both()`,
      `uaudio_chan_start()` and `uaudio_chan_fill_info_sub()`.
- [ ] **3. Build** — each commit builds standalone, `-Werror`, with and without
      `USB_DEBUG`. Re-check on the real tree:
      ```sh
      cd sys/modules/sound/driver/uaudio && make clean && make
      ```
- [ ] **4. Push** to your fork
- [ ] **5. Open the PR** with `PR-DESCRIPTION.md` as the body
      ```sh
      gh pr create --repo freebsd/freebsd-src \
        --title "sound: uaudio: finish the UAC2 shared-clock fix" \
        --body-file ../open-media-drc/freebsd-uaudio-patch/upstream-series/PR-DESCRIPTION.md
      ```
- [ ] **6. Request review: `christos@`** (committed `755685dd665e` via #2323).
      **CC `hselasky@`** — commit 2/3 is isochronous endpoint policy, his side.
- [ ] **7. Comment on the bug** — paste `BUGZILLA-COMMENT.txt` into
      <https://bugs.freebsd.org/bugzilla/show_bug.cgi?id=295933>, replacing
      `NNNN` with the PR number. Manual; see the Anubis note above.

## The three commits

| | Evidence | Droppable? |
|---|---|---|
| **1/3** never rewrite a shared clock another stream owns; `GET_CUR` read-back; no stale capture alt | live trace | no — the core fix |
| **2/3** prefer the explicit feedback endpoint over the auto-started capture stream | counter differential (~126 extra isoc transfers/s) | no, but it is the behaviour change most worth review |
| **3/3** program the clock before arming the stream (clock-before-alt) | **listening only, one device** | **yes — deliberately last so it can be dropped without a rebase** |

Say this out loud in review rather than waiting to be asked: 3/3 has no measured
proof, and the silent-open fault it targets did **not** reproduce in 42
controlled cycles (21 of them into 44.1 kHz). 1/3 and 2/3 stand without it.

## Do not claim

The three defects are established from code, a trace and counters. **None of
them is claimed as the cause of the intermittent 44.1 kHz silent-open.** Leading
with an unreproducible symptom is how this gets closed as unreproducible.

## After it lands (or doesn't)

- [x] **Install the local rollup** (`../uaudio-clock-transaction.c.patch`) on the
      audio box — done 2026-09-06, and **verified on the wire** with DTrace:
      redundant post-arming `SET_CUR` gone, same-rate start writes the clock zero
      times, capture stream never armed. See [`../traces/`](../traces/README.md).
      This is new evidence for the PR and is worth posting to it.
- [ ] Run the three-leg A/B in [`../bench/README.md`](../bench/README.md)
      — legs differ only by `hw.usb.uaudio.prefer_feedback`. Still the
      experiment that decides audibility; the trace only settles the wire.
- [ ] Decide the five declared-but-not-patched items (listed in
      `PR-DESCRIPTION.md` and `../SUBMISSION-295933.md`): park-every-interface +
      `STOP`/`START` coalescing, unlocked cross-direction reads, `-EBUSY` for
      incompatible simultaneous rates, `bmControls` writability
- [ ] Rebase `../uaudio-feedback-follow.c.patch` — commit 2/3 changes the
      sync-callback gate, so that patch finally becomes reachable on this DAC
- [ ] Re-check after any OS update: `freebsd-update` silently reverts both the
      module and `/usr/src` (see `../README.md`)
