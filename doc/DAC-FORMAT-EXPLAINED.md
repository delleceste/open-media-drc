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

**The gap, now closed:** every one of those wire captures was taken on the
*direct* path, which bypasses `virtual_oss` and `brutefir`. For a while the DRC
path — the one music actually takes — had not been wire-verified on either
machine. On 2026-09-10 it was, with a **null test** (§11): the same material
through the same filter on both systems, the USB captures subtracted from each
other sample by sample. **Zero differing samples.** The two DRC chains put
identical bytes on the wire.

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

> **All three were settled on the FreeBSD run of 2026-09-10**
> (`DAC-FORMAT-CROSS-OS-PROCEDURE.md` §7). The alt-setting was captured on the
> wire — `SET_INTERFACE wValue=1 wIndex=1`, alt 1. The kernel's feeder chain is
> `feeder_root` only, flagged `BITPERFECT`: no mixer, no volume, no resampler.
> And MPD presents a 24-bit track as `44100:32:2`, never as any 24-bit OSS
> format, so the stride ambiguity above cannot occur on this chain.

## 10. Everything matched — so where next?

It did match. The format and alignment reaching the DAC are the same on both
systems, and the cross-OS null (§11) showed the whole DRC chain is
bit-identical too. The difference being heard is not a container problem and
not a convolver or loopback problem. What is left, in order of likelihood:

1. **Sample-rate mismatch with mixed-rate material** — **half done.** The
   chain runs at one fixed rate; when a track at a different rate plays through
   `DRC-native`, MPD resamples it with soxr (the loopback cannot; `virtual_oss`
   only would with `-S`, which is not passed). `./drc.sh status` flags it as
   `[MISMATCH]` — on FreeBSD that guard never fired until 2026-09-10. On Linux
   it has now been measured: the guard works, and the resample lands within
   2.5–4 dB of the 32-bit floor, i.e. costs nothing measurable. The FreeBSD
   measurement is pending: `RATE-MISMATCH-PROCEDURE.md`.
2. **Everything downstream of the wire** — the DAC's clocking and analogue
   stages, USB electrical noise, grounding. Beyond what a wire capture can see.
3. **Not in the equipment** — level matching and expectation. Worth saying
   plainly, because the measured digital difference is now exactly zero.

(One path is still unverified against the *source* rather than against the
other machine: the DRC chain with a unit-impulse filter — a single `1.0`
followed by zeros — which turns the convolution into an identity operation.
The method is in `BIT-PERFECT-VERIFICATION.md`. It would prove the chain
transparent in absolute terms; the null proved the two operating systems
equal.)

## 11. The null test — what it is, and how to read one

A null test is the audio equivalent of `diff`. You subtract one signal from
another; if they are identical the result is exactly zero — a *null*. Anything
left over **is** the difference, isolated, with a number attached. The name
comes from the result you hope for: nothing.

**The classic analogue version.** Invert the polarity of one signal and sum it
with the other; identical signals cancel to silence. Feed the same input
through two amplifiers, null their outputs, and whatever you can still hear is
precisely what one does that the other does not.

**Why it beats listening for this question.** "Do these sound the same?"
depends on auditory memory — reliable for only a few seconds of timbre, which a
two-minute reboot between systems destroys — and on knowing which one is
playing. A null replaces that with "what is left when I subtract one from the
other?" If nothing is left, *no* listening test can tell them apart, because
there is nothing to tell. If something is left, you do not just learn that they
differ: you get the difference by itself, so you can measure its level, see
where it first appears, and even listen to it alone.

### What was actually done

1. **Held everything identical, and proved it by hash.** Same input file
   (sha256 `01317af6…` on both machines), same filter coefficients
   (`0b3a9c24…` / `a69dd603…` on both). The tool refuses to run when these
   disagree — a null between two different filters would come out non-zero and
   be misread as an operating-system difference.
2. **Played it through each machine's full DRC chain** and captured the bytes
   leaving for the DAC — `usbmon` on Linux, `usbdump` on FreeBSD.
