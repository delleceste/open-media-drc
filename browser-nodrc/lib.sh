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

DAC_DEV=/dev/dsp0                  # OSS DAC node the browser opens directly
DAC_WARMUP_SECS=2                  # silent-stream warm-up before launch

# Warm the DAC clock before the browser opens it.  The OKTO routes silence on a
# *cold* open (see ../OKTO-DAC8-silent-first-open.md) — which is why the *-nodrc
# launchers used to need starting two or three times before any sound came out.
# Feed the DAC a brief silent stream so its clock locks first; the browser's open
# is then the warm "second" open the DAC actually plays.  Best-effort: skipped
# silently if the node is absent (non-OSS host) or busy (MPD already playing
# direct, in which case the DAC is already warm).
drc_warm_dac() {
	[ -c "$DAC_DEV" ] || return 0
	dd if=/dev/zero of="$DAC_DEV" bs=8k >/dev/null 2>&1 &
	_warm_pid=$!
	sleep "$DAC_WARMUP_SECS"
	kill "$_warm_pid" 2>/dev/null
	wait "$_warm_pid" 2>/dev/null
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
	echo "browser-nodrc: warming DAC clock (${DAC_WARMUP_SECS}s silent stream)" >&2
	drc_warm_dac
}
