# FreeBSD boot after the open-media-drc lifecycle migration

This is the comparison record for the boot that began on 2026-08-25 at
08:34:35 CEST.  It is compared with `boot-before-audit.md`, whose baseline boot
began on 2026-08-24 at 08:54:58.  Evidence was collected during the first
minutes of the new boot and again after unrelated USB storage hotplug.

## Host and evidence window

```text
host:        bee
kernel:      FreeBSD 15.1-RELEASE-p2 releng/15.1-n283596-aadd58dddcbc GENERIC amd64
boot time:   Tue Aug 25 08:34:35 2026 CEST
```

Evidence came from `dmesg -a`, `/var/log/messages`, `/var/log/daemon.log`,
`/var/log/bsdisks.log`, `rcorder`, `sysrc -a`, `ps`, `procstat`, `sockstat`,
`/dev/sndstat`, `usbconfig dump_stats`, the installed scripts, runtime role
state, DRC state/log files, and the live MPD outputs.

## Verdict

The project-related failure has been corrected in this boot.  There is no rc
recursion, duplicate wireless supplicant, audio transition loop, stale legacy
marker, conflicting role-link writer, lock failure, USB-audio failure, or MPD
output race.  The chain was built once at 192 kHz; the additional boot, pcm
and MPD-start reconciles observed the finished level state and exited as
no-ops.

Not every host warning is gone.  The absent late-mounted NTFS disk, dirty Linux
ext partitions probed by `bsdisks`, the initial Intel firmware lookup warning,
the stale `linker.hints` warning, low root-filesystem free space, and the
initial non-root `omdrcctrl`/`omdrcvideo` status-path mismatch are independent
items described below.  The status-path mismatch was subsequently corrected
without restarting either service.

## Before/after acceptance results

| Check | Before | This boot | Result |
|---|---|---|---|
| syslog pathname | `/var/run/log` was missing | `/var/run/log` and `logpriv` exist; messages continued after boot | PASS |
| rc recursion | `/bin/sh /etc/rc oneupdate` caused a second pass | no `/etc/rc` or `oneupdate`; one devd, syslogd startup, dhclient, late mount and musicpd startup | PASS |
| wireless | two `wpa_supplicant` streams; 143 `iwm0: device timeout` lines | exactly one wlan0 supplicant; zero device timeouts | PASS |
| rcorder | missing `mpd` and `oss` providers | no missing provider and no cycle; `devd` 60, `omdrc_audio` 71, `musicpd` 186, `upmpdcli` 192 | PASS |
| lifecycle owners | three legacy rc scripts plus hotplug helper and old devd files | only `omdrc_audio` plus `omdrc-audio.conf` own the audio lifecycle | PASS |
| legacy runtime state | active marker, old roles and old locks | all absent | PASS |
| current coordination | three possibly nested locks | root `device.lock` and fixed repo-state `drc.lock`; no holder remained after convergence | PASS |
| card roles | correct only as an eventual before-state | ESI resolved as pcm0/capture and OKTO as pcm1/DAC from USB identity | PASS |
| semantic links | potential competing writers | all four links are root-owned symlinks: capture to unit 0, DAC to unit 1 | PASS |
| dynamic default | could be overwritten by a fixed sysctl | `hw.snd.default_unit=1`, equal to the discovered DAC role | PASS |
| saved intent | source was not explicitly persisted | `last_power=on`, `last_source=music`, `last_arg=192000`, geometry `flat`; boot restored that selection | PASS for saved music state |
| MPD availability | could delay or abort the physical chain | physical chain completed with MPD pending; musicpd post-start reconcile selected only `DRC-native` | PASS |
| USB audio | no storm, but legacy scripts made correctness uncertain | both cards enumerated once; no audio detach/reset/error; all USB transfer failure counters zero | PASS |
| off ordering | intent used to be written after teardown | code writes `last_power=off` before teardown and tests cover failure; this boot did not exercise an `off` restore | CODE/TEST PASS |

`dmesg` prints `Starting Network` twice, but this is not another rc pass.  The
first line configures `lo0 em0 wlan0`; after devd starts, the second line names
only `em0`.  All whole-boot sentinels occur once, and there is only one
supplicant, devd and musicpd.  The route messages saying that the loopback
routes already exist likewise occur in the ordinary routing portion of the one
boot and are not accompanied by repeated service startup.

## Audio convergence evidence

The durable DRC log for this boot is:

```text
08:34:57 reconcile rebuild: physical mismatch, request 192000, source music
08:34:59 BruteFIR start attempt 1: ok
08:34:59 verification: observed 192004, wanted 192000, ok
08:35:00 physical chain active; MPD output pending
08:35:01 reconcile no-op: physical match, MPD pending
08:35:01 reconcile no-op: physical match, MPD pending
08:35:02 musicpd post-start reconcile no-op: physical match, MPD ok
```

