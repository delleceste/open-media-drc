#!/usr/bin/env bash
# glitch-usbtap.sh — definitive, last-hop glitch check at the OKTO's USB endpoint.
#
# Captures the DAC's isochronous OUT endpoint (0x01) with usbdump for a bounded
# window, then finds where the host failed to feed the DAC on time.  This sits
# downstream of MPD, virtual_oss and brutefir, so it catches a starvation glitch
# regardless of which stage caused it.
#
# Detection is HEADER-ONLY and therefore scales to multi-minute captures:
# `usbdump -r` (no -v) prints one line per transfer with a microsecond timestamp
# and the submitted length, e.g.
#     10:43:05.646324 usbus0.4 SUBM-ISOC-EP=00000001,SPD=HIGH,NFR=32,SLEN=6144,...
# At 192 kHz these arrive every ~4 ms with SLEN=6144.  We flag:
#   * TIMING GAPS  — a gap between transfers >> the nominal interval (the host
#                    stalled); logged per-event with its real timestamp.
#   * SHORT FRAMES — SLEN below nominal (the host had less than a full block).
# Each anomaly is appended to glitch.log as stage=usb so glitch-analyze.py can
# tell whether the dropouts are periodic or random.
#
# Blind spot: a "silence-insertion" underrun (host submits a full-length block of
# zeros) keeps timing AND SLEN nominal, so header-only analysis cannot see it.
# That needs payload inspection, which does not scale to minutes; run a short
# capture and `scripts/verify-bitperfect.sh` if you suspect that mode.
#
# CPU note: the *capture* (usbdump -w) is I/O-bound and barely shows on top.
# The CPU cost is the *decode* pass, which runs after the window closes.
#
# Usage: glitch-usbtap.sh [seconds] [logfile]      (default 180 s)
#
set -u

base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DUR="${1:-180}"
LOG_FILE="${2:-${GLITCH_LOG:-$base_dir/glitch.log}}"
GAP_FACTOR="${GLITCH_GAP_FACTOR:-2.5}"     # flag a gap > this × the nominal interval
SHORT_FRAC="${GLITCH_SHORT_FRAC:-0.5}"     # flag SLEN below this fraction of nominal
                                           # (ignores the normal async feedback wobble)
TAP_TMP="${GLITCH_TMP:-/var/tmp}"          # NOT /tmp — a multi-minute pcap is large

err() { printf '\033[31m%s\033[0m\n' "$*" >&2; }
say() { printf '\033[1m%s\033[0m\n' "$*"; }

command -v usbdump >/dev/null 2>&1 || { err "usbdump not found (FreeBSD only)"; exit 1; }

# ── locate the DAC on the USB bus (same logic as verify-bitperfect.sh) ───────
loc="$(sysctl -n dev.uaudio.0.%location 2>/dev/null || true)"
[ -n "$loc" ] || { err "uaudio0 not found (DAC attached?)"; exit 1; }
bus="$(echo "$loc"   | sed -n 's/.*bus=\([0-9]*\).*/\1/p')"
daddr="$(echo "$loc" | sed -n 's/.*devaddr=\([0-9]*\).*/\1/p')"
USBUS="usbus${bus}"

RATE="$(sysctl -n dev.pcm.0.feedback_rate 2>/dev/null || true)"
case "$RATE" in ''|*[!0-9]*) RATE=44100 ;; esac

# NB: FreeBSD mktemp only substitutes TRAILING Xs, so no .pcap suffix here.
PCAP="$(mktemp "$TAP_TMP/usbtap.XXXXXXXX")" || { err "mktemp failed in $TAP_TMP"; exit 1; }
PYSCRIPT="$(mktemp "$TAP_TMP/usbtap-decode.XXXXXXXX")" || { err "mktemp failed"; exit 1; }
cleanup() {
  rm -f "$PYSCRIPT" 2>/dev/null
  [ -n "${GLITCH_KEEP_PCAP:-}" ] || sudo rm -f "$PCAP" 2>/dev/null
}
trap cleanup EXIT

say "USB tap: $USBUS dev $daddr EP 0x01 — ${DUR}s @ ${RATE} Hz  (pcap: $PCAP)"
sudo timeout "$DUR" usbdump -i "$USBUS" -f "$daddr" -s 65536 -w "$PCAP" >/dev/null 2>&1 || true
# usbdump runs as root → the pcap is root-owned; make it readable for the decode.
sudo chmod a+r "$PCAP" 2>/dev/null || true
[ -s "$PCAP" ] || { err "no capture (permission? wrong bus?)"; exit 1; }
say "captured $(du -h "$PCAP" | cut -f1); decoding headers…"

# ── decode: header-only stream → python finds gaps / short frames ────────────
# The decoder is written to a file (not `python3 - <<EOF`): a here-doc on
# `python3 -` would feed the PROGRAM as stdin and the piped data would never
# reach sys.stdin.  Same pattern as scripts/verify-bitperfect.sh.
cat > "$PYSCRIPT" <<'PY'
import sys, re, datetime, statistics
rate, gap_factor = int(sys.argv[1]), float(sys.argv[2])
short_frac = float(sys.argv[3])   # flag SLEN below this fraction of nominal

