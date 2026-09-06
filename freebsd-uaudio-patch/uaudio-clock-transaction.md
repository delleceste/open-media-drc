# Audit: why the OKTO DAC8 still refuses 44.1 kHz now and then

**Date:** 2026-08-26 · **Host:** FreeBSD 15.1-RELEASE-p2 (`releng/15.1-n283596`),
amd64 · **Device:** OKTO RESEARCH DAC8 STEREO `0x152a:0x88c5`, `ugen0.3`, `pcm1`
· **Running module:** stock + [`uaudio-clock-before-alt.c.patch`](uaudio-clock-before-alt.c.patch)
+ [`uaudio-shared-clock-fix.c.patch`](uaudio-shared-clock-fix.c.patch)

Deep re-audit of the two locally applied `uaudio(4)` patches against the kernel
source they patch, prompted by the residual symptom: **switching to 44100 Hz
from another rate still sometimes leaves the DAC silent, and going up a rate
and back down fixes it.**

---

## TL;DR

The clock-before-alt patch fixed the **playback** configuration pass. It did not
fix the **capture** pass that runs immediately after it — and that pass issues a
second `SET_CUR(UA20_CS_SAM_FREQ_CONTROL)` to the same shared clock entity,
with the same value, **after playback has already been armed and its
isochronous transfers are running**, with no settling delay. That re-creates,
from the capture side, exactly the hazard the reorder was written to remove.

The shared-clock guard does not stop it, by construction: it skips the write
only when the other direction wants a *different* rate, and
`uaudio_chan_start()`'s own rate alignment guarantees the two rates are
*equal*.

**The upstream fix (`755685dd665e`, MFC `6886e8a9a0aa`) has the same gap** — it
is the same guard, verbatim — *plus* the stock `SET_INTERFACE`-before-`SET_CUR`
ordering, which the reorder patch has never been upstreamed to fix.

Two follow-up patches are in this directory, both built `-Werror` clean with
and without `USB_DEBUG`:

| Patch | What it does |
|---|---|
| [`uaudio-upstream-0001-shared-clock-write-discipline.c.patch`](uaudio-upstream-0001-shared-clock-write-discipline.c.patch) | A clock another stream owns is never written again. `GET_CUR` read-back before every write. No stale capture alt. |
| [`uaudio-upstream-0002-prefer-explicit-feedback.c.patch`](uaudio-upstream-0002-prefer-explicit-feedback.c.patch) | Stop borrowing the capture stream for jitter when the playback alt has an explicit feedback endpoint. |
| [`uaudio-clock-transaction.c.patch`](uaudio-clock-transaction.c.patch) | Both of the above, rolled up and rebased onto **this host's** tree. |

---

## You were right about the OKTO not being a capture device

Confirmed from the descriptors, and it matters more than it looks.

The DAC8 STEREO's AudioControl interface really does declare a capture path:
Input Terminal `0x01`, `wTerminalType 0x0201` (**Microphone**) → Output Terminal
`0x16`, `wTerminalType 0x0101` (USB streaming), and AudioStreaming interface 2
carries three alt-settings on IN endpoint `0x82`. That boilerplate is why
FreeBSD reports `pcm1: <OKTO RESEARCH DAC8STEREO> (play/rec)`. There is no
microphone behind it.

The part that is *not* obvious from the descriptors: **that stream is running
right now, continuously, during every playback.** Measured on the live chain at
44.1 kHz over a 20 s window:

```
UE_ISOCHRONOUS_OK  delta : 5038  ->  251.9 /s      (usbconfig dump_stats)
dsp1.play interrupts     : 2509  ->  125.45 /s     (/dev/sndstat, verbose)
                          251.9 ≈ 125.45 (play) + 125.45 (capture) + 1 (feedback)
```

`uaudio_chan_need_both()` starts it on purpose: for an asynchronous playback
endpoint the driver prefers to derive rate feedback from the *lengths of the
capture packets* rather than spend an endpoint on explicit feedback. On this
device that trade is pure loss — the OKTO already has an explicit feedback
endpoint (`0x81`, `bmAttributes 0x11`, present on all four playback alts, and
`dev.pcm.1.feedback_rate` reads a live 44101), and the driver ignores it for
rate control (`uaudio_chan_play_sync_callback()` gates the feedback value on
`sc_rec_chan[i].num_alt == 0`, which is false here) while streaming a fake
microphone at full bandwidth to recompute the same number.

