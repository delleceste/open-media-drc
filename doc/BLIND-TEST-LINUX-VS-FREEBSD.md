# Double-blind listening test: Linux (arki) vs FreeBSD (bee)

This is the plan for a session that Claude runs from **dixi**. The listener
(Giacomo) talks to Claude only. Claude reboots the dual-boot box at
192.168.1.9 into a randomly chosen OS for each trial. Neither the listener nor
Claude learns which OS played until the whole session is over.

To start, say "blind test: go" and point Claude at this file.

## Roles and blinding

- **Listener.** Picks the tracks, listens, and answers "Linux or FreeBSD?"
  after each trial. Uses only the Claude terminal on dixi, plus at most a
  plain MPD client (see "Playback").
- **Claude.** Runs `blindtest` (see below) and never runs any command whose
  output could reveal the OS: no `uname`, no `hostname`, no `drc.sh status`
  printed raw, no service names, no boot logs. All box access goes through the
  runner, and the runner prints only OS-neutral lines.
- **Truth file.** The runner draws the OS and writes it to
  `~/blindtest/<session>/truth.log` on dixi. Claude does not open that file
  until the reveal step, so Claude is blind as well. This is what makes the
  test double-blind rather than single-blind.
- **Answers file.** Claude writes the listener's answer for each trial to
  `~/blindtest/<session>/answers.log` *before* the next trial starts.

The listener should not expand tool output in the terminal (ctrl+o). They
should also not look at the box, its screen, its LEDs or the router, or open
the OMDRC app or kiosk during the test.

## How the box is switched

Both OSes have a reboot script on the box. On arki it is
`grub-reboot-to-freebsd.sh` (sticky, `grub-set-default`). On bee it is
`/usr/local/sbin/reboot-to-arch` (not in this repo). It sets GRUB's one-shot
`next_entry`, but every menuentry calls `savedefault`, so Arch becomes the
saved default once it boots and a later plain reboot stays on Linux. Each
trial is **one reboot**:

- target is the other OS: run that OS's reboot script;
- target is the same OS: plain reboot (`systemctl reboot` /
  `shutdown -r now`). This is the "fake" switch.

Each trial is an independent coin flip, so a fake switch happens about half
the time. The box makes no OS-specific sound at boot (confirmed by the
listener), so the only physical cue is how long it takes, and the fixed `T`
below hides that.

### Hiding the boot-time difference

Linux boots much faster than FreeBSD. The runner measures nothing aloud and
waits until a **fixed wall-clock time `T` after the trial started** before it
prints READY. `T` is set during the dry run to the slowest observed path (the
second reboot going to FreeBSD, starting from FreeBSD), plus a margin of at
least 30 s. A trial that overruns `T` is voided (see below) rather than
announced late, because a late READY would point to FreeBSD.

The checks run at the same point in every trial and print the same lines on
both OSes, so their duration gives nothing away.

## Checks after each boot (OS-neutral output)

The runner compares the box with a reference snapshot taken on *both* OSes
during the dry run, and prints only `checks: OK` or `checks: FAILED (<neutral
reason>)`:

- DRC is on: geometry `120.green`, design `@120.green.multipos.fdw6` (home), BruteFIR
  running.
- BruteFIR attenuation and the MPD volume equal the reference.
- Output rate and sample format for the test tracks equal the reference.
- The controller (`omdrcctrl`) and MPD respond.
- No glitch or xrun alert has been raised since boot.

The settings are restored automatically from the saved session at boot, so a
failure means something is wrong. A trial that fails its checks or a boot is
**void**: it is not counted, the runner logs the reason in the truth file, and
a fresh trial with a new coin flip replaces it. Claude only says "trial void,
technical problem, redoing it".

## The runner: nothing on screen but neutral lines

The runner is `tools/blindtest/blindtest` (Python 3, stdlib only). It runs on
dixi, and its data lives in `~/blindtest/` (or `$BLINDTEST_HOME`):
`config.json`, a private `known_hosts` holding both host keys, and one
directory per session.

Prep commands are **not blind**. They may name the running OS, so run them
only before a session:

```
blindtest init                   default config (reboot commands, expected DRC)
blindtest keyscan                on each OS: store its ssh host key
blindtest doctor                 ssh, sudo -n, reboot commands found, DRC/MPD state
blindtest capture-queue          save the MPD queue (built in the app) as tracks 1..n
blindtest prep-queue             on each OS once: stop, clear, load tracks, single=1
blindtest snapshot               on each OS: reference state; says if the two differ
blindtest calibrate linux|freebsd   timed reboot; run all four paths, set ready_after_s
```

Session commands are blind. Claude uses only these once the session starts:

