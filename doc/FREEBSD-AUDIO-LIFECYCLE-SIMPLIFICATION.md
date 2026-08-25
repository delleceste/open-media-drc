# FreeBSD audio lifecycle simplification plan

Status: proposal.  This document describes the intended replacement for the
current boot and hotplug chain.  It is not a description of code that has
already been implemented.

## Goal

Make boot, USB sound-card attachment, USB sound-card detachment, and explicit
DRC `on`/`off` requests converge on the correct state without recursive rc
execution, stale marker files, duplicated DRC rebuilds, or several nested
locking protocols.

The preferred deployment considered here has:

- one playback DAC;
- no CD input or capture path;
- no need for `/dev/dsp.capture`;
- DRC either on or off according to the saved user preference;
- direct playback to the DAC while DRC is off.

If the DAC is genuinely the only PCM device FreeBSD can enumerate, the design
can be reduced to one state-changing script and one lock.  If other PCM
devices can appear, a small root-owned device resolver is still required, but
the DRC state machine remains in one place.

## Terminology

### Reconcile

To **reconcile** means:

1. inspect the desired state;
2. inspect the actual state;
3. make only the changes needed to make the two agree.

It is level-triggered: an event means "the world may have changed; inspect it
again", not "blindly replay an attach or detach operation".

A reconcile operation must be idempotent.  Once the state is correct,
repeating it must do no work:

```text
first reconcile:   repair or construct the chain
second reconcile:  already correct, do nothing
third reconcile:   already correct, do nothing
```

This is different from merely putting a lock around `start`.  A lock can
serialize two starts while still allowing both to rebuild the chain:

```text
caller A: lock -> rebuild -> unlock
caller B: wait -> lock -> rebuild again -> unlock
```

The second caller must inspect the actual state *after* acquiring the lock and
then return without rebuilding.

### Debounce

To **debounce** means to collect several rapid notifications and handle them
as one logical change.  A USB audio device may produce a burst of kernel
events while its interfaces and PCM nodes appear:

```text
event --- event - event ----- quiet period ----- one update
```

Debouncing reduces redundant work.  It must not be required for correctness.
If reconciliation is idempotent, ten notifications may cause ten cheap state
checks, but only the first check that finds a mismatch may change the audio
chain.

The first simplified implementation should therefore have no dirty flag and
no elaborate debounce state machine.  A short delay can be added later only
if measurement shows that repeated discovery is materially expensive.

### Lock

A lock prevents two processes from modifying one resource at the same time.
It does not record whether a service is active and it does not replace an
actual-state check.

The existence of a lock file is not state.  `lockf(1)` associates the lock
with a live process/file descriptor; the lock is released if that process
exits, even if the path remains.

Runtime locks belong under a controlled runtime directory such as
`/var/run/omdrc/` because:

- they are not persistent configuration;
- `/var/run` is recreated or cleaned at boot;
- ownership and permissions can be fixed by the rc service;
- a private directory avoids the shared-namespace and symlink problems of
  `/tmp`.

No code may interpret the mere existence of `drc.lock` as "DRC is running".

## Why the current chain is too complicated

The current path is approximately:

```text
devd
  -> omdrc-hotplug       lock + dirty flag + settle/debounce loop
  -> omdrc_sndlink       second lock, role links and sound settings
  -> drc_usb_audio       active marker and presence policy
  -> brutefir_drc        privilege-changing rc wrapper
  -> drc.sh              third lock and actual chain transition
```

Each layer solves a real problem, but the responsibilities overlap:

| Component | Present purpose | Problem in the combined design |
|---|---|---|
| `devd` rule | Notify on PCM attach/detach | Must not wait for a slow audio rebuild |
| `omdrc-hotplug` | Detach from `devd`, debounce and serialize events | Adds a dirty-file protocol because downstream operations are not sufficiently idempotent |
| `omdrc_sndlink` | Discover card roles, update `/dev` links and apply sysctls/mixer settings | Has a second lock; its `$0` re-exec target is correct under `service(8)` but wrong when FreeBSD sources it from `/etc/rc` at boot (verified below) |
| `drc_usb_audio` | Convert DAC presence into start/stop and track an active marker | The marker can disagree with the processes and device nodes that actually exist |
| `brutefir_drc` | Run `drc.sh` as the audio user | Becomes another service boundary even though it owns no independent state |
| `drc.sh` | Build and tear down the real DRC chain | Its lock serializes work but is acquired too late and does not by itself coalesce duplicate rebuilds |

