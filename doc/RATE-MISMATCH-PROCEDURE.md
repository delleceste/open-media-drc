# Rate-mismatch test — Linux results, and the FreeBSD procedure

**Audience: the Claude Code session on the FreeBSD host (`bee`).** Read §1–§3
for context, run §4, judge with §5, report with §7.

Linux half run 2026-09-11 on `arki` (Arch 7.2.3-arch1-3), same NUC, same disk.
Background: `doc/SOUND-QUALITY-INVESTIGATION.md` §9 (why this is the last open
digital question) and `doc/DAC-FORMAT-EXPLAINED.md` §11 (what a null test is).

---

## 1. The question

When the rate of the track differs from the rate the DRC chain runs at,
something has to resample. Three things were unknown:

1. Does the guard in `drc.sh status` notice? It never fired on FreeBSD until
   `f1aaf3a` (2026-09-10) fixed a GNU-only `sed` pattern and mpc ≥ 0.34's
   missing `audio:` line.
2. **Who** resamples, and how well? This is the part that could explain an
   audible FreeBSD-only difference: a mediocre resampler loses clarity while
   leaving the music perfectly recognisable. (The man page already says who —
   §3.1 — but nobody had measured how well, on either OS.)
3. Does `resamp` mode's forced 24-bit output cross the OSS boundary safely on
   FreeBSD? (See §3.3 — this was never tested.)

Every null run so far was taken at one fixed rate with matching material, so
none of them could see any of this.

## 2. What Linux showed

Chain: MPD → `snd-aloop` → BruteFIR (flat, `dirac pulse`, 0 dB) → DAC. The flat
filter makes the chain a pass-through, so anything that changes the signal is
something other than the filter.

DAC today: **Cambridge Audio DacMagic 100**, not the OKTO — alt 1, 4-byte subslot,
24 valid bits, `S32_LE`, async feedback, 44.1–192 kHz. Same container as the
OKTO. Irrelevant to this test (the guard never looks at the DAC; see
`SOUND-QUALITY-INVESTIGATION.md` §9), but record which DAC is attached on your
side too.

### 2.1 The guard works, both directions, every bit depth

Full output: `bp-results/rate-guard-matrix-linux.txt`.

| chain | material | `mpc %audioformat%` | MPD → loopback opened at | guard |
|---|---|---|---|---|
| 192 k | 192 k / 24, 32 | `192000:32:2` | `S32_LE @ 192000` | `[match]` |
| 192 k | 44.1 k / 16, 24, 32 | `44100:16:2`, `44100:32:2` | `S32_LE @ 192000` | `[MISMATCH]` |
| 44.1 k | 44.1 k / 16, 24, 32 | `44100:…` | `S32_LE @ 44100` | `[match]` |
| 44.1 k | 192 k / 24, 32 | `192000:32:2` | `S32_LE @ 44100` | `[MISMATCH]` |
| resamp | 44.1 k / 24 | `44100:32:2` | `S32_LE @ 192000` | `[MISMATCH]` — deliberate, see §2.5 |
| resamp | 192 k / 24 | `192000:32:2` | `S32_LE @ 192000` | `[match]` |

`%audioformat%` is the **decoder's** format (the source rate), which is why the
guard can see a mismatch at all. Had it reported MPD's output format, a resample
done inside MPD would have been invisible to it.

### 2.2 On Linux, MPD resamples — the loopback cannot

Column 4 is the proof: whenever decoder and chain disagree, MPD opens the
loopback **at the chain's rate**. `snd-aloop` does not resample, so MPD converts
before it, with its configured resampler — `soxr`, quality "very high"
(`mpd --version` lists `soxr` under `Filters:`).

### 2.3 How good is that resample? At the 32-bit floor.

Measured with a new, alignment-free instrument: `scripts/gen-tone-wav.py` makes a
near-silent (−90 dBFS) 997 Hz / 1499 Hz two-tone file; the chain plays it; the
USB wire is captured; `scripts/resampler-residual.py` fits each tone back out
and reports what remains — the resampler's aliasing, imaging and noise — in dB
below the tone and in dB above the 32-bit rounding floor (−197.4 dBFS rms).

Validated first on synthetic input: a perfect tone lands exactly on the floor
(−0.0 dB); a crude linear-interpolation resample lands **+42 dB** above it with
its spurs at 44100 ± 997 Hz — exactly the images a poor resampler produces.

