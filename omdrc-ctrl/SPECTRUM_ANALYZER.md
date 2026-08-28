# Live Spectrum Analyzer

`omdrc-ctrl` can show a live two-channel FFT spectrum from the audio currently
played by MPD.  The implementation deliberately avoids USB packet sniffing and
does not insert anything into the BruteFIR/DAC playback path.

## Signal Source

The analyzer is, at bottom, a FIFO reader: it opens a FIFO non-blocking, FFTs
raw S32_LE stereo, and knows nothing about who wrote it.  That is what lets
more than one part of the chain feed it.  `source` in `commands.conf` picks the
producer:

| `source` | Producer | Rate |
|---|---|---|
| `mpd` | MPD's secondary `fifo` output | `sample_rate` |
| `cdin` | the CD / S-PDIF bridge | `cdin_sample_rate` |
| `auto` (default) | `cdin` while a disc is playing through the bridge, else `mpd` | follows the choice |

The rate travels **with** the source rather than being one global, because the
two differ — CD is 44.1 kHz where the MPD FIFO is configured at 48 kHz — and
analysing at the wrong rate mislabels every frequency bin while failing in no
visible way: a 1 kHz tone read at the wrong rate lands at 1088 Hz and nothing
anywhere reports an error.

### The MPD source

The analyzer uses a secondary MPD `fifo` output:

```conf
audio_output {
        type "fifo"
        name "OMDRC Spectrum"
        path "/tmp/omdrc-spectrum.fifo"
        format "48000:32:2"
        enabled "no"
}
```

MPD writes a raw stereo S32_LE copy of the stream to the FIFO.  `omdrc-ctrl`
reads that copy, computes an FFT, and streams compact display bins to the web
page.

The FIFO is raw PCM, so it has no header carrying rate/format metadata.
`sample_rate` in `commands.conf` tells the analyzer how to interpret the byte
stream and how to compute frequency-bin labels.  It must match the first field
of MPD's FIFO `format`.

This is a pre-DRC MPD tap.  It shows the music stream feeding MPD, not the
post-BruteFIR corrected DAC signal.  The playback outputs remain unchanged.

**MPD owns this FIFO, and the analyzer must not.**  MPD's `fifo` plugin creates
the special file when it enables the output, opens a write *and* a read end on
it, and unlinks it again when the output is disabled.  Put another FIFO at the
same path and the two sides part company in complete silence: MPD goes on
writing the inode it opened, which no longer has a name, while the analyzer
reads a new one nobody writes.  `mpc outputs` still says enabled, the FIFO is
still there, and the display is simply empty for ever — MPD does not reopen an
output it believes is fine, so only an MPD restart recovers.  So the analyzer
enables the output *first* and opens whatever MPD puts there, never creating it
(`_ensure_fifo(..., create=False)`), and re-checks while it runs that what it
holds is still what the path names.  The CD FIFO is the other way round: it is
ours, `omdrcctrl` creates it, and a reader appearing on it is the signal that
starts the tee.

When PCM never arrives while the producer says it is playing, the card says so
(`no writer`) with the reason, rather than sitting on "waiting".  On FreeBSD
that check cannot come from the read alone: a FIFO opened `O_RDONLY|O_NONBLOCK`
does not report EOF until a writer has attached and gone, so "nobody has ever
written this" and "the writer has nothing to say" look identical at the
descriptor.

### The CD / S-PDIF source

CD audio never passes through MPD.  The bridge reads the capture interface and
writes the loopback directly (`doc/CDIN-LINUX.md`, and the chain diagram in the
manual), with MPD a *sibling* writer on the same output rather than anything
upstream.  So during a disc the MPD FIFO stays empty and the analyzer sees
nothing — which is the whole reason this source exists.

How the PCM reaches the FIFO differs by operating system, because the two
bridges are not the same kind of thing.

**FreeBSD.**  `omdrc-cdin` is ours, and its playback thread already holds the
period it is about to write to the device.  It tees that buffer to the FIFO
itself (`cdin/src/spectap.c`), enabled with one rc.conf line:

```sh
omdrc_cdin_spectrum_fifo="/tmp/omdrc-cdin-spectrum.fifo"   # same as cdin_fifo_path
```

Three properties make that safe to leave on permanently, and each is asserted
in `cdin/tests/test_spectap.c` rather than trusted:

* **It can never stall playback.**  The descriptor is `O_NONBLOCK` and a short
  or `EAGAIN` write is discarded, never retried.  A reader that stops draining
  loses frames; it does not back up into the audio path.  This is the one that
  would otherwise be *audible*.
* **The reader's presence is the entire protocol.**  Opening a FIFO `O_WRONLY`
  fails with `ENXIO` while nobody holds the read end, so the daemon retries on
  a slow cadence and starts teeing the moment the panel opens it; when the
  panel goes away the next write gets `EPIPE` and the tap re-arms.  No IPC, no
  shared state, nothing to clean up after a crash on either side.
* **It never creates the FIFO.**  `omdrcctrl` does that, as its own user.  The
  daemon usually runs as root, and a root-created world-writable FIFO for a
  feature nobody asked to be always on is a hazard.  No FIFO means no tap,
  which is correct when the analyzer is not configured.

While a reader is attached the daemon's `[stats]` line carries the tap, and the
CD input card reads it back:

```
... starves 0  silence 0%  up 125 s  spectrum 1411200 B dropped 4096 B
```

It is absent the rest of the time — a field that is almost always missing says
more by its presence than a permanent `spectrum 0` would.  Steady growth in
`dropped` means the analyzer is not keeping up and its display is not quite
what it appears to be; it does **not** mean the disc dropped out.

**Linux.**  `alsaloop(1)` is a third-party binary and the supervisor never sees
a sample, so there is nothing in-process to tee.  Instead omdrcctrl reads the
*same capture device* a second time and writes the FIFO itself, by running

```sh
arecord -D <cdin_capture_pcm> -f S32_LE -c2 -r44100 -t raw <cdin_fifo_path>
```

for the life of the stream.  That needs a **shareable** capture PCM, because a
second `hw:` opener gets `EBUSY`.  `dsnoop` is exactly that, and it neither
resamples nor mixes, so what reaches BruteFIR stays bit-identical:

```
pcm.dsnoop_cdin {
    type dsnoop
    ipc_key 2748
    slave { pcm "hw:CARD=U24XL,DEV=0" channels 2 format S32_LE rate 44100 }
}
```

Then point both at it — `cdin_capture_pcm = dsnoop_cdin` in `commands.conf`,
and `omdrc-cdin-alsaloop`'s `-C` at the same PCM.

Reading independently, rather than teeing alsaloop's output, is the point: if
this reader stalls it misses samples and alsaloop is untouched.  The tempting
alternative — an ALSA `file` plugin on alsaloop's `-P`, which looks tidier —
was rejected because it puts omdrcctrl's scheduling *inside* the CD's audio
path: the plugin blocks on opening a FIFO with no reader, and once open, a
FIFO that is not drained fast enough blocks the writer.  That is CD dropouts
caused by the spectrum display, and it contradicts the rule this whole feature
is built on.

> **Not yet verified on hardware.**  `--sync playshift` steers snd-aloop's rate
> shift from capture timing, and inserting `dsnoop` changes the period layer it
> observes.  The data path stays bit-perfect, but the control loop's behaviour
> through `dsnoop` has not been measured on the NUC.  Leave `cdin_capture_pcm`
> empty until it has been.

## Enabling

The feature is off by default.  Enable it in the installed
`commands.conf`:

```ini
[spectrum]
enabled = yes
source = auto
mpd_output_name = OMDRC Spectrum
fifo_path = /tmp/omdrc-spectrum.fifo
sample_rate = 48000
cdin_fifo_path = /tmp/omdrc-cdin-spectrum.fifo
cdin_sample_rate = 44100
cdin_capture_pcm =
bits = 32
channels = 2
refresh_hz = 25
fft_size = 16384
precision_fft_size = 65536
bands = 24
min_frequency = 31.5
floor_db = -40
fall_db_per_s = 30
vu_mode = bars
drc_delay_trim_ms = 0
drc_delay_delta_min_ms = -1000
drc_delay_delta_max_ms = 2000
```

