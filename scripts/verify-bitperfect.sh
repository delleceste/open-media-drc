#!/usr/bin/env bash
set -euo pipefail

# verify-bitperfect.sh — prove that audio bytes traverse a stage of the chain
# UNCHANGED, by feeding a known signal in one end and capturing what comes out.
#
# It supports three taps and two signal sources, so you can measure individual
# links or whole sub-chains:
#
#   SOURCE  --source writer        built-in OSS writer -> --play device
#           --source mpd:OUTPUT    feed via MPD (enables MPD output OUTPUT and
#                                  plays a generated WAV; MPD is flow-controlled
#                                  by the sink, the authoritative real-world feed)
#
#   TAP     --tap usb              sniff the OKTO DAC's isochronous OUT endpoint
#                                  0x01 (the bytes that reach the DAC)        [root]
#           --tap loop:/dev/dsp.X  read an OSS loopback/capture node, e.g.
#                                  /dev/dsp.loop = what virtual_oss feeds brutefir
#
# Useful combinations
# -------------------
#   # 1. direct kernel/USB path (default): writer -> /dev/dsp.dac, sniff the DAC
#   sudo ./verify-bitperfect.sh
#
#   # 2. virtual_oss bridge: writer -> /dev/dsp.play, read /dev/dsp.loop
#   #    (needs virtual_oss running; use --paced because virtual_oss free-runs)
#   ./verify-bitperfect.sh --play /dev/dsp.play --tap loop:/dev/dsp.loop --paced
#
#   # 3. whole MPD direct path: MPD OKTO-DAC output -> /dev/dsp.dac, sniff the DAC
#   sudo ./verify-bitperfect.sh --source mpd:OKTO-DAC --tap usb
#
#   # 4. MPD -> virtual_oss bridge (front half of the DRC chain)
#   ./verify-bitperfect.sh --source mpd:DRC-native --tap loop:/dev/dsp.loop
#
# Notes
# -----
# * The DRC path as a whole is NOT bit-perfect: brutefir convolves the FIR room
#   correction. Only the DIRECT path and individual non-convolving links are.
# * virtual_oss is bit-transparent in VALUE but free-runs (master = /dev/null),
#   so exact byte-COUNT requires a flow-controlled producer (MPD, or --paced and
#   even then a rare clock slip is expected). The comparator distinguishes a
#   benign timing slip from real value corruption.
# * Default signal is a deterministic near-silent (~-90 dBFS) counter; the L and
#   R channels differ (catches swaps). FULLSCALE=1 = loud full-range random
#   (disconnect the amp first).

# ── defaults ─────────────────────────────────────────────────────────────────
# /dev/dsp.dac is the DAC by role: FreeBSD pcm unit numbers follow attach
# order, so /dev/dsp0 is only the DAC by luck on a multi-card box.  The
# omdrc_sndlink service keeps the link pointed at the right one; without it
# (single-DAC box, or Linux) fall back to unit 0.
PLAY_DEV="$([ -e /dev/dsp.dac ] && echo /dev/dsp.dac || echo /dev/dsp0)"
RATE=44100
FRAMES=200000
TAP="usb"
SOURCE="writer"
PACED=0

usage() { sed -n '3,40p' "$0"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --play)    PLAY_DEV="$2"; shift 2;;
    --rate)    RATE="$2";     shift 2;;
    --frames)  FRAMES="$2";   shift 2;;
    --tap)     TAP="$2";      shift 2;;
    --source)  SOURCE="$2";   shift 2;;
    --paced)   PACED=1;       shift;;
    -h|--help) usage; exit 0;;
    # positional backward-compat: [rate] [frames]
    [0-9]*)    if [ "$RATE" = "44100" ]; then RATE="$1"; else FRAMES="$1"; fi; shift;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 1;;
  esac
done

TMP="$(mktemp -d /tmp/bp.XXXXXX)"
SRC="$TMP/src.raw"
WAV="$TMP/src.wav"
CAP="$TMP/cap.raw"
PCAP="$TMP/cap.pcap"
WRITER="$TMP/bpwrite"
READER="$TMP/bpread"
trap 'cleanup' EXIT
BP_OUTSTATE=""
cleanup() {
  [ "$SOURCE" != "${SOURCE#mpd:}" ] && restore_mpd
  rm -rf "$TMP"
}

say() { printf '\033[1m%s\033[0m\n' "$*"; }
err() { printf '\033[31m%s\033[0m\n' "$*" >&2; }
grn() { printf '\033[32m%s\033[0m\n' "$*"; }

