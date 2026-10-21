# Double-blind listening test: Linux (arki) vs FreeBSD (bee)

This is the plan for a session that Claude runs from **dixi**. The listener
(Giacomo) talks to Claude only. Claude reboots the dual-boot box at
192.168.1.9 into a randomly chosen OS for each trial. Neither the listener nor
Claude learns which OS played until the whole session is over.

To start, say "blind test: go" and point Claude at this file.

## Roles and blinding

- **Listener.** Picks the tracks, listens, and answers "Linux or FreeBSD?"
  after each trial. Uses only the Claude terminal on dixi plus the playback
  controls on the phone app or kiosk.
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

The listener should not expand tool output in the terminal (ctrl+o), look at
the box, its screen, its LEDs or the router, or open any page in the web UI or
app that shows system information (diagnostics, bit-perfect, configuration,
logs). Use only Now, the library and the playback controls.

## How the box is switched

GRUB (BIOS) is the boot loader, and **Arch Linux must be the saved default**.
`scripts/grub-reboot-to-freebsd.sh` is sticky (`grub-set-default`) and there
is no script on the FreeBSD side to switch back, because the return path is a
manual pick at the GRUB menu. That pick cannot be made remotely, so the test
uses a one-shot switch instead:

- Linux → FreeBSD: `sudo grub-reboot "<FreeBSD entry>" && sudo systemctl reboot`
  (one-shot: GRUB sets `boot_once`, so `savedefault` does not record it).
- FreeBSD → Linux: plain `sudo shutdown -r now`. The one-shot entry has been
  used up, so GRUB boots the saved default, Arch Linux.

**Every trial does exactly two reboots**, whatever the outcome, so the number
of DAC drop-outs, relay clicks and fan spin-ups is always the same:

1. Reboot whatever is running. This always lands in Linux.
2. From Linux, either reboot plainly (target Linux) or one-shot into FreeBSD
   (target FreeBSD).

A trial whose target is the OS that just played is a normal outcome, not a
special case. Each trial is an independent coin flip, so repeats happen about
half the time.

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

- DRC is on: geometry `120.green`, design `fdw6.multipos` (home), BruteFIR
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

Each trial costs two reboots plus `T` plus listening time, roughly 6–10 min.
**16 trials** is a good target, split over two sittings if needed. Fix the
count before starting and do not stop early because of a good or bad streak.

## Preparation (dry run, before the real session)

Do this once, ideally the day before:

- [ ] On dixi: key-based `ssh` to 192.168.1.9 works on **both** OSes. Both host
      keys are stored in one dedicated known_hosts file used by the runner, so
      the key changing between OSes produces no warning.
- [ ] Passwordless `sudo` works for the reboot commands on both OSes.
- [ ] On arki: `grub-reboot-to-freebsd.sh -l` lists the exact FreeBSD entry
      title, `-c` passes, and `saved_entry` is Arch Linux (set it with
      `sudo grub-set-default "<Arch entry>"` if not).
- [ ] The one-shot path works: from Linux, `grub-reboot` into FreeBSD, then a
      plain reboot from FreeBSD comes back to Linux.
- [ ] DRC `120.green` / `fdw6.multipos` comes up on both OSes after a reboot,
      with the same attenuation and volume. Take the reference snapshot.
- [ ] Time all four paths (start Linux or FreeBSD × target Linux or FreeBSD).
      Set `T`.
- [ ] Write `blindtest` (in `~/blindtest/` on dixi, not in this repo). It
      should provide `prep`, `trial`, `answer` and `reveal` subcommands, use
      Python `secrets` for the coin, and print only neutral lines.
- [ ] Listen to whether the box makes an OS-specific sound you can hear from
      the listening seat, such as a BIOS/boot beep, a disk spin-up or a
      different fan profile. If it does, fix it or move away from the box during
      the switch.
- [ ] Decide who starts playback. Either the listener uses the app, or the
      runner queues and starts the track through MPD so that every trial
      starts identically.

## After the session

- Leave GRUB's default set to whichever OS the listener wants.
- Keep `truth.log` and `answers.log`, and add a short results section to
  `doc/SOUND-QUALITY-INVESTIGATION.md` (§10, "Level matching / expectation" is
  the open item this test addresses).
