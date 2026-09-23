# Notes on `linux.vs.freebsd.mdat`

Analysis of the REW project comparing the OKTO DAC8 STEREO's analogue output
under Linux and FreeBSD, per `doc/OKTO-REW-CROSS-OS-PROCEDURE.md`. Captured
2026-09-22, both OSes on the same dual-boot computer, same `120.green.fdw6`
DRC config at 192 kHz, OKTO at −16, captured on a **Creative Sound Blaster
X-Fi HD (SB1240)** line input at 48 kHz/24-bit.

Data was pulled from REW's live API (`localhost:4735`, REW 5.40 Beta 135,
API 0.9.8) with this file open, not by parsing the `.mdat` binary directly.

## Measurements

| # | Title | Date (local) | S/N (dB) | Delay (µs) | Clock adj. (ppm) |
|---|---|---|---|---|---|
| 1 | L LINUX  | 2026-09-22 21:34:58 | 51.79 | −10.11 | −1.8 |
| 2 | L2 LINUX | 2026-09-22 21:40:37 | 51.58 | −10.20 | −1.7 |
| 3 | R LINUX  | 2026-09-22 21:36:55 | 55.14 | −10.44 | −1.8 |
| 4 | R2 LINUX | 2026-09-22 21:39:14 | 55.39 | −10.48 | −1.7 |
| 5 | L FreeBSD  | 2026-09-22 21:56:52 | 51.83 | −10.48 | −1.3 |
| 6 | L2 FreeBSD | 2026-09-22 21:57:57 | 51.83 | −10.50 | −1.3 |
| 7 | R FreeBSD  | 2026-09-22 21:59:13 | 55.37 | −10.89 | −1.2 |
| 8 | R2 FreeBSD | 2026-09-22 21:59:56 | 55.31 | −10.90 | −1.2 |

All eight report `inverted: false` and no calibration/alignment offset
(`alignSPLOffsetdB: 0`), so the raw magnitude/phase arrays are directly
comparable.

## Frequency response, 20 Hz–20 kHz

RMS and peak deviation of the magnitude arrays (656-point float32, linear
frequency spacing, ~0.336 Hz/step):

| Comparison | RMS Δ | Peak Δ |
|---|---|---|
| Linux L repeat (1 vs 2) | 0.012 dB | 0.12 dB @ 16 kHz |
| Linux R repeat (3 vs 4) | 0.008 dB | 0.07 dB @ 20 kHz |
| FreeBSD L repeat (5 vs 6) | 0.012 dB | 0.14 dB @ 16 kHz |
| FreeBSD R repeat (7 vs 8) | 0.008 dB | 0.07 dB @ 20 kHz |
| Cross-OS L (1 vs 5, 2 vs 6) | 0.012 dB | 0.10–0.11 dB @ ~20 kHz |
| Cross-OS R (3 vs 7, 4 vs 8) | 0.008 dB | 0.06–0.08 dB @ ~20 kHz |

Mean offset (overall level) is ≤0.0002 dB in every pair — no gain difference.
Cross-OS deviation is statistically indistinguishable from each OS's own
repeat-to-repeat noise floor: **no systematic level, tilt, or shape
difference attributable to the OS.**

## Phase, 100 Hz–10 kHz

RMS phase disagreement: Linux repeat 0.04–0.08°, FreeBSD repeat 0.04–0.07°,
cross-OS 0.04–0.06°. Cross-OS is *not worse* than within-OS repeatability.
No polarity issue (consistent with `inverted: false` on all eight).

## Timing / impulse response

IR start/peak times and estimated acoustic delay are the same order of
magnitude and internally consistent across all eight runs (~10.1–10.9 µs);
no cross-OS timing anomaly.

## Clock-adjustment ppm — a real, unexplained gap, not an analogue difference

`clockAdjustmentPPM` is REW's estimate of the relative clock-rate mismatch
between the OKTO's playback clock and the Creative's capture clock, derived
from the embedded timing reference over the ~19.3 s sweep. It is a
frequency-domain quantity, unrelated to the fixed start-latency captured in
`delay`/`timingOffset`.

- Linux: −1.7 to −1.8 ppm (repeat spread ~0.1 ppm)
- FreeBSD: −1.2 to −1.3 ppm (repeat spread ~0 ppm)

The ~0.5 ppm OS-to-OS gap is about 5× the within-OS repeat spread, so it
looks systematic rather than noise — but it does **not** show up in level,
phase, or S/N at all, and does not change the frequency-response conclusion
below.

A candidate mechanism exists in `freebsd-uaudio-patch/AUDIT-2026-09-22-LINUX-7.2.3.md`
(same day, same machine): FreeBSD currently batches USB audio feedback
application far coarser than Linux (`buffer_ms=8` ⇒ ~16 ms before a new
feedback value affects a queued transfer, vs. Linux's 1 ms feedback interval
on this device). That is a real, currently-installed driver difference and a
plausible source of a clock-tracking difference — **but the observed
direction is backwards from the naive prediction** (FreeBSD's coarser
feedback batching measured *tighter* to nominal, not looser), and nothing
here confirms the audio box was actually running `buffer_ms=8` at capture
time, or rules out thermal/oscillator drift from the intervening reboot as
the real cause. Unconfirmed; would need a controlled `buffer_ms` sweep on
FreeBSD with repeat REW captures to test causally.

## Conclusion

Per the procedure's stopping condition, both channels agree between Linux
and FreeBSD within their own repeatability. **No measurable analogue-output
difference was found at the Creative SB1240's resolution.**

Caveat: the SB1240 is a budget capture path with its own noise floor,
distortion, and jitter, likely above what's actually audible from the OKTO.
This result means "no difference detectable through this capture chain,"
not "no audible difference exists" — a real difference smaller than, or
masked by, the Creative's own resolution would not appear here.

## Files retained

- `linux.vs.freebsd.mdat` — REW project, all 8 raw measurements, unmodified.
- This file.

Not yet retained/verified: FreeBSD `sysctl hw.usb.uaudio` / `/etc/sysctl.conf`
snapshot from the moment of capture, filter hashes, and OKTO front-panel
settings recorded during the session — needed to confirm the clock-ppm
hypothesis above and to close out the procedure's "files to retain" list.
