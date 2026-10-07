#!/usr/bin/env python3
"""Measure the dynamic range of what is playing, the way the DR database does.

The DR versions page can say what other people's copies of a record measure;
this measures the copy on the wire. The tracks are fetched again -- a second
copy alongside the one MusicPD is streaming -- decoded, measured, and deleted
the moment each one is done, so at most one track sits on disk at a time and
none survives the job.

The meter is the TT Dynamic Range algorithm, as the foobar2000 Dynamic Range
Meter 1.1.1 logs in the database implement it (and as dr14_tmeter reproduces):

  - each channel is cut into 3-second blocks, the last one partial;
  - each block's RMS is sqrt(2 * sum(x^2) / block_length) -- the factor 2 makes
    a full-scale sine read 0 dB -- and its peak is its largest |x|;
  - the loudest 20% of blocks by RMS (at least one) give rms_upper, the root
    mean square of their RMS values;
  - the second-highest block peak is the reference peak: a single clipped
    transient cannot inflate the result;
  - DR per channel is 20*log10(peak / rms_upper), and the track's DR is the
    mean over channels, rounded;
  - the album's DR is the mean of its tracks' integer DR values, rounded --
    the "Official DR value" the database's logs close with.

Checked sample-for-sample against dr14_tmeter (compute_dr14.py), including
its quirks: 44 160-sample blocks at 44.1 kHz, a partial last block measured
over its own length, and Python's round-half-to-even.

Decoding is ffmpeg's, at the file's native rate and channel count: resampling
would move the peaks. Samples are read one block at a time, so a 24/192 track
of any length needs a few megabytes, not the gigabyte a whole decode would.

Run as a script it measures one file and prints JSON, which is how the panel
uses it -- in a subprocess at the lowest CPU priority, so a measurement can
never take cycles from the DRC convolution running on the same box.
"""
from __future__ import annotations

import json
import math
import os
from collections import deque
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

BLOCK_SECONDS = 3.0
TOP_FRACTION = 0.2


class DrMeterError(RuntimeError):
    """A file that could not be probed, decoded or measured."""


# ── the meter ────────────────────────────────────────────────────────────────

class Meter:
    """Block statistics for one track, fed a block of frames at a time."""

    def __init__(self, rate: int, channels: int) -> None:
        if rate <= 0 or channels <= 0:
            raise DrMeterError(f"unusable stream: {rate} Hz, {channels} ch")
        self.rate = rate
        self.channels = channels
        # At 44.1 kHz the reference meter counts 3 x 44 160 samples to a
        # block, not 3 x 44 100 -- a quirk carried over from the foobar2000
        # meter whose logs fill the database, so it is kept here too.
        self.block = int(BLOCK_SECONDS * (rate + (60 if rate == 44100 else 0)))
        self._rms2: list[np.ndarray] = []      # per block: 2*mean(x^2), per channel
        self._peak: list[np.ndarray] = []      # per block: max |x|, per channel
        self._sum2 = np.zeros(channels)        # for the whole-track RMS
        self._count = 0

    def feed(self, frames: np.ndarray) -> None:
        """One block of frames, shape (n, channels); n < block only at the end."""
        if frames.size == 0:
            return
        squares = np.square(frames, dtype=np.float64)
        total = squares.sum(axis=0)
        # The short last block is normalised by its own length, as the
        # reference does -- over a full block it would read quieter than it is.
        self._rms2.append(2.0 * total / frames.shape[0])
        self._peak.append(np.abs(frames).max(axis=0))
        self._sum2 += total
        self._count += frames.shape[0]

    def result(self) -> dict:
        if not self._rms2:
            raise DrMeterError("no audio")
        rms2 = np.vstack(self._rms2)            # (blocks, channels)
        peaks = np.vstack(self._peak)
        blocks = rms2.shape[0]
        top = max(1, int(blocks * TOP_FRACTION))

        per_channel = []
        for ch in range(self.channels):
            loudest = np.sort(rms2[:, ch])[-top:]
            rms_upper = math.sqrt(float(loudest.mean()))
            ordered = np.sort(peaks[:, ch])
            peak2 = float(ordered[-2] if blocks > 1 else ordered[-1])
            if rms_upper <= 0 or peak2 <= 0:
                per_channel.append(0.0)         # digital silence has no range
            else:
                per_channel.append(20.0 * math.log10(peak2 / rms_upper))

        exact = float(np.mean(per_channel))
        peak = float(peaks.max())
        # The whole-track RMS is the mean of the channels' RMS values, as the
        # reference reports it -- not the RMS of their pooled power.
        rms = float(np.mean(np.sqrt(2.0 * self._sum2 / self._count)))
        return {
            # Rounded as the reference rounds (Python's round, half to even),
            # so a track reads the integer the database's own tools print.
            "dr": int(round(exact)),
            "dr_exact": round(exact, 2),
            "peak_db": round(_db(peak), 2),
            "rms_db": round(_db(rms), 2),
            "seconds": round(self._count / self.rate, 1),
            "rate": self.rate,
            "channels": self.channels,
        }


