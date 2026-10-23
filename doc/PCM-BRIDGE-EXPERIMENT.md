# Experimental PCM bridge deployment on the Cambridge DAC host

Status: deployment plan, 2026-10-23. This plan does not switch the live audio
chain. The first run is a supervised, temporary 192 kHz stereo S32 trial with
music only. It uses `/usr/local/sbin/omdrc-pcm-bridge` in place of virtual_oss,
BruteFIR with a zero-attenuation Dirac pulse, and the current `/dev/dsp.dac`
role link for output.

```text
temporary MPD (192 kHz, stereo S32)
  → /dev/dsp.omdrc_experiment_play
  → omdrc-pcm-bridge (bounded queue)
  → /dev/dsp.omdrc_experiment_loop
  → BruteFIR (Dirac pulse, 0 dB, dither off)
  → /dev/dsp.dac → FreeBSD PCM → USB → Cambridge DAC
```

The bridge carries only MPD-to-BruteFIR PCM. BruteFIR keeps its direct OSS
output to the DAC. This deliberately excludes CD input and simultaneous audio
sources: the bridge has one writer, whereas virtual_oss mixed multiple clients.

## Current host facts and required preparation

- The installed bridge passed isolated CUSE and real-client byte tests and is
  installed but not started as a service. Neither musicpd, BruteFIR nor
  virtual_oss was running when this plan was prepared. `/dev/dsp.dac` pointed
  to the Cambridge Audio DAC100 (`dsp0`). Rediscover the DAC role at run time.
- `/usr/local/etc/open-media-drc/omdrc.conf` selects `GEOMETRY=flat`. The
  installed flat BruteFIR configs use `"dirac pulse"` but also set
  `attenuation: 8.0`. That is no room correction, yet it is **not** an identity
  transfer. Use a temporary copy of the 192 kHz flat config with both
  coefficient attenuations set to `0.0` for this experiment. Keep output
  `dither: false` and any persisted CLI gain override out of the trial.
- Installed musicpd is configured from
  `/usr/local/etc/open-media-drc/musicpd.conf`. Its `DRC-native` output uses
  `*:*:*` and `DRC-resamp` uses `192000:24:2`. The new `DRC-bridge` output is
  installed, fixed at `192000:32:2`, and disabled by default. Use a separate
  temporary musicpd instance for the first end-to-end trial so its EOS behavior
  cannot affect the production player. The fixed 192 kHz output makes MPD
  convert lower-rate sources; only native 192 kHz sources can be considered
  for an end-to-end bit-perfect test.
- `/dev/cuse` is root-only on this host. The bridge now accepts `--user giacomo`
  to create mode-0600 nodes owned by the playback user while the daemon runs as
  root. The installed binary passed the live OSS suite at 192 kHz with those
  node permissions.
- `drc.sh` still starts and stops virtual_oss by process name and expects
  `/dev/dsp.play` and `/dev/dsp.loop`. Its output routing, rollback, status,
  CD-source handoff, and hotplug restoration do not know this bridge. Do not
  call `drc.sh` to start the isolated trial. Before any persistent deployment,
  add an explicit backend selection and update every lifecycle branch.

## Trial sequence

1. Recheck the installed bridge and `/dev/cuse` permissions before the trial.
   The unprivileged 192 kHz OSS test already passed against the installed
   executable, including format rejection, exact bytes, backpressure, active
   client shutdown, and node removal.
2. Prepare a private trial directory owned by `giacomo`, containing a copy of
   the BruteFIR defaults (with input pointing to the experiment loop node,
   output still `/dev/dsp.dac`, and cache/config paths kept in that directory),
   the flat 192 kHz config with zero attenuation, and a musicpd config with
   only the experiment output. Validate that no inherited `cfoa`, volume
   normalization, equalizer, or dither changes the sample values. Keep the
   original installed config files and state untouched.
3. Generate a deterministic stereo 192 kHz S32 WAV with ample headroom and
   channel-distinct samples. Append several seconds of guard silence, then
   record the exact marked payload as the comparison reference. Use a short
   duration initially; the existing USB tap tools explain alignment, but
   their historical OKTO endpoint must not be assumed for this Cambridge DAC.
4. Ensure the DAC has no owner and no automatic DRC restore can race the
   trial. Snapshot running processes, enabled services, MPD outputs, DAC role,
   `dev.pcm.<unit>.bitperfect`, and `play.vchans`. If a production renderer is
   running, stop its output through its normal control path and verify the DAC
   file is released. Temporarily suppress only the restore path that could
   restart `drc.sh`; record precisely how to restore it. Do not remove any
   user's pending state or edit the production configuration.