The main failure modes follow from this split:

- two callers can both observe an absent active marker before either creates
  it, then perform two serialized rebuilds;
- a failed stop can remove the active marker while the chain is still alive;
- a failed late MPD command can omit the marker even though BruteFIR and
  `virtual_oss` were already started;
- early hotplug can bypass normal rc ordering and try to start DRC before MPD;
- the current `omdrc_sndlink` self-exec uses `$0`; the verified boot-time path
  launches `/bin/sh /etc/rc oneupdate` and repeats the rc pass once;
- three different locks protect related portions of a single intended state
  transition without a single owner of that transition.

## Verified `$0` boot re-entry

This is a confirmed bug on this machine, not a speculative migration concern.
It is more precise to call it a **bounded one-level rc re-entry** than an
unbounded recursion.

The observation that all three re-exec sites receive an absolute `$0` under
`service(8)` is correct.  The important exception is that
`omdrc_sndlink` is not launched through `service(8)` during normal boot.

FreeBSD's `/etc/rc.subr` documents and implements the boot path at lines 1865
and 1883 as follows:

```sh
rc_service="$_file"
( set $_arg; . $_file )
```

The rc script is **sourced**, not executed.  Sourcing changes neither the
caller's `$0` nor the process image.  Therefore, during boot:

```text
$0         = /etc/rc
$rc_service = /usr/local/etc/rc.d/omdrc_sndlink
```

The current call path is:

```text
/etc/rc
  -> run_rc_script /usr/local/etc/rc.d/omdrc_sndlink start
  -> source omdrc_sndlink
  -> omdrc_sndlink_start
  -> omdrc_sndlink_update
  -> lockf ... /bin/sh "$0" oneupdate
  -> /bin/sh /etc/rc oneupdate
```

The second `/etc/rc` inherits `OMDRC_SNDLINK_LOCKED=1`.  When its own pass
reaches `omdrc_sndlink`, the script skips the re-exec block.  That prevents an
infinite loop, but it still runs almost the entire boot sequence a second
time.

The two `$0` sites at `libexec/omdrc-hotplug:97,110` are different and are not
implicated.  The devd rule executes the helper as the absolute path
`/usr/local/libexec/omdrc-hotplug` through `daemon(8)`; the helper is not
sourced by `/etc/rc`, so its `$0` is the intended helper path.

### Observed symptom

The installed `omdrc_sndlink` was byte-for-byte identical to the repository
version when checked.  The boot beginning on 24 August 2026 at 08:54:58 again
showed the two passes in `dmesg -a`:

```text
first pass:   Setting hostuuid
              Starting wpa_supplicant
              Starting devd

second pass:  Setting hostuuid
              Starting wpa_supplicant
              Starting devd
              devd: Can't open devctl device /dev/devctl: Device busy
```

Later in the same boot, routes were added twice and the second attempts to
start `syslogd`, D-Bus, Music Player Daemon, cron, `omdrcctrl`, `omdrcvideo`,
and SDDM reported already-running processes, existing PID files, occupied
sockets, or duplicate routes.  Two `wpa_supplicant` instances (PIDs 514 and
3075 in the log) then competed while `iwm0` emitted a long device-timeout
storm.  This repeated boot-service sequence is the observed symptom that led
to inspection of the `$0` path.

The immediate correction is to re-execute the service path maintained by
`rc.subr`, not the shell's process name:

```sh
/bin/sh "${rc_service}" oneupdate
```

Using `${rc_service:-$0}` is a defensive fallback, but `rc_service` is set by
`rc.subr` for both direct/service invocation and the boot sourcing path.  The
same correction must be made in the port template.  It remains Phase 0 because
the current boot has reproduced the failure; if it had not been verified, it
would belong in investigation rather than incident containment.

## First decision: is the DAC really the only PCM device?

Disabling `omdrc_cdin` is not enough to establish this.  If the ESI capture
interface remains physically attached, FreeBSD still enumerates it.  Built-in
audio, HDMI audio, USB headsets, and other USB audio devices also count.

There are two valid target designs.

### Design A: exactly one stable PCM device

Use this only if all of the following are true:

