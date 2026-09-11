# The FreeBSD / Linux sound-quality investigation — complete record

**Period:** 2026-09-08 → 2026-09-10
**Machine:** Intel NUC `arki` (Arch Linux) / `bee` (FreeBSD 15.1-RELEASE-p2) — one
box, dual-boot, one disk
**DAC:** OKTO RESEARCH DAC8STEREO, USB `152a:88c5`, bcdDevice 1.60, serial 000483

Written to be read start to finish by someone who wasn't there. Companion
documents go deeper on individual pieces:

| Document | What it covers |
|---|---|
| `DAC-FORMAT-EXPLAINED.md` | plain-language background: containers, alignment, what each failure sounds like |
| `DAC-FORMAT-ALIGNMENT.md` | the Linux-side format analysis and symptom triage |
| `DAC-FORMAT-CROSS-OS-PROCEDURE.md` | the runbook, and §7 the FreeBSD run's results |
| `CROSS-OS-FILTER-COEFFICIENTS.md` | the filter divergence, and §7 the final null |
| `BIT-PERFECT-VERIFICATION.md` | the USB-wire tap suite |
| `RATE-MISMATCH-PROCEDURE.md` | the rate-mismatch test: Linux results, and the FreeBSD procedure |

---

## 1. The complaint, and the answer

**The complaint.** The same music, the same DAC, the same room, the same filter
design — but FreeBSD was heard as worse than Linux: a loss of *clarity, air and
detail*. The suspicion to test first was that one operating system was handing
the DAC samples that were correctly valued but wrongly positioned — shifted by a
byte, or padded on the wrong end.

**The answer, established over three days of measurement.** It is not that. Given
the same input and the same filter, **FreeBSD and Linux put bit-identical bytes
on the USB wire.** Not "close", not "within rounding" — zero differing samples
out of 7,051,384, verified by nulling two independent USB captures against each
other.

Every candidate inside the digital path has been measured and excluded. One
digital caveat remains genuinely open — the rate-mismatch guard, §9 — and it
concerns *mixed-rate ordinary listening* rather than anything tested here.
Everything else that is left lies downstream of the wire, or is not in the
equipment at all.

---

## 2. The chain, on each operating system

```
                 ┌─────────── Linux (arki) ────────────┐
   file/stream → MPD → snd-aloop → BruteFIR → USB → DAC
                        hw:1,0/1,1        hw:2,0

                 ┌────────── FreeBSD (bee) ────────────┐
   file/stream → MPD → virtual_oss → BruteFIR → USB → DAC
                    /dev/dsp.play    /dev/dsp.dac
                    /dev/dsp.loop
```

The convolver, the DAC and the material are common. The loopback differs by
necessity — `snd-aloop` is a kernel module, `virtual_oss` a userland cuse daemon
— and that difference was the prime suspect for most of the investigation.

`virtual_oss` is started as:

```
virtual_oss -D /tmp/virtual_oss.pid -r <rate> -i 8 -C 2 -c 2 -b 32 \
            -s 200ms -f /dev/null -a 0 -d dsp.play -L dsp.loop
```

`-f /dev/null` means it is a pure bridge, not the thing driving the DAC; BruteFIR
opens the DAC itself. It therefore free-runs, which matters later.

---

## 3. Three ideas that the whole question turns on

Conflating these is what makes this class of bug hard to reason about.

- **The container** — how many bytes each sample occupies. Determines *spacing*,
  where one sample ends and the next begins.
- **The valid bits** — how many of those bits carry real audio. A 24-bit DAC
  ignores anything finer.
- **The justification** — *where* the valid bits sit inside the container: pushed
  against the top (MSB / left) or the bottom (LSB / right).

One 24-bit sample `AABBCC` in a 4-byte container, three ways:

```
left-justified  (MSB-aligned):   AA BB CC 00
right-justified (LSB-aligned):   00 AA BB CC
packed, 3 bytes, no container:      AA BB CC
```

Same audio, three different byte streams. Feed one to something expecting
another and you get a defect, never an error message.

**Why the symptom mattered as evidence.** A *stride* mismatch — producer writing
4 bytes per sample, consumer reading 3 — displaces every sample after the first
and degenerates within a fraction of a second into full-scale noise. It is
unmistakable. A *justification* error is a clean ±48 dB level error. Neither
sounds like "slight loss of air". That asymmetry was a hint from the start that
if anything real was happening, it was subtler than a shift — and the most likely
subtle candidate is silent resampling (§9).

---

## 4. What the DAC actually asks for

From the USB configuration descriptors, playback interface 1 (UAC2):

| Alt | `bSubslotSize` | `bBitResolution` | Container | `bmFormats` |
|-----|---------------|------------------|-----------|-------------|
| **1** | **4 bytes** | **24** | `S32_LE` | `0x00000001` PCM |
| 2 | 2 bytes | 16 | `S16_LE` | `0x00000001` PCM |
| 3 | 4 bytes | 32 | `S32_LE` | `0x00000001` PCM |
| 4 | 4 bytes | 32 | `S32_LE` | `0x80000000` RAW/DSD |

