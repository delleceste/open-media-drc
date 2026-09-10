# How the bits get to the DAC, and how we check they arrive intact

> **Start here for the whole story:** [`doc/SOUND-QUALITY-INVESTIGATION.md`](SOUND-QUALITY-INVESTIGATION.md) is the complete record — what was measured, what it ruled out, and what is still open.

A plain-language companion to `doc/DAC-FORMAT-ALIGNMENT.md` (the findings) and
`doc/DAC-FORMAT-CROSS-OS-PROCEDURE.md` (the runbook). This one explains what
the problem *is*, what could go wrong, what it would sound like, and how to
read the answer. No prior USB-audio knowledge assumed.

---

## 1. The question

Sound quality differs between the Linux box and the FreeBSD box. One
possibility — the one worth eliminating first, because it is cheap to test and
catastrophic when true — is that one of the two operating systems is handing
the DAC samples that are **correctly valued but wrongly positioned**: shifted
by a byte, or padded on the wrong end.

This is worth taking seriously because it is a *silent* failure. Nothing logs
an error. The DAC does not complain. Audio plays. It just sounds wrong.

## 2. A sample's journey

A stereo 24-bit 192 kHz track is, on disk, a long list of number pairs — one
pair per instant, 192 000 pairs per second. Each number says how far the
speaker cone should be from its resting position at that instant.

To get from the file to the speaker, each pair passes through:

```
file → decoder → loopback → brutefir (room correction) → USB → DAC → analogue
```

At every arrow the numbers are handed from one piece of software to the next,
and at every arrow both sides must agree on **how the numbers are packed into
bytes**. When they disagree, the numbers survive but their *meaning* does not.

## 3. Containers, valid bits, and justification

Three separate ideas, easy to conflate, and the whole problem lives in the gap
between them.

**The container** is how many bytes each number occupies. A 32-bit container
is 4 bytes per number per channel. A 16-bit container is 2. This is purely
about *spacing* — where one number ends and the next begins.

**The valid bits** are how many of those bits carry real information. A DAC
with 24-bit converters ignores anything finer, even if you hand it 32 bits.

**The justification** is *where* the valid bits sit inside the container —
pushed up against the top (left-justified / MSB-aligned) or the bottom
(right-justified / LSB-aligned).

Concretely, one 24-bit sample with value `AABBCC` inside a 4-byte container:

```
left-justified  (MSB-aligned):   AA BB CC 00      ← the pad is at the bottom
right-justified (LSB-aligned):   00 AA BB CC      ← the pad is at the top
packed, 3 bytes, no container:      AA BB CC      ← no pad at all
```

All three carry the same audio. All three are different byte streams. Hand one
to something expecting another and you get a defect, not an error message.

> Byte order adds a twist: these buses are little-endian, so a 4-byte word is
> transmitted lowest-byte-first. `AA BB CC 00` above is written the way the
> *value* reads; on the wire the pad byte physically goes first. This is why
> reasoning about "which end is padded" must be done in terms of the numeric
> value, not the transmission order — and why the tooling compares whole
> streams rather than eyeballing hex.

## 4. What goes wrong, and what it sounds like

**Stride mismatch — the serious one.** Producer writes 4 bytes per sample,
consumer reads 3. Sample 1 is fine. Sample 2 starts one byte late and every
sample after it drifts further. Within a fraction of a second you are reading
sample boundaries at essentially random offsets.

The result is **full-scale noise**. Not "harsh", not "thin" — unlistenable,
and loud, because random bytes interpreted as samples average to roughly
maximum amplitude. Anyone hearing this knows instantly. So if the system plays
recognisable music, a stride mismatch is *not* happening on that path.

**Justification mismatch — the quiet one.** Container and stride agree, but
the valid bits sit at the wrong end. Everything is multiplied or divided by
256, exactly 48 dB:

- Right-justified data read as full-scale → **48 dB too loud**, hard-clipped.
- Full-scale data read as right-justified → **48 dB too quiet**, but clean.

