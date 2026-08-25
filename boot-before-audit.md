# FreeBSD boot before the open-media-drc lifecycle migration

This is the frozen **before** record for the boot that began on 2026-08-24 at
08:54:58 CEST.  It was captured on 2026-08-24 at 19:27 CEST, before replacing
the installed legacy open-media-drc rc/devd stack.  Compare it with the next
boot; do not treat later observations appended to this file as part of this
baseline.

## Host and evidence window

```text
host:        bee
kernel:      FreeBSD 15.1-RELEASE-p2 releng/15.1-n283596-aadd58dddcbc GENERIC amd64
boot time:   Mon Aug 24 08:54:58 2026 CEST
capture:     Mon Aug 24 19:27:14 2026 CEST
uptime:      10:32 at capture
```

The principal evidence sources were `kern.boottime`, `last reboot`, `dmesg`,
`/var/log/messages`, `/var/log/daemon.log`, `rcorder`, `sysrc -a`, `ps`,
`sockstat`, `/dev/sndstat`, the installed scripts under `/usr/local`, and the
runtime files under `/var/run`.  The system log is incomplete after 09:14
because `/var/run/log` was unlinked while syslogd still held the old socket;
that defect is documented below.

## Executive before-state

The USB controller and the two USB audio devices did **not** repeatedly detach
and attach in the captured kernel log.  They enumerated once and reached stable
PCM units:

```text
ugen0.3: <ESI U24XL> at usbus0
uaudio0: <ESI U24XL ...> on usbus0
pcm0 on uaudio0
ugen0.5: <OKTO RESEARCH DAC8STEREO> at usbus0
uaudio1: <OKTO RESEARCH DAC8STEREO ...> on usbus0
pcm1 on uaudio1
```

At capture time the semantic links happened to be correct:

```text
/dev/dsp.capture   -> dsp0     ESI U24XL
/dev/mixer.capture -> mixer0
/dev/dsp.dac       -> dsp1     OKTO RESEARCH DAC8STEREO
/dev/mixer.dac     -> mixer1
hw.snd.default_unit=1
```

BruteFIR and `virtual_oss` were also running at 192 kHz.  Therefore the next
boot should not merely be judged by whether sound eventually works.  It must be
judged by whether it reaches that state once, without a second rc pass, without
duplicate network activity, without stale markers, and without competing devd
transactions.

## Issue 1: verified rc recursion trigger in the installed service

The installed `/usr/local/etc/rc.d/omdrc_sndlink` contains this lock re-entry:

```sh
OMDRC_SNDLINK_LOCKED=1 \
    /usr/bin/lockf -s -t 15 "${omdrc_sndlink_lockfile}" \
    /bin/sh "$0" oneupdate
```

This is a verified defect for the boot invocation, not a speculative `$0`
concern.  FreeBSD's `/etc/rc` sources rc scripts during the boot pass.  In that
context `$0` is `/etc/rc`, not the path of `omdrc_sndlink`.  The lock wrapper
therefore executes the equivalent of:

```text
/bin/sh /etc/rc oneupdate
```

That starts another rc pass.  The inherited `OMDRC_SNDLINK_LOCKED=1` makes the
observed recursion bounded to one extra pass, but the second pass is enough to
repeat network and service initialization and destabilize the boot.  The new
service uses `${rc_service:-$0}` and, more importantly, consolidates the old
stack so this re-entry cannot accidentally select `/etc/rc`.

### Correlated network evidence

The boot produced two concurrent supplicant streams in `daemon.log`, with PIDs
514 and 3075 repeatedly attempting the same association.  Representative
lines are:

```text
Aug 24 08:55:45 ... wpa_supplicant[514]:  wlan0: Trying to associate ...
Aug 24 08:55:45 ... wpa_supplicant[3075]: wlan0: Trying to associate ...
Aug 24 08:55:50 ... wpa_supplicant[514]:  wlan0: CTRL-EVENT-DISCONNECTED ...
Aug 24 08:55:50 ... wpa_supplicant[3075]: wlan0: CTRL-EVENT-DISCONNECTED ...
```

Both streams continued until the network was reset at about 09:14.  The kernel
record contains **143** `iwm0: device timeout` messages in this boot's `dmesg`,
each generally followed by:

```text
iwm0: dumping device error log
iwm0: errlog not found, skipping
```

This is the main measurable symptom to compare after reboot.  There should be
one `wpa_supplicant` for `wlan0` and no repeated seven-second timeout storm.
The new audio service must never execute or source `/etc/rc`.

## Issue 2: the installed lifecycle is still the legacy multi-service stack

Before migration these active files existed:

```text
/usr/local/etc/rc.d/brutefir_drc
/usr/local/etc/rc.d/drc_usb_audio
/usr/local/etc/rc.d/omdrc_sndlink
/usr/local/etc/devd/omdrc-sndlink.conf
/usr/local/libexec/omdrc-hotplug
```

