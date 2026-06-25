# How the A/V sync delay is calculated (theory + code, from scratch)

When Digital Room Correction (DRC) is active, the sound you hear is **late**: the
audio is pushed through brutefir's correction filter before it reaches the DAC,
and that processing takes time. The picture, meanwhile, is drawn immediately. So
to keep lips and sound together we **delay the video** by however long the audio
path takes (`DRC_VIDEO_DELAY` in `play-bluray.sh`).

This document explains, from first principles, **how we get that number** —
what the filter file contains, why its "peak" is a delay, the Python that finds
it, and the extra latency brutefir and virtual_oss add on top. No prior DSP
knowledge assumed.

---

## 1. What is in `L.raw` / `R.raw`?

A brutefir correction filter is an **FIR filter**: a long list of numbers called
*coefficients* or *taps*. `filters/120.blue/192000/L.raw` is exactly that list,
stored as raw binary with **no header**:

- The brutefir config says `format: "FLOAT64_LE"`, i.e. each number is a
  **64-bit (8-byte) little-endian double**.
- The file is `4 194 304` bytes. Number of taps = `4194304 / 8 = 524 288`
  (which is 2¹⁹).
- The sample rate of this filter is `192000` Hz (it lives in the `.../192000/`
  folder and the brutefir config sets `sampling_rate: 192000`).

That list of 524 288 numbers **is the filter's impulse response**, written `h[n]`.
That single fact is the key to everything below.

---

## 2. Why the impulse response *is* the delay (the theory)

### 2.1 What a filter does: convolution

An FIR filter turns an input signal `x` into an output signal `y` by
**convolution**:

```
y[n] = Σ  h[k] · x[n − k]
       k=0..N−1
```

In words: every output sample is a weighted blend of the most recent `N` input
samples, the weights being the taps `h`.

### 2.2 The impulse test

Feed the filter a single, infinitely short "click" — a **unit impulse**: an input
that is `1` at time 0 and `0` everywhere else (written `δ[n]`). Plug `x = δ` into
the formula and every term vanishes except the one where `x` is 1:

```
y[n] = h[n]
```

**The output is the coefficient list itself.** That is *why* the coefficients are
called the impulse response: they literally are what comes out when you put one
click in. So `L.raw` is a picture of "what the speaker+room produces, after
correction, in response to one click."

### 2.3 Where is the sound? At the peak.

A click goes in at time 0. When does the bulk of it come *out*? At the moment
`h[n]` is largest — its **peak**. If the biggest coefficient is at index `p`,
then a transient entering at time `0` emerges, loudest, at time `p` (in samples).

That offset `p` is the filter's **group delay** for the main wavefront — the
amount the filter delays the sound. Convert to seconds by dividing by the sample
rate:

```
delay_seconds = p / sample_rate
```

### 2.4 Why "peak", and why it isn't in the middle

You might expect the peak in the *middle* of the file (a so-called *linear-phase*
filter, peak at `N/2 = 262 144`). It is **not** — our peak is at `96 000`. This
is a **mixed-phase** room-correction filter: the designer reserved
`96 000 samples = 0.5 s` *before* the peak for **pre-ringing** (the part that
corrects phase/time smearing), and left the long tail *after* the peak for the
rest of the correction. So:

```
|h[n]|
        pre-ring (phase fix)        peak            long correction tail
   |......................^.................................................|
   0                   96 000                                          524 288
   |<----- 0.500 s ----->|<--------------- 2.23 s ----------------------->|
```

The whole filter is `524288 / 192000 = 2.73 s` long, but only the **0.5 s up to
the peak** is "delay you must wait through" before the main sound arrives. We find
`p` by locating the largest-magnitude tap — `argmax(|h|)`.

> We use `|h|` (absolute value) because coefficients can be negative; the peak of
> a waveform can be a large *negative* number. Indeed ours is `h[96000] ≈ −0.183`.

---

## 3. The Python, line by line