The second is genuinely easy to miss if you reach for the volume knob.

**Silent resampling — the subtle one, and the most likely.** No alignment
problem at all: two stages simply disagree about the sample *rate*, so
something in between quietly resamples. The music is perfectly recognisable
and just sounds slightly dull or grainy. **This is the failure mode that best
matches "it sounds worse but I cannot say why."**

The takeaway: *"it sounds a bit off" and "the bytes are shifted" are almost
mutually exclusive symptoms.* A shift is dramatic. Subtle degradation points
somewhere else.

## 5. What this particular DAC asks for

A USB DAC advertises the formats it accepts as a menu of "alt-settings". The
OKTO DAC8STEREO offers four for playback:

| Alt | Bytes/sample | Valid bits | What it is |
|-----|--------------|-----------|------------|
| **1** | **4** | **24** | 24-bit audio in a 32-bit container ← *both OSes use this* |
| 2 | 2 | 16 | native 16-bit |
| 3 | 4 | 32 | full 32-bit |
| 4 | 4 | 32 | DSD |

Alt 1 is the interesting one: 4 bytes of space, 24 bits of content. The USB
audio specification settles the justification question — samples are
**left-justified**, valid bits at the top, pad at the bottom. So the DAC takes
the top 24 bits of each 32-bit word and discards the lowest byte.

Which means: **send it an ordinary full-range 32-bit stream and it is
correct.** The 8 bits you lose sit around −144 dBFS, far below the noise floor
of any analogue circuit ever built.

Worth noting for contrast: the ESI U24XL on the same host advertises a
**3-byte** container. Any helper written against that device and reused for the
OKTO would produce exactly the stride mismatch of §4.

## 6. What each operating system does with that menu

Both pick alt 1 — by different reasoning, which is why this needed checking.

**Linux** selects the container from the byte count (4 bytes → `S32_LE`) and
keeps the "24 valid bits" figure as a note to itself.

**FreeBSD** computes the depth as *bytes × 8*, so it reads alt 1 as "32-bit"
and **ignores the declared 24**. It then keeps only one alt per sample rate,
first in descriptor order — which is alt 1.

Different logic, same destination. This is also why the DAC's front panel shows
"24" even for a 16-bit track: the panel reports the alt-setting's declared
resolution, not the file's. Cosmetic.

## 7. What we actually found

**On Linux, measured live at 192 kHz with the chain running:** every hop —
player → loopback, loopback → brutefir, brutefir → DAC — is `S32_LE`, 2
channels, 192 000 Hz. No stage converts anything. The DAC is on alt 1.

**Across the two operating systems, from earlier work in `bp-results/`:** the
actual bytes on the USB wire were captured on both machines (`usbmon` on
Linux, `usbdump` on FreeBSD) while playing the same file. The payloads have
**identical SHA-256**. Not "equivalent" — identical.

So for the direct path, the shifted-bytes hypothesis is **dead**. Both systems
hand the DAC the same bytes, in the same container, with the same
justification.

**The gap:** every one of those wire captures was taken on the *direct* path,
which bypasses `virtual_oss` and `brutefir`. The DRC path has never been
wire-verified on either machine. If something real is happening, that is where
it is.

There is one honest caveat about the DRC path worth stating plainly: brutefir
does its arithmetic in 64-bit floating point and writes 32-bit integers with
dither switched off, so the conversion truncates rather than rounds. That is a
real, deliberate loss — and it is around −144 dBFS. It is not what anyone is
hearing.

## 8. Running the check yourself

```sh
./scripts/dac-format-probe.py --out bp-results/dac-format-<os>.json
./scripts/dac-format-probe.py --compare \
    bp-results/dac-format-linux.json bp-results/dac-format-freebsd.json
```

The probe reads `/proc` and `sysctl`, opens no audio device and changes
nothing. **Run it while music is playing** — an idle chain has no open streams,
and the open streams are the interesting part.

Reading the output:

- **The alt table** should be identical on both machines. It describes the
  hardware, and the hardware does not change when you change operating system.
  A difference here means one side mis-read the descriptors.
- **`open_streams`** is the chain, one line per hop. You want one format and
  one rate down the whole list. Two different rates means something is
  resampling; two different formats means something is converting.
- **`wire container width`** is the byte-alignment answer. 4 on both sides is
  correct. 4 on one and 3 on the other is the §4 stride mismatch.
- **Node names will differ** (`hw:2,0` vs `/dev/dsp.dac`) and that is fine.
  So will buffer sizes and brutefir's partition layout — `32768,16` on Linux
  against `8192,64` on FreeBSD is deliberate, same total filter taps.

Exit status 0 is MATCH, 1 is MISMATCH with the findings listed.

## 9. What the probe cannot tell you

Three things need a human on the FreeBSD box; `DAC-FORMAT-CROSS-OS-PROCEDURE.md`
§4 has the commands.

**Which alt is really selected.** Linux states it outright. FreeBSD does not,
so it must be inferred from the driver's start-up message — or settled
properly by capturing the `SET_INTERFACE` request on the USB bus as playback
begins.

**Whether the kernel inserted a mixer.** FreeBSD can slip a resampling,
volume-applying stage in front of the DAC when "virtual channels" are enabled.
Linux's raw devices have no equivalent, so a comparison cannot catch it — it
has to be checked directly.

**What MPD negotiated with `virtual_oss`.** This is the one place a real shift
could still hide, and it is a genuine ambiguity rather than a bug in anyone's
code: the OSS constant `AFMT_S24_LE` means *24 bits inside a 4-byte container*
in the OSS version 4 documentation, but FreeBSD's own audio layer treats
24-bit formats as **3-byte packed**. MPD's internal 24-bit format is 4 bytes.
Three parties, two incompatible readings of one name — precisely the §4 stride
mismatch waiting to happen.

It would only bite on 24-bit material through the DRC path, which is why the
procedure insists on testing **16-, 24- and 32-bit** tracks. A 32-bit-only test
cannot reveal it.

## 10. If everything matches

Then the format and alignment reaching the DAC are the same on both systems,
and the difference you are hearing is not a container problem. Next stops, in
order of likelihood:

1. **Sample-rate mismatch** in the DRC chain — `./drc.sh status` reports it.
2. **The DRC path itself**, still unverified on either OS. The method is in
   `BIT-PERFECT-VERIFICATION.md`: replace the room-correction filter with a
   single `1.0` followed by zeros, which makes the convolution an identity
   operation, then capture the wire and compare against the source delayed by
   the filter's latency. That isolates `virtual_oss` and brutefir's I/O from
   the filtering.
3. **Everything downstream of the bits** — the filter coefficients themselves,
   clocking, or the analogue side. All beyond what this tooling can see.

---

### Glossary

| Term | Meaning |
|------|---------|
| **Alt-setting** | One entry on the menu of formats a USB device advertises. The host picks one and stays on it. |
| **Container / subslot** | Bytes allocated per sample per channel. Determines spacing, not content. |
| **Valid bits / bit resolution** | How many bits in the container carry real audio. |
| **Justification** | Whether the valid bits sit at the top (MSB/left) or bottom (LSB/right) of the container. |
| **Stride** | Bytes from the start of one sample to the start of the next. Producer and consumer disagreeing is the catastrophic case. |
| **Bit-perfect** | The bytes the DAC receives are exactly the bytes in the file — no volume, resampling, dither or conversion. |
| **Isochronous** | USB transfer mode for audio: guaranteed timing, no retries. A dropped packet is gone. |
| **Async / feedback endpoint** | The DAC runs on its own clock and tells the host how fast to send. Better than the host dictating the rate. |
| **usbmon / usbdump** | The USB sniffers on Linux and FreeBSD. The only way to see what truly left the machine. |
| **dBFS** | Decibels relative to full scale; 0 dBFS is the loudest representable sample, everything else negative. |