- the playback DAC is the only PCM device that can be present;
- it reliably appears as the configured OSS device, normally `/dev/dsp0`;
- there is no capture or CD-input consumer;
- adding another sound device is outside the appliance's supported
  configuration.

Then remove role discovery and use the OSS device directly:

```text
devd PCM notification
        |
        v
daemon -f service omdrc_audio reconcile
        |
        v
omdrc_audio drops to the login audio user
        |
        v
drc.sh reconcile
```

In this design:

- MPD direct output uses `/dev/dsp0`;
- DRC input/output configuration uses the known DAC device;
- `/dev/dsp.dac`, `/dev/dsp.capture`, and the mixer-role links are
  unnecessary;
- fixed sound settings can be applied once at boot through supported sysctls
  or a very small rc setup action;
- `drc.sh` owns the only mutable state machine and the only operational lock.

This is the preferred design if the hardware assumption can be guaranteed.

### Design B: several PCM devices may exist

If another PCM device may enumerate, `/dev/dsp0` cannot mean "the DAC".  The
unit number may change with attachment order.  Keep a small root-owned device
resolver:

```text
devd PCM notification
        |
        v
daemon -f service omdrc_audio reconcile
        |
        v
omdrc_audio (root rc service)
  - identify the DAC by USB ID/serial
  - update or remove /dev/dsp.dac
  - apply the DAC's sound settings
  - release the short device lock
  - enter the audio user's login environment with su -l
        |
        v
drc.sh reconcile as the audio user
```

The resolver is necessary because `/dev` links and system sound settings are
root-owned.  A user-owned `drc.sh` should not silently acquire root authority.
The resolver contains no DRC active marker and no audio-chain state machine.

With no CD input, it does not create `/dev/dsp.capture`, select a recording
source, or contain any capture-card policy.

## Target responsibilities

### `devd`

`devd` provides notification only.

- Match sound/PCM attachment and detachment, not generic USB device events.
- Never match a keyboard or unrelated USB device.
- Launch the reconciler through `daemon(8)` or an equivalent detached
  mechanism so `devd` immediately resumes processing the global device-event
  stream.
- Do not run `service` chains or perform waits inside the rule itself.
- Treat every event as a hint to inspect the present state.

### `omdrc_audio` rc service

`omdrc_audio` is the named FreeBSD lifecycle owner and the master
administrative switch:

```text
omdrc_audio_enable="YES|NO"
omdrc_audio_user="giacomo"
```

It **absorbs**, rather than discards, two existing services:

- from `drc_usb_audio`: the rc.conf master switch, boot entry point, hotplug
  policy entry point, stop/status verbs, and the rule that disabling the rcvar
  disables automatic DRC operation;
- from `brutefir_drc`: `su -l` privilege reduction, the login `HOME` and
  `PATH`, and the invariant that BruteFIR is owned by the same audio user who
  runs interactive `drc.sh` commands.

The login environment is functional, not cosmetic.  Dropping `su -l` can
make `brutefir`, `mpc`, and `virtual_oss` disappear from `PATH`, select the
wrong `HOME`, and create a root-owned BruteFIR process that an interactive
`drc.sh off` cannot kill.

The intended rc verbs are:

| Verb | Meaning |
|---|---|
| `start` | Boot reconciliation; obey `omdrc_audio_enable` |
| `reconcile` | Boot/devd/manual level check; obey `omdrc_audio_enable` |
| `stop` | Transient chain teardown; preserve saved user power preference |
| `status` | Report role assignment and actual DRC-chain health |

In Design A its device-role step is absent.  In Design B it also owns one
short root-level transaction:

1. enumerate current PCM devices;
2. identify the configured DAC by stable hardware identity;
3. atomically replace `/dev/dsp.dac` and any required mixer link, or remove
   stale links if the DAC is absent;
4. apply only DAC-specific sysctls/mixer settings;
5. release its device lock;
6. leave the device lock;
7. invoke `drc.sh reconcile` through `su -l` as `omdrc_audio_user`.

It must not:

- use an active marker;
- start or stop BruteFIR directly;
- hold its device lock while waiting for the DRC lock;
- call its own rc script through `$0`;
- keep a dirty flag or event queue.

The role operation is needed at boot and on attach/detach even while DRC is
off, because direct MPD output still needs a valid DAC path.  It therefore
cannot exist only inside `drc.sh start` unless Design A removes the role link
entirely.

### `drc.sh`

