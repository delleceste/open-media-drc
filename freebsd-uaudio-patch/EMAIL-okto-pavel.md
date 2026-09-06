# To OKTO RESEARCH — DAC8 STEREO on FreeBSD, and a measurement request

*Draft for Pavel. Context: he has been enthusiastic about the DAC8 working on
FreeBSD, and OKTO has the lab instrumentation we do not.*

---

Hi Pavel,

A status report on the DAC8 STEREO under FreeBSD, and one request at the end
that only you can act on.

**Short version:** the DAC8 STEREO now works properly on FreeBSD, bit-perfect,
all eight rates. Getting there turned up four defects — all of them in
FreeBSD's `uaudio(4)` driver, none in your hardware. One fix is already
committed upstream; three more are in an open pull request. Along the way we
found two things the driver was doing to your DAC that it should not have been,
and we can now switch one of them on and off **at runtime with a single
sysctl, while the DAC keeps playing**. That makes for an unusually clean A/B —
which is where your lab comes in.

---

## 1. Where things stand

The chain is a FreeBSD music system aiming at bit-perfect playback with digital
room correction:

> MPD → brutefir (DRC) → virtual_oss → **DAC8 STEREO** (USB) → Apollon Purifi
> 1ET9040BA → B&W Nautilus 801

Bit-perfectness is verified end to end: the bytes that enter the chain are the
bytes that reach the USB interface. Project and all the material referenced
here: <https://github.com/delleceste/open-media-drc>

## 2. What was wrong — all of it on the FreeBSD side

For the record, since some of this started as "the OKTO goes silent on
FreeBSD": **none of these were faults in the DAC8.** Your device was behaving
correctly and exposing driver bugs that nobody had hit, because almost nobody
pushes FreeBSD's USB audio stack this hard.

1. **The driver programmed the sample clock underneath an already-armed
   streaming interface.** Linux parks the interface at alternate setting 0,
   programs the clock, then selects the alt. FreeBSD did not. On the DAC8 this
   produced the intermittent silent open when switching into the 44.1 kHz
   family — the device latched its stream configuration at `SET_INTERFACE` and
   then had the clock changed under it.

2. **An idle capture stream could reprogram the shared clock.** The DAC8
   exposes a single Clock Source shared by both directions, so a capture pass
   left at a 48 kHz default would overwrite a running 44.1 kHz playback rate.
   The device then consumed samples faster than they arrived, lost sync and
   re-locked — the dropouts and front-panel flicker. *This one is now fixed
   upstream: FreeBSD commit `755685dd665e`, July 2026.*

3. **The clock was written twice on every stream start, the second time after
   playback was armed** — same value, redundant, and precisely the hazard item 1
   was meant to remove. The guard added by the upstream fix only skipped a write
   on a rate *mismatch*, but the same commit aligned the rates, guaranteeing a
   match.

4. **The driver started your capture interface on every playback**, and kept it
   streaming for the whole duration, purely to derive rate-feedback information
   — which your device already reports on its explicit feedback endpoint. More
   on this below; it is the interesting one.