Then make sure the matching MPD output exists in the MPD config — `mpd/mpd.conf`
on Linux, `mpd/musicpd.conf` on FreeBSD (both rendered from their `.in`
templates) — and restart MPD plus `omdrcctrl`.

The output name, FIFO path, and format settings must match between MPD and
`commands.conf`.  The current analyzer supports stereo S32_LE input.

## Runtime Lifecycle

At rest the MPD FIFO output is disabled and the card shows only its title and
the Start button; the spectrum, VU and floor controls stay collapsed.

When the web UI Start button is pressed:

0. The card expands to reveal the graphs, VU meters and floor slider.
1. The source is resolved — once per stream, not once per process, so `auto`
   sees the chain as it is *now*: a disc may have gone on since boot.
2. `omdrc-ctrl` creates that source's FIFO if needed.
3. The analyzer opens the FIFO for non-blocking read.
4. The source is started: for `mpd` that enables the output named by
   `mpd_output_name`; for `cdin` on Linux it spawns the `arecord` reader, and
   on FreeBSD it does nothing at all, because opening the FIFO for reading is
   itself the signal the bridge is waiting for.
5. FFT frames are sent to the browser using Server-Sent Events, each carrying
   the source name and its rate so the card can label what it is showing.

When Stop is pressed the card collapses back to the title row.

When the browser stream closes:

1. The server notices the disconnect.
2. The analyzer thread stops.
3. The source is stopped — the MPD FIFO output is disabled, or the `arecord`
   reader is terminated — and only then is the read end closed, so a source
   with an explicit writer is never left writing into a closing FIFO.  For the
   FreeBSD bridge that close *is* the stop: its next write gets `EPIPE`.

The page also stops the stream on `visibilitychange` and `pagehide`, so hiding
the browser on Android stops capture and FFT processing.  If the user had
started the analyzer before hiding the tab, returning to the visible page starts
it again.

### Multiple clients

The analyzer is reference-counted: one capture/FFT thread publishes frames that
every connected browser receives over Server-Sent Events.  Opening the page on
a second device just joins the same broadcast — it does **not** start a second
capture.  The MPD FIFO output is enabled when the first client starts and
disabled only when the **last** client disconnects.

### Reliable disable

The FIFO output must never be left enabled with no consumer (it would make MPD
write to a pipe nobody drains).  Two safeguards guarantee this:

- A quick Stop→Start (for example a Music/Precision switch, which reconnects)
  is serialised: a new capture thread waits for the previous one to finish its
  `mpc disable` before it runs `mpc enable`, so the two can never race.
- On startup `omdrc-ctrl` force-disables the output once, recovering from a
  previous run that was killed mid-stream.  Combined with `enabled "no"` in the
  MPD config, the output is therefore off until Start is pressed.

## FFT and Display

Default settings:

| Setting | Value |
| --- | --- |
| Refresh | 25 Hz |
| Music FFT size | 16384 samples |
| Precision FFT size | 65536 samples |
| Bands | 24 nice-label logarithmic bands, starting at 31.5 Hz |
| FIFO rate | 48 kHz |
| Level scale | dBFS, default floor -40 dBFS |
| Graph | Interactive Chart.js separate Left/Right band charts, low-to-high color gradient |
| VU default | `bars` or `needles`; switchable in the web UI |

The server computes the FFT with NumPy, sums linear FFT energy into logarithmic
display bands, computes per-channel RMS/peak VU values, and sends only the
compact graph data to the phone.  The VU RMS/peak are measured over a short
trailing slice of the capture buffer (~50 ms) rather than the whole FFT window,
so the meters track the music instead of lagging by the FFT length.

