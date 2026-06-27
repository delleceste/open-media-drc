# virtual_oss / cuse(3): unkillable D-state hang on virtual_oss teardown (reboot required)

**Status:** draft bug report — FreeBSD base (`usr.sbin/virtual_oss` + `sys/fs/cuse`).
**Maintainer/author:** Hans Petter Selasky (`hps@FreeBSD.org`) authored both cuse(3)
and virtual_oss — file on FreeBSD Bugzilla and/or mail hps@ directly.

## Environment

- `FreeBSD bee 15.1-RELEASE FreeBSD 15.1-RELEASE releng/15.1-n283562-96841ea08dcf GENERIC amd64`
- `virtual_oss` from base: `/usr/sbin/virtual_oss` (src: `/usr/src/usr.sbin/virtual_oss`)
- `cuse.ko` from base: `/usr/src/sys/fs/cuse/cuse.c`
- Use case: a DRC audio chain — `virtual_oss` provides a loopback
  (`-f /dev/null ... -d dsp.play -L dsp.loop`); MPD opens `/dev/dsp.play`
  (client), BruteFIR opens `/dev/dsp.loop` (client) and writes the USB DAC.

## Summary

Stopping `virtual_oss` (SIGTERM or SIGKILL — and via any path: `drc.sh off`, a
sample-rate change that restarts it, or `killall virtual_oss`) **intermittently**
leaves the `virtual_oss` process wedged **forever** in an uninterruptible,
SIGKILL-immune state while exiting:

```
PID   STAT  MWCHAN  COMMAND
5992  D<E   W       virtual_oss ... -f /dev/null ... -d dsp.play -L dsp.loop
```

- `D` = uninterruptible kernel sleep, `<` = realtime prio (from `-i 8`),
  `E` = process is *exiting*, `MWCHAN = W`.
- `kill -TERM` and `kill -9` have **no effect** (the thread is past signal
  delivery, blocked in the kernel).
- `cuse.ko` is now pinned (`kldstat` refcount held by the zombie); `kldunload
  cuse` itself then hangs in `D` too, as does any new `virtual_oss` (it cannot
  recreate the still-registered `dsp.play`/`dsp.loop` cuse devices:
  `virtual_oss: Could not create CUSE DSP device`).
- **Only a reboot recovers the machine.** This makes every virtual_oss
  stop/restart (i.e. every DRC rate change) a reboot risk.

## Where it is wedged (kernel)

`MWCHAN = W` pinpoints `cuse_server_free()`, the cdevpriv destructor for the
daemon's `/dev/cuse` fd, run when the daemon exits — `sys/fs/cuse/cuse.c:765`:

```c
static void
cuse_server_free(void *arg)
{
    struct cuse_server *pcs = arg;
    /* The final server unref should be done by the server thread
     * to prevent deadlock in the client cdevpriv destructor,
     * which cannot destroy itself. */
    while (cuse_server_do_close(pcs) != 1)   /* returns pcs->refs */
        pause("W", hz);                       /* <-- MWCHAN "W", spins forever */
    cuse_server_unref(pcs);
}
```

`cuse_server_do_close()` returns `pcs->refs` (`cuse.c:759`). The loop busy-waits
(1s `pause` ticks) until `pcs->refs == 1` — i.e. until **every client handle has
been released**. `pcs->refs` is `++`'d per client open (`cuse.c:1506`, in
`cuse_client_open`) and dropped in the client cdevpriv destructor
`cuse_client_free()` → `cuse_server_unref()` (`cuse.c:1480`). When a client ref
is never dropped, this loop **never terminates and is not interruptible** (plain
`pause()`, no signal/timeout escape).