| run (flat chain) | error below tone | vs 32-bit floor | max \|residual\| | artifact |
|---|---|---|---|---|
| 192 k tone → 192 k chain (matched control) | −104.4 dB | **−0.0 dB** | 1 LSB | `rate-192000-linux-tone192k-matched` |
| 44.1 k tone → 192 k chain (mismatch, up) | −100.4 dB | **+4.0 dB** | 2 LSB | `rate-192000-linux-tone44k-mismatch` |
| 192 k tone → 44.1 k chain (mismatch, down) | −101.9 dB | **+2.5 dB** | 1 LSB | `rate-44100-linux-tone192k-mismatch` |
| 44.1 k tone, `resamp` mode | −100.4 dB | **+4.0 dB** | 2 LSB | `rate-resamp-linux-tone44k` |

(`.residual.json` next to each in `bp-results/`.) The only residual spurs are odd
harmonics of the tones around −200 dBFS — ordinary rounding. **On Linux a rate
mismatch costs nothing measurable**: MPD's soxr resamples to within 2.5–4 dB of
what 32-bit arithmetic can represent at all.

### 2.4 `resamp` mode's 24-bit path is bit-perfect on Linux

`--drc-output DRC-resamp --reference source` with the 192 k / 24-bit counter (no
rate change needed): **BIT-PERFECT — all 15 360 000 reference bytes identical on
the USB wire** (`rate-resamp-linux-counter192k-s24`).

Side observation: `resamp`'s forced `192000:24:2` did **not** reduce resolution
to 24 bits on Linux — the resamp tone's residual is identical to the native
one's, where 24-bit truncation would have put it ~48 dB higher. MPD appears to
hand the loopback 32-bit because the loopback only accepts `S32_LE`. FreeBSD's
`virtual_oss` *does* accept 24-bit formats, which is exactly why §3.3 matters.

### 2.5 Three things learned about the tools

* **`resamp` mode made the guard cry wolf.** A 44.1 k track in `resamp` mode is
  resampled *on purpose* by soxr, yet `status` printed a bare `[MISMATCH]`. Now
  `[MISMATCH: deliberate - resamp mode resamples with MPD soxr]`. The word
  MISMATCH is kept on purpose — `bitperfect_runner.py` keys on it, and a
  resampler is in the path either way.
* **Two runs through the resampler are not sample-identical.** The mismatched
  counter, captured twice: a whole-sample null reported DIFFERENT (58 % of
  samples, −55.3 dBFS — mostly from the capture edges). After a sub-sample
  realignment of +0.195 output samples the runs agree to ~112 dB below the
  signal (−154.2 dBFS), not to the floor. The obvious explanation — MPD starting
  the two runs a whole *input* sample apart, which at 44.1→192 k is a 0.354-sample
  step — does **not** fit the measured offset; the mechanism is unexplained.
  Consequence: **for a resampled path, a null is the wrong instrument.** Compare
  `resampler-residual.py` numbers instead. `bitperfect-null.py` now refuses
  captures taken with `--allow-resample`, with this explanation.
* **The capture tools read the wrong rate.** A capture's `rate` is the
  *material's*; on the DRC route the wire runs at the *chain's*. Analysed at
  44.1 k, a 997 Hz tone resampled to 192 k "appeared" at 229 Hz (= 997 × 44100 /
  192000). The runner now records `wire_rate`; the null and the analyser use it
  (falling back to `chain.brutefir_rate` for older captures).

## 3. What FreeBSD has to answer

### 3.1 Who resamples on FreeBSD? Documented: MPD. Not yet measured.

`virtual_oss(8)` answers it, and `BIT-PERFECT-VERIFICATION.md` already quotes
it (added 2026-09-10, `42975a6`):

```
-S      Enable automatic DSP rate resampling.
-Q quality
        Set resampling quality: 0=best, 1=medium and 2=fastest (default).
```

`-S` is opt-in and `drc.sh` does not pass it (`VIRTUAL_OSS_ARGS`, `drc.sh:96`).
So `virtual_oss` coerces its client to its own `-r`: MPD's `SNDCTL_DSP_SPEED`
asks for 44100, gets 192000 back, and **MPD** converts with soxr "very high" —
the same resampler, at the same quality, as on Linux (§2.2). The runner encodes
the same reading: it records `loopback_resampling` as the presence of `-S`.

