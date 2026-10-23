---
name: deploy-box
description: Update this machine (dal, bee, arki) to the latest sources - pull, reconcile git, rebuild, reinstall, restart and verify - keeping the box's own host.cmake settings (box name, DR sync repo, paths). Use when the user says "update/deploy/reinstall this box", "pull and install", or mentions bee, arki or dal in that sense.
---

# Deploy this box

The user runs Claude on the headless boxes and says "pull, reconcile, reinstall,
deploy". That is standing authorization for exactly the steps below (install
under `/usr/local`, restart the omdrc services). Nothing else on the system.

## The boxes

| host | OS | role | `DR_SYNC_BOX` | service manager |
|------|----|------|---------------|-----------------|
| dal  | FreeBSD 15 | office | `dal`  | rc.d (`service omdrcctrl ...`) |
| bee  | FreeBSD 15 | home   | `bee`  | rc.d |
| arki | Arch Linux | home   | `arki` | systemd (`systemctl ... omdrcctrl`) |

bee and dal are the same FreeBSD 15 setup. arki is Arch (pacman; packages
may differ from Debian-style names).

`DR_SYNC_REPO` is `https://github.com/delleceste/omdrc-dr-log.git` on all of them.

## Host-specific settings live in `host.cmake`

`host.cmake` (repo root, git-ignored, never committed: it holds keys) is the only
place per-box values live: `AUDIO_USER`, `MUSIC_DIR`, `OMDRC_SITE_DATA_DIRS`,
`FRIENDLY_NAME`, `DR_SYNC_REPO`, `DR_SYNC_BOX`, ... The installed `commands.conf`
is **overwritten by every install** and rendered from it, so a value missing
from `host.cmake` is lost on reinstall.

1. `hostname -s` must be one of the table above; otherwise stop and ask.
2. `grep -n DR_SYNC host.cmake`: both must be set and `DR_SYNC_BOX` must equal
   the table entry for this host. If `host.cmake` is missing or lacks them,
   add the two lines (copy the rest from `host.cmake.sample` only if the file
   does not exist, and ask for the values that cannot be guessed - paths, user):
   ```
   set(DR_SYNC_REPO "https://github.com/delleceste/omdrc-dr-log.git" CACHE STRING "")
   set(DR_SYNC_BOX  "<bee|arki|dal>" CACHE STRING "")
   ```
3. Never replace, regenerate or `git checkout` `host.cmake`.

## Steps

