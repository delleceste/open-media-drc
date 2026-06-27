#!/bin/sh
# cuse_snapshot.sh [PCS]
# One-shot forensic snapshot to run THE MOMENT a virtual_oss wedges (D<E).
# Everything here is safe on a wedged system (no cuse-device touching).
# PCS (optional) = wedged server pointer from cuse_teardown.d; if given, walks it.
#
# CUSE_CMD: 0 NONE 1 OPEN 2 CLOSE 3 READ 4 WRITE 5 IOCTL 6 POLL 7 SIGNAL 8 SYNC
set -u
PCS="${1:-}"

echo "===== $(date) cuse wedge snapshot ====="
echo "--- process states (look for D<E on virtual_oss) ---"
ps -axo pid,ppid,stat,pri,nice,wchan,mwchan,%cpu,command | \
  awk 'NR==1 || /virtual_oss|brutefir|mpd/ && !/awk/'

echo "--- virtual_oss kernel stacks (expect cuse_server_free / pause W) ---"
for p in $(pgrep -x virtual_oss); do echo "[pid $p]"; sudo procstat -kk "$p" 2>/dev/null; done

echo "--- brutefir kernel stacks (stuck in cuse_client_receive_command_locked?) ---"
for p in $(pgrep -x brutefir); do echo "[pid $p]"; sudo procstat -kk "$p" 2>/dev/null; done

echo "--- open cuse/dsp handles ---"
sudo fstat 2>/dev/null | grep -iE "cuse|dsp\." || echo "(none)"

echo "--- kldstat cuse (pinned refcount?) ---"; kldstat | awk 'NR==1||/cuse/'

echo "--- dmesg: diagnostic 'exit stuck' tail ---"; dmesg | grep "exit stuck" | tail -5

if [ -n "$PCS" ]; then
  echo "--- kgdb walk of wedged pcs=$PCS ---"
  DIR=$(dirname "$0"); "$DIR/cuse_walk_pcs.sh" "$PCS"
fi
echo "===== end snapshot ====="