There is one `virtual_oss`, one BruteFIR owned by `giacomo`, and one musicpd.
BruteFIR has `/dev/dsp.loop` open for input and the real `/dev/dsp1` OKTO unit
open for output.  Its live feedback is 192004 Hz for a requested 192000 Hz.
MPD has only `DRC-native` enabled; `OKTO-DAC`, `DRC-resamp`, and the spectrum
output are disabled.

The two pcm attach events and the rc start caused several reconcile requests,
which is expected with detached devd workers.  Only the first rebuilt the
chain.  Later callers passed through the two non-nested lock domains, rescanned
level state, and became no-ops.  No lock timeout or transition failure appears.

An unrelated mouse detach and USB-storage attach later in the boot produced no
new DRC log entry.  This confirms that the devd rule's `pcm[0-9]+` boundary does
not react to arbitrary USB devices.

## USB state

```text
pcm0  ESI U24XL                 /dev/dsp.capture -> dsp0
pcm1  OKTO DAC8STEREO           /dev/dsp.dac     -> dsp1  (default)
```

Role sysctls are correct: ESI `bitperfect=1`, `rec.vchans=0`, digital `pcm2`
record source; OKTO `bitperfect=1`, `play.vchans=0`.  Root-read USB statistics
after the chain had been streaming showed:

```text
ESI:  CONTROL_FAIL=0 ISOCHRONOUS_FAIL=0 BULK_FAIL=0 INTERRUPT_FAIL=0
OKTO: CONTROL_FAIL=0 ISOCHRONOUS_FAIL=0 BULK_FAIL=0 INTERRUPT_FAIL=0
```

The OKTO had completed more than 614,000 successful isochronous transfers at
capture time.  There is no controller reset, audio-device detach, transfer
failure, or broad USB disturbance attributable to open-media-drc.

## Remaining non-audio issues

### Missing optional NTFS disk

The late mount prints:

```text
ntfs-3g: Failed to access volume '/dev/ntfs/USBHD2': No such file or directory
```

`/etc/fstab` contains a `late,failok` entry for that external disk.  The label
was absent at boot, so the attempted mount failed and boot continued.  This is
not emitted by any project script.  Use `noauto` if absence should be silent,
or ensure the labelled disk is present before the late-mount phase.

### Dirty Linux ext partitions, not the FreeBSD root

Four messages say:

```text
WARNING: R/W mount denied. Filesystem is not clean - run fsck
```

The baseline called this a root-filesystem warning, but that attribution is
incorrect.  The exact text comes from `sys/fs/ext2fs/ext2_vfsops.c`; the UFS
root explicitly reported `FILE SYSTEM CLEAN; SKIPPING CHECKS`.  At the same
08:35:08 timestamp, `bsdisks` started and probed the internal Linux partitions
`ada0s1` and `ada0s2`.  This is strong temporal and source-level evidence that
the warnings are its read-write ext probes of dirty Linux filesystems.  Run
Linux `e2fsck` on those partitions while they are unmounted, or configure the
desktop storage layer not to attempt read-write probing.  Do not run a Linux
ext repair tool on the mounted FreeBSD root.

### Other host warnings

* `iwm8000Cfw: could not load firmware image, error 8` still appears once, but
  the driver subsequently loads firmware and associates; the former timeout
  storm is gone.
* `snd_uaudio.ko` is still newer than `linker.hints`; rebuild the hints with the
  normal kernel/module maintenance procedure.  The driver nevertheless loaded
  and both devices operate without transfer failures.
* No PackageKit signal-11 exit or crash dump appeared in this boot.  Its
  previous crash is not reproduced.
* At initial capture, non-root `service omdrcctrl status` and
  `service omdrcvideo status` selected per-user `/tmp` pidfiles and reported
  false negatives.  The rc scripts were subsequently changed to use only the
  canonical `/var/run/<service>/<service>.pid`, independent of caller UID.
  Both non-root and root probes now report supervisors 3518 and 3503, and a
  non-root `onestart` correctly refuses a duplicate.  The running processes
  were not restarted.
* `/` remains 96% used, with less than 1 GiB available.  That is an operational
  stability risk independent of devd and USB audio.

## Limits of this boot test

This boot restored the saved **music** source, not CD input.  The code and
tests cover persistence of `last_source=cdin` at 44.1 kHz and the early
`last_power=off` write, but a future reboot deliberately started with CD input
selected is still the end-to-end boot test for that branch.  Likewise, startup
provided concurrent pcm attach requests, but no audio card was physically
unplugged during this evidence window; detach correctness is supported by the
level-reconcile implementation and concurrency tests rather than a destructive
live unplug test.
