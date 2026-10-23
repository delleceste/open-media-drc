# Experimental PCM bridge deployment on the Cambridge DAC host

Status: deployment plan, 2026-10-23. This plan does not switch the live audio
chain. The first run is a supervised, temporary 48 kHz stereo S32 trial with
music only. It uses `/usr/local/sbin/omdrc-pcm-bridge` in place of virtual_oss,
BruteFIR with a zero-attenuation Dirac pulse, and the current `/dev/dsp.dac`
role link for output.

```text
temporary MPD (48 kHz, stereo S32)
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
  transfer. Use a temporary copy of the 48 kHz flat config with both
  coefficient attenuations set to `0.0` for this experiment. Keep output
  `dither: false` and any persisted CLI gain override out of the trial.
- Installed musicpd is configured from
  `/usr/local/etc/open-media-drc/musicpd.conf`. Its `DRC-native` output uses
  `*:*:*` and `DRC-resamp` uses `192000:24:2`. Neither guarantees the bridge's
  exact stereo S32 contract. Use a separate temporary musicpd instance, with
  its own database, Unix control socket, and a single OSS output whose format
  is explicitly `48000:32:2`. Do not alter the installed player's config for
  the first run.
- `/dev/cuse` is root-only on this host. A bridge launched as root creates
  mode-0600 root-owned client nodes; musicpd and BruteFIR normally run as
  `giacomo`. Before live testing, add and test a bridge option that selects
  the **CUSE client node owner** (`giacomo`) while the daemon retains the
  permission needed to open `/dev/cuse`. Validate the numeric UID/GID and
  reject invalid input. Do not change the global `/dev/cuse` permissions.
  A diagnostic root-only run does not establish that the deployed clients can
  open the nodes.
- `drc.sh` still starts and stops virtual_oss by process name and expects
  `/dev/dsp.play` and `/dev/dsp.loop`. Its output routing, rollback, status,
  CD-source handoff, and hotplug restoration do not know this bridge. Do not
  call `drc.sh` to start the isolated trial. Before any persistent deployment,
  add an explicit backend selection and update every lifecycle branch.

## Trial sequence

1. Complete the node-owner option and add a live test that runs the bridge as
   root while an unprivileged `giacomo` client opens both 0600 CUSE nodes.
   Repeat format-rejection, exact-byte, backpressure, active-client shutdown,
   and node-removal tests on the **installed** executable.
2. Prepare a private trial directory owned by `giacomo`, containing a copy of
   the BruteFIR defaults (with input pointing to the experiment loop node,
   output still `/dev/dsp.dac`, and cache/config paths kept in that directory),
   the flat 48 kHz config with zero attenuation, and a musicpd config with
   only the experiment output. Validate that no inherited `cfoa`, volume
   normalization, equalizer, or dither changes the sample values. Keep the
   original installed config files and state untouched.
3. Generate a deterministic stereo 48 kHz S32 WAV with ample headroom and
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
5. Start the bridge with `--rate 48000 --format s32le --channels 2`, a bounded
   queue (initially 200 ms), unique experiment node names, and the audio-user
   node owner. Wait for both nodes, verify their type and owner, then start
   BruteFIR as `giacomo` with its temporary Dirac config. Verify that it holds
   the experiment loop for reading and `/dev/dsp.dac` for writing; confirm
   the DAC reports the requested rate and a bit-perfect playback feeder path.
6. Start the temporary musicpd as `giacomo` on its private socket. Check that
   its sole output negotiates `48000:32:2` on the experiment play node. Play
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
Then add a selectable backend to `drc.sh`, MPD S32 outputs, boot/hotplug
selection, status and rollback. Default to the existing virtual_oss path until
those checks pass. Keep a one-command route back to direct DAC playback.
