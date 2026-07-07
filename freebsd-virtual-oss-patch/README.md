# virtual_oss / cuse(3) patches — runtime livelock + teardown deadlock

Two related bug clusters, both analysed live on this box:

1. **Runtime engine deadlock / "155% CPU" livelock** (the chain freezes
   mid-playback, virtual_oss spins, clients stuck in `cuse-cli`) — root cause
   and fix:
   [`ROOTCAUSE-settrigger-sync-engine-deadlock.md`](ROOTCAUSE-settrigger-sync-engine-deadlock.md),
   patch `virtual_oss-settrigger-sync-deadlock.patch` (**built + installed
   2026-07-06, pending post-reboot validation**). This livelock is what
   creates the client population that makes the teardown deadlock below fire.
2. **Teardown deadlock** (stopping virtual_oss wedges it `D<E`, pins
   `cuse.ko`, reboot required) — documented in
   [`../VIRTUAL_OSS_CUSE_DEADLOCK.md`](../VIRTUAL_OSS_CUSE_DEADLOCK.md)
(Bugzilla-ready text: `../VIRTUAL_OSS_CUSE_DEADLOCK.bugzilla.txt`):

> Stopping `virtual_oss` intermittently wedges it forever while exiting (state
> `D<E`, `MWCHAN W`, immune to `kill -9`), pins `cuse.ko`, and requires a reboot.
> The kernel `cuse_server_free()` busy-waits in `pause("W", hz)` for `pcs->refs`
> to drop to 1, but `virtual_oss` never destroys its DSP/loopback cuse devices on
> exit, so the client references are never released.

All paths are relative to `/usr/src` and apply with `-p1`.

## Patches

### 0. Runtime livelock fix — SETTRIGGER vs synchronized-loopback engine wait (userland)

- `virtual_oss-settrigger-sync-deadlock.patch` — four changes in
  `usr.sbin/virtual_oss` (`main.c`, `virtual_oss.c`, `int.h`):
  `SNDCTL_DSP_SETTRIGGER` honours the fd open mode; trigger/halt ioctls
  `atomic_wakeup()` the DSP engine; the synchronized wait loops re-check
  `tx_enabled`/`rx_enabled`; new `tx_written` latch so the engine never
  sleeps waiting for play data from a client that has never written.
  Full analysis:
  [`ROOTCAUSE-settrigger-sync-engine-deadlock.md`](ROOTCAUSE-settrigger-sync-engine-deadlock.md).

### 1. Teardown fix — `virtual_oss` destroys all its devices on exit (userland)

- `virtual_oss-teardown-int.h.patch` — adds `oss_dev` / `wav_dev`
  (`struct cuse_dev *`) to `struct virtual_profile` so the device handles are
  kept instead of discarded.
- `virtual_oss-teardown-main.c.patch` — stores each handle at
  `cuse_dev_create()` time, and adds `destroy_devices()`, called from `main()`
  *before* exit, which `cuse_dev_destroy()`s every DSP/WAV/loopback device.

  `cuse_dev_destroy()` runs `cuse_server_free_dev()` in the kernel, which marks
  each client of the device closing — so clients drop their server refs and the
  kernel's `cuse_server_free()` no longer waits forever. This addresses the
  trigger without any kernel change.

  Note: the worker threads block in `cuse_wait_and_process()` (a server-level
  `ioctl`/`GET_COMMAND` that is not a cancellation point and is not woken by
  device destruction), so `destroy_threads()` still uses `pthread_cancel()` —
  a clean `pthread_join()` would need a kernel-side wakeup. That fragility is
  not what causes the hang; the missing device destroy is. The thread cleanup
  can be hardened separately upstream.

### 2. The diagnostic — identify the residual kernel leak (kernel, non-behavioral)

- `cuse-teardown-diag.c.patch` — in `sys/fs/cuse/cuse.c` `cuse_server_free()`,
  log `pcs->refs` (and the server pid) every ~5 s while the `pause("W")` loop
  spins. Pure `printf`, no behavior change. If the userland fix above does not
  fully eliminate the hang, this prints the stuck refcount to the console/dmesg
  so the leak can be characterized without a debugger. Remove once the kernel
  refcount leak is understood/fixed.

## Apply

```sh
cd /usr/src
patch -p1 < /path/to/freebsd-virtual-oss-patch/virtual_oss-teardown-int.h.patch
patch -p1 < /path/to/freebsd-virtual-oss-patch/virtual_oss-teardown-main.c.patch
patch -p1 < /path/to/freebsd-virtual-oss-patch/cuse-teardown-diag.c.patch   # optional
```

Verified to apply cleanly (`patch --dry-run`) against
`releng/15.1-n283562-96841ea08dcf` base source.

## Build & install

Userland `virtual_oss` (the fix):
```sh
cd /usr/src/usr.sbin/virtual_oss
make
sudo make install            # installs /usr/sbin/virtual_oss
```
The edited sources pass `cc -fsyntax-only`. Restart `virtual_oss` (or the DRC
chain) to pick up the new binary — **after a reboot**, see below.

Kernel module `cuse.ko` (the diagnostic, optional):
```sh
cd /usr/src/sys/modules/cuse
make
sudo make install            # installs /boot/modules or /boot/kernel
```
The module must match the running kernel ABI (it does for the release source
above).

## IMPORTANT — reboot first

These patches are **not yet runtime-tested**: when they were written the box had
a live wedged `virtual_oss` (`D<E`) pinning `cuse.ko`, so a new `cuse.ko` cannot
be loaded and a fresh `virtual_oss` cannot recreate the devices until the
machine is rebooted. Reboot, then build/install, then verify.

## Verification plan (after reboot)

1. Reproduce the original bug deterministically to confirm it still happens on
   the stock binary: start the DRC chain (`drc.sh 48000`) with MPD attached,
   then `drc.sh off`; repeat / churn rate changes. Watch for
   `ps -o pid,stat,mwchan | grep virtual_oss` showing `D<E  W`.
2. Install the userland fix; repeat the same stress. Expect `virtual_oss` to
   always exit cleanly (no `D<E`, devices released, `cuse.ko` unloadable).
3. If a hang still occurs, install the diagnostic `cuse.ko`, reproduce, and read
   the `cuse: server pid N exit stuck: refs=K` lines from dmesg to pin the
   leaked ref — feed that into the kernel-side fix and the Bugzilla report.
