# omdrc-cdin — CD / S/PDIF input for the DRC chain

Bridges a digital transport into the open-media-drc chain:

```
CD player ──S/PDIF 44.1k──► ESI U24 XL ──USB──► /dev/dspN
                                                    │  omdrc-cdin
                                                    ▼
                                            ring (a few seconds)
                                                    │
                                                    ▼
                                             /dev/dsp.play  (virtual_oss)
                                                    │
                                                    ▼
                                                BruteFIR ──► /dev/dsp0 (Okto DAC8)
```

It takes the same seat `mpv` already takes for video (`video/lib/drc-audio.sh`):
a non-MPD writer into the virtual_oss loopback that BruteFIR reads.

## The problem it solves

Capture is slaved to the CD's crystal; the DAC runs on its own. Samples arrive
at `f_cd` and must leave at `f_dac`, and the two differ by a few ppm **forever**.
On Linux `alsaloop(1)` reconciles this. FreeBSD ships no equivalent — which is
what made this look blocked, but the missing piece was a tool, not a kernel
facility: FreeBSD's OSS layer exposes `SNDCTL_DSP_CURRENT_IPTR/OPTR`,
`GETODELAY` and `GETISPACE/GETOSPACE`, and this daemon does not even need them.
A blocking `read()` runs at the CD's clock, a blocking `write()` runs at the
DAC's, so the ring fill between them *is* the drift signal.

**There is no resampler.** The data path is a `memcpy`, so what reaches BruteFIR
is bit-identical to what the transport sent — and that is verifiable with
`scripts/verify-bitperfect.sh`, which would be meaningless if a resampler sat in
the path. Drift is absorbed by the lead instead.

## The lead is the only number that matters

The lead is simultaneously three things:

* the **drift margin** — how long before the buffer runs out,
* the **startup delay** — you cannot pre-fill a lead you have not waited for,
* the **transport lag** — every Play/Stop/Skip is heard this much later.

They cannot be tuned separately. The arithmetic that sets the upper bound:

> `time-to-splice = lead ÷ drift`. At a pessimistic **50 ppm**, a **2000 ms**
> lead covers **~11 hours** of continuous gapless audio.

A disc is at most 80 minutes, so **drift cannot cause a discontinuity inside a
disc**. Parking minutes of audio on disk would buy nothing here: it only buys
time-shift and archival, which are features, not correctness.

The *lower* bound is not set by drift at all — it is set by transport seeks and
USB stalls (see below). That is why the default is 2000 ms rather than the
~50 ms drift alone would need.

### Calibrating it during deployment

```sh
omdrc-cdin --in /dev/dspN --out /dev/dsp.play --lead 2000 -d -s 10 -l /tmp/cdin.log
```

1. Play a full disc. In the stats line, **`starves` must stay 0** — each one is
   an audible dropout.
2. After ~5 minutes the `drift` field settles and reports the measured ppm plus
   the projected headroom (`lead drains in N h`). This replaces the 50 ppm
   assumption with your actual hardware.
3. To cut latency, step `--lead` down (1500, 1000, 750…) and repeat. The first
   value that produces **any** starve is below the floor your transport
   imposes — go back up one step and keep a margin.
4. Below ~250 ms the daemon warns: there is nothing left to absorb a seek.

Record the final value; it becomes the deployed default.

## What the CD transport does to the stream

A CD player's S/PDIF output is **always 44.1 kHz**. Transport actions change
*what* is sent, never *how fast* — so there is no re-lock, ever.

| Action | On the wire | Daemon behaviour |
|---|---|---|
| **Pause** | Carrier alive, digital silence (most players); some drop carrier | Plays the silence through. Lead unchanged — both ends still clocked |
| **Skip track** | Brief mute (0.1–1 s), then audio | Just a short silence. Heard `lead` later |
| **Fast fwd / rewind** | Chopped audible scan snippets, or mute — **rate unchanged** | Ordinary audio or ordinary silence. Nothing special |
| **Stop / tray / power** | Carrier drops | `read()` stalls or errors → session ends → device reopened |