`drc.sh` becomes the sole owner of desired and actual DRC state.

Its public mutating commands include:

- `on`: record the desired state as on, then reconcile;
- `off`: record the desired state as off, then reconcile;
- `restore`: load the saved preference, then reconcile;
- `reconcile`: make actual state match the already-recorded preference;
- existing rate/design/geometry changes: update the requested configuration,
  then reconcile.

Every mutating command acquires the same lock before reading or writing state
that participates in the transition.  In particular, `off` records
`last_power=off` immediately after acquiring the lock and before attempting
any teardown.  A failure must not leave the saved intent as "on".

Read-only commands such as status and lists need not take the transition lock
unless they require a coherent multi-file snapshot.

### Absorbed and removed components

After migration:

- absorb `drc_usb_audio`'s rcvar and lifecycle verbs into `omdrc_audio`, then
  remove `drc_usb_audio` and `/var/run/drc_usb_audio.active`;
- remove the state-machine portions of `omdrc-hotplug`;
- remove `omdrc_sndlink` in Design A;
- merge `omdrc_sndlink`'s reduced DAC-only role logic into `omdrc_audio` in
  Design B;
- absorb `brutefir_drc`'s `su -l`, login environment, user ownership, and
  start/stop/status dispatch into `omdrc_audio`, then remove the old wrapper;
- keep controller, video, renderer, and any actual long-running daemon rc
  scripts separate because they own independent processes.

During package upgrade, retain compatibility aliases long enough to translate
or warn about `drc_usb_audio_enable`, `brutefir_drc_user`, and
`omdrc_sndlink_*`.  The final documented keys are `omdrc_audio_enable`,
`omdrc_audio_user`, and, only for Design B, `omdrc_audio_dac` plus the
DAC-specific sound settings.  No master switch disappears unnamed.

## Desired-state and actual-state model

The reconciler must not use one marker file as a proxy for the whole chain.

Inputs describing **desired state** include:

- saved DRC power preference (`on` or `off`);
- selected sample rate;
- selected filter design and geometry;
- administrative enable/disable configuration.

Inputs describing **actual state** include:

- whether the DAC currently exists;
- whether the expected `virtual_oss` instance is alive;
- whether the expected BruteFIR instance is alive;
- whether `/dev/dsp.play` and other DRC endpoints exist and point to the
  intended live devices;
- whether the running configuration/rate matches the requested one;
- whether MPD routing has been updated, when MPD is available.

The core decision table is:

| DAC | Desired power | Actual chain | Action |
|---|---|---|---|
| absent | either | running or partial | Stop/clean the chain; preserve the saved user preference |
| absent | either | stopped | No-op; wait for a later attach notification |
| present | off | running or partial | Stop/clean the chain and ensure direct-DAC routing |
| present | off | stopped/direct | No-op |
| present | on | correct and healthy | No-op |
| present | on | stopped, partial, or wrong configuration | Construct or repair once |

The saved power preference must survive a temporary DAC detach.  Detachment
means "hardware unavailable", not "the user requested DRC off".

## MPD tolerance, not an MPD readiness gate

The physical DRC chain must not depend on MPD becoming ready.  Making
`reconcile` wait for MPD would replace the current race with a new boot-order
dependency and would prevent a healthy BruteFIR/`virtual_oss` chain from
starting merely because MPD is slow or temporarily broken.

The target order inside one reconciliation is:

1. resolve the DAC device path;
2. construct or repair `virtual_oss` and BruteFIR when desired;
3. verify the physical chain;
4. attempt the MPD output switch last, with a hard timeout;
5. if MPD is unavailable, keep the verified physical chain, report
   `mpd_pending`; the successful-start hook loaded from
   `/usr/local/etc/rc.conf.d/musicpd/omdrc_audio` issues exactly one later
   reconcile when MPD is factual rather than guessing readiness with a sleep.

FreeBSD permits `rc.conf.d/musicpd` to be either a single file or a directory.
Installers must prepare the directory form before adding this independent
fragment. A pre-existing file is moved without rewriting it to
`musicpd/00-local.conf`; CMake, the direct Make install, and the package
pre-install script all implement that idempotent migration.

All bare `mpc` calls in lifecycle paths must go through one bounded helper,
including read-only `status` and `current` calls.  A representative interface
is:

```sh
mpc_bounded()
{
    /bin/timeout -k 1 "${OMDRC_MPC_TIMEOUT:-2}" mpc "$@"
}
```