5. Start the bridge with `--rate 192000 --format s32le --channels 2`, a bounded
   queue (initially 200 ms), unique experiment node names, and the audio-user
   node owner. Wait for both nodes, verify their type and owner, then start
   BruteFIR as `giacomo` with its temporary Dirac config. Verify that it holds
   the experiment loop for reading and `/dev/dsp.dac` for writing; confirm
   the DAC reports the requested rate and a bit-perfect playback feeder path.
6. Start the temporary musicpd as `giacomo` on its private socket. Check that
   its sole output negotiates `192000:32:2` on the experiment play node. Play
   the deterministic WAV once. Capture BruteFIR's effective config, bridge
   counters immediately before/after playback, BruteFIR underrun/overflow
   logs, DAC feedback rate, and USB OUT payloads from the discovered audio
   endpoint. Run a sustained middle-of-track capture as well as one natural
   end-of-track capture. Pause/resume and seek are separate trials, never
   included in an uninterrupted bit-perfect verdict.
7. For the marked region, align USB payloads to the WAV's S32 PCM, compare
   complete frames and count missing/duplicate frames. A zero-attenuation
   Dirac pulse should make this a valid end-to-end identity check, subject to
   BruteFIR's internal round-trip accuracy. Verify the active USB alternate
   setting really carries stereo S32 before claiming byte equality. If it
   uses another container, test the conversion separately and do not label
   the result a bit-perfect S32 transfer. Capture loss makes the result
   inconclusive. Report the actual sample differences if BruteFIR rounds.
8. Inspect the EOS result separately. The previous isolated real-MPD test
   observed `RESET` with 65,536 bytes still queued, which the bridge correctly
   discarded. Guard silence can keep the marked region intact, but it does
   not prove complete-track delivery. Resolve MPD's drain/cancel behavior and
   repeat until a natural completion has no unexplained discarded audio.

## Stop and rollback

Stop playback and close the temporary MPD output while BruteFIR still reads.
Inspect the bridge's delivered/discarded counts. Stop the temporary MPD,
then BruteFIR so it releases `/dev/dsp.dac`, then terminate the bridge and
verify both CUSE nodes disappear. If MPD cannot close within a bounded time,
stop the temporary clients and bridge by their recorded PIDs; investigate any
stuck CUSE client before restarting audio. Restore only the service/restore
state changed for the trial. Finally restart the previous renderer or use the
existing `drc.sh restore` path and check its output selection, DAC holder,
process health, and audible output. A failed trial must not leave a root-owned
DAC or orphaned bridge behind.

## Criteria before persistent integration

The first trial answers whether this bridge transports a live identity stream
to this USB DAC correctly. Persistence needs additional evidence: fixed EOS
drain behavior, repeatable pause/resume, native S16/S24 music expanded to S32
with documented precision, supported sample rates, CD handoff or an explicit
CD exclusion, device detach/reattach, and long-run starvation/teardown tests.
Then add a selectable backend to `drc.sh`, boot/hotplug selection, and rollback.
The disabled MPD S32 output and read-only status are already present. Default
to the existing virtual_oss path until those checks pass. Keep a one-command
route back to direct DAC playback.

The intended `drc.sh` launch sequence is: persist an explicit backend choice;
disable the direct and legacy DRC MPD outputs while the old reader still runs;
stop BruteFIR and virtual_oss; launch the bridge as root with `--user giacomo`,
`--rate 192000`, and the canonical `/dev/dsp.play` and `/dev/dsp.loop` names;
wait for both nodes; start the zero-attenuation flat 192 kHz BruteFIR config;
verify the DAC clock and process health; then enable only `DRC-bridge`. On any
failure, disable `DRC-bridge` while BruteFIR can drain, stop BruteFIR and the
bridge, and restore the direct DAC output. `off`, `stop`, `restore`, CD handoff,
and USB detach must follow the same backend choice. The host's installed direct
MPD output is named `cambridge audio DacMagic 100`, while `drc.sh` currently
asks for `OKTO-DAC`; resolve that naming mismatch before using its rollback.

The web and kiosk read `/mpd/info` to show the active bridge and the deliberate
MPD conversion. They should offer a backend selector only after the lifecycle
and EOS gates pass. Android already uses the same DRC/session control path;
there is no Android-specific launch service to add. An Android backend control
would call the same guarded server-side selector and would require a new APK.