The real hazard is a player that briefly **drops carrier** during a seek (some
do across pregap or index boundaries). That is a sub-second input stall, and
absorbing it is the second reason for a seconds-scale lead, independent of
drift. Without it, every such seek would be an audible dropout.

Unavoidable cost: **Stop is also heard `lead` late** — the music runs on for
about two seconds after you press it.

Every row of that table is reproducible without a CD player: `--transport`
scripts them against a WAV disc, so the claims above are testable rather than
merely asserted. See "Scripting the buttons" below.

## Status

**Phase 1 (this code)** — capture → ring → playback, ratio 1.0, with device
retry, periodic stats and file logging, lossless width negotiation for
bit-perfect output devices, and a simulated CD transport standing in for the
capture hardware.

Exercised end to end on the dev box (`dal`, which has no capture device):
a 12-track 16-bit rip played to a bit-perfect `/dev/dsp0` with `starves 0`,
`drops 0` and the lead inside a one-period band throughout, including across
track gaps, skips, seeks and a 4 s pause; an 800 ms scripted carrier dropout
cost exactly 800 ms of lead and starved nothing. What is *not* tested is
everything on the far side of `/dev/dspN` — see Phase 0.

**Phase 2, first half (this code)** — the `NO_CARRIER → IDLE → PLAYING` state
machine and the lazy output open. This is what makes the daemon something you
leave running: see "Running it continuously" below.

**Phase 2, second half (next)** — drift resync during the inter-track silence,
by padding or trimming, with a zero-crossing / 10 ms crossfade splice as the
fallback. Not needed inside a disc (drift cannot cause a discontinuity in 80
minutes) but needed for a session that never stops. Seams are marked
`TODO(phase2b)`.

**Phase 3** — an `rc.d` service (done, `rc.d/omdrc_cdin.in`) and the web panel
integration (done, the CD input card — see below). Still open: a `drc.sh cd`
verb and a `drc-status.sh` line.

## Running it continuously

The daemon is meant to be up all the time — CD player on or off, interface
plugged in or not:

```sh
# /etc/rc.conf
omdrc_cdin_enable="YES"
omdrc_cdin_in="/dev/dsp1"        # the ESI, NOT the DAC; check /dev/sndstat
```

That is only safe because of what the state machine does with the *output*
device, and the reason is sharper than "it would be rude to MPD":

| | on the wire | the daemon | `/dev/dsp.play` |
|---|---|---|---|
| `NO_CARRIER` | no frames at all | retries the capture device | not held |
| `IDLE` | frames, all exact zeros | counts the silence | **released** |
| `PLAYING` | audio | ring → output | held |

Two devices, two very different tenancies. The **capture** device is held for
the life of a session: it is the interface's own node, nobody else wants it,
and it is the only thing that can tell us whether a carrier exists. The
**output** device is virtual_oss's client node, and it is taken only for the
duration of actual music.

That asymmetry is not politeness. `drc.sh` restarts virtual_oss on every rate
change, and an open cuse client handle at that moment is what wedges the
teardown *permanently* — `cuse_server_free()` spins uninterruptibly until every
client handle is gone, SIGKILL does not touch it, and only a reboot recovers
the machine (`../VIRTUAL_OSS_CUSE_DEADLOCK.md`). A daemon that held
`/dev/dsp.play` around the clock would put a fresh instance of that hazard
under every single rate change. Holding it only while a disc plays reduces the
exposure to the times you asked for a rate change mid-disc — and for those,
`drc.sh` sends `SIGHUP` first:

```sh
service omdrc_cdin release      # or: pkill -HUP -x omdrc-cdin
```

`SIGHUP` releases the output immediately and refuses to re-acquire it for a few
seconds, which covers the stop/restart. Without that hold-off a spinning disc
would grab the device again on the very next period — the same handle we just
gave up.

### Choosing `--idle-after`

The threshold has one failure mode at each end, and the safe band between them
is wide:

* **too short** and Red Book's 2 s inter-track pause releases the device
  mid-disc, so every track change costs a `lead` to resume;
* **too long** and a stopped player keeps holding the chain.