Endpoint `0x01` OUT (async), `wMaxPacketSize` 392, explicit feedback endpoint
`0x81` IN (4 B, 16.16 format). Rates 44.1 k – 384 k.

**Alt 1 is what both systems use**: 4 bytes of space, 24 bits of content. The USB
Audio Class specification settles the justification — samples are
**left-justified**, valid bits at the top — so the DAC takes the top 24 bits of
each 32-bit little-endian word and discards the lowest byte. Send it an ordinary
full-range 32-bit stream and it is correct; the 8 bits lost sit around
−144 dBFS.

There is **no 3-byte (`S24_3LE`) alt-setting on this DAC**, so the classic
packing shift has nowhere to happen. For contrast the ESI U24XL on the same host
*does* declare a 3-byte container — a helper written for that device and reused
for the OKTO would produce exactly the stride mismatch above. (That contrast
becomes relevant in §8.)

Two quirks worth knowing:

- The device exposes **two configuration descriptors that both report
  `bConfigurationValue = 1`**. Tools must distinguish them by ordinal, or every
  alt appears twice.
- The front panel reads **24** even for a 16-bit track, because it reports the
  alt-setting's declared resolution, not the file's. Cosmetic.

---

## 5. How the two operating systems reach alt 1

Different code, same destination — which is why it needed checking rather than
assuming.

**Linux** (`parse_audio_format_i_type()`) selects the container from the subslot
size, 4 bytes → `S32_LE`, and keeps `bBitResolution` as metadata.

**FreeBSD** (`uaudio_chan_fill_info_sub()`) computes depth as
`bSubslotSize * 8`, **ignoring `bBitResolution`**, so alts 1, 3 and 4 all
register as "32-bit"; it then keeps only one alt per sample rate, first in
descriptor order — **alt 1**.

---

## 6. Everything that was measured

In the order it was done. Every row is a measurement, not an inference.

### 6.1 The direct path, both operating systems (pre-existing, `bp-results/`)

USB wire captures — `usbmon` on Linux, `usbdump` on FreeBSD — of the same file
played to the raw device, chain down.

| Test | OS | aligned payload sha256 | verdict |
|------|----|------------------------|---------|
| 44100 / S32 / 30 s | linux 7.1.5 | `02905a1e322dc6e1…` | BIT-PERFECT |
| 44100 / S32 / 30 s | freebsd 15.1 | `02905a1e322dc6e1…` | BIT-PERFECT |
| 44100 / S24 / 30 s | freebsd 15.1 | `caf889126900964b…` | BIT-PERFECT |
| 192000 / S24 / 10 s | freebsd 15.1 | `7acfeed3f84614d1…` | BIT-PERFECT |

The first two rows are the same input on two operating systems, **byte-identical
on the wire**. The 24-bit rows show FreeBSD promoting 24-bit material into the
32-bit container losslessly, low byte zeroed, no shift.

**These captures bypass `virtual_oss` and BruteFIR**, which is why the
investigation continued: "direct is bit-perfect" does not generalise to the
chain music actually takes.

### 6.2 Linux chain state, live at 192 kHz

`/proc/asound/*/hw_params`, captured with audio playing:

| Hop | Node | Format | Rate | Ch |
|-----|------|--------|------|----|
| player → loopback | `card1/pcm0p/sub0` | `S32_LE` | 192000 | 2 |
| loopback → BruteFIR | `card1/pcm1c/sub0` | `S32_LE` | 192000 | 2 |
| BruteFIR → DAC | `card2/pcm0p/sub0` | `S32_LE` | 192000 | 2 |

`/proc/asound/card2/stream0`: `Interface = 1, Altset = 1, Packet Size = 288,
Momentary freq = 192005 Hz, Feedback Format = 16.16`.

`S32_LE` end to end, no conversion at any hop. `snd-aloop` forces one shared
format across the pair, and BruteFIR opens `hw:` devices directly with no `plug`
layer, so ALSA can insert no converter.

### 6.3 FreeBSD chain state — alt-setting confirmed on the wire

Not inferred from the driver's attach message. BruteFIR alone was restarted
(`virtual_oss` left up, avoiding cuse teardown risk) while `usbdump` recorded:

```
01 0B 00 00 01 00 00 00   SET_INTERFACE  wValue=0  wIndex=1   (BruteFIR closes)
A1 01 00 01 00 29 04 00 → 00 EE 02 00   GET CUR sample rate = 192000
01 0B 01 00 01 00 00 00   SET_INTERFACE  wValue=1  wIndex=1   (BruteFIR opens)
```

