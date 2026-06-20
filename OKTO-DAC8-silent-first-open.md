# OKTO DAC8 STEREO — silent / unlocked on the first stream open ("issue drc.sh twice")

## Summary

When the DRC chain is (re)started, the OKTO DAC8 frequently **opens the stream
but routes no audio** on the *first* open. The front panel shows "play" and the
host sees a healthy, running stream (USB async feedback present, no underruns),
yet nothing comes out of the speakers. **Opening the device a second time at the
same rate fixes it** — which is why `drc.sh` has to be issued two or three times
before music actually plays.

`drc.sh` already tries to automate the workaround by **priming** the DAC (it
opens brutefir once at the new rate, tears it down, then opens it again — the
"second" open). In practice the prime is **not always enough**, and the same
silent-first-open is visible from a completely different path: the
`*-nodrc` browser launchers (`firefox-nodrc` etc.) also have to be started
several times before the browser produces any sound — even though all they do is
`drc.sh off` and then open `/dev/dsp0` directly.

This document audits the candidate causes. They fall into two groups:

1. **Device / kernel-driver behaviour** — the DAC needs settle/relock time on a
   cold open that the host (`uaudio(4)` on FreeBSD, `snd-usb-audio` on Linux)
   does not wait out. This is the same class of issue as the 44.1 kHz flicker
   already filed in `OKTO-DAC8-FreeBSD-44k1-flicker.md`.
2. **`drc.sh` orchestration gaps** — the prime is keyed on stale state, fully
   closes the device between prime and real open, and never verifies that audio
   actually flows, so it both *misses* cases that need priming and *reports
   success* when the DAC is silent.

---

## Test environment (live, this box)

| Item | Value |
|------|-------|
| DAC | OKTO RESEARCH DAC8 STEREO (`pcm0: <OKTO RESEARCH DAC8STEREO> (play) default`) |
| Host OS | FreeBSD 15.1-RC1 (GENERIC, amd64), `uaudio(4)` |
| Device node | `/dev/dsp0`, single-open: `dev.pcm.0.bitperfect: 1`, `dev.pcm.0.play.vchans: 0` |
| Async feedback (idle) | `dev.pcm.0.feedback_rate: 48001` |
| `hw.usb.uaudio.buffer_ms` | 8 (already the maximum) |
| Player | MPD, OSS output to `/dev/dsp0` (direct) or `/dev/dsp.play` (DRC) |
| DRC chain | MPD → `/dev/dsp.play` → virtual_oss → `/dev/dsp.loop` → brutefir → `/dev/dsp0` |
| Also affected | Linux side (`snd-usb-audio`, `hw:0,0`) and the `*-nodrc` browser launchers |

The same DAC plays perfectly once it is "warm"; the failure is strictly at the
**cold open** boundary.

---

## Who opens the single-open DAC, and when it goes cold

`/dev/dsp0` is bit-perfect and has vchans disabled, so **exactly one client may
hold it at a time**:

- **DRC on:** `brutefir` opens `/dev/dsp0` (output) and reads `/dev/dsp.loop`;
  MPD plays into `/dev/dsp.play`.
- **DRC off / direct:** MPD's `OKTO-DAC` output opens `/dev/dsp0` directly.
- **Browser bypass:** `firefox`/`chrome`/`chromium` open `/dev/dsp0` via OSS
  after `drc.sh off` has freed it.

Every transition between these states **fully closes `/dev/dsp0`**. On close the
DAC stops streaming and (apparently) drops back toward an idle/relock state;
the *next* open is therefore a cold open and is the one that can route silence.

---

## What `drc.sh` priming does today, and why it is not enough

`drc.sh` (lines ~350–510) detects a rate change and primes:

```
prev_rate = state_to_rate(last_arg)          # last rate drc.sh itself set
prime=1   if mode != off AND prev_rate != actual_rate
...
if prime:
    start_brutefir        # open #1  (the "prime")
    sleep 1
    stop_brutefir         # full close — DAC goes idle
    sleep 0.5
start_brutefir            # open #2  (the "real" open)
```

Four distinct reasons this misses or fails:

### P1 — Prime is keyed on `last_arg`, not on the DAC's actual clock state
`prev_rate` comes from `last_arg`, i.e. *the last rate `drc.sh` successfully
set* — not what the hardware is currently doing. The DAC's clock can change
without `drc.sh` knowing:

- **First start after boot.** `drc-usb-audio.service` runs `drc.sh restore`,
  which re-applies `last_arg`. So `prev_rate == actual_rate` and **priming is
  skipped on the single coldest open in the whole system's life** — the DAC is
  still in its enumerated default (384 kHz / 48 kHz family) and the restored
  rate (often 44.1 kHz family) needs a full clock-domain switch with no prime.
  This matches "I have to run it again right after the box comes up".
- **`off` → direct MPD playback → `on` at the "same" rate.** While DRC is off,
  MPD's `OKTO-DAC` output opens `/dev/dsp0` at the *track's* native rate, moving
  the DAC clock with no update to `last_arg`. A later `drc.sh <that rate>` sees
  `prev_rate == actual_rate` and skips the prime even though the DAC was just
  left cold/idle (or on a different family) by direct playback.
- **DAC replug / power-cycle.** The udev/devd ATTACH re-triggers `drc.sh`, but
  `last_arg` is unchanged, so a freshly-enumerated (cold) DAC may get no prime.

### P2 — The prime fully closes the device before the real open
With a single-open DAC the prime *must* close `/dev/dsp0` before reopening it
(the two opens cannot overlap). If the silent-first-open is triggered by the
**idle→stream cold start** (not purely by the rate value), the `sleep 0.5` idle
window is enough for the DAC to fall back to the relock state, so the "real"
open is cold *again*. The prime only helps if the device remembers the rate
across the close; the 0.5 s gap is an untuned guess that may be **too short**
(DAC not finished releasing the prime stream) or **too long** (DAC powers its
clock down after idle and forgets the primed rate).

### P3 — The real open starts iso transfers before the clock has relocked
This is the device/driver hypothesis from `OKTO-DAC8-FreeBSD-44k1-flicker.md`,
hypotheses (1)/(2): UAC2 sets the rate via `SET_CUR` on the clock source and the
host starts isochronous transfers immediately. If the OKTO needs extra
settle/relock time when its PLL changes family, the stream that "took the lock"
plays into a DAC that is still relocking → silence for that stream. A subsequent
open finds the clock already on the right family (no relock) → audio. That is
exactly the "works on the second try" signature.