The exact duration is configurable and tested, but it is never infinite.  A
timeout is logged distinctly from "MPD rejected the output name".  MPD
routing failures are non-fatal once the physical chain has been verified;
process/device construction failures remain fatal and roll back safely.

The MPD configuration uses `restore_paused "yes"`, which makes late output
selection safe during boot: bringing up the physical chain does not itself
resume playback.  Normal rc ordering may still place `omdrc_audio` after MPD
as a useful scheduling preference, but correctness must not rely on that
order and an early devd reconcile is allowed to construct the chain.

Any bounded retry of the late MPD switch must have a total deadline.  It must
not turn one `mpc` timeout into an indefinite retry loop while `drc.lock` is
held.

The implemented retry is event-driven instead of timed. FreeBSD `rc.subr`
sources the project fragment while loading `musicpd` and runs its
`start_postcmd` only after a successful daemon start. The hook calls
`service omdrc_audio reconcile` synchronously; that path already has finite
device-lock, DRC-lock, and `mpc` deadlines. Failure is warned but deliberately
does not make an already-running MPD service fail. The direction is only
`musicpd -> omdrc_audio`: no audio path starts or restarts MPD, so this is not
a service cycle. The fragment owns no lock and needs no sleep, debounce,
background retry process, or boot-order dependency.

An rc script may re-enter through `$0` only if it is guaranteed to have been
executed as a program.  A script that FreeBSD can source through
`run_rc_script` must use `rc_service` as its own path.  Until
`omdrc_sndlink` is removed, its immediate boot re-entry fix is therefore to
replace the target at `etc/rc.d/omdrc_sndlink:498` with
`${rc_service:-$0}`.

## Bounded work while holding `drc.lock`

Increasing the number of reconcile calls before bounding external commands
would make the remaining lock-stall risk worse.  Deadline work therefore
precedes the reconcile rollout.

The audit identified bare `mpc` calls in status, rollback, off, rebuild, and
final output-selection paths.  Every one receives the common timeout above.
The same audit must cover other commands executed under `drc.lock`, especially
`sudo`, `service omdrc_cdin`, BruteFIR startup verification, and
`virtual_oss` shutdown.  Each operation must be one of:

- an in-process, predictably fast state/file operation;
- a subprocess with a hard deadline and defined failure result;
- a poll with a fixed deadline and sleep interval.

No password prompt is permitted in a service transition.  Required `sudo`
operations must use non-interactive mode and fail immediately if the NOPASSWD
grant is missing.

### CD-input release acknowledgement

The current `release_cdin` sends SIGHUP and sleeps for 1.5 seconds.  The sleep
is finite, but it is blind: it neither returns early when the descriptor has
closed nor proves that `/dev/dsp.play` was released before `virtual_oss`
teardown.  Proceeding while the CUSE client descriptor remains open is the
dangerous case.

Replace the blind delay with a bounded acknowledgement:

1. signal the specific `omdrc-cdin` PID;
2. poll kernel-visible file descriptors (or an explicit daemon acknowledgement)
   for release of `/dev/dsp.play`;
3. return immediately when it is closed;
4. fail after a configured short deadline;
5. on failure, abort the `virtual_oss` teardown rather than risk the known CUSE
   teardown deadlock.

The subsequent `service omdrc_cdin onerestart` must also have a hard deadline.
It stays non-fatal after the chain is healthy, but a timeout is reported as an
incomplete CD-input handoff rather than hidden.

### CD input rate policy and UI action

The CD/S-PDIF source is fixed at 44.1 kHz, while the normal DRC state may be
192 kHz.  Reconcile will **not** infer a rate request merely because
`omdrc-cdin` is running or sees a carrier.  A background input must not
silently tear down music playback and rebuild the chain at another rate.

When CD input is supported, the UI exposes an explicit **CD input (44.1 kHz)**
action.  That action:

1. requests `drc.sh 44100`, thereby recording 44.1 kHz as desired state;
2. waits for the bounded, verified reconcile result;
3. starts or restarts `omdrc-cdin` only after the 44.1 kHz playback endpoint
   exists;
4. reports failure instead of starting the bridge against a 192 kHz endpoint.

Stopping CD input does not guess which previous rate the user wants.  The
ordinary DRC rate/resampling controls remain the explicit way to leave CD
mode.  If CD input is disabled, all of this disappears from the active
lifecycle and no capture role is created.

