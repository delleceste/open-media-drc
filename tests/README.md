# Bit-perfect test assets

## Cross-OS USB-tap asset (30 s, generated, not committed)

`bitperfect-test-44100-s32-stereo-30s.wav` — the **common input** for the
cross-OS USB wire tap suite (`scripts/bitperfect-tap-linux.sh`,
`scripts/bitperfect-tap-freebsd.sh`, `scripts/bitperfect-compare.py`).
It is 10.6 MB so it is gitignored; regenerate it **byte-identically on any
OS** with:

```sh
python3 tests/gen-bitperfect-wav.py tests/bitperfect-test-44100-s32-stereo-30s.wav
```

and verify the printed hash matches:

```
sha256 88d365eeaccb1fa830bb1a2726b0f29bb545885824351080e0c5b4cbc9602348
```

Format: WAV, 32-bit PCM (S32_LE), 2 ch, 44100 Hz, 1,323,000 frames (30 s).
Unlike the original asset below, **every (L,R) pair is unique over the whole
file** (`L = i & 0xFFFF`, `R = (i*40503 + (i >> 16)) & 0xFFFF` — the block
index folded into R breaks the 65536-frame period), so capture alignment is
unambiguous at any length and any dropped/duplicated/altered sample is
detectable at any offset, while the signal stays near-silent (~ −90 dBFS).

### Cross-OS workflow

Each OS runs its tap script on the (locally regenerated) common WAV; only
the tiny `bp-results/*.txt` reports are committed — they carry the tap
payload's length and sha256, which proves byte-identity without moving the
10 MB streams through git (`bp-results/*` is otherwise ignored):

```sh
# Linux box                                  # FreeBSD box (DAC freed: ./drc.sh off)
./scripts/bitperfect-tap-linux.sh \          ./scripts/bitperfect-tap-freebsd.sh \
    tests/bitperfect-test-44100-s32-stereo-30s.wav   # (same argument)
git add bp-results/*-linux.txt               git add bp-results/*-freebsd.txt
git commit && git push                       git commit && git push

# then on either box:
git pull
./scripts/bitperfect-compare.py \
    bp-results/bitperfect-test-44100-s32-stereo-30s-linux.txt \
    bp-results/bitperfect-test-44100-s32-stereo-30s-freebsd.txt
```

Step-by-step commands and the mismatch-forensics path are in
`../scripts/README.md` (*Prove Linux and FreeBSD send the DAC the very
same bytes*) and `../doc/BIT-PERFECT-VERIFICATION.md` (*Cross-OS
comparison*).

## Original short asset (committed)

`bitperfect-test-44100-s32-stereo.wav` — the deterministic signal used to verify
the playback chain (see `../doc/BIT-PERFECT-VERIFICATION.md`).

- Format: **WAV, 32-bit PCM (S32_LE), 2 ch, 44100 Hz, 100000 frames (~2.27 s)**.
- Content: a per-sample counter in the **low 16 bits** — L = `i & 0xFFFF`,
  R = `(i*40503) & 0xFFFF`. This is **near-silent (~−90 dBFS)** yet every sample
  is uniquely determined, so any truncation / dither / volume / resampling shows
  up immediately, and the distinct L/R streams catch a channel swap. The WAV's
  PCM payload is byte-identical to `bitperfect-test-44100-s32-stereo.raw`
  (the `.raw` is just the WAV minus its 44-byte header — the reference for
  byte comparison).

Why a WAV: MPD cannot play headerless raw, so the identical PCM is wrapped in a
header-only WAV. What MPD decodes and outputs equals the `.raw` byte-for-byte.

Regenerate:

```sh
python3 - tests/bitperfect-test-44100-s32-stereo.wav tests/bitperfect-test-44100-s32-stereo.raw 100000 44100 <<'PY'
import sys, struct, wave
wav, raw, n, rate = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
buf = bytearray()
for i in range(n): buf += struct.pack("<ii", i & 0xFFFF, (i*40503) & 0xFFFF)
open(raw, "wb").write(buf)
w = wave.open(wav, "wb"); w.setnchannels(2); w.setsampwidth(4); w.setframerate(rate)
w.writeframes(buf); w.close()
PY
```

Feed it to MPD without the music dir mounted, by adding a local socket and using
a `file://` URL (MPD forbids `file://` over TCP):

```sh
# add a local socket to musicpd.conf, restart, then:
export MPD_HOST=/tmp/mpd.sock
cp tests/bitperfect-test-44100-s32-stereo.wav /tmp/bp.wav && chmod 0644 /tmp/bp.wav
mpc enable only OKTO-DAC
mpc clear && mpc add "file:///tmp/bp.wav" && mpc play
```