**wValue = 1 on wIndex = 1 — alt 1, directly.** The clock is read *before* the
alt is selected, which is the clock-before-alt sequence from the patched
`uaudio`. Iso OUT frames that follow are 192 bytes each: 192 / 4 / 2 = 24 frames
per 125 µs microframe = 192 kHz at 4 bytes per sample. The arithmetic closes.

### 6.4 FreeBSD — no kernel feeder in the path

`bitperfect=1`, `play.vchans=0`, `feedback_rate=192005`,
`hw.usb.uaudio.default_bits=0`, nothing `uaudio`-related in `/boot/loader.conf`.
The decisive evidence is the feeder chain in verbose `sndstat`:

```
[dsp1.play.0]: spd 192000, fmt 0x00201000, pid 2067 (brutefir)
  channel flags=0x2000110c<RUNNING,TRIGGERED,BUSY,HAS_SIZE,BITPERFECT>
  {userland} -> feeder_root(0x00201000) -> {hardware}
```

No `feeder_format`, no `feeder_volume`. `feeder_root` is the channel itself, not
a conversion stage. **With a control**: the idle ESI U24XL on the same box shows
`feeder_root -> feeder_format -> feeder_volume -> {hardware}` — what a
non-transparent path looks like on the same machine. Showing that contrast is
what makes the negative result trustworthy.

### 6.5 The 24-bit stride ambiguity cannot arise

This was the last place a genuine shift could have hidden, and it is a real
ambiguity rather than anyone's bug:

- OSSv4 documents `AFMT_S24_LE` as *24 bits in a 32-bit container* → 4 bytes.
- FreeBSD's `pcm` feeder treats 24-bit formats as **3-byte packed**.
- MPD's internal `S24_P32` is **24 bits in 4 bytes**.

Three parties, two incompatible readings of one name. Resolved by measuring what
MPD actually negotiates, via `mpc status '%audioformat%'` with each file playing
through the DRC chain:

| material | MPD output format |
|---|---|
| 44100 / 16-bit | `44100:16:2` |
| 44100 / 24-bit | `44100:32:2` |
| 44100 / 32-bit | `44100:32:2` |
| 192000 / 24-bit | `192000:32:2` |

A 24-bit track is presented as 32-bit, **never** as any 24-bit OSS format. The
ambiguity cannot occur on this chain at any source bit depth.

### 6.6 FreeBSD self-null — the chain is deterministic

Two independent runs of the same chain, nulled against each other:

```
compared 4,045,609 frames (21.071 s)
differing samples : 0        max difference : 0 LSB
null depth        : -inf dBFS   alignment confidence : 1.0
```

**Bit-exactly reproducible.** This one result kills a whole class of hypotheses:
no xruns or dropouts corrupting the stream, no nondeterministic resampling, no
sample slips from jitter, no race in the `virtual_oss` bridge, no drifting
floating-point accumulation. Whatever FreeBSD does, it does identically every
time — so the complaint cannot be an intermittent digital glitch.

### 6.7 Partitioning — bit-identical, not merely "acoustically identical"

BruteFIR runs different partitioning on the two hosts: `32768,16` on Linux,
`8192,64` on FreeBSD (same 524 288 total taps; smaller partitions on FreeBSD for
`virtual_oss` ring-buffer headroom). Both were run **on the same machine** and
nulled:

```
compared 5,156,484 frames (26.857 s)
differing samples : 0        max difference : 0 LSB
```

The comparison tool's own provenance note hedged that "the last bit may differ".
It didn't. This makes sense in hindsight — float64 carries ~52 mantissa bits, the
output is 32-bit, so different FFT sizes' rounding differences sit ~20 bits below
the output quantum — but it was one of the few real configuration differences
between the two machines, and it is now excluded by measurement rather than by
argument.

> This corrects an assumption stated as fact earlier in
> `DAC-FORMAT-CROSS-OS-PROCEDURE.md`, which listed the partitioning difference
> as "expected to differ — acoustically identical" without testing it.

### 6.8 The cross-OS null — the headline result

The test the whole investigation was building toward: same input samples, same
coefficients, one machine's wire capture nulled against the other's. (What a
null test is and how to read its numbers: `DAC-FORMAT-EXPLAINED.md` §11.)

```
bp-results/null-192000-fbsd-part32768   freebsd15/15.1-RELEASE-p2   9,031,912 frames
bp-results/null-192000-arch-fbsdcoef    linux/7.2.3-arch1-3         3,921,411 frames

aligned at lag -5,506,220 frames (r = 1.000000)
compared 3,525,692 frames (18.363 s)
differing samples : 0 (0.000e+00 of all)
max difference    : 0 LSB of S32
null depth        : -inf dBFS

IDENTICAL: the two chains produced the same bytes
```

Peak level −46.4 dBFS on both sides. Zero differing samples out of 7,051,384,
through **two different loopbacks, two kernels and two audio stacks**.