> Correction: `SOUND-QUALITY-INVESTIGATION.md` §9, `DAC-FORMAT-ALIGNMENT.md` §5.1
> and `DAC-FORMAT-EXPLAINED.md` §10 said the opposite — that `virtual_oss`
> "silently resamples with its own low-quality resampler". That contradicted the
> man page and was never measured. They are corrected as of 2026-09-11.

What is **not** yet established is that FreeBSD behaves as documented in
practice. The residual measurement settles it without needing to observe the
mechanism: if MPD's soxr does the work, the FreeBSD number lands beside Linux's
+2.5…+4 dB. If it lands tens of dB higher, something else is converting — and
§3.2 is then the first suspect, because it is the one way the documented path
could still end up at a poor resampler.

Expectation, stated plainly: **FreeBSD will match Linux here**, and the rate
mismatch will turn out not to explain the audible difference. The test is worth
running because §3.2 and §3.3 are genuine FreeBSD-only risks the man page does
not cover.

### 3.2 Is MPD on FreeBSD built with soxr?

If the `audio/musicpd` build lacks soxr, the `resampler { plugin "soxr" }`
block cannot be honoured and MPD would use its internal fallback resampler — a
poor one. That alone would make every mismatched track worse on FreeBSD only.
Check before anything else (§4.0).

### 3.3 Does `resamp` mode's 24-bit output survive OSS?

`DRC-resamp` forces `format "192000:24:2"`. On FreeBSD that 24-bit format goes
through OSS to `virtual_oss` — the `AFMT_S24_LE` 3-byte / 4-byte ambiguity that
`DAC-FORMAT-CROSS-OS-PROCEDURE.md` §4.3 described. The FreeBSD run of 2026-09-10
declared it impossible, but **it only exercised `DRC-native`**, where MPD chose
32-bit. `resamp` mode was never tested. If MPD writes 4-byte samples to a
consumer counting 3, every sample after the first shifts — full-scale noise.
The Linux reference (§2.4) is BIT-PERFECT.

---

## 4. Procedure (FreeBSD)

The runner, `resampler-residual.py` and the null read and write files and are
OS-independent. **`--drc-output` and `--allow-resample` have only been
exercised on Linux** — watch their output.

### 4.0 Pull, and check the preconditions

```sh
cd ~/open-media-drc && git pull --ff-only
musicpd --version | grep -A1 '^Filters:'     # must list soxr — see §3.2
ls -l /dev/dsp.dac                             # which pcm unit is the DAC
ps -ax -o args | grep '[v]irtual_oss'          # note whether -S is present
```

If `soxr` is missing from `Filters:`, **stop and report that first** — it is a
finding in its own right (§5).

If the installed `omdrc` predates this pull, reinstall so `omdrc status` carries
today's `resamp` label (`drc.sh` is the source of `/usr/local/bin/omdrc`). The
test does not depend on the label, only the guard fix from `f1aaf3a`.

### 4.1 Record the state, stop everything that can drive MPD

```sh
omdrc status > /tmp/rate-before.txt; omdrc session >> /tmp/rate-before.txt
mpc playlist -f %file% > /tmp/rate-queue.txt
cp -a ~/.local/state/omdrc /tmp/rate-state      # or $OMDRC_STATE_DIR / /var/db/omdrc
sudo service omdrc_renderer stop; sudo service upmpdcli stop
ps -ax | grep -E '[u]pmpdcli|[q]obuzconnect2mpd'   # must print NOTHING
mpc stop; mpc clear
```

**The renderer check is not optional.** On 2026-09-10 `qobuzconnect2mpd` kept
refilling MPD's queue; two captures came out full of −4.7 dBFS music instead of
the −90 dBFS test signal, and the comparison correctly said "not the same
material" for a reason nobody suspected.

### 4.2 Generate the material and check it

```sh
M=$HOME/rate-material; mkdir -p "$M"
for spec in "44100 16" "44100 24" "44100 32" "192000 24" "192000 32"; do
  set -- $spec
  ./tests/gen-bitperfect-wav.py "$M/counter-$1-s$2.wav" --rate $1 --bits $2 --frames $(( $1 * 10 ))
done
./scripts/gen-tone-wav.py "$M/tone-44100-s32.wav"  --rate 44100
./scripts/gen-tone-wav.py "$M/tone-192000-s32.wav" --rate 192000
sha256 "$M"/*.wav
```

