#!/usr/bin/env python3
"""
DAC lock bench: repeated open/play/close cycles across sample rates, with a
per-cycle verdict on whether the DAC actually produced sound.

Why an analog capture is the only honest verdict: every host-side signal is
blind to this failure.  dev.pcm.N.feedback_rate, clock validity, channel
flags, underrun counters and the USB stream itself all look perfectly healthy
while the DAC routes silence -- that is the whole character of the bug.  So
the ground truth has to come from outside the computer.

Verdict sources, in decreasing order of usefulness:
  --monitor capture   record the DAC's analog output on a second sound card
                      and measure the tone.  Fully automatic.  Needs a cable.
  --monitor ask       you listen, and answer y/n per cycle.
  --monitor none      no verdict; just exercise the transitions and log the
                      machine-side state (useful with --trace).

Subcommands:
  run       the bench
  play      one file, once (for a quick manual check)
  listen    record and analyse without playing (level/noise-floor calibration)
"""

import argparse
import csv
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ossio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- machine state
def sysctl(name):
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def sysctl_set(name, value):
    subprocess.run(["sudo", "-n", "sysctl", "%s=%s" % (name, value)],
                   capture_output=True, text=True, timeout=5)


def usb_get_clock(dev, clockid):
    """GET_CUR on the UAC2 sample-frequency control -- what the DAC says it is."""
    if not dev:
        return None
    try:
        out = subprocess.run(
            ["sudo", "-n", "usbconfig", "-d", dev, "do_request",
             "0xA1", "0x01", "0x0100", hex(clockid << 8), "4"],
            capture_output=True, text=True, timeout=10)
        m = re.search(r"REQUEST = <([^>]*)>", out.stdout)
        if not m or not m.group(1).strip():
            return None
        b = bytes(int(x, 16) for x in m.group(1).split())
        return int.from_bytes(b, "little") if len(b) == 4 else None
    except Exception:
        return None


SYSLOG = "/var/log/messages"


def log_offset():
    """Byte offset into syslog, so a trace window is exact.

    NOT dmesg: the kernel message buffer here is ~96 KB and a single second of
    hw.usb.uaudio.debug=6 overruns it, evicting the uaudio20_set_speed lines
    before they can be read.  syslogd persists the same printf output to a file
    that does not wrap.
    """
    try:
        return os.path.getsize(SYSLOG)
    except OSError:
        return None


def log_since(offset):
    if offset is None:
        return ""
    try:
        with open(SYSLOG, "r", errors="replace") as f:
            f.seek(offset)
            return expand_repeats(f.read())
    except OSError:
        return ""


REPEAT_RE = re.compile(r"^(.*)syslogd: last message repeated (\d+) times\s*$")


def expand_repeats(text):
    """syslogd collapses identical consecutive lines; put them back.

    Without this, N identical SET_CUR writes appear as one line plus a
    'repeated N-1 times' note, and every count is wrong.
    """
    out, prev = [], None
    for line in text.splitlines():
        m = REPEAT_RE.match(line)
        if m and prev is not None:
            out.extend([prev] * int(m.group(2)))
        else:
            out.append(line)
            prev = line
    return "\n".join(out)


def streaming_uaudio_units(exclude):
    """Other uaudio pcm units currently streaming.

    hw.usb.uaudio.debug is global and the DPRINTF lines carry no device tag, so
    a second active uaudio device silently interleaves its callbacks into the
    trace.  On this host the ESI's capture (omdrc-cdin) does exactly that at
    ~128 lines/s.
    """
    busy = []
    try:
        subprocess.run(["sudo", "-n", "sysctl", "hw.snd.verbose=2"],
                       capture_output=True, text=True, timeout=5)
        txt = open("/dev/sndstat").read()
    except Exception:
        return busy
    finally:
        subprocess.run(["sudo", "-n", "sysctl", "hw.snd.verbose=0"],
                       capture_output=True, text=True, timeout=5)
    unit = None
    for line in txt.splitlines():
        m = re.match(r"pcm(\d+):", line)
        if m:
            unit = m.group(1)
        if "RUNNING" in line and unit is not None and unit != str(exclude):
            busy.append("pcm%s" % unit)
    return sorted(set(busy))


