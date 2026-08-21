# ESI U24 XL — selecting the S/PDIF input, and why `pcm2` reads `0.00`

## Summary

`omdrc-cdin` captures from the U24 XL, but the card has **two** inputs and only
one is live at a time. Which one is selected is a mixer setting, not a `cdin`
setting, and it does **not** survive a reboot. On a fresh boot the card comes up
on the *analog RCA* input, so `cdin` records silence from the CD transport until
the recording source is switched.

The command is:

```sh
mixer -f /dev/mixer.capture pcm2.recsrc=set
```

The rest of this document is the reasoning behind that one line, because two
things about the mixer output are actively misleading.

## The device

```
$ cat /dev/sndstat
pcm0: <OKTO RESEARCH DAC8STEREO> (play/rec) default
pcm1: <ESI U24XL> (play/rec)

$ mixer -f /dev/mixer.capture
pcm1:mixer: <ESI U24XL> on uaudio1 (play/rec)
    vol       = 0.75:0.75     pbk
    pcm       = 0.75:0.75     pbk
    line      = 0.75:0.75     rec src
    pcm2      = 0.00:0.00     rec
```

`/dev/mixer1` pairs 1:1 with `/dev/dsp1`, and `/dev/mixer.capture` with
`/dev/dsp.capture`. Unit numbers are not stable by themselves — see the next
section, which is why the commands below name the role, not the number.

## Stable names: `/dev/dsp.capture` is the U24 XL, whatever unit it took

Out of the box the numbering above is a coin toss decided by USB port order, and
on this box the U24 XL wins it: it sits on a lower-numbered root-hub port than
the DAC, so it attaches first and comes up as `pcm0`. That used to matter a
great deal, because the chain addressed the DAC by unit and nothing else.

It no longer does. `omdrc_sndlink` (rc.d + a devd rule) keeps two symlinks
pointed at the right cards, by role:

```
/dev/dsp.dac       /dev/mixer.dac       the DAC
/dev/dsp.capture   /dev/mixer.capture   this card
```

```sh
sysrc omdrc_sndlink_enable=YES
sysrc omdrc_sndlink_capture="ESI U24XL"   # or 0xVID:0xPID — see below
service omdrc_sndlink status              # roles, links, and the recsrc
```

The capture link exists **only** when that name matches a card, which is also
what makes the whole capture half of the service opt-in: a box with no CD input
leaves `omdrc_sndlink_capture` empty and gets nothing but `/dev/dsp.dac`.

The match is a substring of the description `/dev/sndstat` prints, or a USB
`vendor:product[:serial]` pair, which is worth preferring if you own two cards
whose descriptions are alike. The ids come from the card's USB parent:

```sh
$ sysctl -n dev.pcm.1.%parent                 # -> uaudio1
$ sysctl -n dev.uaudio.1.%pnpinfo             # -> vendor=0x... product=0x... sernum="..."
$ sysrc omdrc_sndlink_capture="0x0a92:0x0053"
```

Capability alone cannot pick this card out, which is why a name is required:
`dev.pcm.N.mode` is a bitmask (`MIXER 0x01 | PLAY 0x02 | REC 0x04`) and the OKTO
DAC reports `7` — play *and* rec — exactly like a capture interface.

**There is no declarative way to pin the unit number**, which is worth knowing
before reaching for one:

* **Device hints** (`hint.pcm.1.at="uaudio0"`) rely on `BUS_HINT_DEVICE_UNIT`,
  and only `acpi(4)`, `pci(4)` and `isa(4)` implement that method — `uaudio(4)`
  does not, so the hint is silently ignored. Worse, `devclass_alloc_unit()`
  *skips* any unit that carries an `at` hint when it numbers an unwired device,
  so hinting `pcm0` would take unit 0 away from the DAC as well.
* **devd** is a userland consumer of events the kernel has already acted on: by
  the time the ATTACH notification arrives the unit is allocated and `/dev/dspN`
  exists. It cannot influence the number, and it has no equivalent of udev's
  `NAME=`/`SYMLINK=`. It *can* create the symlink afterwards, which is the whole
  design.
* **`hw.snd.default_unit`** only selects among the units that already exist.

Two consequences worth remembering:

* A sysctl OID cannot be symlinked, so the few readers of `dev.pcm.<unit>.*`
  resolve the unit from the link instead: `readlink /dev/dsp.dac` gives `dspN`,
  and `N` follows. `drc.sh` has this as `dac_unit()`.
* Hotplug is covered rather than refused. The devd rule fires on the **pcm**
  attach — at which point the unit exists and can be identified — and `update`
  is a full rescan, so a card that is replugged onto a different unit simply
  takes its link with it, without touching the other card. The mixer state that
  a replug resets (the S/PDIF `recsrc` below) is re-asserted in the same pass.