# ── 1. test signal (raw always; wav when feeding MPD) ────────────────────────
say "Generating ${FRAMES}-frame S32_LE/${RATE} signal"
python3 - "$SRC" "$FRAMES" "$WAV" "$RATE" <<'PY'
import sys, struct, os, wave
path, n, wavpath, rate = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
full = os.environ.get("FULLSCALE") == "1"
buf = bytearray()
if full:
    import random; random.seed(0xC0FFEE)
    for i in range(n):
        buf += struct.pack("<ii", random.getrandbits(32)-2**31, random.getrandbits(32)-2**31)
else:
    for i in range(n):
        buf += struct.pack("<ii", i & 0xFFFF, (i*40503) & 0xFFFF)
open(path, "wb").write(buf)
# 32-bit PCM WAV (format tag 1) for MPD feeding — PCM payload identical to the raw
w = wave.open(wavpath, "wb")
w.setnchannels(2); w.setsampwidth(4); w.setframerate(rate)
w.writeframes(buf); w.close()
PY
SRC_SZ=$(stat -f%z "$SRC")
say "Source: $SRC_SZ bytes"

# ── 2. compile the OSS writer (format-guarded) and reader ────────────────────
cc -O2 -o "$WRITER" -x c - <<'C'
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/soundcard.h>
#include <sys/ioctl.h>
/* usage: bpwrite dsp rate raw [chunk_ms]  (chunk_ms>0 = rate-paced) */
int main(int argc, char **argv){
    if(argc<4){fprintf(stderr,"usage: %s dsp rate raw [chunk_ms]\n",argv[0]);return 2;}
    int rate=atoi(argv[2]); int chunk_ms = argc>4 ? atoi(argv[4]) : 0;
    int fd=open(argv[1],O_WRONLY); if(fd<0){perror("open dsp");return 1;}
    int fmt=AFMT_S32_LE;
    if(ioctl(fd,SNDCTL_DSP_SETFMT,&fmt)<0||fmt!=AFMT_S32_LE){fprintf(stderr,"FAIL: format coerced 0x%x -> NOT bit-perfect\n",fmt);return 3;}
    int ch=2; if(ioctl(fd,SNDCTL_DSP_CHANNELS,&ch)<0||ch!=2){fprintf(stderr,"FAIL: channels coerced %d\n",ch);return 3;}
    int sp=rate; if(ioctl(fd,SNDCTL_DSP_SPEED,&sp)<0||sp!=rate){fprintf(stderr,"FAIL: rate coerced %d (asked %d) -> resampling\n",sp,rate);return 3;}
    int in=open(argv[3],O_RDONLY); if(in<0){perror("open raw");return 1;}
    if(chunk_ms<=0){                              /* flat-out: hardware blocks us */
        char b[65536]; ssize_t n;
        while((n=read(in,b,sizeof b))>0){char*p=b;while(n>0){ssize_t w=write(fd,p,n);if(w<0){perror("write");return 1;}p+=w;n-=w;}}
    } else {                                      /* rate-paced for a free-running sink */
        long fpc=(long)rate*chunk_ms/1000, bpc=fpc*8; char *b=malloc(bpc);
        struct timespec t0; clock_gettime(CLOCK_MONOTONIC,&t0);
        long k=0; ssize_t n;
        while((n=read(in,b,bpc))>0){
            long tot_ns=t0.tv_nsec + (long)k*chunk_ms*1000000L;
            struct timespec tgt; tgt.tv_sec=t0.tv_sec + tot_ns/1000000000L; tgt.tv_nsec=tot_ns%1000000000L;
            clock_nanosleep(CLOCK_MONOTONIC,TIMER_ABSTIME,&tgt,NULL);
            char*p=b;while(n>0){ssize_t w=write(fd,p,n);if(w<0){perror("write");return 1;}p+=w;n-=w;} k++;
        }
    }
    ioctl(fd,SNDCTL_DSP_SYNC,NULL); close(fd); return 0;
}
C

cc -O2 -o "$READER" -x c - <<'C'
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/soundcard.h>
#include <sys/ioctl.h>
/* usage: bpread dsp rate nbytes outfile  (real-time paced by the capture node) */
int main(int argc, char **argv){
    if(argc<5){fprintf(stderr,"usage: %s dsp rate nbytes out\n",argv[0]);return 2;}
    int rate=atoi(argv[2]); long want=atol(argv[3]);
    int fd=open(argv[1],O_RDONLY); if(fd<0){perror("open cap");return 1;}
    int fmt=AFMT_S32_LE;
    if(ioctl(fd,SNDCTL_DSP_SETFMT,&fmt)<0||fmt!=AFMT_S32_LE){fprintf(stderr,"FAIL: cap format 0x%x\n",fmt);return 3;}
    int ch=2; if(ioctl(fd,SNDCTL_DSP_CHANNELS,&ch)<0||ch!=2){fprintf(stderr,"FAIL: cap channels %d\n",ch);return 3;}
    int sp=rate; if(ioctl(fd,SNDCTL_DSP_SPEED,&sp)<0||sp!=rate){fprintf(stderr,"FAIL: cap rate %d\n",sp);return 3;}
    int out=open(argv[4],O_WRONLY|O_CREAT|O_TRUNC,0644); char b[65536];
    while(want>0){size_t k=want<(long)sizeof b?(size_t)want:sizeof b; ssize_t n=read(fd,b,k); if(n<=0)break; ssize_t o=write(out,b,n);(void)o; want-=n;}
    return 0;
}
C

