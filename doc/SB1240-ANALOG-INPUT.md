# Analog (LP) input via a Creative SB1240 X-Fi HD

**Purpose:** run vinyl through the DRC chain for a listening test, reusing
`omdrc-cdin` — the bridge written for the ESI U24 XL S/PDIF interface.

**Short answer:** cdin is not ESI-specific and will work, but selecting the
card in the web configuration page is *not* sufficient. Three settings must be
changed by hand, and one behaviour (the silence gate) does not apply to an
analog source at all — so you must know how to hand the chain back manually.

Signal path for the test:

```
LP ─► Lehmann phono stage ─► SB1240 Line in ─► /dev/dsp.capture
                                                     │  omdrc-cdin
                                                     ▼
                                             ring (lead, ~2 s)
                                                     │
                                                     ▼
                                          /dev/dsp.play (virtual_oss)
                                                     │
                                                     ▼
                                            BruteFIR ─► OKTO DAC8
```

Use the **Line** input, not Phono: the Lehmann already applies RIAA and gain,
and the card's phono path would apply both a second time.

---

## 1. What works with no changes

`cdin/src/main.c` opens whatever `--in` points at. The ESI appears only in
comments and defaults — there is no device-specific code.

The web **Configuration** page writes the card selection and reconciles the
role symlinks, so `/dev/dsp.capture` follows the SB1240 across replugs:

| Step | File |
|---|---|
| page + endpoint | `omdrc-ctrl/src/app.py` |
| writes `omdrc_audio_capture=`, runs `omdrc_audio reconcile` | `scripts/omdrc-config-helper.py` (`freebsd_apply`, ~line 84) |
| resolves the card, creates `/dev/dsp.capture` | `etc/rc.d/omdrc_audio.in` |
| reads `/dev/dsp.capture` by default (`omdrc_cdin_in`) | `cdin/rc.d/omdrc_cdin.in` |

That part is genuinely one click.

---

## 2. Three settings that must be changed by hand

None of these are exposed in the web UI; all are `rc.conf` variables consumed
by `cdin/rc.d/omdrc_cdin.in` and `etc/rc.d/omdrc_audio.in`.

### 2.1 Sample width — `omdrc_cdin_bits`

Default is **24**, and that is an ESI fact, not a neutral default: the U24 XL's
capture endpoint is 24-bit S-LE and nothing else. `omdrc_audio` sets
`bitperfect=1` on the capture card (`omdrc_audio_capture_sysctls`), so there is
**no format feeder to hide a mismatch behind — a wrong width fails the open
outright.**

Find what the SB1240 actually exposes, then set it:

```sh
cat /dev/sndstat
sysctl dev.pcm.<N>                 # <N> = the capture card's pcm unit
sysrc omdrc_cdin_bits=24           # or 16, per the above
```

### 2.2 Sample rate

The rc script does **not** pass `--rate`, so cdin uses its own default of
**44100**. FreeBSD's `uaudio` fixes one `(rate, bits)` pair at attach time, so
the card must be attached at the rate you ask cdin for. To run the test at
another rate, pass it through the escape hatch:

```sh
sysrc omdrc_cdin_flags="--rate 48000"
```

and line up the driver side (`hw.usb.uaudio.default_rate` /
`default_bits`, or the per-unit sysctls) to match.

### 2.3 Recording source — `omdrc_audio_capture_recsrc`

**This one silently captures the wrong jack.** `audio_apply_recsrc()` in
`etc/rc.d/omdrc_audio.in` (~line 450) treats `auto` as "prefer
`pcm2 dig1 dig2 dig3`" — i.e. the *digital* input, which is correct for the
ESI. The SB1240 has an S/PDIF input too, so `auto` will happily select it and
you will record silence while the LP plays.

Pin the analog input explicitly. Find its real mixer name first — only sources
tagged `rec` are selectable:

```sh
mixer -f /dev/mixer.capture          # look for the line tagged "rec"
sysrc omdrc_audio_capture_recsrc=line
service omdrc_audio reconcile
```

---

## 3. The behaviour that does not carry over: the silence gate

`cdin/src/gate.h` releases the output device when the wire has carried
**exact zeros** for `omdrc_cdin_idle_after` ms — "a CD's silence is literally
0x0000, so there is no threshold to tune on the sample side".

An analog ADC's noise floor is never zero. Therefore:

* `GATE_IDLE` is **never** reached;
* `omdrc-cdin` holds `/dev/dsp.play` **indefinitely**;
* MPD and mpv cannot get the chain back until you intervene.

The carrier detector (`cdin/src/carrier.h`) does not rescue this either. It
tests whether frames are arriving at rate; a free-running ADC is always
clocked, so `carrier_live()` is permanently true. That verdict is *correct* —
it is simply measuring something that only varies for a clock-slaved S/PDIF
receiver.