## Trap 1: the input you want is called `pcm2`, not `pcm`

Only devices flagged `rec` can be a recording source. On this card that is
`line` and `pcm2`; `vol` and `pcm` are `pbk` (playback-only) and the kernel
rejects them outright:

```
$ mixer -f /dev/mixer.capture pcm.recsrc=add
mixer: pcm.recsrc=add: Operation not supported by device
```

Both rec devices are the two positions of the card's single USB selector unit:

```
$ sysctl dev.pcm.1.mixer.selector_0
dev.pcm.1.mixer.selector_0.min: 1
dev.pcm.1.mixer.selector_0.max: 2
dev.pcm.1.mixer.selector_0.val: 1
```

- `line` = selector position 1 = analog RCA input
- `pcm2` = selector position 2 = digital (S/PDIF) input — **this is the CD path**

Because it is a *selector*, the two are mutually exclusive: `set`, `add` and
`toggle` all end up with exactly one source. Applying it flips the hardware:

```
$ mixer -f /dev/mixer.capture pcm2.recsrc=set
pcm2.recsrc: remove -> add

$ sysctl -n dev.pcm.1.mixer.selector_0.val
2
```

`mixer -f /dev/mixer.capture -s` prints just the active source, which is the cheap
check to put in a script. To go back to the analog input:
`mixer -f /dev/mixer.capture line.recsrc=set`.

## Trap 2: `pcm2 = 0.00:0.00` is **not** a muted capture gain

This is the part that costs time. `pcm2` sits at `0.00` next to three devices at
`0.75`, which reads as "your record level is zeroed". It is not. **There is no
gain to raise, and raising it does nothing.**

Sweeping it through `0.00 → 1.00 → 0.50 → 0.00` moves **no** hardware node —
every `dev.pcm.1.mixer.vol_*` value stays put. For contrast, sweeping `line`
moves them immediately. There is no software fallback either:
`sys/dev/sound/pcm/mixer.c` applies feeder volume only to `SOUND_MIXER_PCM`
under `SD_F_SOFTPCMVOL`, which is the *playback* path.

The `0.00` is a display artifact of a gap in a kernel table. In
`sys/dev/sound/pcm/mixer.c`:

```c
static u_int16_t snd_mixerdefaults[SOUND_MIXER_NRDEVICES] = {
	[SOUND_MIXER_VOLUME]	= 75,
	[SOUND_MIXER_PCM]	= 75,
	[SOUND_MIXER_LINE]	= 75,
	[SOUND_MIXER_MIC] 	= 25,
	...
};
```

There is no `[SOUND_MIXER_PCM2]` entry, so it zero-initialises. `line` shows
`0.75` for the same reason it *is* in the table, at 75. Nothing was measured
from the card in either case.

Practical consequence: capture level on the S/PDIF input is whatever the
transport sends, bit-for-bit. That is the correct state for `cdin` — the whole
point of the chain is that no gain stage touches the samples.

## The volume scale is linear in dB, not an exponent

Worth recording since it is the obvious next suspicion about a `0.00`. It is a
plain linear map onto the raw range, from `uaudio_mixer_bsd2value()` in
`sys/dev/sound/usb/uaudio.c`:

```c
val = (val * mc->mul) / 100;   /* mul = maxval - minval */
val = val + mc->minval;
```

so `raw = oss*(max-min)/100 + min`, with `raw` in USB's 1/256 dB units. An OSS
value of `0` therefore means `minval` — the **bottom** of the range, maximum
attenuation — and never 0 dB unity. Measured on `line`, whose range is
`-10240 … +3072` raw = `-40.0 … +12.0` dB:

| `line` | raw | dB |
|--------|-------|-------|
| 1.00   | 3072  | +12.0 |
| 0.75   | -256  | -1.0  |
| 0.50   | -3584 | -14.0 |

An exact linear fit. So on a control that *is* wired, `0.00` would be -40 dB —
very quiet, but still not digital silence.

## Gotcha: the `vol_*` sysctls collide

Do not trust `dev.pcm.1.mixer.vol_1_*.val` versus `vol_3_*.val` to be different
readings. `uaudio_mixer_sysctl_handler()` (`uaudio.c`) looks a node up by
`wValue` alone and takes the first match:

```c
for (pmc = sc->sc_mixer_root; pmc != NULL; pmc = pmc->next) {
	for (chan = 0; chan != (int)pmc->nchan; chan++) {
		if (pmc->wValue[chan] != -1 && pmc->wValue[chan] == hint) {
			temp = pmc->wData[chan];
			goto found;
		}
	}
}
```

