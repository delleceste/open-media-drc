# FreeBSD Bugzilla draft

## Title

kern: coredump of a process with many EVFILT_VNODE knotes hangs for hours,
process unkillable (SIGKILL pending), holds fd table lock
(NT_PROCSTAT_KQUEUES note generation)

## Component

kern (sys/kern/kern_event.c, sys/kern/imgact_elf.c)

## Environment

FreeBSD dal 15.1-RELEASE releng/15.1-n283562-96841ea08dcf GENERIC amd64

## Overview

A process (the "codex" CLI, a Rust program using the notify-rs file watcher
with its kqueue backend) had ~670,000 open file descriptors, each with an
EVFILT_VNODE knote attached (kqueue file watching requires one open fd per
watched vnode; kern.openfiles was 675,453 while the process was alive).

The process received a core-dumping signal at 11:04. More than 35 minutes
later the core file still existed with size 0 and the process was:

- unkillable: SIGINT, SIGTERM and SIGKILL all shown as pending
  (procstat -j: flags "P--" for INT/TERM/KILL) and never delivered;
- shown as T+ in ps (56 of 57 threads suspended in thread_suspend_check
  via the coredump single-threading);
- pinning one CPU: a single thread in state "run" busy inside the kernel;
- blocking fd-table introspection: `procstat -f <pid>` blocks (it emitted
  output only very slowly; the note generation runs the whole knote walk
  under fget_remote_foreach on the target's fd table).

## Kernel stack of the busy thread (procstat -kk)

```
15844 147355 codex  notify-rs kqueue lo
  kmem_back_domain+0x14d kmem_malloc_domainset+0xd2 malloc_large+0x2c
  sbuf_put_bytes+0x16a sbuf_bcat+0xe
  kern_proc_kqueue_report_one+0x185 kern_proc_kqueue_report+0x90
  fget_remote_foreach+0x112 kern_proc_kqueues_out+0x67
  elf64_prepare_notes+0x99c elf64_coredump+0x91 coredump_vnode+0xba2
  sigexit+0x271 postsig+0x23a ast_sig+0x1d7 ast_handler+0x88 ast+0x20
  fast_syscall_common+0x1a2
```

All other 56 threads:

```
  mi_switch+0xbc thread_suspend_check+0x23c ast_suspend+0x31
  ast_handler+0x88 ast+0x20 doreti_ast+0x1c
```

## Observed kernel memory behaviour

`vmstat -m | grep sbuf`, sampled while stuck (memory column in bytes):

```
t+0s    sbuf  5  404443520  260315 ...
t+5s    sbuf  4  202736000  260725 ...
...
t+10m   sbuf  5  425611648  268230 ...
t+10m8s sbuf  4  213578112  268949 ...
t+10m16 sbuf  4  214352256  269613 ...
t+10m24 sbuf  5  430223744  270176 ...
t+10m32 sbuf  4  215875968  270633 ...
t+~20m  sbuf  4  284258688  336461 ...
```

The buffer grows monotonically (~100-300 KB/s); the transient 2x spikes are
the SBUF_AUTOEXTEND doubling (allocate double, copy, free old) done via
malloc_large on a buffer that is already hundreds of MB.

## Analysis

sizeof(struct kinfo_knote) is 1160 bytes because it embeds
knt_vnode_fullpath[PATH_MAX]. With ~670k vnode knotes the
NT_PROCSTAT_KQUEUES note is ~780 MB.

The note is generated twice (imgact_elf.c note_procstat_kqueues): once for
the sizing pass (sb == NULL) and once for the emit pass. Although the
sizing pass hands in an sbuf with sbuf_count_drain, kern_proc_kqueues_out()
(kern_event.c) ignores the drain and buffers the *entire* report in its own
kernel sbuf first:

```c
	s = sbuf_new(&sm, NULL, sb_len, maxlen == -1 ? SBUF_AUTOEXTEND :
	    SBUF_FIXEDLEN);
	error = kern_proc_kqueues_out1(curthread, p, s, compat32);
	sbuf_finish(s);
	if (error == 0)
		sbuf_bcat(sb, sbuf_data(s), ...);
```

So the sizing pass alone malloc's ~780 MB of kernel memory just to count
bytes, growing by doubling+copy (malloc_large of hundreds of MB each time),
and the emit pass then does it all again into a second ~780 MB SBUF_FIXEDLEN
buffer.

Per knote, kern_proc_kqueue_report_one() additionally does
kn_enter_flux / KQ_UNLOCK / f_userdump (vn_fullpath for vnode knotes) /
KQ_LOCK / kn_leave_flux, which is what makes the walk proceed at only a few
hundred knotes per second — hours for 670k knotes, times two passes. The
NT_PROCSTAT_FILES note (registered before KQUEUES, also with per-fd
fullpath resolution, kern.coredump_pack_fileinfo=1) has the same shape and
had presumably already consumed part of the elapsed time.

During all of this the process is in sigexit(): further signals, including
SIGKILL, are only marked pending and are never acted on, and the whole knote
walk runs inside fget_remote_foreach() on the process's fd table, so other
consumers of that table (procstat -f) block as well.

## Impact

- A crash of any fd-heavy kqueue-based file watcher (watchman, notify-rs,
  etc. — increasingly common with LLM coding agents watching large source
  trees) turns into an hours-long, unkillable, CPU-pinning core dump that
  transiently allocates ~1.5 GB of kernel malloc memory.
- Unprivileged local DoS vector: any user can open a few hundred thousand
  vnode kevents (subject only to kern.maxfilesperproc, here 1,878,633) and
  raise SIGQUIT; there is no way for the administrator to kill the
  resulting process short of reboot.
- There is no sysctl to disable the kqueue note (kern.coredump_pack_fileinfo
  and kern.coredump_pack_vmmapinfo exist; there is no
  kern.coredump_pack_kqinfo).

## Suggested directions

- Make kern_proc_kqueues_out() honor the caller's sbuf/drain instead of
  buffering the entire note in a private sbuf (the sizing pass would then
  cost no large allocations at all).
- Add a kern.coredump_pack_kqinfo sysctl (parallel to pack_fileinfo), or an
  overall cap on procstat note sizes in core dumps.
- Consider checking for a pending SIGKILL periodically during note
  generation so an administrator can abort a pathological dump.

## How to reproduce

On a machine with enough fds (raise kern.maxfilesperproc if needed), run a
program that opens ~500k files, registers an EVFILT_VNODE kevent for each
on one kqueue, then calls abort(). Observe: 0-byte core file, unkillable
process in T state, one thread spinning in elf64_prepare_notes /
kern_proc_kqueues_out, sbuf malloc type growing to hundreds of MB, and
`procstat -f <pid>` blocking.

## Follow-up comment (posted after filing): why it takes hours — sbuf growth is O(n^2)

Further live measurement explains the pathological duration. The sizing-pass
sbuf in kern_proc_kqueues_out() is SBUF_AUTOEXTEND, and kernel sbuf extension
(sys/kern/subr_sbuf.c) stops doubling at SBUF_MAXEXTENDSIZE = PAGE_SIZE:

```c
	if (size < (int)SBUF_MAXEXTENDSIZE) {
		newsize = SBUF_MINEXTENDSIZE;
		while (newsize < size)
			newsize *= 2;
	} else {
		newsize = roundup2(size, SBUF_MAXEXTENDINCR);	/* 4 KB steps */
	}
```

and sbuf_extend() does SBMALLOC(newsize) + memcpy(entire buffer) + SBFREE
on every extension. So past 4 KB the buffer grows in 4 KB increments, and
each increment reallocates and copies the whole (now hundreds of MB) buffer:
O(n^2) in the note size, with a full malloc_large page-allocation/free churn
per 4 KB of progress.

Observed on the live process (signal at 11:04):

- 11:38  sbuf = 284 MB, vmstat -m sbuf req rate ~16/s (= 4 KB × 16 = 64 KB/s)
- 13:50  sbuf = 639 MB, 174 min of CPU time consumed, core file still 0 bytes,
  progress rate decayed linearly with buffer size (~330 KB/s at 200 MB,
  ~64 KB/s at 640 MB) — one CPU 100% busy memcpy'ing the buffer once per
  4 KB appended.

At ~780 MB final note size this is roughly 190,000 reallocations copying an
average of ~390 MB each — tens of petabytes of memcpy for the sizing pass
alone. This turns what could be a seconds-long dump into many hours.

This also suggests the smallest fix with the biggest effect: make
kern_proc_kqueues_out() honor the caller's drain (sbuf_count_drain for the
sizing pass, the core vnode drain for the emit pass) instead of accumulating
into a private SBUF_AUTOEXTEND sbuf — the O(n^2) growth, the ~780 MB×2
allocations, and most of the wall-clock time all disappear. Independently,
sbuf's 4 KB linear extension policy is a footgun for any large in-kernel
sbuf consumer.

## Notes from the live incident

- ps:      `15844 codex resume  T+  elapsed 01:37:28` (parent: interactive bash)
- core:    /home/giacomo/devel/qobuzconnect2mpd/codex.core, 0 bytes, born 11:04
- signals: procstat -i shows INT, TERM, KILL all pending ("P--")
- fds:     procstat -f had emitted 673,201 lines when we gave up waiting;
           kern.openfiles = 675,453
- memory:  34 GB free RAM, 53 GB free kmem — the allocations themselves
           succeed; the cost is the repeated doubling/copying and the
           per-knote lock/flux/vn_fullpath cycle.