```
blindtest session new <name> <trials>
blindtest trial                  "trial N: switching" ... "checks: OK" + "trial N: READY"
                                 or "trial N: void (technical problem)"
blindtest play 1|2|3 / stop / pause / resume   -> "ok" / "failed"
blindtest answer linux|freebsd [1-3]           -> "trial N: recorded"
blindtest status                 trials done/answered/void, nothing else
blindtest reveal                 score table + p, only when every trial is answered
```

- The coin comes from Python `secrets`, and the target is written to
  `private/pending.jsonl` before the reboot is issued. The outcome and
  timings go to `private/truth.jsonl`, and the box's replies to
  `private/trial.log`. Tracebacks from blind commands go to
  `private/errors.log`, and the screen shows only `failed`.
- `trial` refuses to start while the previous trial is unanswered. A void
  trial is reprinted at the same `T` as a good one and redone with a new coin.
- ssh runs with `-q`, `LogLevel=QUIET`, `BatchMode=yes` and its own
  known_hosts file, so no host-key warning or banner can name the OS.
- `T` (`ready_after_s`) can exceed Claude's 10-minute foreground Bash limit. In
  that case Claude runs `trial` in the background and waits for it to finish.

## Playback without revealing the box

The kiosk header shows the mascot (Tux or Beastie, with the OS name in its
`title`). There is no setting that hides it, and Now, DRC, Chain and Source
also show OS-specific details (`virtual_oss`, ALSA). So during the test **the
OMDRC app and kiosk stay closed**. Playback uses one of these:

- **The runner (default).** The listener tells Claude "play track 1", "play
  track 2", "stop", and so on. Claude runs `blindtest play 1` etc., which
  plays that position of the queue from the start. MPD's `single` mode is on,
  so it stops at the end of the track. The queue is built **once per OS during
  prep** (`capture-queue` on the first OS, then `prep-queue` on each). MPD
  keeps it across reboots, and every trial's checks confirm it is still exactly
  the test tracks.
- **A plain MPD client** (only as a fallback) pointed at `192.168.1.9:6600`, for example MALP on the
  phone. It shows only the IP. Check beforehand that it does not display the
  server's version string or any output names.

During the dry run, check that the same queue entries resolve on both OSes.
Local paths come from the same library. Qobuz URLs go through upmpdcli, which
needs to be up on both.

## Session flow

1. **Tracks.** Choose 2 or 3 tracks together and use only those. Note their
   format (44.1k vs 96k and so on) because the reference snapshot depends on
   it.
2. **Trial loop.** Claude says "trial N: switching" and runs the runner, which
   prints READY after `T`. The listener plays the tracks and listens for as long
   as they like. Claude then asks: **"Trial N — was it Linux or FreeBSD?"**
   (with optional confidence 1–3) and writes the answer down.
3. **No feedback** until the end: Claude does not say whether an answer was
   right.
4. **Reveal.** After the last trial, Claude opens the truth file, matches it
   against the answers and reports the score, the p-value and the per-trial
   table.

### How many trials

The p-value is for a one-sided binomial test against guessing (p = 0.5):

| trials | correct needed for p < 0.05 | p at that score |
|---|---|---|
| 10 | 9 | 0.011 (8/10 gives 0.055) |
| 12 | 10 | 0.019 |
| 16 | 12 | 0.038 |
| 20 | 15 | 0.021 |

Each trial costs one reboot plus `T` plus listening time, roughly 6–10 min.
**16 trials** is a good target, split over two sittings if needed. Fix the
count before starting and do not stop early because of a good or bad streak.

## Preparation (dry run, before the real session)

Do this once, ideally the day before:

- [ ] On dixi: key-based `ssh` to 192.168.1.9 works on **both** OSes. Both host
      keys are stored in one dedicated known_hosts file used by the runner, so
      the key changing between OSes produces no warning.
- [ ] Passwordless `sudo` works for the reboot commands on both OSes.
- [ ] On arki, `grub-reboot-to-freebsd.sh -c` passes. On bee,
      `reboot-to-arch --prepare-only` then `--cancel` passes. Run all four
      paths once: L→F, F→L, L→L, F→F.
- [ ] DRC `120.green` / `fdw6.multipos` comes up on both OSes after a reboot,
      with the same attenuation and volume. Take the reference snapshot.
- [ ] Time all four paths (start Linux or FreeBSD × target Linux or FreeBSD).
      Set `T`.
- [ ] `blindtest init`, then `keyscan` + `doctor` on each OS.
- [ ] Build the queue of test tracks in the app, `capture-queue`, then
      `prep-queue` + `play 1..3` on each OS, then `snapshot` on each OS.

## After the session

- Leave GRUB's default set to whichever OS the listener wants.
- Keep `truth.log` and `answers.log`, and add a short results section to
  `doc/SOUND-QUALITY-INVESTIGATION.md` (§10, "Level matching / expectation" is
  the open item this test addresses).
