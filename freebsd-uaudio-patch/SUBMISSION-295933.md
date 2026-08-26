# Follow-up submission for PR 295933 / commit 755685dd665e

**What this is:** the shipped shared-clock fix left four things unfinished, one
of which re-creates the hazard it was written to prevent. Two patches close
them; the rest are declared, not patched.

**Route:** last time this went in as
[freebsd-src PR #2323](https://github.com/freebsd/freebsd-src/pull/2323),
authored by you and committed by **Christos Margiolis `<christos@FreeBSD.org>`**
(`Reviewed by: christos`, `MFC after: 2 weeks`). Same route again — a GitHub PR
with the two commits below, plus a comment on
[bug 295933](https://bugs.freebsd.org/bugzilla/show_bug.cgi?id=295933) pointing
at it. Worth CCing **Hans Petter Selasky `<hselasky@FreeBSD.org>`** — item 3 is
about isochronous endpoint policy, which is his side of the house.

The two commits are already split and ordered, build `-Werror` clean with and
without `USB_DEBUG`, and apply to `main` / `stable/15` after `755685dd665e`
(verified against a reconstruction of that tree: `releng/15.1` + the committed
patch, which is what upstream took):

| | |
|---|---|
| `uaudio-upstream-0001-shared-clock-write-discipline.c.patch` | items 1, 2, 4 |
| `uaudio-upstream-0002-prefer-explicit-feedback.c.patch` | item 3 |

---

## Read this part first

**Do not lead with "my DAC goes silent."** The audible symptom that started
this is intermittent and **has not been reproduced under controlled
conditions** — 42 consecutive open/play/close cycles across all eight supported
rates, 21 of them transitions *into* 44.1 kHz, were all audible with correct
pitch. Leading with an unreproducible symptom invites the report to be closed
as unreproducible.

Lead with what is provable without any reference to audibility:

* item 1 is **captured on the wire** (trace below) — the driver writes a clock
  another stream is actively using, after that stream is armed;
* item 3 is **measured** — half the device's isochronous bandwidth spent
  recomputing a number the device already reports;
* items 2 and 4 are **plain correctness**, and item 2 is what Linux does.

These stand as defects whether or not any particular device misbehaves.

---

## 1. The guard does not stop the write it was added to stop

`uaudio_chan_start()` auto-starts the capture stream of an async playback device
for jitter information, and `uaudio_configure_msg()` configures playback first.
So the capture pass issues its `SET_CUR` **after** the playback alternate
setting has armed the device and its isochronous transfers are running.

The guard added in 755685dd cannot prevent it:

```c
if (other != 0 &&
    other != chan_alt->sample_rate) {   /* skip */ }
```

It skips only on a rate **mismatch** — and the rate alignment added by the *same
commit* (`uaudio_chan_match_rate()` in `uaudio_chan_start()`) guarantees a
**match**. The write always falls through.

Captured on an OKTO DAC8 STEREO (`0x152a:0x88c5`, one Clock Source id 41 in both
`bit_output` and `bit_input`), `hw.usb.uaudio.debug=6`, opening at 44.1 kHz:

```
uaudio_chan_set_param_speed: Selecting alt 2          <- capture picks its alt
uaudio_chan_set_param_speed: Selecting alt 7          <- playback picks 44100
uaudio_configure_msg_sub:                             <- PLAY pass
uaudio20_set_speed: ifaceno=0 clockid=41 speed=44100       <- first write
uaudio_configure_msg_sub: clock rate changed to 44100 Hz; settling 100 ms
uaudio_configure_msg_sub: fps=8000 sample_rem=4100    <- play armed, transfers started
uaudio_configure_msg_sub:                             <- CAPTURE pass
uaudio20_set_speed: ifaceno=0 clockid=41 speed=44100       <- second write, same value
uaudio_chan_play_callback: transferring 2816 bytes    <- playback already streaming
```

Two writes, one settle, on every open. Same on 48 k, 96 k, 192 k.

Rewriting a clock is not free: a number of UAC2 implementations reload their
sample-clock PLL on every `SET_CUR` regardless of the value written. Whatever a
given device does with it, writing the clock underneath a running stream is
precisely what the guard exists to prevent.

**Fix:** skip whenever the other direction is streaming at all, not only at a
different rate. The first active stream owns the clock.

*(Not claimed: that this is the cause of the intermittent silence. It is a
defect on its own terms.)*

## 2. `SET_CUR` is issued blind

`uaudio20_set_speed()` sends the request and nothing else. There is no
`GET_CUR`, so the driver never skips a write that would change nothing, and
never learns whether the device took the rate — a completed control transfer
only proves the request was *accepted*.

Linux's `set_sample_rate_v2v3()` reads the rate back first and returns early
when it already matches, with `QUIRK_FLAG_ALWAYS_SET_RATE` for the few devices
that misreport. The patch adds `uaudio20_get_speed()` and does the same, with
`hw.usb.uaudio.clock_readback=0` as the equivalent escape hatch. A write is only
ever skipped on the strength of a *successful* read-back, so devices without
`GET_CUR` behave exactly as before.

This also subsumes item 1 for non-shared clocks (a stream restarting at the rate
it last used).

## 3. The capture stream is auto-started in preference to an explicit feedback endpoint

`uaudio_chan_need_both()` starts the capture stream of *any* async playback
device that also exposes a capture interface, and uses its packet lengths as the
rate-feedback signal — even when the playback alternate setting carries an
explicit feedback endpoint. `uaudio_chan_play_sync_callback()` then discards the
feedback value, because it gates on `sc_rec_chan[i].num_alt == 0`.

The cost is not theoretical. Measured on the OKTO at 44.1 kHz over 20 s:

```
UE_ISOCHRONOUS_OK delta : 5038  -> 251.9/s   (usbconfig dump_stats)
dsp.play interrupts     : 2509  -> 125.4/s   (/dev/sndstat, hw.snd.verbose=2)
                          251.9 = 125.4 play + 125.4 capture + 1 feedback
```

Half the device's isochronous bandwidth, plus a second streaming interface armed
immediately after playback, plus (on a shared clock) the second programming pass
in item 1 — all to recompute a number the feedback endpoint reports once a
second. It is worst on D/A converters that advertise a UAC2 input terminal they
cannot source: their capture interface exists only in the descriptors, and
uaudio(4) streams it for the lifetime of every playback.

**Fix:** record each alternate setting's feedback endpoint while walking the
descriptors — `uaudio_chan_fill_info_sub()` currently skips it with
`/* We ignore sync endpoint information until further. */` — and do not borrow
the capture stream when the playback alt has one.
`hw.usb.uaudio.prefer_feedback=0` restores the old behaviour.

This is the riskiest of the four; it changes rate control for every async device
with both a feedback endpoint and a capture interface. Hence the tunable, and
hence flagging it explicitly for review rather than burying it.

The patch also fixes a pre-existing hole in the same callback: gating on
`cur_alt` rather than `num_alt` means playback no longer free-runs with **no**
rate feedback from either source when the capture stream exists but was never
started or failed to configure.

## 4. A failed rate match leaves a stale capture alternate setting

`uaudio_chan_match_rate()` returning false leaves `ch_rec->set_alt` at its
previous value, and the stream is started anyway — at some unrelated rate, so it
either fights for a shared clock or feeds the playback path jitter computed from
frame sizes that do not belong to it. Run playback alone instead.

---

## Declared, not patched

Please treat these as known and open rather than overlooked:

1. **Ordering.** Stock still does `SET_INTERFACE(alt N)` → `SET_CUR(clock)` →
   start transfers, i.e. it programs the clock under an armed streaming
   interface. Linux parks at alt 0, programs, then selects the alt. I carry a
   local patch for this (`uaudio-clock-before-alt.c.patch`, adds
   `hw.usb.uaudio.clock_settle_ms`) that has never been submitted, because it
   was only ever validated by ear on one device. Happy to submit it separately
   if wanted.
2. **Only the interface being configured is parked**, not every interface on
   that clock. Combined with `usb_proc_msignal()`'s two-entry queue and
   `uaudio_chan_reconfigure()` keeping only the latest `operation` per channel,
   a `STOP` immediately followed by a `START` can be coalesced, leaving the
   other direction's interface armed across a clock write. The real fix is to
   make configuration of both directions one transaction with a generation
   counter — considerably larger than these patches.
3. **Unlocked cross-direction reads.** `uaudio_configure_msg()` drops the
   explore lock for the duration of `uaudio_configure_msg_sub()`, so
   `uaudio_dir_running_rate()` reads the other channel's `running` (written
   under the explore lock) and `cur_alt` (written under `chan->lock`) with
   neither held. Aligned scalar reads, so no tearing, but not a stable snapshot.
   This came in with 755685dd — mine to own.
4. **Incompatible simultaneous rates are not rejected.** The guard declines to
   reprogram, but the second stream is still configured and packetised for the
   rate it asked for: software at rate B, hardware at rate A, silently. Linux
   returns `-EBUSY`.
5. **`bmControls` is not consulted.** Linux checks
   `uac_v2v3_control_is_writeable(bmControls, UAC2_CS_CONTROL_SAM_FREQ)` and
   skips the write for a read-only clock, verifying the current rate instead.

---

## Device generality

Neither patch contains a VID/PID or a quirk entry. They key off descriptor facts
the driver already parses:

| Change | Condition | Inert on |
|---|---|---|
| shared-clock guard | a Clock Source id in **both** direction bitmaps | separate clocks per direction; all UAC1 |
| read-back | UAC2 | UAC1/UAC3; and where `GET_CUR` fails, the write proceeds unchanged |
| stale capture alt | no capture alt matches the playback rate | devices whose capture covers every playback rate |
| prefer feedback | async playback **+** feedback endpoint **+** capture interface | no feedback endpoint, or no capture interface |

The topology behind item 1 — one clock feeding both directions — is common:
XMOS/Thesycon reference designs, most DAC+ADC interfaces, and every DAC shipping
a vestigial capture path.

`bench/uaudio-affects.py` re-implements `uaudio20_mixer_find_clocks_sub()` and
reports per device which paths go live, from a raw descriptor dump, so this is
checkable for hardware neither of us owns:

```sh
sudo usbconfig -d ugenX.Y do_request 0x80 0x06 0x0200 0x0000 513 > dac.hex
python3 bench/uaudio-affects.py --dump dac.hex
```

On my host: OKTO DAC8 STEREO → all paths active; **ESI U24XL → UAC1, every path
inert** (the regression control — the other card sees no change at all).

## Testing

* Both patches build `-Werror` clean, with and without `USB_DEBUG`, standalone
  and stacked.
* Item 1 confirmed on the wire (trace above) on the **unpatched** module.
* Item 3 quantified by counter differential (above).
* Clock read-back verified live: `GET_CUR` on clock 41 returns `0xac44` (44100),
  `CLOCK_VALID` returns 1, `RANGE` returns the 8 supported rates.
* **Not** yet listening-tested with the patches installed, and the intermittent
  silence is **not** reproduced either way. Stated plainly so nobody assumes
  otherwise.
