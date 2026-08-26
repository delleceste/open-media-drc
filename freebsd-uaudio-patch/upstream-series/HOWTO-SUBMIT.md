# How to send this

I could not do it from here. Three blockers, all environmental:

* `gh` is not installed (`pkg install gh`, then an interactive browser/token auth);
* there is no `freebsd-src` checkout, and `/` is 94% full with ~1.5 GB free —
  not enough for a clone, and I was not going to risk filling your root;
* bugs.freebsd.org is behind an Anubis JS proof-of-work gate that blocks the
  REST API as well, so a comment cannot be posted programmatically.

Everything else is done. The series is `git am`-clean against `main` after
`755685dd665e` (verified).

## 1. Get a tree (somewhere with room — not `/`)

```sh
git clone --depth 200 https://github.com/freebsd/freebsd-src.git
cd freebsd-src
git checkout -b uaudio-shared-clock-followup
```

## 2. Apply the series

```sh
git am /path/to/freebsd-uaudio-patch/upstream-series/000*.patch
git log --oneline -3     # three commits, authored as you
```

If `git am` complains the base moved, rebase onto `main` — the hunks are local
to `uaudio_configure_msg_sub()`, `uaudio_chan_play_sync_callback()`,
`uaudio_chan_need_both()`, `uaudio_chan_start()` and `uaudio_chan_fill_info_sub()`.

## 3. Sanity-build

```sh
cd sys/modules/sound/driver/uaudio && make clean && make
```

## 4. Push and open the PR

```sh
git push -u origin uaudio-shared-clock-followup      # your freebsd-src fork
gh pr create --repo freebsd/freebsd-src \
  --title "sound: uaudio: finish the UAC2 shared-clock fix" \
  --body-file /path/to/upstream-series/PR-DESCRIPTION.md
```

Same route as #2323. Request **christos@** (committed 755685dd665e) and CC
**hselasky@** — commit 2/3 is isochronous endpoint policy, his side.

## 5. Comment on the bug

Paste `BUGZILLA-COMMENT.txt` into
<https://bugs.freebsd.org/bugzilla/show_bug.cgi?id=295933>, replacing `NNNN`
with the PR number from step 4.

## If you would rather not use GitHub

`git format-patch` output is already mbox format, so the series can go to
`freebsd-multimedia@` / `freebsd-usb@` with `git send-email`, or straight onto
the bug as three attachments.