On this card both OID families resolve to the same node, which is why `vol_3_0`
happily reports `3072` while advertising `max: 0`. The per-OID `min`/`max` are
correct; `val` is not usable for telling the two apart.

Also note these report the driver's shadow copy (`mc->wData`), not a read-back
from the device — `uaudio_mixer_set()` is write-only.

## Persistence

The selector setting is lost on reboot, and a replug resets it too.
`/etc/rc.d/mixer` would not help much: it saves state only for `mixer0` unless
`mixer_enable="YES"` is set, and the U24 XL is USB, so it must be attached at
boot for the rc script to see it at all.

`omdrc_sndlink` asserts it instead, in the same pass that links the card, on
every attach:

```sh
omdrc_sndlink_capture_recsrc="auto"   # the default
```

`auto` does not hardcode this card. `mixer(8)` prints a flag per device — `rec`
for anything that can be a recording source, `src` for the one that currently
is — so the service looks for the digital input among the sources the card
actually offers (`pcm2`, then the `dig1..3` fallbacks) and leaves a card with
none of them alone. Set it to `line` to force the analog input, or to `none` to
keep hands off. Either way it is idempotent and costs one USB control transfer.

## Trap 3: a "44100 Hz" capture open that is neither 44100 Hz nor bit-perfect

This is the expensive one, because every layer reports success.

By default FreeBSD puts a **virtual channel** in front of the card. The hardware
then runs at `dev.pcm.N.rec.vchanrate` — **48000**, always, regardless of the
card or the source — and a kernel `feeder_rate` resamples that to whatever the
application asked for. The OSS API tells the application exactly what it wanted
to hear: `SNDCTL_DSP_SPEED` returns 44100, `SETFMT` returns the requested width,
every ioctl succeeds. `cdin` checks all three and is satisfied.

What actually happens with a 44.1 kHz transport on the S/PDIF input is that the
card delivers 44100 frames per second into a stream the kernel believes is
48000, and the resampler scales them:

> 44100 × 44100/48000 ≈ **40517 Hz** arriving at the application.

`cdin` then reads 40.5k frames/s and writes 44.1k to the DAC. The lead drains at
3.6k frames/s, so a 2000 ms lead is gone in about 25 seconds — after which the
DAC underruns continuously and the music is audibly destroyed. It looks exactly
like catastrophic clock drift, and it is not drift at all.

The only place it is visible:

```sh
sysctl hw.snd.verbose=2 && cat /dev/sndstat
```

```
[dsp1.record.0]: spd 48000 ...                          <-- the hardware
dsp1.record.0[dsp1.virtual_record.0]: spd 44100/48000 ...
  ... -> feeder_rate(q:4  48000 -> 44100) -> {userland}  <-- the lie
```

The cure is to remove the vchan layer and demand the hardware format:

```sh
sysctl dev.pcm.1.rec.vchans=0
sysctl dev.pcm.1.bitperfect=1
```

after which the same command prints the whole capture path as:

```
[dsp1.record.0]: spd 44100, fmt 0x00210000, ... <RUNNING,TRIGGERED,BUSY,BITPERFECT>
	{hardware} -> feeder_root(0x00210000) -> {userland}
```

`feeder_root` and nothing else — one `memcpy` from the USB transfer to the read
buffer, at the rate the transport is really running.

### Where these settings have to live, and why not `/etc/sysctl.conf`

`dev.pcm.<unit>.*` is the natural home for them and it is the wrong one here,
for three independent reasons:

* the OID is keyed by **unit number**, which is the one thing that is not stable
  on this box — `dev.pcm.0.*` may be the *capture* card, so a line meant for the
  DAC lands on the U24 XL;
* `/etc/rc.d/sysctl` is read at **rc position 3**, long before anything knows
  which card is which;
* a re-attach re-creates the whole `dev.pcm.<unit>.*` tree from driver defaults,
  so anything applied at boot is gone the moment a card is replugged.

So the settings are keyed by **role** in `/etc/rc.conf` and applied by
`omdrc_sndlink` on every attach — which is what makes the third point harmless
rather than a trap:

```sh
omdrc_sndlink_dac_sysctls="bitperfect=1 play.vchans=0 mixer.vol_0.val=0"
omdrc_sndlink_capture_sysctls="bitperfect=1 rec.vchans=0"
```

Global `hw.snd.*` and `hw.usb.uaudio.*` tunables are **not** unit-keyed and
survive a re-attach, so they stay in `/etc/sysctl.conf` where they belong. Only
the `dev.pcm.*` lines move.

### Which card ends up on which unit no longer matters

It used to be worth swapping the USB cables so the DAC sat on the
lower-numbered port and won the boot race (`sysctl dev.uaudio.N.%location`, the
`port=` field). With the links in place there is nothing to win: the roles are
assigned by identity, both cards keep whatever unit they were given, and no card
is ever replugged to make a number come out right — which is also why the mixer
state below now survives a boot.