1. **Sources.** `git status --short`, `git fetch`. Local uncommitted changes:
   keep them (they may be the user's work); `git pull --rebase --autostash`.
   Diverged history or conflicts: resolve only if trivial and clearly
   unrelated to the user's work, otherwise stop and show the state.
   Report the commits pulled (`git log --oneline ORIG_HEAD..HEAD`).
2. **Build directory.** `-C host.cmake` only applies to entries that do not
   exist yet. If `host.cmake` changed since the build dir was made (or a new
   variable was added), use a fresh dir: `rm -rf build && mkdir build`, then
   `cmake -S . -B build -C host.cmake`. Otherwise reuse `build/`.
   `cmake --build build`. Fix build errors; do not install on failure.
3. **Backup, then install.** Copy the installed
   `<prefix>/etc/omdrcctrl/commands.conf` to the scratchpad. `sudo cmake
   --install build`. Then `diff` the backup against the new file and report any
   difference that is not explained by the pulled commits (hand edits that the
   install just removed -> tell the user, and offer to move them into
   `host.cmake` or the template).
4. **MPD service (every deploy).** The user has authorized these rc.conf edits.
   - **FreeBSD (dal, bee):** MPD runs from the project's `omdrc_musicpd`, never
     the package's `musicpd`. The package owns `rc.d/musicpd`, and its stock
     script ignores `musicpd_config`; every `pkg upgrade` puts it back. The
     install prints a `renderers: rc.conf ...` line: `OK` or a WARNING naming
     the fix. Check anyway:
     ```sh
     for f in /etc/rc.conf /etc/rc.conf.local; do
       sysrc -f $f -n musicpd_enable musicpd_config omdrc_musicpd_enable omdrc_musicpd_config 2>/dev/null
     done
     ```
     Migrate whatever is not yet in this state, editing the file that holds the key:
     `musicpd_enable` and `musicpd_config` absent, `omdrc_musicpd_enable="YES"`.
     ```sh
     sudo sysrc [-f <file>] -x musicpd_enable musicpd_config
     sudo sysrc omdrc_musicpd_enable=YES
     ```
     Do not add `omdrc_musicpd_config`: it defaults to
     `/usr/local/etc/open-media-drc/musicpd.conf`. Set it only if the old
     `musicpd_config` named a different file that exists, and report it.
     Then switch the running MPD over. `omdrc_musicpd stop` finds the pid
     through our config; the stock script would look in the wrong place:
     ```sh
     sudo service omdrc_musicpd stop; pgrep -lf musicpd   # must be empty
     sudo service omdrc_musicpd start && sudo service omdrc_renderer restart
     ```
     Verify: `service omdrc_musicpd status`; exactly one `pgrep -lf musicpd`
     with `/usr/local/etc/open-media-drc/musicpd.conf`; `mpc outputs`;
     `/usr/local/etc/rc.conf.d/musicpd/omdrc_audio` is gone (the install
     removes it) and `/usr/local/etc/rc.conf.d/omdrc_musicpd/omdrc_audio`
     exists. Leave the package's `/usr/local/etc/musicpd.conf` alone; it is
     unused.
   - **arki (Arch):** nothing to migrate. The drop-in
     `/usr/local/lib/systemd/system/mpd.service.d/open-media-drc.conf` is
     not owned by pacman, so upgrades keep it. Verify after
     `sudo systemctl daemon-reload`: `systemctl show -p User,ExecStart mpd`
     names `AUDIO_USER` and `/usr/local/etc/open-media-drc/mpd.conf`, and
     `systemctl is-active mpd`. If an `/etc/systemd/system/mpd.service.d/`
     override also sets `ExecStart`, report it, since it wins over ours.
5. **Restart only what changed.** Panel changes (`omdrc-ctrl/`): restart
   `omdrcctrl`. Audio chain/MPD changes: follow the install output's printed
   enable steps, and remember that **restarting musicpd makes upmpdcli exit -
   start it again**. Do not restart audio services for a docs or panel-only
   change.
6. **Verify.**
   - service up: `service omdrcctrl status` / `systemctl status omdrcctrl`
   - panel answers: `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:9090/`
   - rendered config: `grep -A4 '^\[dr_sync\]' <prefix>/etc/omdrcctrl/commands.conf`
     shows `repo = ...` and this host's `box = ...`
   - DR sharing (after ~30 s or **Share now**): the DR page / API reports
     `ok` and lists the other boxes; the clone is `dr-sync/` in the state dir
     (`$OMDRC_STATE_DIR`, else `~/.local/state/omdrc` of the service user).
7. **DR sync credentials.** If the sync reports a git error, the service user
   has no credentials on this box. That needs an interactive login: give the
   user the commands (`gh auth login` then `gh auth setup-git` as the service
   user, or an SSH deploy key with write access) and ask them to run them with
   `!`. Never put tokens in `commands.conf` or the repo. Test afterwards with
   `GIT_TERMINAL_PROMPT=0 git ls-remote <repo>` as that user.
8. **FIFO rate drift.** On a box not yet checked, compare the installed
   `musicpd.conf` Spectrum FIFO format with the repo's (44100:32:2); the
   install normally fixes it, but verify after the restart.

## Report

Pulled commits, whether the build dir was fresh, `commands.conf` diff verdict,
services restarted and their state, MPD service migration (rc.conf keys changed,
or already done), DR sync status, anything not completed.
Do not commit anything unless source files were changed as part of the task.
