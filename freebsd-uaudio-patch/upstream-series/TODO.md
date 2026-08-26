# TODO — submit the uaudio(4) follow-up series

Everything in this directory is finished and verified. What remains is the
push, which could not be done from the audio box: no `gh`, no `freebsd-src`
checkout, and `/` at 94% with ~1.5 GB free. Do it from a machine with room.

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

- [ ] Install the local rollup (`../uaudio-clock-transaction.c.patch`) on the
      audio box and run the three-leg A/B in [`../bench/README.md`](../bench/README.md)
      — legs differ only by `hw.usb.uaudio.prefer_feedback`
- [ ] Decide the five declared-but-not-patched items (listed in
      `PR-DESCRIPTION.md` and `../SUBMISSION-295933.md`): park-every-interface +
      `STOP`/`START` coalescing, unlocked cross-direction reads, `-EBUSY` for
      incompatible simultaneous rates, `bmControls` writability
- [ ] Rebase `../uaudio-feedback-follow.c.patch` — commit 2/3 changes the
      sync-callback gate, so that patch finally becomes reachable on this DAC
- [ ] Re-check after any OS update: `freebsd-update` silently reverts both the
      module and `/usr/src` (see `../README.md`)
