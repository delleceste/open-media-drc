# Verifying the DAC receives bytes PERFECTLY UNCHANGED

This document explains how to prove that the audio bytes written on the host
arrive at the OKTO DAC8 **bit-for-bit identical**, and how the verification tool
(`scripts/verify-bitperfect.sh`) is implemented.

> **Scope.** This verifies the **direct, bit-perfect path** (MPD `OKTO-DAC`
> output → `/dev/dsp0`). The **DRC path** (… → brutefir → `/dev/dsp0`) is
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
dev.pcm.0.bitperfect = 1     # first opener's format becomes the hardware format
dev.pcm.0.play.vchans = 0    # no virtual-channel mixer/resampler in front of the DAC
```

This check is definitive for the **host** portion of the path. It does not, by
itself, prove the USB layer is transparent — that's what level 2 adds.

### 2. Empirical wire tap (gold standard, end-to-end)

`scripts/verify-bitperfect.sh` performs the full proof:

```sh
# free the DAC first (single-open device):
./drc.sh off            # stop virtual_oss/brutefir, enable direct output
# stop whatever renderer holds /dev/dsp0 (e.g. upmpdcli / mpd) if needed

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
| A | MPD `OKTO-DAC` → `/dev/dsp0` → DAC | USB endpoint `0x01` | **BIT-PERFECT**, 0 slips, 0 corruption |
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
   refuse to run if `/dev/dsp0` is already held by another process (`fuser`).

3. **Generate a deterministic test signal** (`S32_LE`, stereo). By default it is
   a **near-silent (~−90 dBFS) per-sample counter** in the low 16 bits, with the
   L and R channels distinct (catches channel swap/duplication). This is
   inaudible yet *maximally sensitive*: truncation, dithering, a non-unity
   volume, or resampling all corrupt the low bits deterministically. Set
   `FULLSCALE=1` for a full-range pseudo-random signal instead — **loud, so
   disconnect the amplifier first.**

4. **Play it bit-perfectly** with an embedded C writer that opens `/dev/dsp0` and
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

**A digital audio sink consumes samples on a clock.** `/dev/dsp0` (the OKTO DAC)
consumes at the DAC's quartz crystal — exactly `rate` samples per second, no more,
no less. `virtual_oss` started with `-f /dev/null` (as `drc.sh` does) has **no
hardware clock**, so it consumes at a **software timer** it generates itself — a
"free-running" clock that is *approximately* `rate` but is its own independent
time base.

**How a producer stays in step: flow control (back-pressure).** A well-behaved
sink exposes a small buffer. When you `write()` faster than the sink drains, the
buffer fills and the next `write()` **blocks** until space frees up. That block
is the sink throttling the producer to *its* clock. So:

- Writing to **`/dev/dsp0`** → the kernel/USB stack blocks your `write()`s in
  lockstep with the DAC crystal. The producer is **slaved to the DAC clock**.
  No drift is possible; the bytes can only arrive bit-exact (proven by the USB
  tap).
- A program (MPD, or any player) writing to **`/dev/dsp.play`** is likewise
  blocked by virtual_oss's buffer and thus **slaved to virtual_oss's software
  clock**. One producer, one clock → no drift, no slip.

**Why a flat-out `write()` loop is *not* paced.** Our standalone writer just
loops `read(file) → write(dsp)`. If the device gave perfect back-pressure that
would be fine — and on `/dev/dsp0` it is. But virtual_oss's play device let our
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
`/dev/dsp.loop` (virtual_oss's free-running clock) and writes `/dev/dsp0` (the
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
the format-guarded OSS writer on `/dev/dsp0` — both refuse any conversion),
captures every isochronous OUT transfer to endpoint `0x01`, concatenates the
payloads, aligns them to the source and writes:

- `PREFIX.wav` — the tapped wire bytes, source-aligned and source-length,
  as a WAV. **If the chain is bit-perfect this file is byte-identical to the
  input WAV** (same sha256), so two bit-perfect OSes trivially produce
  identical outputs.
- `PREFIX.wire.raw` — the full untrimmed wire stream (priming bytes and all).
- `PREFIX.txt` — verdict + sha256 report.

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
- Verified on this Linux host (DacMagic 100, kernel 7.1.5-arch1):
  44100/32-bit × 30 s and 192000/24-bit × 10 s both **BIT-PERFECT** —
  tap WAV sha256 equal to the source WAV.

## Related

- `freebsd-uaudio-patch/` — the play-only patch that disables the DAC's capture
  interface (why there is no digital loopback, and why endpoint `0x82` is
  ignored).
- `OKTO-DAC8-FreeBSD-44k1-flicker.md` — the shared-clock bug the patch works
  around.
- `scripts/verify-bitperfect.sh` — the tool documented here.
