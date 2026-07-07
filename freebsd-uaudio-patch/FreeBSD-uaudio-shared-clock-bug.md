# `uaudio(4)`: an idle capture stream clobbers the active playback sample rate on devices with a shared UAC2 Clock Source

**For the FreeBSD `uaudio(4)` / USB maintainer.** This document describes a
reproducible `uaudio(4)` bug, the measured root cause, the relevant code paths,
a proposed direction for a general fix, and full instructions for filing it.

> **Status:** filed as **bug #295933**. A concrete general fix implementing
> the direction below (plus the mandatory rate-alignment of the auto-started
> jitter record stream, which the original sketch missed) is in
> [`uaudio-shared-clock-fix.c.patch`](uaudio-shared-clock-fix.c.patch) /
> [`uaudio-shared-clock-fix.md`](uaudio-shared-clock-fix.md); the
> capture-disable diagnostic below has been retired from this host.

---

## TL;DR

On a UAC2 device that exposes **one Clock Source entity shared between its
playback and capture interfaces**, `uaudio(4)` programs that clock for *both*
directions. When playback uses a sample rate from the **44.1 kHz family**
(44100 / 88200 / 176400 / 352800 Hz) and the (idle, non‑streaming) capture
channel is configured for a **48 kHz‑family** default, the capture channel's
`SET_CUR(CUR_SAM_FREQ)` is issued **after** the playback one and **overwrites
it on the shared clock**. The device's converter then runs at ~48 kHz while the
host streams 44.1 kHz data → continuous input‑FIFO underrun inside the device →
the DAC repeatedly drops and re‑acquires USB streaming lock (audible dropouts;
front‑panel "play/idle" flicker several times per second).

* The **48 kHz family is unaffected** — both directions then request 48000, so
  there is no conflict on the shared clock.
* The **same device works perfectly under Linux** (`snd-usb-audio`), which does
  not let an idle capture stream reprogram the clock used by active playback.
* A device with **no capture interface** (Cambridge Audio DacMagic 100) never
  shows the problem on the same FreeBSD host.

The host side is verifiably healthy throughout (`underruns 0`, channel stays
`RUNNING`); the fault is the **wrong clock rate programmed into the device**, not
data starvation.

---

## Affected component and environment

| Item | Value |
|------|-------|
| Driver | `sys/dev/sound/usb/uaudio.c` (`uaudio(4)` / `snd_uaudio.ko`) |
| OS (reproduced on) | FreeBSD **15.1‑RC1**, amd64, `GENERIC` (`releng/15.1-n283533`) |
| Expected to affect | All branches; the code paths below are long‑standing |
| Host controller | Intel Sunrise Point‑LP xHCI, USB High‑Speed (480 Mbps) |
| Reference player | MPD 0.24.12, OSS output to `/dev/dsp0`, `dev.pcm.0.bitperfect=1` |

### Device used to reproduce
| Item | Value |
|------|-------|
| Device | OKTO RESEARCH **DAC8 STEREO** (D/A only — no analog inputs) |
| USB IDs | idVendor `0x152a`, idProduct `0x88c5`, bcdDevice `0x0160` |
| Firmware | Thesycon UAC2 (`bInterfaceProtocol 0x20`), `bcdUSB 0x0200` |
| Link | USB High‑Speed |

> Note: although the DAC8 has **no physical inputs**, its USB descriptor still
> advertises a UAC2 **capture interface** (Interface 2) — common boilerplate in
> Thesycon/XMOS reference firmware. It is this *vestigial, never‑streaming*
> capture interface that triggers the bug.

---

## Clock topology (the key structural fact)

From `usbconfig dump_curr_config_desc` and the Linux descriptor dump
(`/proc/asound/card*/stream0`):

* **One Clock Source** entity, `bClockID 0x29` (decimal **41**), internal
  programmable (`bmControls 0x07` → host‑programmable frequency + validity).
* A Clock Selector (`bClockID 0x28`) in front of it.
* **The same Clock Source `0x29` feeds both the playback (Interface 1) and the
  capture (Interface 2) AudioStreaming interfaces.**

So both `sc_mixer_clocks.bit_output` (playback) and `sc_mixer_clocks.bit_input`
(capture) end up pointing at clock id 41. Programming the rate for *either*
direction issues `SET_CUR` to the *same* physical clock.

---

## Root cause — measured

### 1. Two `SET_CUR` calls to the shared clock, capture wins

With `sysctl hw.usb.uaudio.debug=15` (GENERIC builds `options USB_DEBUG`), at the
start of 44.1 kHz playback:

```
uaudio_chan_set_param_speed: Selecting alt 6
uaudio_chan_set_param_speed: Selecting alt 7
uaudio20_set_speed: ifaceno=0 clockid=41 speed=44100     <-- playback sets shared clock to 44100
uaudio20_set_speed: ifaceno=0 clockid=41 speed=48000     <-- capture default 48000 then overwrites it
```

Same `clockid=41`. The second call wins; the device clock ends at 48 kHz.

### 2. The capture channel is idle, yet it owns the clock

`cat /dev/sndstat` with `hw.snd.verbose=2`, during 44.1 kHz playback:

```
[dsp0.play.0]:   spd 44100, fmt 0x00201000, flags ...<RUNNING,TRIGGERED,NBIO,BUSY,BITPERFECT>
                 interrupts <climbing>, underruns 0, feed <climbing>, ready <near full>
[dsp0.record.0]: spd 48000, fmt 0x00200010/0x00201000, flags 0x00000000   <-- NOT running, but holds 48000
```

The record channel has flags `0x00000000` (not `RUNNING`) — it is not streaming
a single byte — yet its 48000 Hz configuration is what sits on the shared clock.

### 3. The device confirms it is running at 48 kHz, not 44.1 kHz

The playback OUT endpoint is asynchronous with an explicit feedback IN endpoint
(`0x81`, Q16.16 at High‑Speed). While the host streams 44100:

```
uaudio_chan_play_sync_callback: Value = 0x0006000a
uaudio_chan_play_sync_callback: Comparing 48001 Hz :: 44100 Hz
```

`0x0006000a` in 16.16 = 6.0001 samples/microframe × 8000 = **~48001 Hz**. The
device's converter is clocked at 48 kHz while being fed 44.1 kHz frames → it
drains ~3900 samples/s faster than it is filled → underrun/mute/relock cycle.

### 4. It is a host/driver problem, not the device firmware

* The **same device, cable and host play the 44.1 kHz family perfectly under
  Linux** (`snd-usb-audio`). Linux programs the shared clock from the active
  playback stream and does not let the idle capture stream override it.
* A DAC with **no capture interface** (DacMagic 100) is stable at every rate on
  the same FreeBSD host.

---

## Relevant code paths

Line numbers are approximate, from FreeBSD **15.1‑RC1** `sys/dev/sound/usb/uaudio.c`.

1. **`uaudio_configure_msg()` (~line 1539)** — runs on the USB explore task and
   unconditionally reconfigures *both* directions of every child:
   ```c
   for (i = 0; i != UAUDIO_MAX_CHILD; i++) {
       uaudio_configure_msg_sub(sc, &sc->sc_play_chan[i], PCMDIR_PLAY);
       uaudio_configure_msg_sub(sc, &sc->sc_rec_chan[i], PCMDIR_REC);
   }
   ```

2. **`uaudio_configure_msg_sub()` (~line 1352)** — on `CHAN_OP_START` it selects
   the alt setting and then, for UAC2, programs the sample rate by iterating
   every clock id flagged for the channel's direction and calling
   `uaudio20_set_speed()` (~line 1449):
   ```c
   } else if (sc->sc_audio_rev >= UAUDIO_VERSION_20) {
       for (x = 0; x != 256; x++) {
           if (dir == PCMDIR_PLAY) {
               if (!(sc->sc_mixer_clocks.bit_output[x/8] & (1u << (x%8)))) continue;
           } else {
               if (!(sc->sc_mixer_clocks.bit_input[x/8]  & (1u << (x%8)))) continue;
           }
           if (uaudio20_set_speed(sc->sc_udev, sc->sc_mixer_iface_no, x, chan_alt->sample_rate))
               DPRINTF("setting of sample rate failed! (continuing anyway)\n");
       }
   }
   ```
   Because clock id 41 is set in **both** `bit_output` and `bit_input`
   (shared clock — see `uaudio20_mixer_find_clocks_sub()` ~line 4858), the REC
   pass reprograms the very clock the PLAY pass just set.

3. **Asynchronous feedback handling — secondary issue.** In
   `uaudio_chan_play_sync_callback()` (~line 2255) the explicit feedback endpoint
   value is only turned into a rate correction when there is **no** capture
   channel (`if (ch->priv_sc->sc_rec_chan[i].num_alt == 0)`, ~line 2306), and the
   feedback transfer is not even submitted when a capture channel exists
   (~line 2332). For a device whose capture interface never streams, this means
   the explicit feedback endpoint is ignored, so there is no closed‑loop
   correction to mask a mis‑programmed clock either.

