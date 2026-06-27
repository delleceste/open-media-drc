#!/bin/sh
#
# repro-deadlock.sh — trigger the virtual_oss/cuse teardown deadlock and capture
# the kernel diagnostic ("cuse: server pid N exit stuck: refs=K").
#
# RUN AFTER A REBOOT, with the diagnostic cuse.ko loaded (it is installed at
# /boot/kernel/cuse.ko; the stock module is /boot/kernel/cuse.ko.orig).
#
# Each successful trigger leaves an unkillable D<E virtual_oss and pins cuse.ko,
# so the machine must be rebooted again afterward. The script stops at the first
# wedge and prints the diagnostic lines.
#
# Strategy: churn the real DRC chain (drc.sh 48000 <-> off) — the same path that
# deadlocked in normal use — with MPD attached as a /dev/dsp.play client, until a
# virtual_oss is left in D state.
set -u

REPO=/home/giacomo/open-media-drc
DRC="$REPO/drc.sh"
CYCLES="${CYCLES:-15}"

echo "=== confirm the DIAGNOSTIC cuse module is the one on disk ==="
strings /boot/kernel/cuse.ko 2>/dev/null | grep -q "exit stuck" \
  && echo "OK: /boot/kernel/cuse.ko is the diagnostic build" \
  || { echo "WARNING: /boot/kernel/cuse.ko is NOT the diagnostic build"; }
echo "loaded cuse module:"; kldstat | grep -i cuse || echo "  (cuse not loaded yet — will load when the chain starts)"
echo

wedged() { ps -ax -o pid,stat,command | awk '$2 ~ /D/ && /virtual_oss/ {print; f=1} END{exit !f}'; }

capture() {
  echo
  echo "*** virtual_oss WEDGED — capturing ***"
  ps -ax -o pid,ppid,stat,mwchan,command | grep virtual_oss | grep -v grep
  echo "--- waiting 8s for diagnostic printf ticks ---"
  sleep 8
  echo "=== dmesg: 'exit stuck' diagnostic ==="
  dmesg | grep "exit stuck" | tail -n 20
  echo
  echo "Record the refs=K value above, then REBOOT (the wedge is unrecoverable)."
}

# best-effort: pick a playable track so MPD holds /dev/dsp.play during the chain
TRK="$(mpc listall 2>/dev/null | grep -iE '\.(flac|wav|mp3|m4a)$' | head -1)"

for i in $(seq 1 "$CYCLES"); do
  echo "=== cycle $i/$CYCLES: drc.sh 48000 ==="
  $DRC 48000 >/dev/null 2>&1 || true
  sleep 3
  if [ -n "$TRK" ]; then
    mpc clear >/dev/null 2>&1; mpc add "$TRK" >/dev/null 2>&1; mpc play >/dev/null 2>&1
  fi
  sleep 3
  echo "=== cycle $i/$CYCLES: drc.sh off ==="
  mpc stop >/dev/null 2>&1 || true
  $DRC off >/dev/null 2>&1 || true
  sleep 3
  if wedged; then capture; exit 0; fi
  echo "    (cycle $i clean)"
done

echo
echo "No wedge in $CYCLES cycles. Re-run (it is intermittent), raise CYCLES, or"
echo "tighten timing. The chain is currently stopped (drc.sh off)."