SETSPEED_RE = re.compile(r"uaudio20_set_speed: ifaceno=(\d+) clockid=(\d+) speed=(\d+)")
GETSPEED_RE = re.compile(r"uaudio20_get_speed: ifaceno=(\d+) clockid=(\d+) speed=(\d+)")


def parse_clock_traffic(text):
    """Return (writes, reads) as lists of (clockid, speed), in order."""
    return ([(int(m.group(2)), int(m.group(3))) for m in SETSPEED_RE.finditer(text)],
            [(int(m.group(2)), int(m.group(3))) for m in GETSPEED_RE.finditer(text)])


# ---------------------------------------------------------------- audio
def load_wav(path):
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 4 or w.getnchannels() != 2:
            raise SystemExit("%s: expected 32-bit stereo" % path)
        return w.getframerate(), w.readframes(w.getnframes())


def play(dev, rate, frames, dry=False):
    """Open, configure, write, drain, close.  Returns timing + granted params."""
    t0 = time.monotonic()
    d = ossio.Dsp(dev, "w", rate, 2, formats=(ossio.AFMT_S32_LE,))
    t_open = time.monotonic() - t0
    try:
        if d.rate != rate:
            return {"error": "device granted %d Hz, asked %d" % (d.rate, rate),
                    "granted_rate": d.rate, "open_s": t_open}
        t1 = time.monotonic()
        if not dry:
            d.write(frames)
            d.drain()
        return {"granted_rate": d.rate, "fmt": d.fmt_name,
                "open_s": t_open, "play_s": time.monotonic() - t1, "error": None}
    finally:
        d.close()


class Recorder(threading.Thread):
    def __init__(self, dev, rate, seconds):
        super().__init__(daemon=True)
        self.dev, self.rate, self.seconds = dev, rate, seconds
        self.data = None
        self.error = None
        self.started = threading.Event()

    def run(self):
        try:
            d = ossio.Dsp(self.dev, "r", self.rate, 2,
                          formats=(ossio.AFMT_S24_LE, ossio.AFMT_S32_LE, ossio.AFMT_S16_LE))
            try:
                # Drop whatever was already buffered so the window starts now.
                d.read_exact(d.frame_bytes * int(d.rate * 0.15))
                self.rate = d.rate
                self.started.set()
                raw = d.read_exact(d.frame_bytes * int(d.rate * self.seconds))
                self.data = ossio.to_float(raw, d.fmt, d.channels)
            finally:
                d.close()
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            self.started.set()