```python
import array

a = array.array('d')                       # 'd' = C double = 8-byte float = FLOAT64
with open('L.raw', 'rb') as fh:            # 'rb' = read raw bytes (no text decoding)
    a.frombytes(fh.read())                 # load the whole file into the array
n = len(a)                                 # number of taps = bytes/8 = 524288
pi = max(range(n), key=lambda i: abs(a[i]))# index of the largest |coefficient|
print(pi, pi / 192000)                     # peak index, and that index in seconds
```

What each part does and *why it is correct here*:

| line | what it does | why it's right |
| --- | --- | --- |
| `array.array('d')` | makes an array whose elements are C `double`s | `'d'` is 8 bytes, matching `FLOAT64`. Python's `array` uses the machine's **native byte order**; this box is x86 = **little-endian**, matching `FLOAT64_LE`. |
| `'rb'` | opens in binary | the file is raw numbers, not text — decoding as text would corrupt it |
| `frombytes(fh.read())` | reinterprets the raw bytes as doubles | `4 194 304 bytes ÷ 8 = 524 288` doubles, exactly the tap count |
| `len(a)` | counts taps | sanity check: should equal file size / 8 |
| `max(range(n), key=lambda i: abs(a[i]))` | returns the **index** of the max-magnitude tap | this index *is* `p`, the group delay in samples |
| `pi / 192000` | samples → seconds | divide by the sample rate of *this* filter |

Result: `pi = 96000` (R) / `96002` (L) → `96000 / 192000 = 0.5000 s`.

> **Endianness foolproofing.** If you ever run this on a big-endian machine, or on
> a `FLOAT32_LE` filter, the native `array` read would be wrong. The portable form
> is explicit: `numpy.fromfile('L.raw', dtype='<f8')` (`<f8` = little-endian
> 8-byte float; use `<f4` for FLOAT32). On x86 with FLOAT64 the simple `array`
> version is identical and dependency-free.

---

## 4. From "filter delay" to "what the video must wait for"

The 0.5 s above is only the **filter's own** delay. Audio actually travels:

```
mpv ─► /dev/dsp.play ─► virtual_oss ─► /dev/dsp.loop ─► brutefir ─► /dev/dsp0 ─► DAC
                         (buffer)                       (filter)     (buffer)
```

Three latencies stack up:

### 4.1 Filter group delay — 0.500 s  (exact, from §2–3)
Deterministic, read straight from the coefficients. `96000 / 192000`.

### 4.2 brutefir engine latency — 0.171 s
brutefir can't convolve a 524 288-tap filter sample-by-sample in real time (far
too many multiplies per sample). It uses **partitioned (block) FFT convolution**:
it chops the work into blocks of `filter_length` samples — here the config says
`filter_length: 32768,16` (partition size **32768**, ×16 partitions = 524 288).

A block engine must **collect a whole block of input before it can produce the
matching block of output**. That waiting *is* latency, and it equals one
partition:

```
32768 / 192000 = 0.1707 s
```

This is *separate from and additional to* the filter's group delay: the block
latency is "when can the engine start emitting", the group delay is "where in the
emitted stream the energy sits".

### 4.3 virtual_oss buffering — a bit more
virtual_oss bridges `dsp.play`→`dsp.loop` with its own ring buffer (`-s 200ms`
in the launch args) and there is buffering again on the `dsp0` output. This adds
tens of milliseconds that depend on runtime settings, so we don't read it from a
file — it's the part you confirm by eye.

### 4.4 Total

```
   0.500 s   filter group delay      (exact, from the .raw peak)
 + 0.171 s   brutefir partition       (exact, from filter_length)
 + ~?         virtual_oss buffering    (runtime; small)
 ─────────
 ≈ 0.67 s+   →  DRC_VIDEO_DELAY
```

That is why `DRC_VIDEO_DELAY` defaults to **0.67** (filter + partition), with the
small virtual_oss remainder left for eyeball fine-tuning.

