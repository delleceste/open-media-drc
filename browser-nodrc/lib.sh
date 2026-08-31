#!/bin/sh
# Shared DRC-bypass helpers for the *-nodrc browser launchers — sourced, not run.
#
# Why this exists: BruteFIR opens the DAC (/dev/dsp.dac) single-open while DRC is on,
# so a web browser (Firefox/Chrome/Chromium) cannot open the device and has no
# sound — or refuses to play.  These launchers turn DRC *off* (freeing the DAC for
# direct output), run the browser in the foreground, and restore the exact DRC
# state that was in effect beforehand when the browser exits.
#
# The restore is deliberately NOT `drc.sh restore`: `drc.sh off` records "off" in
# last_power and `drc.sh restore` honours that (by design, so an `off` survives a
# reboot).  Restoring via `restore` would therefore leave DRC off after the
# browser quits.  Instead we snapshot the power state + rate from drc.sh's own
# state (via `drc.sh session`) up front and re-apply that exact state, through an EXIT
# trap so it runs even if the browser crashes or the launcher is killed.
#
# A launcher sets HERE (its own directory) and BROWSER, then calls
# drc_bypass_begin before running "$BROWSER".  A launcher whose browser has no
# OSS backend also sets BROWSER_AUDIO=alsa first (see the ALSA shim below), and
# passes $BROWSER_ALSA_FLAGS on the browser's command line.

: "${HERE:?lib.sh: caller must set HERE to the launcher directory}"

# The engine: the installed omdrc wrapper when there is one, else this checkout.
DRC="$(command -v omdrc 2>/dev/null || echo "$HERE/../drc.sh")"

# The DRC state is read from the engine (`drc.sh session`), never from state
# files at a path guessed here.  It used to read "$HERE/../last_power" and
# "$HERE/../last_arg", which are correct ONLY in repo mode: a packaged install
# pins OMDRC_STATE_DIR in ${PREFIX}/etc/open-media-drc/omdrc.conf (on this box
# ~/.local/state/omdrc) and the checkout's own copies are stale leftovers.  A
# stale "off" made the exit trap conclude there was nothing to restore and leave
# the chain DOWN after the browser quit.  Asking the engine cannot drift: it is
# the same resolution `drc.sh restore` itself uses.

# /dev/dsp.dac is the DAC by role: FreeBSD pcm unit numbers follow attach
# order, so /dev/dsp0 is only the DAC by luck on a multi-card box.  The
# omdrc_audio service keeps the link pointed at the right one; without it
# (single-DAC box, or Linux) fall back to unit 0.
DAC_DEV=$([ -e /dev/dsp.dac ] && echo /dev/dsp.dac || echo /dev/dsp0)
DAC_WARMUP_SECS="${DAC_WARMUP_SECS:-0}"    # final silent warm-up hold before launch
DAC_PRIME_CYCLES="${DAC_PRIME_CYCLES:-0}"  # open/close bounces before the warm-up
DAC_PRIME_HOLD="${DAC_PRIME_HOLD:-0.8}"    # seconds to hold the DAC open per bounce
DAC_PRIME_GAP="${DAC_PRIME_GAP:-0.3}"      # seconds to let the DAC release between bounces
DAC_PRIME_RATE="${DAC_PRIME_RATE:-}"       # Hz to warm at (empty = whatever OSS defaults to)
DAC_RATE_TOL="${DAC_RATE_TOL:-100}"        # Hz slack when reading back the programmed rate

# Hold the DAC open for $1 seconds by feeding it silence, then close.  Returns
# non-zero (best-effort) only if the node is missing; a busy node means another
# client (MPD direct) already holds it, so treat that as "already warm".
#
# Rate matters here, and dd cannot express one: it just writes bytes, so the
# device opens at the OSS default and the warm-up lands on whatever crystal that
# rate belongs to — which, on a DAC with vchans off and bitperfect on, is a
# rate CHANGE in its own right.  When DAC_PRIME_RATE is set we therefore feed
# silence through sox, which issues SNDCTL_DSP_SPEED for the rate we ask for.
# dd stays the fallback for a box without sox (same behaviour as before).
_dac_hold_open() {
	[ -c "$DAC_DEV" ] || return 1
	if [ -n "${DAC_PRIME_RATE:-}" ] && command -v sox >/dev/null 2>&1; then
		# `sine 0` is a 0 Hz tone, i.e. digital silence.
		sox -q -n -r "$DAC_PRIME_RATE" -c 2 -b 32 -t ossdsp "$DAC_DEV" \
		    synth "$1" sine 0 >/dev/null 2>&1
		return 0
	fi
	dd if=/dev/zero of="$DAC_DEV" bs=8k >/dev/null 2>&1 &
	_feed_pid=$!
	sleep "$1"
	kill "$_feed_pid" 2>/dev/null
	wait "$_feed_pid" 2>/dev/null
	return 0
}

