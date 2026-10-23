# Experimental FreeBSD PCM bridge

`omdrc-pcm-bridge` implements the first milestone of
[the bridge design](../doc/PCM-BRIDGE-DESIGN.md): a bounded byte queue with two
CUSE OSS endpoints, one writer and one reader. It never opens the DAC and never
mixes, rescales, dithers or resamples audio. Stereo interleaved S32_LE and an
explicit fixed rate are mandatory. A mismatched negotiation fails.

This is an isolated prototype, **not yet a replacement for the production
virtual_oss backend**. `drc.sh` continues to use virtual_oss. The normal build
omits the prototype unless `OMDRC_BUILD_PCM_BRIDGE=ON` is selected.

## Build and install

```sh
cmake -S pcm-bridge -B /tmp/omdrc-pcm-bridge-build -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/omdrc-pcm-bridge-build
ctest --test-dir /tmp/omdrc-pcm-bridge-build --output-on-failure
sudo cmake --install /tmp/omdrc-pcm-bridge-build --prefix /usr/local --component pcm-bridge
```

The standalone queue test is portable C. The daemon and OSS client tests require
FreeBSD, libcuse and pthreads. All C targets compile with warnings as errors;
test assertions remain enabled in release builds.

```sh
sudo kldload cuse                       # only if not already loaded
sudo /usr/local/sbin/omdrc-pcm-bridge --rate 192000 --user giacomo
```

The foreground process creates `/dev/dsp.omdrcbridgeplay` (write-only) and
`/dev/dsp.omdrcbridgeloop` (read-only), mode 0600. `--user LOGIN` sets their
owner and primary group so unprivileged musicpd and BruteFIR can open them
while the daemon retains access to root-only `/dev/cuse`. Without `--user`,
the effective launch user/group owns the nodes. No service is
started or enabled by installation. Names can be overridden with `--play` and
`--capture`; do not point production clients at these until rollout gates pass.

`--capacity-ms` defaults to 200 and limits queue capacity; it is not a processing
period or minimum latency. `--format s32le --channels 2` are accepted explicitly.
Use `--help` for syntax. SIGUSR1 prints a consistent counter snapshot; SIGTERM
or SIGINT cancels blocked requests, joins workers, and removes both nodes.

## Implemented contract

- Exclusive open per direction; duplex and second-open requests fail.
- Exact rate/channel/format negotiation, format/capability queries, fragment
  sizing, space/delay queries, reset, sync, and blocking-mode changes.
- Poll readiness and blocking/nonblocking transfer. Timed condition waits only
  check libcuse client cancellation; there is no timer-driven audio worker.
- Raw byte buffering across partial writes. Reads expose complete eight-byte
  frames; a read buffer smaller than one frame fails. Partial final frames are
  never padded, and prevent a successful drain.
- Backpressure when full, waiting when empty, and no synthetic silence.
- Copies occur outside the queue lock. Concurrent operations in the same
  direction return busy; a reset during an active copy also returns busy.
- Reset explicitly discards queued data and changes the generation. A request
  blocked across reset fails rather than consuming data from another generation.
- Writer close drains the bridge queue, bounded to five seconds, unless the
  session is already failed. Nonblocking close with queued data cannot drain.
  Sync has the same bound. Neither operation drains downstream FIR/hardware.
- Closing either endpoint ends the session. A surviving file gets an error and
  must close before a new pair can open. This intentionally exposes discontinuity
  instead of silently attaching a new producer to an old consumer.
- Counters cover accepted, delivered, queued/discarded bytes, high-water mark,
  resets, failures and generation. They report open directions, not process IDs
  or per-client timing. Full design diagnostics remain future work.

Trigger, mmap, duplex, software volume and asynchronous SIGIO support are not
advertised. Unknown ioctls fail. Queue capacity is fixed for a process lifetime;
SETFRAGMENT sets per-open reporting/period geometry, not the global capacity.

## Tests

The following explicitly create isolated devices with unique names. They never
open `/dev/dsp.dac`, change live MPD configuration or restart audio services.

```sh
sudo python3 pcm-bridge/test_live.py /tmp/omdrc-pcm-bridge-build --user giacomo --rate 192000
sudo python3 pcm-bridge/test_clients.py /tmp/omdrc-pcm-bridge-build
```

The live suite covers exact negotiation, wrong-format rejection, exclusive
ownership, partial frames, readiness, full/empty queues, a 1 MiB deterministic
byte comparison using mismatched transfer sizes, interrupted reads, reset,
drain-on-close, peer loss, 30 reopen cycles per run, repeated daemon teardown,
and shutdown while a reader or writer is blocked. An optional `--daemon PATH`
tests an installed executable using the build-tree OSS test client.

The real-client suite runs temporary MPD and BruteFIR instances. MPD uses a
private Unix socket/database and a generated S32 WAV. BruteFIR uses isolated
XDG defaults/cache and identity filters, writing to a temporary raw file.
The marked 262,144-byte region is compared exactly at both boundaries.
It uses trailing guard silence and is **not an end-of-stream losslessness test**.

For memory and undefined-behavior checking:

```sh
cmake -S pcm-bridge -B /tmp/omdrc-pcm-bridge-asan \
  -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_FLAGS='-fsanitize=address,undefined -fno-omit-frame-pointer'
cmake --build /tmp/omdrc-pcm-bridge-asan
ctest --test-dir /tmp/omdrc-pcm-bridge-asan --output-on-failure
sudo python3 pcm-bridge/test_live.py /tmp/omdrc-pcm-bridge-asan
```

## Results and production gates

On 2026-10-23, the queue oracle test, live OSS suite (including ASan/UBSan), and
installed MPD/BruteFIR marked-region tests passed on FreeBSD 15.1.
The release executable was installed at `/usr/local/sbin/omdrc-pcm-bridge`;
its hash matched the build artifact and its isolated OSS/shutdown suite passed
at 192 kHz using unprivileged clients and `--user giacomo`.
All temporary nodes and test processes were removed. No production service was
restarted or switched to the bridge.

The MPD experiment also exposed a real integration blocker: at natural EOS the
installed player issued reset with 65,536 bytes still queued in this test.
The bridge correctly discarded them under its reset contract. Guard silence
keeps the marked reference before that boundary, but does not solve it.
Production integration must resolve drain/cancel behavior at its source;
ignoring reset would break intentional seek/stop cancellation. Inspect the
installed MPD implementation and reconcile it with the checked-out OSS plugin,
which describes a drain-on-close path. Do not infer production readiness from
only the marked-region test.

Other gates remain: pause/resume without starving BruteFIR, explicit source
handoff/reopen orchestration, actual CD client tests, native S16/S24 producer
conversion to S32, all supported rates, long-running stress, full DAC/USB
comparison, hotplug recovery, and integration of process/status/readiness checks.
The prototype retains the CUSE kernel dependency; it does not fix kernel bugs.
There is no demonstrated USB bit-perfect result for this new bridge yet.