class KeyWatcher(threading.Thread):
    """Passive listening: the operator presses a key ONLY on a failure.

    Prompting per cycle caps a session at the operator's patience -- about
    forty trials.  An intermittent fault needs hundreds.  Inverting the
    interaction (silence is the event worth reporting, and a silent cycle is
    unmistakable against a 0.5 s gap) makes an unattended-length run possible
    with no capture hardware at all.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue()

    def run(self):
        try:
            for line in sys.stdin:
                self.q.put((time.monotonic(), line.strip()))
        except Exception:
            pass

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                return out


def attribute(press_t, windows, grace=3.0):
    """Which cycle was sounding (or had just sounded) when the key went down."""
    for step, t0, t1 in reversed(windows):
        if t0 <= press_t <= t1 + grace:
            return step
    return windows[-1][0] if windows else None


def _peak(spec, freqs):
    i = int(np.argmax(spec))
    if 0 < i < len(spec) - 1:  # parabolic sub-bin interpolation
        a, b, c = spec[i - 1], spec[i], spec[i + 1]
        denom = a - 2 * b + c
        delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
    else:
        delta = 0.0
    df = freqs[1] - freqs[0]
    return freqs[i] + delta * df, spec[i]


def analyse(x, rate, expect_hz, snr_min=15.0, tol=0.004):
    """x: (frames, ch) float.  Returns a verdict dict."""
    mono = x.mean(axis=1) if x.ndim > 1 else x
    n = len(mono)
    if n < rate // 4:
        return {"verdict": "NO-CAPTURE", "note": "capture too short"}

    a, b = int(n * 0.10), int(n * 0.90)
    seg = mono[a:b]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    freqs = np.fft.rfftfreq(len(seg), 1.0 / rate)

    hi = min(rate * 0.45, 20000.0)
    band = (freqs >= 150.0) & (freqs <= hi)
    peak_hz, peak_mag = _peak(spec[band], freqs[band])

    guard = np.abs(freqs[band] - peak_hz) > max(25.0, 4 * (freqs[1] - freqs[0]))
    floor = float(np.median(spec[band][guard])) or 1e-12
    snr = 20.0 * np.log10(peak_mag / floor)

    rms = float(np.sqrt(np.mean(seg ** 2)))
    dbfs = 20.0 * np.log10(max(rms, 1e-12) * np.sqrt(2.0))

    # onset: first 50 ms hop where the expected tone rises out of the floor
    hop = max(1, int(rate * 0.05))
    onset_ms = None
    k = 2.0 * np.pi * expect_hz / rate
    for h in range(0, n - hop, hop):
        w = mono[h:h + hop]
        t = np.arange(len(w))
        mag = abs(np.dot(w, np.exp(-1j * k * t))) / len(w)
        if mag > 4.0 * rms / 32.0 and mag > 1e-4:
            onset_ms = 1000.0 * h / rate
            break

    ratio = peak_hz / expect_hz if expect_hz else float("nan")
    if snr < snr_min:
        verdict = "SILENT"
    elif abs(ratio - 1.0) <= tol:
        verdict = "OK"
    else:
        verdict = "WRONG-RATE"
    return {"verdict": verdict, "peak_hz": round(float(peak_hz), 2),
            "expect_hz": expect_hz, "ratio": round(float(ratio), 5),
            "snr_db": round(float(snr), 1), "level_dbfs": round(float(dbfs), 1),
            "onset_ms": None if onset_ms is None else round(onset_ms, 1),
            "note": ""}


# ---------------------------------------------------------------- sequences
def build_sequence(kind, rates, target=44100):
    rates = list(rates)
    if kind == "into":
        seq = []
        for r in rates:
            if r != target:
                seq += [r, target]
        return seq
    if kind == "all":
        seq = []
        for i, r in enumerate(rates):
            for s in rates:
                if s != r:
                    seq += [r, s]
        return seq
    if kind == "sweep":
        return rates + rates[::-1][1:]
    if kind == "random":
        import random
        seq, prev = [], None
        for _ in range(len(rates) * 4):
            c = [r for r in rates if r != prev]
            prev = random.choice(c)
            seq.append(prev)
        return seq
    return [int(x) for x in kind.split(",")]


# ---------------------------------------------------------------- commands
def cmd_run(a):
    preflight(a, a.monitor == "capture")
    man = json.load(open(os.path.join(a.wav_dir, "manifest.json")))
    files = man["files"]
    rates = sorted(int(r) for r in files)
    if a.rates:
        want = [int(x) for x in a.rates.split(",")]
        rates = [r for r in rates if r in want]

    seq = build_sequence(a.sequence, rates, a.target)
    unknown = sorted({r for r in seq if str(r) not in files})
    if unknown:
        raise SystemExit("no WAV for rate(s) %s -- have %s.\n"
                         "Generate them with: python3 make-test-wavs.py --rates %s"
                         % (unknown, sorted(int(x) for x in files),
                            ",".join(str(u) for u in unknown)))
    seq = seq * a.cycles
    if not seq:
        raise SystemExit("empty sequence")

    cache = {}
    for r in set(seq):
        p = os.path.join(a.wav_dir, files[str(r)]["file"])
        cache[r] = load_wav(p)

    dur = man["seconds"] + 2 * man["lead"]
    print("bench: %d step(s) = %d cycle(s) x %d, play=%s monitor=%s"
          % (len(seq), a.cycles, len(seq) // max(a.cycles, 1), a.play_dev, a.monitor))
    print("       plan: %s" % " -> ".join(str(r) for r in seq[:len(seq) // max(a.cycles, 1)])
          + (" (x%d)" % a.cycles if a.cycles > 1 else ""))
    print("       rates: %s" % ", ".join(str(r) for r in rates))
    if a.monitor == "capture":
        print("       capture: %s @ %d Hz  (DAC analog out must be patched into it)"
              % (a.capture_dev, a.capture_rate))

    trace_was = None
    if a.trace:
        other = streaming_uaudio_units(a.pcm_unit)
        if other and not a.trace_anyway:
            raise SystemExit(
                "%s is streaming, and hw.usb.uaudio.debug is global with no\n"
                "per-device tag -- its callbacks would interleave into the trace\n"
                "and corrupt the SET_CUR counts.\n"
                "  Stop it first (on this host: omdrc-cdin holds the ESI), or\n"
                "  pass --trace-anyway if you will separate them by hand."
                % ", ".join(other))
        trace_was = sysctl("hw.usb.uaudio.debug")
        subprocess.run(["sudo", "-n", "conscontrol", "mute", "on"],
                       capture_output=True, text=True)
        sysctl_set("hw.usb.uaudio.debug", str(a.trace_level))
        print("       trace: hw.usb.uaudio.debug=%d, console muted "
              "(NOTE: tracing perturbs timing -- do not compare traced and "
              "untraced failure rates)" % a.trace_level)

    rows = []
    prev = None
    watcher = None
    windows = []
    flagged = set()
    if a.monitor == "watch":
        watcher = KeyWatcher()
        watcher.start()
        print("\n       WATCH MODE -- just listen.  Press ENTER the moment a cycle is")
        print("       SILENT (or type its step number, then ENTER).  'q' + ENTER quits.")
        print("       Everything you do not flag is recorded as audible.\n")
    try:
        for i, rate in enumerate(seq, 1):
            wrate, frames = cache[rate]
            expect = files[str(rate)]["tone_hz"]
            row = {"step": i, "from_rate": prev, "rate": rate,
                   "expect_hz": expect, "ts": time.strftime("%H:%M:%S")}

            row["clock_before"] = usb_get_clock(a.usb_dev, a.clock_id)
            row["feedback_before"] = sysctl("dev.pcm.%s.feedback_rate" % a.pcm_unit)
            dm0 = log_offset() if a.trace else None

            rec = None
            if a.monitor == "capture":
                rec = Recorder(a.capture_dev, a.capture_rate, dur + 1.2)
                rec.start()
                if not rec.started.wait(timeout=5):
                    print("  capture did not start", file=sys.stderr)

            t_play0 = time.monotonic()
            pinfo = play(a.play_dev, wrate, frames, dry=a.dry_run)
            t_play1 = time.monotonic()
            windows.append((i, t_play0, t_play1))
            row.update({k: pinfo.get(k) for k in ("granted_rate", "open_s", "play_s", "error")})

            if rec is not None:
                rec.join(timeout=dur + 8)
                if rec.error:
                    row.update({"verdict": "NO-CAPTURE", "note": rec.error})
                elif rec.data is None:
                    row.update({"verdict": "NO-CAPTURE", "note": "no data"})
                else:
                    row.update(analyse(rec.data, rec.rate, expect,
                                       snr_min=a.snr_min, tol=a.tol))
            elif a.monitor == "ask":
                replayed = 0
                while True:
                    ans = input("  step %d: %d Hz (%.0f Hz tone) -- audible? "
                                "[y=heard / n=silent / r=replay / q=quit] "
                                % (i, rate, expect)).strip().lower()
                    if ans == "r":
                        # A replay is another open at the same rate -- the very
                        # thing that "fixes" it by hand.  Answer for the FIRST
                        # play, not for the replay, or the failure rate is lost.
                        replayed += 1
                        print("      (replay %d -- still answer for the FIRST play)"
                              % replayed)
                        play(a.play_dev, wrate, frames, dry=a.dry_run)
                        continue
                    if ans == "q":
                        raise KeyboardInterrupt
                    if ans in ("y", "n"):
                        row["verdict"] = "OK" if ans == "y" else "SILENT"
                        row["replays"] = replayed
                        break
            elif a.monitor == "watch":
                for t, txt in watcher.drain():
                    if txt.lower() == "q":
                        raise KeyboardInterrupt
                    if txt.isdigit():
                        flagged.add(int(txt))
                    else:
                        st = attribute(t, windows)
                        if st is not None:
                            flagged.add(st)
                row["verdict"] = None      # decided after the run
            else:
                row["verdict"] = "UNKNOWN"

            row["clock_after"] = usb_get_clock(a.usb_dev, a.clock_id)
            row["feedback_after"] = sysctl("dev.pcm.%s.feedback_rate" % a.pcm_unit)

            if a.trace:
                new = log_since(dm0)
                writes, reads = parse_clock_traffic(new)
                row["setcur"] = ";".join("%d:%d" % w for w in writes)
                row["setcur_n"] = len(writes)
                row["getcur_n"] = len(reads)
                row["dup_setcur"] = int(len(writes) > 1)

            if a.monitor == "watch":
                print("  %3d  %7s -> %-7s  %4.0f Hz tone%s"
                      % (i, prev if prev else "-", rate, expect,
                         "  [flagged]" if i in flagged else ""))
                rows.append(row)
                prev = rate
                if a.gap > 0:
                    time.sleep(a.gap)
                continue

            v = row.get("verdict", "?")
            mark = {"OK": "  ok", "SILENT": "SILENT", "WRONG-RATE": " RATE",
                    "NO-CAPTURE": "  n/a", "UNKNOWN": "    ?"}.get(v, v)
            extra = ""
            if "peak_hz" in row:
                extra = " peak %8.2f Hz  ratio %.5f  snr %5.1f dB  lvl %6.1f dBFS" % (
                    row["peak_hz"], row["ratio"], row["snr_db"], row["level_dbfs"])
                if row.get("onset_ms") is not None:
                    extra += "  onset %6.1f ms" % row["onset_ms"]
            if a.trace:
                extra += "  SET_CUR[%s]" % row.get("setcur", "")
            print("  %3d  %7s -> %-7s %s%s"
                  % (i, prev if prev else "-", rate, mark, extra))
            if row.get("error"):
                print("       play error: %s" % row["error"])

            rows.append(row)
            prev = rate
            if a.gap > 0:
                time.sleep(a.gap)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        if a.trace:
            sysctl_set("hw.usb.uaudio.debug", trace_was or "0")
            subprocess.run(["sudo", "-n", "conscontrol", "mute", "off"],
                           capture_output=True, text=True)

    if watcher is not None:
        # give a late reaction to the final cycle a chance to land
        time.sleep(1.5)
        for t, txt in watcher.drain():
            if txt.isdigit():
                flagged.add(int(txt))
            elif txt.lower() != "q":
                st = attribute(t, windows)
                if st is not None:
                    flagged.add(st)
        for r in rows:
            r["verdict"] = "SILENT" if r["step"] in flagged else "OK"
        if flagged:
            print("\n  flagged silent: %s" % sorted(flagged))

    if rows:
        cols = sorted({k for r in rows for k in r})
        order = ["step", "ts", "from_rate", "rate", "granted_rate", "verdict",
                 "peak_hz", "expect_hz", "ratio", "snr_db", "level_dbfs", "onset_ms",
                 "clock_before", "clock_after", "feedback_before", "feedback_after",
                 "open_s", "play_s", "setcur_n", "dup_setcur", "setcur", "getcur_n",
                 "error", "note"]
        cols = [c for c in order if c in cols] + [c for c in cols if c not in order]
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        summarise(rows, a.csv)


def summarise(rows, csvpath):
    print("\n" + "=" * 68)
    tot = len(rows)
    by = {}
    for r in rows:
        by.setdefault(r.get("verdict", "?"), []).append(r)
    for v in sorted(by):
        print("  %-11s %3d / %d" % (v, len(by[v]), tot))

    bad = by.get("SILENT", []) + by.get("WRONG-RATE", [])
    if bad:
        print("\n  failures by transition:")
        agg = {}
        for r in bad:
            agg.setdefault((r.get("from_rate"), r["rate"]), 0)
            agg[(r.get("from_rate"), r["rate"])] += 1
        att = {}
        for r in rows:
            att.setdefault((r.get("from_rate"), r["rate"]), 0)
            att[(r.get("from_rate"), r["rate"])] += 1
        for k in sorted(agg, key=lambda k: -agg[k]):
            print("    %7s -> %-7s  %d / %d" % (k[0], k[1], agg[k], att[k]))

    if any("dup_setcur" in r for r in rows):
        print("\n  duplicate SET_CUR vs verdict:")
        tab = {}
        for r in rows:
            if "dup_setcur" not in r:
                continue
            tab.setdefault((r["dup_setcur"], r.get("verdict")), 0)
            tab[(r["dup_setcur"], r.get("verdict"))] += 1
        for k in sorted(tab):
            print("    dup=%d  %-11s %d" % (k[0], k[1], tab[k]))
        print("  (a strong association here is the direct evidence for the")
        print("   duplicate-write hypothesis)")
    print("\n  csv -> %s" % csvpath)


def cmd_play(a):
    preflight(a, False)
    man = json.load(open(os.path.join(a.wav_dir, "manifest.json")))
    info = man["files"][str(a.rate)]
    rate, frames = load_wav(os.path.join(a.wav_dir, info["file"]))
    r = play(a.play_dev, rate, frames)
    print(json.dumps(r, indent=2))


def cmd_listen(a):
    rec = Recorder(a.capture_dev, a.capture_rate, a.seconds)
    rec.start()
    rec.join(timeout=a.seconds + 8)
    if rec.error:
        raise SystemExit(rec.error)
    print(json.dumps(analyse(rec.data, rec.rate, a.expect_hz,
                             snr_min=a.snr_min, tol=a.tol), indent=2))


def preflight(a, need_capture):
    for path, what in ((a.play_dev, "playback"),
                       (a.capture_dev, "capture") if need_capture else (None, None)):
        if not path:
            continue
        if not os.path.exists(path):
            raise SystemExit("%s (%s device) does not exist" % (path, what))
        who = ossio.holders(path)
        if who:
            raise SystemExit(str(ossio.DeviceBusy(path, who)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wav-dir", default=os.path.join(HERE, "wavs"))
    ap.add_argument("--play-dev", default="/dev/dsp1")
    ap.add_argument("--capture-dev", default="/dev/dsp0")
    ap.add_argument("--capture-rate", type=int, default=44100)
    ap.add_argument("--usb-dev", default="ugen0.3", help="ugenB.D of the DAC ('' to skip clock reads)")
    ap.add_argument("--clock-id", type=int, default=41)
    ap.add_argument("--pcm-unit", default="1")
    ap.add_argument("--snr-min", type=float, default=15.0)
    ap.add_argument("--tol", type=float, default=0.004)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--monitor", choices=("capture", "watch", "ask", "none"),
                   default="watch",
                   help="watch: listen passively, flag only failures (best without "
                        "capture hardware); capture: automatic; ask: prompt per cycle")
    r.add_argument("--sequence", default="into",
                   help="into | all | sweep | random | comma-separated rate list")
    r.add_argument("--target", type=int, default=44100, help="rate 'into' switches to")
    r.add_argument("--rates", default="", help="restrict to these rates")
    r.add_argument("--cycles", type=int, default=1)
    r.add_argument("--gap", type=float, default=1.0, help="idle seconds between cycles")
    r.add_argument("--trace", action="store_true",
                   help="enable uaudio DPRINTF tracing and record the SET_CUR sequence")
    r.add_argument("--trace-level", type=int, default=6)
    r.add_argument("--trace-anyway", action="store_true",
                   help="trace even though another uaudio device is streaming")
    r.add_argument("--dry-run", action="store_true", help="open/close without writing audio")
    r.add_argument("--csv", default=os.path.join(HERE, "dac-bench.csv"))
    r.set_defaults(func=cmd_run)

    p = sub.add_parser("play")
    p.add_argument("rate", type=int)
    p.set_defaults(func=cmd_play)

    l = sub.add_parser("listen")
    l.add_argument("--seconds", type=float, default=3.0)
    l.add_argument("--expect-hz", type=float, default=997.0)
    l.set_defaults(func=cmd_listen)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
