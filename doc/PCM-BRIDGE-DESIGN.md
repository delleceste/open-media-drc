# Minimal FreeBSD PCM bridge

Status: design proposal, 2026-10-23. No implementation or deployment yet.

## Decision and scope

Replace the upstream `virtual_oss` instance with a small, single-stream OSS
loopback. Keep BruteFIR writing directly to `/dev/dsp.dac`. The bridge transports
PCM bytes; it does not open the DAC, mix streams, or process sample values.

```text
MPD or selected producer → dsp.play → bounded byte queue → dsp.loop
                                                           ↓
                                                        BruteFIR
                                                           ↓
                                            dsp.dac → PCM → USB → DAC
```

Two separate guarantees must be tested:

1. Bytes accepted from the producer are delivered unchanged to BruteFIR,
   in order and exactly once during an uninterrupted stream session.
2. Bytes emitted by BruteFIR reach the USB audio payload unchanged, accounting
   explicitly for the negotiated transport container and channel layout.

Room correction intentionally changes samples. Comparing the original music
with the USB stream is not the second guarantee's test. Host USB capture also
does not establish what processing occurs inside the DAC.

## Source evidence

These findings refer to the local sources inspected on the date above, not an
assumption about every upstream version. Neither BruteFIR nor virtual_oss was
running during inspection.

The normal launch in `drc.sh` is:

```sh
virtual_oss -D /tmp/virtual_oss.pid -r "$actual_rate" \
  -i 8 -C 2 -c 2 -b 32 -s 200ms \
  -f /dev/null -a 0 -d dsp.play -L dsp.loop
```

The source of truth for parsing is
`/usr/src/usr.sbin/virtual_oss/virtual_oss/main.c`, `parse_options()`
(the switch begins around line 2044 in the inspected tree).

| Option | Source behavior |
| --- | --- |
| `-D` | PID-file path. |
| `-r` | Global engine rate. |
| `-i 8` | Request realtime scheduling priority 8; print a warning on failure. |
| `-C 2` | Engine mixing-channel count. |
| `-c 2` | Channel count for subsequent device profiles. |
| `-b 32` | Profile sample depth; establishes backend depth when first configured. |
| `-s 200ms` | Processing block of rate × 0.2 frames, not total queue latency. |
| `-f /dev/null` | Both backend directions use the null backend. No DAC open. |
| `-a 0` | Zero input/output amplification shifts. |
| `-d dsp.play` | Ordinary virtual OSS device profile. |
| `-L dsp.loop` | Synchronized loopback profile. |

`-S` is absent: optional virtual_oss resampling is not enabled. The installed
standalone `brutefir_loopback` rc script has a different default block, `4ms`;
that is not the normal `drc.sh` command's `200ms`.

In `virtual_oss.c`, `virtual_oss_mixer_core()` selects noise injection for a
positive original shift. The ordinary-client mixer calculates the format
shift as profile bits minus client sample bytes × 8. Therefore unity gain
alone does not guarantee transparency when expanding narrower samples into
the S32 profile. Same-width S32 avoids that particular expansion-noise path.
The replacement will contain no sample arithmetic.

`/usr/src/lib/virtual_oss/oss/oss.c` verifies negotiated sample rate and format,
but permits a reduced backend channel count. Our strict contract permits no
such fallback.

The local BruteFIR source is
`/home/giacomo/devel/brutefir/src/bfio_oss.c`. It negotiates format, channels,
rate and fragment size, queries capabilities, optionally uses triggers, and
queries `GETISPACE`/`GETOSPACE` before read/write. A FIFO alone cannot replace
its OSS device interface.

At inspection `/dev/dsp.dac` resolved to `dsp0`, a Cambridge Audio DAC100;
`dev.pcm.0.bitperfect=1`, `dev.pcm.0.play.vchans=0`, and
`hw.snd.vchans_enable=0`. These are observations, not permanent device IDs or
proof of delivered bytes. `/usr/src/sys/dev/sound/pcm/feeder_chain.c` bypasses
most feeder-chain construction for `CHN_BITPERFECT` channels.

## Initial interface

Use CUSE to retain existing OSS clients. This keeps the CUSE dependency and
its kernel lifecycle risks; a smaller daemon does not itself fix kernel bugs.

Initially support exactly stereo interleaved signed S32_LE at a fixed,
explicit session rate. Support the rates already used by the application
only after checking the attached DAC's capabilities. A producer requesting
S16 or S24 must fail explicitly; configure its output to S32 before migration.
Do not silently widen samples or enable resampling. Additional lossless
container conversion, if ever required, is a separate feature with separate
tests and a revised byte-transparency contract.

Proposed invocation (not an existing executable):

```sh
omdrc-pcm-bridge --rate 48000 --format s32le --channels 2 \
  --capacity-ms 200 --play dsp.play --capture dsp.loop
```

Capacity is a maximum queue size, not a processing quantum or target fill.
At 48 kHz, 200 ms of stereo S32 is 76,800 bytes; at 192 kHz it is 307,200 bytes.
Begin with a configurable 200 ms capacity for measurement, then tune from
BruteFIR periods and scheduling-stall measurements. Do not copy the old
engine's 200 ms processing block into the new read/write scheduling.

Allow one producer and one consumer. A second opener for an occupied direction
gets `EBUSY`. Unsupported access directions and duplex opens fail. The control
layer must release MPD before handing ownership to CD input or another source;
the old mixer allowed overlapping writers, so this is an integration change.

Implement and test the OSS subset required by both ends:

- Exact `SETFMT`, `CHANNELS`, `SPEED` negotiation and corresponding queries.
- `SETFRAGMENT`, `GETBLKSIZE`, `GETISPACE`, and `GETOSPACE`, with consistent
  byte counts and fragment geometry. Fragment size is independent of capacity.