The default of 15 s is an order of magnitude above the inter-track gap and
still frees the chain within seconds of the music ending. `--idle-after 0`
disables the gate and restores Phase 1 behaviour (output held for the whole
run), which is occasionally useful when measuring.

Note this is silence *on the wire*. A player that drops carrier rather than
sending zeros never reaches the gate at all: the read fails and it lands in
`NO_CARRIER`, which is the state that reopens the device.

### Resuming does not lose the first note

When audio returns, the ring already holds the seconds of silence that came
just before it, because it keeps rolling whether or not anything is playing.
So an episode begins by *trimming* the ring to one lead rather than clearing it
and waiting for a fresh pre-fill. The audible result is identical — the music
still emerges one `lead` later — but the period that carried the first sample
is still in the buffer instead of having been thrown away with the silence, and
the device is opened when the music arrives rather than a `lead` ahead of it.

### What it looks like in the log

```
[INF] playback /dev/dsp.play: available, 32-bit
[INF] capture /dev/dsp1: available
[INF] state idle: capture open, waiting for audio
[INF] state playing: audio on the wire
[INF] playback /dev/dsp.play: acquired
[INF] playback: lead reached (1998 ms buffered), starting
[INF] state idle: digital silence long enough to release the output
[INF] playback /dev/dsp.play: released
```

Those lines are a contract, not just prose: the web panel parses them (see
below), so `state <name>: <why>` and `<device> <path>: <available|unavailable|
acquired|released>` keep their shape.

Note that **availability and holding are separate axes**. `unavailable` means
the device could not be opened and nothing can play — that is the red light.
`acquired` / `released` is the ordinary rhythm of a daemon doing its job, and
is never a fault.

## The web panel

`omdrc-ctrl` shows a **CD input** card driven entirely by this log — the daemon
is watched, not driven, because there is nothing to drive: it starts at boot and
manages its own devices. Configure it in `commands.conf`:

```ini
[cdin]
enabled = yes
log_file = /tmp/omdrc-cdin.log     # must match omdrc_cdin_logfile in rc.conf
process = omdrc-cdin
service = omdrc_cdin
refresh = 5
max_events = 20
```

The card shows one LED, one status line, both device paths, the latest `[stats]`
line, and a scrolling event list. Two rules govern it:

* **the LED follows availability only** — red when either end cannot be opened,
  because that is the question "can a disc play right now?". A released output
  device is normal operation and never colours anything;
* **failures are kept, health is replaced.** Every error stays in the list, in
  red, in chronological order, even after the condition clears; the healthy
  status line is replaced rather than accumulated. A live status line can never
  say "the output was missing for ten minutes this morning", and that is
  precisely the thing worth saying.

A daemon that is not running is reported as idle, not broken, however alarming
the last lines of its log are — the log outlives the process, and nothing is
unavailable when nothing is trying to open it.

## Options

| Short | Long | Default | |
|---|---|---|---|
| `-i` | `--in` | `/dev/dsp1` | capture device, or a WAV file / a directory of them (a disc) |
| `-o` | `--out` | `/dev/dsp.play` | playback device; `none` = capture-only measurement |
| `-r` | `--rate` | `44100` | |
| `-c` | `--channels` | `2` | |
| `-b` | `--bits` | `32` | source width: 16, 24 or 32 (a WAV source overrides it) |
| | `--out-bits` | | force the output width instead of negotiating it |
| `-L` | `--lead` | `2000` | target lead in ms — see above |
| `-B` | `--ring` | `8000` | ring capacity in ms |
| `-p` | `--period` | `1024` | application transfer period in frames; OSS fragment sizing is separate, including for packed 24-bit audio |
| `-s` | `--stats` | `5` | stats interval in seconds |
| `-R` | `--retry` | `2` | device retry interval |
| | `--idle-after` | `15000` | digital silence on the wire before the output device is released — see above; `0` disables the gate |
| `-l` | `--log` | | also log to this file |
| `-d` | `--debug` | | emit periodic stats |
| `-v` | `--verbose` | | more verbose; repeat (`-vv`) for debug lines |
| `-P` | `--probe` | | open both devices, report, exit |
| | `--in-ppm` | `0` | offset the simulated source's pace, in ppm (drift rig) |
| | `--gap` | `2000` | digital silence between tracks, ms (`0` = gapless disc).  Set it above `--idle-after` to test the release/re-acquire cycle |
| | `--transport` | | script the transport buttons — see below |
| | `--loop` | | restart the disc at the end |