---

## 5. How the number is applied in mpv

- `--audio-delay=<sec>`: **negative delays the video** (positive delays audio).
  We pass `-0.67` so mpv presents each frame 0.67 s later, lining the picture up
  with the late audio. (See `play-bluray.sh`.)
- `--sub-delay=<sec>`: subtitles are timed **relative to the video**, and we only
  shifted the *audio* — so subs already ride with the delayed picture and need
  **no** offset (`DRC_SUB_DELAY=0`). See [README](README.md#audio-drc-aware).
- Live fine-tune: mpv `z` / `Z` nudge subtitle timing; the audio/video offset is
  the `DRC_VIDEO_DELAY` knob (re-launch to change, or adjust live in mpv with the
  audio-delay bindings).

---

## 6. Recomputing for a different filter or rate (foolproof checklist)

1. **Confirm the format** in the brutefir config: `format: "FLOAT64_LE"` → 8-byte
   doubles. (`FLOAT32_LE` → 4-byte; use `<f4` / `'f'`.)
2. **Confirm the sample rate** — it must be the rate of *that* filter folder
   (e.g. `48000`, `96000`, `192000`), not the disc's rate. Divide by *this*.
3. **Find the peak** (dependency-free, FLOAT64, little-endian host):
   ```sh
   python3 -c 'import array;a=array.array("d");a.frombytes(open("L.raw","rb").read());i=max(range(len(a)),key=lambda k:abs(a[k]));print(i,"samples =",i/192000,"s")'
   ```
   (portable form: `python3 -c 'import numpy as np;h=np.fromfile("L.raw",dtype="<f8");print(h.argmax() if abs(h.max())>=abs(h.min()) else h.argmin())'`)
4. **Add the brutefir partition**: read `filter_length: P,parts` from the config;
   add `P / sample_rate` seconds.
5. **Set `DRC_VIDEO_DELAY`** to the sum, then verify on a lip-sync-heavy scene and
   nudge.

### Sanity checks
- `file_size_bytes / bytes_per_tap` should equal the tap count, and
  `P × parts` from `filter_length` should equal that same count. For us:
  `4194304/8 = 524288 = 32768 × 16`. ✓
- The peak index should be **less than** the tap count and usually well under
  `N/2` for a sensibly-delayed mixed-phase filter. `96000 < 262144`. ✓
- `peak / sample_rate` landing on a clean value (here exactly 0.5 s) is a hint the
  filter was designed to a target pre-ring budget — reassuring, not required.

---

## 7. Where else this delay is used: the omdrc-ctrl spectrum analyzer

`play-bluray.sh` uses a **hardcoded** `DRC_VIDEO_DELAY=0.67` because the video
path always runs brutefir at 192 kHz (resampled), so the number is fixed.

The omdrc-ctrl live spectrum/VU analyzer faces the *same* problem from the other
side: it taps the **pre-DRC** MPD FIFO, so its display would run *ahead* of the
audible sound by exactly this delay. There it is **computed at runtime** rather
than hardcoded, because music plays at the native rate (44.1–192 kHz) and the
partition term is rate-dependent:

```
delay = argmax(|h|)/rate          (group delay, from the active L.raw — §2–3)
      + filter_length/rate         (one brutefir partition — §4.2)
      + drc_delay_trim_ms          (loopback/output buffering — §4.3)
```

It reads the **active** brutefir conf (sampling rate + coeff file) and
`filter_length` from `~/.config/BruteFIR/brutefir_defaults.conf`, caches the
result keyed on file path + mtime, and recomputes only when the filter / preset /
rate / defaults change. At 192 kHz this reproduces the 0.67 s derived above; at
48 kHz native it correctly grows to ~1.18 s. See
[`omdrc-ctrl/SPECTRUM_ANALYZER.md` → DRC Sync](../omdrc-ctrl/SPECTRUM_ANALYZER.md#drc-sync).