# Prime + warm the DAC before the browser opens it.  The OKTO routes SILENCE on a
# cold open after its clock relocks for a new rate, and only starts routing after
# SEVERAL open/close cycles — a single held warm-up is NOT enough (see
# ../OKTO-DAC8-silent-first-open.md).  Bouncing the device open/closed
# DAC_PRIME_CYCLES times (the "prime") and then holding it warm was the
# mitigation; the browser was then the warm Nth open the DAC actually plays.
#
# Both default to 0 now, mirroring drc.sh: the kernel-side fix (uaudio
# clock-before-alt reorder, ../freebsd-uaudio-patch/uaudio-clock-before-alt.md)
# is installed and is meant to make the prime unnecessary.  The knobs stay so the
# recipe can be put back from the environment if listening says otherwise —
# DAC_PRIME_CYCLES=2 DAC_WARMUP_SECS=2 chromium-nodrc.sh — which is also why the
# rate handling below still matters when they are non-zero.
#
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

# First of $@ that exists on PATH, echoed; non-zero if none do.  Browser command
# names are not portable — the FreeBSD chromium port installs its launcher as
# `chrome` (there is no `chromium` at all), while Linux distributions ship
# `chromium` or `chromium-browser` and keep `chrome` for Google Chrome.  So the
# launchers name candidates in preference order rather than one fixed command;
# cmake/browser-audio.cmake probes the SAME lists to decide which .desktop
# entries are worth installing.
browser_resolve() {
	for _b in "$@"; do
		command -v "$_b" >/dev/null 2>&1 && { echo "$_b"; return 0; }
	done
	return 1
}

# ── sndio mixing server for the browser session (FreeBSD) ────────────────────
# sndiod takes the DAC, mixes every client into one stream, and converts rate and
# format.  Chromium picks its sndio backend on its own once PulseAudio is absent
# (its probe order is PulseAudio -> SNDIO -> ALSA), so unlike the ALSA shim this
# needs no browser flags and no chrome://flags choice.
#
# The daemon MUST NOT outlive the browser: it holds the DAC, so BruteFIR could
# not reopen it and the DRC restore would fail.  _drc_restore stops it before
# re-applying DRC.  Only an instance we started is stopped — a system-wide
# sndiod (someone else's choice) is left alone and simply reused.
SNDIOD_PID=""            # set only when WE started it
sndiod_started_by_us() { [ -n "$SNDIOD_PID" ]; }

browser_sndio_begin() {
	[ "$(uname)" = "FreeBSD" ] || return 0
	command -v sndiod >/dev/null 2>&1 || {
		echo "browser-nodrc: sndiod not found — install audio/sndio, or use BROWSER_AUDIO=alsa" >&2
		return 0
	}
	# A chrome://flags "Audio Backend" choice OVERRIDES the probe, so a profile
	# left on ALSA (e.g. from the older shim) will bypass sndiod entirely, open
	# the raw DAC that sndiod is holding, get EBUSY, and be silent.  The flag is
	# per profile and nothing outside the browser can change it.
	case "$BROWSER" in
		google-chrome*) _prof="$HOME/.config/google-chrome" ;;
		*)              _prof="$HOME/.config/chromium" ;;
	esac
	if [ -f "$_prof/Local State" ] && grep -q 'audio-backend@' "$_prof/Local State" 2>/dev/null; then
		echo "browser-nodrc: WARNING — this profile pins an 'Audio Backend' in chrome://flags." >&2
		echo "  That overrides the sndio path and will make $BROWSER silent." >&2
		echo "  Set it back: chrome://flags -> 'Audio Backend' -> Default -> Relaunch." >&2
	fi

	if pgrep -x sndiod >/dev/null 2>&1; then
		echo "browser-nodrc: reusing the sndiod already running" >&2
		return 0
	fi

	# Pin the rate to what the DAC is already programmed at, for the same reason
	# the ALSA shim does: an unpinned sndiod opens at its own default (48 kHz),
	# which on a DAC sitting at 44.1 kHz is a crystal switch.
	_rate=$(_dac_current_rate) || _rate="${BROWSER_ALSA_RATE:-48000}"

	# sndio addresses the card by unit number: rsnd/<N> is /dev/dspN.
	_unit=$(basename "$(readlink -f "$DAC_DEV" 2>/dev/null || echo "$DAC_DEV")")
	_unit=${_unit#dsp}
	case "$_unit" in ''|*[!0-9]*) _unit=0 ;; esac

	# ORDER MATTERS: per-device options must PRECEDE -f, or they are silently
	# ignored and the device opens at sndiod's default rate instead.
	if sndiod -r "$_rate" -f "rsnd/$_unit"; then
		# sndiod daemonises; find the instance we just created.
		SNDIOD_PID=$(pgrep -x sndiod | tail -1)
		echo "browser-nodrc: sndiod mixing on rsnd/$_unit at ${_rate} Hz (pid ${SNDIOD_PID:-?})" >&2
	else
		echo "browser-nodrc: sndiod failed to start — the browser will have no sound" >&2
	fi
}

