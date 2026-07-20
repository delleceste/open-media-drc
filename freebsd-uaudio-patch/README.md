# FreeBSD `uaudio(4)` patches — OKTO DAC8 STEREO fixes

Local fixes kept in-tree **while waiting for an official FreeBSD fix.**
Two `uaudio(4)` source patches are currently applied to `/usr/src`, in this
order on top of stock `releng/15.1`:

1. **`uaudio-clock-before-alt.c.patch`** — fix for the *rate-change
   cold-open silence* ("run drc.sh several times"; any rate change, not just
   44.1↔48 crystal crossings): program the UAC2 sample clock **before**
   selecting the streaming alt-setting (interface parked at alt 0, Linux's
   ordering) plus a tunable `hw.usb.uaudio.clock_settle_ms` pause on any
   rate change. Replaces the host-side `DAC_PRIME_CYCLES` prime for *all*
   clients. **Built + installed since 2026-07-06; per listening reports,
   different-rate tracks now lock — full test matrix in the doc still worth
   finishing.** Full analysis:
   [`uaudio-clock-before-alt.md`](uaudio-clock-before-alt.md).

2. **`uaudio-shared-clock-fix.c.patch`** — the **proper, general fix** for
   the shared-clock 44.1 kHz flicker (bug #295933), replacing the retired
   device-gated capture-disable workaround: (a) the record stream that
   `uaudio` auto-starts as a jitter source for async playback is
   **rate-aligned to the playback rate** before starting; (b) a
   **shared-clock guard** stops any stream from reprogramming a shared UAC2
   Clock Source the other direction is actively using at a different rate;
   (c) the explicit-feedback SYNC transfer is **always submitted** so
   `dev.pcm.0.feedback_rate` stays live as a diagnostic. The OKTO's capture
   interface is **no longer removed** — `pcm0 (play/rec)` is expected.
   **Applied to `/usr/src` 2026-07-07, builds `-Werror`-clean standalone on
   stock and on top of patch 1; NOT yet installed to `/boot/kernel`, NOT
   yet listening-tested.** Full analysis:
   [`uaudio-shared-clock-fix.md`](uaudio-shared-clock-fix.md).

Also kept here (not applied):

- **`uaudio-feedback-follow.c.patch`** — candidate to make playback follow
  the device's reported feedback rate smoothly (Linux-style), targeting the
  *occasional tick*. **Unbuilt sketch; now needs REBASING** — it touches the
  same sync-callback region as the shared-clock fix's change (c). Analysis:
  [`uaudio-feedback-follow.md`](uaudio-feedback-follow.md).

Retired (removed from the tree and from this directory; see git history):

- `uaudio.c.patch` — the VID/PID-gated **capture-disable workaround** for
  the flicker. Confirmed the root cause live, superseded by the proper fix
  (patch 2). Reverted from `/usr/src` on 2026-07-07.
- `uaudio-shared-clock-guard.c.patch` — the guard-only sketch. The
  2026-07-07 audit showed guard-alone would break playback (the auto-started
  jitter rec stream keeps mismatched framing → the play callback strips
  samples continuously); folded into patch 2 together with the mandatory
  rate-alignment.

## What the flicker fix addresses

The OKTO RESEARCH DAC8 STEREO (USB `0x152a:0x88c5`) drops/re-acquires USB
streaming lock continuously on the **44.1 kHz rate family** on FreeBSD,
while the 48 kHz family is fine. Root cause: the device exposes one UAC2
**Clock Source shared between playback and capture**, and `uaudio(4)` lets
the (vestigial, never-streaming) capture side reprogram that clock to its
48 kHz default, clobbering the active playback rate. Details:
[`FreeBSD-uaudio-shared-clock-bug.md`](FreeBSD-uaudio-shared-clock-bug.md).

## Built/tested environment

- FreeBSD **15.1-RELEASE**, amd64, `GENERIC` (`releng/15.1-n283562`)
- The module is built **with `USB_DEBUG`** (restores the
  `hw.usb.uaudio.debug` sysctl + DPRINTF tracing, matching stock GENERIC).
- No prebuilt binary is kept here: a `.ko` is **ABI-specific to its kernel**
  and is overwritten by every OS update, so always **rebuild from the
  patches** (below) after a kernel change.

## Contents

| File | Purpose |
|------|---------|
| `uaudio-clock-before-alt.c.patch` | Clock-before-alt reorder + settle delay (cold-open silence fix). |
| `uaudio-shared-clock-fix.c.patch` | Shared-clock proper fix: jitter-stream rate alignment + clock guard + always-on feedback SYNC. |
| `uaudio-feedback-follow.c.patch` | Follow the feedback rate smoothly, like Linux (unbuilt candidate — needs rebase). |
| `Makefile.patch` | Adds `CFLAGS+=-DUSB_DEBUG` to the module Makefile. |
| `FreeBSD-uaudio-shared-clock-bug.md` | Flicker bug: full analysis + upstream bug-filing instructions (bug #295933). |
| `uaudio-shared-clock-fix.md` | The proper fix: design, audit of the guard-only sketch, test plan. |
| `uaudio-clock-before-alt.md` | Cold-open silence: analysis and test plan. |
| `uaudio-feedback-follow.md` | Feedback-handling audit: FreeBSD vs Linux 7.1. |

## Apply from source and rebuild

```sh
cd /usr/src
patch -p1 < /path/to/uaudio-clock-before-alt.c.patch
patch -p1 < /path/to/uaudio-shared-clock-fix.c.patch   # applies with offsets; also applies to pure stock
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
sudo cp -f /boot/kernel/snd_uaudio.ko /boot/kernel/snd_uaudio.ko.orig   # only if .orig is stale

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
cat /dev/sndstat | grep pcm0              # expect: pcm0: <OKTO...> (play/rec)  <- capture is BACK (by design)
sysctl hw.usb.uaudio.clock_settle_ms      # exists (clock-before-alt applied)
sysctl dev.pcm.0.feedback_rate            # tracks playback rate during playback
```
Then run the listening test plan in
[`uaudio-shared-clock-fix.md`](uaudio-shared-clock-fix.md) — no 44.1 kHz
flicker is the signal that counts.

## Revert to stock

```sh
sudo cp /boot/kernel/snd_uaudio.ko.orig /boot/kernel/snd_uaudio.ko
sudo kldunload snd_uaudio && sudo kldload snd_uaudio
```

## Persistence / upgrade caveat

The module lives in `/boot/kernel/snd_uaudio.ko`, so it **survives reboot**
(loaded by name via `devmatch`/`devd` on device attach). However
`freebsd-update` or `make installkernel` (any OS/kernel update) **overwrites
it with the stock module** — rebuild + reinstall from these patches
afterwards. Once the official fix lands upstream, this whole directory can
be retired.

## Known remaining limitation (separate bug): DAC displays 24 bit for 16-bit material

`uaudio` fixes **one wire format per attach** and pads 16-bit content into
the 32-bit container, so the DAC front panel shows **24** on 44.1 k/16-bit
tracks. **No effect on bit-perfectness or audio quality** — details below.
Not addressed by these patches.

**Why exactly 24 (and not 32) — confirmed in descriptors + source,
2026-07-07.** The OKTO's playback interface exposes four alt-settings
(FORMAT_TYPE_I `bSubslotSize`/`bBitResolution`): alt 1 = 24-in-32, alt 2 =
**native 16-bit**, alt 3 = 32-bit PCM, alt 4 = 32-bit RAW/DSD (capture has
the same 24/16/32 trio). Two `uaudio` behaviours combine:

1. `uaudio_chan_fill_info_sub()` (UAC2 branch) computes bit depth as
   `bSubslotSize * 8`, **ignoring `bBitResolution`** — so alts 1/3/4 all
   count as "32-bit".
2. The same function keeps only **one alt-setting per sample rate**
   (later matches are dropped as "Duplicate sample rate detected"), so the
   **first matching alt in descriptor order wins: alt 1**, whose declared
   resolution is **24**.

Result: FreeBSD streams 32-bit slots into the 24-valid-bit alt at every
rate. The DAC panel reports the alt's declared `bBitResolution` (24);
dmesg reports the container ("32-bit S-LE") — both describe the same alt.

**Why quality is untouched:** 16-bit samples sit in the top 16 bits,
LSBs zero-padded — the DAC converts exactly the original bits (no SRC, no
dither, no volume with `bitperfect=1`; see
`doc/BIT-PERFECT-VERIFICATION.md`). 24-bit material fits the alt exactly.
Only a hypothetical *true* 32-bit source would lose bits 25–32 — content
below the physical resolution of any DAC silicon.

**Options:**
- **Leave it (recommended).** The display shows the container's declared
  word length, not the file's. Cosmetic only.
- `hw.usb.uaudio.default_bits=16` would select the native 16-bit alt
  (panel reads 16) but forces **every** stream to 16 bit, truncating
  hi-res material. Note the knob is easy to apply wrong: it is `RWTUN`
  and a **driver reload resets it** — set it in `/boot/loader.conf` or
  set the sysctl *after* module load and re-attach via `usbconfig reset`
  only (this is the likely reason it appeared "ignored" in the earlier
  mitigation attempts logged in `../OKTO-DAC8-FreeBSD-44k1-flicker.md`).
- The correct fix is Linux-style **per-stream alt switching** — an
  architectural `uaudio` rework (format is fixed through the pcm feeder
  chain at attach). A small separable upstream improvement: honour
  `bBitResolution` when selecting/reporting formats instead of
  `bSubslotSize*8`.