# ── 3. decoder + comparator helpers ──────────────────────────────────────────
cat > "$TMP/decode_usb.py" <<'PY'
import sys, re
out=bytearray(); in_out=False; in_frame=False
hdr=re.compile(r'\bEP=([0-9A-Fa-f]{8})'); hexline=re.compile(r'^ [0-9A-Fa-f]{4}  (.*)$')
for line in sys.stdin:
    if hdr.search(line):
        in_out = "SUBM-ISOC-EP=00000001" in line; in_frame=False; continue
    if not in_out: continue
    if "WRITE" in line and "frame[" in line: in_frame=True; continue
    if ("READ" in line and "frame[" in line) or line.lstrip().startswith("flags"): in_frame=False; continue
    if in_frame:
        m=hexline.match(line)
        if m:
            for t in m.group(1).split("|",1)[0].split():
                if len(t)==2 and t!="--": out.append(int(t,16))
open(sys.argv[1],"wb").write(bytes(out))
PY

cat > "$TMP/compare.py" <<'PY'
import sys
src=open(sys.argv[1],"rb").read(); cap=open(sys.argv[2],"rb").read()
print(f"source bytes : {len(src)}")
print(f"capture bytes: {len(cap)}")
if not cap: print("\033[31mNothing captured.\033[0m"); sys.exit(2)
po=min(8192,max(0,len(src)//4)); probe=src[po:po+4096]
pos=cap.find(probe)
if pos<0:
    n=min(len(src),len(cap)); i=0
    while i<n and src[i]==cap[i]: i+=1
    print(f"\033[31mSource not found in capture (matched {i} leading bytes). Bytes differ or capture gap.\033[0m")
    sys.exit(1)
si,ci=po,pos; matched=0; slips=0; WIN=8192
corrupt=False; underrun=False
while si<len(src)-256 and ci<len(cap)-256:
    if src[si]==cap[ci]: si+=1; ci+=1; matched+=1; continue
    needle=src[si:si+256]
    j=cap[ci:ci+WIN].find(needle)          # cap has extra bytes (duplicated) -> advance cap
    k=src[si:si+WIN].find(cap[ci:ci+256])  # cap dropped bytes -> advance src
    if j>0 and (k<0 or j<=k): ci+=j; slips+=1; continue
    if k>0: si+=k; slips+=1; continue
    # no resync: if the capture from here on is silence, it's an underrun/tail,
    # not value corruption (the producer fell behind the free-running sink).
    if not any(cap[ci:ci+4096]): underrun=True; break
    corrupt=True; break
print(f"aligned at capture offset {pos}; bytes value-matched: {matched}")
if corrupt:
    hx=lambda b,o:" ".join(f"{x:02x}" for x in b[o:o+8])
    print(f"\033[31mVALUE CORRUPTION at source offset {si}:\033[0m")
    print(f"  src : {hx(src,si)}"); print(f"  cap : {hx(cap,ci)}")
    sys.exit(1)
tail = "  (capture underran to silence at the end — producer fell behind the sink)" if underrun else ""
if slips==0 and not underrun:
    print(f"\033[32mBIT-PERFECT: {matched} contiguous bytes identical ✔\033[0m"); sys.exit(0)
if slips==0 and underrun:
    print(f"\033[32mVALUE-EXACT: {matched} contiguous bytes identical, no value change.\033[0m{tail}")
    sys.exit(0)
print(f"\033[33mVALUE-TRANSPARENT but {slips} timing slip(s): sample VALUES never altered, "
      f"byte-count drifted.{tail}\n"
      f"Expected for a free-running sink (virtual_oss, -f /dev/null) unless the producer "
      f"is flow-controlled (MPD).\033[0m")
sys.exit(0)
PY

# ── MPD feed/restore ─────────────────────────────────────────────────────────
feed_mpd() {
  local out="$1"
  command -v mpc >/dev/null 2>&1 || { err "mpc not found"; return 1; }
  mpc status >/dev/null 2>&1 || { err "MPD not reachable"; return 1; }
  BP_OUTSTATE="$(mpc outputs 2>/dev/null)"
  mpc -q rm bp_backup 2>/dev/null || true
  mpc -q save bp_backup 2>/dev/null || true
  chmod 0755 "$TMP"; chmod 0644 "$WAV"
  mpc -q clear
  mpc enable only "$out" >/dev/null || { err "no such MPD output: $out"; return 1; }
  if ! mpc -q add "file://$WAV" 2>/dev/null; then
    err "MPD refused 'file://$WAV' (needs local-file permission)."
    err "Fallback: copy the WAV under music_directory, 'mpc update --wait', then add it."
    return 1
  fi
  mpc -q play
}
restore_mpd() {
  [ -n "$BP_OUTSTATE" ] || return 0
  mpc -q stop 2>/dev/null || true
  mpc -q clear 2>/dev/null || true
  mpc -q load bp_backup 2>/dev/null || true
  mpc -q rm   bp_backup 2>/dev/null || true
  echo "$BP_OUTSTATE" | while read -r l; do
    case "$l" in
      *"is enabled"*)  n=$(echo "$l" | sed -n 's/.*(\(.*\)) is enabled.*/\1/p');  [ -n "$n" ] && mpc -q enable  "$n" 2>/dev/null || true;;
      *"is disabled"*) n=$(echo "$l" | sed -n 's/.*(\(.*\)) is disabled.*/\1/p'); [ -n "$n" ] && mpc -q disable "$n" 2>/dev/null || true;;
    esac
  done
  say "MPD queue and outputs restored."
}