## Locks in the target design

There is no "devd lock" in the target design.  `devd` is the notifier for the
whole machine and must return immediately.  The optional root lock protects
the **device-role transaction** performed by an independently running helper.
The other lock protects the **DRC-chain transaction** in `drc.sh`.

### Concrete callers that can collide today

The device-role operation can currently be entered by:

- boot: `/etc/rc` runs `omdrc_sndlink start`;
- hotplug: a `pcmN` attach or detach causes `omdrc-hotplug` to run
  `service omdrc_sndlink update`;
- an administrator: `service omdrc_sndlink update`;
- two hotplug workers at once, for example when the ESI becomes `pcm0` and the
  OKTO becomes `pcm1` during the same boot.

The transaction changes several related objects:

```text
/dev/dsp.dac and /dev/mixer.dac
/dev/dsp.capture and /dev/mixer.capture (only when capture is configured)
hw.snd.default_unit
dev.pcm.N.bitperfect and play/record vchan settings
capture recording source
/var/run/omdrc_sndlink.roles
```

`hw.snd.default_unit` looks global, but its value is a pcm unit and therefore
has the same attach-order instability as every `dev.pcm.N.*` setting. It must
not be hard-coded in `/etc/sysctl.conf`, which runs before role discovery.
The implemented role transaction writes it from the resolved DAC role with
`/sbin/sysctl`, reads it back, logs a write/readback failure, and makes status
fail visibly on a mismatch. This keeps one dynamic owner and prevents bare
`/dev/dsp` clients outside the project from silently opening the capture card.

In Design B, imagine these two level-triggered workers without a device lock:

```text
worker A scans: OKTO is pcm1
worker B scans after a detach: no OKTO
worker B removes /dev/dsp.dac
worker A resumes and creates /dev/dsp.dac -> dsp1
```

The final link is stale even though the last observed hardware state was
"detached".  The device lock makes each worker perform its scan and all
resulting changes as one unit.  Because each queued worker scans *after* it
gets the lock, the last worker observes the current world instead of replaying
an old attach/detach edge.

The DRC-chain operation can currently be entered by:

- boot: `drc_usb_audio start` -> `brutefir_drc onestart` ->
  `drc.sh restore`;
- DAC attach: `omdrc-hotplug` follows the same service path;
- DAC detach or shutdown: the service path invokes `drc.sh stop`;
- a shell user: `./drc.sh off`, `./drc.sh 192000`, `./drc.sh resamp`,
  `./drc.sh geometry NAME`, or `./drc.sh design NAME`;
- the control panel: its No DRC, resampling, fixed-rate, geometry, and design
  actions invoke those same `drc.sh` commands;
- browser/video helper workflows that temporarily switch between direct DAC
  and DRC operation.

Those commands modify or depend on the same resources:

```text
BruteFIR process and its open DAC/loop descriptors
virtual_oss process and /dev/dsp.play + /dev/dsp.loop
MPD output selection
last_power, last_arg, last_geometry, and selected design
CD-input handoff while that feature still exists
```

A concrete collision is a DAC attach restoring 192 kHz at the same moment the
user presses **No DRC** in `omdrc-ctrl`:

```text
attach worker                    user action
-------------                    -----------
drc.sh restore                   drc.sh off
stop old BruteFIR                 stop BruteFIR
start virtual_oss                 remove /dev/dsp.loop
start BruteFIR                    enable direct MPD output
enable DRC MPD output             save last_power=off
```

Without serialization, either command can tear down what the other just
created.  Plausible outcomes include BruteFIR down with `virtual_oss` still
running, MPD pointed at a missing `/dev/dsp.play`, or saved `off` intent with
the chain actually running.

In the target design both commands take `drc.lock` and inspect desired and
actual state only after acquiring it:

```text
restore wins first: restore completes; off then stops it and records off
off wins first:     off records off; restore then sees off and is a no-op
```

Both orders end in a coherent state.  This is why the lock must cover the
saved intent as well as process and device changes.

### Design A

One lock:

```text
STATE_DIR/drc.lock
```

It protects every DRC-chain mutation and its participating saved state.

For example, all of the following take this same lock:

```sh
./drc.sh off                 # user or control-panel request
./drc.sh 192000              # rate/chain rebuild
./drc.sh geometry flat       # filter-set change and rebuild
./drc.sh reconcile           # boot or detached devd notification
```

