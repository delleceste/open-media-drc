# Verifying the DAC receives bytes PERFECTLY UNCHANGED

This document explains how to prove that the audio bytes written on the host
arrive at the OKTO DAC8 **bit-for-bit identical**, and how the verification tool
(`scripts/verify-bitperfect.sh`) is implemented.

> **Scope.** This verifies the **direct, bit-perfect path** (MPD `OKTO-DAC`
> output → `/dev/dsp.dac`). The **DRC path** (… → brutefir → `/dev/dsp.dac`) is
> *intentionally not* bit-perfect: brutefir convolves the FIR room-correction
> filter, so its output is *supposed* to differ from its input. See
> "[Testing the DRC path](#testing-the-drc-path)" for how to check that path's
> transparency separately.

---

## Why a USB tap is the only true test here

The OKTO DAC8 is **play-only**. Its UAC2 capture interface is deliberately
disabled by our `uaudio(4)` patch (see `freebsd-uaudio-patch/`), so you **cannot
loop audio back through the DAC** to compare what came out. The only place to
observe *the exact bytes the DAC actually receives* is the **USB wire** between
host and DAC.

The DAC's USB endpoints (from `usbconfig -d ugenB.D dump_all_config_desc`):

| Endpoint | Direction | Type | Purpose |
|----------|-----------|------|---------|
| `0x01`   | OUT       | async isochronous | **playback audio (S32_LE)** ← the bytes we check |
| `0x81`   | IN        | isochronous | explicit async feedback (Q16.16 rate) |
| `0x82`   | IN        | async isochronous | capture — *present in firmware, ignored by the patched driver* |

The payload of the `0x01` OUT transfers is exactly the interleaved
`S32_LE` stereo stream the DAC's converter consumes — no extra framing. Capture
it with `usbdump(8)`, concatenate every OUT payload, and byte-compare to the
source file. A long zero-difference run = bit-perfect.

---

## Two levels of check

### 1. Structural check (fast, kernel-certified)

FreeBSD's sound system will tell you whether *any* converting stage is in the
path. While the **direct** output is playing:

```sh
sysctl hw.snd.verbose=2
cat /dev/sndstat
```

A bit-perfect path shows the play channel with the `BITPERFECT` flag and a
feeder graph of **exactly**:

```
{userland} -> feeder_root(0x00201000) -> {hardware}
```

If you instead see a `feeder_rate`, `feeder_volume`, `feeder_float`, or any
format-conversion node, the kernel is altering the bytes. Preconditions that
keep the graph clean (both already set on this host):

```sh
bitperfect = 1     # first opener's format becomes the hardware format
play.vchans = 0    # no virtual-channel mixer/resampler in front of the DAC

# on the DAC's own pcm unit — asserted on every attach by omdrc_audio
# (omdrc_audio_dac_sysctls), since a replug rebuilds dev.pcm.<unit>.* from
# driver defaults.  Read the unit back from the link when you need the OID:
#   sysctl dev.pcm.$(readlink /dev/dsp.dac | tr -dc 0-9).bitperfect
```

This check is definitive for the **host** portion of the path. It does not, by
itself, prove the USB layer is transparent — that's what level 2 adds.

### 2. Empirical wire tap (gold standard, end-to-end)

`scripts/verify-bitperfect.sh` performs the full proof:

```sh
# free the DAC first (single-open device):
./drc.sh off            # stop virtual_oss/brutefir, enable direct output
# stop whatever renderer holds the DAC (e.g. upmpdcli / mpd) if needed

sudo ./scripts/verify-bitperfect.sh             # 44100 Hz, ~4.5 s
sudo ./scripts/verify-bitperfect.sh 88200       # pick the rate to test
sudo ./scripts/verify-bitperfect.sh 96000 50000 # rate, frame count
```

Expected success line:

```
BIT-PERFECT: <N> contiguous bytes identical on the USB wire ✔
```

Any divergence prints the source vs wire bytes at the first differing offset.

**Verified on this host (FreeBSD 15.1-RC1, OKTO DAC8, patched uaudio):**

```
44100 Hz : BIT-PERFECT: 798752 contiguous bytes identical on the USB wire ✔
88200 Hz : BIT-PERFECT: 800000 contiguous bytes identical on the USB wire ✔
48000 Hz : BIT-PERFECT: 768016 contiguous bytes identical on the USB wire ✔
```

**Validated on the live `musicpd` instance (real MPD → real chain):**

| Test | Path | Tap | Result |
|------|------|-----|--------|
| A | MPD `OKTO-DAC` → `/dev/dsp.dac` → DAC | USB endpoint `0x01` | **BIT-PERFECT**, 0 slips, 0 corruption |
| B | MPD `DRC-native` → `/dev/dsp.play` → virtual_oss | `/dev/dsp.loop` | **VALUE-EXACT**, 0 slips (brutefir stopped so the tap owns the loopback) |

Test A was also confirmed from the kernel side during a sustained playback:
`/dev/sndstat` showed `[dsp0.play.0]: spd 44100 ... <RUNNING,...,BITPERFECT>`,
`underruns 0`, fed by `musicpd`, with the feeder graph `{userland} ->
feeder_root -> {hardware}` (no conversion node) — and the DAC front panel visibly
switched to play at 44.1 kHz. The test WAV is `tests/bitperfect-test-44100-s32-stereo.wav`,
fed via a `file://` URL over a temporary MPD local socket (no music dir needed).

> The wire stream carries a few thousand extra leading bytes vs the source —
> these are OSS stream-priming zeros emitted before the first written sample;
> the tool aligns past them and compares the real overlap. Each isochronous OUT
> transfer carries `NFR=64` microframe fragments of ~40/48 bytes (5–6 stereo
> S32 frames per 125 µs microframe at 44.1 kHz); the decoder concatenates them
> back into the contiguous sample stream.

---

## How the tool is implemented

The script is self-contained (it embeds a small C program and a Python decoder
via here-docs). Steps:

1. **Locate the DAC dynamically.** USB addresses change across replugs, so the
   ugen/bus/devaddr is read from `sysctl dev.uaudio.0.%location` rather than
   hard-coded.

2. **Sanity-print the bit-perfect knobs** (`bitperfect`, `play.vchans`) and
   refuse to run if the DAC is already held by another process (`fuser`).