Use `--out /dev/dsp0` to write the DAC directly, bypassing BruteFIR — useful to
isolate whether a problem is in the chain or in the bridge.

## Simulating a CD transport instead of a capture device

Point `--in` at a **directory** and its `*.wav` files become the tracks of a
disc, played in name order — which is how every ripper numbers them. A single
file is a one-track disc. The format is taken from the first track and all the
others must match, because a disc has one format by definition and the output
is opened once to suit it.

```sh
# insert the disc
omdrc-cdin -i "/media/.../Mendelssohn - Orgelsonaten - Hurford" -o /dev/dsp0 -d
```
```
[INF] disc: 12 tracks, 57:53, 44100 Hz 16-bit 2ch, 2000 ms between tracks
[INF] transport: track 2/12 (split-track01.wav) begins at 0:00
```

Between tracks the rig emits `--gap` milliseconds of **exact digital silence**,
default 2000 — Red Book's standard inter-track pause. That silence is not
decoration. It is what the silence gate looks for (`silence %` in the stats),
so a rig that could not produce it could not test the thing the design depends
on — and a `--gap` longer than `--idle-after` is how the release/re-acquire
cycle is exercised without a CD player. `--gap 0` gives a gapless disc, which
is the harder case.

Real rips already carry some of their own: on the disc above, track 1 is a
0.43 s pregap of pure zeros and track 2 opens with 1.81 s of them, which is why
`silence` reads non-zero at the start even with `--gap 0`.

### Scripting the buttons

`--transport` takes `AT:EVENT` pairs separated by commas, `AT` being seconds
into the stream. Each event puts on the wire what the corresponding button puts
on a real player, per the table further up:

| Event | On the wire |
|---|---|
| `skip` / `prev` | 300 ms mute, then the next/previous track |
| `seek=[+-]N` | 300 ms mute, then N seconds forward/back within the track |
| `pause=N` | N seconds of digital silence; carrier up, position held |
| `dropout=N` | the carrier drops for N **milliseconds** and no frames arrive |
| `stop` | the carrier drops for good and the stream ends |

```sh
omdrc-cdin -i DISC -o /dev/dsp0 -d -s 5 \
    --transport "20:skip,35:pause=4,55:dropout=800,70:seek=+30,85:prev,105:stop"
```

**`dropout` is the important one.** Everything else changes *what* is sent
while both clocks keep running, so the lead is untouched — that is the whole
content of "transport actions change what is sent, never how fast". A carrier
dropout is the exception and the one real hazard: no frames arrive at all while
the wall clock runs, so the lead drains by exactly the dropout's length and
never recovers. That is what a seconds-scale lead is *for*, independently of
drift, and it is directly visible:

```
[WRN] transport: carrier dropout of 800 ms — no frames on the wire; the lead absorbs it
[stats] lead 1374 ms (min 836, max 1649)  drift ref dropped (lead jumped)  ...  dropouts 1
[stats] lead  840 ms (min 836, max  859)  ...  starves 0  ...  dropouts 1
```

800 ms of lead gone, `starves 0` — absorbed, exactly as designed. Run the same
script with `--lead 500` and it starves instead, which is how you find the
floor for your own transport.

### The medium is not part of the emulation

The rig emulates the CD player's clock, not the disk's seek time, so the disc
is prefetched on its own thread (4 s deep) and the paced read is served from
RAM. This is not incidental: with the read inline, one 3 s stall on an external
USB drive drained a 2 s lead to 23 ms and starved playback — a property of the
rig, not of the design under test. Two rules follow from the same principle,
that a capture device can neither stall nor burst:

* if the medium genuinely cannot sustain realtime, the daemon says so
  (`the medium is not keeping up — prefetch buffer ran dry`) rather than
  quietly reporting it as a lead problem;
* if the schedule slips more than 100 ms anyway, it is shifted forward rather
  than firing every overdue deadline at once. Hardware cannot replay the frames
  it missed, and a burst would permanently inflate the lead by the length of the
  stall and put a step change through the drift estimate.

Both are counted and surface as `rig stalls N slips N` in the stats — shown only
when non-zero, because both should be zero. A scripted `dropout` is deliberate
and counted separately, as `dropouts`.

### Reading the stats

```
[stats] lead 1635 ms (min 1625, max 1649)  drift +38.7 ppm (+/-387.0), ring fills in 46 h
        in 44100.206 Hz  out 44088.817 Hz  frames 4054016/3980288
        drops 0 B  starves 0  silence 0%  up 90 s
```

* **`lead` is the ring only.** It settles *below* `--lead` — the pre-fill hands
  the first few hundred ms straight to the output device's own buffer as soon as
  playback starts. End-to-end latency is this figure plus the output device
  buffer, plus (in the real chain) virtual_oss's 200 ms and BruteFIR's filter
  group delay. The startup delay you actually wait is `--lead`.
* **`drift` is measured from the change in `lead`**, not from the frame
  counters: those carry each device's constant buffer offset, which at ppm scale
  would swamp the figure, and which cancels in a difference. The `+/-` is the
  period quantisation over elapsed time — while it exceeds the estimate, the
  estimate means nothing. It needs minutes, and tightens for hours.
* **`in` / `out` are measured from the instant playback began** — not from
  startup, so the pre-fill seconds (input frames, no output frames) do not bias
  them — and the window restarts at every discontinuity, for the same reason
  the drift reference does. An 800 ms dropout would otherwise leave the
  cumulative average reading low for the rest of the session, long after the
  event that caused it had scrolled off the screen.
* **`starves`** counts events, not periods: one continuous starvation is 1.
* **`drift ref dropped (lead jumped)`** means the estimate was thrown away and
  restarted, because the lead moved for a reason that is not the clocks — a
  starve, a ring overflow, or a step too large to be drift. Measuring across
  such an event reports the step: a 3.3 s jump inside a 60 s window once read
  as `+54361 ppm`, which is a stall wearing a drift figure's clothes.
* **`dropouts`** counts *scripted* carrier drops. They are deliberate, and the
  lead they consume is the measurement.
* **`rig stalls / slips`** appears only for the simulated source, and only when
  non-zero. It is a statement about the *test rig* — the host could not read
  the medium fast enough — not about the daemon or the design.

### The drift rig

`--in-ppm` offsets the simulated source's pace, which makes the design's central
claim testable in seconds instead of hours — real hardware differs by a few ppm
and takes a full day to show anything. Unlike `dropout`, this is not a
transport event: it is the *clock* being wrong, which is the condition the
whole daemon exists to survive.

```sh
# source 10% slow: the lead drains and the DAC starves
omdrc-cdin -i FILE -o /dev/dsp0 --loop --in-ppm -100000 --lead 500 -d -s 2

# source 10% fast: the lead grows to the ring cap, then frames are dropped
omdrc-cdin -i FILE -o /dev/dsp0 --loop --in-ppm 100000 --lead 500 --ring 1000 -d -s 2
```

Both failure modes behave as the arithmetic predicts, and are worth recognising
in the stats:

* **Lead exhausted** — `starves` increments, then `in` and `out` converge to the
  *same* wrong rate. That equality is the backpressure signature: with no buffer
  left, the DAC is paced by the source instead of its own clock, which is a
  continuous underrun.
* **Ring saturated** — `lead` pins at the ring capacity and `drops` climbs at the
  drift rate. Audio is being discarded, one discontinuity per drop.

Neither can happen at real-hardware ppm within a disc; they are here to prove
the instrumentation reports honestly when they do.