# Stop only the sndiod we started, and wait for it to let the DAC go: the DRC
# restore that follows needs to open the device itself.
browser_sndio_end() {
	sndiod_started_by_us || return 0
	kill "$SNDIOD_PID" 2>/dev/null
	_i=0
	while kill -0 "$SNDIOD_PID" 2>/dev/null && [ "$_i" -lt 20 ]; do
		sleep 0.25; _i=$(( _i + 1 ))
	done
	kill -0 "$SNDIOD_PID" 2>/dev/null && kill -KILL "$SNDIOD_PID" 2>/dev/null
	SNDIOD_PID=""
	echo "browser-nodrc: sndiod stopped" >&2
}

# ── ALSA shim for a browser with no OSS backend (FreeBSD) ────────────────────
# Chromium/Chrome have no OSS output at all: their FreeBSD backends are
# PulseAudio, SNDIO and ALSA, in that probe order.  Firefox does have one (its
# cubeb OSS backend), which is why only the Chromium family needs this.
#
# On FreeBSD "ALSA" is not a layer: there is no kernel component, alsa-lib is a
# userland shim and the only PCM plugin installed is libasound_module_pcm_oss.so,
# which opens /dev/dspN.  It exists only inside processes that link libasound —
# the browser does, MPD/BruteFIR/cdin do not — and the config below is handed to
# the browser through ALSA_CONFIG_PATH, so it is not merely unused by the rest of
# the chain, it is unreachable by it.  No global ALSA file is touched.
#
# By the time this runs DRC is off, so the browser is the SOLE opener of the
# DAC: hw.snd.vchans_enable=0 and dev.pcm.<unit>.bitperfect=1 mean no in-kernel
# mixer and no virtual channels.  Nothing is shared and nothing is arbitrated.
#
# Rate policy — pin the DAC, resample in userland.  With no vchans the browser's
# open would otherwise PROGRAM the DAC's clock, and on the OKTO a rate change on
# a cold open routes silence (../OKTO-DAC8-silent-first-open.md).  So the slave
# is pinned to the rate the DAC is already running and alsa-lib's `plug` does the
# conversion: no crystal switch, no cold-open prime, a deterministic launch.  The
# cost is a userland SRC on audio that is lossy streaming material to begin with.
# Format policy — pin it too, and pin it WIDE.  Leaving the format to be
# negotiated is a trap that costs you the audio without telling you: alsa-lib's
# OSS plugin advertises S24_LE among its formats, so for a 16-bit source (which
# is what a browser produces) `plug` legitimately picks S24_LE for the slave and
# converts correctly *for 24-bit*.  The device is running 32-bit, so those
# samples are read as 32-bit and come out 256x too small — 48 dB down, silence
# to the ear.  Nothing reports an error: the byte counts are right, every write
# is accepted, and a syscall trace is identical to the working case.  Diagnosed
# 2026-08-31 by dumping the negotiated chain (snd_pcm_dump) — S16 in gave
# "Slave: OSS PCM I/O Plugin / format: S24_LE" while S32 in gave S32_LE and was
# audible.  Pinning S32_LE removes the choice, and plug's S16->S32 conversion is
# exact (measured peak 0.089996 for a 0.09 full-scale tone).
#
# S32_LE is right for USB DACs on FreeBSD, where uaudio fixes one (rate,bits)
# per attach and pads narrower sources up to 32-bit
# (../OKTO-DAC8-FreeBSD-44k1-flicker.md).  A device that genuinely cannot do
# S32 needs BROWSER_ALSA_FORMAT set, or the open fails and the browser is silent.
# oss    — the browser has a native OSS backend (Firefox).  Nothing to do.
# sndio  — start a per-session sndiod that owns the DAC and mixes; the browser
#          reaches it through its own sndio backend.  This is the default for the
#          Chromium family, because the DAC is SINGLE-OPEN and Chromium is not:
#          it opens and closes the device around each playback, so a second tab,
#          a UI sound, or merely the tail of one stream meeting the head of the
#          next needs a second concurrent open.  There is none, the open fails
#          with "PcmOpen: <dev>,Device busy", and Chromium never retries — the
#          session is silent from then on.  sndiod mixes, so the collision cannot
#          happen; measured with two concurrent tabs, zero errors.
# alsa   — the alsa-lib/pcm_oss shim (see browser_alsa_begin).  No daemon, but no
#          mixing either, so it inherits the single-open problem above; it also
#          needs a per-profile chrome://flags choice that nothing can set for you.
#          Kept because it is debugged and needs no extra process, not because it
#          is recommended.
BROWSER_AUDIO="${BROWSER_AUDIO:-oss}"   # oss | sndio | alsa
BROWSER_ALSA_FORMAT="${BROWSER_ALSA_FORMAT:-S32_LE}"   # slave format; see above
BROWSER_ALSA_FLAGS=""                   # browser command-line flags, set below
_alsa_tmpdir=""

