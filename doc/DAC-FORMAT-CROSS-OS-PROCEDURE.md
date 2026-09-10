# Cross-OS DAC format & byte-alignment comparison — procedure

**Audience: the Claude Code session running on the FreeBSD host.**
Everything you need is in this repo. Read §1, run §3, report §6.

Companion documents:
`doc/DAC-FORMAT-EXPLAINED.md` (plain-language background — read this first if
USB audio formats are unfamiliar), `doc/DAC-FORMAT-ALIGNMENT.md` (the Linux
analysis and why this matters), and `doc/BIT-PERFECT-VERIFICATION.md` (the
heavier USB-wire tap suite, which this procedure deliberately does **not**
require).

---

## 1. What you are being asked to do

The question is whether FreeBSD hands the OKTO DAC8STEREO samples in the same
container and alignment Linux does, or whether something in the FreeBSD chain
is shifting them.

A reference probe has already been taken on the Linux host and committed:

```
bp-results/dac-format-linux.json
```

Your job:

1. Run `scripts/dac-format-probe.py` on FreeBSD to produce the matching artifact.
2. Run the same script in `--compare` mode against the Linux reference.
3. Report the verdict, plus the three FreeBSD-only facts the probe cannot
   reach on its own (§4).

The probe is **read-only**: it opens no audio device, changes no sysctl,
restarts no service. It is safe to run while music is playing, and it is *most
useful* while music is playing — an idle chain has no open streams to inspect.

---

## 2. The Linux reference, in short

Taken 2026-09-10 on Arch Linux 7.2.3-arch1-3, with the full DRC chain running
at 192 kHz. Full detail in `bp-results/dac-format-linux.json`.

**Device descriptors** — OKTO DAC8STEREO, USB `152a:88c5`, bcdDevice 1.60,
serial 000483. Playback interface 1, UAC2:

| Alt | `bSubslotSize` | `bBitResolution` | Container | `bmFormats` |
|-----|---------------|------------------|-----------|-------------|
| **1** | **4** | **24** | `S32_LE` | `0x00000001` PCM |
| 2 | 2 | 16 | `S16_LE` | `0x00000001` PCM |
| 3 | 4 | 32 | `S32_LE` | `0x00000001` PCM |
| 4 | 4 | 32 | `S32_LE` | `0x80000000` RAW/DSD |

Endpoint `0x01` OUT (async), `wMaxPacketSize` 392, feedback endpoint `0x81` IN.

> The device exposes **two** configuration descriptors that both report
> `bConfigurationValue = 1` — firmware quirk. The probe distinguishes them by
> ordinal and summarises the first. If you see every alt listed twice, that is
> why; it is not a fault.

**Selected at runtime:** interface 1, **alt 1** → 4 bytes per sample on the
wire, top 24 bits converted, low byte discarded by the DAC.

**Every hop of the chain, measured live:**

| Hop | Node | Format | Rate | Ch |
|-----|------|--------|------|----|
| player → loopback | `card1/pcm0p/sub0` | `S32_LE` | 192000 | 2 |
| loopback → brutefir | `card1/pcm1c/sub0` | `S32_LE` | 192000 | 2 |
| brutefir → DAC | `card2/pcm0p/sub0` | `S32_LE` | 192000 | 2 |

**brutefir:** input `alsa hw:1,1` `S32_LE`, output `alsa hw:2,0` `S32_LE`,
`dither: false`, `float_bits: 64`, `filter_length: 32768,16`.

---

## 3. Run this on FreeBSD

```sh
cd /path/to/open-media-drc
git pull                       # get the probe script and the reference JSON

# Start the DRC chain and play something, so there are open streams to see.
./drc.sh status                # note the rate; note any MISMATCH line

./scripts/dac-format-probe.py --out bp-results/dac-format-freebsd.json

./scripts/dac-format-probe.py --compare \
    bp-results/dac-format-linux.json \
    bp-results/dac-format-freebsd.json
```

