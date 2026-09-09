# Audit: the continuous-feedback patch series (0003-0008)

Date: 2026-09-09.  Scope: `uaudio-upstream-0003..0008.c.patch`, evaluated
against current upstream FreeBSD `sys/dev/sound/usb/uaudio.c` and against
current Linux `sound/usb/{endpoint,pcm}.c` in the UAC2 context.

## Method

The six patches were not read in isolation.  Upstream `main`'s `uaudio.c` was
fetched, the three `upstream-series` patches applied, then the six new patches
applied on top, and the *merged* source was read.  Every claim below about
FreeBSD behaviour was checked in that reconstructed source or in the upstream
headers (`usb.h`, `uaudioreg.h`, `usb_device.c`); every claim about Linux was
checked in the current tree, not from memory.

**All nine patches apply cleanly to today's upstream `main`**, in the order
`upstream-series/0001..0003`, then `uaudio-upstream-0003..0008`.

## Verdict

The series is sound and close to upstreamable.  The Q16.16 port is faithful:
the arithmetic matches Linux term by term, including two things that are easy
to get wrong.  The defects found are in endpoint *discovery*, not in the
scheduler.

## Findings

### 1. `0003` binds endpoint 0 when there is no recognized feedback endpoint

`uaudio.c:1810` in the merged source:

```c
usb_cfg[UAUDIO_NCHANBUFS].endpoint = chan_alt->sync_ep;
```

Stock leaves `UE_ADDR_ANY` there, which binds *any* IN isochronous endpoint in
the alternate setting.  `usbd_get_endpoint()` does

```c
ea_mask |= UE_ADDR;
ea_val  |= (setup->endpoint & UE_ADDR);
```

so `sync_ep == 0` matches nothing, and `no_pipe_ok = 1` turns that into
`xfer[UAUDIO_NCHANBUFS] == NULL`.

That matters because `uaudio_chan_find_sync_ep()` (merged `uaudio.c:2119`)
requires `UE_GET_ISO_USAGE(ed->bmAttributes) == UE_ISO_USAGE_FEEDBACK`.
**Linux deliberately does not check the usage type for explicit feedback.**
`snd_usb_audioformat_set_sync_ep()` takes endpoint index 1 of the alternate
setting, requires only that it be isochronous with `bSynchAddress == 0`, and
consults the usage type solely to flag *implicit* feedback:

```c
if ((sync_attr & USB_ENDPOINT_USAGE_MASK) == USB_ENDPOINT_USAGE_IMPLICIT_FB)
        fmt->implicit_fb = 1;
```

A device that declares its feedback endpoint with usage 0 (common firmware
sloppiness) therefore works on Linux and on stock FreeBSD, and with this series
loses feedback entirely -- not even the legacy once-per-second correction,
because the transfer no longer exists.  The failure is silent.

A second, narrower path to the same outcome: `find_sync_ep()` resumes the
descriptor walk from the CS_ENDPOINT descriptor, so a device that places the
feedback endpoint descriptor *before* the data endpoint's CS_ENDPOINT
descriptor also yields `sync_ep == 0`.

**Fix.**  Keep `UE_ADDR_ANY` when `sync_ep == 0`, and relax `find_sync_ep()` to
Linux's rule -- any IN isochronous endpoint in the alternate setting -- keeping
usage `FEEDBACK` and `bSynchAddress` as *preferences*, which is exactly the
shape `0004` already has.

The same call site sets `sync_ep` for record channels; there the search finds
nothing and both old and new code end at NULL, so no behaviour changes.

### 2. `0005` conflates "no data this interval" with "implausible value"

Merged `uaudio.c:2670,2673`:

```c
if (nframes == 0)
        goto bad_feedback;
len = usbd_xfer_frame_len(xfer, 0);
if (len < 3)
        goto bad_feedback;
```

`bad_feedback` clears `feedback_shift_valid` and, with `0008`, increments
`feedback_bad`.  Linux treats the same condition as a non-event:

```c
if (urb->iso_frame_desc[0].status != 0 ||
    urb->iso_frame_desc[0].actual_length < 3)
        return;
```

Linux resets `freqshift` only when a decoded value falls outside the acceptance
window.  Any device polled faster than it reports will now re-run shift
autodetection on every empty microframe and accumulate meaningless
`feedback_bad` counts.  `feedback_valid` and `feedback_last_sbt` are untouched,
so the staleness fallback still behaves; the damage is churn and useless
diagnostics.

**Fix.**  Split the two paths: return without touching state for a short or
absent packet, reset the shift and count only for an out-of-range value.

### 3. `bRefresh` is not honored

`feedback_interval_ms` derives from the sync transfer's `fps_shift`, i.e. from
`bInterval`.  Linux's `endpoint_set_syncinterval()` prefers `bRefresh` when the
endpoint descriptor is long enough:

```c
if (desc->bLength >= USB_DT_ENDPOINT_AUDIO_SIZE &&
    desc->bRefresh >= 1 && desc->bRefresh <= 9)
        ep->syncinterval = desc->bRefresh;
```

That is how UAC1 full-speed devices declare a slower feedback cadence.
Harmless for UAC2 and for the OKTO; it compounds finding 2 on older devices.

`FREEBSD-LINUX-USB-AUDIO-QUALITY.md` section 4.3 claims `bRefresh` is among
"the parts adopted".  That line overclaims and should be corrected.

### 4. The acceptance window is now the device's authority over drain rate

Worth stating explicitly in the submission rather than leaving for a reviewer
to find.  The old path could bend the rate by `±rate/16000` per second.  The
new one lets any value in