Fixes for 1, 3 and 4 are in an open FreeBSD pull request
([#2390](https://github.com/freebsd/freebsd-src/pull/2390)) and installed here.

## 3. What we measured on the wire

Rather than trust driver log lines, we traced the actual USB control transfers
with DTrace, decoding `bmRequestType` / `bRequest` / `wValue`. Three
open/play/close cycles of **digital silence** — 44.1 kHz entered from a 48 kHz
clock, 44.1 kHz again, then 48 kHz. Silence deliberately, so the result is a
statement about bus traffic and does not depend on anything being audible.

| | before | after |
|---|---|---|
| `SET_CUR` (clock write) per start, same rate | 2 | **0** |
| `SET_CUR` per start, rate change | 2 | **1** |
| `SET_CUR` issued *after* playback armed | 1 | **0** |
| `SET_CUR` during steady-state playback | 0 | 0 |
| capture interface armed | every playback | **never** |

Worth stating plainly, because it retires a worry we had: **the driver never
touches the clock during playback.** Not on a timer, not per transfer. Once a
stream is running, the DAC8's clock is left alone. Whatever else was wrong, it
was not continuous clock disturbance.

Your feedback endpoint reports 44101 and 48001 Hz — live and tracking, about
+23 ppm.

## 4. What we read out of your descriptors

We dumped and decoded the full configuration descriptors. Please correct us if
any of this is wrong — we are inferring your design from the wire.

- **One Clock Source, ID 41** (`bmAttributes = 0x03`: internal programmable,
  not SOF-locked — a proper local async clock), behind **Clock Selector ID 40**
  with a single input pin.
- **Every terminal references clock 40** — USB-in (ID 2), Speaker out (ID 20),
  and both capture-path terminals. So the clock is genuinely shared, in the UAC2
  sense, across both directions.
- **Playback**: interface 1, alts 1–4 — 24-bit in a 4-byte subslot, **native
  16-bit in a 2-byte subslot**, 32-bit, and a RAW/DSD alt
  (`bmFormats = 0x80000000`). Endpoint `0x01` OUT, async isochronous.
- **An explicit feedback endpoint**, `0x81` IN, `bmAttributes = 0x11`
  (isochronous, usage type Feedback), 4 bytes, in *every* playback alt.
- **Capture**: interface 2, alts 1–3, endpoint `0x82` IN — sourced by an
  **INPUT_TERMINAL declared as a Microphone** (`wTerminalType = 0x0201`).

That last one is the curiosity. The DAC8 STEREO has no ADC and no microphone,
so we read interface 2 as vestigial — Thesycon/XMOS boilerplate that the DAC8
carries but cannot source. FreeBSD's driver, however, took it at face value and
streamed it inbound for the entire duration of every playback: roughly **126
isochronous transfers per second from a device with nothing to send**, to
recompute a number your feedback endpoint was already reporting. Linux never
does this, because it uses the feedback endpoint.

Two consequences worth your attention as the designer:

- FreeBSD ignores your **native 16-bit alt** and pads 16→32 instead, because the
  driver fixes one format at attach rather than switching alts per stream.
- FreeBSD skips the **DSD/RAW alt** entirely — no native DSD on this OS.

## 5. Questions only you can answer

These are firmware/hardware questions we have no way to settle from the host:

1. **Does the DAC8 reload or re-lock its sample-clock PLL on a
   `SET_CUR(SAM_FREQ)` carrying a value it is already running?** Our fix removes
   those redundant writes regardless, but whether they ever mattered depends
   entirely on this. Several UAC2 implementations are said to re-lock on any
   write; we do not know if yours does.

2. **What actually happens inside the DAC8 when interface 2 is armed?** Does the
   XMOS core spin up a capture path, source zeros, draw extra current, or run
   activity near the analog section? If arming a phantom input costs nothing
   internally, item 4 above is merely wasteful. If it costs something, it was
   worse than wasteful.

3. **Does Clock Selector 40 switch between two physical crystals** for the 44.1
   and 48 kHz families, and what is the realistic re-lock time? We currently
   allow 100 ms (`hw.usb.uaudio.clock_settle_ms`) before arming the stream. We
   would rather use your number than our guess.

4. **Is the ~1 Hz offset** we read back from the feedback endpoint (44101 /
   48001) the expected behaviour, or an artefact of how we are reading it?

5. **Is the vestigial capture interface intentional** — a firmware option you
   could disable in a future build — or fixed by the Thesycon stack?

## 6. The request: would your lab measure this?

Here is why we are asking you specifically, beyond the fact that you have the
instruments and we do not.

We have verified that FreeBSD delivers the correct bits, and that it does not
disturb the clock during playback. What we **cannot** determine without
instrumentation is whether any of the driver's unnecessary work ever reached the
analog output. And there is a persistent, honest, entirely subjective impression
on our side that the same chain sounds better under Linux than under FreeBSD —
which we would very much like to be wrong about, and which we are not going to
settle by listening.

What makes this worth lab time is that **we can now toggle single variables at
runtime, with nothing else changing** — no reboot, no re-cabling, no different
build, the DAC playing throughout:

```sh
sysctl hw.usb.uaudio.prefer_feedback=0   # phantom capture stream ON  (old behaviour)
sysctl hw.usb.uaudio.prefer_feedback=1   # phantom capture stream OFF (new behaviour)

sysctl hw.usb.uaudio.clock_readback=0    # redundant clock writes ON
sysctl hw.usb.uaudio.clock_readback=1    # redundant clock writes OFF
```

That is a clean experiment: one bit changes, everything else is held constant,
and any difference in the analog output is attributable. It is rare to be able
to hand a measurement lab an A/B this tight.

The measurements we would most value, in priority order:

1. **Output noise floor / FFT under digital silence**, with
   `prefer_feedback` 0 vs 1. Does streaming the phantom capture interface lift
   the noise floor, add spurs, or change anything at all? This is the single
   most informative test, and it isolates host-and-bus effects from programme
   content.
2. **J-test (16-bit)** for jitter sidebands, across: FreeBSD patched, FreeBSD
   unpatched (`clock_readback=0`, `prefer_feedback=0`), and Linux. If the
   redundant clock write or the phantom stream ever perturbed the conversion
   clock, this is where it shows.
3. **THD+N and multitone**, same three legs, level-matched — we expect these to
   be identical, and confirming that is itself a useful result.
4. If you are curious: **current draw or near-field probe on the USB cable**
   with the capture stream on vs off. Purely to see whether 126 extra inbound
   transfers per second are visible at all.

We would of course publish whatever you find, including — especially including —
a null result. "FreeBSD and Linux measure identically on the DAC8" is a genuinely
useful thing for us to be able to say, and good for the DAC8 besides.

## 7. What we can supply

- The exact FreeBSD build and patched driver, plus the sysctls above
- Our DTrace script and the before/after wire traces
- A bench harness (`freebsd-uaudio-patch/bench/`) that does repeated
  open/play/close cycles across all eight rates with a per-cycle verdict from an
  analog capture, and test WAVs with a distinct non-mains-harmonic tone per rate
  — so a wrong clock shows up as a pitch ratio rather than as an opinion
- Any host-side configuration or trace you would like, on request

If it is easier, we can also just ship you a reproducible setup and let your
people drive it.

Thank you for the DAC8 — it has been a genuine pleasure to work on, and it held
up correctly throughout while we found four bugs in someone else's driver with
it. If any of the above is interesting, we would be glad to take it further.

Best regards,
Giacomo
delleceste@gmail.com