The Floor slider sets the bottom of the scale; the top is always pinned at
0 dBFS.  It applies to the band graphs and to both VU styles (bars and
needles), so all of them share one dynamic range.  When the music stops every
display drops to its bottom (effectively -∞) regardless of the floor.

### VU styles

A Bars/Needles toggle below the equalizer switches the VU display;
`[spectrum] vu_mode` only picks the startup default.

- **Bars** — horizontal Left/Right meters with a fast (~50 ms) fill.
- **Needles** — analogue-style dials drawn on a canvas.  The dial scale follows
  the Floor slider (five ticks spread from the floor to 0 dBFS) and the arc is
  drawn tall with a wide angular sweep so the needle travels a long, readable
  distance.

Both styles use **analog-style ballistics** so they do not snap to zero when the
music stops.  A `requestAnimationFrame` loop eases the displayed level toward the
latest measured value: *rises are instant* (transients pop immediately), while
*falls are rate-limited* so the needle/bar glides down like a weighted meter.
The fall is level-dependent — small musical dips (≤ 8 dB) return fast at
`VU_FALL_FAST` so the meters stay lively, but a large abrupt drop (a stop) glides
at the slower `VU_FALL_SLOW`, reaching the floor in roughly half a second to a
second.  A constant dB/s rate (rather than an exponential) gives a uniform,
needle-like travel speed; the three constants at the top of the VU-ballistics
block in `index.html` tune the feel.

The default FFT size is intentionally moderate: at 48 kHz, 16384 samples give
about 2.9 Hz bin spacing and a much livelier display than long measurement
windows.  The web UI's Music/Precision toggle switches the stream between this
music window and `precision_fft_size` for narrow test-tone display.

### Multi-resolution bands

A single FFT window forces a bad compromise: bass needs a **long** window for
frequency resolution, but a long window is **slow on transients** — with one
8192-sample window every band is an average of 171 ms, so cymbals and drum
attacks look smeared and late.

Music mode therefore analyses in three resolution tiers and gives every display
band the **shortest window that still resolves it**:

| Tier | Window @ 48 kHz | Used for |
| --- | --- | --- |
| `fft_size` | 171 ms (at 8192) | bass, up to ~160 Hz |
| `fft_size / 4` | 43 ms | lower mids (~250 Hz) |
| `fft_size / 8` | 21 ms | ~400 Hz and up — cymbals, drum attacks |

Assignment is automatic (`_assign_band_tiers`): a band takes the shortest tier
that still lands at least ~3 FFT bins inside it, falling back to the full window
for the narrow low bands.  All tiers end on the same sample, so they stay time
aligned, and because the per-band value is `sqrt(Σ bin²)` of amplitude-normalised
magnitudes, levels are window-length independent — measured systematic error
across a crossover is **≤ 0.8 dB**, so there is no visible step.

The payoff is large: after a 5 ms noise burst (a cymbal hit), the high bands read
about **−29 dB with multi-resolution versus −74 dB with one 8192 window** — the
transient actually registers instead of being diluted across 171 ms.  Cost is
~7 % more FFT work, about **1 % of one core at 30 Hz refresh**.

Precision mode keeps a **single full-length window** — measurement work wants
maximum resolution everywhere, not fast transients.

