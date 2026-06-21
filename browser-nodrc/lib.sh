#!/bin/sh
# Shared DRC-bypass helpers for the *-nodrc browser launchers — sourced, not run.
#
# Why this exists: BruteFIR opens the DAC (/dev/dsp0) single-open while DRC is on,
# so a web browser (Firefox/Chrome/Chromium) cannot open the device and has no
# sound — or refuses to play.  These launchers turn DRC *off* (freeing the DAC for
# direct output), run the browser in the foreground, and restore the exact DRC
# state that was in effect beforehand when the browser exits.
#
# The restore is deliberately NOT `drc.sh restore`: `drc.sh off` records "off" in
# last_power and `drc.sh restore` honours that (by design, so an `off` survives a
# reboot).  Restoring via `restore` would therefore leave DRC off after the
# browser quits.  Instead we snapshot the power state + rate from drc.sh's own
# persisted state files up front and re-apply that exact state, through an EXIT
# trap so it runs even if the browser crashes or the launcher is killed.
#
# A launcher sets HERE (its own directory) and BROWSER, then calls
# drc_bypass_begin before running "$BROWSER".

: "${HERE:?lib.sh: caller must set HERE to the launcher directory}"

DRC="$HERE/../drc.sh"
POWER_FILE="$HERE/../last_power"   # "on" / "off"  (matches drc.sh POWER_FILE)
STATE_FILE="$HERE/../last_arg"     # remembered rate/variant (drc.sh STATE_FILE)

DAC_DEV=/dev/dsp0                          # OSS DAC node the browser opens directly
DAC_WARMUP_SECS="${DAC_WARMUP_SECS:-2}"    # final silent warm-up hold before launch
DAC_PRIME_CYCLES="${DAC_PRIME_CYCLES:-2}"  # open/close bounces before the warm-up
DAC_PRIME_HOLD="${DAC_PRIME_HOLD:-0.8}"    # seconds to hold the DAC open per bounce
DAC_PRIME_GAP="${DAC_PRIME_GAP:-0.3}"      # seconds to let the DAC release between bounces

# Hold the DAC open for $1 seconds by feeding it silence, then close.  Returns
# non-zero (best-effort) only if the node is missing; a busy node means another
# client (MPD direct) already holds it, so treat that as "already warm".
_dac_hold_open() {
	[ -c "$DAC_DEV" ] || return 1
	dd if=/dev/zero of="$DAC_DEV" bs=8k >/dev/null 2>&1 &
	_feed_pid=$!
	sleep "$1"
	kill "$_feed_pid" 2>/dev/null
	wait "$_feed_pid" 2>/dev/null
	return 0
}

# Prime + warm the DAC before the browser opens it.  The OKTO routes SILENCE on a
# cold open that switches master crystal (44.1k<->48k family) and only starts
# routing after SEVERAL open/close cycles — a single held warm-up is NOT enough
# (see ../OKTO-DAC8-silent-first-open.md and drc.sh's crystal-switch prime, which
# this mirrors).  So bounce the device open/closed DAC_PRIME_CYCLES times (the
# "prime"), then one final warm hold so the clock is locked when the browser
# opens; the browser is then the warm Nth open the DAC actually plays.
#
# Rate caveat: dd opens /dev/dsp0 at the OSS default rate, so the prime warms
# whichever crystal that rate belongs to.  If the browser then plays content from
# the *other* crystal family, the device must still cross crystals on the
# browser's own open — which a generic launcher cannot prime, as it can't know
# the browser's rate in advance — so that case may still need a relaunch.
# Best-effort throughout: skipped if the node is absent (non-OSS host) or busy
# (MPD already playing direct, in which case the DAC is already warm).
drc_warm_dac() {
	[ -c "$DAC_DEV" ] || return 0
	_i=0
	while [ "$_i" -lt "$DAC_PRIME_CYCLES" ]; do
		_dac_hold_open "$DAC_PRIME_HOLD" || return 0
		sleep "$DAC_PRIME_GAP"   # let the DAC release before the next open
		_i=$((_i + 1))
	done
	_dac_hold_open "$DAC_WARMUP_SECS"
	return 0
}

# Re-apply the DRC state captured by drc_bypass_begin.  Clears its own traps
# first so a TERM-then-EXIT sequence cannot restore twice.
_drc_restore() {
	trap - EXIT INT TERM
	[ "${_prev_power:-off}" = "on" ] || return 0   # was off → leave it off
	echo "browser-nodrc: restoring DRC (${_prev_state:-default})" >&2
	# shellcheck disable=SC2086  # state args are intentionally word-split
	"$DRC" ${_prev_state:-restore}
}

# Snapshot the current DRC state, arm the restore trap, then disable DRC.
drc_bypass_begin() {
	_prev_power=off
	[ -f "$POWER_FILE" ] && _prev_power=$(cat "$POWER_FILE")
	_prev_state=
	[ -f "$STATE_FILE" ] && _prev_state=$(cat "$STATE_FILE")
	trap _drc_restore EXIT INT TERM
	echo "browser-nodrc: disabling DRC for direct DAC output" >&2
	"$DRC" off
	echo "browser-nodrc: priming + warming DAC clock (${DAC_PRIME_CYCLES} bounces + ${DAC_WARMUP_SECS}s hold)" >&2
	drc_warm_dac
}
