"""Bit-perfect verification workflows for the omdrcctrl web page.

The Flask process stays unprivileged.  Everything here shells out to the
scripts in `scripts/` — the same ones a person runs by hand — so the page can
never produce a verdict the command line would not.  Only the USB tap needs
root, and it escalates inside those scripts with `sudo -n`.

Job plumbing (the `Job` dataclass, the SSE generator, line-buffered
`run_command`) is inherited from `ConfigurationManager` rather than copied:
the two pages then behave identically for the operator, and the shared
`OPERATION_LOCK` means a filter publication and a tap run cannot fight over
the DAC.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime
from typing import Callable

from configuration import ConfigurationManager, Job
from configuration import Settings as ConfigurationSettings


# The rates the DRC actually offers.  Used only as the fallback when the site
# tree cannot be read; the real list is enumerated from the installed
# brutefir-<rate>.conf files, exactly as drc.sh's geometry_rates() does, so a
# geometry offering a different set is reflected without a code change.
CANONICAL_RATES = (44100, 48000, 88200, 96000, 192000)

# 30 s is the documented cross-OS asset length.  192 kHz is capped at 10 s
# because the FreeBSD tap decodes `usbdump -vv` TEXT, and 30 s there is several
# hundred MB of hex dump (doc/BIT-PERFECT-VERIFICATION.md, "Start with the
# canonical 44100 Hz asset").
DEFAULT_SECONDS = {192000: 10}
DEFAULT_SECONDS_OTHERWISE = 30

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,120}$")
_RESULT = re.compile(r"^@@RESULT\s+(.*)$")


@dataclass
class Settings:
    enabled: bool = True
    tools_root: Path = Path(__file__).resolve().parents[2] / "scripts"
    generator: Path = (Path(__file__).resolve().parents[2] / "tests"
                       / "gen-bitperfect-wav.py")
    results_root: Path = Path("/var/tmp/omdrc-bitperfect/results")
    assets_root: Path = Path("/var/tmp/omdrc-bitperfect/assets")
    state_root: Path = Path("/var/tmp/omdrc-bitperfect/jobs")
    site_root: Path = Path("/usr/local/etc/open-media-drc")
    music_root: Path | None = None
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    run_timeout: int = 900


class BitPerfectManager(ConfigurationManager):
    def __init__(self, settings: Settings, env: Callable[[], dict]):
        # ConfigurationManager only needs state_root off its Settings; the
        # bit-perfect settings live alongside it.
        super().__init__(ConfigurationSettings(state_root=settings.state_root), env)
        self.bp = settings
        self._runner_module = None
        for directory in (settings.results_root, settings.assets_root):
            directory.mkdir(parents=True, exist_ok=True)

    # ── tools ───────────────────────────────────────────────────────────────

    def _tool(self, name: str) -> Path:
        path = self.bp.tools_root / name
        if not path.exists():
            raise RuntimeError(
                f"{name} is not installed at {self.bp.tools_root}. The tap "
                "suite ships with the panel; reinstall, or point "
                "[bitperfect] tools_root at a checkout.")
        return path

    def _runner(self):
        """The runner module, imported in-process for its read-only helpers.

        Only `discover()` and friends are used this way — anything that starts
        a capture or drives MPD goes through a subprocess, so a run is always
        exactly the command line a person would type."""
        if getattr(self, "_runner_module", None) is None:
            import importlib.util
            path = self._tool("bitperfect_runner.py")
            spec = importlib.util.spec_from_file_location("bp_runner", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._runner_module = module
        return self._runner_module

    def _capture(self, argv: list[str], timeout: int = 60) -> dict:
        """Run a tool that prints one JSON object and return it."""
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, env=self.env())
        if not r.stdout.strip():
            raise RuntimeError((r.stderr or "no output").strip()[:400])
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            raise RuntimeError((r.stdout + r.stderr).strip()[:400])

    # ── rates and assets ────────────────────────────────────────────────────

    def rates(self) -> list[int]:
        found: set[int] = set()
        configs = self.bp.site_root / "configs"
        if configs.is_dir():
            for conf in configs.glob("*/brutefir-*.conf"):
                m = re.match(r"brutefir-(\d+)(?:@.*)?\.conf$", conf.name)
                if m:
                    found.add(int(m.group(1)))
        return sorted(found) or list(CANONICAL_RATES)

    @staticmethod
    def default_seconds(rate: int) -> int:
        return DEFAULT_SECONDS.get(rate, DEFAULT_SECONDS_OTHERWISE)

    def asset_name(self, rate: int, bits: int, seconds: int) -> str:
        return f"bitperfect-test-{rate}-s{bits}-stereo-{seconds}s.wav"

    def assets(self) -> list[dict]:
        """One row per offered rate, whether or not the file exists yet."""
        rows = []
        for rate in self.rates():
            seconds = self.default_seconds(rate)
            for bits in (32,):
                name = self.asset_name(rate, bits, seconds)
                path = self.bp.assets_root / name
                row = {"rate": rate, "bits": bits, "seconds": seconds,
                       "name": name, "present": path.is_file()}
                if row["present"]:
                    row["bytes"] = path.stat().st_size
                    row["sha256"] = _sha256(path)
                rows.append(row)
        # Anything else the user generated or loaded, listed after the grid.
        # Loader-owned WAVs are represented by material(), not here.  In
        # particular, exposing a FLAC's decoded reference WAV as independent
        # material would let a user accidentally bypass the decoder they meant
        # to test.
        loader_owned: set[str] = set()
        root = self.bp.assets_root.resolve()
        for item in self.material():
            for key in ("source", "wav"):
                try:
                    candidate = Path(item.get(key, "")).resolve()
                except (OSError, TypeError):
                    continue
                if candidate.parent == root:
                    loader_owned.add(candidate.name)
        known = {r["name"] for r in rows}
        for path in sorted(self.bp.assets_root.glob("*.wav")):
            if path.name not in known and path.name not in loader_owned:
                rows.append({"name": path.name, "present": True, "extra": True,
                             "bytes": path.stat().st_size,
                             "sha256": _sha256(path)})
        return rows

    def generate_asset(self, job: Job, rate: int, bits: int, seconds: int) -> None:
        if rate <= 0 or bits not in (16, 24, 32) or not (1 <= seconds <= 600):
            raise ValueError("invalid rate, bit depth or duration")
        name = self.asset_name(rate, bits, seconds)
        target = self.bp.assets_root / name
        job.write(f"Generating {name} — the signal is a near-silent counter "
                  "whose every (L,R) pair is unique, so any altered, dropped "
                  "or duplicated sample is detectable at any offset.")
        self.run_command(job, [
            "python3", str(self.bp.generator),
            "--rate", str(rate), "--bits", str(bits),
            "--frames", str(rate * seconds), str(target)], timeout=300)
        job.write(f"Asset ready: {target}")

    # ── material (any WAV/FLAC) ─────────────────────────────────────────────

    def load_material(self, job: Job, source: Path) -> dict:
        """Decode an arbitrary track into a reference and describe it."""
        job.write(f"Loading {source.name}")
        info = self._capture([
            "python3", str(self._tool("bitperfect_material.py")), "load",
            str(source), "--out-dir", str(self.bp.assets_root)], timeout=600)
        if not info.get("ok"):
            raise RuntimeError(info.get("error", "could not load the file"))
        job.write(f"{info['name']}: {info['rate']} Hz, {info['bits']}-bit, "
                  f"{info['channels']}ch, {info['seconds']} s "
                  f"(decoder: {info['decoder']})")
        job.write(f"alignment anchor at byte {info['anchor_offset']}"
                  + ("" if info["anchor_unique"] else " — NOT unique"))
        if info.get("warning"):
            job.write(f"WARNING: {info['warning']}")
        (self.bp.assets_root / f".{_slug(source.name)}.material.json").write_text(
            json.dumps(info, indent=2) + "\n")
        return info

    def material(self) -> list[dict]:
        rows = []
        for path in sorted(self.bp.assets_root.glob(".*.material.json")):
            try:
                rows.append(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        return rows

    def resolve_material(self, requested: str) -> Path:
        """Resolve only material that the page has already admitted.

        Generated assets are addressed by basename.  Loaded files are
        addressed by the exact ``source`` recorded by load_material(), after
        the upload/music-root checks have run.  Keeping this check here means
        a caller cannot bypass those checks by posting an arbitrary absolute
        path directly to /api/run.
        """
        if not requested:
            raise ValueError("choose test material first")
        if "/" not in requested and "\\" not in requested:
            path = (self.bp.assets_root / requested).resolve()
            if path.is_file() and path.parent == self.bp.assets_root.resolve():
                return path
        admitted = {str(row.get("source", "")): row for row in self.material()}
        if requested in admitted:
            path = Path(requested).resolve()
            allowed_roots = [self.bp.assets_root.resolve()]
            if self.bp.music_root is not None:
                allowed_roots.append(self.bp.music_root.resolve())
            if path.is_file() and any(
                    path.parent == root or root in path.parents
                    for root in allowed_roots):
                return path
        raise ValueError(
            "material is not in the bit-perfect library; load it from the "
            "configured music library, or upload it, before running")

    # ── chain readiness ─────────────────────────────────────────────────────

    def readiness(self) -> dict:
        """Everything that decides whether a run can produce a real verdict."""
        state: dict = {"os": platform.system(), "blocking": [], "warnings": []}

        try:
            state["dac"] = self._runner().discover()
            state["dac_holder"] = self._runner().dac_busy(state["dac"])
        except Exception as error:
            state["dac"] = None
            state["dac_holder"] = ""
            state["blocking"].append(f"the DAC could not be located: {error}")

        state["renderer"] = self._runner().running_renderer()

        brutefir = subprocess.run(["pgrep", "-x", "brutefir"],
                                  capture_output=True, text=True)
        state["brutefir"] = brutefir.returncode == 0
        if state["brutefir"]:
            state["blocking"].append(
                "brutefir is convolving the room-correction filter, so the DRC "
                "path is not bit-perfect by design. Stop the chain "
                "(drc.sh off) before verifying.")

        state["sudo"] = _sudo_available(self.env())
        if not state["sudo"]:
            state["blocking"].append(
                "the USB tap needs root and `sudo -n` was refused. Grant the "
                "panel's user a password-less sudo rule for the tap.")

        state["mpd"] = _mpd_state(self.env())
        mpd = state["mpd"]
        if mpd.get("ok"):
            if mpd.get("volume") not in ("n/a", "?", "100"):
                state["warnings"].append(
                    f"MPD volume is {mpd['volume']} — anything but a disabled "
                    "mixer scales samples and breaks bit-perfection.")
            if mpd.get("replaygain") not in ("off", "?", ""):
                state["warnings"].append(
                    f"MPD replaygain is '{mpd['replaygain']}' — it applies gain "
                    "and breaks bit-perfection.")

        state["rates"] = self.rates()
        return state

    # ── runs ────────────────────────────────────────────────────────────────

    def run_test(self, job: Job, source: str, material_path: str,
                 duration: float, allow_drc: bool) -> None:
        if source not in ("aplay", "mpd", "mpd-http", "upnp", "live"):
            raise ValueError(f"unknown source: {source}")
        if not 5 <= duration <= 600:
            raise ValueError("tap window must be between 5 and 600 seconds")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        prefix = self.bp.results_root / f"{stamp}-{source}"

        argv = ["python3", str(self._tool("bitperfect_runner.py")),
                "--source", source, "--out", str(prefix),
                "--duration", str(duration)]
        if source != "live":
            path = self.resolve_material(material_path)
            if source == "aplay" and path.suffix.lower() != ".wav":
                raise ValueError(
                    "the direct control accepts PCM WAV files only; choose a WAV "
                    "or use an MPD path for FLAC and other containers")
            argv += ["--input", str(path)]
        if allow_drc:
            argv.append("--allow-drc")

        job.write("The tap records the isochronous OUT payloads of endpoint "
                  "0x01 — the last point the host controls. Everything past it "
                  "is the controller's DMA engine and the DAC's receiver, "
                  "neither of which can alter a PCM payload.")
        self.run_command(job, argv, timeout=self.bp.run_timeout)
        job.write(f"Artifacts: {prefix}.*")

    def runs(self) -> list[dict]:
        rows = []
        for path in sorted(self.bp.results_root.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            identifier = path.name[:-len(".json")]
            prefix = self.bp.results_root / identifier
            data["id"] = identifier
            # Only a run that got as far as aligning has payloads to browse;
            # NO CAPTURE / ALIGNMENT FAILED runs have a report and nothing else.
            data["has_bytes"] = (Path(f"{prefix}.wav").is_file()
                                 and Path(f"{prefix}.ref.raw").is_file())
            rows.append(data)
        return rows

    def _run_paths(self, identifier: str) -> tuple[Path, Path, dict]:
        if not _SAFE_ID.match(identifier):
            raise ValueError("invalid run id")
        prefix = self.bp.results_root / identifier
        report = Path(f"{prefix}.json")
        if not report.is_file():
            raise RuntimeError("unknown run")
        ref, tap = Path(f"{prefix}.ref.raw"), Path(f"{prefix}.wav")
        if not (ref.is_file() and tap.is_file()):
            raise RuntimeError(
                "this run has no comparable payload — it never got far enough "
                "to align the capture against the reference")
        return ref, tap, json.loads(report.read_text())

    def report(self, identifier: str) -> dict:
        if not _SAFE_ID.match(identifier):
            raise ValueError("invalid run id")
        report = self.bp.results_root / f"{identifier}.json"
        if not report.is_file():
            raise RuntimeError("unknown run")
        data = json.loads(report.read_text())
        text = self.bp.results_root / f"{identifier}.txt"
        if text.is_file():
            data["text"] = text.read_text()
        return data

    def window(self, identifier: str, offset: int, frames: int) -> dict:
        ref, tap, meta = self._run_paths(identifier)
        frames = max(1, min(frames, 4096))
        return self._capture([
            "python3", str(self._tool("bitperfect-lib.py")), "window",
            str(ref), str(tap), str(max(0, offset)), str(frames),
            str(meta.get("ch", 2))], timeout=60)

    def leadin(self, identifier: str, offset: int, frames: int) -> dict:
        """Browse the untrimmed capture ahead of the aligned stream start.

        Deliberately NOT routed through `_run_paths`: that helper demands the
        aligned pair, and the untrimmed wire is exactly what is still worth
        reading when alignment failed and there is no aligned pair to show.
        """
        if not _SAFE_ID.match(identifier):
            raise ValueError("invalid run id")
        prefix = self.bp.results_root / identifier
        report = Path(f"{prefix}.json")
        wire = Path(f"{prefix}.wire.raw")
        if not report.is_file():
            raise RuntimeError("unknown run")
        if not wire.is_file():
            raise RuntimeError("this run kept no untrimmed capture")
        meta = json.loads(report.read_text())
        frames = max(1, min(frames, 4096))
        return self._capture([
            "python3", str(self._tool("bitperfect-lib.py")), "leadin",
            str(wire), str(meta.get("start") or 0), str(meta.get("ch", 2)),
            str(max(0, offset)), str(frames)], timeout=60)

    def scan(self, identifier: str, buckets: int) -> dict:
        ref, tap, meta = self._run_paths(identifier)
        buckets = max(16, min(buckets, 2048))
        return self._capture([
            "python3", str(self._tool("bitperfect-lib.py")), "scan",
            str(ref), str(tap), str(buckets), str(meta.get("ch", 2))],
            timeout=300)

    def asset_path(self, name: str) -> Path:
        """Resolve an asset by BASENAME only — never a caller-supplied path.

        This backs the one unauthenticated route on the page (MPD's curl
        plugin and upmpdcli both fetch by URL), so it must not be able to
        address anything outside the asset cache."""
        if "/" in name or "\\" in name or not _SAFE_ID.match(name):
            raise ValueError("invalid asset name")
        path = (self.bp.assets_root / name).resolve()
        if path.parent != self.bp.assets_root.resolve() or not path.is_file():
            raise RuntimeError("unknown asset")
        return path


# ── helpers ─────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]", "_", name)[:80] or "material"


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_SUDO_REFUSAL = re.compile(
    r"^sudo:|a (?:password|terminal) is required|not allowed to execute",
    re.MULTILINE)


def _sudo_available(env: dict) -> bool:
    """Whether `sudo -n` will run anything at all for this user.

    Probed with `true`, not with the tap: asking the tap costs a capture, and
    a refusal has to be told apart from the tool merely failing — the same
    distinction app.py's _chain_run_tool makes."""
    if os.geteuid() == 0:
        return True
    try:
        r = subprocess.run(["sudo", "-n", "true"], capture_output=True,
                           text=True, timeout=5, env=env)
    except Exception:
        return False
    return r.returncode == 0 and not _SUDO_REFUSAL.search(r.stdout + r.stderr)


