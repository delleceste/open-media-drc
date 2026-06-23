# Detecting and characterizing audio glitches in the DRC chain

This document describes the glitch-detection subsystem: how to turn it on, what
each layer measures, and how to read the result — in particular how to tell a
**periodic** glitch (a buffer or clock cycle) from a **random** one (CPU /
scheduling contention) or a **bursty** one (something waking up).

It is implemented for **FreeBSD** (the NUC). On Linux the same scripts run but
the FreeBSD-specific sources (`dev.pcm` counters, the `uaudio` USB tap) are
simply quiet — no glitches have ever been observed on the Linux path.

---

## The one switch

Everything is driven by a single global switch, `glitch-debug.sh`, exposed as a
**Debug card at the bottom of the omdrc-ctrl web UI**. Turning it **On** drops a
state file (`.glitch-debug.enabled`) and launches the lightweight monitor
daemon; **Off** removes the state file (the daemon self-terminates) and kills it.

```
./glitch-debug.sh on        # start monitoring
./glitch-debug.sh status     # state, daemon, event count
./glitch-debug.sh analyze    # periodicity report (see below)
./glitch-debug.sh usbtap 30  # one 30 s last-hop USB capture (needs sudo)
./glitch-debug.sh tail 40    # raw events
./glitch-debug.sh clear      # truncate glitch.log
./glitch-debug.sh off        # stop
```

All events land in `glitch.log` (one line each), gitignored:

```
2026-06-23T12:34:56 epoch=1750000000 stage=brutefir kind=warn msg=xrun...
```

`epoch` is what the analyzer uses; `stage` is the source; `kind` is the class.

---

## The layers, and what each one catches

```
MPD ──► virtual_oss ──► brutefir ──► OKTO (USB isoc OUT 0x01)
 (mpd)     (pcm)        (brutefir)        (usb)
```

### `glitch-monitor.sh` — always-on, lightweight (the `on` switch starts it)

Polls every second (`GLITCH_INTERVAL`) and logs every *new* anomaly from four
independent, best-effort sources:

| stage      | source                              | catches |
|------------|-------------------------------------|---------|
| `brutefir` | new warning lines on `/tmp/brutefir.out` (brutefir's whole-life stdout, see `drc.sh`) | the DSP engine missing its real-time deadline (under/overflow, skip, clip) |
| `dmesg`    | new `uaudio`/USB error lines in the kernel ring | USB transport errors, stalls, timeouts |
| `mpd`      | new error/underrun lines in the MPD log | decoder stalls, the Qobuz/curl CPU spin (see `MPD-CURL-CPU-SPIN-FreeBSD.md`) |
| `pcm`      | any `dev.pcm.*` counter whose name carries `under/over/err/xrun` that increased | kernel/OSS-side over- and under-runs (name-agnostic; quiet if no such counters exist) |

Resolution is the poll interval — fine for periods of **seconds to minutes**.
For sub-second / sample-accurate timing, use the USB tap.

### `glitch-usbtap.sh` — definitive, last-hop, heavier (the `USB tap` button)

The gold standard: it taps the OKTO's **isochronous OUT endpoint 0x01** with
`usbdump` (same endpoint as `scripts/verify-bitperfect.sh`), downstream of every
software stage, so it sees a starvation glitch no matter what caused it.

Detection is **header-only**, so it scales to multi-minute captures. `usbdump -r`
(no `-v`) prints one line per transfer with a microsecond timestamp and the
submitted length, e.g.

```
10:43:05.646324 usbus0.4 SUBM-ISOC-EP=00000001,SPD=HIGH,NFR=32,SLEN=6144,IVAL=0
```

At 192 kHz these arrive every **~4 ms** with **SLEN=6144** (32 frames x 192 B).
It flags, each with its own timestamp:

* **timing gaps** — a gap between transfers `> GLITCH_GAP_FACTOR x` the nominal
  interval (default 2.5x, i.e. >10 ms): the host stalled. The cleanest, least
  ambiguous glitch signal.
* **short frames** — SLEN below `GLITCH_SHORT_FRAC x` nominal (default 0.5): a
  genuinely half-empty/zero block.

> **Why not average throughput?** A single brief dropout in a multi-minute window
> averages away to nothing, so a whole-window B/s metric (the first design)
> *masks* occasional glitches. Per-transfer gap/short detection catches each one.

> **Ignore the feedback wobble.** Asynchronous USB constantly shaves +/- a few
> samples per packet (SLEN 6096/6104/6192...) to track the DAC clock
> (`feedback_rate` ~ 191994 vs 192000). That is **normal**, counted separately as
> "feedback adj", and never flagged — only `SHORT_FRAC`-scale drops count.

> **Blind spot.** A "silence-insertion" underrun (host submits a full-length block
> of zeros) keeps timing *and* SLEN nominal, so header-only analysis can't see it.
> That needs payload inspection, which doesn't scale to minutes — run a short
> capture and `scripts/verify-bitperfect.sh` if you suspect that mode.

Notes: needs root (`sudo usbdump`, same sudoers the UI uses for
`service`/`reboot`); writes the pcap to `/var/tmp` (`GLITCH_TMP`) since a 5-minute
capture is ~525 MB; the **capture** is I/O-bound (low CPU) — the CPU cost is the
**decode** pass after the window closes. The web Debug card offers 60 s / 3 / 5 /
10 min; the CLI takes any duration (`glitch-debug.sh usbtap 300`).

---

## Reading the verdict: periodic vs random

`glitch-analyze.py` (`glitch-debug.sh analyze`, or the **Analyze** button) takes
the inter-event intervals and reports, per stage, the **coefficient of
variation** CV = stddev/mean:

| CV        | verdict                | typical cause |
|-----------|------------------------|---------------|
| ≈ 0       | **PERIODIC** — the mean interval *is* the period | a fixed buffer/clock cycle; e.g. ~250 ms ↔ the `virtual_oss` buffer |
| < 0.5     | semi-regular           | a loose cycle |
| ≈ 1       | **RANDOM / Poisson**   | CPU contention, scheduling, thermal |
| > 1.5     | **BURSTY / clustered** | a process waking (cron, indexer, network rebuffer) |

It also runs a small **autocorrelation** to surface a dominant period buried in
noise, and — when `drc.log` is present — reports how many glitches landed next to
a DRC rate switch. A high correlation there points at the known **crystal-switch
family** (`okto-44k1-coldopen-silence`), not a steady-state fault.

Example:

```
interval analysis (CV = stddev/mean):
  brutefir  n=29  mean= 10.0s cv=0.00  [10.0s..10.0s]  → PERIODIC (mean is the period)
            ↳ autocorrelation hints a ~10.0s period (score 0.97)
  dmesg     n=29  mean= 12.6s cv=0.98  [0.0s..43.0s]   → RANDOM / Poisson
```

---

## Files

| file | role |
|------|------|
| `glitch-debug.sh`   | the global switch / orchestrator |
| `glitch-monitor.sh` | the always-on poller daemon |
| `glitch-usbtap.sh`  | the bounded last-hop USB capture |
| `glitch-analyze.py` | the periodicity classifier |
| `glitch.log`        | the unified event log (gitignored) |
| `.glitch-debug.enabled` | the state file = the switch (gitignored) |

The web UI (`omdrc-ctrl`) exposes all of this through `/debug/glitch*` routes and
the Debug card.
