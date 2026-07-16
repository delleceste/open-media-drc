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

See also `../glitch-debug.sh` (repo root) and
[`doc/GLITCH-DETECTION.md`](../doc/GLITCH-DETECTION.md) for the runtime
glitch-detection subsystem.