# Base alsa.conf to include before our overrides: ALSA_CONFIG_PATH REPLACES the
# top-level config rather than adding to it, so without this the file would have
# no plugin definitions at all.
_alsa_base_conf() {
	for _c in /usr/local/share/alsa/alsa.conf /usr/share/alsa/alsa.conf; do
		[ -f "$_c" ] && { echo "$_c"; return 0; }
	done
	return 1
}

# The rate the DAC is programmed at right now, snapped to the nearest standard
# rate.  dev.pcm.<unit>.feedback_rate is the USB async feedback figure, so it
# reads a hair off (44101 for 44100) — hence the tolerance, the same one drc.sh
# uses to decide whether the DAC is streaming at the rate it asked for.
_dac_current_rate() {
	_unit=$(basename "$(readlink -f "$DAC_DEV" 2>/dev/null || echo "$DAC_DEV")")
	_unit=${_unit#dsp}
	case "$_unit" in
		''|*[!0-9]*) return 1 ;;
	esac
	_fb=$(sysctl -n "dev.pcm.${_unit}.feedback_rate" 2>/dev/null) || return 1
	case "$_fb" in
		''|*[!0-9]*) return 1 ;;
	esac
	for _std in 44100 48000 88200 96000 176400 192000; do
		_d=$(( _fb - _std ))
		[ "$_d" -lt 0 ] && _d=$(( -_d ))
		if [ "$_d" -le "${DAC_RATE_TOL:-100}" ]; then
			echo "$_std"
			return 0
		fi
	done
	return 1
}

# Warn if the browser has not been told to USE the ALSA backend.  This is a
# per-profile chrome://flags choice ("Audio Backend" -> ALSA) and there is no
# command-line equivalent that works: this build's audio manager probes
# PulseAudio then SNDIO and never reaches ALSA on its own, and passing
# --audio-backend=alsa does not steer it.  Without the flag the browser plays
# through sndio (or nothing), our config is never consulted, and the symptom is
# silence with no error anywhere — an hour of debugging that one warning saves.
browser_alsa_check_flag() {
	_ls="$1/Local State"
	[ -f "$_ls" ] || return 0            # first run: the profile does not exist yet
	grep -q 'audio-backend@' "$_ls" 2>/dev/null && return 0
	echo "browser-nodrc: WARNING — this profile has no 'Audio Backend' flag set." >&2
	echo "  $BROWSER will not use ALSA, and the DAC config below is ignored (= silence)." >&2
	echo "  Fix it once, in the browser: chrome://flags -> search 'Audio Backend'" >&2
	echo "  -> select ALSA -> Relaunch.  The choice is remembered per profile." >&2
}