class RollingEstimate:
    """TT-style DR of complete three-second PCM blocks in a bounded window.

    Only each block's energy and peak are retained, never the audio. This is
    an excerpt estimate, not the full-track or album DR printed by Meter.
    """

    def __init__(self, rate: int, channels: int, seconds: int = 60) -> None:
        if rate <= 0 or channels <= 0:
            raise DrMeterError("invalid PCM format")
        self.rate = rate
        self.channels = channels
        self.block = int(BLOCK_SECONDS * (rate + (60 if rate == 44100 else 0)))
        self.blocks = deque(maxlen=max(2, int(seconds / BLOCK_SECONDS)))
        self.total_blocks = 0
        self.count = 0
        self.sum2 = np.zeros(channels, dtype=np.float64)
        self.peak = np.zeros(channels, dtype=np.float64)

    def feed(self, frames: np.ndarray) -> bool:
        """Consume PCM and return true if a complete block was added."""
        completed = False
        offset = 0
        while offset < len(frames):
            part = frames[offset:offset + self.block - self.count]
            values = part.astype(np.float64, copy=False)
            self.sum2 += np.square(values).sum(axis=0)
            self.peak = np.maximum(self.peak, np.max(np.abs(values), axis=0))
            self.count += len(part)
            offset += len(part)
            if self.count == self.block:
                self.blocks.append((2.0 * self.sum2 / self.block, self.peak.copy()))
                self.total_blocks += 1
                self.count = 0
                self.sum2.fill(0)
                self.peak.fill(0)
                completed = True
        return completed

    def discard_partial(self) -> None:
        """Drop an incomplete block after lost PCM, preserving completed ones."""
        self.count = 0
        self.sum2.fill(0)
        self.peak.fill(0)

    def add_gap(self) -> None:
        """Add one empty time slot for a paused or stopped source."""
        self.discard_partial()
        self.blocks.append((np.zeros(self.channels), np.zeros(self.channels)))
        self.total_blocks += 1

    def mark_track_start(self) -> int:
        """Return the boundary without discarding the shared rolling history."""
        self.discard_partial()
        return self.total_blocks

    def result(self) -> dict | None:
        exact = blocks_dr(self.blocks)
        if exact is None:
            return None
        return {"dr": int(round(exact)), "dr_exact": round(exact, 2),
                "seconds": int(len(self.blocks) * BLOCK_SECONDS)}

    def history(self) -> list[list[list[float]]]:
        """Compact per-block [RMS squared, peak] pairs for the live timeline."""
        return [[rms2.tolist(), peak.tolist()] for rms2, peak in self.blocks]


