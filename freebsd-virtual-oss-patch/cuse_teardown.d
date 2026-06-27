/*
 * cuse_teardown.d — live tracer for the virtual_oss/cuse teardown deadlock.
 *
 * Run in the background BEFORE triggering a teardown:
 *     sudo dtrace -qs cuse_teardown.d | tee cuse_teardown.log
 *
 * All probes here are LOW frequency (teardown events only) — they do NOT fire
 * during the steady-state poll livelock, so this is safe to leave running.
 * Field offsets are from the stock cuse.ko.debug (layout == diagnostic build):
 *   cuse_server.pid=200  is_closing=204  refs=208
 *   cuse_client.server=1400
 * The pcs captured at cuse_server_free:entry is the EXACT wedged server; feed it
 * to kgdb (cuse_walk_pcs.sh) post-wedge for the per-client/per-command dump.
 */
#pragma D option quiet

dtrace:::BEGIN { printf("== cuse teardown tracer armed @ %Y ==\n", walltimestamp); }

/* Server fd destructor begins: arg0 = the pcs that may wedge in pause("W"). */
fbt::cuse_server_free:entry {
    g_pcs = (uintptr_t)arg0;
    printf("%Y FREE:entry   pcs=%p pid=%d refs=%d is_closing=%d  (caller %s/%d tid=%d)\n",
        walltimestamp, arg0,
        *(int *)((uintptr_t)arg0 + 200),
        *(int *)((uintptr_t)arg0 + 208),
        *(int *)((uintptr_t)arg0 + 204),
        execname, pid, tid);
    printf("           >>> WEDGE CANDIDATE pcs=%p — run: ./cuse_walk_pcs.sh %p\n",
        arg0, arg0);
}
/* Each iteration of the wait loop: arg1 == pcs->refs (want 1). The spin trail.
 * do_close returning 1 == recovered; refs stuck >1 forever == permanent wedge. */
fbt::cuse_server_do_close:return {
    printf("%Y do_close     refs=%d  (wedge pcs=%p)\n",
        walltimestamp, arg1, (void *)g_pcs);
}

/* A client cdevpriv destructor — this is what must run to drain refs. */
fbt::cuse_client_free:entry {
    this->pcs = *(uintptr_t *)((uintptr_t)arg0 + 1400);
    printf("%Y client_free  pcc=%p pcs=%p refs(pre)=%d  (by %s/%d)\n",
        walltimestamp, arg0, (void *)this->pcs,
        *(int *)(this->pcs + 208), execname, pid);
}

/* Every server ref drop. */
fbt::cuse_server_unref:entry {
    printf("%Y unref        pcs=%p refs(pre)=%d  (by %s/%d)\n",
        walltimestamp, arg0, *(int *)((uintptr_t)arg0 + 208), execname, pid);
}
