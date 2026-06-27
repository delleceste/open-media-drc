# Root cause of the permanent virtual_oss/cuse wedge: ref leak in `cuse_client_open()`

**Captured live 2026-06-27** with the diagnostic `cuse.ko` + a dtrace teardown
tracer + live kgdb walk (`freebsd-virtual-oss-patch/cuse_teardown.d`,
`cuse_walk_pcs.sh`, `cuse_snapshot.sh`; raw log `CAPTURE-2026-06-27-wedge.log`).
This **supersedes** the earlier hypotheses in `VIRTUAL_OSS_CUSE_DEADLOCK.md`
(loopback self-ref — refuted by the `pcs->pid == curproc->p_pid` guard at
cuse.c:1507; and the `cuse.c:660` `proc_refs` trap — refuted here: `proc_refs`
was 0 on every client at the wedge).

## The bug (kernel, `sys/fs/cuse/cuse.c`, `cuse_client_open()`)

```c
cuse_server_lock(pcs);
pcs->refs++;                                   /* 1506: ref taken */
if (pcs->refs < 0 || pcs->pid == curproc->p_pid) {
    pcs->refs--;                               /* only this path undoes it */
    cuse_server_unlock(pcs); return (EINVAL);
}
cuse_server_unlock(pcs);
... malloc pcc ...
cuse_server_lock(pcs);
TAILQ_INSERT_TAIL(&pcs->hcli, pcc, entry);     /* 1539: client linked */
if ((pcs->is_closing != 0) || (dev->si_drv1 == NULL))
    error = EINVAL;                            /* 1542: server tearing down */
else
    error = 0;
cuse_server_unlock(pcs);
if (error != 0)
    return (error);                            /* 1549: LEAK — see below */
if ((error = devfs_set_cdevpriv(pcc, &cuse_client_free)) != 0)  /* 1552 */
    return (error);
```

On the `is_closing` / `si_drv1==NULL` error path (1542 → 1549) the function
returns with:

1. `pcs->refs` **incremented and never decremented** (the `--` only exists on the
   pid/overflow path at 1509), and
2. `pcc` **left linked in `pcs->hcli`**, and
3. **`cuse_client_free` never registered** — `devfs_set_cdevpriv()` (1552) is
   only reached when `error == 0`. So nothing will ever run the destructor that
   would `TAILQ_REMOVE` the client and `cuse_server_unref()` the ref.

**Every `open()` that races into the `is_closing` window leaks exactly one server
ref (and one `cuse_client` struct).** `cuse_server_free()` then busy-waits
`while (cuse_server_do_close(pcs) != 1) pause("W", hz)` forever (refs can only go
up, never back to 1) → process stuck `D<E` `MWCHAN=W`, SIGKILL-immune; `cuse.ko`
pinned (`cuse_kern_uninit` drains `cuse_server_head` forever, so `kldunload cuse`
also hangs); **reboot required.**

## Captured proof

```
FREE:entry  pcs=...c900 refs=2 is_closing=1            (virtual_oss SIGKILLed)
do_close    refs=2
client_free pcc=...caf000 refs(pre)=2 (by brutefir)    brutefir's real ref: 2->1
unref       pcs=...c900   refs(pre)=2 (by brutefir)
do_close    refs=2  ->  refs=3  ->  refs=4  (then frozen at 4 for 190+ s)
```
kgdb walk at the wedge: `refs=4`, **3 clients** in `hcli` (= server 1 + 3 leaked),
all on `server_dev` = `dsp.loop`, all `cflags=0x6` (poll), all `proc_refs=0`.
i.e. brutefir's legitimate close dropped the ref to 1, then 3 open-races during
the teardown window leaked it back up to 4. (One leaked client reused the freed
brutefir address `...caf000` — malloc recycling, not the original.)

## Why intermittent
The leak only fires if an `open()` lands in the `is_closing` window before the
device node is destroyed. Fast teardowns with no concurrent opener recover
normally (historically logged `refs=2/3 ... after 1 s` then gone). Our repro
*guaranteed* it: the steady-state poll-livelock (brutefir busy-polling `dsp.loop`,
see brutefir `dai.c` poll-mode spin) kept clients hammering the device, and
brutefir itself was wedged unkillable in cuse, so opens kept arriving exactly as
the server tore down.

## Secondary bug (client side, same file)
`cuse_client_receive_command_locked()` uses an **uninterruptible** `cv_wait()` at
cuse.c:637 (the `error != 0` branch) and cuse.c:660 (`while (pccmd->proc_refs)`).
Once a client (brutefir) takes a signal, it drops into that wait and becomes
**unkillable** (SIGKILL/SIGTERM seen pending, `0x4100`, undeliverable) until the
now-dead server services the command — which never happens. This both wedges the
client and lengthens the server's `is_closing` window that the open-leak exploits.

## Fixes
**Kernel (the real fix):** in `cuse_client_open()`, on the `is_closing` /
`si_drv1==NULL` error path, undo the work: `TAILQ_REMOVE` the client and
`cuse_server_unref(pcs)` (or restructure so `refs++`/insert happen only after the
`is_closing` check passes). Separately, bound/limit `cuse_server_free()`'s
`pause("W")` and give the cuse.c:637/660 waits a signal/closing escape.

**Userland (Christos's committed `0bd5ef6b4363`) mitigates but does not cure:** by
`cuse_dev_destroy()`-ing the devices on exit it removes the devfs nodes so new
opens fail *before* reaching `cuse_client_open`, closing the race window in the
normal-exit path. It does nothing for `kill -9 virtual_oss` (no cleanup runs) or
any other path that sets `is_closing` while the node still exists — the kernel
leak remains latent.
