# DAC sample format and byte alignment — Linux reference, FreeBSD checklist

> **Start here for the whole story:** [`doc/SOUND-QUALITY-INVESTIGATION.md`](SOUND-QUALITY-INVESTIGATION.md) is the complete record — what was measured, what it ruled out, and what is still open.

**Date:** 2026-09-09 · **Reference host:** Arch Linux, kernel 7.2.3-arch1-3 ·
**DAC:** OKTO RESEARCH DAC8STEREO (Thesycon/XMOS `152a:88c5`, bcdDevice 1.60)

For the plain-language version of everything below — what a container is,
what a shift would sound like, and how to read the probe output — see
`doc/DAC-FORMAT-EXPLAINED.md`. The FreeBSD-side runbook is
`doc/DAC-FORMAT-CROSS-OS-PROCEDURE.md`.

Written to answer one question: *is something in the chain handing the DAC
misaligned or wrongly-justified samples, and is FreeBSD doing it differently
from Linux?*

The short answer is in [Verdict](#verdict). Read that first — it changes where
you should spend time on the FreeBSD box.

---

## 1. What the DAC actually asks for (device-side truth, OS-independent)

Read from the USB configuration descriptors (`lsusb -v -d 152a:88c5`), playback
interface 1. Both configurations expose the same four alt-settings:

| Alt | `bSubslotSize` | `bBitResolution` | Meaning on the wire | ALSA name |
|-----|---------------|------------------|---------------------|-----------|
| **1** | **4 bytes** | **24** | 24 valid bits in a 32-bit slot | `S32_LE` |
| 2 | 2 bytes | 16 | native 16-bit | `S16_LE` |
| 3 | 4 bytes | 32 | full 32-bit | `S32_LE` |
| 4 | 4 bytes | 32 (`bmFormats 0x80000000`) | RAW/DSD | `DSD_U32_BE` |

Endpoint `0x01` OUT, `wMaxPacketSize` 392 B, async, with an explicit feedback
endpoint `0x81` IN (4 B, 16.16 format). `bNrChannels` 2, rates 44.1k–384k.

Two consequences that matter for alignment:

- **A 4-byte subslot is a 4-byte subslot.** Per USB Audio Class Type-I, the
  sample is stored **MSB-aligned (left-justified)** inside the subslot; the
  unused low bits are ignored by the device. So for alt 1 the DAC takes the
  **top 24 bits** of each 32-bit little-endian word and discards the lowest
  byte. Feeding it a normal full-range `S32_LE` stream is exactly right — the
  low byte you lose sits at about −144 dBFS.
- **There is no 3-byte (packed `S24_3LE`) alt-setting on this DAC.** Any code
  path that decides to emit 3-byte samples to it is wrong by construction. For
  contrast, the ESI U24XL on the same host *does* declare a 3-byte container
  (`bSubslotSize 3`) — do not let a helper written for that device leak into
  the OKTO path.

## 2. What Linux is sending right now (measured, live)

Chain in the running DRC configuration:

```
mpv/MPD ──► hw:1,0  snd-aloop playback
            hw:1,1  snd-aloop capture ──► brutefir ──► hw:2,0 OKTO DAC ──► USB
```

`/proc/asound/*/hw_params`, captured with music playing:

| Node | Owner | access | format | rate | period / buffer |
|------|-------|--------|--------|------|-----------------|
| `card1/pcm0p/sub0` (aloop play) | mpv, pid 1005 | `RW_INTERLEAVED` | `S32_LE` | 192000 | 32768 / 98304 |
| `card1/pcm1c/sub0` (aloop capture) | brutefir, pid 673 | `MMAP_INTERLEAVED` | `S32_LE` | 192000 | 32768 / 262144 |
| `card2/pcm0p/sub0` (OKTO) | brutefir, pid 673 | `MMAP_INTERLEAVED` | `S32_LE` | 192000 | 32768 / 384000 |

`/proc/asound/card2/stream0` while running:

```
Interface = 1, Altset = 1, Packet Size = 288
Momentary freq = 192005 Hz (0x18.002a), Feedback Format = 16.16
```

So: **`S32_LE`, 2 ch, 192 kHz, end to end, no format change at any hop, and the
DAC is on alt 1 (4-byte subslot / 24 valid bits).** `snd-aloop` performs no
conversion — both sides of a loopback pair negotiate one identical format — so
the loopback link cannot introduce a shift. brutefir opens both `hw:` devices
directly (no `plug`), so ALSA inserts no converter there either.

brutefir's own settings (`~/.config/BruteFIR/brutefir_defaults.conf`):
`float_bits: 64`, input `hw:1,1` `S32_LE`, output `hw:2,0` `S32_LE`,
`dither: false`, `safety_limit: 6`; filters at `attenuation: 1.5`.

> **Note on `dither: false`.** brutefir reduces 64-bit float to a 32-bit
> integer container, and the DAC then drops the low 8 bits. That is an
> undithered truncation to 24 bits at roughly −144 dBFS. It is not audible and
> it is not what you are chasing, but it is the only *intentional* bit loss in
> the Linux chain — worth knowing before you go looking for one.

## 3. Cross-OS wire evidence that already exists

`bp-results/` holds USB-wire captures (usbmon on Linux, `usbdump` on FreeBSD)
taken with `scripts/bitperfect-tap-{linux,freebsd}.sh`. The aligned payload
hashes:

| Test | OS | aligned payload sha256 | verdict |
|------|----|------------------------|---------|
| 44100 / S32 / 30 s | linux 7.1.5 | `02905a1e322dc6e1…` | BIT-PERFECT |
| 44100 / S32 / 30 s | freebsd 15.1-RELEASE | `02905a1e322dc6e1…` | BIT-PERFECT |
| 44100 / S24 / 30 s | freebsd 15.1-RELEASE | `caf889126900964b…` | BIT-PERFECT |
| 192000 / S24 / 10 s | freebsd 15.1-RELEASE | `7acfeed3f84614d1…` | BIT-PERFECT |

The first two rows are the same 32-bit input played on two different operating
systems, and the bytes that reached the isochronous OUT endpoint are
**byte-identical**. The 24-bit rows show FreeBSD promoting 24-bit material into
the 32-bit container losslessly (low byte zeroed), with no shift.

## 4. FreeBSD's alt-setting choice — different mechanism, same result

FreeBSD `uaudio` reaches alt 1 by a different route than Linux, documented in
`freebsd-uaudio-patch/README.md`:

1. `uaudio_chan_fill_info_sub()` computes bit depth as `bSubslotSize * 8` and
   **ignores `bBitResolution`**, so alts 1, 3 and 4 all register as "32-bit".
2. It keeps only one alt per sample rate, first match in descriptor order wins
   → **alt 1**.

Linux's `parse_audio_format_i_type()` also selects the container from the
subslot size (4 → `S32_LE`) and keeps `bBitResolution` as metadata only.

Different code, **same landing spot**: both OSes stream 32-bit slots into
alt 1. This is also why the DAC front panel reads `24` even for 16-bit tracks —
cosmetic, already analysed, not a defect.

---

## Verdict

**The "FreeBSD is feeding the DAC shifted bytes" hypothesis is refuted for the
direct path.** Same alt-setting, same container, same justification, and
independently captured USB wire bytes with matching SHA-256 across both
operating systems. There is no 16/24/32-bit alignment defect between
`/dev/dsp.dac` and the DAC.

**But every one of those captures bypasses `virtual_oss` and `brutefir`.** They
tap the *direct* path (`writer` or MPD `OKTO-DAC` → `/dev/dsp.dac`). The DRC
path has **never been wire-verified on either OS**. If a real macroscopic
problem exists, that is where it is — so stop looking at the DAC end and start
at the bridge.

---

## 5. FreeBSD checklist — run these, in this order

The DRC path on FreeBSD is:

```
MPD ──► /dev/dsp.play ──(virtual_oss)──► /dev/dsp.loop ──► brutefir ──► /dev/dsp.dac ──► uaudio ──► USB
```

started as (`drc.sh:96`, `drc.sh:1614`):

```sh
virtual_oss -D /tmp/virtual_oss.pid -r <rate> \
    -i 8 -C 2 -c 2 -b 32 -s 200ms -f /dev/null -a 0 -d dsp.play -L dsp.loop
```

### 5.1 Rule out the rate mismatch first — this is the likeliest culprit

`virtual_oss` runs at a fixed `-r <rate>`, while MPD's `DRC-native` output is
`format "*:*:*"` (keeps the source rate). If they disagree, something resamples
silently — no error, no log. A poor resampler would produce exactly the
"macroscopic quality problem, chain otherwise looks fine" symptom, which is why
this was the first thing to rule out.

> **Correction (2026-09-11).** This originally said `virtual_oss` does that
> resampling "with its own low-quality resampler". Per `virtual_oss(8)` it only
> resamples with `-S`, which `drc.sh` does not pass: it coerces MPD to its rate
> and MPD converts with soxr "very high". On Linux the equivalent path was
> measured at the 32-bit floor. See `RATE-MISMATCH-PROCEDURE.md`.

```sh
./drc.sh status          # must NOT print MISMATCH
```

Play the track that sounds wrong and check again while it is playing. If it
says `MISMATCH`, that is your answer: restart DRC at the track's rate, or use
`resamp` mode (MPD's soxr "very high") for mixed-rate playlists.

### 5.2 Check what MPD actually negotiated on `/dev/dsp.play`

This is the one place a genuine byte shift could still hide, and it is worth
being precise about why.

`AFMT_S24_LE` is **ambiguous across OSS implementations**:

- OSSv4 documents it as *24 bits in a 32-bit container, LSB-aligned* → 4 bytes.
- FreeBSD's `pcm` feeder treats 24-bit formats as **3-byte packed**. This
  repo's own `freebsd-uaudio-patch/bench/ossio.py` encodes that assumption
  (`FMT_BYTES = {…, AFMT_S24_LE: 3, …}`).
- MPD's internal `S24_P32` is **24 bits in 4 bytes**.

If MPD requests `AFMT_S24_NE` for a 24-bit track and then writes 4-byte frames
into a consumer counting 3 bytes per sample, every sample after the first is
displaced by one byte — full-scale noise, immediately obvious. If instead a
right-justified 24-in-32 stream is consumed as full-scale 32-bit you get a
+48 dB overload; the reverse gives −48 dB, quiet but clean.

So: confirm MPD is negotiating `S32_LE` and not any 24-bit variant.

```sh
grep -i 'format\|opened\|failed' ~/.local/share/mpd/mpd.log | tail -30
```

Force the question if the log is quiet — set `format "192000:32:2"` (or the
track's rate with `:32:`) on the `DRC-native` output temporarily. If the problem
disappears with an explicit 32-bit request, you have found it.

### 5.3 Wire-verify the bridge itself (never yet done)

`scripts/verify-bitperfect.sh` already supports this exact tap — it just has
not been run for the DRC path:

```sh
# MPD -> virtual_oss bridge, the front half of the DRC chain.
./verify-bitperfect.sh --source mpd:DRC-native --tap loop:/dev/dsp.loop

# Same link, built-in writer instead of MPD (virtual_oss free-runs, so --paced).
./verify-bitperfect.sh --play /dev/dsp.play --tap loop:/dev/dsp.loop --paced
```

Run it with **16-bit, 24-bit and 32-bit** source material — the whole point is
that the bug, if it exists, is bit-depth-dependent and 32-bit material would
never show it. `tests/` already has generated assets at 44100/S24, 44100/S32 and
192000/S24.

The comparator distinguishes a benign `virtual_oss` clock slip (it free-runs
against `-f /dev/null`) from real value corruption — read the verdict line, not
just the exit code.

### 5.4 Wire-verify the whole DRC path

Per `doc/BIT-PERFECT-VERIFICATION.md` § "Testing the DRC path": build a
**unit-impulse** filter (`L.raw`/`R.raw` = a single `1.0` FLOAT64 sample
followed by zeros) with the config's `attenuation` set to `0`, run the chain,
tap endpoint `0x01`, and compare against the source delayed by the filter
latency. That reduces the convolution to identity and leaves `virtual_oss` +
container handling + brutefir I/O as the only things under test.

### 5.5 Confirm no kernel feeder is inserted

```sh
sysctl dev.pcm.<N>.bitperfect      # want 1
sysctl dev.pcm.<N>.play.vchans     # want 0
sysctl dev.pcm.<N>.feedback_rate   # should track the requested rate
```

With `vchans` non-zero the kernel can insert a mixing/resampling feeder with
volume applied ahead of the DAC. That is not a shift, but it is a real quality
path and it is cheap to rule out.

Also verify `hw.usb.uaudio.default_bits` is **not** set to 16 in
`/boot/loader.conf` — it would pin every stream to the native 16-bit alt and
truncate hi-res material. Note the knob is `RWTUN` and a driver reload resets
it, which is why earlier attempts to use it appeared to be ignored
(`OKTO-DAC8-FreeBSD-44k1-flicker.md`).

---

## 6. Triage by symptom

Use this to skip straight to the right hypothesis.

| What you hear | Cause | Where |
|---|---|---|
| Full-scale noise / unlistenable | 1-byte progressive shift, stride mismatch (3-byte vs 4-byte sample) | §5.2 |
| Gross clipping, distorted loud | 8-bit left shift — right-justified 24-in-32 consumed as full-scale 32 | §5.2 |
| Very quiet (~−48 dB) but clean | 8-bit right shift — the inverse | §5.2 |
| Dull, grainy, "digital"; recognisable music | a poor resampler on a rate mismatch (MPD without soxr; or `virtual_oss` if started with `-S`) | §5.1 |
| Image collapse / phase oddness | channel swap or half-frame offset | §5.3 |
| Fine on direct, wrong on DRC only | bridge or filter, not the DAC | §5.3, §5.4 |
| Wrong on both direct and DRC | not covered by any of this — the direct path is proven byte-identical to Linux (§3); look at the analogue side, clock, or filter data |

---

## 7. Bug found on the Linux side while writing this

`brutefir_defaults.linux.conf` in the repo hardcodes the output as:

```
device: "hw:0,0"; # omdrc-managed-dac
```

On this host `hw:0` is the **ESI U24XL** (max 48 kHz, 3-byte `S24_3LE`
container), not the OKTO — which is `hw:2`. The installed copy at
`~/.config/BruteFIR/brutefir_defaults.conf` has been hand-corrected to
`hw:2,0`, so the running system is fine, but that correction is **not in git**.

ALSA card numbers follow enumeration order, exactly like FreeBSD's `pcm` unit
numbers — which is precisely why the FreeBSD side introduced the `/dev/dsp.dac`
by-role symlink managed by `omdrc_audio`. The Linux side has no equivalent: the
udev rule (`99-usb-audio-drc.rules`) matches *any* USB sound card and does not
pin an index.

A reinstall from the template would point brutefir at the U24XL and it would
fail to open at 192 kHz. Worth fixing with a stable identifier
(`hw:DAC8STEREO,0`, or a `snd_usb_audio` index assignment) rather than a card
number.

---

## Appendix — commands used to produce the Linux reference

```sh
cat /proc/asound/cards
cat /proc/asound/card2/stream0
cat /proc/asound/card*/pcm*[pc]/sub*/hw_params
cat /proc/asound/card*/pcm*[pc]/sub*/status
lsusb -d 152a:88c5 -v          # descriptors readable from sysfs without root
```

Everything in §2 was read from a live, playing system; nothing was restarted or
reconfigured to produce it.