### The consequence: capture is 24-bit, and `cdin` must ask for 24-bit

With the format feeder gone, the card's own width is the only one it accepts,
and the U24 XL's capture endpoint offers exactly one:

```
uaudio1: Record[0]: 48000 Hz, 2 ch, 24-bit S-LE PCM format. (selected)
uaudio1: Record[0]: 44100 Hz, 2 ch, 24-bit S-LE PCM format.
```

So `cdin` needs `--bits 24` (the `omdrc_cdin_bits` rc variable, default 24). It
opens the output side wider on its own and widens 24 → 32 by left-justification,
which is byte placement, not arithmetic — the stream stays bit-exact. Note the
48000 entry is marked `(selected)`: that is only the alt-setting `uaudio(4)`
parks on at attach, not a limit. Asking for 44100 switches it, as long as no
vchan is in the way holding the hardware at its own rate.

### Why 24-bit capture needed a `cdin` fix first

Asking for 24 bits was not enough on its own — `cdin` refused the device:

```
[ERR] capture /dev/dsp1: unavailable — period of 1024 frames is 6144 bytes,
      which is not a power of two
```

That error was `cdin` being stricter than OSS requires, and it made 24-bit
capture impossible on any device. The arithmetic:

`SNDCTL_DSP_SETFRAGMENT` takes one `int`. The high 16 bits are the maximum
number of fragments; the low 16 bits are an exponent `n`, and the fragment size
is `2^n` **bytes**. So the only sizes that can be *expressed* are 256, 512,
1024, 2048, 4096, 8192 … — there is no encoding for "6144 bytes".

`cdin` works in frames (`--period`, default 1024). A frame is one sample instant
across all channels:

| width | frame | 1024 frames | expressible? |
|-------|-------|-------------|--------------|
| 32-bit stereo | 4 × 2 = **8** bytes | 8192 = 2¹³ | yes |
| 24-bit stereo | 3 × 2 = **6** bytes | 6144 = 2¹¹ × 3 | **no** |

And no other period rescues it: `6N = 2^k` would need 3 to divide a power of
two. **Every** period is inexpressible at 24-bit stereo, so the old code's
"pick a better period" advice had no answer.

The fix is to stop treating the fragment as the transfer size. It is not: the
fragment is the device's *interrupt granularity* — how much the driver
accumulates before it wakes a blocked `read()`. The read asks for whatever it
wants and blocks until that much has arrived, spanning as many fragments as it
takes. `cdin` now asks for the largest power of two that fits inside the period
(4096 bytes for a 6144-byte period) and goes on reading its own 1024-frame
periods. The device is simply woken slightly more often than strictly needed;
not one byte of audio changes. The log shows both numbers:

```
[DBG] /dev/dsp1: period of 1024 frames is 6144 bytes at 24-bit, which no
      fragment size can express; asking for 4096 bytes
[INF] /dev/dsp1: opened for capture, 44100 Hz 24-bit 2ch, period 1024 frames
      (device 682)
```

`682` is the device's own block in frames (4096 / 6, rounded down). `cdin` reads
1024 at a time regardless, and the run records `overruns 0`.

## Checklist when `cdin` captures silence

0. `service omdrc_sndlink status` — the U24 XL must hold the `capture` role with
   `bitperfect=1 rec.vchans=0`, and the DAC the `dac` role. If the stream is not
   silent but *distorted*, and the lead drains to zero in ~25 s, it is Trap 3
   above, not the input selector.
1. `ls -l /dev/dsp.capture` — it must exist and point at the U24 XL's unit. If it
   is missing, `omdrc_sndlink_capture` does not match the card.
2. `mixer -f /dev/mixer.capture -s` — must print `pcm2`. If it prints `line`, the card
   is on the analog input.
3. Ignore `pcm2 = 0.00:0.00`. It is cosmetic; see above.
4. Only then look at the transport, the optical/coax cable, and lock.

## References

- `mixer(8)` — rewritten in FreeBSD 14; `dev.control=value` syntax, `recsrc`
  modifiers `^ + - =`
- `sys/dev/sound/usb/uaudio.c` — `uaudio_mixer_bsd2value()`,
  `uaudio_mixer_sysctl_handler()`, `uaudio_mixer_set()`
- `sys/dev/sound/pcm/mixer.c` — `snd_mixerdefaults[]`, `mixer_set()`
- Observed on FreeBSD 15.1-RELEASE-p2, `uaudio1: <ESI U24XL, class 0/0,
  rev 1.10/0.01, addr 2>`, 2026-08-20