# Write the per-run ALSA config and export it for the browser alone.  Sets
# BROWSER_ALSA_FLAGS on success and leaves it empty on any failure, so a browser
# still launches (without sound) rather than not launching at all.
browser_alsa_begin() {
	[ "$(uname)" = "FreeBSD" ] || return 0     # Linux Chromium talks to real ALSA
	_base=$(_alsa_base_conf) || {
		echo "browser-nodrc: no alsa.conf found — install audio/alsa-lib + audio/alsa-plugins" >&2
		return 0
	}
	if [ ! -f /usr/local/lib/alsa-lib/libasound_module_pcm_oss.so ]; then
		echo "browser-nodrc: alsa-lib has no OSS plugin — install audio/alsa-plugins" >&2
		return 0
	fi

	# A rate we READ BACK is a rate we know we are not changing.  A guessed one
	# may well be a change — harmless while the kernel fix holds, and the case the
	# DAC_PRIME_CYCLES knob exists for if it does not.
	_rate=$(_dac_current_rate) || {
		_rate="${BROWSER_ALSA_RATE:-48000}"
		echo "browser-nodrc: cannot read the DAC rate — guessing ${_rate} Hz" >&2
	}
	# Mixer node matching the DAC: the role link if omdrc_audio maintains one,
	# else the numbered node beside the numbered dsp.
	_mixer=/dev/mixer.dac
	[ -c "$_mixer" ] || _mixer="/dev/mixer$(basename "$(readlink -f "$DAC_DEV")" | sed 's/^dsp//')"

	# Profile location differs per browser (FreeBSD's chromium port is `chrome`
	# but still uses ~/.config/chromium).
	case "$BROWSER" in
		google-chrome*) browser_alsa_check_flag "$HOME/.config/google-chrome" ;;
		*)              browser_alsa_check_flag "$HOME/.config/chromium" ;;
	esac

	_alsa_tmpdir=$(mktemp -d -t omdrc-alsa) || return 0
	cat > "$_alsa_tmpdir/omdrc.conf" <<-EOF
		# Generated per run by browser-nodrc/lib.sh — not a system file.
		pcm.omdrc_dac {
		    type plug
		    slave {
		        pcm { type oss; device "$DAC_DEV" }
		        rate $_rate
		        channels 2
		        format $BROWSER_ALSA_FORMAT
		    }
		}
		ctl.omdrc_dac { type oss; device "$_mixer" }

		# Belt and braces: if the browser ignores --alsa-output-device, "default"
		# still lands on the pinned DAC instead of plug:oss, which would follow
		# hw.snd.default_unit at a rate of the browser's choosing.
		pcm.!default pcm.omdrc_dac
		ctl.!default ctl.omdrc_dac
	EOF
	# Overriding "default" has to happen from a hook, not from the top-level file:
	# alsa.conf's own @hooks[0] loads /usr/local/etc/asound.conf (which defines
	# pcm.!default as plug:oss) AFTER the top-level file has been parsed, so a
	# pcm.!default written here directly is loaded first and then overwritten.
	# Appending a second hook puts our overrides last, where they win.
	cat > "$_alsa_tmpdir/asound.conf" <<-EOF
		# Generated per run by browser-nodrc/lib.sh — not a system file.
		<$_base>

		@hooks.1 {
		    func load
		    files [ "$_alsa_tmpdir/omdrc.conf" ]
		}
	EOF

	ALSA_CONFIG_PATH="$_alsa_tmpdir/asound.conf"
	export ALSA_CONFIG_PATH
	# Pin the backend explicitly rather than letting the probe run: PulseAudio is
	# ahead of ALSA in that order, and a probe is enough to autospawn a server
	# onto the DAC (see cmake/browser-audio.cmake for the guard against that).
	BROWSER_ALSA_FLAGS="--audio-backend=alsa --alsa-output-device=omdrc_dac"

	# If the prime/warm has been switched back on from the environment, it must
	# run at the rate we pinned: opening the DAC at some *other* rate to warm it
	# would relock the clock, which is the one thing this path exists to avoid.
	# (Pinning is also why the prime has nothing to do here even when enabled —
	# same rate in, same rate out, so there is no crystal switch to survive.)
	DAC_PRIME_RATE="$_rate"
	echo "browser-nodrc: ALSA shim -> $DAC_DEV pinned at ${_rate} Hz (alsa-lib resamples)" >&2
}