**Consequence:** treat cdin-on-analog as something you start for a listening
session and hand back afterwards, not something you leave enabled the way the
CD input is.

---

## 4. Stopping it / handing the chain back

Three levels, least disruptive first.

### 4.1 Release the output, keep the daemon running

This is the one you normally want — it frees `/dev/dsp.play` for MPD without
restarting anything. It is `SIGHUP`, wrapped as an rc verb
(`omdrc_cdin_release` in `cdin/rc.d/omdrc_cdin.in`; it is also what `drc.sh`
does before stopping virtual_oss):

```sh
service omdrc_cdin release
```

By hand, if you started the daemon yourself rather than via the service:

```sh
kill -HUP "$(cat "${TMPDIR:-/tmp}/omdrc_cdin-$(id -un).pid")"
```

Pidfile locations (set in `cdin/rc.d/omdrc_cdin.in`, ~line 124):

| Started as | `omdrc_cdin_pidfile` |
|---|---|
| root (`service omdrc_cdin start`) | `/var/run/omdrc_cdin/omdrc_cdin.pid` |
| unprivileged (`service omdrc_cdin onestart`) | `${TMPDIR:-/tmp}/omdrc_cdin-$(id -un).pid` |

### 4.2 Stop the daemon

```sh
service omdrc_cdin stop          # or onestop, if you used onestart
```

A foreground run (see §5) stops on `Ctrl-C`: `SIGINT`/`SIGTERM` set
`cdin_stop`, which interrupts the blocking transfers rather than letting them
retry (`cdin/src/cdin.h`).

### 4.3 Turn it off across reboots

```sh
sysrc omdrc_cdin_enable=NO
```

**Do not** reach for stopping `virtual_oss` as a way of freeing the chain: on
this box that intermittently wedges it unkillable and pins `cuse.ko`, needing a
reboot. Release cdin instead.

---

## 5. Suggested first run

Do **not** enable the service for the first attempt. Run it in the foreground
so a failed format negotiation is loud rather than buried in a log:

```sh
omdrc-cdin --in /dev/dsp.capture --out /dev/dsp.play \
           --rate 44100 --bits <what sndstat reports> \
           --lead 2000 -d -s 10 -l /tmp/cdin-lp.log
```

Checks while a side plays:

1. **`starves` must stay 0.** Each one is an audible dropout. The SB1240 is a
   different USB topology from the ESI and may have a different stall floor, so
   do not assume the ESI's calibrated `--lead` transfers — recalibrate per the
   procedure in `cdin/README.md`.
2. **The `drift` field** settles after ~5 minutes and reports measured ppm.
   Two independent crystals (SB1240 ADC vs OKTO) is the same situation as the
   CD case, and the `lead ÷ drift` arithmetic is unchanged: ~11 h at 50 ppm
   against a 2000 ms lead. An LP side is ~25 minutes, so drift cannot cause a
   discontinuity within a side. There is no resampler in the path.
3. **The DAC must be powered on.** The OKTO enumerates and streams with its
   front panel off — a run with the panel dark measures a sleeping converter
   and proves nothing.

Note the ~2 s lead is also the transport lag: dropping the needle is heard
about two seconds later, and lifting it runs on for about two seconds.

---

## 6. Settings summary

```sh
# card selection — or use the web Configuration page
sysrc omdrc_audio_capture="<SB1240 as it appears in /dev/sndstat>"
sysrc omdrc_audio_capture_recsrc=line        # NOT auto: auto picks S/PDIF

# cdin
sysrc omdrc_cdin_bits=<16|24>                # must match the endpoint exactly
sysrc omdrc_cdin_flags="--rate 44100"        # only if not 44100
sysrc omdrc_cdin_lead=2000                   # recalibrate for this card

service omdrc_audio reconcile
service omdrc_cdin start
# ... listen ...
service omdrc_cdin release                   # give /dev/dsp.play back to MPD
service omdrc_cdin stop
```

## 7. Files referenced

| Path | Why it matters here |
|---|---|
| `cdin/rc.d/omdrc_cdin.in` | every `omdrc_cdin_*` variable; the `release` verb; pidfile paths |
| `etc/rc.d/omdrc_audio.in` | card→role resolution, `/dev/dsp.capture`, `audio_apply_recsrc()`, `bitperfect=1` |
| `cdin/src/gate.h` | the exact-zero silence gate that will never fire on analog |
| `cdin/src/carrier.h` | the rate-based carrier detector; always live on a free-running ADC |
| `cdin/src/cdin.h` | `cdin_stop` — how SIGINT/SIGTERM interrupt transfers |
| `cdin/src/main.c` | option parsing, width negotiation, the `[stats]` line |
| `cdin/README.md` | lead calibration procedure |
| `scripts/omdrc-config-helper.py` | what the web Configuration page actually writes |
| `omdrc-ctrl/src/app.py` | the CD input card / log tailing in the panel |
