# FreeBSD `uaudio(4)` patch — OKTO DAC8 STEREO 44.1 kHz fix

Local workarounds kept in-tree **while waiting for an official FreeBSD fix.**
There are two `uaudio(4)` source patches for this DAC:

1. **`uaudio.c.patch`** — disables the vestigial shared-clock capture interface
   so the 44.1 kHz family stops dropping lock (the continuous *flicker*).
   **Works** — confirmed live (`No recording`, no flicker). Full analysis:
   [`FreeBSD-uaudio-shared-clock-bug.md`](FreeBSD-uaudio-shared-clock-bug.md).

2. **`uaudio-feedback-follow.c.patch`** — makes the playback path *follow the
   device's reported feedback rate directly and smoothly* (fold it into the
   per-frame sample distribution), the way Linux `snd-usb-audio` does, instead of
   nudging the nominal rate with a clamped, once-per-second batch. Targets the
   *occasional tick* on the OKTO that the Cambridge DacMagic 100 never shows on
   the same host. **Candidate fix — written from a source audit, NOT yet built
   or heard; build and listen-test before trusting it.** Full analysis:
   [`uaudio-feedback-follow.md`](uaudio-feedback-follow.md).

3. **`uaudio-clock-before-alt.c.patch`** — driver-level fix candidate for the
   *rate-change cold-open silence* ("run drc.sh several times"; per the
   2026-07-06 user report **any** rate change can trigger it, not only
   44.1↔48 crystal crossings): program the UAC2 sample clock **before**
   selecting the streaming alt-setting (with the interface parked at alt 0,
   Linux's ordering) plus a tunable `hw.usb.uaudio.clock_settle_ms` pause on
   any rate change. Would replace the host-side `DAC_PRIME_CYCLES` prime in
   `drc.sh` (which only fires on crystal crossings, so it misses same-family
   changes) for *all* clients. **Built, installed, and partially
   listening-tested (2026-07-06): a cold 192k open and a 192k→44.1k crystal
   switch were both audible with zero primes. The full test-plan matrix (other
   crossings, same-family changes, virtual_oss, browser) is still pending —
   it was cut short by an unrelated virtual_oss livelock.** Full analysis:
   [`uaudio-clock-before-alt.md`](uaudio-clock-before-alt.md).

## What it fixes

The OKTO RESEARCH DAC8 STEREO (USB `0x152a:0x88c5`) drops/re-acquires USB
streaming lock continuously on the **44.1 kHz rate family** (44.1/88.2/176.4/
352.8 kHz) on FreeBSD, while the 48 kHz family is fine. Root cause: the device
exposes one UAC2 **Clock Source shared between playback and capture**, and
`uaudio(4)` lets the idle capture channel reprogram that clock to 48 kHz,
clobbering the active playback rate. (Details in the bug-report doc.)

This patch makes `uaudio` **drop the (vestigial — the DAC8 has no analog inputs)
capture interface for this device**, so the shared clock follows playback.
Result: **bit-perfect 44.1 kHz, stable lock, no flicker.**

> This is a deliberately narrow, device-gated workaround — **not** the fix to
> propose upstream. The proper fix is general (an idle/secondary stream must not
> reprogram a shared clock); see the bug-report doc.

## Built/tested environment

- FreeBSD **15.1-RELEASE**, amd64, `GENERIC` (`releng/15.1-n283562`)
- The module is built **with `USB_DEBUG`** (restores the
  `hw.usb.uaudio.debug` sysctl + DPRINTF tracing, matching stock GENERIC).
- No prebuilt binary is kept here: a `.ko` is **ABI-specific to its kernel** and
  is overwritten by every OS update, so always **rebuild from the patches**
  (below) after a kernel change.

## Contents

| File | Purpose |
|------|---------|
| `uaudio.c.patch` | Source change to `sys/dev/sound/usb/uaudio.c` (the device-gated capture-disable / flicker fix). |
| `uaudio-feedback-follow.c.patch` | Source change to `sys/dev/sound/usb/uaudio.c` (follow the feedback rate smoothly, like Linux). |
| `Makefile.patch` | Adds `CFLAGS+=-DUSB_DEBUG` to the module Makefile. |
| `FreeBSD-uaudio-shared-clock-bug.md` | Flicker bug: full analysis + upstream bug-filing instructions. |
| `uaudio-feedback-follow.md` | Feedback-handling audit: FreeBSD vs Linux 7.1, why "follow the rate", test plan. |

## Apply from source and rebuild

```sh
cd /usr/src
patch -p1 < /path/to/uaudio.c.patch
patch -p1 < /path/to/uaudio-feedback-follow.c.patch   # independent region of the same file
patch -p1 < /path/to/Makefile.patch

cd /usr/src/sys/modules/sound/driver/uaudio
make clean && make
# -> /usr/obj/usr/src/amd64.amd64/sys/modules/sound/driver/uaudio/snd_uaudio.ko
```

## Install the freshly built module

```sh
OBJ=/usr/obj/usr/src/amd64.amd64/sys/modules/sound/driver/uaudio/snd_uaudio.ko

# back up the *current* stock module first (refresh it after every OS update,
# so the revert path always matches the running kernel's ABI)
sudo cp -f /boot/kernel/snd_uaudio.ko /boot/kernel/snd_uaudio.ko.orig

sudo service musicpd stop                 # release /dev/dsp0
sudo cp -f "$OBJ" /boot/kernel/snd_uaudio.ko
sudo kldunload snd_uaudio                 # devd auto-reloads from /boot/kernel
UG=$(usbconfig | awk '/DAC8STEREO/{print $1}' | tr -d ':')
sudo usbconfig -d "$UG" reset             # clean re-enumeration
sudo sysctl -f /etc/sysctl.conf           # reload resets buffer_ms -> restore baseline
sudo service musicpd start
```

Verify:
```sh
cat /dev/sndstat | grep pcm0              # expect: pcm0: <OKTO...> (play)   <- play-only
sysctl hw.usb.uaudio.debug                # exists (USB_DEBUG build)
```

## Revert to stock

```sh
sudo cp /boot/kernel/snd_uaudio.ko.orig /boot/kernel/snd_uaudio.ko
sudo kldunload snd_uaudio && sudo kldload snd_uaudio
```

## Persistence / upgrade caveat

The module lives in `/boot/kernel/snd_uaudio.ko`, so it **survives reboot**
(loaded by name via `devmatch`/`devd` on device attach). However
`freebsd-update` or `make installkernel` (any OS/kernel update) **overwrites it
with the stock module** — rebuild + reinstall from these patches afterwards.
Once the official fix lands upstream, this whole directory can be retired.