3. **Generate a deterministic test signal** (`S32_LE`, stereo). By default it is
   a **near-silent (~−90 dBFS) per-sample counter** in the low 16 bits, with the
   L and R channels distinct (catches channel swap/duplication). This is
   inaudible yet *maximally sensitive*: truncation, dithering, a non-unity
   volume, or resampling all corrupt the low bits deterministically. Set
   `FULLSCALE=1` for a full-range pseudo-random signal instead — **loud, so
   disconnect the amplifier first.**

4. **Play it bit-perfectly** with an embedded C writer that opens `/dev/dsp.dac` and
   sets the format explicitly:

   ```c
   ioctl(fd, SNDCTL_DSP_SETFMT,  &fmt);   /* AFMT_S32_LE */
   ioctl(fd, SNDCTL_DSP_CHANNELS,&ch);    /* 2 */
   ioctl(fd, SNDCTL_DSP_SPEED,   &sp);    /* rate */
   ```

   Crucially, after each `ioctl` it **checks the returned value** and **aborts**
   if the kernel coerced the format/channels/rate to something else. A coercion
   means a converting feeder would be inserted — i.e. *not* bit-perfect — so the
   writer fails loudly instead of silently producing altered audio. It then
   `write()`s the raw file straight through and `SNDCTL_DSP_SYNC`s.

5. **Tap the wire in parallel.** Before playing, it starts:

   ```sh
   usbdump -i usbusB -f DEVADDR -s 65536 -w cap.pcap
   ```

   capturing all transfers for the DAC (control + feedback + audio) to a pcap.

6. **Decode and compare** (Python). It runs `usbdump -r cap.pcap -vv` and parses
   the text records. The relevant record layout (learned empirically) is:

   ```
   HH:MM:SS.uuuuuu usbusB.D SUBM-ISOC-EP=00000001,SPD=HIGH,NFR=1,SLEN=...,
    frame[0] WRITE <N> bytes
    0000  de ad be ef 01 02 03 04  05 06 07 08  -- -- -- --  |............    |
    flags 0 <0>
   ```

   The decoder:
   - keys on the record header `EP=00000001` (the OUT submissions; `0x81`
     feedback and `0x80` control are skipped);
   - within a `WRITE` frame, reads the hex dump lines, drops the trailing
     `|ascii|` column and the `--` placeholders, and appends the real bytes;
   - concatenates all OUT payloads into the **wire byte stream**.

   It then finds a probe slice of the source inside the wire stream (to absorb
   any partial first/last USB frame or capture start offset), aligns on it, and
   counts the contiguous identical bytes. All-identical over the overlap ⇒
   **bit-perfect**; otherwise it reports the first mismatch with hex context.

The decoder is unit-tested against a synthetic record (multi-line and
single-line frames, with control/feedback records interleaved) and reconstructs
the payload exactly.

### Failure modes the tool catches

| Failure | How it shows up |
|---------|-----------------|
| OSS would resample (rate not honored) | writer aborts: `rate coerced … -> resampling -> NOT bit-perfect` |
| OSS would change format / channels | writer aborts: `format/channels coerced … -> NOT bit-perfect` |
| A volume/format feeder alters samples | wire bytes differ → `MISMATCH at source offset …` |
| L/R swapped or duplicated | distinct L/R counter → probe not found / mismatch |
| vchan mixer/dither in the path | low bits differ → mismatch |

---

## Clock domains: free-running sinks, real-time pacing, and flow control

This is the subtle part, and it is why the test *method* matters as much as the
result.

**A digital audio sink consumes samples on a clock.** `/dev/dsp.dac` (the OKTO DAC)
consumes at the DAC's quartz crystal — exactly `rate` samples per second, no more,
no less. `virtual_oss` started with `-f /dev/null` (as `drc.sh` does) has **no
hardware clock**, so it consumes at a **software timer** it generates itself — a
"free-running" clock that is *approximately* `rate` but is its own independent
time base.

**How a producer stays in step: flow control (back-pressure).** A well-behaved
sink exposes a small buffer. When you `write()` faster than the sink drains, the
buffer fills and the next `write()` **blocks** until space frees up. That block
is the sink throttling the producer to *its* clock. So:

- Writing to **`/dev/dsp.dac`** → the kernel/USB stack blocks your `write()`s in
  lockstep with the DAC crystal. The producer is **slaved to the DAC clock**.
  No drift is possible; the bytes can only arrive bit-exact (proven by the USB
  tap).
- A program (MPD, or any player) writing to **`/dev/dsp.play`** is likewise
  blocked by virtual_oss's buffer and thus **slaved to virtual_oss's software
  clock**. One producer, one clock → no drift, no slip.

**Why a flat-out `write()` loop is *not* paced.** Our standalone writer just
loops `read(file) → write(dsp)`. If the device gave perfect back-pressure that
would be fine — and on `/dev/dsp.dac` it is. But virtual_oss's play device let our
writer dump the whole buffer's worth and return *faster than real time*
(measured: 480 KB "played" in 0.17 s instead of 1.36 s). With no throttle, the
writer overran virtual_oss's free-running consumer and most samples were dropped.
Adding manual `clock_nanosleep` pacing (`--paced`) helped — but now there are
**two independent clocks**: our `CLOCK_MONOTONIC` schedule and virtual_oss's
timer. They drift by tens of ppm plus scheduler jitter, so every second or so a
buffer over/underruns and a **single sample is dropped or duplicated** — a
*timing slip*. Crucially, the slip changes *when* samples arrive, never *what*
they are: every sample that gets through is bit-identical.

**Why MPD does it right.** MPD does not run on its own clock — it `write()`s and
**blocks on virtual_oss's buffer**, so it is flow-controlled by (slaved to) the
same clock that drains the data. One clock governs both ends → no drift → no
slip. Measured end-to-end **MPD → `/dev/dsp.play` → virtual_oss → `/dev/dsp.loop`**:

```
value-matched 175376 frames (~4 s), slips=0, corrupt=0   => VALUE-EXACT
```

So **virtual_oss as configured is bit-transparent**: with a properly
flow-controlled producer it neither alters nor drops a single sample. The slips
in the synthetic-writer test were the *test harness's* fault, not virtual_oss's.

**The one caveat for the real DRC chain.** In playback, brutefir reads
`/dev/dsp.loop` (virtual_oss's free-running clock) and writes `/dev/dsp.dac` (the
DAC crystal) **without resampling**. Those two clocks are not the same, so over
long runs brutefir must occasionally drop/duplicate one sample at the DAC to
reconcile them — an inaudible slip every several minutes, on top of the
intentional FIR convolution. Sample *values* are never altered; only the direct
path is sample-count-exact indefinitely.

