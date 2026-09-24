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
                self.blocks.append((2.0 * self.sum2 / self.block,
                                    self.peak.copy()))
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

    def result(self) -> dict | None:
        if len(self.blocks) < 2:
            return None
        rms2 = np.vstack([block[0] for block in self.blocks])
        peaks = np.vstack([block[1] for block in self.blocks])
        top = max(1, int(len(self.blocks) * TOP_FRACTION))
        per_channel = []
        for ch in range(self.channels):
            rms_upper = math.sqrt(float(np.sort(rms2[:, ch])[-top:].mean()))
            peak2 = float(np.sort(peaks[:, ch])[-2])
            per_channel.append(20.0 * math.log10(peak2 / rms_upper)
                               if rms_upper > 0 and peak2 > 0 else 0.0)
        exact = float(np.mean(per_channel))
        return {"dr": int(round(exact)), "dr_exact": round(exact, 2),
                "seconds": int(len(self.blocks) * BLOCK_SECONDS)}

    def history(self) -> list[list[list[float]]]:
        """Compact per-block [RMS squared, peak] pairs for the live timeline."""
        return [[rms2.tolist(), peak.tolist()] for rms2, peak in self.blocks]


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

def probe(path: str, ffprobe: str = "ffprobe") -> tuple[int, int]:
    """(sample_rate, channels) of the first audio stream."""
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,channels", "-of", "json", path],
            capture_output=True, text=True, timeout=60, check=True).stdout
        stream = json.loads(out)["streams"][0]
        return int(stream["sample_rate"]), int(stream["channels"])
    except (subprocess.SubprocessError, OSError, KeyError, IndexError,
            ValueError) as error:
        raise DrMeterError(f"cannot probe {os.path.basename(path)}: {error}")


def measure_file(path: str, ffmpeg: str = "ffmpeg",
                 ffprobe: str = "ffprobe") -> dict:
    """Decode `path` at its native rate and measure it."""
    rate, channels = probe(path, ffprobe)
    meter = Meter(rate, channels)
    frame_bytes = 4 * channels
    want = meter.block * frame_bytes
    # The context manager closes both pipes: the panel is a long-lived
    # process, and a descriptor leaked per track adds up over a year.
    with subprocess.Popen(
            [ffmpeg, "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0",
             "-f", "f32le", "-acodec", "pcm_f32le", "-"],
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
                        "rms_db": None, "error": ""} for t in tracks],
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
                        ("dr", "dr_exact", "peak_db", "rms_db")})
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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <audio file>", file=sys.stderr)
        return 2
    try:
        print(json.dumps(measure_file(argv[1])))
    except DrMeterError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