So the correction to your framing is small: from the driver's point of view it
*is* a capture device, and it is being treated as one. Your conclusion holds —
nothing should be streaming there.

---

## Root cause, in the order it happens

Clock topology, read off `usbconfig -d ugen0.3 dump_curr_config_desc`:

* one Clock Source, `bClockID 0x29` (41), `bmAttributes 0x03` (internal
  programmable), `bmControls 0x07` (frequency read/write + validity read);
* one Clock Selector `0x28` (40) in front of it;
* **all four terminals** — playback IT `0x02`, playback OT `0x14`, capture IT
  `0x01`, capture OT `0x16` — carry `bCSourceID = 0x28`.

`uaudio20_mixer_find_clocks_sub()` records only `UDESCSUB_AC_CLOCK_SRC` ids, so
clock **41** lands in both `sc_mixer_clocks.bit_output` and `bit_input` →
`uaudio20_clock_is_shared(sc, 41)` is true.

A 48 kHz → 44.1 kHz transition then runs like this
(`uaudio_configure_msg()` at `uaudio.c:1674` iterates **play first, then rec**):

```
uaudio_chan_start(play)                      uaudio.c:2984
  need_both(play, rec)  == true              (async OUT ep + rec->num_alt > 0)
  match_rate(rec, 44100) -> rec->set_alt aligned to 44100
  reconfigure(rec, START); reconfigure(play, START)

USB explore thread, uaudio_configure_msg_sub(play)   uaudio.c:1408
  SET_INTERFACE(iface 1, alt 0)              park (usually already parked)
  guard: rec not running -> other == 0 -> no skip
  SET_CUR(clock 41, 44100)
  sc_clock_rate 48000 != 44100 -> settle 100 ms
  SET_INTERFACE(iface 1, alt N)              <-- DAC arms, starts locking
  usbd_transfer_setup(); usbd_transfer_start(x2)     <-- audio flowing

... microseconds later, same thread ...
uaudio_configure_msg_sub(rec)
  SET_INTERFACE(iface 2, alt 0)
  guard: other = 44100, chan_alt->sample_rate = 44100
         -> `other != 0 && other != rate` is FALSE -> NOT skipped
  SET_CUR(clock 41, 44100)                   <-- second write, clock reload
  sc_clock_rate == 44100 -> rate_changed = 0 -> no settle
  SET_INTERFACE(iface 2, alt M)              <-- second interface armed
  usbd_transfer_setup(); usbd_transfer_start(x2)
```

Two things land on the device inside a millisecond of playback arming, while
the DAC is acquiring lock on a freshly-changed master clock:

1. **A redundant `SET_CUR` on the sample clock.** Thesycon/XMOS firmware
   commonly reloads the clock generator on every `SET_CUR`, value-independent.
   Linux never issues it: `snd_usb_set_sample_rate_v2v3()` reads the rate back
   with `GET_CUR` first and returns early when it already matches.
2. **A `SET_INTERFACE` arming a second AudioStreaming interface.** On XMOS
   reference firmware, stream start/stop calls the vendor `AudioHwConfig()`
   hook, which for many designs re-initialises clock hardware.