The replacement files did not yet exist:

```text
/usr/local/etc/rc.d/omdrc_audio               MISSING
/usr/local/etc/devd/omdrc-audio.conf          MISSING
```

That legacy path is:

```text
devd -> omdrc-hotplug -> omdrc_sndlink -> drc_usb_audio
     -> brutefir_drc -> drc.sh
```

It distributes one desired state across three locks, a hotplug debounce/dirty
protocol, an active marker, and multiple rc scripts.  It creates opportunities
for lock nesting, stale observations between steps, and attach/detach races.
The new design must leave only `omdrc_audio` and `omdrc-audio.conf` as the
FreeBSD audio lifecycle entry points.

## Issue 3: stale/non-authoritative DRC marker

The old marker was present from boot:

```text
-rw-r--r-- 1 root wheel 0 Aug 24 08:55 /var/run/drc_usb_audio.active
```

At capture time it happened to agree with the live chain, but it is only an rc
side effect and is not authoritative.  It can remain after `drc.sh off`, after
a failed teardown, or after a partial transition.  The legacy status output
was simply:

```text
drc_usb_audio active
```

The migration must remove both the marker and the service.  Desired power is
owned by `last_power`; desired source is owned by `last_source`; actual status
is derived from the running processes and their geometry.

## Issue 4: split locking and temporary-directory hazard

The before stack had three independent lock domains:

```text
/var/run/omdrc-hotplug.lock
/var/run/omdrc_sndlink.lock
${TMPDIR:-/tmp}/drc.lock
```

The first two could be nested by the devd path, and `drc.sh` could then wait on
its third lock while executing external commands.  The `TMPDIR` expression is
also unsafe across invocation contexts: a future per-session `TMPDIR` would
silently give interactive, root, and `su -l` callers different DRC locks.

The post-migration invariant must be exactly two **non-nested** locks:

```text
/var/run/omdrc/device.lock       root-only role/link/sysctl transaction
<persistent state>/drc.lock     user-owned DRC process/state transition
```

The root transaction must release its lock before `su -l giacomo` invokes
`drc.sh reconcile`.  Both FreeBSD `lockf` sites use `-k -s` for stable waiter
ordering.  No `mpc`, service restart, sleep, or other potentially blocking
operation belongs inside the root device lock.

## Issue 5: unbounded waits in the old DRC critical section

The audited pre-migration `drc.sh` had 18 bare `mpc` calls without a timeout.
The CD-input release path also used a blind 1.5-second sleep while the DRC lock
was held.  Either could stall a transition and force other callers to queue or
time out.  Running reconcile more frequently without fixing these waits would
make that defect more visible.

The post-migration checks must show that every lifecycle `mpc` call is bounded,
MPD being slow does not prevent the audio chain from starting, output switching
is attempted late, and CD-input release waits for an explicit daemon state/log
acknowledgement with a deadline.  A timeout must not blindly destroy CUSE state.

## Issue 6: rcorder has two unresolved requirements

The installed service headers produced:

```text
rcorder: requirement `mpd' in file `/usr/local/etc/rc.d/upmpdcli' has no providers.
rcorder: requirement `oss' in file `/usr/local/etc/rc.d/musicpd' has no providers.
```

The new installed headers must instead order `upmpdcli` after `musicpd`, remove
the nonexistent `oss` requirement, and produce no unknown requirement or cycle.

## Issue 7: syslog socket pathname vanished

At capture time:

```text
ls: /var/run/log: No such file or directory
srw------- 1 root wheel ... /var/run/logpriv
```

`syslogd` PIDs 3894, 3897, and 3898 were still alive.  `sockstat` showed PID
3894 holding the now-unlinked `/var/run/log` socket and devd still connected to
that old socket object.  New clients cannot connect by pathname, and
`/var/log/messages` stopped at approximately 09:14 although the host continued
running for hours.  This makes absence of later legacy hotplug log messages
non-evidence.

The repository audit found no project command that broadly deletes `/var/run`,
so the unlink cause remains separate and unexplained.  The migration must not
depend on syslog for correctness: role state is atomically published in
`/var/run/omdrc/audio.roles`, while durable DRC state/logging remains in the
user-owned state directory.  Before reboot, restarting syslogd should recreate
the pathname; after reboot it must exist normally.

## Issue 8: installed rc.conf still names the old services

Relevant before-state keys were:

```text
brutefir_drc_user="giacomo"
drc_usb_audio_enable="YES"
omdrc_sndlink_enable="YES"
omdrc_sndlink_dac="0x152a:0x88c5:000483"
omdrc_sndlink_capture="0x0a92:0x00d1"
omdrc_sndlink_dac_sysctls="bitperfect=1 play.vchans=0 mixer.vol_0.val=0"
omdrc_sndlink_capture_sysctls="bitperfect=1 rec.vchans=0"
omdrc_sndlink_capture_recsrc="auto"
kld_list="fusefs i915kms"
```

`omdrc_cdin_enable` was and remains `NO`; the migration must not enable it on
the user's behalf.  The new master switch and role keys must all use the
underscore rc name `omdrc_audio_*`.  `cuse` should be added to `kld_list`
because `drc.sh` starts `virtual_oss` directly and cannot rely on an unrelated
rc service to load its required kernel module.

## Issue 9: source intent was not yet persisted

The live repo state at capture was:

```text
last_power: on
last_arg:   192000
last_source: MISSING