`floor_db` controls visual sensitivity and is also adjustable from the web UI
with the Floor slider.  A higher floor such as `-35` hides more low-level band
energy; a lower floor such as `-70` reveals quiet detail.  The default `-40` is
tuned for music rather than measurement work.  A change made with the slider is
remembered across restarts (persisted as `spectrum-floor-db` in the state
directory — see [Runtime state](#runtime-state)), so `floor_db` in
`commands.conf` is only the initial default until the slider is first moved.
The Floor and Sync sliders live behind the **Sliders** toggle in the
Music/Precision row.

### Runtime state

The slider positions are the only things the analyzer writes at runtime.  The
state directory is resolved exactly as `drc.sh` resolves it, so the whole stack
shares one location (see `doc/FREEBSD-PORT-PLAN.md` §1.4):

| Condition | State directory |
| --- | --- |
| `$OMDRC_STATE_DIR` set | that path (services pin this) |
| run-from-repo (`config.env` in the checkout) | beside the checkout |
| running as root | `/var/db/omdrc` |
| otherwise | `${XDG_STATE_HOME:-~/.local/state}/omdrc` |

A packaged install must never write inside its own installed files — `pkg
check -s` flags any modified packaged file.  Note that the `omdrcctrl` rc.d
script drops privileges to a service user, so the root branch is *not* taken
under `service(8)`: pin `OMDRC_STATE_DIR` (in `rc.conf` or `omdrc.conf`) to give
the service a writable, shared state directory.  Writes are best-effort — if the
directory is not writable the sliders simply stop being remembered rather than
failing the request.

## Detached window

The analyzer card can be popped out of the main page into its own window, so it
can sit on a second monitor or stay visible while you use other apps.  Two buttons
in the card's title row drive this:

- **⇗ Detach** — opens the same page in *spectrum-only* mode in a separate browser
  window (`window.open('?view=spectrum', …)`).  That mode simply hides every card
  except the analyzer and auto-starts it, so it is the exact same code with
  nothing to keep in sync.  Works in every browser.
- **PiP** — opens an always-on-top **picture-in-picture** window (Chromium's
  Document Picture-in-Picture).  The PiP window hosts the spectrum-only page in an
  `<iframe>`, so it is fully interactive.  The button appears only where the
  browser supports the API (Chrome/Edge; hidden in Firefox/Safari).

Each detached view is just another analyzer client: it joins the same
reference-counted, server-side SSE broadcast rather than starting a second
capture.  The Sync delta and Floor are server-side settings, so every view — main
page, popout and PiP — shows the same corrected, identically-scaled display.

With `source = auto`, an already-open card follows playback hand-offs in both
directions: it closes the MPD FIFO and attaches the CD-in FIFO when the CD bridge
becomes active, then returns to MPD when the bridge stops. The browser stream
and card stay open throughout the hand-off.

## DRC Sync

The tap is at the **top** of the chain — MPD's second FIFO output, or the CD
bridge teeing the period it is about to play — while the sound you hear has been
through the loopback, BruteFIR and the DAC, all of which are late.  Left
uncorrected the bars run ahead of the music, and keep going after it stops.  The
analyzer therefore holds its analysis window back by the whole path delay.

Every stage gets its own term, derived from the chain that is actually running:

| Stage | How it is obtained | At 44.1 kHz, flat, `-s 200ms` |
| --- | --- | --- |
| virtual_oss | `drc_voss_blocks` x its own `-s` argument, read off the running process | 600 ms |
| Filter group delay | `argmax(|h|) / rate` of the active coefficient | 0 ms (`dirac pulse`) |
| Convolver | one `filter_length` partition | 186 ms |
| BruteFIR I/O | `drc_brutefir_io_partitions` more of them | 371 ms |
| Output | `drc_output_delay_ms` — DAC buffer and USB | 150 ms |

The panel prints the breakdown term by term, so an estimate that looks wrong can
be traced to the stage responsible instead of guessed at.

Two properties are worth calling out because they are easy to get backwards:

- **The loopback is usually the largest term, and it does not scale with rate.**
  `drc.sh` starts virtual_oss with `-s 200ms`, a *duration*, so those 600 ms are
  the same at 44.1 kHz and at 192 kHz — while the BruteFIR partitions shrink as
  the rate rises.  An earlier version of this estimate counted BruteFIR alone
  and was short by well over a second.
- **`dirac pulse` really does have zero group delay.**  BruteFIR's built-in
  identity coefficient is an in-memory impulse, not a missing RAW file, and it
  peaks at index 0 — so on the shipped flat profile the entire delay is
  buffering.  The convolver partition is still charged.

Stages that are not running contribute nothing: with the chain down (MPD
straight to the DAC) the total is zero.

The hold-back is anchored to the **clock**, not to the end of the capture
buffer.  That distinction only shows up when the music stops: a fixed byte
offset from the end of a buffer that has stopped growing freezes the display,
so the plots outlive the sound by the whole delay.  Anchored to the clock, the
read point keeps draining forward at one second per second once the writes
stop — the display plays out the buffered tail exactly as the chain does, and
goes silent when the tail is genuinely finished rather than after a fixed
fraction of a second.  It also means a producer that drops frames (the CD
bridge tees non-blocking, and says so in its `[stats]` line) cannot walk the
display out of sync.

With **Auto sync delay** off, the three *modelled* stages — virtual_oss, the
BruteFIR I/O buffers and the output — are replaced wholesale by the configured
`drc_delay_trim_ms`, so a figure measured with `tools/measure-drc-delay.sh` is
not double-counted against a model of the same buffering.  What is *read* from
the filter (group delay and the convolver partition) still counts in both modes.

Reading the FIR to find its peak is cached on the filter's mtime; the rest is a
short-TTL `ps` read, so the estimate follows a virtual_oss restart or a preset
change under a card that never closed, at no per-frame cost.  The live value is
shown by the `base` / `delta` / `total` readout under the sliders.

**Auto sync delay** is the one checkbox below the Sync slider.  With it on, a
running analyzer re-derives the estimate every couple of seconds, because the
chain under an open card is not fixed: virtual_oss restarts at every rate change
and a preset switch reloads BruteFIR with a different filter.  The expensive
part is cached, so this costs nothing per frame.  Ordinary MPD, qobuzconnect2mpd
and upmpdcli track changes do not change the answer.  The setting is remembered
across restarts.

This is a calculation from the active DRC configuration, not an acoustic or DAC
loopback measurement. A real end-to-end measurement needs a separate post-DRC
capture tap and an injected known signal; `tools/measure-drc-delay.sh` provides
that disruptive calibration procedure. It is intentionally never run from the
web card during music playback.

### Stale history is dropped at a resume

The hold-back reads back into the capture buffer, and that buffer survives a
pause.  So a resume used to land the read point in the tail of whatever played
*before* the gap: the bars came alive within a frame of Play, showing the end of
the previous track, while the new music was still working its way down the
chain.  To a listener that reads as "the bars move immediately and the sound
arrives two seconds later" — the display looks early when it is in fact a whole
track late, and no amount of Sync-slider adjustment can fix it, because the
error is *what* is being drawn and not *when*.

PCM arriving after a gap longer than the silence timeout therefore clears the
retained history.  The bars then stay at the floor until real audio has been in
flight for the length of the hold-back — which is also, to within a frame,
when the first of it reaches the speakers.

### What the no-DRC case tells you

With the chain down the model yields **zero** — no virtual_oss, no convolver,
no BruteFIR I/O — and the analyzer holds nothing back.  That is exactly what a
listener sees: MPD straight to the DAC already looks in sync.  It is worth
stating plainly, because it is the one free calibration point available, and it
says the model's *shape* is right.  MPD's own buffering does not put the tap
ahead of the DAC; everything that does is added by the DRC chain, and every
stage of that chain is enumerated above.

What the model gets wrong is the *size*, not the set of terms: with DRC up the
real lead measures larger than they sum to.  Closing that residual is the Sync
slider's job.

### Sync slider

The measured delay is a good estimate, but the last few tens of milliseconds of
runtime buffering vary between machines, and only the listener can judge when the
bars line up with the sound.  A **Sync** slider under the analyzer adds a live
**delta** on top of the measured **base** delay so it can be nudged by ear.  The
row beside it prints all three figures in milliseconds:

    base <measured> ms · delta <slider> ms · total <base+delta> ms

The total applied hold-back is floored at 0 — you cannot show samples that have
not been played yet — so a negative delta larger than the base has no further
effect.  The slider position is remembered across restarts (persisted as
`spectrum-drc-delay-delta` in the state directory — see
[Runtime state](#runtime-state)) and is applied to the shared capture thread, so
every connected browser sees the same corrected display.

A slider move applies on the **very next frame**.  It did not always: the
capture buffer was trimmed to the hold-back then in force, and the trim runs
before the section that recomputes it, so every *increase* found the history
already cut short and the analyzer published `waiting` with no bands for
exactly as long as the increase.  Dragging the slider restarted that stall at
every intermediate value, so the plot never moved and the control looked
completely inert.  The buffer now reserves the slider's whole travel
(`base + drc_delay_delta_max_ms`, a couple of MB), so the read point stays
inside it wherever the slider is put.  The one wait that remains is inherent
and only at startup: the analyzer cannot show delayed audio it has not captured
yet, so the first `base + delta` seconds of a fresh attachment read `waiting`.

The travel limits are the two config-editable keys `drc_delay_delta_min_ms` and
`drc_delay_delta_max_ms` (default `-1000` … `2000`, i.e. −1 s … +2 s).  Unlike
`drc_delay_trim_ms`, which replaces the modelled stages when Auto sync is off,
the slider delta is a runtime setting and is not written back to `commands.conf`.

## Responsiveness

Two settings decide how the bars behave, and they are deliberately separate:

| Setting | What it controls |
| --- | --- |
| `refresh_hz` | how often the server analyses and publishes a frame |
| `fall_db_per_s` | how fast a bar falls back once its band goes quiet |

There is no attack setting, because there is no attack shaping.  Every band is
**peak-held across the whole frame interval**: a tier whose analysis window is
shorter than the interval steps back over the new audio in half-window hops and
keeps the loudest result, so a transient is drawn whatever moment inside the
frame it arrived in.  This is what fast percussion needs — a 2048-sample treble
window spans about 43 ms, and at the old 10 Hz default that left most of every
100 ms interval unanalysed, so hi-hats landing in the gaps were simply never
seen.  At 25 Hz the interval is 40 ms, every window already covers it, and the
hops cost nothing; they only start doing work if `refresh_hz` is lowered or a
frame runs late.

Falls are shaped instead, in the browser, at a constant dB/s so the bars travel
at a readable and uniform speed.  Because the release is animated client-side,
the server does not have to keep publishing frames to make the bars come down
when the music stops — the last frame says where they are going.

## Host Load

Expected load is low:

| Resource | Approximate cost |
| --- | --- |
| FIFO PCM read | about 0.38 MB/s at 48 kHz, stereo, 32-bit |
| FFT CPU | small; normally far below BruteFIR convolution cost |
| LAN traffic | tiny at 25 Hz with 24 bands |
| Memory | a small rolling PCM buffer plus FFT arrays |

The analyzer does no work while the card is stopped or the browser page is
hidden/closed.  The band analysis measures about 2 ms per display frame for both
channels on this box — roughly 5% of one core at 25 Hz, against 2.6% at the old
10 Hz default, which is the whole CPU price of the responsiveness.  A frame that
has nothing new to say is not published at all, so a paused source costs nothing
on the wire either.  The delay estimate is re-derived on a two-second cadence
only while a card is open, and behind a cache, so there is no background timer.

## Limitations

- Linux and FreeBSD.  The tap is a plain MPD `fifo` output (a named pipe), so
  it behaves identically on both; only platforms without `os.mkfifo` (Windows)
  are unsupported.
- MPD-only source. Other programs playing directly to ALSA are not captured.
- Pre-DRC signal. Post-BruteFIR analysis would need a separate BruteFIR/ALSA
  monitor tap.
- The analyzer copy may be resampled to the configured FIFO format. This does
  not imply the playback output is resampled.
- Requires NumPy in the Python environment used by `omdrcctrl`.

## Troubleshooting

If the UI reports that the MPD output is not found, check:

```bash
mpc outputs
```

The output name must exactly match `mpd_output_name`.

If the graph stays in `waiting`, check that MPD is playing and that the FIFO
path in MPD matches `fifo_path`.

If MPD fails to start, make sure it can create or open the FIFO path's parent
directory and that the `audio_output` syntax is supported by the installed MPD.
