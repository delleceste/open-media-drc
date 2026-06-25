# `uaudio(4)`: follow the asynchronous feedback rate, like Linux

Analysis behind [`uaudio-feedback-follow.c.patch`](uaudio-feedback-follow.c.patch).
**Status: written from a source audit (FreeBSD 15.1 vs Linux 7.1), NOT yet built
or listen-tested.** Build per [`README.md`](README.md), then A/B against the
stock module with the OKTO connected.

> This supersedes an earlier idea ("just poll the feedback endpoint more often").
> That idea is **wrong** — see [Why "poll more often" is wrong](#why-poll-more-often-is-wrong).

---

## The question

A USB asynchronous DAC runs off its own crystal and reports, over an isochronous
IN **feedback endpoint**, the exact rate it wants ("send me 191994 Hz, not
192000"). The host should just **follow that number**. So why does FreeBSD tick
on the OKTO while Linux is clean?

## What Linux 7.1 does (`sound/usb/endpoint.c`)

The feedback value *is* the rate, tracked continuously and fractionally:

- `snd_usb_handle_sync_urb()` runs **every feedback interval**, reads `f`, accepts
  it across a **wide window** — `freqn - freqn/8 … freqn + freqn/2` (−12.5%…+50%,
  `freqmax = freqn + freqn/2`) — and sets `ep->freqm = f` **verbatim**. No small
  per-update clamp.
- `synced_next_packet_size()` sizes **every packet** straight from `freqm` with a
  16.16 phase accumulator:
  ```c
  phase = (ep->phase & 0xffff) + (ep->freqm << ep->datainterval);
  ret   = min(phase >> 16, ep->maxframesize);
  ```
  So the correction is **spread evenly over every packet** — never bunched.
- Buffers are sized from `freqmax` (`ep->freqmax = ep->freqn + (ep->freqn >> 1)`,
  then `maxsize` from it), so following a faster-than-nominal device never
  overflows.

## What FreeBSD 15.1 does (`sys/dev/sound/usb/uaudio.c`)

Two structural differences in `uaudio_chan_play_sync_callback()` /
`uaudio_chan_play_callback()`:

1. **It doesn't follow the rate — it nudges the nominal rate by a batch.** The
   per-frame distribution accumulator (`sample_rem`/`sample_curr`,
   `bytes_per_frame[0]/[1]`) always distributes the **nominal** `sample_rate`. The
   feedback only sets `jitter_curr = temp - sample_rate`, **hard-clamped to
   `±sample_rate/16000` (~62 ppm)**, applied ±1 sample per frame.
2. **The feedback is read once per second** and the whole second's correction is
   applied greedily at the *start* of the second (e.g. `jitter_curr = -6` is spent
   over the first ~6 microframes ≈ 0.75 ms, then nothing for the rest of the
   second).

Consequences for the OKTO (true rate ≈ 191994 Hz, −6 samples/s ≈ 31 ppm):

- The 31 ppm offset is *within* the 62 ppm clamp, so steady state "works" — but
  the correction arrives as a **once-per-second bunched blip**, a periodic
  disturbance a stiff DAC FIFO can turn into an audible tick. (At rates that are
  exact multiples of the USB frame rate — 48/96/192 kHz, all multiples of 8000 —
  `sample_rem == 0` and `bytes_per_frame[0] == [1]`, so the base accumulator
  *cannot* shave a sample at all; only the bunched `jitter_curr` can.)
- A clock relock that momentarily needs more than 62 ppm is **truncated** by the
  clamp, so the host under-corrects until the device drifts back in range.

The Cambridge DacMagic 100 tolerates both (more forgiving FIFO/feedback), which
is why it never ticks on the same host.

## Why "poll more often" is wrong

The tempting one-liner — read the feedback endpoint N×/second instead of once —
**breaks the correction**, because `jitter_curr` is a *per-second batch*, not a
rate. `uaudio_chan_play_callback()` drains `jitter_curr` to 0 within ~1 ms of each
read; re-seeding the **full** `temp - sample_rate` delta N times per second
applies it N times → an ~N× over-correction (e.g. −6 samples/s becomes ≈ −300
samples/s at 50 Hz polling: gross rate error). Polling cadence cannot be raised
without first changing what the feedback *means* to the driver.

## The fix in this patch

Make FreeBSD **follow the feedback rate smoothly**, like Linux, by folding it into
the existing per-frame accumulator instead of the clamped batch. In the async
(no-capture) branch of `uaudio_chan_play_sync_callback()`:

```c
uint32_t base    = temp / fps;                       /* whole samples/frame   */
uint32_t bpf_max = howmany(sample_rate, fps) * ss;   /* nominal alloc bound   */

if ((base + 1) * ss <= bpf_max) {
    ch->bytes_per_frame[0] = base * ss;
    ch->bytes_per_frame[1] = (base + 1) * ss;
    ch->sample_rem         = temp - base * fps;       /* fractional remainder  */
    ch->jitter_curr        = 0;                       /* batch path disabled   */
} else {
    /* device faster than the pre-sized buffer allows: keep stock clamp */
    ...existing jitter_curr clamp...
}
```

Now `sample_rem` carries the **feedback** remainder, so `play_callback`'s existing
accumulator distributes `temp` samples/second **evenly across every frame** (no
bunching), with no `±62 ppm` clamp. This is the FreeBSD equivalent of Linux's
`freqm` + phase accumulator.

**Safety / scope (deliberately conservative):**
- Applied only when `(base+1) * ss <= bytes_per_frame[1]_nominal`, i.e. the result
  fits the transfers and ring buffer already allocated for the nominal rate. For a
  device at or **below** nominal (the OKTO) this always holds; a device running
  *above* nominal falls back to the stock clamp (the proper general fix would size
  buffers from a `freqmax` like Linux — left for upstream).
- `play_sync_callback` and `play_callback` run serialized in the same per-controller
  USB process, so updating several `ch->` fields together is race-free (the stock
  code already relies on this for `jitter_curr`).
- Cadence is left at once/second: with a *rate* (not a batch) a 1 s-stale value is
  off by <1 sample of accumulated phase — negligible. (With this model, polling
  more often is now *safe* and would only speed relock response; optional.)

## Apply, build, deploy

This patch changes one function in `sys/dev/sound/usb/uaudio.c` and ships as the
loadable `snd_uaudio.ko` module — **no full kernel rebuild**. It must be applied
**together with** `uaudio.c.patch` (the capture-disable fix); both are required
for the OKTO. `Makefile.patch` restores `USB_DEBUG` (matches stock GENERIC).
([`README.md`](README.md) is the canonical copy of this flow.)

### 1 — apply the source patches
```sh
cd /usr/src
patch -p1 < /path/to/freebsd-uaudio-patch/uaudio.c.patch              # capture-disable
patch -p1 < /path/to/freebsd-uaudio-patch/uaudio-feedback-follow.c.patch
patch -p1 < /path/to/freebsd-uaudio-patch/Makefile.patch
```
Each hunk should report `succeeded`. (Dry-run first with `--dry-run` if unsure.)

### 2 — build the module
```sh
cd /usr/src/sys/modules/sound/driver/uaudio
make clean && make
# -> /usr/obj/usr/src/amd64.amd64/sys/modules/sound/driver/uaudio/snd_uaudio.ko
```

### 3 — deploy (install + reload, no reboot needed)
```sh
OBJ=/usr/obj/usr/src/amd64.amd64/sys/modules/sound/driver/uaudio/snd_uaudio.ko

# back up the CURRENT stock module once (refresh after every OS update so the
# revert path matches the running kernel ABI)
sudo cp -f /boot/kernel/snd_uaudio.ko /boot/kernel/snd_uaudio.ko.orig

sudo service musicpd stop                  # release /dev/dsp0
sudo cp -f "$OBJ" /boot/kernel/snd_uaudio.ko
sudo kldunload snd_uaudio                  # devd auto-reloads from /boot/kernel
UG=$(usbconfig | awk '/DAC8STEREO/{print $1}' | tr -d ':')
sudo usbconfig -d "$UG" reset              # clean re-enumeration
sudo service musicpd start
```

### 4 — verify the patched module is live
```sh
# both patches build into one .ko; the capture-disable banner confirms the
# patched module loaded (the feedback-follow change has no banner of its own):
dmesg | grep -i 'OKTO DAC8: capture interface disabled'
cat /dev/sndstat | grep pcm0               # OKTO, play-only
sysctl dev.pcm.0.feedback_rate             # device's reported rate (e.g. 191994)
```
There is no sysctl that prints the new path directly — confirm behaviour with the
test plan below (USB tap + listening).

### 5 — revert to stock
```sh
sudo service musicpd stop
sudo cp -f /boot/kernel/snd_uaudio.ko.orig /boot/kernel/snd_uaudio.ko
sudo kldunload snd_uaudio && sudo kldload snd_uaudio
sudo service musicpd start
```

> Persistence: the module lives in `/boot/kernel/snd_uaudio.ko` and survives
> reboot, but `freebsd-update` / `make installkernel` overwrites it with the stock
> module — rebuild + reinstall from these patches after any OS/kernel update.

## Test plan

1. Reconnect the OKTO; apply/build/deploy as above.
2. Enable the glitch detector (`../glitch-debug.sh on`), provoke the marginal case
   (resamp @ 44.1 kHz), and run a multi-minute USB tap during playback
   (`../glitch-debug.sh usbtap 300`).
3. Compare `usbgap`/`usbshort` counts and **listen** for the tick, against a
   stock-module baseline. Fewer/zero + no audible tick ⇒ the batch/clamp feedback
   handling was the cause.

## Upstream

The clean general change is Linux's: track the feedback as a fractional rate
per packet and size buffers from a max-feedback rate. This in-tree patch is the
narrow, allocation-safe subset that fixes the play-only OKTO case.