# ── locate the DAC (USB tap only) ────────────────────────────────────────────
start_usb_tap() {
  local loc bus daddr
  loc="$(sysctl -n dev.uaudio.0.%location 2>/dev/null || true)"
  [ -n "$loc" ] || { err "uaudio0 not found (DAC attached?)"; exit 1; }
  bus="$(echo "$loc"  | sed -n 's/.*bus=\([0-9]*\).*/\1/p')"
  daddr="$(echo "$loc" | sed -n 's/.*devaddr=\([0-9]*\).*/\1/p')"
  USBUS="usbus${bus}"; DADDR="$daddr"
  say "USB tap: $USBUS device $DADDR endpoint 0x01"
  sudo usbdump -i "$USBUS" -f "$DADDR" -s 65536 -w "$PCAP" >/dev/null 2>&1 &
  TAPPID=$!
}
stop_usb_tap() { sudo kill "$TAPPID" 2>/dev/null || true; wait "$TAPPID" 2>/dev/null || true
  sudo usbdump -r "$PCAP" -vv 2>/dev/null | python3 "$TMP/decode_usb.py" "$CAP"; }

start_loop_tap() {
  local dev="$1"
  [ -e "$dev" ] || { err "loopback device $dev not found (is virtual_oss running?)"; exit 1; }
  say "Loopback tap: reading $dev"
  local nb=$(( SRC_SZ + RATE*8 ))      # source + ~1 s margin
  timeout 30 "$READER" "$dev" "$RATE" "$nb" "$CAP" &
  TAPPID=$!
}
stop_loop_tap() { wait "$TAPPID" 2>/dev/null || true; }

# ── run ──────────────────────────────────────────────────────────────────────
case "$TAP" in
  usb)        start_usb_tap;;
  loop:*)     LOOPDEV="${TAP#loop:}"; start_loop_tap "$LOOPDEV";;
  *) err "unknown --tap: $TAP"; exit 1;;
esac
sleep 0.4

case "$SOURCE" in
  writer)
    [ -c "$PLAY_DEV" ] || { err "play device $PLAY_DEV not found"; exit 1; }
    if [ "$PACED" = "1" ]; then
      say "Playing (rate-paced) to $PLAY_DEV"
      "$WRITER" "$PLAY_DEV" "$RATE" "$SRC" 10 || { err "writer aborted (see FAIL above)"; exit 3; }
    else
      say "Playing (flat-out, hardware-clocked) to $PLAY_DEV"
      "$WRITER" "$PLAY_DEV" "$RATE" "$SRC" 0  || { err "writer aborted (see FAIL above)"; exit 3; }
    fi
    ;;
  mpd:*)
    say "Feeding via MPD output '${SOURCE#mpd:}'"
    feed_mpd "${SOURCE#mpd:}" || { err "MPD feed failed"; exit 1; }
    sleep "$(python3 -c "print($FRAMES/$RATE + 1.5)")"
    ;;
  *) err "unknown --source: $SOURCE"; exit 1;;
esac

sleep 0.4
case "$TAP" in usb) stop_usb_tap;; loop:*) stop_loop_tap;; esac

say "Comparing captured bytes to source ..."
python3 "$TMP/compare.py" "$SRC" "$CAP"