## Build

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build
ctest --test-dir build
```

Four suites, all covering places where a bug is silent rather than loud:
`test_ring` (wrap-around, the drop-oldest policy, the back-pressured write and
end-of-data read the WAV prefetch needs, every blocking path's wake-up, and the
trim-to-the-lead that starts an episode), `test_convert` (the width widening —
see the widening section below) and `test_gate` (the silence threshold, its
edge behaviour, and the fact that one non-zero sample resets the whole run),
and `test_ossdev` (OSS fragment sizing, including packed 24-bit stereo).

The gate is worth the suite for the same reason as the others: both of its
mistakes are quiet ones. Fire too early and a disc tears its own output down in
the Red Book pause; fire late, or twice, and the release either never happens or
happens again every 23 ms. Neither is a crash.

FreeBSD only — the daemon talks OSS directly. On Linux the equivalent job is
already done by `alsaloop(1)`.

## The ESI U24 XL — what the vendor documentation settles

Researched 2026-08-19 from ESI's own documentation, so the clocking question is
no longer an assumption:

* **It slaves to the incoming S/PDIF automatically.** ESI KB00307EN: when the
  source is clock master "the U24 XL will receive clock from the source and
  automatically will be slave", and there is *no* manual clock switch in the
  control panel. So there is no clock source to select — the behaviour this
  design needs is the only behaviour it has.
* **USB Audio Class 1.0, USB 2.0 Full Speed** (manual §6). Class compliant, so
  `uaudio(4)` drives it natively; no vendor driver is involved.
* **32 / 44.1 / 48 kHz, 24-bit max** (manual §6). 44.1 kHz is supported — and
  48 kHz is the ceiling, which is irrelevant for CD but rules this interface out
  for anything higher.
* **The sample rate is NOT auto-detected.** KB00307EN is explicit that bit depth
  and rate must be set to match the incoming signal; nothing does it for you.
  Here that is `SNDCTL_DSP_SPEED` = 44100, which the daemon already sets — but
  it means a non-44.1 source would be captured at the wrong rate rather than
  refused.

### The real open risk is input *selection*, not clocking

Analog-vs-digital input is a **software** choice, not a switch on the box:
Windows uses ESI's control panel (manual §4.2), macOS uses Audio MIDI Setup
("External Line Connector" vs "External SPDIF Interface"), Linux uses
`alsamixer`. That macOS can do it at all is good evidence it is a standard UAC
Selector Unit rather than a vendor-specific request.

FreeBSD reaches selector units through the **OSS record-source** mixer.
`uaudio(4)` parses `UDESCSUB_AC_SELECTOR` into a `MIX_SELECTOR`
(`sys/dev/sound/usb/uaudio.c:4172,3636`), assigns each pin a class from a
reserve list that includes `SOUND_MIXER_DIGITAL1..3`, folds those into
`recsrc_info` (`uaudio.c:5515`), registers them with `mix_setrecdevs()`
(`uaudio.c:5552`) and drives the selector from
`uaudio_mixer_setrecsrc()` (`uaudio.c:5592`). So the expected incantation is:

```sh
mixer -f /dev/mixerN                 # list devices; rec-capable ones are marked
mixer -f /dev/mixerN -s              # print only the current recording source(s)
mixer -f /dev/mixerN pcm2.recsrc=set # make the S/PDIF input the only source
```

**It is `pcm2`, not `dig1`.** `uaudio` maps terminal types to OSS mixer devices
through `uaudio_tt_to_feature[]` (`uaudio.c:4494`): `UATE_SPDIF` and
`UATE_DIGITALAUIFC` both map to `SOUND_MIXER_ALTPCM`, while `UATE_LINECONN` and
`UATE_ANALOGCONN` map to `SOUND_MIXER_LINE`. `SOUND_MIXER_ALTPCM` is 10
(`soundcard.h:1031`) and index 10 of `SOUND_DEVICE_NAMES` is `"pcm2"`
(`soundcard.h:1073`). The `dig1` names are `SOUND_MIXER_DIGITAL1..3`, which
`uaudio` uses only as *fallbacks* when two selector pins would otherwise collide
on the same class (`uaudio_mixer_check_selectors()`). Line and S/PDIF do not
collide, so the two pins should surface as `line` and `pcm2`.

Everything else verified against the installed FreeBSD 15.1 base:

* The syntax is `dev[.control[=value]]`. The pre-14 form `mixer =rec dig1` no
  longer exists; `mixer -h` on 15.1 gives
  `mixer [-f device] [-d pcmN | N] [-os] [dev[.control[=value]]] ...`.
* `mod_recsrc()` in `/usr/src/usr.sbin/mixer/mixer.c` accepts both word and
  symbol forms — `set` or `=`, `add` or `+`, `remove` or `-`, `toggle` or `^`.
  (An older source tree has only the word forms, so prefer `set`.)
* In the plain listing (`printdev()`, mixer.c:278) each device is tagged `pbk`
  or `rec`, and `src` when it is currently a recording source. If no device
  shows `rec`, `uaudio` found no selector unit and this route is closed.

### Evidence that it really is a standard selector unit

Linux exposes this device's input choice as **`PCM Capture Source`** with values
**`Line`** and **`IEC958 In`** (Arch forum thread 224914). Checked against the
Linux 7.2 tree, those strings are generated, not written:

* `sound/usb/mixer.c:626,628` holds ALSA's terminal-type name table, including
  `{ 0x0603, "Line" }` and `{ 0x0605, "IEC958 In" }`;
* `parse_audio_selector_unit()` builds the value list from `get_term_name()` per
  input pin and names the control with
  `append_ctl_name(kctl, " Capture Source")`.

**There is no U24 XL quirk in Linux at all.** ESI's vendor id `0x0a92` appears
exactly three times in `sound/usb`, and none of them is this product:
`quirks.c:2092` (`0x0053`, AudioTrak Optoplay), `midi.c:1449` (`0x1020`, ESI
M4U) and `mixer_maps.c:559` (`0x0091`, MAYA44 — and that is a `usbmix_name_map`,
which only *renames* generically discovered controls, it creates none). The
U24 XL's product id is absent from the tree.

So ALSA drives this switch purely through generic UAC1 Selector Unit parsing,
on the same descriptor `uaudio` parses at `uaudio.c:4172`. Two consequences:

1. **There is nothing to port.** A FreeBSD quirk would have no Linux original to
   copy from, because the Linux support *is* the generic path.
2. If `mixer -f /dev/mixerN` nevertheless shows no `rec` device on the hardware,
   the gap is in `uaudio`'s generic selector/terminal handling, not in
   device-specific support. The likeliest spot is the `uaudio_tt_to_feature[]`
   mapping above: a terminal type outside that table falls through to a default
   and the pin can vanish. That is a small table addition rather than a quirk
   subsystem, and it is upstreamable on its own merits — the same area, and the
   same shape of fix, as the `uaudio` shared-clock patch already committed
   upstream from this project (FreeBSD `755685dd665`).

**Caveat:** a 2016 FreeBSD 10.3 report with this exact device could not select
S/PDIF and hit "Multiple formats is not supported" / "Wrong number of channels"
(forums.freebsd.org thread 57631). That predates a decade of `uaudio` work and
may simply have missed `=rec dig1`, but it is the one report of anyone trying
this combination, and it failed. Treat selection as unproven until tested.

**Do not update the interface firmware.** A Linux report (raspberrypi/linux
issue #351) has S/PDIF capture working on the original firmware and becoming
"completely distorted" after a firmware upgrade.

### Consequence for this code: the two ends do not share a format

The digital input is 24-bit at most and a CD is 16-bit, while the chain
downstream runs S32_LE (`virtual_oss -b 32`, BruteFIR `sample: "S32_LE"`). With
`bitperfect=1` the kernel's format feeder is gone by design — `feeder_chain.c`
makes origin and target identical — so the device does **not** convert, it
simply refuses the width. That is not hypothetical: opening `/dev/dsp0` at
16-bit on the dev box fails outright.

So the daemon negotiates and converts itself (`src/convert.c`):

* the output is opened at the **source** width first, because no conversion is
  always truest; if the device refuses, a wider one is tried, **never** a
  narrower one;
* widening is left-justification, which for little-endian PCM is pure byte
  placement — `16→32` is `{0, 0, s0, s1}`. No arithmetic means no rounding, no
  dither, no sign or overflow behaviour to get wrong, so it is provably
  lossless and the bit-perfect claim survives it;
* narrowing is never offered. It would need truncation or dither, which is
  exactly what this daemon exists to avoid.

`--out-bits N` forces the output width instead of negotiating it. The run says
which path it took:

```
[DBG] playback /dev/dsp0 at 16-bit: device rejected 16-bit samples — trying wider
[INF] /dev/dsp0: opened for playback, 44100 Hz 32-bit 2ch
[INF] output opened at 32-bit; widening 16 -> 32 bit losslessly (left-justified, no arithmetic)
```

`tests/test_convert.c` pins this down by the **value** relation rather than the
byte layout — each widened sample must equal `src << (dst_bits - src_bits)` —
plus canary bytes around the destination, because the failure mode here is
silent: a wrong byte index does not crash, does not warn, and does not show up
in the stats. It just rescales or byte-swaps every sample on the way to the DAC.

## Phase 0 — checks still owed on the audio box

None of this can be verified on a box without the hardware.

* `cat /dev/sndstat`, `sysctl dev.pcm` — find the ESI; confirm a record channel.
* `mixer -f /dev/mixerN` — **the decisive test**: does any device show `rec`,
  and is one of them `pcm2`? Then `mixer -f /dev/mixerN pcm2.recsrc=set`.
  (Not `=rec dig1`: that syntax was removed before FreeBSD 14, and the name is
  `pcm2` — see the selector section above for both derivations.)
* `usbconfig -d ugenX.Y dump_curr_config_desc` — confirm the Selector Unit and
  the input terminals if the mixer route comes up empty. If the descriptor has
  a selector but the mixer does not show it, that is the `uaudio` gap described
  above, and it is worth a patch.
* Which sample formats does capture actually accept — `-b 32`, `-b 24`, `-b 16`?
  Whatever it answers, the output side already adapts: see the widening section
  above. What this settles is only which direction the conversion runs.
* **Measure the real drift**: `omdrc-cdin --in /dev/dspN --out none -d -s 30`
  reports the capture rate against the host clock; compare with the Okto's
  `dev.pcm.<n>.feedback_rate`. Their difference is the drift the lead must cover.
* **Carrier loss**: stop the CD, then unplug the coax. Does `read()` block,
  short-read, or error? The state machine assumes a short read or an error
  means `NO_CARRIER`, and that a player which merely *mutes* keeps delivering
  frames of zeros and is caught by the silence gate instead. If a stopped
  player blocks the read forever instead of doing either, the gate never runs
  and the daemon needs a read timeout to notice.
* **Loopback pacing**: virtual_oss runs with `-f /dev/null` (`drc.sh:96`), so it
  owns no hardware clock. Confirm a writer to `/dev/dsp.play` is throttled at
  the DAC rate via BruteFIR draining `/dev/dsp.loop`, and check what happens
  when BruteFIR is **not** running — writes may block forever, in which case the
  `cd` verb must refuse to start unless the chain is up.
* Re-check the `freebsd-uaudio-patch/` shared-clock behaviour with a second USB
  audio device streaming; ideally put the ESI on a different root hub.

### Sources

* [ESI KB00307EN — S/PDIF input usage and clock](https://kb.esi-audio.com/?goto=KB00307EN)
* [ESI KB00187EN — U24 XL under Linux](https://kb.esi-audio.com/?goto=KB00187EN)
* [U24 XL User's Guide (rev 5, 2018)](https://download.esi-audiotechnik.com/download/ESI/U24_XL/U24_XL-English.pdf)
* [FreeBSD forums — S/PDIF capture with an ESI U24XL](https://forums.freebsd.org/threads/s-pdif-capture-playback-with-an-esi-u24xl.57631/)
* [raspberrypi/linux#351 — distortion after firmware upgrade](https://github.com/raspberrypi/linux/issues/351)