`drc.sh status`, `drc.sh session`, and list operations do not change the
chain and ordinarily do not need it.

### Design B

Two locks:

```text
/var/run/omdrc/device.lock   root-owned, held only for role links/settings
STATE_DIR/drc.lock           audio-user-owned, held for DRC transitions
```

They are never nested:

```text
acquire device.lock
update roles/settings
release device.lock

acquire drc.lock
reinspect actual DRC state
perform required transition
release drc.lock
```

The second inspection after obtaining `drc.lock` is what makes simultaneous
devd, boot, and user requests safe.  The lock only serializes them; the
inspection makes later callers no-ops.

A complete Design B attach looks like this:

```text
devd receives pcm1 ATTACH
  -> daemon launches omdrc_audio reconcile; devd returns
  -> omdrc_audio takes device.lock
  -> it scans the current PCM set, creates /dev/dsp.dac -> dsp1,
     applies dev.pcm.1 settings, and publishes the role
  -> it releases device.lock
  -> it invokes drc.sh reconcile as the audio user
  -> drc.sh takes drc.lock and rechecks desired + actual DRC state
  -> it starts once or returns because another caller already did
```

The root helper never waits for `drc.lock` while holding `device.lock`.  A
direct user `drc.sh off` therefore cannot deadlock against a devd attachment.

### Source verdict: devd serialization does not remove these locks

FreeBSD 15.1's `sbin/devd/devd.cc` currently implements a direct action by
forking `/bin/sh -c command` and waiting for that exact child with `wait4()`.
Direct actions are therefore serialized in this implementation. The manuals
only say that devd executes the highest-priority matching action; they do not
promise a concurrency contract, so project correctness must not depend on the
implementation detail alone.

Our rule deliberately runs:

```text
/usr/sbin/daemon -f /usr/sbin/service omdrc_audio reconcile
```

`daemon` detaches and lets devd's small action child return. This is required:
a full audio reconcile can wait on device and DRC locks, MPD, BruteFIR,
virtual_oss/CUSE, and hardware verification; running that inline would stop
devd from handling every other device event. Once detached, two pcm events can
produce overlapping workers, and boot, the MPD post-start hook, an
administrator, and the UI are independent callers anyway.

The simplification options were audited and rejected:

* removing `daemon` would borrow devd's current serialization at the cost of
  blocking the machine-wide event loop, and would not serialize non-devd
  callers;
* dropping a busy event is unsafe because a detach can occur during another
  worker's scan and must receive a final current-state pass;
* a debounce/dirty-marker protocol would merely replace the short queued lock
  with more state and another worker lifecycle;
* combining both locks would hold a root device transaction across the much
  longer audio-user chain transition and recreate lock coupling.

The minimal safe result is therefore the implemented pair of non-nested locks.
`device.lock` protects one scan/link/sysctl/mixer/publication transaction;
`drc.lock` protects desired state plus BruteFIR/virtual_oss/MPD transition. The
MPD hook owns neither lock and reuses the same reconcile path.

## Attach, detach, and `off` behaviour

### DAC attachment

```text
devd returns immediately after launching detached work
  -> resolve the current DAC path if necessary
  -> acquire the DRC lock
  -> if desired power is on and the chain is absent, start once
  -> if desired power is off, retain direct-DAC mode
  -> otherwise do nothing
```

### DAC detachment

```text
devd returns immediately after launching detached work
  -> remove any stale DAC role link
  -> acquire the DRC lock
  -> stop/clean only the affected DRC chain
  -> retain the saved desired power state
```

Detach handling must never match or act on unrelated USB devices.  It must
not restart the entire USB subsystem, unload USB drivers, or manipulate
generic USB nodes.

### Explicit `off`

```text
acquire the DRC lock
  -> save desired power = off first
  -> inspect the actual chain
  -> stop only components that are present
  -> restore direct-DAC routing if the DAC is present
  -> verify the resulting state
release the lock
```

In Design A, the known DAC path requires no role refresh.  In Design B, the
root device path has already been maintained by boot/devd.  If a defensive
manual refresh is required, expose a root-level `omdrc-device reconcile`
entry point rather than making user-owned `drc.sh` modify `/dev` or sysctls.

## Migration plan

### Phase 0: make the installed system safe

