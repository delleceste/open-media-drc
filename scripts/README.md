# scripts/ — helper tools

Utility scripts for filter generation, headroom calculation, chain
verification, and service installation. Each is documented in depth in the
main [README](../README.md) or in `doc/`; this file is the quick index.

| Script | Purpose | Platform |
|---|---|---|
| `REW2raw.sh` | Convert one REW-exported WAV impulse response to a brutefir-ready raw `FLOAT64_LE` file, resampling to a target rate (default 192 kHz) with the theoretically correct FIR coefficient scale (`Fs_source / Fs_target`, no peak normalisation). | Linux + FreeBSD (needs `sox`) |
| `REW2raw-all-rates.sh` | Batch wrapper around `REW2raw.sh`: generates the `L.raw` / `R.raw` pair (plus a `sox.txt` conversion log) for **every** numeric sample-rate directory under a filter root, e.g. `filters/120.blue/{44100,48000,88200,96000,192000}/`. Asks before overwriting unless `-y`. | Linux + FreeBSD |
| `headroom_calc.py` | Computes the minimum `attenuation:` value for each brutefir `.conf` from the filters' worst-case FFT gain (+ safety margin, default 1 dB), so playback never clips while dynamics are maximised. Run it after every filter (re)generation. | Linux + FreeBSD (python3) |
| `verify-bitperfect.sh` | End-to-end bit-perfectness proof: feeds a deterministic S32_LE signal through a chosen source (built-in OSS writer, or MPD by output name) and compares it byte-for-byte against a chosen tap (the OKTO's isochronous USB OUT endpoint via `usbdump`, or an OSS loopback node such as `/dev/dsp.loop`). See [`doc/BIT-PERFECT-VERIFICATION.md`](../doc/BIT-PERFECT-VERIFICATION.md). | FreeBSD (USB tap needs root) |
| `bitperfect-tap-linux.sh` | Plays a WAV (16/24/32-bit, any supported rate) to the USB DAC and records the exact bytes sent on the USB wire (usbmon tap of isochronous OUT endpoint 0x01) into `PREFIX.wav` / `PREFIX.wire.raw` / `PREFIX.txt`, with a local bit-perfect verdict. Same CLI and artifacts as the FreeBSD twin, for cross-OS comparison. | Linux (tap needs root) |
| `bitperfect-tap-freebsd.sh` | FreeBSD twin of the above (`usbdump` tap, format-guarded OSS writer on `/dev/dsp0`). | FreeBSD (tap needs root) |
| `bitperfect-compare.py` | Opens two tap artifacts (from either OS; `.wav`, `.wire.raw`, or the tiny committable `.txt` report — hash-proxy comparison, so the 10 MB streams never need to travel through git) and verdicts **MATCH: byte-by-byte identical** or **MISMATCH** with the first differing offset (when payloads are present). | Linux + FreeBSD (python3) |
| `bitperfect-lib.py` | Shared engine for the two tap scripts (WAV→S32 wire-container promotion, usbmon reader, usbdump decoder, alignment/verdict/report) — not called directly. | Linux + FreeBSD |
| `systemd-user-install.sh` | Legacy convenience: symlinks `drc.service` into `~/.config/systemd/user/`, reloads the user daemon and enables the service. Superseded by the system-level hotplug units installed via `install.sh` (see the main README, *USB DAC hotplug automation*), kept for user-session setups. | Linux only (systemd) |

## Typical workflows

**New/updated filters** (after a REW measurement session):

```sh
scripts/REW2raw-all-rates.sh \
  -L filters/120.blue/rew/FLX-trimmed-48k.wav \
  -R filters/120.blue/rew/FRX-trimmed-48k.wav \
  -o filters/120.blue
python3 scripts/headroom_calc.py     # then set attenuation: in the .conf files
```

**Prove the direct path is untouched** (FreeBSD, DAC free):

```sh
./drc.sh off
sudo ./scripts/verify-bitperfect.sh 44100
```

**Prove Linux and FreeBSD send the DAC the very same bytes** (the cross-OS
USB-tap suite; full background in
[`doc/BIT-PERFECT-VERIFICATION.md`](../doc/BIT-PERFECT-VERIFICATION.md)):

```sh
# 0. On each machine, materialize the common input (10.6 MB, gitignored,
#    byte-deterministic — the printed sha256 must match tests/README.md):
python3 tests/gen-bitperfect-wav.py tests/bitperfect-test-44100-s32-stereo-30s.wav

# 1. Linux box: play + tap the USB wire (sudo asked internally for usbmon)
./scripts/bitperfect-tap-linux.sh tests/bitperfect-test-44100-s32-stereo-30s.wav
git add bp-results/bitperfect-test-44100-s32-stereo-30s-linux.txt
git commit -m 'bp: linux tap report' && git push

# 2. FreeBSD box: free the DAC, play + tap (usbdump), commit its report
#    (needs dev.pcm.N.bitperfect=1 and dev.pcm.N.play.vchans=0; the writer
#     aborts with FAIL: rather than play converted audio if they are unset)
./drc.sh off        # and stop any renderer holding /dev/dsp0
./scripts/bitperfect-tap-freebsd.sh tests/bitperfect-test-44100-s32-stereo-30s.wav
git add bp-results/bitperfect-test-44100-s32-stereo-30s-freebsd.txt
git commit -m 'bp: freebsd tap report' && git push

# 3. Either box, after pulling both reports: compare
git pull
./scripts/bitperfect-compare.py \
    bp-results/bitperfect-test-44100-s32-stereo-30s-linux.txt \
    bp-results/bitperfect-test-44100-s32-stereo-30s-freebsd.txt
# -> MATCH: payload sha256 identical over 10584000 bytes — byte-by-byte
#    identity proven by hash          (exit 0; MISMATCH -> exit 1)
```

**Other rates / widths.** Both tap scripts read rate and sample width from
the WAV header, so testing 192k/24-bit is just a different input file — no
flags to change:

```sh
python3 tests/gen-bitperfect-wav.py --rate 192000 --bits 24 --frames 5760000 \
    tests/bitperfect-test-192000-s24-stereo-30s.wav      # --frames = s * rate
./scripts/bitperfect-tap-linux.sh tests/bitperfect-test-192000-s24-stereo-30s.wav
```

Use the **same width on both machines** for a cross-OS comparison: 16/24-bit
input is promoted losslessly to the 32-bit wire container (`<<16` / `<<8`),
so captures of different widths carry differently shifted values and will
not byte-match. Details in [`../tests/README.md`](../tests/README.md).

Only the ~600-byte `.txt` reports travel through git: they record each tap
payload's exact length and sha256, and hash equality on equal-length
payloads proves byte-identity. The 10 MB `.wav` / `.wire.raw` artifacts
stay on the machine that produced them (`bp-results/*` is gitignored,
`!bp-results/*.txt` re-included); they are only needed on one machine if a
MISMATCH ever has to be localized to its first differing offset — then
move them with scp or a temporary `git add -f`, and run the comparator on
the two `.wav` files instead of the reports. Any 16/24/32-bit PCM WAV can
replace the canonical input (e.g. 192k/24 material) — both tap scripts
promote it identically and losslessly to the S32_LE wire container.

See also `../glitch-debug.sh` (repo root) and
[`doc/GLITCH-DETECTION.md`](../doc/GLITCH-DETECTION.md) for the runtime
glitch-detection subsystem.