There *is* a mitigation that should prevent this: `cuse_server_do_close()` sets
`pcs->is_closing` and wakes all clients (`cuse.c:752-755`), and a client blocked
in `cuse_client_receive_command_locked()` returns `CUSE_ERR_OTHER` when
`pcs->is_closing` is set (`cuse.c:631`). The observed hang means a server ref is
held by a path that **escapes** this wakeup. The author's own comment at
`cuse_server_free` ("the client cdevpriv destructor, which cannot destroy
itself") points at the self-referential case — which is exactly the **loopback**
(`-L`/`-l`) device, where virtual_oss is effectively both server and client of
the same cuse server.

## How virtual_oss's teardown contributes

`usr.sbin/virtual_oss/virtual_oss/main.c`:

1. **Worker threads are cancelled, never joined, and the DSP/loopback cuse
   devices are never destroyed.** On exit (`main()` `main.c:2625-2632`):
   ```c
   virtual_oss_process(NULL);   /* returns when voss_exit == 1 */
   destroy_threads();           /* main.c:2498 */
   if (voss_ctl_device[0] != 0)
       cuse_dev_destroy(pdev);  /* CTL device ONLY */
   return (0);
   ```
   `destroy_threads()` is:
   ```c
   for (idx = 0; idx < voss_ntds; idx++)
       pthread_cancel(voss_tds[idx]);
   free(voss_tds);              /* no pthread_join() */
   ```
   The DSP devices created at `main.c:1918` (`vclient_oss_methods`,
   `dsp.play`/`dsp.loop`) and the WAV device (`main.c:1935`) are **never
   `cuse_dev_destroy()`'d** — only the CTL device is. Teardown relies entirely on
   the process exit closing `/dev/cuse`.

2. **Worker threads block in a non-cancellable syscall.** Each worker runs
   (`main.c:1990`):
   ```c
   while (1) { if (cuse_wait_and_process() != 0) break; }
   ```
   `cuse_wait_and_process()` blocks in an `ioctl(2)` on `/dev/cuse`. `ioctl` is
   **not a POSIX cancellation point**, so `pthread_cancel()` does not reliably
   unblock these threads; they are torn down only by process exit, possibly
   mid-command. A thread cancelled between `proc_refs++` and `proc_refs--`
   (`cuse.c:866/884`, `961/979`) would also strand `cuse.c:660`
   `while (pccmd->proc_refs != 0) cv_wait(...)`, a second teardown hazard.

The combination — server process exiting while a loopback/self client ref is
outstanding, with no join/ordered device destroy — leaves `pcs->refs > 1`
permanently, so `cuse_server_free()` spins in `pause("W", hz)` forever.

## Empirical evidence (instrumented kernel + DTrace)

A diagnostic `cuse.ko` that logs `pcs->refs` inside the wait loop, plus DTrace on
the client/server lifecycle, confirmed the mechanism over many start/stop cycles
(MPD + BruteFIR clients):

- **Every** teardown enters the loop with `refs > 1` — the busy-wait is on the
  normal stop path:
  `cuse: server pid 11036 exit stuck: refs=2 (want 1) after 1 s` (refs=3 =
  server + BruteFIR `dsp.loop` + MPD `dsp.play`; refs=2 = server + 1 client).
- DTrace (`fbt:cuse:cuse_client_open/free`, `cuse_server_free`, by `execname`)
  shows the loop draining as each client's cdevpriv destructor
  (`cuse_client_free` → `cuse_server_unref`) runs.
- **Usual** outcome: clients close within ~1–6 s and the server recovers — a
  routine multi-second stall on every stop (pid 11036 logged `after 1 s` then
  `after 6 s`, then recovered).
- **Severe/intermittent** outcome: a client ref never drops → the loop spins
  forever → `D<E`, SIGKILL-immune, `cuse.ko` pinned, reboot required (pid 5992,
  pre-instrumentation). At that point no userland process holds the devices open
  (`procstat -f`) — the leak is purely kernel-side.

So the `is_closing` wakeup *does* release clients in the common case; the
permanent hang is the unlucky-timing race where a client close can't be
serviced (consistent with the userland teardown defects above). The unbounded,
uninterruptible `pause("W")` is what turns that transient leak into a reboot-only
hang.

## Reproduction

1. `virtual_oss -i 8 -C 2 -c 2 -b 32 -s 200ms -f /dev/null -a 0 -d dsp.play -L dsp.loop`
2. Open a client on the loopback and keep it busy, e.g. BruteFIR reading
   `/dev/dsp.loop` (S32_LE, 2ch) and/or MPD playing to `/dev/dsp.play`.
3. Stop virtual_oss (`kill`, `killall`, or stop the consumer then virtual_oss).
4. Intermittently, `virtual_oss` never exits: `ps -o pid,stat,mwchan` shows
   `D<E  W`; `kill -9` has no effect; `cuse.ko` can no longer be unloaded and a
   fresh `virtual_oss` cannot recreate the devices. Reboot required.

Frequency rises with rapid start/stop churn and with a client still attached at
stop time.

## Impact

- Unkillable process; pinned kernel module; **reboot is the only recovery.**
- For any virtual_oss-based audio routing, every restart/rate-change can brick
  the audio stack until reboot.

## Suggested fixes

Kernel (`sys/fs/cuse/cuse.c`):
- `cuse_server_free()` must not busy-wait unboundedly/uninterruptibly. Bound it
  (timeout + forced reclamation) or restructure so a server teardown forcibly
  invalidates outstanding client refs that can no longer be serviced (the
  `is_closing` wakeup currently does not cover the stuck case, esp. loopback /
  self-client).

virtual_oss (`usr.sbin/virtual_oss/virtual_oss/main.c`):
- On exit, `cuse_dev_destroy()` **all** created devices (DSP, WAV, loopback),
  not just CTL, in an order that releases loopback self-references first.
- `pthread_join()` the worker threads after signalling them, rather than
  `pthread_cancel()` + immediate `free(voss_tds)`; have the worker loop exit on
  `voss_exit` cleanly instead of relying on cancellation of a thread blocked in
  a non-cancellation-point `ioctl`.

## Diagnostics captured

```
$ ps -o pid,ppid,stat,wchan,mwchan,command -p <pid>
 PID PPID STAT WCHAN MWCHAN COMMAND
5992 5988 D<E   W     W      virtual_oss ... -d dsp.play -L dsp.loop
$ sudo kill -TERM <pid>; sudo kill -9 <pid>     # no effect, stays D<E
$ sudo procstat -kk <pid>                        # empty KSTACK
$ kldstat | grep cuse                            # cuse.ko refcount held (pinned)
$ sudo kldunload cuse                            # itself hangs in D
# no userland process holds /dev/dsp.play or /dev/dsp.loop open (procstat -f sweep)
```