1. Apply the confirmed `omdrc_sndlink` boot re-entry fix to the repository,
   port template, and installed script before the next reboot, then verify
   that `dmesg -a` contains only one rc pass.
2. Make `drc_usb_audio stop` retain its marker when teardown fails during the
   transition period.
3. Do not install or enable the old broad USB-interface devd rule.
4. Remove the unconditional base `snd.conf` action or configure it correctly
   so it does not repeatedly invoke `virtual_oss_cmd` with an empty device.

### Phase 1: establish the hardware assumption

1. Disable CD input and remove its playback/capture policy from the target
   design.
2. Boot and replug with the intended hardware set.
3. Record every `pcmN` device that can appear, including built-in and HDMI
   devices.
4. Select Design A only if the DAC device path is genuinely stable.
5. Otherwise select Design B and retain only DAC identity resolution.

### Phase 2: make `drc.sh` the state owner

1. Move its lock acquisition ahead of every mutating state read/write.
2. Put the lock in `/var/run/omdrc/` with explicit ownership and mode.
3. Add actual-state inspection.
4. Implement idempotent `reconcile`.
5. Route `on`, `off`, `restore`, rate, design, and geometry mutations through
   it.
6. Treat MPD unavailability according to the boot readiness policy rather
   than leaving a half-started chain represented as fully stopped.

### Phase 3: simplify device notification

1. Point the PCM devd rule at a tiny detached invocation.
2. For Design A, invoke `drc.sh reconcile` through the required user boundary.
3. For Design B, implement the reduced `omdrc-device reconcile`, release its
   lock, and then invoke `drc.sh reconcile` as the audio user.
4. Initially omit debounce and the dirty flag.
5. Verify that simultaneous notifications serialize and become no-ops after
   the first successful transition.

### Phase 4: remove compatibility layers

After the new path passes testing:

1. remove the `drc_usb_audio` active marker and rc service;
2. remove `brutefir_drc` as an independent service boundary;
3. remove the old `omdrc-hotplug` dirty/debounce protocol;
4. remove `omdrc_sndlink`, or replace it with the reduced Design B resolver;
5. remove obsolete rc.conf entries and document the remaining project-owned
   settings;
6. update installation and package manifests so deleted scripts cannot remain
   installed after an upgrade.

## Acceptance tests

The simplification is complete only when all of these pass.

### Boot

- One complete rc pass; no repeated host UUID, routing, devd, dbus, NTP,
  renderer, display manager, or cron startup.
- One `devd` process.
- At most one DRC construction.
- No early MPD failure causing repeated construction.
- Saved DRC-off state remains off after boot.
- Saved DRC-on state starts once when the DAC is available.

### Hotplug

- Attach the DAC once: correct device path and at most one chain start.
- Detach the DAC once: stale links removed and at most one chain stop.
- Rapid detach/attach: final state matches the currently attached hardware.
- Attach or detach a keyboard: no OMDRC action.
- Attach a non-DAC sound device in Design B: DAC link remains correct and no
  unnecessary DRC rebuild occurs.

### Concurrency

- Run at least 25 concurrent reconcile requests: one state transition and 24
  post-lock no-ops.
- Run `off` concurrently with a devd attach: saved `off` intent wins and the
  final chain is down/direct.
- Run a rate change concurrently with detach: no stale process or device link
  remains.
- Kill a reconciler while it holds a lock: the next invocation can acquire
  the lock; no stale-file interpretation blocks recovery.

### Failure recovery

- BruteFIR start failure is represented as incomplete actual state and is
  repairable by the next reconcile.
- `virtual_oss` start or stop failure does not produce a false active/inactive
  marker.
- MPD unavailable during early boot does not cause a rebuild loop.
- Removing the DAC while audio clients are open does not invoke generic USB
  reset, module unload, or unrelated device teardown.

## Expected final result

For a guaranteed one-card/no-CD-input appliance:

```text
one PCM-specific devd notification
  -> one user-context DRC reconciler
  -> one lock
  -> no role links, active marker, dirty flag, or nested rc services
```

For a machine where other PCM devices can appear:

```text
one PCM-specific devd notification
  -> one small root device resolver with one short lock
  -> one user-context DRC reconciler with one lock
  -> no active marker, dirty flag, or nested locks
```

In both cases, repeated events are harmless because each invocation observes
the current world and changes only what is wrong.