- `GETCAPS`, advertising only implemented capabilities; initially do not
  advertise trigger support unless producer compatibility requires it.
- Blocking and nonblocking read/write, readiness notification, interruption,
  and cancellation. Inspect the actual MPD and CD clients before freezing
  this list; BruteFIR alone is not the complete compatibility requirement.
- Explicit reset and drain semantics for the ioctls used by those clients.
  Unknown operations fail with an appropriate error, never fake success.

If triggers are required, track the open direction independently from trigger
bits, wake all affected waits on transitions, and test stop/start while queues
are full and empty. Do not repeat the synchronized-loopback deadlock pattern.

## Queue and lifecycle rules

Store raw bytes in one bounded ring. Maintain exact accepted/delivered byte
counters; never overwrite unread data. Handle arbitrary syscall boundaries
without changing byte order, while exposing complete frames to the consumer.
Retain partial-frame tails; report an incomplete final frame at drain/close
rather than silently padding it. The frame size is eight bytes in version one.

The DAC is the ultimate clock: its consumption backpressures BruteFIR, which
backpressures bridge reads and then producer writes. No periodic audio worker,
independent sample clock, busy polling, or timer-driven silence generation is
needed. Never hold the queue lock while copying through potentially blocking
CUSE operations or waiting for a peer. Reserve/commit transfers must remain
safe across cancellation and session changes.

Use explicit states: waiting for peers, ready, active, draining, failed, and
stopping. Each open/session has a generation number so pending work cannot
commit into a replacement session.

- Empty queue: consumer waits, or gets `EAGAIN` in nonblocking mode.
- Full queue: producer waits, or gets `EAGAIN`; no dropping oldest/newest data.
- Orderly drain: deliver all accepted complete frames while the consumer is
  available. Define a bounded cancellation path if the consumer disappears.
- Reset/abort: discard pending data explicitly, count it, and wake waiters.
- Unexpected peer close: end the session and notify the survivor; do not
  concatenate a replacement producer's data with an old queue accidentally.
- Shutdown: stop accepting opens, cancel waits, finish/cancel in-flight copies,
  release references, and destroy devices without holding a lock needed by
  a closing client. Verify against the locally installed CUSE kernel fixes.
- Rate change: stop the old session and reopen the chain with the new exact
  format. Never reinterpret queued bytes at a new rate.

Starvation is a discontinuity, not a successful continuous-transfer result.
Blocking reads may let BruteFIR/DAC underrun when the producer pauses. Before
rollout, decide and test the application's idle behavior: pause/stop the chain,
or have an explicitly identified upstream source supply idle silence. The
bridge itself must not conceal missing source frames by inventing samples.
Latency through BruteFIR's filters and queued hardware data must be included
when implementing drain or stop; draining this queue alone is insufficient.

Expose queue occupancy/high-water mark, accepted/delivered/discarded bytes,
peer identities, format, generation, blocked durations, resets and failures.
Read-only diagnostics must never consume PCM or block the audio path. Any
future capture tap needs its own bounded queue and explicit loss reporting.

## Validation and rollout gates

1. Audit the installed producer and BruteFIR OSS usage, including poll/select,
   pause, close, trigger and error behavior. Confirm producer S32 output for
   native S16/S24 content. Record required ioctls before implementing CUSE.
2. Test the ring and session state machine with deterministic random data,
   extrema, zeros, channel-distinct counters, odd transfer sizes, wraparound,
   partial transfers and cancellation. Assert exact bytes and conservation:
   accepted = delivered + queued + explicitly discarded, at consistent snapshots.
3. Expose isolated `dsp.omdrcbridgeplay` / `dsp.omdrcbridgeloop` nodes. Test
   format rejection, multiple-open rejection, full/empty backpressure,
   interruptions, peer crashes, reopen, shutdown, and sustained scheduler load.
   Repeated teardown must leave neither stuck clients nor pinned CUSE devices.
4. Run the real clients on isolated nodes with temporary BruteFIR configuration.
   Compare the bridge's output with producer input; establish idle/resume and
   source-handoff behavior. Check bounded memory and absence of busy loops.
5. Validate DAC output independently using the existing USB capture tooling in
   `scripts/bitperfect-tap-freebsd.sh` and `scripts/bitperfect-lib.py`. Discover
   the current DAC's endpoint and alternate-setting format; do not assume the
   historical OKTO endpoint/layout applies to the Cambridge DAC.
6. Capture a deterministic stream with unique frame markers, then compare
   complete USB payload sequences after accounting for startup/drain boundaries.
   Compare against actual BruteFIR output for a real-filter test; a validated
   identity-filter run is an additional control. Require no unexpected internal
   gaps, duplicated frames or modified bytes. Capture loss makes the test
   inconclusive, not passed. Do not claim exact S32 byte preservation when the
   negotiated USB container differs; reject or separately specify conversion.
7. Test every supported rate, track transitions, prolonged pause/resume,
   source handoff, rate changes, DAC removal/replug and process failure.
   Record errors and recovery outcomes separately from uninterrupted-stream
   bit-perfect results. Inspect the active PCM feeder graph as supporting evidence.

Only after these gates, add an explicit selectable backend in `drc.sh`, update
process/device readiness and status checks, and preserve the virtual_oss path
for rollback. Start the bridge before BruteFIR; enable the producer only after
both are ready. Stop producers before dismantling the consumer and bridge.
Install only the FreeBSD bridge and affected orchestration; no Android APK is
needed. Verify installed behavior and service health before changing defaults.

The first implementation milestone is the tested queue/state machine and an
isolated CUSE prototype. Production replacement is a later milestone gated by
real-client compatibility and USB verification. A downstream DAC-owning bridge
is outside this design; direct BruteFIR OSS output remains the reference path.