geometry=flat
power=on
source=music
mode=192000
rate=192000
```

`source=music` was therefore a compatibility default, not an explicitly saved
choice.  The new UI/CD action must write `last_source=cdin` before requesting
44.1 kHz.  Music/rate actions must write `last_source=music`.  `restore` and
`reconcile` must read both under the pinned DRC lock, so a reboot restores CD
input at 44.1 kHz or music at the selected rate.  MPD may be late or absent;
that must defer only output switching, not the audio chain.

## Issue 10: other current-boot failures, not attributed to open-media-drc

These are real before-state issues and must remain visible in the comparison,
but the available evidence does not connect them to the audio scripts:

* The root filesystem logged `WARNING: R/W mount denied. Filesystem is not
  clean - run fsck` at 08:55:53.  This needs filesystem maintenance independent
  of the lifecycle migration.
* `packagekitd` exited on signal 11 at 08:56:17 and again at 09:00:56.
* The kernel warned that `snd_uaudio.ko` was newer than `linker.hints`.
* `iwm8000Cfw` initially reported firmware load error 8.  The extraordinary
  repeated timeout storm is correlated with duplicate network initialization,
  but the initial firmware warning itself is outside this project.
* `omdrcctrl` and `omdrcvideo` processes existed, while their non-root `service
  ... status` probes reported "not running".  That status/pidfile discrepancy
  is recorded but is not part of the USB-audio lock redesign.

## Stable device/process baseline at capture

```text
pcm0: <ESI U24XL> (play/rec)
pcm1: <OKTO RESEARCH DAC8STEREO> (play/rec) default
dsp.play: <virtual_oss device> (play/rec)
dsp.loop: <virtual_oss device> (play/rec)

musicpd:    PID 4126, user giacomo
brutefir:   PID 4552, user giacomo, flat 192000 configuration
virtual_oss PID 4549, user root, 192000 Hz
devd:       PID 2551
```

The privilege boundary is intentional and must survive the migration:
BruteFIR runs as `giacomo`, while `virtual_oss` runs as root.  The consolidated
service must use `su -l giacomo` so `HOME` and `PATH` are correct and an
interactive `drc.sh` owned by the same user can stop BruteFIR.

## Acceptance checklist for the next boot

1. `/var/run/log` exists and new messages continue to arrive after boot.
2. Exactly one rc pass is observed; no `/bin/sh /etc/rc oneupdate` or duplicate
   network/service startup occurs.
3. There is one `wpa_supplicant` for `wlan0`; the `iwm0` timeout storm is gone.
4. `rcorder` reports no missing `mpd` or `oss` provider and no cycle.
5. Only `omdrc_audio` owns the FreeBSD audio lifecycle.  The three old rc
   scripts, old devd config, and hotplug helper are absent from active paths.
6. `/var/run/drc_usb_audio.active`, `/var/run/omdrc_sndlink.roles`, and the old
   lock files are absent.
7. `/var/run/omdrc/device.lock` and `audio.roles` are the only root audio
   coordination artifacts; the DRC lock has one fixed user-state path.
8. The ESI and OKTO devices resolve by stable USB identity even if `pcm0` and
   `pcm1` swap; all four semantic links point to the matching units.
9. Concurrent pcm attach/detach events serialize one complete role transaction
   each and do not start/stop the chain recursively.
10. `omdrc_audio reconcile` restores `last_power`, `last_source`, geometry, and
    rate.  A saved CD source restores at 44.1 kHz; a saved music source restores
    its selected rate.
11. MPD delay or failure is bounded and does not block chain startup.
12. `drc.sh off` persists `last_power=off` before teardown, so a teardown error
    cannot cause DRC to return unexpectedly at the next boot.
13. No new USB controller reset, broad USB detach storm, conflicting symlink
    writer, or non-symlink overwrite appears.
14. Separately check whether the unclean-filesystem, PackageKit, and initial
    wireless firmware warnings remain; do not claim the audio migration fixed
    them unless the post-boot evidence supports that conclusion.
