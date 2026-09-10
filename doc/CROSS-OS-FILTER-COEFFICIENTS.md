# The two machines are not convolving the same filter

> **Start here for the whole story:** [`doc/SOUND-QUALITY-INVESTIGATION.md`](SOUND-QUALITY-INVESTIGATION.md) is the complete record — what was measured, what it ruled out, and what is still open.

Found 2026-09-10 while pre-flighting the cross-OS null test
(`doc/BIT-PERFECT-VERIFICATION.md`, "Cross-OS comparison through a REAL
filter"). It blocks that test, and it is worth understanding before anyone
tries to read a null result as an operating-system difference.

---

## 1. The finding

`120.blue`, variant `@multi.pt`, 192 kHz — the filter the appliance actually
runs — has **different coefficient files on each operating system**:

```
                                        L.raw              R.raw
FreeBSD  /usr/local/etc/…/@multi.pt/    0b3a9c24…          a69dd603…
Arch     /usr/local/etc/…/@multi.pt/    28a9c5ba…          d0cc9b57…
```

Everything around them is identical:

* the BruteFIR config that names them — `brutefir-192000@multi.pt.conf`,
  sha256 `c2ae7dec…` on both hosts, byte for byte;
* the attenuation baked into it, 1.5 dB on both;
* the file sizes, 4 194 304 bytes = 524 288 `FLOAT64_LE` taps;
* and, decisively, **the source impulse responses the coefficients are made
  from**. Both design bundles record the same
  `FLX-trimmed-48k.wav` / `FRX-trimmed-48k.wav`, sha256 `bad43a63…` and
  `e52569b6…`. Same measurement, same design, same input.

The same divergence exists at every rate and in the `@legacy` variant too. It
is not confined to one deployment.

## 2. Why

`new_filter_design.py` → `scripts/deploy_filter.py` `generate_runtime()` does
not ship the coefficients that the design produced. It **regenerates** them on
whichever machine performs the install, by resampling the 48 kHz source impulse
with SoX (`scripts/REW2raw.sh:264`):

```sh
sox "$IN" -b 64 -e floating-point "$INTERMEDIATE" rate -v -L -s "$SR"
```

and the two machines do not have the same SoX:

| host | binary | resampler |
|---|---|---|
| FreeBSD 15.1 | `sox 14.4.2.20210509_7` | upstream SoX `rate` |
| Arch | `sox → sox_ng 14.8.0.1` | the `sox_ng` fork's `rate` |

Two different implementations of the same very-high-quality polyphase
resampler, so two different sets of coefficients. The design manifest at
`filters/120.blue/provenance/multi.pt.json` records the sha256 of each
generated file — and the **FreeBSD** files match it, because the design was run
there. The Arch install regenerated its own and wrote its own manifest with a
different `bundle_id`.

> This is a provenance bug in its own right: `verify_filter_bundle.py` checks
> deployed coefficients against the manifest, and the Arch deployment can only
> ever pass against a manifest it wrote itself.

## 3. Why it blocks the null test

`scripts/bitperfect-null.py` `compare_provenance()` treats a coefficient
sha256 mismatch as **blocking**, not as a note:

```
STOP:  filter coefficients differ: [...] vs [...] — the two machines
       convolved different filters
```

It exits 2 ("cannot judge") before comparing a single byte of audio. That is
the right behaviour — a null between two different filters is meaningless, and
a meaningless null gets read as an operating-system difference — but it does
mean the cross-OS experiment cannot start until this is resolved.

**Fix before running the null:** copy one machine's coefficients to the other
rather than deploying the bundle twice.

```sh
# from FreeBSD, with the Arch root mounted (see §6)
R=/media/KINGSTON_SM2280S3G2120G_50026B726706A3BC_s1
D=usr/local/etc/open-media-drc/filters/120.blue/192000/@multi.pt
sudo cp /$D/L.raw /$D/R.raw "$R/$D/"
```

Prefer the FreeBSD set, because it is the one the design manifest attests.

## 4. Is it audible? No.

Both files are 4× upsamples of the same 48 kHz impulse, and the DFT bin spacing
works out identical (`192000/524288 == 48000/131072 == 0.3662109375 Hz`), so
each 192 kHz filter can be compared against its own source bin-for-bin with no
interpolation and no windowing error. Measured:

**Reproduction of the source response, 20 Hz – 23.8 kHz** (ideal:
`H_192k(f) == H_48k(f)`):

| band | SoX 14.4.2 | sox_ng 14.8.0.1 |
|---|---|---|
| 20 Hz – 1 kHz | 0.00016 dB, 0.0012° | 0.00019 dB, 0.0015° |
| 1 – 10 kHz | 0.00012 dB, 0.0008° | 0.00013 dB, 0.0009° |
| 10 – 20 kHz | 0.00014 dB, 0.0009° | 0.00015 dB, 0.0010° |
| 20 – 22.05 kHz | 0.00016 dB, 0.0011° | 0.00016 dB, 0.0010° |

(worst-case magnitude and phase error in each band, both channels)

Both are flat to **±0.0002 dB and ±0.002°** across the whole audible band and
well beyond it, then brickwall in the last ~200 Hz below 24 kHz — which is the
anti-imaging filter doing its job, and both do it in the same place.

**Direct difference between the two coefficient sets:** peak tap difference
1.34e-6 against a filter peak of 0.183, i.e. **−103 dB**; RMS −106 dB; 73 802
of 524 288 taps differ at all. Below 20 kHz the two transfer functions agree to
**±0.00002 dB**. Every visible divergence is above 20 kHz, where a filter
derived from a 48 kHz source carries nothing but resampler stop-band residue.

## 5. "Could sox_ng have produced better filters under Linux?"

Measurably yes, in one respect; audibly no, in any respect.

**Where sox_ng is better — image rejection above 24 kHz** (ideal: zero energy;
dB relative to the passband peak):

| | SoX 14.4.2 | sox_ng 14.8.0.1 | difference |
|---|---|---|---|
| peak | −93.3 dBr | −98.2 dBr | sox_ng better by **4.86 dB** |
| rms | −103.2 dBr | −103.5 dBr | sox_ng better by 0.29 dB |

**Where SoX 14.4.2 is (very slightly) better — the audible band.** In every
band up to 22 kHz the upstream SoX result is marginally closer to the source
response: 0.000115 dB worst-case error against sox_ng's 0.000133 dB. The
difference between them is **0.00002 dB**. It is noise in the measurement of
noise.

So the honest reading is:

* sox_ng's advantage is real, reproducible on both channels, and confined
  entirely to **ultrasonic image rejection** — a ~5 dB improvement at a peak
  that is already 93 dB below the passband and sits **above 24 kHz**, where
  there is no programme material, the DAC's own reconstruction filter is
  already removing it, and no listener has hearing.
* In the band that reaches the ear, the two are indistinguishable, and if you
  insist on a winner it is the FreeBSD one.

**Neither resampler is responsible for FreeBSD sounding worse.** If anything,
the measurement points the wrong way for that hypothesis: FreeBSD is running
the filter that is fractionally more faithful in the audible band.

The reproduction script is `scripts/compare-filter-resamplers.py`.

## 6. Inspecting the other OS without rebooting

`bee` dual-boots, and while FreeBSD is running both Arch partitions are already
mounted read-write:

```
/media/KINGSTON_SM2280S3G2120G_50026B726706A3BC_s1   Arch /
/media/KINGSTON_SM2280S3G2120G_50026B726706A3BC_s2   Arch /home
```

(`/etc/fstab` also lists them `ro,noauto` as `/arki/root` and `/arki/home`;
those mounts then fail with "Device busy" because the automounter got there
first. Use the `/media` paths.) `$s2/giacomo` needs `sudo` to read, and `git`
refuses the checkout there for "dubious ownership".

They are mounted **rw** and the Linux system is not running. Treat them as
read-only; a stray write is a real risk.

## 7. What should change

1. **Ship the coefficients, do not regenerate them.** The design bundle already
   records their sha256; the deploy should install those exact bytes on every
   host. Regenerating makes the deployed filter a function of the host's SoX
   build, which is neither recorded nor controlled.
2. Until then, treat "same geometry, same variant, same rate" as **not**
   implying "same filter" across hosts, and copy the files when it matters.
3. `verify_filter_bundle.py` should be run against the *design* manifest, not a
   locally regenerated one, or it cannot catch this.

---

## 7. Resolved, and the null has now run — IDENTICAL

**2026-09-10, Arch side.** Rather than overwrite the deployed `@multi.pt`
coefficients, FreeBSD's were staged as a second variant and the chain pointed at
that, leaving the Arch deployment untouched:

* FreeBSD is dual-boot on this same disk, so its filesystem was mounted
  read-only (`mount -t ufs -o ro,ufstype=ufs2 /dev/sda5`) — no network copy, and
  no chance of writing to it. `L.raw`/`R.raw` came straight from
  `/usr/local/etc/.../@multi.pt/`, sha256 `0b3a9c24…` / `a69dd603…`, matching
  the hashes the FreeBSD capture recorded in its own provenance.
* Staged as `@fbsdcoef` — coefficients under `filters/120.blue/192000/@fbsdcoef/`
  and a `brutefir-192000@fbsdcoef.conf` identical to the `@multi.pt` config
  except for the two `filename:` lines. Attenuation stayed 1.5 dB.
* The input material was regenerated from `tests/gen-bitperfect-wav.py` and came
  out at sha256 `01317af6…`, byte-identical to the FreeBSD run's input. The
  generator's determinism claim holds across operating systems.
* Partitioning was `32768,16` on both sides, so the FreeBSD `part32768` capture
  is the apples-to-apples reference.

### Result

```
bp-results/null-192000-fbsd-part32768   freebsd15/15.1-RELEASE-p2   9031912 frames
bp-results/null-192000-arch-fbsdcoef    linux/7.2.3-arch1-3         3921411 frames

aligned at lag -5506220 frames (r=1.000000); compared 3525692 frames (18.363 s)
differing samples : 0 (0.000e+00 of all)
max difference    : 0 LSB of S32
null depth        : -inf dBFS

IDENTICAL: the two chains produced the same bytes
```

Artifact: `bp-results/null-192000-xos-fbsdcoef.json`.

**Reading.** Given the same input samples and the same coefficients, FreeBSD and
Linux put *bit-identical* bytes on the USB wire — through two different
loopbacks (`virtual_oss` vs `snd-aloop`), two different kernels, two different
audio stacks. Not "within rounding": zero differing samples out of 7 051 384,
peak level −46.4 dBFS on both.

Every digital explanation for an audible difference between the two systems is
now excluded, on this material at this rate. What remains is downstream of the
wire (the DAC's own clocking and analogue behaviour, host USB electrical noise,
grounding) or not in the equipment (level matching, expectation). The one
digital caveat still open is the rate-mismatch guard described in
`DAC-FORMAT-CROSS-OS-PROCEDURE.md` §7: it was inoperative on FreeBSD until
2026-09-10, and it governs *mixed-rate ordinary listening*, not the fixed-rate
captures nulled here.

### Three obstacles worth recording

They cost more than the test did, and each one produced a wrong answer that
looked like a result.

1. **The runner tapped the wrong DAC.** `discover_linux()` returned the
   lowest-numbered USB audio card — the ESI U24XL — not the OKTO the chain
   feeds. A capture of the wrong endpoint is indistinguishable from a capture of
   the right one. It now refuses to guess when several USB audio cards are
   present and takes `--card N`.
2. **`qobuzconnect2mpd` kept repopulating MPD's queue.** Two captures were
   dominated by full-level Qobuz audio at −4.7 dBFS peak while the test signal
   sits near −90 dBFS by design, so cross-correlation locked onto the music and
   reported `r=0.072` — "not the same material". Correct verdict, misleading
   cause. The renderer has to be stopped, not just the queue cleared.
3. **The null blocked on the variant *label*.** `variant` is in `MUST_MATCH`,
   but it is only a proxy for "a different correction curve", and the direct
   evidence — coefficient sha256 equality — is checked separately a few lines
   later. When those hashes agree the curve is provably the same and the label
   is just the directory the identical taps were read from, which is exactly the
   shape of this run. It is now a note in that case, still blocking otherwise.

### Reproducing

The staged variant was removed afterwards, leaving `@multi.pt` byte-identical to
its pre-test backup. To redo it: mount the FreeBSD partition read-only, copy
`@multi.pt/{L,R}.raw` to a `@fbsdcoef` variant, `sed` the two `filename:` lines
in a copy of the config, `omdrc 192000 @fbsdcoef`, stop every renderer, clear
MPD's queue, then run the capture with `--card 2` and null it against
`bp-results/null-192000-fbsd-part32768`.

### Still worth fixing

The provenance bug in §2 is untouched by this: `deploy_filter.py` still makes
the deployed filter a function of the installing host's SoX build, and
`verify_filter_bundle.py` on Arch can still only pass against a manifest Arch
wrote itself. The null above had to work around that rather than benefit from a
fix.