Artifact: `bp-results/null-192000-xos-fbsdcoef.json`.

---

## 7. The obstacle: the two machines were convolving different filters

Discovered while pre-flighting §6.8, and important in its own right.

`120.blue @multi.pt` at 192 kHz — the filter the appliance actually runs — had
**different coefficient files on each operating system**:

```
             L.raw        R.raw
FreeBSD      0b3a9c24…    a69dd603…
Arch         28a9c5ba…    d0cc9b57…
```

Everything around them was identical: the BruteFIR config naming them
(`c2ae7dec…` on both, byte for byte), the 1.5 dB attenuation, the file sizes
(4 194 304 B = 524 288 `FLOAT64_LE` taps), and — decisively — the **source impulse
responses** the coefficients are derived from (`bad43a63…` / `e52569b6…` on both).
Same measurement, same design, same input.

**Why.** `deploy_filter.py` does not ship the coefficients the design produced. It
*regenerates* them on whichever machine performs the install, resampling the
48 kHz source with SoX — and the two machines do not have the same SoX:

| host | binary | resampler |
|---|---|---|
| FreeBSD 15.1 | `sox 14.4.2.20210509_7` | upstream SoX `rate` |
| Arch | `sox → sox_ng 14.8.0.1` | the `sox_ng` fork's `rate` |

The same divergence exists at every rate and in the `@legacy` variant. It is a
provenance bug too: `verify_filter_bundle.py` checks deployed coefficients
against a manifest, and the Arch deployment can only ever pass against a manifest
Arch wrote itself.

**The critical consequence for the listening tests: no listening comparison made
before 2026-09-10 was an operating-system comparison.** It was two different
filters on two different operating systems.

### Is that difference audible? No.

Measured with `scripts/compare-filter-resamplers.py`. Both files are 4× upsamples
of the same 48 kHz impulse and the DFT bin spacing works out identical
(`192000/524288 == 48000/131072`), so each can be compared against its own source
bin-for-bin with no interpolation or windowing error.

Reproduction of the source response, worst case per band:

| band | SoX 14.4.2 | sox_ng 14.8.0.1 |
|---|---|---|
| 20 Hz – 1 kHz | 0.00016 dB, 0.0012° | 0.00019 dB, 0.0015° |
| 1 – 10 kHz | 0.00012 dB, 0.0008° | 0.00013 dB, 0.0009° |
| 10 – 20 kHz | 0.00014 dB, 0.0009° | 0.00015 dB, 0.0010° |
| 20 – 22.05 kHz | 0.00016 dB, 0.0011° | 0.00016 dB, 0.0010° |

Both flat to **±0.0002 dB and ±0.002°** across the audible band. Direct
difference between the two coefficient sets: peak tap difference 1.34e-6 against
a filter peak of 0.183 — **−103 dB**; RMS −106 dB; 73 802 of 524 288 taps differ
at all. Below 20 kHz the two transfer functions agree to **±0.00002 dB**. Every
visible divergence is above 20 kHz, where a filter derived from a 48 kHz source
carries nothing but resampler stop-band residue.

"Could `sox_ng` have produced better filters under Linux?" Measurably yes in one
respect, audibly no in any. `sox_ng` rejects ultrasonic images better — peak
−98.2 dBr vs −93.3 dBr, better by 4.86 dB — at a peak already 93 dB down and
above 24 kHz. In the audible band the **upstream SoX (FreeBSD) result is
fractionally the more faithful**, by 0.00002 dB. Which points the wrong way for
"FreeBSD sounds worse", if it points anywhere.

### How §6.8 worked around it

Without overwriting the deployed Arch coefficients:

- FreeBSD is dual-boot on this same disk, so its filesystem was mounted
  **read-only** (`mount -t ufs -o ro,ufstype=ufs2 /dev/sda5`) — no network copy,
  no possibility of writing to it. `L.raw`/`R.raw` were taken straight from
  `/usr/local/etc/.../@multi.pt/`, and their hashes matched what the FreeBSD
  capture had recorded in its own provenance.
- They were staged as a **second variant** `@fbsdcoef`, with a config identical to
  the `@multi.pt` one except the two `filename:` lines. `@multi.pt` was never
  written, and afterwards verified byte-identical to its pre-test backup.
- The input material was regenerated with `tests/gen-bitperfect-wav.py` and came
  out at sha256 `01317af6…`, **identical to the FreeBSD run's input** — the
  generator's cross-OS determinism claim holds.
- Partitioning was `32768,16` on both sides, so FreeBSD's `part32768` capture was
  the apples-to-apples reference.

The staged variant was removed afterwards. To reproduce, see
`CROSS-OS-FILTER-COEFFICIENTS.md` §7.

---

## 8. Six tool defects, every one of which returned a confident wrong answer

The recurring theme, and the most transferable lesson: **"the comparison found
nothing" and "the comparison found agreement" are indistinguishable in the
output unless the tool is built to tell them apart.**