ts_re   = re.compile(r'^(\d{2}):(\d{2}):(\d{2})\.(\d+)')
slen_re = re.compile(r'SLEN=(\d+)')
today   = datetime.date.today()

times, slens = [], []
for line in sys.stdin:
    m = ts_re.match(line)
    s = slen_re.search(line)
    if not m or not s:
        continue
    h, mi, se, frac = m.groups()
    sec = int(h)*3600 + int(mi)*60 + int(se) + float("0." + frac)
    times.append(sec)
    slens.append(int(s.group(1)))

n = len(times)
if n < 10:
    print(f"transfers      : {n}")
    print("too few transfers to judge (is audio playing?)")
    print(f"@@SUMMARY transfers={n} verdict=INCONCLUSIVE")
    sys.exit(0)

# unwrap a midnight rollover, just in case
for i in range(1, n):
    if times[i] < times[i-1]:
        times[i] += 86400
span = times[-1] - times[0]

deltas  = [times[i] - times[i-1] for i in range(1, n)]
nominal = statistics.median(deltas)
nom_slen = statistics.mode(slens)
thresh   = nominal * gap_factor

def hhmmss(sec):
    sec %= 86400
    return f"{int(sec//3600):02d}:{int(sec%3600//60):02d}:{int(sec%60):02d}"

def epoch_of(sec):
    dt = datetime.datetime.combine(today, datetime.time()) + datetime.timedelta(seconds=sec % 86400)
    return int(dt.timestamp())

gaps = []
for i in range(1, n):
    d = times[i] - times[i-1]
    if d > thresh:
        gaps.append((times[i], d))

# A genuine underrun is a DRASTICALLY short/zero frame; small SLEN wobble around
# the nominal is the normal async-USB feedback shaving a few samples to track the
# DAC clock — that is NOT a glitch and must be ignored.
short_thresh = nom_slen * short_frac
shorts   = [(times[i], slens[i]) for i in range(n) if slens[i] <  short_thresh]
feedback = sum(1 for s in slens if s != nom_slen and s >= short_thresh)

obs_bps = sum(slens) / span if span else 0
exp_bps = rate * 8
print(f"transfers      : {n}   span {span:.1f}s   nominal {nominal*1000:.2f} ms / {nom_slen} B")
print(f"throughput     : {obs_bps:,.0f} B/s  (expected {exp_bps:,} B/s)")
print(f"timing gaps    : {len(gaps)} (> {thresh*1000:.1f} ms)"
      + (f"   max {max(g[1] for g in gaps)*1000:.1f} ms" if gaps else ""))
print(f"short frames   : {len(shorts)} (< {short_thresh:.0f} B = {short_frac:.0%} of nominal)")
print(f"feedback adj   : {feedback} (normal async ± clock tracking — ignored)")
verdict = "CLEAN" if not gaps and not shorts else "GLITCHES"
print(f"verdict        : {verdict}")

# machine lines: each anomaly carries its OWN timestamp/epoch so the analyzer
# sees when it actually happened, not when the decode ran.
print(f"@@SUMMARY transfers={n} span={span:.1f} gaps={len(gaps)} shorts={len(shorts)} "
      f"feedback={feedback} obs_bps={obs_bps:.0f} exp_bps={exp_bps} "
      f"nominal_ms={nominal*1000:.2f} verdict={verdict}")
for t, d in gaps:
    print(f"@@EVENT {epoch_of(t)} {hhmmss(t)} usbgap gap {d*1000:.1f} ms (nominal {nominal*1000:.2f})")
for t, sl in shorts[:200]:    # cap the flood if a long stretch is short
    print(f"@@EVENT {epoch_of(t)} {hhmmss(t)} usbshort SLEN {sl} (nominal {nom_slen})")
PY

report="$(sudo usbdump -r "$PCAP" 2>/dev/null \
  | grep 'SUBM-ISOC-EP=00000001' \
  | python3 "$PYSCRIPT" "$RATE" "$GAP_FACTOR" "$SHORT_FRAC")"

# ── show human lines; fold @@ machine lines into glitch.log ──────────────────
printf '%s\n' "$report" | grep -v '^@@'

now_iso() { date +%Y-%m-%dT%H:%M:%S; }
while IFS= read -r line; do
  case "$line" in
    '@@SUMMARY '*)
      printf '%s epoch=%s stage=usb kind=usbtap msg=%q\n' \
        "$(now_iso)" "$(date +%s)" "${line#@@SUMMARY }" >> "$LOG_FILE" 2>/dev/null || true
      ;;
    '@@EVENT '*)
      # @@EVENT <epoch> <hhmmss> <kind> <msg...>
      set -- ${line#@@EVENT }
      ep="$1"; hhmmss="$2"; kind="$3"; shift 3
      iso="$(date -r "$ep" +%Y-%m-%dT%H:%M:%S 2>/dev/null || echo "$hhmmss")"
      printf '%s epoch=%s stage=usb kind=%s msg=%q\n' \
        "$iso" "$ep" "$kind" "$*" >> "$LOG_FILE" 2>/dev/null || true
      ;;
  esac
done < <(printf '%s\n' "$report" | grep '^@@')