### Feeding MPD for a whole-chain test

`--source mpd:OUTPUT` enables an MPD output by name and plays a generated WAV
(the WAV's PCM payload is byte-identical to the raw — MPD cannot play headerless
raw, so the bytes are wrapped in a header-only WAV). It backs up and restores
your MPD queue and output enables.

To test **without touching your running MPD/library** (recommended), run a
throwaway MPD on its own port and music dir — this is exactly how the result
above was produced:

```sh
mkdir -p /tmp/mpdtest                    # put src.wav here (32-bit PCM WAV)
cat > /tmp/mpdtest/mpd.conf <<EOF
music_directory "/tmp/mpdtest"
db_file "/tmp/mpdtest/db"
pid_file "/tmp/mpdtest/pid"
bind_to_address "127.0.0.1"
port "6610"
audio_output { type "oss" name "tap" device "/dev/dsp.play" mixer_type "none" }
EOF
/usr/local/bin/musicpd /tmp/mpdtest/mpd.conf
mpc -p 6610 update --wait
mpc -p 6610 add src.wav
# start the loopback reader, then: mpc -p 6610 play
```

(Requires virtual_oss running — e.g. `./drc.sh 44100` — so `/dev/dsp.play` and
`/dev/dsp.loop` exist. brutefir need not run: the loop is read directly.)

## Testing the DRC path