| # | Defect | Effect |
|---|---|---|
| 1 | `dac-format-probe.py` ran `usbconfig` unprivileged | `usb_audio_devices: []`, no alt table — probe still exited 0 |
| 2 | Descriptor blob rebuilt from `usbconfig`'s `RAW dump:` blocks | impossible: `usbconfig` prints standard descriptors as decoded fields with no hex. Every standard descriptor missing, the walker never opened an alt |
| 3 | `AFMT` channel field read at bit 24 | `sound.h` puts it at **bit 20**. Every stereo stream decoded as *zero* channels |
| 4 | `bitperfect_runner.py` `discover_linux()` returned the lowest-numbered USB audio card | that is the **ESI U24XL**, not the OKTO the chain feeds. A capture of the wrong endpoint looks exactly like a capture of the right one |
| 5 | `bitperfect-null.py` blocked on the filter variant **label** | `variant` was in `MUST_MATCH`, yet coefficient sha256 equality is checked directly a few lines later and proved the filters identical. The label is only a proxy for the curve |
| 6 | `drc.sh` rate guard never fired on FreeBSD | see §9 — the most consequential of the six |

Defects 1–3 were mine, in the probe I wrote and handed to the FreeBSD side;
defect 2 in particular I wrote speculatively, with no FreeBSD to test against,
and described as though it worked. They were caught and fixed on the FreeBSD run,
along with three smaller ones in the same pass: FreeBSD 15.1 spells verbose
`sndstat` channels `[dsp1.play.0]`, not `[pcm1:play:dsp1.p0]`; `record` is not
`rec`, so the record channel's converting feeder chain was being attributed to
the playback channel above it; and idle channels on a second, unused sound card
were counted as open streams, which by itself read as "the chain disagrees on
rate" — the suite's hard stop. Regression tests: `tests/test_dac_format_probe.py`.

Fixes 4 and 5 were made here. The probe now refuses to guess when several USB
audio cards are present and takes `--card N`; the null treats a variant-label
difference as a note when the coefficient hashes agree, and still blocks when
they do not.

### A seventh, environmental: the renderer

Two cross-OS captures were dominated by full-level Qobuz audio at **−4.7 dBFS
peak** while the test signal sits near **−90 dBFS by design**. Cross-correlation
locked onto the music and reported `r = 0.072` — "almost certainly not recordings
of the same material". Correct verdict, entirely misleading cause.

`qobuzconnect2mpd` runs as a user service and **repopulates MPD's queue**, so
clearing the queue is not enough — the renderer has to be stopped. Worth
remembering for any future capture.

---

## 9. What is still open — rate mismatch. **Linux half done 2026-09-11; FreeBSD half pending.**

> **Status and procedure: `doc/RATE-MISMATCH-PROCEDURE.md`.** It holds the Linux
> results and the exact FreeBSD steps. The summary at the end of this section is
> the short version.

`virtual_oss` runs at a fixed `-r <rate>` while MPD's `DRC-native` output is
`format "*:*:*"` (it keeps the source rate). When the two disagree, something
has to resample — with no error, no log entry, nothing in the UI.

> **Correction (2026-09-11).** This section first said that `virtual_oss` does
> that resampling "with its own built-in resampler", at the fastest quality. That
> contradicts `virtual_oss(8)`, which `BIT-PERFECT-VERIFICATION.md` already quoted:
> resampling is opt-in via `-S`, and `drc.sh` does not pass it. So `virtual_oss`
> coerces MPD to its rate and **MPD resamples with soxr "very high"** — as on
> Linux, where it has now been measured (below). I missed that section when
> writing this record.

A resampler is the most plausible *kind* of remaining explanation for "sounds
worse but I cannot say why", because unlike a byte shift it is subtle: the music
stays recognisable and just loses clarity. Whether it is plausible *here*
depends on which resampler it is and how good it is — which is what was
measured.

`./drc.sh status` is supposed to catch it and print

```
Rate:  MPD 44100 Hz != virtual_oss 192000 Hz  [MISMATCH]
```

which §5 of the procedure document calls a hard stop. **It has never once fired
on FreeBSD.** Two independent causes:

1. `sed -n 's/.*\[\(playing\|paused\)\].*/\1/p'` — `\|` alternation inside a
   basic regular expression is a GNU extension. FreeBSD's `sed` does not error on
   it, it simply never matches. So `drc.sh` reported `MPD: stopped` while `mpc`
   reported `[playing]`.
2. mpc ≥ 0.34 dropped the `audio:` line from `mpc status`; the format is now only
   available as `mpc status '%audioformat%'`. So the `Output audio:` line never
   printed at all.

Together: the only guard against a silently resampling chain was inoperative on
FreeBSD for the entire period during which the sound-quality difference was
being heard. Fixed 2026-09-10 in `f1aaf3a`.

