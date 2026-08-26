# DAC lock bench

Repeated open / play / close cycles across sample rates, with a per-cycle
verdict on whether the OKTO **actually produced sound** — the measurement the
`uaudio(4)` follow-up patches need and that no host-side sysctl can give.

## Why an analog capture

Every machine-side signal is blind to this failure. `feedback_rate`, clock
validity, channel flags, underrun counters and the USB stream itself all look
perfectly healthy while the DAC routes silence — that is the whole character of
the bug, and it is why `DAC_PRIME_CYCLES` could never be auto-verified.

`usbdump` does not help either: this kernel is `GENERIC`, which has **no
`options USBPF`**, so `usbdump` captures nothing (verified — zero packets even
unfiltered). And even with it, USB sniffing would only show the host sending
the same bytes it always sends. The failure is inside the DAC.

So ground truth comes from outside the computer: record the DAC's analog
output and measure it.

## What the tones buy you

Each rate gets its own non-harmonic-of-mains tone, so a single recording answers
three questions at once:

| Question | How |
|---|---|
| Is the DAC routing audio at all? | tone present above the noise floor |
| Which file is playing? | which tone frequency |
| **Is the DAC on the right clock?** | `measured_hz / expected_hz` |

That third one is the good part. A DAC left on the 48 kHz crystal while fed
44.1 kHz data reproduces its tone **8.84 % sharp** — `ratio 1.0884`,
unmistakable, and exactly the original flicker failure mode. The bench reports
it as `WRONG-RATE` rather than lumping it in with silence.

| rate | tone | | rate | tone |
|---|---|---|---|---|
| 44100 | 997 Hz | | 176400 | 2971 Hz |
| 48000 | 1319 Hz | | 192000 | 3821 Hz |
| 88200 | 1741 Hz | | 352800 | 4877 Hz |
| 96000 | 2273 Hz | | 384000 | 6113 Hz |

## Files

| File | Purpose |
|---|---|
| `make-test-wavs.py` | Generate the WAV set (32-bit stereo, matching uaudio's fixed format). |
| `dac-bench.py` | The bench: `run`, `play`, `listen`. |
| `ossio.py` | Minimal `/dev/dspN` helper (raw OSS ioctls; `ossaudiodev` is deprecated and cannot express S24_LE). |
| `selftest.py` | Offline analyser self-test — no hardware. **Run this first.** |
| `uaudio-affects.py` | Decides *from descriptors alone* whether any device is affected by the patches. |
| `wavs/` | Generated tones + `manifest.json`. |

## Wiring (for the automatic verdict)

```
  OKTO DAC8 STEREO  RCA out  ────────►  ESI U24XL  analog line in   (/dev/dsp0)
```

The tones are generated at **−20 dBFS** precisely so this is safe: the OKTO
puts out ~2.1 Vrms at 0 dBFS on RCA, so −20 dBFS is ~0.21 Vrms — comfortable
line level, no risk of pinning the ESI's input.

You will need to unplug whatever currently feeds the ESI (the CD input) and
stop `omdrc-cdin`, since the bench needs the capture device exclusively.

**No cable?** Use `--monitor ask` and be the ear. Everything else still works.

## Running it

```sh
cd freebsd-uaudio-patch/bench
python3 selftest.py                     # 7 synthetic cases, must ALL PASS
python3 make-test-wavs.py               # already generated; re-run to change level/length

# the chain must not hold the hardware device
drc.sh off          # or: stop musicpd / virtual_oss / brutefir

# calibrate the input level first (nothing playing → should read SILENT)
python3 dac-bench.py listen --seconds 3

# one file, by ear
python3 dac-bench.py play 44100

# the bench
python3 dac-bench.py run --monitor capture --sequence into --cycles 20
python3 dac-bench.py run --monitor ask     --sequence into --cycles 3
```

`--sequence into` builds `X → 44100` for every other rate X, which is the
reported failure. `all` does every ordered pair, `sweep` walks up and back,
`random` shuffles, or pass an explicit list like `--sequence 192000,44100`.

The bench refuses to start if something holds the device, and tells you what.

## The three-leg A/B

This is what decides whether the duplicate `SET_CUR` is the trigger.
Use `DAC_PRIME_CYCLES=0` throughout so `drc.sh` cannot mask a failure.

| Leg | Module | sysctls | Isolates |
|---|---|---|---|
| A | current | — | baseline failure rate |
| B | patched | `prefer_feedback=0` | duplicate `SET_CUR` gone, capture stream still running |
| C | patched | `prefer_feedback=1` (default) | capture stream gone as well |

```sh
sysctl hw.usb.uaudio.prefer_feedback=0     # leg B
python3 dac-bench.py run --monitor capture --sequence into --cycles 20 --csv legB.csv
```

* **B fixes it** → the redundant write was the trigger; patch 0001 alone suffices.
* **B doesn't, C does** → the capture `SET_INTERFACE` / second stream was; both needed.
* **Neither** → the mechanism is elsewhere; use `--trace`.

## Catching the duplicate write in the act

`--trace` sets `hw.usb.uaudio.debug=6`, mutes the console with `conscontrol`
(so the trace does not turn into console I/O in the middle of the audio path),
and records the `SET_CUR` sequence for each cycle:

```sh
python3 dac-bench.py run --monitor capture --sequence into --cycles 10 --trace
```

The CSV then carries `setcur` (e.g. `41:44100;41:44100` — the duplicate) and
`dup_setcur`, and the summary cross-tabulates it against the verdict. **This
works on the current, unpatched module**, so it can confirm or kill the
hypothesis before anything is installed.

Caveat: tracing perturbs timing. Do not compare failure *rates* between traced
and untraced runs — use tracing to characterise, not to count.

## Output

A CSV per run plus a summary: verdict counts, failures broken down by
transition, and the duplicate-`SET_CUR`-vs-verdict table. Columns include
`peak_hz`, `ratio`, `snr_db`, `level_dbfs`, `onset_ms` (how late the DAC
started, if it did), `clock_before`/`clock_after` (GET_CUR read straight off
the device), `feedback_before`/`feedback_after`, and `open_s`.

## Is my device the only one affected?

```sh
sudo python3 uaudio-affects.py --probe          # every USB audio device here
python3 uaudio-affects.py --dump someone-elses-dac.bin
```

It re-implements the driver's own clock discovery and reports, per device,
which patch paths go live and why. Neither patch is device-gated — they key off
descriptor facts, never VID/PID — so this answers the question for hardware you
do not own, from a dump someone mails you:

```sh
# how a DAC8 owner on another machine produces a dump for you
sudo usbconfig -d ugenX.Y do_request 0x80 0x06 0x0200 0x0000 513 > dac.hex
```

On this host it currently reports the OKTO as `EXPOSED` on all three counts and
the ESI U24XL as **UAC1 — every patch path inert**, which is the regression
control: the other sound card on this machine sees no behaviour change at all.