Expected (Linux):

```
counter-44100-s16.wav   3f1d309a160c0443bf03f415874a92a3c246b0b1b0868a0d5b6697b9c592ba68
counter-44100-s24.wav   b4135e398681c10185856412c1c027bdfd531e488a3e05d4327a9d5b72a45779
counter-44100-s32.wav   17f040e7d298a2bdc907a88de177cd6296ba1d4edeb1a8d78bf0eee11ebfdde9
counter-192000-s24.wav  01317af6523ec67f74834361ce47df63438f8892d5238f0b2c08239eec903320
counter-192000-s32.wav  c97f0da5fe982b716dda0177209dd7376e1fd097c9d63b9e57645bc11ec4ec0c
tone-44100-s32.wav      d0aee305cc85691e20e168feba20496f2b785f637bb8791fd25de1f743b99880
tone-192000-s32.wav     63dbfe371efc26eef08f377f56f12a9bdeac4b5258bf522d577123e7df2b0084
```

The **counters must match** (integer-only generator). The **tones may not** —
`math.sin` goes through FreeBSD's libm, which can differ from glibc's in the last
bit. A tone mismatch is harmless: the residual analysis fits frequency and
level, it never compares bytes.

Note the 24-bit counter reaches the wire at about **−42 dBFS**, not −90: the
generator puts the same counter values in a 24-bit container. Faint noise
through the speakers is expected.

### 4.3 Guard matrix

Serve the material to MPD (the runner does the same when `music_directory` is
not writable):

```sh
( cd "$M" && exec python3 -m http.server 8765 --bind 127.0.0.1 ) &
SRV=$!                          # stop it later with: kill $SRV
```

> Do **not** stop it with `pkill -f 'http.server 8765'` from a shell whose own
> command line contains that text — it matches itself and kills the shell. That
> happened on Linux.

```sh
probe() {
  mpc -q stop; mpc -q clear; mpc -q add "http://127.0.0.1:8765/$1"; mpc -q play
  sleep 3
  printf '%-24s decoder=%-12s | %s\n' "$1" "$(mpc status '%audioformat%' | head -1)" \
    "$(omdrc status | sed -n 's/^Rate: *//p')"
  mpc -q stop
}
omdrc geometry flat; omdrc 192000
for f in counter-192000-s24.wav counter-192000-s32.wav counter-44100-s16.wav counter-44100-s24.wav counter-44100-s32.wav; do probe $f; done
omdrc 44100
for f in counter-44100-s16.wav counter-44100-s24.wav counter-44100-s32.wav counter-192000-s24.wav counter-192000-s32.wav; do probe $f; done
omdrc resamp
for f in counter-44100-s24.wav counter-192000-s24.wav; do probe $f; done
```

Save the output as `bp-results/rate-guard-matrix-freebsd.txt`, in the shape of
the Linux one. Every row must print `[match]` or `MISMATCH` exactly as in §2.1.
A missing `Rate:` line means the guard is still blind — that is a finding.

Follow the usual `virtual_oss` precautions for each rate change (renderers
stopped, CD bridge released — `drc.sh` handles the latter; see
`VIRTUAL_OSS_CUSE_DEADLOCK.md`).

### 4.4 Captures

No `--card` on FreeBSD — the runner taps whatever `/dev/dsp.dac` points at.

```sh
kill $SRV    # the runner serves its own copy
R="python3 scripts/bitperfect_runner.py --route drc --source mpd --allow-drc"

omdrc geometry flat; omdrc 192000
$R --reference capture --input "$M/tone-192000-s32.wav" --out bp-results/rate-192000-freebsd-tone192k-matched
$R --reference capture --allow-resample --input "$M/tone-44100-s32.wav" --out bp-results/rate-192000-freebsd-tone44k-mismatch

omdrc 44100
$R --reference capture --allow-resample --input "$M/tone-192000-s32.wav" --out bp-results/rate-44100-freebsd-tone192k-mismatch

omdrc resamp
$R --drc-output DRC-resamp --reference source --input "$M/counter-192000-s24.wav" --out bp-results/rate-resamp-freebsd-counter192k-s24
$R --drc-output DRC-resamp --reference capture --allow-resample --input "$M/tone-44100-s32.wav" --out bp-results/rate-resamp-freebsd-tone44k
```