**Why the nulls do not settle it.** Every capture nulled in §6 was taken at a
fixed 192 kHz with 192 kHz material — `sink_rate`, `brutefir_rate` and
`material_rate` all agreed, and `loopback_resampling: false` is recorded in the
provenance. No resampling occurred in those runs. The instrumentation gap shows
up directly in the same records, though:

```
mpd_rate: None    mpd_format: None    rate_verdict: None
```

— the very fields that would have confirmed MPD's side were never populated, on
*both* operating systems. So the guard remains untested where it actually
matters: **ordinary listening with mixed-rate material**, e.g. a 44.1 kHz track
playing while `virtual_oss` sits at 192 kHz.

### What the Linux half showed (2026-09-11)

Full detail, tables and artifacts: `RATE-MISMATCH-PROCEDURE.md` §2.

* **The guard works** in both directions and at 16, 24 and 32 bits: `[MISMATCH]`
  whenever the decoder rate differs from the chain's, `[match]` otherwise.
* **MPD resamples, not the loopback**: MPD opens `snd-aloop` at the chain's rate
  while its decoder runs at the source rate.
* **The resample is at the 32-bit floor**: a −90 dBFS two-tone through a
  mismatched flat chain comes back +2.5…+4 dB above the 32-bit rounding floor,
  ~100 dB below the tone, in both directions and in `resamp` mode. On Linux a
  rate mismatch costs nothing measurable.
* **`resamp` mode's 24-bit path is BIT-PERFECT** on Linux.
* Three tool fixes: `resamp` mode no longer prints a bare `[MISMATCH]` for a
  deliberate resample; captures now record the wire's rate, which differs from
  the material's through a resampler; and the null refuses resampled captures,
  because two runs of MPD's soxr were measured not to be sample-identical.

### What FreeBSD still has to show

That it behaves as documented — MPD's soxr doing the work, residual beside
Linux's. Two FreeBSD-only risks the man page does not cover: MPD built without
soxr would fall back to a poor internal resampler (`musicpd --version`,
`Filters:`), and `resamp` mode forces a 24-bit format across OSS to
`virtual_oss`, the 3-byte/4-byte ambiguity that the 2026-09-10 run only cleared
for `DRC-native`. Expectation: FreeBSD matches Linux, and rate mismatch does not
explain the difference.

### The original plan (kept for the record; superseded by the procedure doc)

1. Confirm the guard now **reports** a mismatch when one exists: bring the chain
   up at 192 kHz, play 44.1 kHz material through `DRC-native`, and check
   `drc.sh status` prints `[MISMATCH]`. Then the converse — matched rates must
   print `[match]`, or the guard is useless in the other direction.
2. Establish what the resampling actually **does** to the signal. The resampling
   happens upstream of the DAC (in MPD — see the correction above), so tap the
   loopback rather than the USB wire:
   `./verify-bitperfect.sh --source mpd:DRC-native --tap loop:/dev/dsp.loop`.
   Compare a rate-matched run against a rate-mismatched one.