The DRC path applies the FIR filter, so it is not byte-equal by design. To check
that the *non-correction* parts of that path (virtual_oss bridge, S32 container,
brutefir I/O) are transparent, generate a **unit-impulse** filter
(`L.raw`/`R.raw` = a single `1.0` FLOAT64 sample followed by zeros, with the
config's `attenuation` set to `0`), run the DRC chain, tap `0x01`, and compare:
the wire stream should equal the source delayed by the filter latency. That
isolates everything except the (now trivial) convolution.

The most realistic precision risk on the DRC path is **not** the convolution but
a **sample-rate mismatch**: `virtual_oss` runs at a fixed `-r <rate>` while MPD's
`DRC-native` output keeps the source rate (`*:*:*`). If the selected DRC rate
does not match the track, `virtual_oss` silently resamples with its built-in
(non-soxr) resampler. `./drc.sh status` flags this as `MISMATCH` — treat that as
a hard stop, or use `resamp` mode (MPD's soxr "very high") for mixed-rate
playlists.

---

## Cross-OS comparison: the same bytes on Linux and FreeBSD

The suite `scripts/bitperfect-tap-linux.sh` / `scripts/bitperfect-tap-freebsd.sh` /
`scripts/bitperfect-compare.py` answers a stronger question than the per-host
proof above: **do Linux and FreeBSD send the *same* bytes to the DAC for the
same input file?**

Both tap scripts have the same CLI and produce the same artifacts:

```sh
# Linux (usbmon tap; sudo asked internally for the tap only)
./scripts/bitperfect-tap-linux.sh tests/bitperfect-test-44100-s32-stereo-30s.wav

# FreeBSD (usbdump tap; free the DAC first: ./drc.sh off)
./scripts/bitperfect-tap-freebsd.sh tests/bitperfect-test-44100-s32-stereo-30s.wav

# compare the two outputs (either machine)
./scripts/bitperfect-compare.py \
    bp-results/bitperfect-test-44100-s32-stereo-30s-linux.wav \
    bp-results/bitperfect-test-44100-s32-stereo-30s-freebsd.wav
```

Each run plays the input flat-out to the DAC (aplay on a `hw:` device /
the format-guarded OSS writer on `/dev/dsp.dac` — both refuse any conversion),
captures every isochronous OUT transfer to endpoint `0x01`, concatenates the
payloads, aligns them to the source and writes:

- `PREFIX.wav` — the tapped wire bytes, source-aligned and source-length,
  as a WAV. **For a 32-bit input, a bit-perfect chain makes this file
  byte-identical to the input WAV** (same sha256), so two bit-perfect OSes
  trivially produce identical outputs. For a **16/24-bit input the two
  files necessarily differ**: `PREFIX.wav` holds the promoted 32-bit wire
  container, so it is both longer and differently valued than the source
  (e.g. the 44100/24-bit asset is 7938044 bytes, its tap WAV 10584044).
  The invariant that always holds — and the one the comparator uses — is on
  the *payload* sha256 reported as `tap wav`, not on the file sha256.
- `PREFIX.wire.raw` — the full untrimmed wire stream (priming bytes and all).
- `PREFIX.txt` — the report. It **names and hashes every stage**, so a run
  can be audited from the report alone, without the payloads:

  ```
  input file : tests/bitperfect-test-44100-s32-stereo-30s.wav  (10584044 bytes, sha256 88d365ee…)
  ref bytes  : 10584000  sha256 02905a1e…
  wire raw   : bp-results/…-freebsd.wire.raw  (10772776 bytes, sha256 ee246f0d…)
  tap wav    : bp-results/…-freebsd.wav  (10584000 PCM bytes, sha256 02905a1e…)
  verdict    : BIT-PERFECT — all 10584000 reference bytes identical on the USB wire
  ```

  `input file` is the source hashed **as it sits on disk**, header included
  — the same digest `gen-bitperfect-wav.py` prints and `tests/README.md`
  tabulates, so the exact asset a run used is identifiable afterwards.

  **Which of these are reproducible, and which are not:** `input file`,
  `ref bytes` and `tap wav` are deterministic — two bit-perfect runs, on
  either OS, reproduce them exactly. `wire raw` is **not**: it is the
  untrimmed capture, so besides the audio it holds the priming zeros, the
  silence pad and however many trailing packets the tap happened to record
  before it was stopped. It is a provenance/debugging record, **never** a
  cross-OS comparison key — Linux and FreeBSD captures of the same input
  legitimately differ in length (10584816 vs 10772776 at 44100/32-bit).
  The field the comparator uses is `tap wav`. What exactly surrounds the
  audio, and why none of it is audible, is measured in
  ["What surrounds the audio"](#what-surrounds-the-audio-priming-zeros-the-pad-and-where-the-boundaries-fall).

**Comparing across machines without moving 10 MB files.** The big
artifacts (`.wav`, `.wire.raw`) are gitignored; only the ~600-byte
`PREFIX.txt` reports are tracked. That is enough: the report records the
tap payload's exact length and sha256, and sha256 equality on equal-length
payloads proves byte-identity just as strongly as `cmp`. The comparator
accepts reports directly, in any combination:

```sh
# on the FreeBSD box: run the tap, commit the report
./scripts/bitperfect-tap-freebsd.sh tests/bitperfect-test-44100-s32-stereo-30s.wav
git add bp-results/bitperfect-test-44100-s32-stereo-30s-freebsd.txt && git commit -m 'bp: freebsd tap report'

# on the Linux box: pull, run the tap, compare local payload vs remote hash
./scripts/bitperfect-tap-linux.sh tests/bitperfect-test-44100-s32-stereo-30s.wav
./scripts/bitperfect-compare.py \
    bp-results/bitperfect-test-44100-s32-stereo-30s-linux.wav \
    bp-results/bitperfect-test-44100-s32-stereo-30s-freebsd.txt   # or .txt vs .txt
```

Only in the MISMATCH case — when you want the first differing offset and
hex context — do the actual `.wav` payloads need to be brought onto one
machine (scp, or a temporary `git add -f`).

Notes:

- **The common input** is the 30 s generated WAV (see `tests/README.md`);
  its per-sample-unique signal keeps alignment unambiguous. Any 16/24/32-bit
  PCM WAV works, though: 24-bit (e.g. 192k/24 material) is promoted
  **losslessly** to the 32-bit wire container (low byte zeroed) inside the
  script, identically on both OSes, because both DACs only expose S32_LE
  USB altsettings.
- **The result does not depend on the DAC model**, only on the negotiated
  wire format. The isochronous payload is the raw interleaved PCM stream;
  packet sizes/boundaries differ per DAC (async feedback pacing) but vanish
  when the payloads are concatenated. Two DACs that both take S32_LE
  containers (OKTO DAC8, DacMagic 100) yield directly comparable — and, if
  both hosts are transparent, identical — byte streams.
  The one thing that *would* break byte comparability is a DAC whose USB
  descriptors declare a **3-byte sample container** (S24_3LE): a 24-bit
  sample then travels as `b0 b1 b2` instead of `00 b0 b1 b2` — same audio
  bits, different byte stream — so its capture would mismatch a 4-byte
  capture on every sample until the pad bytes are stripped/inserted
  (normalization). No such normalization is implemented, since both DACs
  in use expose only 32-bit altsettings.
**Validation status of the suite itself** (distinct from the older
`verify-bitperfect.sh` results quoted earlier in this document):

| Script | Status |
|---|---|
| `bitperfect-tap-linux.sh` | **Executed and passing** on the Linux host (DacMagic 100, kernel 7.1.5-arch1): 44100/32-bit × 30 s, 44100/24-bit × 30 s and 192000/24-bit × 10 s all **BIT-PERFECT**, 0 truncated events, 0 usbmon drops. Note a 16/24-bit input does not change the playback path — `prep` promotes to the 32-bit wire container first, so the player always emits S32_LE and only the sample *values* differ (`<<8` / `<<16`). |
| `bitperfect-compare.py` | **Executed** on all comparison paths (wav↔wav, wav↔txt, txt↔txt, refusal of raw↔txt), including a deliberately bit-flipped payload to confirm MISMATCH is reported at the exact offset. |
| `bitperfect-tap-freebsd.sh` | **Executed and passing** on the FreeBSD host (Cambridge Audio DacMagic 100, `usbus0` devaddr 2, FreeBSD 15.1-RELEASE): 44100/32-bit × 30 s, 44100/24-bit × 30 s and 192000/24-bit × 10 s all **BIT-PERFECT** (exit 0). For 44100/32-bit, `bitperfect-compare.py` also reports MATCH against the committed Linux report for the same input; the 24-bit pair has no committed Linux counterpart yet, so those two stand as local proofs only. The 192 kHz run confirms the DAC clock actually followed (`dev.pcm.0.feedback_rate` = 191994) and cost 23.7 s wall clock end to end. The first run exposed one real defect — a truncated capture, fixed by the trailing silence pad described in ["Anatomy of a tap run"](#anatomy-of-a-tap-run-refraw-the-pad-and-the-cut) below. |

Preconditions for the FreeBSD run, beyond a free DAC
(`./drc.sh off` plus stopping any renderer): `dev.pcm.N.bitperfect=1` and
`dev.pcm.N.play.vchans=0`, as in
"[Structural check](#1-structural-check-fast-kernel-certified)" above. The
embedded writer re-checks this at runtime and aborts rather than play
converted audio, so a missing knob shows up as an explicit `FAIL:`, never
as a silent mismatch.

If the FreeBSD run does not print BIT-PERFECT, its `PREFIX.txt` carries the
classified verdict (value corruption vs timing slip vs incomplete capture
vs head lost) — commit that report; it is enough to diagnose from the other
machine without moving any payload.

**Start with the canonical 44100 Hz asset.** The FreeBSD tap decodes
`usbdump -vv` *text*, so the parsing cost scales with the capture: 30 s at
44100 Hz is ~10.5 MB of payload arriving as tens of MB of hex-dump text
(fine), while 30 s at 192 kHz is ~46 MB of payload as several hundred MB
of text — slow, and it stresses the pcap capture too. Prove the path at
44100 first, then use a shorter duration for the high-rate case
(`--frames 1920000` = 10 s at 192 kHz). The Linux side reads usbmon's
binary interface and has no such limit.

### Anatomy of a tap run: `ref.raw`, the pad, and the cut

This section walks the FreeBSD run end to end, because two of its steps are
easy to misread: what exactly is being compared (`ref.raw`, *not* the input
WAV), and why the player emits half a second of silence that the verdict
never sees.

#### Step 1 — `ref.raw`: the reference is the wire container, not the file

`ref.raw` is the **byte stream the DAC is expected to receive**: the input
WAV's PCM payload, header stripped, promoted to the S32_LE container the
USB altsetting actually carries. It is produced by `bitperfect-lib.py prep`
(`cmd_prep`, `bitperfect-lib.py:91`) and is the *only* thing the verdict
ever compares against:

```python
pcm = w.readframes(n)               # raw interleaved PCM, header stripped
if sw == 4:                         # already the wire container
    out = pcm
elif sw == 3:                       # S24_3LE -> 24-in-32 (value << 8)
    s = len(pcm) // 3
    out = bytearray(s * 4)          # zero-initialized -> pad bytes = 0x00
    out[1::4] = pcm[0::3]; out[2::4] = pcm[1::3]; out[3::4] = pcm[2::3]
elif sw == 2:                       # S16_LE -> 16-in-32 (value << 16)
    ...
print(rate, ch, sw * 8, n)          # parsed by the calling shell script
```

Three consequences worth internalising:

- **The 44-byte RIFF header is not part of the reference.** For a 32-bit
  input `ref.raw` is the WAV minus its header, which is why the canonical
  30 s asset (10584044 bytes on disk) yields `ref bytes : 10584000`.
- **Promotion is lossless and host-independent.** A 16- or 24-bit input is
  left-shifted into the 32-bit container by the *same Python* on both OSes,
  so the reference bytes are identical on Linux and FreeBSD by construction.
  A 16/24-bit run therefore does not exercise a different playback path —
  the player always emits S32_LE; only the sample values differ.
- **`prep` also returns the format**, which the shell reads into `RATE CH
  BITS FRAMES` (`bitperfect-tap-freebsd.sh:107`) and feeds to the writer's
  ioctls — so the format the DAC is opened with is derived from the file,
  never assumed.

#### Step 2 — the tap: every isochronous OUT payload, concatenated

`usbdump` captures the DAC's device address to a pcap, and the pcap is
re-read as `-vv` text and decoded by `cmd_decode_usbdump`
(`bitperfect-lib.py:267`). The filter is what makes the result a clean
audio stream: only submissions to endpoint `0x01`, only the WRITE frames.

```python
if hdr.search(line):
    in_out = "SUBM-ISOC-EP=00000001" in line
    in_frame = False
    continue
if "WRITE" in line and "frame[" in line:
    in_frame = True
```

Packet *boundaries* are discarded by concatenation. This is deliberate:
under async feedback the DAC pulls a varying number of frames per USB
microframe, so packet sizes differ from run to run and from host to host,
while the concatenated payload does not. That is precisely why a Linux and
a FreeBSD capture are byte-comparable at all.

#### Step 3 — the pad: what is *played* is not what is *compared*

The script plays `play.raw` = `ref.raw` + `PAD_MS` of digital silence, and
hands `finalize` the unpadded `ref.raw`:

```sh
PAD_BYTES=$(( RATE * CH * 4 * PAD_MS / 1000 ))
cp "$TMP/ref.raw" "$TMP/play.raw"
dd if=/dev/zero bs="$PAD_BYTES" count=1 status=none >> "$TMP/play.raw"
...
"$TMP/bpwrite" "$PLAY_DEV" "$RATE" "$CH" "$TMP/play.raw"      # padded copy
...
python3 "$LIB" finalize "$TMP/ref.raw" "$TMP/cap.raw" ...      # unpadded ref
```

At 44100/2ch that is 176400 bytes = 500 ms of zeros appended to a 10584000
byte reference.

**Why it is needed.** `usbdump` writes the pcap through a buffer, and
terminating it discards whatever has not been flushed — costing the last
few milliseconds of the capture. The first FreeBSD run hit exactly that:

```
wire bytes : 10583688
tap wav    : ... (10578040 PCM bytes, sha256 bce861c8…)
verdict    : INCOMPLETE — first 10578040 bytes identical but the capture
             ends 5960 bytes early (tap stopped before playback drained?)
```

5960 bytes = 745 frames = **16.9 ms** missing from the end. Note what the
verdict already told us: *every captured byte matched*. The playback path
was transparent; the capture simply stopped short. Padding moves that loss
into the silence, where nothing depends on it. Belt-and-braces, the tap is
now stopped with `SIGINT` rather than the default `SIGTERM` (giving
`usbdump` the chance to flush), and the drain sleep went 0.6 s → 1 s.

The Linux twin taps usbmon's *binary* interface, has never shown the loss —
its capture ran only 816 bytes longer than the reference and still returned
BIT-PERFECT, i.e. nothing was missing from the end — and is deliberately
left unchanged.

#### Step 4 — the cut: by length, never by silence detection

The pad is removed by **arithmetic, not by looking for zeros**. `finalize`
(`bitperfect-lib.py:420-431`) locates where the reference stream begins
inside the capture by searching for a 4 KiB probe of the reference, then
takes exactly `len(ref)` bytes from that point:

```python
po    = find_probe_offset(ref)
pos   = cap.find(ref[po:po + 4096])
start = pos - po
...
aligned = cap[start:start + len(ref) - refskip]
```

Everything before `start` (stream-priming zeros, capture lead-in) and
everything after `start + len(ref)` (the pad, plus any trailing packets) is
discarded by the same two lines. The pad needs no special handling and is
not mentioned in the verdict at all.

##### How the start of the audio is actually found

In plain terms: **the tool does not look for where the padding ends. It
looks for a piece of the reference it already knows, and works backwards
from it.**

The piece it looks for is called the *anchor* (`probe` in the code): 4096
consecutive bytes copied out of the reference. Since the tool knows the
anchor's offset *inside the reference* (`po`), finding the anchor inside
the capture (`pos`) immediately gives the position of reference byte 0:

```
start = pos - po
```

Here it is on the real 44100/32-bit run. The reference begins with the
counter signal, so the anchor is simply its first 4096 bytes (`po = 0`):

```
reference  ref.raw — 10584000 bytes, exactly what the DAC must receive
           ┌──────────────────────────────────────────────────────────┐
byte 0 ──► │ 00 00 00 00 00 00 00 00 │ 01 00 00 00 37 9e 00 00 │ 02 …  │
           └──────────────────────────────────────────────────────────┘
             frame 0: L=0  R=0         frame 1: L=1  R=40503
           └──────────── anchor = ref[0 : 4096] ────────────┘   po = 0

capture    wire.raw — 10772776 bytes, everything the tap recorded
           0            5648                        10589648   10772776
           ├─────────────┼───────────────────────────┼──────────┤
           │priming zeros│   A U D I O = reference   │pad+trail │
           │   5648 B    │        10584000 B         │ 183128 B │
           │ all 00      │ 00 00 …  01 00 00 00 37 9e│ all 00   │
           │ 16.01 ms    │                           │ 519 ms   │
           └─────────────┴───────────────────────────┴──────────┘
                         ▲
                         └─ anchor found here:  pos = 5648
                            start = pos − po  = 5648 − 0 = 5648
                            aligned = cap[5648 : 5648 + 10584000]
```

**Why not simply "skip the leading zeros"?** Because zeros can be data. A
reference may legitimately begin with silence — a quiet intro on real
music — and those zeros are part of what the DAC must receive. On the wire
they are byte-for-byte indistinguishable from the priming zeros in front of
them:

```
reference        │◄──── 8192 B of REAL silence ────►│◄── signal ──…
capture   │◄5648 B►│◄──── 8192 B of REAL silence ────►│◄── signal ──…
           priming
          └──────── 13840 consecutive 00 bytes ──────┘
                                    ▲
                    nothing in the bytes marks this boundary

anchor:  po  = 8192   ← first window with ≥ 256 non-zero bytes
found:   pos = 13840
start    = 13840 − 8192 = 5648   ✓ intro preserved as data
a "skip the zeros" rule would give   13840   ✗ intro eaten
```

That is what `find_probe_offset` (`bitperfect-lib.py:300`) is for: it steps
forward through the reference in 4 KiB windows until one contains **≥ 256
non-zero bytes**, i.e. is unmistakably signal, and takes the anchor there.
An all-zero anchor would match at the start of the zero run and misalign
the whole comparison — silently.

The same reasoning rules out trimming by silence detection in general: this
test signal is *itself* near-silent (~ −90 dBFS; only the low 16 bits of
each 32-bit sample are ever non-zero, which is visible in the byte dump
above — every fourth byte pair is `00 00`), so a silence-seeking rule would
cut into real payload.

**Why the anchor is unambiguous.** In the real capture the 4 KiB anchor
occurs **exactly once** — not "probably once". That is a direct payoff of
the generator's design: `R = (i*40503 + (i >> 16)) & 0xFFFF` folds the
block index into the right channel, making every (L,R) pair unique over the
whole file, so a 512-frame window cannot recur. The older 100000-frame
asset repeated every 65536 frames and could in principle have matched at
several offsets.

##### What if the anchor is not found?

Then the run is reported as an error and **no verdict is given**, because
there is nothing trustworthy to compare:

```python
if not cap:
    return out("NO CAPTURE — nothing seen on the USB wire", 2)
po  = find_probe_offset(ref)
pos = cap.find(ref[po:po + 4096])
if pos < 0:
    return out("ALIGNMENT FAILED — reference not found in the wire stream "
               "(gross corruption, wrong device, or capture gap)", 2)
```

The exit code carries the distinction, which is worth respecting if these
are ever wrapped in CI:

| exit | meaning | verdicts |
|---|---|---|
| 0 | judged, and the chain is transparent | `BIT-PERFECT` |
| 1 | judged, and something is wrong | `HEAD LOST`, `INCOMPLETE`, `VALUE CORRUPTION`, `TIMING SLIP(S)`, `UNDERRUN TAIL` |
| 2 | **could not judge** — capture unusable | `NO CAPTURE`, `ALIGNMENT FAILED` |

**A trap worth knowing.** Exit 2 does *not* guarantee the problem is your
capture setup. Aligning needs 4096 **consecutive intact** bytes; a defect
that alters *every* sample leaves no such run anywhere, so the search fails
before any comparison happens. Feeding `finalize` a capture with every
sample scaled by 0.5 — precisely what a volume feeder in the path would
produce, a serious bit-perfection failure — yields:

```
ALIGNMENT FAILED — reference not found in the wire stream
(gross corruption, wrong device, or capture gap)          exit 2
```

not `VALUE CORRUPTION`. At the alignment stage the tool genuinely cannot
tell "you tapped the wrong USB device" from "every sample was altered",
which is why the message names all three possibilities. **If you ever see
exit 2, open `PREFIX.wire.raw`:** junk or zeros point at the tap, while
plausible-looking audio points at a converting feeder in the playback path.
The failures that *do* get classified (exit 1) are the ones where enough of
the stream survives intact to anchor on: a bit flipped here and there, a
dropped or duplicated packet, a truncated capture.

Measured on the passing run, with the pad in place:

| quantity | bytes | meaning |
|---|---|---|
| capture (`wire.raw`) | 10772776 | everything usbdump recorded |
| lead-in before `start` | 5648 | OSS priming zeros — trimmed |
| `aligned` = reference | 10584000 | compared byte-for-byte → **BIT-PERFECT** |
| tail after the reference | 183128 | pad + post-close packets — trimmed |

The 5648-byte lead-in was **identical in the failing and the passing run**:
FreeBSD's OSS priming is deterministic, and the pad changed only the tail.
The surviving tail (183128 B ≈ 519 ms) slightly exceeds the 176400 B pad
because the kernel keeps the isochronous channel running briefly after the
writer closes, and the tap is still recording during the 1 s drain sleep.

#### What surrounds the audio: priming zeros, the pad, and where the boundaries fall

`wire raw` is always longer than the reference. Decomposing two real
captures at the alignment point shows what the extra material is — and,
importantly, that **all of it is zero**:

| | 44100/32-bit | 192000/24-bit |
|---|---|---|
| capture (`wire raw`) | 10772776 B | 16182800 B |
| head, before the audio | 5648 B — **all zero** | 24576 B — **all zero** |
| head as frames / time | 706 fr / **16.01 ms** | 3072 fr / **16.00 ms** |
| audio (`= ref bytes`) | 10584000 B | 15360000 B |
| tail, after the audio | 183128 B — **all zero** | 798224 B — **all zero** |
| tail as time | 519 ms | 520 ms |

**The head is the priming zeros.** An isochronous endpoint owns a fixed
slot in every USB (micro)frame: it cannot wait for data, it transmits
whatever the DMA ring holds when its slot comes up. At stream start the
ring is zero-filled and transmission begins before the first `write()` has
propagated through it, so the opening packets carry that zero fill. Note
the measurement: **exactly 16 ms at both rates** — different byte counts,
same duration. That is a fixed-duration buffer prime, not a timing
accident, which is why the head is reproducible run to run (5648 B in every
44100 run recorded here).

**The tail is the silence pad** (500 ms, [Step 3](#step-3--the-pad-what-is-played-is-not-what-is-compared))
**plus ~19 ms of packets the kernel keeps sending after the writer closes**,
and *this* is the part that wobbles. The tap is stopped by a wall-clock
`sleep`, which lands at an arbitrary point in the USB frame schedule, so
the last few packets fall inside or outside the window:

| run pair, same input | first | second | delta |
|---|---|---|---|
| 44100/24-bit | 10772744 | 10772824 | 80 B = 10 frames |
| 192000/24-bit | 16182832 | 16182800 | 32 B = 4 frames |
| 44100/32-bit | 10772776 | 10772776 | 0 — happened to reproduce |

So identical inputs can yield different `wire raw` **without the audio
differing at all**: the audio is bit-identical (that is what the verdict
certifies); only the amount of trailing silence recorded changes.

**Where the boundaries fall, and why they are inaudible.** Two distinct
things are easy to conflate here:

- A **capture boundary** is a property of the *observer*, not of the wire.
  It decides what lands in the pcap; the DAC received the same stream
  either way. Bytes the tap missed still reached the DAC — that was exactly
  the earlier `INCOMPLETE` bug, real audio the DAC got and we failed to
  record. Nothing about where the tap started or stopped is audible,
  because it never touched the stream.
- The **extra material inside the window** *is* genuine traffic the DAC
  received. It is inaudible for a measured reason, not by assumption: it is
  all-zero PCM (table above), so the DAC's output sits at its zero level.

On a `BIT-PERFECT` verdict both boundaries necessarily fell **outside** the
audio — head in the priming zeros, tail in the pad. They *can* fall inside,
and the tool names it rather than hiding it: inside at the start is
`HEAD LOST`, inside at the end is `INCOMPLETE`. The pad exists to make
"outside" the reliable case at the tail instead of a matter of luck.

One qualification on "inaudible": it describes the sample *values*, not the
act of starting and stopping a stream. Opening or closing an isochronous
stream, and any rate change around it, can produce a genuine audible
artifact from the DAC's analogue side — mute relay, PLL relock, the
cold-open silence documented in `OKTO-DAC8-FreeBSD-44k1-flicker.md`. That
comes from stream start/stop and clock changes, not from the zeros and not
from where the tap's window happened to fall.

#### Step 5 — the comparison

First, note what the comparison is **not**: it never involves the other OS.
`finalize` compares the capture against `ref.raw`, which came from the input
file on this machine —

```python
refcmp = ref[refskip:]
if aligned == refcmp:
    ...
    return out(f"BIT-PERFECT — all {len(refcmp)} reference bytes identical "
               "on the USB wire", 0)
```

— so **a single run is already a complete local file → USB proof**, and
exits 0 on its own. `bitperfect-compare.py` is a separate, optional step
that answers the *additional* question of whether two hosts agree.

`finalize` writes `PREFIX.wav` by wrapping `aligned` in a WAV header built
from the *reference's* format. For a 32-bit input that makes the tap WAV
byte-identical to the input WAV; for a 16/24-bit input it cannot be, since
the tap WAV carries the promoted 32-bit container (see the artifact list
above). In the 44100/32-bit run all three digests line up:

```
input WAV file                          88d365ee…   (generator output)
freebsd tap wav sha256 (file)           88d365ee…
linux   tap wav sha256 (file)           88d365ee…

reference payload (ref.raw)             02905a1e…
freebsd tap payload (aligned)           02905a1e…
linux   tap payload (aligned)           02905a1e…
```

and the cross-OS comparator reduces to a hash equality on equal-length
payloads:

```
$ ./scripts/bitperfect-compare.py \
      bp-results/…-30s-linux.txt bp-results/…-30s-freebsd.txt
A: … linux/7.1.5-arch1-2   — verdict: BIT-PERFECT   sha256 02905a1e…
B: … freebsd/15.1-RELEASE  — verdict: BIT-PERFECT   sha256 02905a1e…
MATCH: payload sha256 identical over 10584000 bytes
```

#### Why the pad cannot hide a defect

The obvious objection to padding is that it might absorb a real fault. It
cannot, and this was verified by replaying the failing run's exact
geometry (5648 B lead-in, 5960 B tail loss) through `finalize` with
synthetic captures:

| case | verdict | exit |
|---|---|---|
| pad + tail loss | BIT-PERFECT | 0 |
| **no** pad + tail loss (old behaviour) | INCOMPLETE — ends 5960 bytes early | 1 |
| pad + one flipped bit mid-stream | VALUE CORRUPTION at offset 176400 | 1 |

The third row is the one that matters: the pad extends the capture window
past the end of the reference, but the comparison window is still exactly
`len(ref)` bytes, so every reference byte is still checked. A defect
*inside* the reference is reported at its exact offset regardless of what
follows it. What the pad removes is only the ability of a late capture cut
to masquerade as a playback fault.

---

## The `/bitperfect` page: running this from the browser

Everything above is a command-line proof of the segment *host → USB wire*. The
panel's **Bit-perfect check** page (`http://<box>:9090/bitperfect`) runs the
same engine, adds the playback paths the box actually uses, and — the part the
command line cannot do — **shows the compared bytes**.

### The five paths, and why more than one is needed

`aplay` proves the host. It does not prove what happens when music arrives the
way music actually arrives: through **upmpdcli** or **qobuzconnect2mpd**, both
of which drive **MPD**. Each added layer is a place bit-perfection can be lost —
MPD resampling, a decoder promoting samples differently, or a renderer setting
MPD's volume or replaygain behind your back.

| Source | Who starts playback | What it proves |
|---|---|---|
| `aplay` | the panel, via the existing per-OS tap script, delegated to unchanged | host → USB, no renderer. The control experiment. |
| `mpd` | the panel: MPD plays a local file on the direct output | MPD → DAC: the segment both renderers share |
| `mpd-http` | the panel: MPD fetches an HTTP URL it serves | MPD's curl input plugin + streaming decoder — structurally the Qobuz stream path, with a *known* file, so the verdict is real byte equality |
| `upnp` | the panel: upmpdcli is found by SSDP and told to play it itself | the whole **upmpdcli** → MPD → DAC path |
| `live` | **you, in the Qobuz app** — the panel only arms the tap and waits | the whole **qobuzconnect2mpd** path, against the renderer's own buffer |

`live` is the one row where the panel is not the playback initiator, and that
is the point rather than a limitation. Every other source hands material to the
chain; `live` deliberately touches nothing, arms the USB tap, and prompts you to
start a track in Qobuz — so the bytes it captures were produced by the real
service path, with nothing synthesised on their behalf. Read "the page plays
nothing" as *the page starts no playback of its own*, not as *no audio flows*:
the tap has to see a live stream or there is no capture to compare.

Running `mpd` and `upnp` on the same material is what localises a fault: if
`mpd` is bit-perfect and `upnp` is not, the renderer is the cause. Both modes
snapshot MPD's volume and replaygain before and after and report any change,
because a renderer that quietly enables replaygain breaks bit-perfection
without altering a single byte of the file it was handed.

### The live Qobuz reference: the renderer's own buffer

`live` needs a reference for a stream you do not have a copy of. It does not
ask for one: **the renderer already wrote the exact bytes it fed MPD to local
storage**, and that file is the reference — so this is genuine byte equality
against the real service, not a comparison against a "same" track that might be
a different master.

Resolution is at run time, never a hard-coded path:

1. `mpc current` — if MPD reports a local file, that is the reference;
2. otherwise the open file descriptors of the MPD/renderer processes
   (`/proc/PID/fd` on Linux, `fstat(1)` on FreeBSD) — renderer-agnostic;
3. otherwise the known buffer locations, filtered to **files that changed
   during the tap window** so a stale buffer from an earlier track can never be
   compared against this capture. On this box those are
   `/tmp/qobuzconnect2mpd-*` and `/tmp/qconnect2mpd-segmented/` (string
   literals in the binary); a segmented buffer is concatenated in **numeric**
   order — lexical order would put `part10` before `part2` and manufacture a
   fault that never happened.

If the buffer turns out to be a **lossy** container, the run is reported as
*decode-path transparent*, not bit-perfect: the DAC legitimately receives the
decoder's output rather than the file's bytes.

### Checking any track, not just the generated asset

The page also loads a **WAV or FLAC** (anything `ffmpeg` decodes). The file is
decoded once to build the reference; the **original** is what gets played,
because the decoder is part of what is under test. Rate and depth come from the
file, so a 96/24 FLAC tests 96/24 with nothing to configure, and the existing
`prep` promotion widens it to the wire container exactly as before.

One caveat is specific to real music. Alignment anchors on a 4 KiB window of the
reference, and the generated counter can never repeat one — real music can
(digital silence, a looped intro, a repeated bar). Aligning on the wrong
occurrence would be a *silent* error, so `find_probe_offset` now takes
`unique_in=` and skips any window that occurs more than once, and the loader
reports the anchor it chose before a run rather than after it.

### Seeing the bytes

A verdict says *whether*. The byte view says *where* and *what*, at two zoom
levels driven by the new `scan` and `window` subcommands:

- **The colour map** paints the whole stream, one cell per slice: green where
  the wire carried exactly the reference bytes, red where it did not, grey where
  the capture did not reach. Every byte of the overlap is really compared — only
  the level row underneath is estimated. It makes the *shape* of a fault
  legible: an isolated red cell is a bit flip, a red tail is an underrun, dense
  speckle is a converting feeder in the path.
- **The side-by-side hex view** shows the reference and the tapped wire in one
  row per frame — byte offset, hex, and the decoded per-channel integers for
  both — with the differing byte positions highlighted in *both* columns at
  once. One slider drives the offset across the whole file with the two views
  locked, so dragging it is a continuous consistency check; `first mismatch`
  and `random spot check` jump straight to the interesting places.

Both read only the artifacts `finalize` already wrote, so they can never
disagree with the verdict — a property `tests/test_bitperfect_window.py` pins
down on captures whose faults are known to the byte.

### Before the stream: the untrimmed wire

The aligned comparison is a black box at its left edge. `finalize` locates the
reference inside the capture and discards everything before it — tap attach
noise, then whatever the audio stack emitted ahead of the first source sample —
and the verdict says nothing about those bytes. The `leadin` subcommand reads
them back out of `PREFIX.wire.raw`, which every path already writes, so the
panel can show the discarded head rather than asserting it was harmless.

It is wire-only by nature: before the stream starts there is no reference byte
to put beside it. Rows ahead of the anchor are marked *trimmed*, so a view
spanning the boundary shows exactly where the compared stream began, and the
summary separates the two things that live in that head — a run of zeros
immediately before the anchor is the ring's priming silence, while a non-zero
byte anywhere in it came from somewhere else and is worth looking at.

Unlike `window` and `scan`, this one does **not** require the aligned pair: the
untrimmed wire is precisely what is still worth reading after an
`ALIGNMENT FAILED` verdict, when there is no aligned pair to show at all.

### Preconditions the page enforces

- **brutefir must be stopped.** The DRC path convolves the FIR filter, so its
  output is *supposed* to differ; a verdict there would look like a failure and
  mean nothing. The page blocks the run and the runner refuses it, overridable
  only with an explicit `--allow-drc`.
- **The tap needs root.** `sudo -n` is probed up front and reported, rather than
  discovered halfway through a capture.
- **The DAC is single-opener**, so the page names whoever currently holds it.

### The same thing from a terminal

```sh
scripts/bitperfect_runner.py --source mpd   --input track.flac --out bp-results/run
scripts/bitperfect_runner.py --source upnp  --input tests/bitperfect-test-44100-s32-stereo-30s.wav --out bp-results/upnp
scripts/bitperfect_runner.py --source live  --duration 60 --out bp-results/qobuz
scripts/bitperfect_material.py load album.flac --out-dir /tmp/mat
scripts/bitperfect-lib.py window PREFIX.ref.raw PREFIX.wav 80000 8 2
scripts/bitperfect-lib.py scan   PREFIX.ref.raw PREFIX.wav 200 2
scripts/bitperfect-lib.py leadin PREFIX.wire.raw 5648 2 0 32
```

`leadin` takes the trim point as its second argument — the `start` field of
`PREFIX.json`, i.e. the capture offset `finalize` aligned on — then the channel
count, the byte offset to display from, and how many frames to show.

`--source aplay` is delegated to `bitperfect-tap-{linux,freebsd}.sh`
unchanged, so the control experiment stays byte-identical to every result
recorded in this document.

---

## Related

- `freebsd-uaudio-patch/` — the play-only patch that disables the DAC's capture
  interface (why there is no digital loopback, and why endpoint `0x82` is
  ignored).
- `OKTO-DAC8-FreeBSD-44k1-flicker.md` — the shared-clock bug the patch works
  around.
- `scripts/verify-bitperfect.sh` — the tool documented here.
- `scripts/bitperfect_runner.py` — the five playback paths behind `/bitperfect`.
- `scripts/bitperfect_material.py` — the WAV/FLAC loader and the live-buffer
  resolver.
- `omdrc-ctrl/src/bitperfect.py` — the panel's side of the same.