```text
[nominal - 12.5%, min(nominal + 50%, wMaxPacketSize capacity)]
```

set packet sizes directly.  The bounds are Linux's, but Linux also has
`synced_next_packet_size()`'s `avail` / `-EAGAIN` check and ALSA XRUN
reporting.  FreeBSD's play callback copies `total` bytes from the ring
unconditionally, so a starved ring silently replays stale bytes.

This is not a regression and is not reachable on a well-behaved DAC, but it is
the one place where porting Linux's math does not carry Linux's safety net.
`feedback_q16_min` / `feedback_q16_max` already provide the visibility needed to
add a tighter secondary band later if it proves necessary.

### 5. `0006` starts the sync transfer on record channels too

`uaudio_chan_configure()` is shared between directions, and
`uaudio_chan_record_sync_callback()` is an empty `/* TODO */`.  The transfer is
started and never submits: inert, but pointless.  Gate the start on the play
direction before submitting upstream.

## Checked and correct

These were verified rather than assumed, and should not need re-litigating.

- **`base_fps = frames_per_second << fps_shift`** recovers 8000 (HS) / 1000
  (FS).  This is the subtle one.  It is right because stock down-shifts `fps`
  at configure time (`uaudio.c:1577`, `fps >>= fps_shift`), so
  `frames_per_second` is the *packet* rate and the shift restores the base
  (micro)frame rate.  The result matches Linux's `freqn` units exactly:
  `((rate << 10) + 62) / 125` = rate/8000 in Q16.16 for high speed,
  `<< 13` for full speed.  Round-to-nearest included.
- **`capacity_q16`** is Linux's
  `(data_maxsize / (frame_bits >> 3)) << (16 - datainterval)`, expressed as a
  left shift by 16 followed by a right shift by `fps_shift`.  Equivalent.
- **Acceptance window** `-12.5% / +50%`, capped by packet capacity, and the
  reset-on-out-of-range rule: identical to Linux.
- **Shift autodetection** matches Linux, and the `±16` iteration bounds are a
  defensible improvement over Linux's unbounded loops -- a garbage value now
  exits the loop and is rejected by the range check instead of spinning.
- **Phase accumulator** is `synced_next_packet_size()` line for line, including
  storing the uncapped phase and truncating it to 32 bits.  The truncation is
  harmless because only the low 16 bits are reused on the next packet.
- **`feedback_raw` is not a stack disclosure.**  Stock `memset`s `buf` before
  the partial `usbd_copy_out()`, so the padding bytes read back as zero.
- **`cur_alt >= num_alt`** is a valid "not streaming" test: `CHAN_MAX_ALT` is
  the sentinel (`uaudio.c:1447`, `2135`) and `num_alt <= CHAN_MAX_ALT`.  The
  claim in `0002`'s commit message holds.
- **Locking.**  All three transfers are set up with `&chan->lock`, so
  `feedback_q16`, `feedback_phase`, `feedback_valid`, `feedback_last_sbt` and
  the diag counters stay consistent between the sync and play callbacks.  The
  diagnostics sysctl takes the same mutex, which `uaudio_chan_init()`
  initializes via `pcm_addchan()` *before* the OID is created, and both are
  gated on `num_alt > 0`.  No window.
- **`getsbinuptime()`** is tick-coarse (≈1 ms at hz=1000) against a 250 ms
  threshold -- correct, and the right choice over `sbinuptime()` inside a
  transfer callback.
- **Stop/teardown.**  `usbd_transfer_unsetup()` drains callbacks before
  `cur_alt = CHAN_MAX_ALT` is stored, so keeping the sync transfer permanently
  in flight does not create an out-of-bounds `usb_alt[cur_alt]` read.
- **Hot switching** works in both directions.  Mode 0 leaves the sync transfer
  one-shot as before; a mid-stream `prefer_feedback=0` degrades to nominal
  free-run rather than misbehaving, matching the documented "reopen required".

## Linux parity summary

| Aspect | Linux | This series |
|---|---|---|
| Feedback units | Q16.16 samples per base (micro)frame | same |
| Q10.14 / Q16.16 autodetect | shift until within `-25% / +50%` | same, bounded to ±16 |
| Acceptance window | `-12.5%` to `min(+50%, capacity)` | same |
| Packet scheduler | `phase += freqm << datainterval` | same, `fps_shift` for `datainterval` |
| Short/absent feedback packet | return, no state change | resets autodetect (finding 2) |
| Sync endpoint discovery | any iso EP at index 1, `bSynchAddress` validated | requires usage `FEEDBACK` (finding 1) |
| Sync interval | `bRefresh`, else `bInterval` | `bInterval` only (finding 3) |
| Underrun handling | `avail` / `-EAGAIN`, XRUN to userspace | none (finding 4) |
| Device quirk table | large (`tenor_fb_quirk`, implicit FB, ...) | none, by design |
| Implicit feedback | dedicated machinery | borrowed-capture model only |

## Documentation

`uaudio-feedback-diagnostics.md` says the series applies "after the two
existing upstream-style clock patches".  It is three: `0006` fails to apply
without `upstream-series/0003` (clock-before-alt).  Confirmed in both
directions by applying the stack with and without it.

## Recommended order of work

1. Finding 1 -- silent loss of feedback on sloppy descriptors, and the only
   finding that is a regression against both stock FreeBSD and Linux.
2. Finding 2 -- small, local, and makes the new counters trustworthy.
3. Finding 5 -- one-line gate, removes an obvious reviewer question.
4. Findings 3 and 4 -- state them in the submission as known gaps rather than
   fixing them now; both are outside what the OKTO exercises.
