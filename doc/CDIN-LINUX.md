# The CD / S-PDIF input on Linux

The FreeBSD input is `omdrc-cdin` (see `cdin/README.md`), an OSS daemon written
because FreeBSD ships nothing that reconciles two free-running audio clocks.
Linux does ship it: `alsaloop(1)` from alsa-utils *is* that control loop. So
the Linux input is not a port of the daemon. It is:

* `scripts/omdrc-cdin-alsaloop` — the supervisor around alsaloop,
* `etc/systemd/user/omdrc-cdin.service.in` — the unit that runs it,
* `etc/modprobe.d/omdrc-snd-aloop.conf` — the loopback's clock,
* the capture half of the `/configuration` Audio hardware page, and
* the source arbitration in `drc.sh`.

The web panel's CD input card is unchanged: it is a parser for the daemon's log
grammar, and the supervisor emits the same grammar, so one card serves both
operating systems.

## The topology, and the clock that is easy to forget

```
CD player ──S/PDIF 44.1k──► ESI U24 XL ──USB──► hw:<cap>,0
                                                     │  alsaloop
                                                     ▼
                                            hw:Loopback,0,0   (snd-aloop)
                                                     │
                                                     ▼
                                            hw:Loopback,0,1
                                                     │  BruteFIR
                                                     ▼
                                                  hw:0,0      (Okto DAC8)
```

On FreeBSD there is exactly **one** pair of independent clocks: the CD's
crystal and the DAC's. `virtual_oss` sits between them and is itself clocked by
the DAC it feeds, so it contributes no third clock.

`snd-aloop` has no clock of its own, and by default it invents one — an
hrtimer. That makes **two** independent pairs on Linux (CD ↔ aloop-timer, and
aloop-timer ↔ DAC), and the second one is present even with no CD input in the
chain, in plain MPD playback. It is normally invisible because both BruteFIR
stanzas in `brutefir_defaults.linux.conf` set `ignore_xrun: true`, so it
presents as an occasional discontinuity rather than as an error.

`etc/modprobe.d/omdrc-snd-aloop.conf` fixes this with the module's
`timer_source` parameter, pointed at the DAC card:

```
options snd-aloop index=1 id=Loopback pcm_substreams=2 timer_source="hw:0,0,0"
```

The loopback then advances at the DAC's rate, the topology is the same shape as
the FreeBSD one, and the CD ↔ DAC drift is the only drift left. That is a
prerequisite for the correction below, not an optimisation: correcting one
drift pair while a second runs free would only move the problem.

`omdrc-config-helper` rewrites the marked line to whichever DAC is selected on
the `/configuration` page. The module is loaded at boot, so a change takes
effect on the next boot.

## Drift correction: `--sync=playshift`, and why not a resampler

Capture is clocked by the CD, playback by the DAC, and the two differ by a few
ppm forever. alsaloop offers several corrections; the one used here is
`playshift`, which steers snd-aloop's `PCM Rate Shift 100000` control so the
loopback consumes at exactly the rate the CD delivers.

**There is no resampler in the data path.** What reaches BruteFIR is
bit-identical to what the transport sent, which is the property the FreeBSD
design exists to protect and which `scripts/verify-bitperfect.sh` would
otherwise be measuring nothing.

The alternatives, and when to reach for them (`OMDRC_CDIN_SYNC`, or `--sync`):

| mode | correction | bit-perfect | use when |
|---|---|---|---|
| `playshift` | steers the loopback's rate-shift control | yes | default |
| `samplerate` | continuous libsamplerate resample | **no** | the shift control misbehaves |
| `simple` | inserts/drops samples | between corrections | diagnosis |
| `none` | nothing | yes | measuring the raw drift |

`captshift` is listed by alsaloop but is not useful here: it needs the rate-shift
control on the **capture** card, and a USB interface does not have one.

The supervisor reads the shift control back every stats interval and reports it
as the `drift` field, in ppm. That is a direct measurement of the correction
being applied, which is a better signal than inferring drift from buffer level:
it is what the loop decided, not what we can guess about it.

## The CD input is an exclusive source