### P4 — `drc.sh` never verifies audio actually flows
`start_brutefir` only checks that the brutefir **process stays alive**. The OKTO
failure mode is "process up, USB feedback present, **routes silence**" — which
process-liveness cannot detect. So `drc.sh` prints `DRC active …` and exits 0
while the speakers are silent, and the user is the one who notices and reruns.
There is no closed-loop confirmation (e.g. checking `dev.pcm.0` interrupts are
incrementing and the channel is `RUNNING`, or polling the DAC's lock state).

---

## The browser launchers confirm the root cause is device-side

`firefox-nodrc.sh` → `lib.sh:drc_bypass_begin()` does only:

```
drc.sh off          # stop brutefir, free /dev/dsp0, enable direct OKTO-DAC
firefox --no-remote
```

There is **no prime here at all**, yet the browser still needs several launches
before it produces sound. Since this path shares nothing with brutefir except
the act of *opening `/dev/dsp0` cold*, it isolates the fault to the
**DAC + kernel uaudio open path**, not to brutefir or virtual_oss. Two
sub-causes:

- **C-cold:** same silent-first-open as P3 — the first OSS open after the DAC was
  just released routes silence.
- **C-busy (EBUSY race):** `drc.sh off` returns before MPD's player thread has
  actually closed/opened `/dev/dsp0` (the script notes this race for the DRC
  path and inserts `sleep 0.5`, but the browser launcher has no equivalent
  guarantee that the device is *free and warm* before firefox opens it). If MPD
  is mid-(re)open of `OKTO-DAC`, firefox's open fails and it silently falls back
  to no audio until retried.

---

## Cause taxonomy

| # | Layer | Cause | Evidence |
|---|-------|-------|----------|
| P1 | drc.sh | Prime keyed on `last_arg`, not real DAC clock → skipped on boot/restore, off→on, replug | `restore` re-applies same rate ⇒ `prev_rate==actual_rate` ⇒ no prime |
| P2 | drc.sh | Prime fully closes DAC; 0.5 s gap untuned (too short/too long) | single-open DAC, `bitperfect=1`, `vchans=0` |
| P3 | device/uaudio | Iso transfers start before clock relock on a cold/family-switch open | mirrors 44k1-flicker hyp. (1)/(2); "second open works" |
| P4 | drc.sh | No verification that audio flows; only checks process liveness | "process up, routes silence" is undetectable to current check |
| C-cold | device/uaudio | First OSS open after release routes silence (no prime in browser path) | `firefox-nodrc` needs multiple launches |
| C-busy | drc.sh/MPD | EBUSY race: device not yet free/warm when next client opens | single-open + async `mpc disable/enable` |

---

## Recommended mitigations (ranked, host-side)

These are `drc.sh` / launcher changes — they work around the device behaviour
without depending on a firmware fix.

1. **Warm-up the clock with silence before enabling the MPD output (best).**
   brutefir streams continuous output to `/dev/dsp0` as soon as it starts (it
   feeds zeros when its input is silent). So instead of priming with a
   close/reopen, start the *real* brutefir, **wait ~1.5–2 s feeding silence**,
   and only then `mpc enable only DRC-native`. The DAC relocks on the steady
   silent stream; by the time audio arrives the lock is solid. This keeps the
   device open across the relock (no second cold open) and can replace the
   close/reopen prime entirely.

2. **Prime on hardware state, not `last_arg`.** Drop the
   `prev_rate == actual_rate` optimisation (or override it) so the chain is
   *always* warmed when turning DRC on — most importantly on the first
   start after boot/restore and on `off → on`. The extra ~2 s is a good trade
   for not having to rerun by hand.

3. **Closed-loop verification + auto-retry.** After starting, confirm the
   stream is actually streaming (`dev.pcm.0` interrupts incrementing / channel
   `RUNNING` on FreeBSD; `/proc/asound/card0/pcm0p/sub0/hw_params` not `closed`
   on Linux) and, if possible, that the DAC reports lock; retry the open if not.
   At minimum this turns a silent success into a logged, retried failure.

4. **Browser path: warm + de-race the handoff.** In `drc_bypass_begin`, after
   `drc.sh off`, feed a short silence burst to `/dev/dsp0` (e.g. ~1.5 s of
   zeros) to lock the clock before launching the browser, and ensure the device
   is free first. This is the browser-side equivalent of mitigation (1).

5. **Make the settle constants tunable.** The hard-coded `sleep 0.5` / `sleep 1`
   in the prime and teardown are guesses; expose them (env or vars) so the
   relock window can be tuned to this DAC.

---

## Device / kernel hypotheses to confirm with the manufacturer / driver

Carrying over from `OKTO-DAC8-FreeBSD-44k1-flicker.md`, focused on the cold open:

1. **Clock relock settle time on open.** Does the DAC require a defined
   settle/lock-valid window after `SET_CUR` on the clock source before iso
   data is sent? If so, the host should poll the clock-validity control before
   starting transfers. (`uaudio` and `snd-usb-audio` differ here; Linux's
   sequencing apparently masks it more often.)
2. **Behaviour on stream close→reopen.** Does the device discard its locked
   rate on stream close and require a fresh relock on the next open (making
   *every* reopen a cold open)? This determines whether priming can ever be
   reliable or whether the host must keep a stream open.
3. **Async feedback validity during relock.** Idle feedback reads `48001`;
   confirm the feedback value is valid/stable from the first micro-frame after a
   family switch, so the host does not over/under-feed during relock.

## Diagnostics to capture next time it happens

- On a *failed* (silent) open vs a *successful* one, compare `cat /dev/sndstat`
  (verbose) — `interrupts`, `underruns`, channel flags — to confirm the host
  side is identical and the difference is device-internal.
- `sysctl dev.pcm.0` before/after, especially `feedback_rate` and `mode`.
- Count how many opens it takes from cold (boot) vs warm (after one good open),
  per rate family, to characterise whether it is purely cold-open or
  family-switch dependent.