def _replaygain_value(output: str) -> str:
    """`mpc replaygain` answers "replay_gain_mode: off", not "off"."""
    text = (output or "").strip()
    return text.split(":", 1)[1].strip() if ":" in text else (text or "?")


def _mpd_state(env: dict) -> dict:
    client = shutil.which("mpc") or shutil.which("musicpc")
    if not client:
        return {"ok": False, "error": "mpc/musicpc not found"}
    try:
        status = subprocess.run([client, "status"], capture_output=True,
                                text=True, timeout=5, env=env).stdout
        outputs = subprocess.run([client, "outputs"], capture_output=True,
                                 text=True, timeout=5, env=env).stdout
        replaygain = subprocess.run([client, "replaygain"], capture_output=True,
                                    text=True, timeout=5, env=env).stdout
    except Exception as error:
        return {"ok": False, "error": str(error)}
    volume = re.search(r"volume:\s*(\S+)", status)
    enabled = [m.group(1) for m in
               re.finditer(r"Output\s+\d+\s+\((.+?)\)\s+is\s+enabled", outputs)]
    return {"ok": True,
            "volume": volume.group(1) if volume else "?",
            "replaygain": _replaygain_value(replaygain),
            "enabled": enabled,
            "state": ("playing" if "[playing]" in status
                      else "paused" if "[paused]" in status else "stopped")}