class SubBlocks:
    """A PCM stream's statistics in short sub-blocks, numbered by frame, so a
    track's DR can be worked out the way Meter works it out on the file: in
    3-second blocks counted from the track's own first sample, the last one
    partial -- wherever in the stream that first sample turns out to be.

    The rolling estimate cuts the stream into blocks as it arrives, and where
    those cuts fall against a track is chance.  For the TT algorithm that is
    not a detail: it keeps the loudest 20 % of blocks and the second-highest
    block peak, a handful of blocks on a short track, and moving the cuts
    alone moved a 44-second track by half a DR.  Sub-blocks of 1/60 of a
    block (50 ms) let the cuts be placed afterwards, to within 25 ms of the
    track's start, once that start is known.

    The block's length is the source's, not the stream's: the reference counts
    3 x 44 160 samples at 44.1 kHz but exactly 3 s at any other rate, so a
    96 kHz file read here at 44.1 kHz has blocks of 132 300 frames, not
    132 480.  Kept at the stream's own length, the grid would drift 4 ms a
    block -- most of a second by the end of a long track."""

    PARTS = 60

    def __init__(self, rate: int, channels: int) -> None:
        if rate <= 0 or channels <= 0:
            raise DrMeterError("invalid PCM format")
        self.rate = rate
        self.channels = channels
        self.block = int(BLOCK_SECONDS * (rate + (60 if rate == 44100 else 0)))
        self.sub = self.block // self.PARTS
        self.frames = 0                 # frames fed so far: the stream's clock
        self.subs: deque = deque()      # (first frame, sum of squares, peak, frames)
        self._first = 0
        self._sum2 = np.zeros(channels, dtype=np.float64)
        self._peak = np.zeros(channels, dtype=np.float64)
        self._count = 0

    def feed(self, frames: np.ndarray) -> None:
        offset = 0
        while offset < len(frames):
            part = frames[offset:offset + self.sub - self._count]
            values = part.astype(np.float64, copy=False)
            if not self._count:
                self._first = self.frames
            self._sum2 += np.square(values).sum(axis=0)
            self._peak = np.maximum(self._peak, np.max(np.abs(values), axis=0))
            self._count += len(part)
            self.frames += len(part)
            offset += len(part)
            if self._count == self.sub:
                self.subs.append((self._first, self._sum2.copy(), self._peak.copy(), self._count))
                self._sum2.fill(0)
                self._peak.fill(0)
                self._count = 0

    def trim(self, before: int) -> None:
        """Forget the sub-blocks wholly before frame `before`."""
        while self.subs and self.subs[0][0] + self.subs[0][3] <= before:
            self.subs.popleft()

    def block_for(self, source_rate: int | None) -> float:
        """Frames of this stream in one block of a `source_rate` source."""
        if not source_rate or source_rate <= 0:
            return float(self.block)
        quirk = 60 if source_rate == 44100 else 0
        return BLOCK_SECONDS * self.rate * (source_rate + quirk) / source_rate

    def dr_between(self, start: int, end: int, block: float | None = None) -> tuple[float | None, float]:
        """(TT DR unrounded, seconds) of frames [start, end): blocks of
        `block` frames (default: this stream's own) from `start`, the last one
        partial, each sub-block counted in the block its middle falls in."""
        block = block or float(self.block)
        subs = list(self.subs)
        if self._count:
            subs.append((self._first, self._sum2, self._peak, self._count))
        groups: dict[int, list] = {}
        for s in subs:
            middle = s[0] + s[3] / 2
            if start <= middle < end:
                groups.setdefault(int((middle - start) // block), []).append(s)
        if not groups:
            return None, 0.0
        chosen = [s for g in groups.values() for s in g]
        rms2, peaks = [], []
        for _, group in sorted(groups.items()):
            count = sum(s[3] for s in group)
            rms2.append(2.0 * sum(s[1] for s in group) / count)
            peaks.append(np.max([s[2] for s in group], axis=0))
        seconds = sum(s[3] for s in chosen) / self.rate
        rms2, peaks = np.vstack(rms2), np.vstack(peaks)
        top = max(1, int(len(rms2) * TOP_FRACTION))
        per_channel = []
        for ch in range(self.channels):
            rms_upper = math.sqrt(float(np.sort(rms2[:, ch])[-top:].mean()))
            ordered = np.sort(peaks[:, ch])
            peak2 = float(ordered[-2] if len(ordered) > 1 else ordered[-1])
            per_channel.append(20.0 * math.log10(peak2 / rms_upper)
                               if rms_upper > 0 and peak2 > 0 else 0.0)
        return float(np.mean(per_channel)), seconds


def blocks_dr(blocks) -> float | None:
    """The TT DR of a run of (RMS squared, peak) blocks, unrounded; None for
    fewer than two blocks, which have no second-highest peak."""
    if len(blocks) < 2:
        return None
    rms2 = np.vstack([block[0] for block in blocks])
    peaks = np.vstack([block[1] for block in blocks])
    top = max(1, int(len(blocks) * TOP_FRACTION))
    per_channel = []
    for ch in range(rms2.shape[1]):
        rms_upper = math.sqrt(float(np.sort(rms2[:, ch])[-top:].mean()))
        peak2 = float(np.sort(peaks[:, ch])[-2])
        per_channel.append(20.0 * math.log10(peak2 / rms_upper)
                           if rms_upper > 0 and peak2 > 0 else 0.0)
    return float(np.mean(per_channel))


def _db(value: float) -> float:
    return 20.0 * math.log10(value) if value > 0 else -math.inf


def album_dr(track_drs: list[int]) -> int | None:
    """The album value: the mean of the tracks' integer DRs, rounded as the
    reference rounds it (dynamic_range_meter.py: int(round(sum / n)))."""
    values = [int(v) for v in track_drs if v is not None]
    if not values:
        return None
    return int(round(sum(values) / len(values)))


# ── decoding ─────────────────────────────────────────────────────────────────

def _wvunpack(path: str) -> list[str] | None:
    """The command that writes a .wv as WAV on stdout, for the files ffmpeg
    cannot open itself (WavPack self-extractors begin with an executable stub,
    "MZP"), or None when this is not one or wvunpack is not installed."""
    exe = shutil.which("wvunpack")
    if not exe or not path.lower().endswith(".wv"):
        return None
    return [exe, "-q", "-y", path, "-o", "-"]


def probe(path: str, ffprobe: str = "ffprobe") -> tuple[int, int]:
    """(sample_rate, channels) of the first audio stream."""
    try:
        cmd = [ffprobe, "-v", "error", "-select_streams", "a:0",
               "-show_entries", "stream=sample_rate,channels", "-of", "json", path]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=60, check=True).stdout
        except subprocess.CalledProcessError:
            unpack = _wvunpack(path)
            if not unpack:
                raise
            cmd[-1] = "pipe:0"
            with subprocess.Popen(unpack, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL) as feeder:
                try:
                    out = subprocess.run(cmd, stdin=feeder.stdout, capture_output=True,
                                         text=True, timeout=60, check=True).stdout
                finally:
                    feeder.kill()
        stream = json.loads(out)["streams"][0]
        return int(stream["sample_rate"]), int(stream["channels"])
    except (subprocess.SubprocessError, OSError, KeyError, IndexError,
            ValueError) as error:
        raise DrMeterError(f"cannot probe {os.path.basename(path)}: {error}")


def rate_source_is_unpacked(path: str, ffprobe: str) -> bool:
    """True when ffprobe cannot open `path` but wvunpack can feed it."""
    if not _wvunpack(path):
        return False
    return subprocess.run([ffprobe, "-v", "error", path], capture_output=True,
                          timeout=60).returncode != 0


def measure_file(path: str, ffmpeg: str = "ffmpeg",
                 ffprobe: str = "ffprobe") -> dict:
    """Decode `path` at its native rate and measure it."""
    rate, channels = probe(path, ffprobe)
    meter = Meter(rate, channels)
    frame_bytes = 4 * channels
    want = meter.block * frame_bytes
    # The context manager closes both pipes: the panel is a long-lived
    # process, and a descriptor leaked per track adds up over a year.
    decode = [ffmpeg, "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0",
              "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    feeder = None
    if rate_source_is_unpacked(path, ffprobe):
        feeder = subprocess.Popen(_wvunpack(path), stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL)
        decode[decode.index("-nostdin")] = "-hide_banner"
        decode[decode.index(path)] = "pipe:0"
    with subprocess.Popen(
            decode, stdin=feeder.stdout if feeder else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            while True:
                # A pipe hands back whatever it has; a block is only a block
                # once all of it has arrived.
                chunk = bytearray()
                while len(chunk) < want:
                    piece = process.stdout.read(want - len(chunk))
                    if not piece:
                        break
                    chunk.extend(piece)
                usable = len(chunk) - len(chunk) % frame_bytes
                if usable:
                    frames = np.frombuffer(bytes(chunk[:usable]), dtype="<f4")
                    meter.feed(frames.reshape(-1, channels))
                if len(chunk) < want:
                    break
            detail = process.stderr.read().decode("utf-8", "replace").strip()[:300]
            process.wait(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
            if feeder:
                feeder.kill()
                feeder.wait()
    if process.returncode != 0:
        raise DrMeterError(f"ffmpeg could not decode {os.path.basename(path)}: {detail}")
    return meter.result()


# ── an album, in the background ──────────────────────────────────────────────

class AlbumMeasurement:
    """Fetch, measure and delete the tracks of one record, one at a time.

    The transport and the meter are passed in -- `download(url, dest,
    progress, cancelled)` and `measure(path)` -- so the orchestration can be
    exercised without a network, an MPD or ffmpeg. `state()` is what the
    panel shows, and deliberately carries no URL: those stay in here.
    """

    DOWNLOAD_SHARE = 0.8    # of one track's progress bar; decoding is the rest

    def __init__(self, album: str, artist: str, tracks: list[dict],
                 download, measure, workdir_parent: str | None = None) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._download = download
        self._measure = measure
        self._parent = workdir_parent or tempfile.gettempdir()
        self._urls = [t["url"] for t in tracks]
        self._state = {
            "status": "queued",
            "album": album,
            "artist": artist,
            "tracks": [{"title": t.get("title", ""), "track": t.get("track"),
                        "disc": t.get("disc"), "status": "waiting",
                        "dr": None, "dr_exact": None, "peak_db": None,
                        "rms_db": None, "seconds": None, "error": ""} for t in tracks],
            "current": None,
            "fraction": 0.0,
            "message": "",
            "album_dr": None,
            "discs": {},
            "started": None,
            "finished": None,
        }
        self.workdir: str | None = None

    # -- control --

    def cancel(self) -> None:
        self._cancel.set()

    def state(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def _set(self, **changes) -> None:
        with self._lock:
            self._state.update(changes)

    def _track(self, index: int, **changes) -> None:
        with self._lock:
            self._state["tracks"][index].update(changes)

    # -- the work --

    def run(self) -> None:
        total = len(self._urls)
        self._set(status="running", started=time.time())
        try:
            self.workdir = tempfile.mkdtemp(prefix="omdrc-dr-", dir=self._parent)
            for index, url in enumerate(self._urls):
                if self._cancel.is_set():
                    break
                self._one(index, url, total)
        except Exception as error:                      # noqa: BLE001
            self._set(status="failed", message=str(error))
        finally:
            # Whatever happened, nothing downloaded outlives the job.
            if self.workdir:
                shutil.rmtree(self.workdir, ignore_errors=True)
            self._finish()

    def _one(self, index: int, url: str, total: int) -> None:
        title = self._state["tracks"][index]["title"] or f"track {index + 1}"
        path = os.path.join(self.workdir, f"track-{index + 1:03d}")
        base = index / total

        def progress(fraction: float) -> None:
            share = self.DOWNLOAD_SHARE * max(0.0, min(1.0, fraction))
            self._set(fraction=base + share / total,
                      message=f"{title}: downloading {int(fraction * 100)}%")

        self._set(current=index, fraction=base, message=f"{title}: downloading")
        self._track(index, status="downloading")
        try:
            self._download(url, path, progress, self._cancel.is_set)
            if self._cancel.is_set():
                self._track(index, status="cancelled")
                return
            self._track(index, status="measuring")
            self._set(fraction=base + self.DOWNLOAD_SHARE / total,
                      message=f"{title}: measuring")
            result = self._measure(path)
            self._track(index, status="done", **{k: result.get(k) for k in
                        ("dr", "dr_exact", "peak_db", "rms_db", "seconds")})
            # The card shows the album value as it builds, not only at the end.
            with self._lock:
                self._summarise()
        except Exception as error:                      # noqa: BLE001
            # One track that will not download or decode does not sink the
            # rest: the album value is then over the tracks that measured.
            self._track(index, status="failed", error=str(error)[:200])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
            self._set(fraction=(index + 1) / total)

    def _summarise(self) -> list:
        """Album and per-disc values over the tracks measured so far. Call
        with the lock held."""
        state = self._state
        measured = [t for t in state["tracks"] if t["status"] == "done"]
        state["album_dr"] = album_dr([t["dr"] for t in measured])
        discs: dict = {}
        for t in measured:
            if t["disc"]:
                discs.setdefault(str(t["disc"]), []).append(t["dr"])
        state["discs"] = ({d: album_dr(v) for d, v in discs.items()}
                          if len(discs) > 1 else {})
        return measured

    def _finish(self) -> None:
        with self._lock:
            state = self._state
            measured = self._summarise()
            if state["status"] == "running":
                state["status"] = "cancelled" if self._cancel.is_set() else "done"
            state["current"] = None
            state["finished"] = time.time()
            if state["status"] == "done":
                failed = len(state["tracks"]) - len(measured)
                state["fraction"] = 1.0
                state["message"] = (f"{len(measured)} track"
                                    f"{'' if len(measured) == 1 else 's'} measured"
                                    + (f", {failed} failed" if failed else ""))
            elif state["status"] == "cancelled":
                state["message"] = "cancelled"


AUDIO_SUFFIXES = (".flac", ".mp3", ".ogg", ".opus", ".wav", ".m4a", ".ape",
                  ".wv", ".aiff", ".aif")


def write_album_report(folder: str) -> int | None:
    """Measure the audio files in `folder` and write its dr14.txt.

    The report ends with the line `Official DR value: DR<n>`, which is what the
    local collection reads (mpd_library._dr14_average). Returns the album DR,
    or None when nothing in the folder could be measured (no file is written).
    """
    names = sorted(n for n in os.listdir(folder)
                   if n.lower().endswith(AUDIO_SUFFIXES)
                   and os.path.isfile(os.path.join(folder, n)))
    rows = []
    for name in names:
        try:
            rows.append((name, measure_file(os.path.join(folder, name))))
        except DrMeterError as error:
            # one line per track that could not be measured; the scan script logs it
            print(f"skipped {name}: {error}", file=sys.stderr)
    album = album_dr([r["dr"] for _, r in rows])
    if album is None:
        print("no audio file could be measured" if names else "no audio files", file=sys.stderr)
        return None
    rule = "-" * 80
    lines = ["Dynamic Range Meter (omdrc drmeter, TT Dynamic Range algorithm)",
             rule, "DR     Peak        RMS         Duration  Track", rule]
    for i, (name, r) in enumerate(rows, 1):
        seconds = int(round(r["seconds"]))
        lines.append(f"DR{r['dr']:<4d} {r['peak_db']:8.2f} dB {r['rms_db']:8.2f} dB"
                     f"  {seconds // 60}:{seconds % 60:02d}      {i:02d}-{name}")
    lines += [rule, f"Number of tracks:  {len(rows)}",
              f"Official DR value: DR{album}", rule]
    tmp = os.path.join(folder, ".dr14.txt.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, os.path.join(folder, "dr14.txt"))
    return album


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--album":
        album = write_album_report(argv[2])
        if album is not None:
            print(f"DR{album}")
        return 0 if album is not None else 1
    if len(argv) != 2:
        print(f"usage: {argv[0]} <audio file> | --album <folder>", file=sys.stderr)
        return 2
    try:
        print(json.dumps(measure_file(argv[1])))
    except DrMeterError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