Exit status of `--compare`: **0 = MATCH**, **1 = MISMATCH** (findings printed),
2 = usage error.

### If the probe comes back thin

**Per-stream format needs verbose sndstat.** Without it `/dev/sndstat` lists
devices but not the negotiated format of each open channel. The probe sets
`verbose_hint` in its JSON when it detects this. Fix and re-probe:

```sh
sysctl hw.snd.verbose=2        # needs root; revert with hw.snd.verbose=0
cat /dev/sndstat               # channel lines should now carry `spd`/`fmt`
```

This is the **only** step in this procedure that changes system state. It is
a display-verbosity knob, it does not touch the audio path, and it does not
survive a reboot unless you put it in `/etc/sysctl.conf`.

**The probe needs root for `usbconfig`.** Unprivileged, `usbconfig` does not
fail: it prints `No device match or lack of permissions.` to *stdout* and exits
0, so the descriptor walk quietly produced an empty alt table and
`selected_alt: null` looked like a FreeBSD limitation rather than a missing
`sudo`. The probe now retries through `sudo -n usbconfig`; if that is not
permitted either, run the whole probe with `sudo`.

**Descriptors come from `usbconfig`, not sysfs — and its hex is incomplete.**
`usbconfig -d ugenX.Y dump_all_desc` prints a `RAW dump:` block only for
class-specific ("Additional Descriptor") entries. **Standard** descriptors —
device, configuration, interface, endpoint — are printed as decoded
`name = 0xVALUE` fields with no hex at all. Concatenating just the RAW blocks
therefore yields a blob with every interface descriptor missing, the walker
never opens an AudioStreaming alt-setting, and the probe reports
`alt_settings: []`. The probe re-encodes the standard descriptors from their
printed fields (16-bit for `w`/`bcd`/`id` names, 8-bit otherwise, then cut to
each descriptor's own `bLength`) and splices them back in order, reproducing
the blob Linux exposes directly in `/sys/bus/usb/devices/*/descriptors`.

If the alt table is still empty, `usbconfig`'s output format has drifted — fall
back to §5 and decode by hand.

**Sanity check before trusting a verdict.** A probe that found nothing does not
report MATCH — it reports `no USB audio device in common` and exits 1 — but a
probe that found only *some* things can still read as agreement. Before
believing a MATCH, confirm the FreeBSD JSON actually contains:

```sh
python3 - <<'EOF'
import json; d = json.load(open("bp-results/dac-format-freebsd.json"))
s = d["summary"]
assert s["dacs"],         "no descriptors — usbconfig ran unprivileged?"
assert s["open_streams"], "no open streams — is the chain playing? hw.snd.verbose=2?"
print(len(s["dacs"]), "device(s),", len(s["open_streams"]), "open stream(s)")
EOF
```

---

## 4. Three facts the probe cannot fully determine on FreeBSD

Please establish these and include them in your report.

### 4.1 Which alt-setting is actually selected

Linux states this outright in `/proc/asound/cardN/stream0` (`Altset = 1`).
FreeBSD has no equivalent file, so `summary.selected_alt` will be `null`.

**Indirect (usually sufficient):** the `uaudio` attach line, captured by the
probe into `uaudio_dmesg`:

```sh
dmesg | grep -i uaudio
```

Look for a line of the form `Play: <rate> Hz, 2 ch, 32-bit S-LE PCM format`.
A **32-bit** container narrows it to alts 1, 3 or 4. FreeBSD's
`uaudio_chan_fill_info_sub()` computes depth as `bSubslotSize * 8` (ignoring
`bBitResolution`) and keeps only the first alt matching each rate, so
descriptor order decides and **alt 1 wins**. This is documented, with source
references, in `freebsd-uaudio-patch/README.md`.

**Authoritative (do this if anything else looks wrong):** capture the
`SET_INTERFACE` control transfer as the stream starts.

```sh
usbconfig list                       # find the DAC's ugenX.Y and device address
usbdump -i usbusN -f <devaddr> -vv > /tmp/setif.txt &
# now start playback
# then stop usbdump and look for bRequest 0x0B (SET_INTERFACE);
# wValue is the alt-setting number, wIndex the interface
grep -B4 -A8 '0x0b' /tmp/setif.txt | head -60
```

`wValue = 1` on `wIndex = 1` confirms alt 1 directly.

### 4.2 Whether a kernel feeder sits in the path

```sh
sysctl dev.pcm.<N>.bitperfect        # want 1
sysctl dev.pcm.<N>.play.vchans       # want 0
sysctl dev.pcm.<N>.feedback_rate     # should track the requested rate
sysctl dev.pcm.<N>.play.vchanformat
sysctl dev.pcm.<N>.play.vchanrate
```

With `vchans` non-zero the kernel can insert a mixing/resampling feeder with
volume ahead of the DAC. That is not a byte shift, but it is a real quality
path and Linux's `hw:` devices have no equivalent, so there is nothing for the
comparison to catch. Rule it out explicitly.

Also confirm `hw.usb.uaudio.default_bits` is **not** pinned to 16 in
`/boot/loader.conf` — it would force the native 16-bit alt and truncate hi-res
material. The knob is `RWTUN` and a driver reload resets it, which is why
earlier attempts to use it appeared to be ignored
(`OKTO-DAC8-FreeBSD-44k1-flicker.md`).

### 4.3 What MPD negotiated on `/dev/dsp.play`

This is the one place a genuine byte shift could still hide, and it has no
Linux counterpart because `AFMT_S24_LE` does not exist on ALSA.

`AFMT_S24_LE` is **ambiguous across OSS implementations**:

- OSSv4 documents it as *24 bits in a 32-bit container, LSB-aligned* → 4 bytes.
- FreeBSD's `pcm` feeder treats 24-bit formats as **3-byte packed**. This
  repo's own `freebsd-uaudio-patch/bench/ossio.py` encodes that assumption
  (`FMT_BYTES = {…, AFMT_S24_LE: 3, …}`).
- MPD's internal `S24_P32` is **24 bits in 4 bytes**.

If MPD requests `AFMT_S24_NE` for a 24-bit track and then writes 4-byte frames
to a consumer counting 3 bytes per sample, every sample after the first is
displaced by one byte. Check what was actually negotiated:

```sh
grep -i 'format\|opened\|failed' ~/.local/share/mpd/mpd.log | tail -30
```

The probe decodes any `AFMT` word it finds in verbose `sndstat` into
`{encoding, channels, wire_bytes}`, so a 3-byte encoding anywhere in the chain
shows up in the comparison as a differing sample width — see §5's red-flag row.

**Play 16-bit, 24-bit and 32-bit material** across the DRC path while probing.
A 32-bit-only test can never expose this: the bug, if present, is bit-depth
dependent.

---

## 5. Comparison rules — what a difference means

The comparator applies these automatically; they are spelled out so you can
judge anything it did not flag.

### Must be identical — a difference is a real finding

| Field | Why |
|-------|-----|
| Per-alt `subslot_bytes`, `bit_resolution`, `bmFormats` | Device-side truth. Same physical DAC ⇒ same descriptors. A difference means one host mis-parsed them, or you are not comparing the same device. |
| Selected alt-setting | Both OSes should land on alt 1. A different alt means a different wire container. |
| Sample width in bytes across all open streams | **This is the byte-alignment question.** 4 bytes on Linux and 3 on FreeBSD is exactly the stride mismatch that shifts every sample. |
| Channel count | 2 throughout. |
| brutefir `input.sample` / `output.sample` | Both must be `S32_LE`. |

### Expected to differ — not findings

| Field | Why |
|-------|-----|
| Device node names (`hw:2,0` vs `/dev/dsp.dac`, `card1/pcm1c` vs `pcm0/dsp0.p0`) | Different OS naming. ALSA card numbers and FreeBSD `pcm` units both follow enumeration order. |
| `access` (`MMAP_INTERLEAVED` vs OSS `write()`) | Different I/O model, same bytes. |
| `period_size` / `buffer_size` vs `-s 200ms` | Different buffering strategies. |
| brutefir `filter_length` (`32768,16` vs `8192,64`) | Deliberate — same total taps, smaller partitions on FreeBSD for virtual_oss's ring-buffer headroom. Acoustically identical. |
| Loopback mechanism (`snd-aloop` vs `virtual_oss`) | Different bridge, same required contract: `S32_LE`, 2 ch, one rate. |
| brutefir `io_module` (`alsa` vs `oss`) | Expected. |

### Red flags the comparator raises

| Finding | Meaning |
|---------|---------|
| `open streams disagree on rate` | A resampler is running inside the chain. On FreeBSD the usual cause is `virtual_oss -r <rate>` disagreeing with MPD's `DRC-native` (`format "*:*:*"`, keeps the source rate) — it resamples **silently** with its own non-soxr resampler. `./drc.sh status` flags this as `MISMATCH`. Treat it as a hard stop. |
| `open streams disagree on format` | A converter is running inside the chain. |
| `different sample widths (X vs Y bytes)` | The stride mismatch. See §4.3. |
| `different alt-setting selected` | The two OSes negotiated different wire containers. |

---

## 6. What to report back

Paste:

1. The full `--compare` output (verdict line included).
2. `./drc.sh status` while playing.
3. The `uaudio` attach line from §4.1, and the alt number you concluded.
4. The `dev.pcm.<N>.*` sysctl values from §4.2.
5. The MPD-negotiated format from §4.3, ideally for a 16-, a 24- **and** a
   32-bit track.
6. `bp-results/dac-format-freebsd.json` committed to the branch, so the
   comparison is reproducible later.

If the verdict is MATCH and §4 turns up nothing, then the format and alignment
handed to the DAC are the same on both operating systems, and the sound-quality
difference is **not** a container or byte-alignment problem — move to
`doc/DAC-FORMAT-ALIGNMENT.md` §6 (triage by symptom) and the DRC-path wire
verification in §5.3–5.4 of that document, which no run has yet performed on
either OS.

---

## 7. Result of the run — 2026-09-10

Run on `bee`, FreeBSD 15.1-RELEASE-p2, chain up at 192 kHz on
`120.blue @multi.pt`, playing `tests/gen-bitperfect-wav.py` material through
MPD → `DRC-native`. Artifact: `bp-results/dac-format-freebsd.json`.

### Verdict: MATCH (exit 0)

```
A = linux/7.2.3-arch1-3     B = freebsd/15.1-RELEASE-p2

── DAC 152a:88c5 (DAC8STEREO)
   alt 1   same    4 bytes/sample, 24 valid bits (S32_LE)
   alt 2   same    2 bytes/sample, 16 valid bits (S16_LE)
   alt 3   same    4 bytes/sample, 32 valid bits (S32_LE)
   alt 4   same    4 bytes/sample, 32 valid bits (S32_LE)

── wire container width
   A: ['S32_LE'] -> [4] bytes/sample
   B: ['S32_LE'] -> [4] bytes/sample

MATCH — both hosts present the DAC the same alt-setting, the same wire
container and a self-consistent chain.
```

### §4.1 — alt-setting, confirmed on the wire

Not inferred from the attach line. `brutefir` alone was restarted (virtual_oss
left running, so no cuse teardown risk — see `VIRTUAL_OSS_CUSE_DEADLOCK.md`)
while `usbdump -i usbus0 -f 3 -vv` was recording:

```
01 0B 00 00 01 00 00 00   SET_INTERFACE  wValue=0  wIndex=1   (brutefir closes)
A1 01 00 01 00 29 04 00 → 00 EE 02 00   GET CUR sample rate = 192000
01 0B 01 00 01 00 00 00   SET_INTERFACE  wValue=1  wIndex=1   (brutefir opens)
```

**wValue = 1 on wIndex = 1 — alt 1, directly.** Note the ordering: the clock is
read back *before* the alt-setting is selected, which is the clock-before-alt
sequence from the patched `uaudio`. The isochronous OUT frames that follow are
192 bytes each, 32 per URB: 192 / 4 / 2 = 24 frames per 125 µs microframe =
192 kHz, at 4 bytes per sample.

### §4.2 — no kernel feeder

`bitperfect=1`, `play.vchans=0`, `feedback_rate=192005`,
`hw.usb.uaudio.default_bits=0`, nothing `uaudio`-related in `/boot/loader.conf`.
The decisive evidence is the feeder chain in verbose `sndstat`:

```
[dsp1.play.0]: spd 192000, fmt 0x00201000, pid 2067 (brutefir)
  channel flags=0x2000110c<RUNNING,TRIGGERED,BUSY,HAS_SIZE,BITPERFECT>
  {userland} -> feeder_root(0x00201000) -> {hardware}
```

No `feeder_format`, no `feeder_volume` — `feeder_root` is the channel itself,
not a conversion stage. For contrast, the idle ESI U24XL on the same box shows
`feeder_root -> feeder_format -> feeder_volume -> {hardware}`; that is what a
non-transparent path looks like, and the probe now records the chain verbatim
for exactly this comparison.

### §4.3 — the 24-bit ambiguity does not arise

MPD's own output format, read with `mpc status '%audioformat%'` while each
file played through the DRC chain:

| material | MPD output format |
|---|---|
| 44100 / 16-bit | `44100:16:2` |
| 44100 / 24-bit | `44100:32:2` |
| 44100 / 32-bit | `44100:32:2` |
| 192000 / 24-bit | `192000:32:2` |

A 24-bit track is presented as 32-bit (`S24_P32`), **never** as any 24-bit OSS
format. The 3-byte/4-byte stride ambiguity described above cannot occur on this
chain, at any source bit depth.

### Conclusion

The format and byte alignment handed to the DAC are identical on both operating
systems. Per §6 above, the sound-quality difference is **not** a container or
byte-alignment problem. What remains is `doc/DAC-FORMAT-ALIGNMENT.md`
§5.3–§5.4 — the bridge and the convolver — i.e. the cross-OS null in
`doc/BIT-PERFECT-VERIFICATION.md`, which has still not been run on the Linux
side. Before it can be, see `doc/CROSS-OS-FILTER-COEFFICIENTS.md`: the two
machines are not currently convolving the same coefficients.

### Three probe defects fixed to get here

All three failed silently — the probe still exited 0, it simply had no evidence
in it. Recorded because "the comparison found nothing" and "the comparison
found agreement" are indistinguishable in the output unless you look.

| Defect | Effect |
|---|---|
| `usbconfig` run unprivileged | `usb_audio_devices: []`, no alt table |
| Blob rebuilt from `RAW dump:` blocks only | every standard descriptor missing, walker never opened an alt |
| `AFMT` channel field read at bit 24 | every stereo stream decoded as 0 channels |

Plus, in the same pass: 15.1 spells verbose `sndstat` channels `[dsp1.play.0]`,
not `[pcm1:play:dsp1.p0]`; `record` is not `rec`, so the record channel's line
did not match and its converting feeder chain was attributed to the playback
channel above it; and idle channels on a second, unused sound card were counted
as open streams, which by itself read as "the chain disagrees on rate" — this
suite's hard stop. Regression tests: `tests/test_dac_format_probe.py`.

### One thing this run found that is not about format

`./drc.sh status` reported `MPD: stopped` while `mpc` reported `[playing]`, on
FreeBSD only, and never printed the `Output audio:` line at all. Two causes:

* `sed -n 's/.*\[\(playing\|paused\)\].*/\1/p'` — `\|` alternation inside a
  BRE is a GNU extension. FreeBSD's `sed` does not error on it, it simply never
  matches.
* mpc ≥ 0.34 dropped the `audio:` line from `mpc status`; the format is now
  only available as `mpc status '%audioformat%'`.

Together these meant the `Rate: … MISMATCH` line — which §5 of this document
calls a hard stop, and which is the suite's only guard against a silently
resampling chain — **has never once fired on FreeBSD**, and every capture taken
by `bitperfect_runner.py` recorded `mpd_rate: null` and `rate_verdict: null`.
Both are fixed in `drc.sh`.

---

## Appendix — Linux/FreeBSD command equivalents

For manual work if the probe fails.

| Purpose | Linux | FreeBSD |
|---------|-------|---------|
| List audio devices | `cat /proc/asound/cards` | `cat /dev/sndstat` |
| USB device list | `lsusb` | `usbconfig list` |
| Full descriptors | `lsusb -v -d 152a:88c5`, or the raw blob at `/sys/bus/usb/devices/*/descriptors` | `usbconfig -d ugenX.Y dump_all_desc` |
| Negotiated format, per open stream | `cat /proc/asound/card*/pcm*/sub*/hw_params` | `sysctl hw.snd.verbose=2; cat /dev/sndstat` |
| Stream owner | `/proc/asound/.../status` → `owner_pid` | `fstat /dev/dsp*` |
| Selected USB alt | `cat /proc/asound/cardN/stream0` | `dmesg \| grep uaudio`, or `usbdump` for `SET_INTERFACE` |
| Driver-chosen format | `stream0` → `Format:` / `Bits:` | `dmesg \| grep uaudio` → `Play: … 32-bit S-LE` |
| Wire tap | `usbmon` (`scripts/bitperfect-tap-linux.sh`) | `usbdump` (`scripts/bitperfect-tap-freebsd.sh`) |

**Decoding a FreeBSD `AFMT` word.** From `sys/dev/sound/pcm/sound.h`, checked
against 15.1-RELEASE-p2:

```c
#define AFMT_ENCODING_MASK      0xf00fffff
#define AFMT_CHANNEL_MASK       0x07f00000      /* bits 20-26, NOT 24-28 */
#define AFMT_CHANNEL_SHIFT      20
#define AFMT_EXTCHANNEL_MASK    0x08000000
#define AFMT_EXTCHANNEL_SHIFT   27
```

So encoding is `fmt & 0xf00fffff`, channels are `(fmt & 0x07f00000) >> 20`, and
a stereo `S32_LE` stream is **`0x00201000`** — which is what this DAC actually
reports. An earlier revision of this table said the channel count sat at bit 24
and gave `0x02001000` as the example; both were wrong, and the probe inherited
the error, decoding every real stream as *zero* channels. Encodings that matter
here:

| Value | Name | Bytes/sample |
|-------|------|--------------|
| `0x00000010` | `S16_LE` | 2 |
| `0x00001000` | `S32_LE` | 4 |
| `0x00010000` | `S24_LE` | 3 (FreeBSD `pcm` semantics) |
| `0x00040000` | `S24_PACKED` | 3 |

**Finding FORMAT_TYPE_I by hand in a hex dump.** A UAC2 Type-I format
descriptor is six bytes: `06 24 02 01 <bSubslotSize> <bBitResolution>`. For
this DAC you should find exactly these, in this order:

```
06 24 02 01 04 18     alt 1 — 4-byte subslot, 0x18 = 24 valid bits
06 24 02 01 02 10     alt 2 — 2-byte subslot, 0x10 = 16 valid bits
06 24 02 01 04 20     alt 3 — 4-byte subslot, 0x20 = 32 valid bits
06 24 02 01 04 20     alt 4 — same, but bmFormats marks it RAW/DSD
```

(twice over, because of the duplicated configuration descriptor noted in §2).
