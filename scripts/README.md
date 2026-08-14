# scripts/ — helper tools

Utility scripts for filter generation, headroom calculation, chain
verification, and service installation. Each is documented in depth in the
main [README](../README.md) or in `doc/`; this file is the quick index.

| Script | Purpose | Platform |
|---|---|---|
| `REW2raw.sh` | Convert one REW-exported WAV impulse response to a brutefir-ready raw `FLOAT64_LE` file, resampling to a target rate (default 192 kHz) with the theoretically correct FIR coefficient scale (`Fs_source / Fs_target`, no peak normalisation). | Linux + FreeBSD (needs `sox`) |
| `REW2raw-all-rates.sh` | Batch wrapper around `REW2raw.sh`: generates the `L.raw` / `R.raw` pair (plus a `sox.txt` conversion log) for **every** numeric sample-rate directory under a filter root, e.g. `filters/120.blue/{44100,48000,88200,96000,192000}/`. Asks before overwriting unless `-y`. | Linux + FreeBSD |
| `headroom_calc.py` | Computes the minimum `attenuation:` value for each brutefir `.conf` from the filters' worst-case FFT gain (+ safety margin, default 1 dB), so playback never clips while dynamics are maximised. Run it after every filter (re)generation. | Linux + FreeBSD (python3) |
| `declare_filter_design.py` | Creates the source-repository declaration that assigns exact measurement/filter files to semantic roles and records their SHA-256 values and TXT headers. It never starts REW. Commit it with the inputs, then create an annotated tag. | Linux + FreeBSD (python3, NumPy, Git) |
| `filter_design_suggest.py` | Read-only helper behind `declare_filter_design.py --suggest-from-source-root`: uses the newest `.mdat` filename to locate `<stem>.txts`, ranks compatible L/R exports, finds supporting aggregate/filter artifacts, and prints a complete candidate command without opening REW or the project. | Linux + FreeBSD (python3, NumPy) |
| `new_filter_design.py` | Consumes a committed source declaration at an annotated tag, verifies its tag object, commit and every input hash, then drives a complete dry-run or publication for a new immutable A/B design. Its final `NEXT` handoff covers publication, verification, commit, install, selection and UI identity checks. It never starts REW. | Linux + FreeBSD (python3, NumPy, SoX, Git) |
| `deploy_filter.py` | Lower-level offline builder: regenerates all declared rates in staging, validates TXT↔WAV response, optional corrected exports, config mapping and headroom, then publishes source copies, graph data and a hash-bound manifest. Dry-run unless `--write` is supplied. | Linux + FreeBSD (python3, NumPy, SoX, Git) |
| `verify_filter_bundle.py` | Read-only verification of bundle ID, source copies, graph dependencies, configs, exact runtime RAW hashes and headroom. A successful direct run repeats the install/select/UI handoff; use `--no-next` for CMake, CI or scripts. | Linux + FreeBSD (python3, NumPy) |
| `verify-bitperfect.sh` | End-to-end bit-perfectness proof: feeds a deterministic S32_LE signal through a chosen source (built-in OSS writer, or MPD by output name) and compares it byte-for-byte against a chosen tap (the OKTO's isochronous USB OUT endpoint via `usbdump`, or an OSS loopback node such as `/dev/dsp.loop`). See [`doc/BIT-PERFECT-VERIFICATION.md`](../doc/BIT-PERFECT-VERIFICATION.md). | FreeBSD (USB tap needs root) |
| `bitperfect-tap-linux.sh` | Plays a WAV (16/24/32-bit, any supported rate) to the USB DAC and records the exact bytes sent on the USB wire (usbmon tap of isochronous OUT endpoint 0x01) into `PREFIX.wav` / `PREFIX.wire.raw` / `PREFIX.txt`, with a local bit-perfect verdict. Same CLI and artifacts as the FreeBSD twin, for cross-OS comparison. | Linux (tap needs root) |
| `bitperfect-tap-freebsd.sh` | FreeBSD twin of the above (`usbdump` tap, format-guarded OSS writer on `/dev/dsp0`). | FreeBSD (tap needs root) |
| `bitperfect-compare.py` | Opens two tap artifacts (from either OS; `.wav`, `.wire.raw`, or the tiny committable `.txt` report — hash-proxy comparison, so the 10 MB streams never need to travel through git) and verdicts **MATCH: byte-by-byte identical** or **MISMATCH** with the first differing offset (when payloads are present). | Linux + FreeBSD (python3) |
| `bitperfect-lib.py` | Shared engine for the two tap scripts (WAV→S32 wire-container promotion, usbmon reader, usbdump decoder, alignment/verdict/report) — not called directly. | Linux + FreeBSD |
| `systemd-user-install.sh` | Legacy convenience: symlinks `drc.service` into `~/.config/systemd/user/`, reloads the user daemon and enables the service. Superseded by the system-level hotplug units installed via `install.sh` (see the main README, *USB DAC hotplug automation*), kept for user-session setups. | Linux only (systemd) |

## Typical workflows

**Suggest the declaration command from a source checkout** (read-only):

```sh
python3 scripts/declare_filter_design.py \
  --suggest-from-source-root ../DRC/DRC-120.blue
```

The newest root-level `.mdat` is selected by filesystem modification time only
and is never opened. Its stem must name a sibling `<stem>.txts` directory. The
tool selects the best compatible acoustic-timing L/R pair there, reports the
alternatives, searches sibling `*.txts` directories and root WAVs for
aggregate/filter/corrected candidates, and prints a complete dry-run command.
This is a suggestion, not an attestation: review every role, then run the printed
command without `--write` so the full DSP and provenance checks can accept or
reject it.

**Declare a new design in its source repository** (one-time semantic decision):

```sh
python3 scripts/declare_filter_design.py \
  --source-root ../DRC/DRC-120.blue \
  --geometry 120.blue --design-id rscreen-fdw8-20260813 \
  --description "120.blue Rscreen, 8-cycle FDW correction" \
  --measurement-left "new.filters.txts/L 120.Rscreen.orig.txt" \
  --measurement-right "new.filters.txts/R 120.Rscreen.orig.txt" \
  --measurement-sum new.filters.txts/LR.orig.txt \
  --filter-left-txt new.filters.txts/FLX.txt \
  --filter-right-txt new.filters.txts/FRX.txt \
  --filter-left-wav FLX-trimmed-48k.wav \
  --filter-right-wav FRX-trimmed-48k.wav \
  --corrected-left-txt new.filters.txts/L.Filtered.txt \
  --corrected-right-txt new.filters.txts/R.Filtered.txt \
  --corrected-sum-txt new.filters.txts/LR.Filtered.txt \
  --sum-mode vector_average
# Review the JSON and PASS metrics, then repeat the same command with --write.
git -C ../DRC/DRC-120.blue add -- \
  omdrc-designs/120.blue/rscreen-fdw8-20260813/design.json \
  "new.filters.txts/L 120.Rscreen.orig.txt" \
  "new.filters.txts/R 120.Rscreen.orig.txt" \
  new.filters.txts/LR.orig.txt \
  new.filters.txts/FLX.txt new.filters.txts/FRX.txt \
  FLX-trimmed-48k.wav FRX-trimmed-48k.wav \
  new.filters.txts/L.Filtered.txt new.filters.txts/R.Filtered.txt \
  new.filters.txts/LR.Filtered.txt
git -C ../DRC/DRC-120.blue commit -m "Declare 120.blue Rscreen FDW8 filter"
git -C ../DRC/DRC-120.blue tag -a 120.blue-rscreen-fdw8-20260813 \
  -m "120.blue Rscreen FDW8 correction"
```

The role assignment is the designer's attestation. Hashes prove that those
chosen files cannot later be substituted; they cannot infer the intended role
from a filename. The `.mdat` is not used by this procedure. An optional
`--project 120.blue.Rscreen.mdat` would only add its path and hash as archival
evidence; omit it, as above, when the source declaration and annotated tag are
the intended trust anchor.
The command accepts REW's float WAVs, detects the common 8192-sample causal
delay and the fixed -3.0003 dB TXT-to-WAV export gain, then checks the residual
amplitude and phase errors. It derives, rather than accepts, the declaration
destination:

```text
omdrc-designs/<geometry>/<design-id>/design.json
```

Run once without `--write` to review every selected header, hash and detected
relationship. A dirty source checkout is allowed at declaration time so new
exports and the declaration can be committed together; deployment still
rejects any selected file that is untracked, dirty, absent from the annotated
tag, or different from its declared hash.

On an interactive terminal, `declare_filter_design.py` presents the audit as
eight coloured progress stages (argument roles, Git context, hashing/parsing,
measurement consistency, TXT/WAV alignment, provenance assembly, prediction,
and write/preview). Colour is automatically disabled for pipes/log files and
can also be disabled with the standard `NO_COLOR=1` environment variable. Its
final **NEXT** section prints underlined `Run from:` directories and complete
copy/paste commands for all remaining phases: write, source commit plus
annotated tag, `new_filter_design.py` dry-run/publication into
`open-media-drc`, bundle verification and commit, CMake installation, web
service restart, and selection of the new `@design-id`.

The declaration preflight does **not** resample or write runtime filters; it
prints that boundary explicitly. During the subsequent
`new_filter_design.py` audit, `deploy_filter.py` runs `REW2raw.sh`/SoX for each
left/right target-rate conversion. Those lines are magenta. For every rate it
prints the exact FIR coefficient scale (`source_rate / target_rate`) and signed
SoX gain in dB. It then prints the worst L/R FFT peak and safety-margin
arithmetic in yellow, the required attenuation rounded upward to 0.1 dB, and
the BruteFIR config bake and read-back verification in blue. A dry run bakes
configs only in private staging; `--write` emits a separate blue
`CONFIG PUBLISHED` line when each verified template is copied into
`configs/<geometry>/`.

At the end of a successful `new_filter_design.py` dry run, **NEXT** gives a
ready-to-copy command using an absolute source checkout and `--write`. After a
successful publication it prints the standalone bundle verification, a Git
status/add/commit limited to that geometry, CMake configuration that preserves
the current geometry set, build/install and service restart, and the installed
`omdrc` geometry/design selectors. The final lines state the annotated tag,
source commit and bundle ID that the green **Filter response** identity must
show. `verify_filter_bundle.py` repeats the post-publication portion when run
directly. CMake invokes it with `--no-next` so configuration output stays
compact.

**Build and publish from the annotated tag** (run in `open-media-drc`):

```sh
python3 scripts/new_filter_design.py \
  --source-root ../DRC/DRC-120.blue \
  --source-ref 120.blue-rscreen-fdw8-20260813 \
  --declaration omdrc-designs/120.blue/rscreen-fdw8-20260813/design.json
# Review the dry run, then publish atomically:
python3 scripts/new_filter_design.py \
  --source-root ../DRC/DRC-120.blue \
  --source-ref 120.blue-rscreen-fdw8-20260813 \
  --declaration omdrc-designs/120.blue/rscreen-fdw8-20260813/design.json --write
python3 scripts/verify_filter_bundle.py --all --require-sources
./drc.sh design --list
./drc.sh design @rscreen-fdw8-20260813
```

An annotated tag is required by default. `--allow-commit-ref` exists for an
explicit lower-assurance exception. Neither command launches REW.

The lower-level `REW2raw-all-rates.sh` remains useful for experiments, but it
does not create the provenance and graph bundle required for a verified UI.

### Keeping room data out of the engine repository

A room's measurements and impulse responses are personal to one listening room
and useless to anyone else, so they do not have to live in the engine checkout.
`configs/<geometry>` and `filters/<geometry>` are read and written through a
*site root*, resolved in this order:

1. `--site-root DIR`, accepted by `new_filter_design.py`, `deploy_filter.py`
   and `verify_filter_bundle.py`;
2. the `OMDRC_SITE_ROOT` environment variable;
3. this checkout — the single-repository layout, and still the default.

The second checkout mirrors the layout exactly, so only the root changes:

```
omdrc-site/
├── configs/120.blue/…
└── filters/120.blue/…
```

Designing on one machine and playing back on another then works through that
repository: publish and commit on the design box, pull on the playback box.

```sh
# design box
export OMDRC_SITE_ROOT=~/devel/omdrc-site
python3 scripts/new_filter_design.py … --write     # writes into the site repo
git -C ~/devel/omdrc-site add -A && git -C ~/devel/omdrc-site commit -m 'Deploy …'
git -C ~/devel/omdrc-site push

# playback box
git -C ~/devel/omdrc-site pull
mkdir -p build
cd build
cmake .. -C ../host.cmake
make
sudo make install
```

Each command's `NEXT` handoff detects the split and names the working directory
per step, because the commit belongs to the site repository and the build to the
engine one.

CMake has the matching seam: `OMDRC_SITE_DATA_DIRS` is a semicolon-separated
search path (first match wins) for `configs/<geo>` and `filters/<geo>`, so the
engine repo can keep shipping the generic `flat` set while a private repo
supplies the room sets. Set it in `host.cmake`:

```cmake
set(OMDRC_SITE_DATA_DIRS "${CMAKE_SOURCE_DIR};$ENV{HOME}/devel/omdrc-site"
    CACHE STRING "Search path for configs/<geo> + filters/<geo>")
```

A geometry that no search directory defines is skipped with a warning rather
than failing the configure, so one missing set never blocks the others.

Three variables are involved, and they are not the same thing:

| Variable | Used by | Means |
|---|---|---|
| `OMDRC_SITE_DATA_DIRS` | CMake | search path for the *sources* of `configs/<geo>` + `filters/<geo>` |
| `OMDRC_SITE_ROOT` | the design scripts | the one checkout they read and write room data in |
| `OMDRC_SITE_DIR` | `drc.sh` at runtime | the *installed* `etc/open-media-drc` |

`OMDRC_SITE_DIR` predates the split and is unrelated to it — except when running
`drc.sh` uninstalled from the checkout, where it defaults to the checkout itself
and so no longer finds room sets that moved out. Point it at the site repo:

```sh
OMDRC_SITE_DIR=~/devel/omdrc-801N ./drc.sh 120.blue 48000
```

It takes a single directory, not a search path, so uninstalled `drc.sh` sees
either the engine repo's `flat` or the site repo's room sets — not both at once.

An installed system is unaffected: `make install` merges both halves into
`$PREFIX/etc/open-media-drc`, and `drc.sh` reads only that.

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