3. Exercise **16-, 24- and 32-bit** material, not just one depth.
4. Decide the fix: either make `DRC-native` refuse to play at a rate
   `virtual_oss` is not running at, or restart `virtual_oss` on a rate change,
   or route mixed-rate playlists through `resamp` mode (MPD's soxr "very high")
   deliberately.

### Can this be done with a different DAC — the one at the office?

**Yes, for the guard test itself, and the DAC is genuinely irrelevant to it.**
Verified by reading what the guard actually compares (`drc.sh:1210-1219`): MPD's
reported rate from `mpc status` against the `-r` argument on the running
`virtual_oss` process command line. It is **pure software state — the DAC is not
part of the comparison at all** on FreeBSD. And the resampling under
investigation happens upstream of the DAC (in MPD, per the correction at the top
of this section), so tapping `/dev/dsp.loop` characterises it without the DAC
being involved either. (In the event the Linux half measured it on the USB wire
with `resampler-residual.py`, which is also DAC-independent because it fits a
tone rather than comparing bytes.)

Three caveats, none of them blocking:

1. **The office DAC must support the rates you want to exercise** — you need at
   least two different rates to create a mismatch, e.g. 44.1 k and 96 k. It does
   not need 192 kHz; any two supported rates that differ will do. Check with
   `cat /dev/sndstat` and the descriptors before starting.
2. **Do not byte-compare captures taken on a different DAC against the OKTO
   ones.** If the office DAC declares a **3-byte** container (`S24_3LE`) rather
   than the OKTO's 4-byte one, the same audio travels as `b0 b1 b2` instead of
   `00 b0 b1 b2` — identical audio, different byte stream — and no normalisation
   for that is implemented. Captures are only comparable within one container
   width. This does not affect the guard test, which is not a byte comparison.
3. **FreeBSD may land on a different alt-setting** on another device, since it
   picks by `bSubslotSize * 8` and keeps the first alt per rate. Fine for the
   guard test; just record what it chose rather than assuming alt 1.

So: leave the OKTO where it is. What you cannot do at the office is repeat §6.8 —
the cross-OS null needs the same DAC on both sides — but that is already done.

---

## 10. What else remains open

- **Downstream of the wire.** The DAC's own clocking and analogue behaviour, host
  USB electrical noise and leakage, mains grounding. These are the only physical
  mechanisms left by which the host could affect the analogue result, and they
  are *electrical, not computational*. The OKTO is well defended against them —
  async USB, its own clock, explicit feedback endpoint (`0x81`, 16.16 format,
  momentary freq 192005), so the host is a byte pump whose timing the DAC does
  not follow.
- **Not in the equipment.** Level matching and expectation. Worth stating plainly
  because the measured digital difference is now exactly zero, while the reported
  perceptual difference is large.
- **The provenance bug of §7 is unfixed.** `deploy_filter.py` still makes the
  deployed filter a function of the installing host's SoX build. The right fix is
  for the deploy to ship the coefficients the design produced — whose sha256 the
  bundle manifest already records — instead of regenerating them. The null in
  §6.8 worked around this rather than benefiting from a fix.
- **The DRC path has never been wire-verified against a *source*** on either OS,
  only against the other machine. The method for that (unit-impulse filter,
  making the convolution an identity) is in `BIT-PERFECT-VERIFICATION.md`.

---

## 11. On the A/B methodology: don't split onto two machines

The question raised was whether to dedicate the NUC to FreeBSD and run Arch on a
~10-year-old laptop, so both could run at once and switching would be fast.

**The premise needs correcting first.** This NUC is an **Intel i3-6100U
(Skylake-U, 2015)** — 2 cores / 4 threads, 15 W. It is itself about eleven years
old. A ten-year-old laptop is the same generation, and a quad-core H-series
laptop of that era may well be *faster*. The comparison being imagined is not the
one that would actually be made.

**Compute is not the constraint.** Measured on this machine: BruteFIR convolving
524 288 taps in stereo at 44.1 kHz costs **3.1 % CPU**. Scaling to 192 kHz is
roughly 4.4× → ~13 %. Any machine of that generation handles it; xruns and
thermal throttling are unlikely to be the problem. (Verify on the laptop with the
panel's BruteFIR CPU card before trusting it.)

**But splitting would give up the one thing this setup has.** Dual-boot on one
machine means identical USB controller, PSU, cable, chassis grounding and mains
outlet — the operating system is the *only* variable. That is a genuinely clean
experiment and it is rare. Two machines introduces a different USB host
controller and its electrical noise, a different PSU and grounding, possibly a
second mains outlet and a ground loop, plus different CPU timing — every one of
which you would then have to exclude before attributing anything to the OS.

**And the deeper point: fast switching does not fix the real problem.** Echoic
memory for timbre is a few seconds, so a two-minute reboot does make live A/B
unreliable. But the fix is not two machines — it is not doing the comparison by
ear in real time. §6.8 is the better instrument, and it has now returned a
definitive answer: the bytes are identical, so **no listening test can
distinguish the two systems on the basis of anything digital**, however fast the
switching. Buying fast switching by introducing a second USB controller and mains
path would mean measuring the confounds you just added.

If a listening test is still wanted, do it on **captured recordings** — level
matched, software ABX, instant switching, blind, repeatable, no reboot. The
hardware for a full-chain analogue capture is already present: the DAC8STEREO
exposes a capture interface (interface 2, alts at 24/16/32-bit), so its analogue
output can be looped back in and what actually leaves the DAC captured on each
OS, analogue included.

---

## 12. Switching between the two systems

Relevant because it is how any A/B gets done. The panel's **Reboot to FreeBSD**
button was broken and is now fixed; two separate defects:

1. The command was declared as `cmd = grub-reboot-to-freebsd.sh` with **no
   `sudo`**, while the helper guards itself with
   `[ "$(id -u)" -eq 0 ] || { echo "Must run as root."; exit 1; }`. It exited 1
   before touching GRUB, so the click appeared to do nothing. `[reboot]` directly
   above it already had the right shape.
2. The helper used `grub-reboot`, which is **one-shot**: it sets `next_entry` and
   GRUB sets `boot_once`, and the generated `savedefault` is guarded by exactly
   that flag — so the choice was deliberately forgotten on the next boot. The
   intended behaviour is the opposite. It now uses `grub-set-default`, so the
   switch is **sticky**: FreeBSD stays the default until something changes it.

The return path needs no FreeBSD-side script and no configuration change. Every
menuentry calls `savedefault`, and `/etc/default/grub` already carries
`GRUB_DEFAULT=saved` with `GRUB_SAVEDEFAULT=true`, so picking "Arch Linux" at the
menu writes it back as the new default:

```
Linux → click the button   → saved_entry = FreeBSD,    sticky
FreeBSD → pick Arch Linux  → saved_entry = Arch Linux, sticky
```

The helper is now version-controlled (`scripts/grub-reboot-to-freebsd.sh`) and
installed — previously it existed only as a hand-placed file in `/usr/local/bin`
that nothing in the project installed, so a rebuilt machine got a button that
could never work. It also gained `-c` (check the prerequisites and print the
current default), `-n` (set the default without rebooting), and a read-back of
`saved_entry` after writing so it refuses to reboot if the write did not take.

---

## 13. Summary table — what is excluded, and how

| Candidate | Status | Evidence |
|---|---|---|
| Wrong sample container | **excluded** | descriptors + both hosts on alt 1, 4-byte `S32_LE` |
| Wrong justification / byte shift | **excluded** | direct-path wire hashes identical across OSes (§6.1) |
| Wrong alt-setting on FreeBSD | **excluded** | `SET_INTERFACE wValue=1 wIndex=1` on the wire (§6.3) |
| Kernel feeder / volume / resampler | **excluded** | `feeder_root` only, `BITPERFECT` set, with a control (§6.4) |
| 24-bit 3-vs-4-byte stride ambiguity | **excluded** | MPD never negotiates a 24-bit OSS format (§6.5) |
| Xruns, dropouts, nondeterminism | **excluded** | FreeBSD self-null, 0 differing samples (§6.6) |
| BruteFIR partitioning difference | **excluded** | bit-identical, 0 differing samples (§6.7) |
| `virtual_oss` vs `snd-aloop` bridge | **excluded** | cross-OS null IDENTICAL (§6.8) |
| The convolver itself | **excluded** | same, §6.8 |
| Different filter coefficients | **real, but inaudible** | −103 dB, all above 20 kHz (§7) |
| Resampling on rate mismatch — Linux | **excluded** | MPD soxr, +2.5…+4 dB above the 32-bit floor (§9, `RATE-MISMATCH-PROCEDURE.md`) |
| **Resampling on rate mismatch — FreeBSD** | **OPEN — procedure ready** | documented as MPD soxr (virtual_oss(8), no `-S`); not yet measured; soxr presence and the `resamp` 24-bit path are FreeBSD-only risks |
| DAC clocking / analogue / USB noise | open, not yet probed | §10 |
| Level matching / expectation | open, not yet probed | §10 |

---

## Appendix A — artifacts

| File | What |
|---|---|
| `bp-results/dac-format-linux.json` | Linux format probe, all three hops live |
| `bp-results/dac-format-freebsd.json` | FreeBSD format probe |
| `bp-results/null-192000-fbsd-run1/run2.json` | FreeBSD captures, `8192,64` |
| `bp-results/null-192000-fbsd-part32768.json` | FreeBSD capture, `32768,16` |
| `bp-results/null-192000-fbsd-selfnull.json` | FreeBSD self-null, IDENTICAL |
| `bp-results/null-192000-partitioning.json` | partitioning null, IDENTICAL |
| `bp-results/null-192000-arch-fbsdcoef.json` | Linux capture on FreeBSD coefficients |
| `bp-results/null-192000-xos-fbsdcoef.json` | **the cross-OS null, IDENTICAL** |
| `bp-results/bitperfect-test-*.txt` | direct-path wire captures, both OSes |

`.wire.raw` streams are deliberately not tracked (tens of MB each); the `.json`
and `.txt` reports carry the hashes and verdicts, which is all a comparison
needs.

## Appendix B — tools

| Tool | Purpose |
|---|---|
| `scripts/dac-format-probe.py` | cross-OS format/alignment probe; `--compare` diffs two runs |
| `scripts/bitperfect_runner.py` | plays material and taps the USB wire; `--route drc`, `--card N` |
| `scripts/bitperfect-null.py` | nulls two captures against each other |
| `scripts/bitperfect-compare.py` | compares direct-path taps by hash |
| `scripts/compare-filter-resamplers.py` | measures two coefficient sets against their source |
| `scripts/verify-bitperfect.sh` | per-stage taps, including `loop:/dev/dsp.loop` |
| `tests/gen-bitperfect-wav.py` | deterministic test material, identical on both OSes |

## Appendix C — key hashes

```
input material, 192000/24/10s     01317af6523ec67f74834361ce47df63438f8892d5238f0b2c08239eec903320
BruteFIR config (both hosts)      c2ae7dec1e7ce0c1136229c5a33bb29238675011e385e05202d7d04eb243743d
coefficients, FreeBSD  L / R      0b3a9c24…  /  a69dd603…
coefficients, Arch     L / R      28a9c5ba…  /  d0cc9b57…
source impulses (both) FLX / FRX  bad43a63…  /  e52569b6…
direct-path wire, 44100/S32       02905a1e322dc6e1…  (identical on both OSes)
```
