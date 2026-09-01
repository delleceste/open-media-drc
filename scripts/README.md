# scripts/ — helper tools

Utility scripts for filter generation, headroom calculation, chain
verification, and service installation. Each is documented in depth in the
main [README](../README.md) or in `doc/`; this file is the quick index.

| Script | Purpose | Platform |
|---|---|---|
| `REW2raw.sh` | Convert one REW-exported WAV impulse response to a brutefir-ready raw `FLOAT64_LE` file, resampling to a target rate (default 192 kHz) with the theoretically correct FIR coefficient scale (`Fs_source / Fs_target`, no peak normalisation). | Linux + FreeBSD (needs `sox`) |
| `REW2raw-all-rates.sh` | Batch wrapper around `REW2raw.sh`: generates the `L.raw` / `R.raw` pair (plus a `sox.txt` conversion log) for **every** numeric sample-rate directory under a filter root, e.g. `filters/120.blue/{44100,48000,88200,96000,192000}/`. Asks before overwriting unless `-y`. | Linux + FreeBSD |
| `headroom_calc.py` | Computes the minimum `attenuation:` value for each brutefir `.conf` from the filters' worst-case FFT gain (+ safety margin, default 1 dB), so playback never clips while dynamics are maximised. Run it after every filter (re)generation. | Linux + FreeBSD (python3) |
| `new_filter_design.py` | The one deployment command: takes the directory REW exported a session into, resolves every role from the file names, verifies each filter TXT against its impulse WAV, hashes the `.mdat` and requires the project commit, reports everything, asks, then publishes and commits the result in the room repository. It never starts REW. | Linux + FreeBSD (python3, NumPy, SoX, Git) |
| `remove_filter_design.py` | Removes one deployed design completely — manifest, analysis, source copies, every rate's coefficient pair and every BruteFIR config template — in an order that never leaves a manifest naming a deleted file, then records the removal in the room repository. Refuses the reserved `default` set. | Linux + FreeBSD (python3, Git) |
| `deploy_filter.py` | Builder library behind the command: regenerates all requested rates in staging, validates TXT↔WAV response, config mapping and headroom, then publishes source copies, graph data and a hash-bound manifest, and commits the room repository. Not called directly. | Linux + FreeBSD (python3, NumPy, SoX, Git) |
| `verify_filter_bundle.py` | Read-only verification of bundle ID, source copies, graph dependencies, configs, exact runtime RAW hashes and headroom. A successful direct run repeats the install/select/UI handoff; use `--no-next` for CMake, CI or scripts. | Linux + FreeBSD (python3, NumPy) |
| `console_ui.py` | Shared terminal formatting for the design commands: stages, colours, confirmation prompts and the uniform `FAIL` line. Not called directly. | Linux + FreeBSD (python3) |
| `filter_workflow_next.py` | Shared operator handoff printed by the commands above: the exact verify/commit/configure/install/select sequence, with a per-step working directory when the site data lives in its own repository. Not called directly. | Linux + FreeBSD (python3) |
| `rew_mdat_audit.py` | Optional archival evidence: audits selected REW project traces through the REW API without a GUI, comparing exported TXT responses numerically and final WAV impulses sample-by-sample against the project, and recording the trace inventory with exact UUIDs. Not a deployment dependency. | Linux + FreeBSD (python3, NumPy) |
| `verify-bitperfect.sh` | End-to-end bit-perfectness proof: feeds a deterministic S32_LE signal through a chosen source (built-in OSS writer, or MPD by output name) and compares it byte-for-byte against a chosen tap (the OKTO's isochronous USB OUT endpoint via `usbdump`, or an OSS loopback node such as `/dev/dsp.loop`). See [`doc/BIT-PERFECT-VERIFICATION.md`](../doc/BIT-PERFECT-VERIFICATION.md). | FreeBSD (USB tap needs root) |
| `bitperfect-tap-linux.sh` | Plays a WAV (16/24/32-bit, any supported rate) to the USB DAC and records the exact bytes sent on the USB wire (usbmon tap of isochronous OUT endpoint 0x01) into `PREFIX.wav` / `PREFIX.wire.raw` / `PREFIX.txt`, with a local bit-perfect verdict. Same CLI and artifacts as the FreeBSD twin, for cross-OS comparison. | Linux (tap needs root) |
| `bitperfect-tap-freebsd.sh` | FreeBSD twin of the above (`usbdump` tap, format-guarded OSS writer on `/dev/dsp.dac`). | FreeBSD (tap needs root) |
| `bitperfect-compare.py` | Opens two tap artifacts (from either OS; `.wav`, `.wire.raw`, or the tiny committable `.txt` report — hash-proxy comparison, so the 10 MB streams never need to travel through git) and verdicts **MATCH: byte-by-byte identical** or **MISMATCH** with the first differing offset (when payloads are present). | Linux + FreeBSD (python3) |
| `bitperfect-lib.py` | Shared engine for the tap scripts and the panel (WAV→S32 wire-container promotion, usbmon reader, usbdump decoder, alignment/verdict/report, plus `window`/`scan` which read the two compared streams back for the browser's byte view). Mostly not called directly. | Linux + FreeBSD |
| `bitperfect_runner.py` | Runs a tap through a chosen playback path — `aplay` (delegates to the tap script above, unchanged), `mpd`, `mpd-http`, `upnp` (drives upmpdcli over OpenHome) or `live` (taps a real Qobuz stream and compares against the buffer the renderer itself wrote). Backs the panel's `/bitperfect` page; emits `@@PHASE`/`@@STAT`/`@@RESULT` progress lines. | Linux + FreeBSD (tap needs root) |
| `bitperfect_material.py` | Turns any track into a run's inputs: decodes WAV/FLAC (or anything `ffmpeg` reads) into the reference stream, checks the alignment anchor is unambiguous, and — for the `live` source — finds what the renderer is streaming (`mpc current`, then open file descriptors, then the known `/tmp` buffers, newest-during-the-tap only). | Linux + FreeBSD (python3) |

## Command-line alternative

This is an alternative to the control panel's `/configuration` live installer,
not a second procedure to run afterwards. Use it when the generated site data
must be reviewed offline, used as CMake provisioning input, or transferred to
another playback machine. For an ordinary live install, use `/configuration`
and skip this section entirely; the page invokes these tools for you.

In this alternative the two repositories have different ownership:

- the REW project repository owns the editable `.mdat`, `.txts`, and source
  WAV/TXT files;
- the site-data repository owns generated `configs/<geometry>` and
  `filters/<geometry>` bundles.

Committing both gives the command-line workflow Git-backed history. The web
installer archives the complete inputs in its persistent design root and uses
Git history when that root already has it; Git is not a web-install prerequisite.

**Deploy a design** — one command, one directory:

```sh
python3 scripts/new_filter_design.py ../DRC/DRC-120.blue/120.blue.Rscreen.txts
```

The directory is where REW exported the session, beside the `.mdat` it came
from. Nothing on the command line says what a file is; the names do:

```text
L.txt  R.txt                    measured left/right, before correction
LR.txt   or   L+R.txt           measured pair: REW's vector average, or the sum
FLX-trimmed.txt  FRX-trimmed.txt        exported filter responses
FLX-trimmed-48k.wav  FRX-trimmed-48k.wav   deployable impulses
L.filtered.txt  R.filtered.txt          REW's filtered result per channel
LR.filtered.txt  or  L+R.filtered.txt   the filtered pair
   or  L+R.remeasured.txt               the room measured again, DRC running
```

`.txt` is optional and case is ignored. Exactly one aggregate style: `LR.txt`
with `LR.filtered.txt`, or `L+R.txt` with `L+R.filtered.txt` or
`L+R.remeasured.txt`. A re-measurement is always a sum, never a vector average,
so `LR.txt` beside `L+R.remeasured.txt` is refused. Missing, duplicate or
mismatched names stop the run before anything is written, in colour, naming the
offending files.

Geometry and design ID come from the path —
`DRC-120.blue/120.blue.Rscreen.txts` reads as `120.blue` / `Rscreen`. Use
`--geometry` and `--design` to override, for instance to date the design as
`rscreen-20260812`.

**What every export must be.** Three checks run before anything is hashed or
written, and none of them has an override:

- every text export is **unsmoothed** (`* Smoothing: None`; an export that
  states no smoothing is refused too, since it cannot be shown to be
  unsmoothed). REW's smoothing is baked into the numbers and cannot be undone
  later; the browser's Smoothing selector is a separate, reversible view;
- every text export comes from a **measurement at 48 kHz or below**, checked as
  *it must not reach past 24 kHz* — the runtime coefficients are all resampled
  from one 48 kHz impulse;
- each **filter TXT is the exported response of its WAV**: one integer causal
  delay and one constant export gain are detected, then the residual magnitude
  and phase errors must stay inside `DEFAULT_LIMITS`. This is what makes the
  plotted FLX/FRX curve a statement about the bytes BruteFIR will load, and it
  is the only DSP in the pipeline.

The first two are reported together, so one message names every offending file.

The run prints the eight curves the web remote will plot with their point counts
and REW smoothing, the project and REW session behind them, the measurement
headers, the two filters with their TXT/WAV residuals, and every path that will
be written. Then it asks. `--dry-run` runs every check, including the SoX
conversions, and stops without asking. `--yes` skips the prompt and refuses to
assume consent when standard input is not a terminal.

**What the run requires of Git.** The export directory must be in a work tree,
and the ten inputs and the `.mdat` must be committed — the manifest records the
commit and the blob IDs, which is what makes the measurements retrievable later.
Uncommitted files stop the deployment and print the `git add` / `git commit`
lines to run; `--allow-uncommitted` proceeds and records the gap in the
manifest, where the verifier and the web UI both show it.

After publication the command makes one commit in the room repository
(`filters/<geometry>` and `configs/<geometry>`) naming the geometry, design,
bundle ID, project commit, session hash and rates. `git log` there is the
history of every filter set that was ever live:

```sh
git -C ../omdrc-801N log --oneline -- filters/120.blue
git -C ../omdrc-801N checkout <commit> -- filters/120.blue configs/120.blue
```

`--no-commit` skips it; a room that is not a work tree warns and deploys anyway.
The web frontend adds `--require-clean-site --require-commit` when its
configured `design_root` is a Git repository, and never adds
`--allow-uncommitted` in that case. A non-Git design root instead uses the
explicit `--allow-uncommitted --no-commit` mode.

**Then verify, install and select:**

```sh
python3 scripts/verify_filter_bundle.py --all --require-sources
./drc.sh design --list
./drc.sh design @Rscreen
```

**Progress output.** `deploy_filter.py` runs `REW2raw.sh`/SoX for each
left/right target-rate conversion; those lines are magenta and print the exact
FIR coefficient scale (`source_rate / target_rate`) and signed SoX gain in dB.
The worst L/R FFT peak and safety-margin arithmetic follow in yellow, with the
required attenuation rounded upward to 0.1 dB, then the BruteFIR config bake and
read-back verification in blue. A dry run bakes configs only in private staging;
a real run adds a blue `CONFIG PUBLISHED` line per template copied into
`configs/<geometry>/`. Colour turns itself off for pipes and log files and obeys
`NO_COLOR=1`.

At the end, **NEXT** prints underlined `Run from:` directories and copy/paste
commands for bundle verification, CMake configuration that preserves the current
geometry set, build/install, service restart and selection of the new
`@design-id`, ending with the bundle ID the green **Filter response** identity
must show. `verify_filter_bundle.py` repeats that handoff when run directly;
CMake invokes it with `--no-next` so configuration output stays compact.

**A design owns its directories.** `filters/<geo>/source/<design>/` and every
`filters/<geo>/<rate>/@<design>/` end up holding exactly what the new manifest
names: a redeployment that renames its inputs prunes the files the previous one
left there, so nothing sits beside a bundle that the bundle does not account
for. Pruning happens after the manifest is written, so a failure leaves harmless
leftovers rather than a manifest naming a deleted file. A bare
`filters/<geo>/<rate>/` is shared with the `default` set and is never touched.
The dry run lists what would be pruned.

**Remove a design** — the exact inverse, and just as complete:

```sh
python3 scripts/remove_filter_design.py --list          # what is deployed
python3 scripts/remove_filter_design.py 120.blue@rscreen-20260812 --dry-run
python3 scripts/remove_filter_design.py 120.blue@rscreen-20260812
```

The selector is the one the UI and `omdrc design --list` show. It deletes the
manifest, the build recipe, the analysis file, `source/<design>/`, every
`<rate>/@<design>/` pair and every `brutefir-<rate>@<design>.conf.in` (plus any
rendered `.conf`), and nothing else — the shared default set and other designs
are never touched. It reports what the design was (bundle, project, session,
rates) and exactly which paths and how many bytes will go, then asks.

The manifest is deleted first: while it exists it is the claim that everything
else is present, so once it is gone the design is already invisible to the web
remote, and no reader can meet a manifest that names a missing file. The
removal is then one commit in the room repository, so the design remains
recoverable from the deployment commit that introduced it. `--no-commit`,
`--dry-run` and `--yes` behave as they do when deploying, and the reserved
`default` set is refused — its un-suffixed configs and coefficients are what a
geometry falls back to.

Removing does not touch an installed machine: re-run CMake, `make install` and
restart the service, exactly as the printed **NEXT** section says.

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
python3 scripts/new_filter_design.py …    # writes and commits in the site repo
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
./drc.sh off        # and stop any renderer holding the DAC
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