# Re-apply the DRC state captured by drc_bypass_begin.  Clears its own traps
# first so a TERM-then-EXIT sequence cannot restore twice.
_drc_restore() {
	trap - EXIT INT TERM
	[ -n "$_alsa_tmpdir" ] && rm -rf "$_alsa_tmpdir"
	browser_sndio_end          # must release the DAC before DRC is re-applied
	[ "${_prev_power:-off}" = "on" ] || return 0   # was off → leave it off
	echo "browser-nodrc: restoring DRC (${_prev_state:-default})" >&2
	# shellcheck disable=SC2086  # state args are intentionally word-split
	"$DRC" ${_prev_state:-restore}
}

# Wait until nothing holds the DAC, so the browser's first open cannot lose a
# race and be silent for the whole session.
#
# The DAC is single-open (vchans off, bitperfect on), and drc.sh only waits for
# BRUTEFIR to exit.  Everything else that can hold it is invisible to that wait:
# a previous browser still shutting down (its audio service can keep the device
# for seconds after the window closes), MPD playing through the direct output
# that `drc.sh off` has just enabled, or a stray player.  Chromium's ALSA backend
# does not retry a failed open — it logs "PcmOpen: <device>,Device busy" once and
# plays silently forever — so losing this race costs the whole session.
DAC_FREE_WAIT="${DAC_FREE_WAIT:-8}"        # seconds to wait for the DAC to be released

# PIDs holding $DAC_DEV, one per line.  fuser prints the device name on stderr
# and the pids on stdout, each with a mode suffix ("28758w"), so the digits have
# to be extracted rather than used as-is.
_dac_holders() {
	fuser "$DAC_DEV" 2>/dev/null | tr ' \t' '\n\n' \
	    | sed 's/[^0-9]//g' | grep -E '^[0-9]+$'
}

wait_dac_free() {
	command -v fuser >/dev/null 2>&1 || return 0     # cannot check; do not block
	_i=0
	while [ "$_i" -lt $(( DAC_FREE_WAIT * 4 )) ]; do
		if [ -z "$(_dac_holders)" ]; then
			[ "$_i" -gt 0 ] && echo "browser-nodrc: DAC released after $(( _i * 250 ))ms" >&2
			return 0
		fi
		_i=$(( _i + 1 ))
		sleep 0.25
	done
	# Still busy: say WHO, because the fix differs (quit the old browser / stop
	# playback), and let the browser start anyway rather than refusing to run.
	echo "browser-nodrc: WARNING — $DAC_DEV is still held after ${DAC_FREE_WAIT}s by:" >&2
	for _p in $(_dac_holders); do
		ps -p "$_p" -o pid= -o command= 2>/dev/null | cut -c1-100 | sed 's/^/    /' >&2
	done
	echo "  $BROWSER will fail its audio open and stay SILENT for this session." >&2
	echo "  Quit that process (a previous browser? MPD playing?) and relaunch." >&2
}

# Snapshot the current DRC state, arm the restore trap, then disable DRC.
drc_bypass_begin() {
	# `session` prints the persistent state restore would use, as key=value lines:
	# power=on|off and mode=<rate>|resamp|cdin (mode is last_arg's remembered form).
	_session=$("$DRC" session 2>/dev/null)
	_prev_power=$(printf '%s\n' "$_session" | sed -n 's/^power=//p')
	_prev_state=$(printf '%s\n' "$_session" | sed -n 's/^mode=//p')
	: "${_prev_power:=off}"
	trap _drc_restore EXIT INT TERM
	echo "browser-nodrc: disabling DRC for direct DAC output" >&2
	"$DRC" off
	# After the chain is down (the DAC is ours) but before the warm-up, which the
	# shim reconfigures: it pins the rate, so there is no crystal switch to prime.
	case "$BROWSER_AUDIO" in
		alsa)  browser_alsa_begin ;;
		sndio) browser_sndio_begin ;;
	esac
	# After the shim config exists but before the browser starts: the browser's
	# very first open must find the device free (see wait_dac_free).
	wait_dac_free
	if [ "$DAC_PRIME_CYCLES" != 0 ] || [ "$DAC_WARMUP_SECS" != 0 ]; then
		echo "browser-nodrc: priming + warming DAC clock (${DAC_PRIME_CYCLES} bounces + ${DAC_WARMUP_SECS}s hold)" >&2
		drc_warm_dac
	fi
}