3. **Aligned the two captures.** They never start on the same sample: priming
   differs, the convolver's latency differs, and each capture began at an
   arbitrary moment. Cross-correlation slides one against the other and finds
   the offset where they match best.
4. **Subtracted** the overlapping region, sample by sample.

The result:

```
aligned at lag -5,506,220 frames (r = 1.000000)
compared 3,525,692 frames (18.363 s)
differing samples : 0 (0.000e+00 of all)
max difference    : 0 LSB of S32
null depth        : -inf dBFS

IDENTICAL: the two chains produced the same bytes
```

### Reading the numbers

- **`r`** — the alignment confidence: how well the two captures correlate at
  the best offset. `1.000000` is perfect. Before it worked, two attempts
  returned `r = 0.072`, which correctly meant "these are not recordings of the
  same thing" — the music player had been refilling its queue, so one capture
  was full of full-level music while the test signal is almost silent. **A low
  `r` means stop and find out why, never "the systems differ".**
- **`differing samples`** — how many individual samples are not equal. Zero out
  of 7,051,384 here.
- **`max difference`** — the largest single difference, in *LSBs* (the smallest
  step a 32-bit sample can take). One LSB is about −192 dBFS, so even a result
  of 1 or 2 would be inaudible by an enormous margin — that is what two
  different partitionings of the same convolution *could* have produced. It
  produced zero.
- **`null depth`** — the RMS of the difference relative to full scale. It reads
  `-inf` because the difference is exactly zero and the logarithm of zero has
  no floor. A very good real-world null might read −140 dB; a genuine defect
  might read −60 dB.

### What a null does *not* prove

- **Only what is upstream of the tap.** These captures tapped the USB wire, so
  the DAC's own clock, its analogue stages and the USB electrical environment
  are untouched by the result.
- **Only the material and rate tested.** One signal, at 192 kHz, with the
  material's rate matching the chain's. This is exactly why the rate-mismatch
  question in §10 is still open: a mismatch never occurred during the test.
- **Only if everything else really was identical** — hence the provenance
  checks, and hence why the discovery that the two machines had been
  convolving different coefficients (`CROSS-OS-FILTER-COEFFICIENTS.md`) had to
  be resolved before the test could mean anything.

### The self-null

The same technique pointed at *one* machine: two runs of FreeBSD's chain,
nulled against each other. That does not test whether two systems are
equivalent — it tests whether one system is **deterministic**. It also came
out identical (0 of 4,045,609 frames), which ruled out xruns, dropouts, sample
slips and timing nondeterminism in a single measurement. Whatever FreeBSD does,
it does the same way every time.

### Why the test signal looks the way it does

A near-silent (~−90 dBFS) counter in which every left/right sample pair is
unique across the whole file. Near-silent so it is inaudible if it ever reaches
a loudspeaker, yet every bit of it is significant, so truncation, dither,
volume or resampling all corrupt it detectably. Unique pairs so alignment can
never lock onto the wrong offset, and any altered, dropped or duplicated sample
shows up wherever it happens. It is generated by `tests/gen-bitperfect-wav.py`
and comes out byte-identical on both operating systems.

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
| **Null test** | Subtract one signal from another. Identical signals leave exactly nothing; whatever remains *is* the difference. See §11. |
| **Self-null** | A null of one system against itself, run twice. Tests determinism, not equivalence. |
| **Null depth** | How far down the leftover difference sits, in dB relative to full scale. `-inf` means exactly zero. |
| **LSB** | Least-significant bit: the smallest step a sample can take. One LSB of a 32-bit sample is about −192 dBFS. |
| **Alignment / cross-correlation** | Sliding two recordings against each other to find the offset where they match; `r` is how well they match there (1.0 = perfectly). |
| **Provenance** | The recorded facts about how a capture was made — OS, config, coefficient hashes, rates. Compared before a null so it only runs between like and like. |