Both platforms expose the same source-selection behavior, for different
low-level reasons.

`virtual_oss` mixes, so FreeBSD cannot rely on a second open returning `EBUSY`.
The CDIN card therefore gates MPD's audible outputs around the bridge service:
START disables them and STOP restores the exact previously enabled output.

`hw:Loopback,0,0` is a **single substream**. alsaloop and MPD's `DRC-native` /
`DRC-resamp` outputs cannot both hold it; whichever opens second gets `EBUSY`.
There is one seat, so:

* `drc.sh cdin` and `drc.sh linein` disable every MPD output, bring the chain
  up at that source's rate, and start `omdrc-cdin.service`. MPD has no output
  while a capture source is selected, and that is the correct state, not a
  limitation worked around — the direct `OKTO-DAC` output is no help either,
  because BruteFIR holds the DAC.
* Any rate action (`drc.sh 44100`, `192000`, `resamp`, …) records `music` as
  the source, stops the bridge, waits for `alsaloop` to actually be gone, and
  then enables the MPD output. Waiting for the *process* rather than the unit
  matters: `systemctl stop` returns before the kernel has closed the substream,
  and reopening it too early is an `EBUSY` that reaches the user as "MPD will
  not play".
* The unit is therefore **not** enabled at boot. `drc.sh restore` /
  `reconcile` bring back whichever source was saved.
* `drc.sh off` / `stop` return the box to MPD direct and leave the bridge down.
  FreeBSD moves its bridge to the DAC instead — it cannot here, because the off
  path hands that same DAC to MPD's direct output and the DAC is single-open.
  Re-select the input with `drc.sh cdin` / `drc.sh linein`.

The one thing that does carry over: when the DRC chain is down, the bridge
writes straight to the DAC instead of the loopback, exactly as
`cdin/src/outsel.h` describes. The output is settled once at startup — alsaloop
negotiates a format against it and cannot re-take the decision while it runs —
so the service is restarted whenever the chain moves.

## Selecting the capture interface

The `/configuration` page's Audio hardware section now applies on Linux for
both roles. Applying writes:

* `<prefix>/etc/open-media-drc/audio-roles.conf` — `OMDRC_AUDIO_DAC` and
  `OMDRC_AUDIO_CAPTURE`, the USB identities, which survive a reboot;
* `/run/omdrc/audio.roles` — the resolved ALSA card numbers, which do not.

`omdrc-config-helper reconcile` republishes the second from the first at boot
(`etc/systemd/system/omdrc-audio-roles.service.in`). A configured capture card
that is not plugged in is dropped with a notice rather than failing the whole
reconcile: the box still plays music, it just has no CD input this boot.

The bridge and the panel's chain diagram both read the capture role from
`/run/omdrc/audio.roles`, so nothing has to hard-code a card number that USB
attach order can change.

## Two capture sources, one bridge

A capture card is usually several inputs, and they are not interchangeable:
they run at different rates and, on some cards, sit on different PCM devices.
So there are two capture sources rather than one, and the difference between
them is three values:

| | `cdin` | `linein` |
|---|---|---|
| `*_RATE` | 44100 | 96000 |
| `*_CAPTURE_SOURCE` | `auto` (the card's digital item) | `Line` |
| `*_CAPTURE_DEVICE` | 0 | 0 |

All six are `omdrc.conf` settings. The rate is not a preference: a CD is
44.1 kHz because Red Book says so, and an analog input is whatever its
converter runs at. The USB Sound Blaster HD's analog device offers 48 and
96 kHz only — asking it for 44.1 does not fail, it resamples through ALSA's
plug layer, and nothing reports it.

Everything downstream follows from the saved source. `drc.sh restore` and
`reconcile` bring the chain back at that source's rate; `drc.sh geometry`
refuses a filter set that has no config for it; the panel's spectrum analyzer
FFTs at the rate the bridge logged rather than at a configured constant, since
reading 96 kHz as 44.1 kHz mislabels every bin without failing at anything.

### How the choice reaches the bridge

The bridge is a `--user` unit started by name, so the input cannot be an
argument. `drc.sh` writes it to `$STATE_DIR/cdin.env` immediately before the
start, and the unit reads it:

```ini
EnvironmentFile=-/home/<user>/.local/state/omdrc/cdin.env
```

```sh
OMDRC_CDIN_RATE=96000
OMDRC_CDIN_CAPTURE_DEVICE=0
OMDRC_CDIN_CAPTURE_SOURCE=Line
```

Writing it as part of the start, rather than once at install time, is what
keeps the two halves together: a stale file is the failure where the chain is
built for one source and the bridge captures the other — the right rate on the
wrong input, with no error anywhere.

`OMDRC_CDIN_SOURCE` is the name the run is known by, and the bridge logs it:

```
[INF] omdrc-cdin 0.1 starting: in=hw:3,0 out=hw:Loopback,0,0 96000 Hz
[INF] source linein: capturing 'Line' from hw:3,0
```

A line of its own, because the start line's grammar is a contract the panel
already parses, and because a bridge started by hand has no selected source to
declare. This is where the panel gets the input it names on the DRC card's
**Active** line — the devices alone cannot say it, since `hw:3,0` is the analog
side of a card whose digital side is a second PCM device here and an item of
the same selector on the next box. It is reported only while the bridge is
actually running: the chain can be up at the Line input's rate with the bridge
dead, and *Active* is about what is running.

On FreeBSD only the rate carries over. `omdrc-cdin` is a daemon configured from
`rc.conf`, so which input of the card it opens is an `omdrc_cdin_*` setting
there; `linein` means "run the chain at the analog input's rate", and the
daemon has to be pointed at that input to match.

### The input selector on the card

`cdin/ESI-U24XL.md` documents the FreeBSD half of this: the card has two inputs,
only one is live, the choice is a mixer setting, and it does **not** survive a
reboot — a fresh boot comes up on the analog RCA input and records silence from
the transport.

The bridge asserts the selector itself, on every `alsaloop` start rather than
once, because it is card state: a reboot, an `alsactl restore` or another
program can move it under a running bridge, and the symptom is silence with a
log reporting perfect health. `OMDRC_CDIN_CAPTURE_SOURCE` says what to select —
`auto` matches the control's digital item (`iec958|s/pdif|digital|optical|coax`),
`keep` leaves the card alone, anything else is an item name used verbatim. The
control is matched by shape (`(capture|input) source`), because it is
`PCM Capture Source` on one card and `Input Source` on the next; a card with no
such control is unaffected either way.

To find the item names for a card:

```sh
amixer -c <card> contents | grep -i -A5 'Capture Source'
```

Two shapes to expect. The ESI U24 XL puts both inputs behind the selector
(`Line`, `IEC958 In`), so `auto` reaches the digital one. The USB Sound Blaster
HD does not: its selector is `Mic` / `Line` / `Phonograph`, all analog, and the
S/PDIF input is a **separate PCM device** — `hw:<card>,1` at 44.1 kHz, where
`hw:<card>,0` is the analog side at 48/96 kHz. On that card `auto` matches
nothing and correctly leaves the selector alone; the digital input is reached
with `CDIN_CAPTURE_DEVICE=1` instead.

## Requirements

* `alsa-utils` (for `alsaloop` and `amixer`)
* `snd-aloop` with `timer_source` support (mainline since 4.x)
* a capture interface that takes its clock from the incoming S/PDIF carrier —
  the ESI U24 XL does, automatically and with no switch (ESI KB00307EN)

## What has not been measured

Everything above is built and reasoned; none of it has run on the Linux box
yet, and two claims are worth confirming before trusting them:

1. `timer_source="hw:0,0,0"` is accepted in that spelling. Check with
   `cat /sys/module/snd_aloop/parameters/timer_source` after a reboot, and for
   the effect, that the loopback's `hw_ptr` advances at the DAC's rate.
2. `--sync=playshift` finds the shift control. The supervisor logs a warning
   naming the control if it does not, and `--sync=samplerate` is the documented
   fallback.

Calibrate `--tlatency` the way `cdin/README.md` calibrates `--lead`: it is the
drift margin, the startup delay and the transport lag all at once, so step it
down until the first underrun and go back up one step.