Both are absent on Linux, which plays this device correctly. Both are absent
once the two follow-up patches are in. That is the mechanism; see
[Confidence](#confidence) for what is proven and what is inferred.

It also explains the shape of the symptom. The failure is a **race against the
DAC's PLL lock**, not a timing shortfall — which is why raising
`hw.usb.uaudio.clock_settle_ms` does not help (the offending write happens
*after* the delay) and why repeating the same rate change usually works.

---

## The eight findings, checked

The audit below is against `/usr/src/sys/dev/sound/usb/uaudio.c` as installed on
this host. Line numbers are that file.

### 1. Double `SET_CUR`, non-atomic play/capture configuration — **confirmed**

As traced above. `uaudio.c:1517` guard, `uaudio.c:1674` ordering,
`uaudio.c:2984` alignment. This is the headline defect and the one the patches
target. One refinement: with clock-before-alt applied the capture pass *does*
park its own interface at alt 0 first, so the write happens with interface 2
idle — but interface 1 is armed and streaming, which is the part that matters.

### 2. `STOP` immediately followed by `START` can be coalesced — **confirmed, consequence narrower than stated**

`usb_proc_msignal()` (`usb_process.c:266`) has two reusable queue entries, while
`uaudio_chan_reconfigure()` (`uaudio.c:2944`) keeps only the latest `operation`
per channel. A `STOP` whose `operation` field is overwritten before the callback
snapshots it executes as `START`.

The consequence is real but narrower than "the old capture alternate setting may
still be active when the playback path changes the clock" implies as a general
statement: because the `START` path itself parks *its own* interface at alt 0
(clock-before-alt), only the **other direction's** interface can still be armed
across a clock write. That is exactly the case codex flags as unfixed — the
patch parks one interface, not every interface on the clock — and it stays
unfixed here. After patch 0002 the OKTO never has a second stream to leave
armed, so the reachable instance on this device is gone; the general fix needs
the full transaction restructure (see [Remaining work](#remaining-work)).

Also note stock FreeBSD is *unaffected by the coalescing* in one respect: it has
no `STOP` alt-0 parking to lose that the `START` path does not redo.

### 3. Capture stream used instead of the explicit feedback endpoint — **confirmed and measured**

See the measurement above. Sub-claim about jitter during relock: arithmetically
`ch_rec->jitter_curr` is clamped to ±`2 * intr_frames` (±128 samples here) and
spent one sample per frame, so a worst-case relock can under- or over-feed by
about 2.9 ms of audio at 44.1 kHz. Real, but modest — I rank it below the
`SET_CUR` as the trigger, not above it.

### 4. Clock state stored globally, not per clock source — **confirmed, not reachable here**

`sc->sc_clock_rate` is one scalar. Not an upstream defect (upstream has no such
field; it arrived with the local clock-before-alt patch). Not reachable on the
OKTO, which has exactly one clock source.

There is a concrete failure it can cause on a multi-clock device that codex did
not spell out: if playback runs at 48 k (`sc_clock_rate = 48000`) and a capture
stream on a *separate* clock is then started at 44.1 k, `sc_clock_rate` becomes
44100 — and the next genuine playback change 48 k → 44.1 k sees
`sc_clock_rate == 44100`, decides nothing changed, and **skips the settling
delay entirely**. The `GET_CUR` read-back in patch 0001 removes the scalar from
the decision and makes it per-entity and truthful.

### 5. Clock programming assumed successful too early — **confirmed, fixed**

`uaudio20_set_speed()` (`uaudio.c:5480`) sends `SET_CUR` and nothing else. Patch
0001 adds `uaudio20_get_speed()` and reads back before the write (to skip it)
and after the settle (to log a device that refused the rate). A write is only
ever skipped on the strength of a *successful* read-back — a device that cannot
report its rate is written exactly as before, so nothing regresses for hardware
that does not implement `GET_CUR`.

Verified against the live device — both reads work:

```
$ usbconfig -d ugen0.3 do_request 0xA1 0x01 0x0100 0x2900 4     # CUR SAM_FREQ
REQUEST = <0x44 0xac 0x00 0x00>                                 # 0xac44 = 44100
$ usbconfig -d ugen0.3 do_request 0xA1 0x01 0x0200 0x2900 1     # CUR CLOCK_VALID
REQUEST = <0x01>
$ usbconfig -d ugen0.3 do_request 0xA1 0x02 0x0100 0x2900 254   # RANGE SAM_FREQ
8 sub-ranges: 44100 48000 88200 96000 176400 192000 352800 384000
```

The clock-validity poll is deliberately *not* reintroduced: it was tried and
removed on 2026-06-21 because this device reports `valid = 1` immediately, as
the read above shows again. Checking `bmControls` for frequency writability
(Linux's `uac_v2v3_control_is_writeable()`) is not implemented — see
[Remaining work](#remaining-work).

### 6. Unsynchronised shared-state reads — **confirmed, not fixed**

`uaudio_configure_msg()` drops the explore lock for the duration of
`uaudio_configure_msg_sub()`, so `uaudio_dir_running_rate()` reads the opposite
channel's `running` (written under the explore lock) and `cur_alt` (written
under `chan->lock`) with neither held. Aligned scalar reads, so no tearing —
but not a stable snapshot. This is an **upstream** defect: it arrived with
`755685dd665e`. Low severity; listed for the bug report rather than patched,
because fixing it properly means restructuring the locking, not sprinkling a
lock over two reads.

### 7. Capture-rate matching can fail silently — **confirmed in code, not reachable on this device**

`uaudio_chan_match_rate()` returning false leaves `ch_rec->set_alt` at its
previous value and the stream is started anyway. Patch 0001 runs playback alone
in that case instead.

It cannot fire on the OKTO: both AudioStreaming interfaces expose the same
16 / 24 / 32-bit stereo PCM trio (`bSubslotSize` 2/4/4, `bBitResolution`
0x10/0x18/0x20), UAC2 takes its rate list from the shared clock's `RANGE` (the
eight rates above, identical for both directions), and
`uaudio_chan_fill_info_sub()` locks each channel to one format — so a matching
capture alt exists for every playback rate. Worth fixing, not the trigger.

### 8. `uaudio-feedback-follow.c.patch` is gated on the absence of capture descriptors — **confirmed, and it becomes relevant**

The gate is the same `sc_rec_chan[i].num_alt == 0` test. Today it never fires on
the OKTO. **After patch 0002 it fires on every playback**, because the sync
callback's gate becomes `cur_alt >= num_alt` and the capture stream is no longer
started. So `uaudio-feedback-follow.c.patch` moves from "irrelevant on this
device" to "now on the live path, and needs rebasing onto the new gate" — it is
the patch that would replace the stock ±62 ppm once-per-second clamped
correction with Linux-style smooth following. Not part of this round.

---

## Is the upstream fix incomplete? Yes

`755685dd665e` (main) and `6886e8a9a0aa` (stable/15) took
[`uaudio-shared-clock-fix.c.patch`](uaudio-shared-clock-fix.c.patch)
essentially verbatim — 95 insertions, 8 deletions, the same three helpers, and
the guard condition character-for-character:

```c
                if (other != 0 &&
                    other != chan_alt->sample_rate) {
```

So upstream inherits every gap above, and is in fact **worse off than this
host**, because it does not have the clock-before-alt reorder: on stock upstream
the sequence is `SET_INTERFACE(alt N)` → `SET_CUR(clock)` → start transfers, for
*each* direction. The redundant capture-side write lands after **two** armed
interfaces rather than one.

What upstream is missing, in priority order:

1. **The redundant write.** A clock the other direction is streaming on must not
   be written at all. (patch 0001)
2. **No read-back.** `SET_CUR` is issued blind and never verified; the rate is
   never skipped when already correct. (patch 0001)
3. **Auto-started capture over explicit feedback.** Costs a second isochronous
   stream and a second clock pass on every async playback device that has a
   feedback endpoint. (patch 0002)
4. **Stale capture alt** when `uaudio_chan_match_rate()` fails. (patch 0001)
5. **Ordering.** `SET_CUR` under an armed streaming interface — still stock
   upstream. ([`uaudio-clock-before-alt.c.patch`](uaudio-clock-before-alt.c.patch),
   never submitted)
6. **Unlocked cross-direction reads** in the new helper. (unfixed)
7. **No rejection of genuinely incompatible simultaneous rates.** The guard
   declines to reprogram, but configures and packetises the second stream for
   the rate it asked for — software at rate B, hardware at rate A, silently.
   Linux returns `-EBUSY`. (unfixed)

Items 1–4 are patches in this directory. Items 5–7 belong in the bug comment as
known-remaining.

---

## Is this a fix for one DAC, or for the driver?

For the driver. **Neither patch contains a VID/PID, a quirk entry, or any other
device gate.** Both key off descriptor facts that the driver already parses:

| Change | Condition it keys off | Devices where it is inert |
|---|---|---|
| 0001 shared-clock guard | a Clock Source id appears in **both** `bit_output` and `bit_input` | anything with separate clocks per direction, and every UAC1 device |
| 0001 `GET_CUR` read-back | device is UAC2 | every UAC1/UAC3 device; also inert where `GET_CUR` fails (write proceeds unchanged) |
| 0001 stale capture alt | `uaudio_chan_match_rate()` finds no capture alt for the playback rate | devices whose capture alt list covers every playback rate — including the OKTO |
| 0002 prefer feedback | playback alt is async **and** declares a feedback endpoint **and** a capture interface exists | devices with no feedback endpoint, or no capture interface |

The failure itself is not vendor-specific either. Any UAC2 device with **one
clock feeding both directions** gets the duplicate `SET_CUR`, and that topology
is extremely common — XMOS/Thesycon reference designs, essentially every
DAC+ADC interface, and every DAC that ships a vestigial capture path. Whether a
*given* device visibly misbehaves depends on its firmware's reaction to a
`SET_CUR` under an armed interface. That distinction is the right way to argue
this upstream: the driver does something it should not do and that Linux does
not do; you do not have to prove every device breaks to justify not doing it.

### Checking a device you do not own

[`bench/uaudio-affects.py`](bench/uaudio-affects.py) re-implements
`uaudio20_mixer_find_clocks_sub()` and reports, per device, which paths go live.
It works on a raw descriptor dump, so an owner of any DAC can mail you one:

```sh
sudo usbconfig -d ugenX.Y do_request 0x80 0x06 0x0200 0x0000 513 > dac.hex
python3 bench/uaudio-affects.py --dump dac.hex
```

On this host, run against everything attached:

* **OKTO DAC8 STEREO** — UAC2, clock 41 in both bitmaps, 4 async playback alts,
  feedback endpoint `0x81`, 3 capture alts → all three paths **ACTIVE**, and
  flagged `EXPOSED to the duplicate SET_CUR`.
* **ESI U24XL** — **UAC1** → every path **inert**. The other sound card on this
  machine sees no behaviour change whatsoever. That is the regression control.

### Known generality risks, and the escape hatches

Being device-independent cuts both ways, so each behaviour change that could
plausibly hurt some other device has a runtime knob — no rebuild, no quirk
table, and an A/B you can run in a minute:

| Risk | Who it could hit | Escape hatch |
|---|---|---|
| A device that misreports its rate via `GET_CUR`, so a needed write is skipped | firmware with a broken frequency read-back | `hw.usb.uaudio.clock_readback=0` |
| A device whose declared feedback endpoint is broken/unimplemented, previously masked because capture supplied the jitter | firmware that declares `UE_ISO_USAGE_FEEDBACK` but does not honour it | `hw.usb.uaudio.prefer_feedback=0` |
| Feedback deviation beyond the stock ±62 ppm clamp (`sample_rate / 16000`) | a device with an unusually offset clock | `prefer_feedback=0`, or finish [`uaudio-feedback-follow.c.patch`](uaudio-feedback-follow.c.patch) |

Linux needs the first escape hatch too and spells it `QUIRK_FLAG_ALWAYS_SET_RATE`
(consulted in `set_sample_rate_v2v3()`, which otherwise reads the rate back and
returns early when it already matches). A per-device quirk is the better
long-term shape for FreeBSD as well; a tunable is what is testable today and
does not require touching a quirk table to rescue a device.

Not-a-risk, worth stating: the guard change cannot introduce a *wrong-rate*
stream. The rates-differ case is unchanged (still skipped); only the
rates-already-equal case stops writing, and there is by definition nothing to
change.

## Remaining work (not in these patches)

* **Park every interface on the clock, not just the one being configured**, and
  make configuration of both directions one transaction with a generation
  counter so a `STOP`/`START` cannot be coalesced across it. This is the full
  8-step restructure; it is the correct end state and it is a much larger
  change than these two patches.
* **Reject or align incompatible simultaneous rates** instead of silently
  running software and hardware at different rates.
* **Honour `bmControls`** on the Clock Source: skip the write when the frequency
  control is read-only and merely verify the current rate, as
  `uac_v2v3_control_is_writeable()` does.
* **Record ring-buffer window.** A capture channel started only as a jitter
  source never goes through `uaudio_chan_start()`, so if an application last
  used it at a lower rate its `start`/`end` window is stale and
  `uaudio_configure_msg_sub()` rejects the buffer as "too big" — leaving
  playback with no jitter source at all. I drafted the obvious fix (re-window in
  `uaudio_chan_start()`) and **deliberately dropped it**: shrinking `end` while
  the record callback may still be draining can leave `ch->end < ch->cur`, and
  `usbd_copy_out()` would then be handed a negative length. It needs to be done
  in `uaudio_configure_msg_sub()`, after `usbd_transfer_unsetup()`, or not at
  all. Not reachable on this host (only `dsp0`/ESI is ever opened for capture).
* **Rebase [`uaudio-feedback-follow.c.patch`](uaudio-feedback-follow.c.patch)**
  onto the new sync-callback gate — see finding 8.

---

## Confidence

**High** that the redundant `SET_CUR` and the non-atomic play/capture
configuration are real defects: both are read directly off the code, the guard
condition provably cannot skip the equal-rate case, and the capture stream is
measured running.

**Not proven** that the redundant `SET_CUR` is *the* immediate trigger of your
silent 44.1 kHz opens rather than the capture `SET_INTERFACE` next to it, or
that either is the whole story. That needs the intermittent failure reproduced,
and the three-way A/B below is what separates them.

Raising `hw.usb.uaudio.clock_settle_ms` is *not* expected to help either way:
the offending traffic happens after the delay.

---

## Build, install, test

**Installed on the audio box 2026-09-06 and verified on the wire** (see the next
section). Before that date this patch had only ever been built in a scratch tree.

```sh
cd /usr/src
patch -p1 < /home/giacomo/open-media-drc/freebsd-uaudio-patch/uaudio-clock-transaction.c.patch

cd /usr/src/sys/modules/sound/driver/uaudio
make clean && make
# -> /usr/obj/usr/src/amd64.amd64/sys/modules/sound/driver/uaudio/snd_uaudio.ko

sudo cp /usr/obj/usr/src/amd64.amd64/sys/modules/sound/driver/uaudio/snd_uaudio.ko \
        /boot/kernel/snd_uaudio.ko
sudo kldunload snd_uaudio && sudo kldload snd_uaudio
```

Stop the DRC chain first. The DAC on this host is **`ugen0.2`**, not `ugen0.3`;
if `kldunload` refuses because `pcm0` is attached, reboot instead.

The one-line check that the patch is still present — `freebsd-update` silently
reverts both `/boot/kernel` and `/usr/src`:

```sh
sysctl hw.usb.uaudio.clock_readback     # OID missing => patch is gone
```

## Verified on the wire — 2026-09-06

Traced with DTrace on `usbd_do_request_flags()`, so what is recorded is the
control transfer that really went out, not a driver log line. Script, logs,
method and the full before/after listings: [`traces/`](traces/README.md).

Three open/play/close cycles of **digital silence** — 44.1 kHz entered from a
48 kHz clock, 44.1 kHz again, then 48 kHz. Silence is the point: the result is a
statement about control-plane traffic and does not depend on audibility.

### Before

```
 4138  uaudio_chan_start
 4138  configure_msg_sub PLAY
 4138  >>> SET_CUR SAM_FREQ  clockid=41  rate=44100
 4276    SET_INTERFACE iface=1 alt=1        <- playback ARMED (138 ms settle)
 4276  configure_msg_sub REC
 4276  >>> SET_CUR SAM_FREQ  clockid=41  rate=44100   <- redundant, post-arming
 4297    SET_INTERFACE iface=2 alt=1        <- capture stream armed
        ... 3 s of playback: no clock writes ...
 7325  uaudio_chan_stop
```

### After

Same rate as the previous stream — the common case, track after track at 44.1 kHz:

```
13451  uaudio_chan_start
13451      GET_CUR SAM_FREQ        <- reads back 44100, matches
13451    SET_INTERFACE iface=1 alt=1
13451  configure_msg_sub REC       <- runs, does nothing
```

Zero clock writes, and no settle delay either, because nothing was written.
On a real rate change exactly one `SET_CUR` goes out, followed by the
post-settle verification `GET_CUR`, before the interface is armed.

| | before | after |
|---|---|---|
| `SET_CUR` per start, same rate | 2 | **0** |
| `SET_CUR` per start, rate change | 2 | **1** |
| `SET_CUR` issued after playback armed | 1 | **0** |
| `SET_CUR` during steady-state playback | 0 | 0 |
| capture stream armed (`SET_INTERFACE iface=2`) | every playback | **never** |

Two findings worth recording beyond the pass/fail:

* **`prefer_feedback` is safe on this device.** The documented risk is a DAC that
  declares `UE_ISO_USAGE_FEEDBACK` but does not honour it. The OKTO honours it:
  `dev.pcm.0.feedback_rate` reads 44101 and 48001 with the capture stream gone,
  i.e. the endpoint is live and tracking, and playback ran clean at both rates.
* **The stock feedback clamp never saturates here.** The reported offset is ~1 Hz
  (~23 ppm) against a clamp of `howmany(44100, 16000)` = ±3 Hz. So the main
  complaint in [`uaudio-feedback-follow.c.patch`](uaudio-feedback-follow.c.patch)
  — real clock offsets truncated to ~62 ppm — does **not** apply to this DAC.
  Its other complaint, how that correction is distributed across frames, is
  untested and remains open.

Still **not** listening-tested through the full DRC chain, and the intermittent
silent-open fault is not reproduced either way. The three-way A/B below is what
decides audibility; this section only settles what the driver puts on the wire.

### Three-way A/B — this is the experiment that decides it

Automated by [`bench/`](bench/README.md): a WAV per rate, repeated
open/play/close cycles, and a per-cycle verdict measured from the DAC's analog
output. Run each leg with `DAC_PRIME_CYCLES=0` so `drc.sh` cannot mask the
result.

```sh
cd freebsd-uaudio-patch/bench
python3 selftest.py                       # analyser self-test, no hardware
drc.sh off                                # the bench needs /dev/dsp1 exclusively
python3 dac-bench.py run --monitor capture --sequence into --cycles 20 --csv legA.csv
```

`--monitor ask` works with no cable — you are the ear, one keypress per cycle.
Because each rate carries its own tone, the bench also separates *silent* from
*wrong clock*: a DAC left on the 48 kHz crystal while fed 44.1 kHz data
reproduces its tone 8.84% sharp and is reported as `WRONG-RATE`, not `SILENT`.

| Leg | Module | `hw.usb.uaudio.prefer_feedback` | Isolates |
|---|---|---|---|
| A | current | — | baseline: how often does it fail today |
| B | new | `0` | redundant `SET_CUR` gone, capture stream still running |
| C | new | `1` (default) | capture stream gone as well |

* **B fixes it** → the redundant `SET_CUR` was the trigger; patch 0001 alone is
  the fix and patch 0002 is a (worthwhile) efficiency change.
* **B does not, C does** → the capture `SET_INTERFACE` / second stream was the
  trigger; both patches are needed.
* **Neither fixes it** → the mechanism is elsewhere; run with `--trace`, which
  records the `SET_CUR` sequence per cycle and cross-tabulates it against the
  verdict. That works on the **current, unpatched** module too, so the
  duplicate-write hypothesis can be confirmed or killed before anything is
  installed. (`usbdump` cannot help: this `GENERIC` kernel has no
  `options USBPF` and captures nothing — verified.)

Also worth checking in leg C: `dev.pcm.1.feedback_rate` must stay live (it now
drives playback rate control, not just diagnostics), `underruns` on
`dsp1.play.0` must stay at 0 over a long listen, and `UE_ISOCHRONOUS_OK` should
drop to roughly half — that is the capture stream going away.

### Upstream

The two `uaudio-upstream-000*.c.patch` files apply to main / stable/15 after
`755685dd665e` / `6886e8a9a0aa` (verified against a reconstruction of that tree:
`releng/15.1` + `uaudio-shared-clock-fix.c.patch`, which is what upstream
committed). Both build `-Werror` clean with and without `USB_DEBUG`. Attach them
to PR 295933 together with the "Is the upstream fix incomplete?" section above.
