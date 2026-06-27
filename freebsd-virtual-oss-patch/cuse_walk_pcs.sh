#!/bin/sh
# cuse_walk_pcs.sh PCS [TEXTBASE]
# Dump a cuse_server's refs + every client's per-command {command,proc_refs,
# proc_curr} via live kgdb (read-only /dev/mem). PCS is the heap pointer printed
# by cuse_teardown.d at cuse_server_free:entry (or by the live pointer-chase).
# TEXTBASE defaults to the loaded cuse.ko base from kldstat.
#
# Safe on a wedged system: kgdb on /dev/mem does NOT touch cuse devices.
set -u
PCS="${1:?usage: cuse_walk_pcs.sh PCS [textbase]}"
TB="${2:-$(kldstat 2>/dev/null | awk '$NF=="cuse.ko"{print $3; exit}')}"
TB="${TB:-0xffffffff83286000}"
DBG=/usr/lib/debug/boot/kernel/cuse.ko.debug

{
cat <<EOF
set confirm off
set pagination off
set print pretty off
add-symbol-file $DBG $TB
set \$s = (struct cuse_server *)$PCS
printf "SERVER %p pid=%d refs=%d is_closing=%d\n", \$s, \$s->pid, \$s->refs, \$s->is_closing
set \$c = \$s->hcli.tqh_first
set \$nc = 0
while (\$c != 0)
  set \$nc = \$nc + 1
  printf "  CLIENT %p cflags=%#x server_dev=%p\n", \$c, \$c->cflags, \$c->server_dev
  set \$i = 0
  while (\$i < 9)
    set \$m = &\$c->cmds[\$i]
    if (\$m->proc_refs != 0 || \$m->command != 0 || \$m->proc_curr != 0 || \$m->entered != 0)
      printf "    cmd[%d] command=%d proc_refs=%d proc_curr=%p entered=%p\n", \$i, \$m->command, \$m->proc_refs, \$m->proc_curr, \$m->entered
      if (\$m->proc_curr != 0)
        printf "         -> client pid=%d comm=%s\n", \$m->proc_curr->p_pid, \$m->proc_curr->p_comm
      end
    end
    set \$i = \$i + 1
  end
  set \$c = \$c->entry.tqe_next
end
printf "  clients=%d  (refs-1 should == client-refs held)\n", \$nc
quit
EOF
} | sudo kgdb -q /boot/kernel/kernel /dev/mem 2>&1 \
  | grep -E "SERVER|CLIENT|cmd\[|client pid|clients="