Each run should end `@@RESULT verdict=CAPTURED` (or `BIT-PERFECT` for the
`--reference source` one) with `0 truncated … 0 dropped`. Read the `WARNING`
line on the mismatched runs: it now says who is expected to resample, based on
whether `virtual_oss` has `-S`.

### 4.5 Analysis

```sh
for p in rate-192000-freebsd-tone192k-matched rate-192000-freebsd-tone44k-mismatch \
         rate-44100-freebsd-tone192k-mismatch rate-resamp-freebsd-tone44k; do
  python3 scripts/resampler-residual.py bp-results/$p --json bp-results/$p.residual.json
done
```

Put the numbers next to §2.3's table. Do **not** null the mismatched captures
against Linux's — see §2.5; the tool will refuse, correctly.

### 4.6 Restore

```sh
omdrc geometry "$(cat /tmp/rate-state/last_geometry)"
omdrc $(cat /tmp/rate-state/last_arg)          # e.g. "44100 @v1"
cp -a /tmp/rate-state/last_source ~/.local/state/omdrc/   # omdrc may have rewritten it
sudo service upmpdcli start; sudo service omdrc_renderer start   # whichever were running
mpc -q clear; while IFS= read -r u; do [ -n "$u" ] && mpc -q add "$u"; done < /tmp/rate-queue.txt
mpc playlist -f %file% > /tmp/rate-queue.now
diff /tmp/rate-queue.now /tmp/rate-queue.txt && echo "queue restored"
omdrc status
```

A renderer may add its own entries while you restore; if the `diff` fails,
clear and re-add. That happened on Linux.

---

## 5. How to read the result

| observation | meaning |
|---|---|
| guard rows as in §2.1 | the guard works on FreeBSD; ordinary listening can rely on it |
| guard prints no `Rate:` line | still blind on FreeBSD — the fix did not take |
| `Filters:` lacks soxr | MPD falls back to its internal resampler on every mismatch — a real FreeBSD-only degradation; fix the build before anything else |
| mismatch residual within a few dB of Linux (+2.5…+4 dB vs floor) | FreeBSD behaves as documented (§3.1): MPD's soxr resamples, as well as on Linux. **Rate mismatch is not the explanation** for the audible difference |
| mismatch residual tens of dB above the floor | a lower-quality resampler is in the path — MPD's fallback if soxr is missing (§3.2), or `virtual_oss`'s own if it is converting despite the absent `-S` (contrary to §3.1). **A measurable FreeBSD-only degradation on mixed-rate listening — the first real candidate found.** Look at the spurs: images at *source rate ± tone* point at the resampler |
| matched control not at the floor | something other than resampling alters the chain — stop and investigate before trusting the rest |
| resamp counter BIT-PERFECT | the 24-bit OSS path is sound (§3.3 closed) |
| resamp counter garbage / massive mismatch | the 3-vs-4-byte stride bug is real in `resamp` mode on FreeBSD |
| resamp counter fails only in the lowest byte | 24-bit truncation, not a stride bug: resolution lost but no shift |

If the mismatch path turns out to be degraded, the candidate fixes are: restart
`virtual_oss` whenever the track rate changes; make `DRC-native` refuse a rate
`virtual_oss` is not running at; or send mixed-rate listening through `resamp`
mode deliberately — *after* §3.3 is known to be safe.

## 6. Pitfalls carried over

* Stop the renderers, and verify with `ps` — clearing the queue is not enough.
* A capture's `rate` is the material's; the wire's is `wire_rate`
  (`chain.brutefir_rate` in older captures).
* Never byte-compare captures from two different DACs unless both declare the
  same container width.
* Never null a resampled path; compare residuals.
* A tool that finds nothing and a tool that finds agreement look the same —
  check the capture sizes, `0 truncated`, and that each `.residual.json` has
  both channels before believing a clean result.

## 7. Report back

1. `musicpd --version` `Filters:` line, `virtual_oss` command line, `/dev/dsp.dac` target, and which DAC is attached.
2. `bp-results/rate-guard-matrix-freebsd.txt`.
3. The four residual results as a table beside §2.3, and the resamp counter verdict.
4. Commit the `.json` and `.residual.json` artifacts (`.wire.raw` stays local, gitignored).
5. Update `doc/SOUND-QUALITY-INVESTIGATION.md` §9 and §13 with the verdict, and
   add the measured confirmation (or refutation) of §3.1 to §9.
