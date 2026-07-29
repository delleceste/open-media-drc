# Live Spectrum Analyzer

`omdrc-ctrl` can show a live two-channel FFT spectrum from the audio currently
played by MPD.  The implementation deliberately avoids USB packet sniffing and
does not insert anything into the BruteFIR/DAC playback path.

## Signal Source

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

## Enabling

The feature is off by default.  Enable it in the installed
`commands.conf`:

```ini
[spectrum]
enabled = yes
mpd_output_name = OMDRC Spectrum
fifo_path = /tmp/omdrc-spectrum.fifo
sample_rate = 48000
bits = 32
channels = 2
refresh_hz = 10
fft_size = 16384
precision_fft_size = 65536
bands = 24
min_frequency = 31.5
floor_db = -40
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
1. `omdrc-ctrl` creates the FIFO if needed.
2. The analyzer opens the FIFO for non-blocking read.
3. `omdrc-ctrl` enables the MPD output named by `mpd_output_name`.
4. FFT frames are sent to the browser using Server-Sent Events.

When Stop is pressed the card collapses back to the title row.

When the browser stream closes:

1. The server notices the disconnect.
2. The analyzer thread stops.
3. `omdrc-ctrl` disables the MPD FIFO output.

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
| Refresh | 10 Hz |
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

## DRC Sync

The tap is the **pre-DRC** MPD FIFO, but the sound you hear has been through
BruteFIR, which is late.  Left uncorrected, the bars and VU would run ahead of
the music.  While BruteFIR is running, the analyzer holds its analysis window
back by the BruteFIR path delay so the display matches what is audible.

The delay is computed from the **active** filter (see
`video/AV-SYNC-DELAY.md` for the full derivation) and has two exact parts:

- **Filter group delay** — the impulse-response peak of the active `L.raw`
  (`argmax(|h|) / rate`).  For the bundled correction filters this is ~0.5 s and
  is the same in *time* at every sample rate.
- **BruteFIR partition latency** — one `filter_length` block,
  `partition_size / rate`, read from `~/.config/BruteFIR/brutefir_defaults.conf`.
  This term is rate-dependent: at 192 kHz it is ~0.17 s (≈0.67 s total, matching
  the video delay); for native playback at 48 kHz it is larger.

`drc_delay_trim_ms` adds the small, runtime loopback/output buffering on top.

The value is cached and recomputed only when the active config, filter file or
defaults change, so it tracks preset / rate / filter edits with no per-frame
cost.  When DRC is bypassed (BruteFIR not running) the delay is zero.  The live
value is shown in the card status line as `DRC sync +Xs`.

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

The travel limits are the two config-editable keys `drc_delay_delta_min_ms` and
`drc_delay_delta_max_ms` (default `-1000` … `2000`, i.e. −1 s … +2 s).  Unlike
`drc_delay_trim_ms`, which is a static fine-tune baked into the base, the slider
delta is a runtime setting and is not written back to `commands.conf`.

## Host Load

Expected load is low:

| Resource | Approximate cost |
| --- | --- |
| FIFO PCM read | about 0.38 MB/s at 48 kHz, stereo, 32-bit |
| FFT CPU | small; normally far below BruteFIR convolution cost |
| LAN traffic | tiny at 10 Hz with 24 bands |
| Memory | a small rolling PCM buffer plus FFT arrays |

The analyzer does no work while the card is stopped or the browser page is
hidden/closed.

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