---

## Proposed fix (direction)

**Primary — do not let an idle/secondary direction reprogram a clock that is
shared with the active streaming direction.** The active stream must own the
shared Clock Source. Concretely, in the UAC2 rate‑setting loop of
`uaudio_configure_msg_sub()`, before issuing `SET_CUR` to a clock id, skip it if
that clock id is also referenced by the *other* direction's channel that is
currently `RUNNING` at a different rate. Equivalently: only program a clock for a
channel that is the one actually transitioning to `RUNNING`, and never let a
non‑running channel push its default rate onto a clock another running channel
depends on.

UAC2 constraint to respect: a single Clock Source physically cannot serve two
different rates simultaneously, so when both directions stream concurrently they
must already agree on one rate; that concurrent case needs its own coherent
handling (e.g. the second stream adopts the first's rate). The common failure
mode here, however, is purely an **idle** capture stream forcing its default —
which should never override active playback.

**Secondary (optional, matches Linux behaviour).** Consider honouring the
device's explicit asynchronous feedback endpoint even when a capture interface
is present, rather than only when `sc_rec_chan[i].num_alt == 0`. Not required if
the clock is programmed correctly, but it brings `uaudio` in line with
`snd-usb-audio` for async devices.

### Diagnostic stopgap used to confirm the hypothesis (NOT proposed for upstream)

To verify the diagnosis, the reporter dropped the capture channels for this one
device, right after `uaudio_chan_fill_info()` in `uaudio_attach()`:

```c
/* local diagnostic only — confirms the shared-clock hypothesis */
if (uaa->info.idVendor == 0x152a && uaa->info.idProduct == 0x88c5) {
    for (i = 0; i != UAUDIO_MAX_CHILD; i++)
        sc->sc_rec_chan[i].num_alt = 0;
}
```

Result: the OKTO then enumerates play‑only, the shared clock follows playback,
and **44.1 kHz locks cleanly and bit‑perfect** (`underruns 0`, gap‑free, stable
front panel). This is offered only as confirmation of the root cause; the real
fix should be the general clock‑ownership change above, which preserves capture
for devices that genuinely record.

---

## Reproduction

1. FreeBSD 15.1‑RC1, `uaudio(4)`, a UAC2 device whose playback and capture
   interfaces share one Clock Source, on a High‑Speed port.
2. Play bit‑perfect audio at any **44.1 kHz‑family** rate to `/dev/dsp0`
   (e.g. MPD OSS output, `dev.pcm.0.bitperfect=1`, no rate enforcement).
   → device drops/re‑acquires lock continuously; `sndstat` shows the play
   channel `RUNNING` with `underruns 0`, and the record channel idle at a
   48 kHz‑family rate.
3. Play any **48 kHz‑family** rate → stable.
4. Same machine/cable/port under Linux (`snd-usb-audio`) → 44.1 kHz plays
   perfectly.

---

## Diagnostics to gather and attach

```sh
# 1. Enable driver tracing (GENERIC has options USB_DEBUG; if not, see note below)
sysctl hw.usb.uaudio.debug=15
sysctl hw.snd.verbose=2

# 2. Start 44.1 kHz playback, then capture:
dmesg | grep -E 'uaudio|set_speed|clockid=|Comparing|sample_rem'   # the two SET_CUR calls + feedback
cat /dev/sndstat                                                   # play RUNNING + idle record rate
sysctl dev.pcm.0 hw.usb.uaudio                                     # knobs in effect

# 3. Full descriptors (run as root):
usbconfig dump_device_desc dump_curr_config_desc                   # clock entities, alt settings, endpoints

# 4. Restore:
sysctl hw.usb.uaudio.debug=0 hw.snd.verbose=0
```

> If `hw.usb.uaudio.debug` is reported as an unknown OID, the running
> `snd_uaudio.ko` was built without `USB_DEBUG`. Rebuild the module with it:
> `cd /usr/src/sys/modules/sound/driver/uaudio && make CFLAGS+=-DUSB_DEBUG`
> (GENERIC kernels enable `options USB_DEBUG`, so the in‑tree module normally has
> it).

To build/test a patched module without a full kernel build:
```sh
cd /usr/src/sys/modules/sound/driver/uaudio && make
# stop the audio app, then:
kldunload snd_uaudio                 # devd reloads from /boot/kernel
cp /usr/obj/usr/src/<arch>/sys/modules/sound/driver/uaudio/snd_uaudio.ko /boot/kernel/
kldunload snd_uaudio && kldload snd_uaudio
usbconfig -d ugenX.Y reset           # clean re-enumeration
```

---

## How to file the bug

### Maintainer
* **Hans Petter Selasky** `<hselasky@FreeBSD.org>` — USB stack and `uaudio(4)`
  maintainer. Add as reviewer / CC.

### Option A — Bugzilla (problem report)
* URL: **https://bugs.freebsd.org/bugzilla/**
* New bug → Product **Base System**, Component **kern**.
* Suggested summary:
  *"uaudio(4): idle capture stream clobbers active playback sample rate on
  devices with a shared UAC2 Clock Source (44.1 kHz family unusable)"*
* In the description, paste the **Root cause — measured** section (the two
  `clockid=41` `SET_CUR` calls, the idle `dsp0.record.0` at 48000, the
  `Comparing 48001 Hz :: 44100 Hz` feedback) and note **Linux works**.
* Attach: the `dmesg` trace, `sndstat` verbose output, `usbconfig` dumps, and a
  unified diff of any proposed change.
* Set the bug's "Assignee" or add `hselasky` to CC.

### Option B — Phabricator (preferred for an actual patch)
* URL: **https://reviews.freebsd.org/**
* Create a Differential revision (web "Create Diff", or `arc diff` if you have
  Arcanist). Generate the patch with `git diff`/`svn diff` against `main`.
* Add reviewer **hselasky**; tag the **usb** / **multimedia** group if offered.
* Put a one‑paragraph summary + "Test Plan" (the reproduction above) in the
  revision. Link the Bugzilla PR number (`PR: kern/NNNNNN`) in the commit
  message so they cross‑reference.

### Option C — Mailing lists (discussion / heads‑up)
* `freebsd-multimedia@FreeBSD.org` (sound) and `freebsd-usb@FreeBSD.org` (USB).
  Good for discussing the fix direction before/after opening the review.

### What makes this report strong
* Exact, reproducible trace showing the **second `SET_CUR` overwriting the
  first on the same `clockid`**.
* Proof the host feed is gap‑free (`underruns 0`) — rules out the usual
  "buffer/feedback" explanations.
* A cross‑OS control (**Linux works**) and a hardware control (**a DAC without a
  capture interface works**), isolating the bug to `uaudio`'s shared‑clock
  handling.
* A confirmed (if device‑specific) stopgap proving that removing the capture
  channel resolves it — pointing straight at the fix.

---

## Appendix — raw evidence

### FreeBSD `dmesg` (with `hw.usb.uaudio.debug=15`), 44.1 kHz playback start
```
uaudio_configure_msg_sub: fps=8000 sample_rem=4100          # 44100 % 8000 = 4100  (playback)
uaudio20_set_speed: ifaceno=0 clockid=41 speed=44100
uaudio20_set_speed: ifaceno=0 clockid=41 speed=48000
uaudio_configure_msg_sub: fps=8000 sample_rem=0             # 48000 % 8000 = 0      (capture default)
uaudio_chan_play_sync_callback: Value = 0x0006000a
uaudio_chan_play_sync_callback: Comparing 48001 Hz :: 44100 Hz
```

### FreeBSD `sndstat` (`hw.snd.verbose=2`), 44.1 kHz playback
```
[dsp0.play.0]:   spd 44100, fmt 0x00201000, flags 0x2000014c
                 interrupts 3863, underruns 0, feed 3862, ready 119784
                 channel flags=0x2000014c<RUNNING,TRIGGERED,NBIO,BUSY,BITPERFECT>
                 {userland} -> feeder_root(0x00201000) -> {hardware}
[dsp0.record.0]: spd 48000, fmt 0x00200010/0x00201000, flags 0x00000000
```

### Linux `/proc/asound/card0/stream0` (same device, 44.1 family plays fine)
```
Playback:
  Status: Running
    Interface = 1, Altset = 1
    Format: S32_LE, Channels: 2
    Endpoint: 0x01 (1 OUT) (ASYNC)
    Rates: 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000
    Sync Endpoint: 0x81 (1 IN)          # explicit Q16.16 feedback
Capture:
  Status: Stop
    Interface 2 ...                     # capture interface present but idle
```

### FreeBSD enumeration (single programmable clock feeds both directions)
```
uaudio0: Play[0]:   384000 / ... / 48000 / 44100 Hz, 2 ch, 32-bit S-LE PCM
uaudio0: Record[0]: 384000 / ... / 48000 / 44100 Hz, 2 ch, 32-bit S-LE PCM
```
