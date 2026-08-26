"""Configuration workflows used by the omdrcctrl web page.

The Flask process remains unprivileged.  Filter tools write only below the
configured live site; hardware changes go through the fixed-purpose
``omdrc-config-helper`` program.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import threading
import time
from typing import Callable, Iterator


_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_FREEBSD_CARD = re.compile(
    r"^/dev/dsp(?P<unit>\d+)\s+(?P<role>\S+)\s+(?P<name>.*?)\s+"
    r"\((?P<driver>[^,]+),\s*(?P<caps>[^,)]*)"
    r"(?:,\s*(?P<vid>0x[0-9a-f]+):(?P<pid>0x[0-9a-f]+)"
    r"(?::(?P<serial>[A-Za-z0-9._+-]+))?)?\)$", re.I)


@dataclass
class Settings:
    enabled: bool = True
    site_root: Path = Path("/usr/local/etc/open-media-drc")
    state_root: Path = Path("/tmp/omdrcctrl-configuration")
    tools_root: Path = Path(__file__).resolve().parents[2] / "scripts"
    helper: str = "omdrc-config-helper"
    max_file_bytes: int = 1024 * 1024 * 1024
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    apply_timeout: int = 120


@dataclass
class Job:
    id: str
    action: str
    directory: Path
    phase: str = "queued"
    output: list[str] = field(default_factory=list)
    error: str = ""
    returncode: int | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    condition: threading.Condition = field(default_factory=threading.Condition,
                                             repr=False)

    def write(self, line: str) -> None:
        clean = _ANSI.sub("", line.rstrip("\r\n"))
        with self.condition:
            self.output.append(clean)
            self.output = self.output[-4000:]
            self._persist()
            self.condition.notify_all()

    def set_phase(self, phase: str, *, error: str = "", returncode=None) -> None:
        with self.condition:
            self.phase = phase
            self.error = error
            self.returncode = returncode
            if phase in {"succeeded", "failed"}:
                self.finished_at = time.time()
            self._persist()
            self.condition.notify_all()

    def snapshot(self, include_output: bool = True) -> dict:
        value = {"id": self.id, "action": self.action, "phase": self.phase,
                 "error": self.error, "returncode": self.returncode,
                 "started_at": self.started_at, "finished_at": self.finished_at}
        if include_output:
            value["output"] = self.output
        return value

    def _persist(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / ".job.json.new"
        temporary.write_text(json.dumps(self.snapshot(), indent=2) + "\n")
        os.replace(temporary, self.directory / "job.json")


class ConfigurationManager:
    def __init__(self, settings: Settings, env: Callable[[], dict],
                 active_design: Callable[[], dict] | None = None):
        self.settings = settings
        self.env = env
        self.active_design = active_design or (lambda: {})
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.settings.state_root.mkdir(parents=True, exist_ok=True)

    def _new_job(self, action: str) -> Job:
        identifier = secrets.token_urlsafe(18)
        job = Job(identifier, action, self.settings.state_root / identifier)
        self.jobs[identifier] = job
        job._persist()
        return job

    def start(self, action: str, worker: Callable[[Job], None]) -> Job:
        job = self._new_job(action)
        return self.launch(job, worker)

    def launch(self, job: Job, worker: Callable[[Job], None]) -> Job:

        def run() -> None:
            if not self.lock.acquire(blocking=False):
                job.set_phase("failed", error="another configuration operation is running")
                return
            try:
                job.set_phase("running")
                worker(job)
                if job.phase == "running":
                    job.set_phase("succeeded", returncode=0)
            except Exception as error:  # the job log is the user-facing traceback boundary
                job.write(f"ERROR: {error}")
                job.set_phase("failed", error=str(error), returncode=1)
            finally:
                self.lock.release()

        threading.Thread(target=run, name=f"omdrc-{job.action}-{job.id[:6]}",
                         daemon=True).start()
        return job

    def get_job(self, identifier: str) -> Job | None:
        return self.jobs.get(identifier)

    def events(self, job: Job) -> Iterator[str]:
        cursor = 0
        while True:
            with job.condition:
                while cursor >= len(job.output) and job.phase not in {"succeeded", "failed"}:
                    job.condition.wait(timeout=15)
                    if cursor >= len(job.output):
                        yield ": keepalive\n\n"
                while cursor < len(job.output):
                    yield "data: " + json.dumps({"line": job.output[cursor]}) + "\n\n"
                    cursor += 1
                if job.phase in {"succeeded", "failed"}:
                    yield "event: done\ndata: " + json.dumps(job.snapshot(False)) + "\n\n"
                    return

    def run_command(self, job: Job, argv: list[str], timeout: int | None = None) -> None:
        job.write("$ " + " ".join(argv))
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, env=self.env(),
                                stdin=subprocess.DEVNULL, start_new_session=True)
        assert proc.stdout is not None
        started = time.monotonic()
        for line in proc.stdout:
            job.write(line)
            if timeout and time.monotonic() - started > timeout:
                proc.kill()
                raise RuntimeError(f"operation timed out after {timeout} seconds")
        rc = proc.wait()
        if rc:
            raise RuntimeError(f"command exited with status {rc}")

    def save_uploads(self, job: Job, files, mdat) -> Path:
        if not files or mdat is None:
            raise ValueError("select the .txts folder and its matching .mdat")
        folder_names: set[str] = set()
        for item in files:
            parts = Path(item.filename or "").parts
            if len(parts) < 2 or parts[0] in {".", "..", "/"}:
                raise ValueError("select one exported .txts folder")
            folder_names.add(parts[0])
        if len(folder_names) != 1:
            raise ValueError("the upload must contain exactly one exported .txts folder")
        folder_name = next(iter(folder_names))
        if not folder_name.lower().endswith(".txts"):
            raise ValueError(f"the selected folder must end in .txts: {folder_name}")

        expected_mdat = folder_name[:-len(".txts")] + ".mdat"
        mdat_name = Path(mdat.filename or "").name
        if mdat_name.casefold() != expected_mdat.casefold():
            raise ValueError(
                f"Matching REW session must be {expected_mdat} for the selected "
                f"{folder_name} folder; got {mdat_name or '(no file)'}")
        export = job.directory / "upload" / Path(folder_name).name
        export.mkdir(parents=True, exist_ok=False)
        total = 0
        seen: set[str] = set()
        for upload in [*files, mdat]:
            name = Path(upload.filename or "").name
            if not name or name.lower() in seen or name in {".", ".."}:
                raise ValueError(f"duplicate or invalid upload name: {name!r}")
            seen.add(name.lower())
            target = export / name
            with target.open("xb") as stream:
                while True:
                    block = upload.stream.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if target.stat().st_size + len(block) > self.settings.max_file_bytes:
                        raise ValueError(f"{name} exceeds the per-file upload limit")
                    if total > self.settings.max_upload_bytes:
                        raise ValueError("the selected files exceed the total upload limit")
                    stream.write(block)
        return export

    def install_filter(self, job: Job, directory: Path, geometry: str = "",
                       design: str = "") -> None:
        script = self.settings.tools_root / "new_filter_design.py"
        live_writable = (self.settings.site_root.is_dir() and
                         os.access(self.settings.site_root, os.W_OK))
        publish_root = self.settings.site_root if live_writable else job.directory / "staged-site"
        publish_root.mkdir(parents=True, exist_ok=True)
        argv = [shutil.which("python3") or "python3", str(script), str(directory),
                "--site-root", str(publish_root), "--allow-uncommitted",
                "--no-commit", "--yes", "--live", "--archive-mdat",
                "--upload-provenance"]
        if not live_writable:
            argv += ["--coefficient-root", str(self.settings.site_root)]
        if geometry:
            if not _SAFE_NAME.fullmatch(geometry):
                raise ValueError("invalid geometry name")
            argv += ["--geometry", geometry]
        if design:
            if not _SAFE_NAME.fullmatch(design) or design == "default":
                raise ValueError("invalid or reserved design ID")
            argv += ["--design", design]
        self.run_command(job, argv)
        if not live_writable:
            helper = shutil.which(self.settings.helper) or self.settings.helper
            command = [helper, "filter-publish", "--staged", str(publish_root),
                       "--site-root", str(self.settings.site_root)]
            if os.geteuid() != 0:
                command = ["sudo", "-n", *command]
            self.run_command(job, command)

    def designs(self) -> list[dict]:
        active = self.active_design()
        rows = []
        for path in sorted(self.settings.site_root.glob("filters/*/provenance/*.json")):
            if path.name.endswith(".source.json"):
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            design = data.get("design_id", data.get("variant", path.stem))
            geometry = data.get("geometry", path.parent.parent.name)
            session = (data.get("source", {}).get("measurements", {}))
            rows.append({"geometry": geometry, "design": design,
                         "description": data.get("description", design),
                         "bundle_id": data.get("bundle_id", ""),
                         "installed": data.get("verification", {}).get("audited_at", ""),
                         "rates": sorted(data.get("runtime", {}).get("rates", {}), key=int),
                         "mdat": session.get("file", ""),
                         "selected": (active.get("geometry") == geometry and
                                      active.get("design") in {f"@{design}", design})})
        return rows

    def remove_filter(self, job: Job, geometry: str, design: str) -> None:
        if not _SAFE_NAME.fullmatch(geometry) or not _SAFE_NAME.fullmatch(design):
            raise ValueError("invalid filter design selector")
        active = self.active_design()
        if active.get("geometry") == geometry and active.get("design") in {design, f"@{design}"}:
            raise RuntimeError("switch away from this selected design before removing it")
        script = self.settings.tools_root / "remove_filter_design.py"
        command = [shutil.which("python3") or "python3", str(script),
                   f"{geometry}@{design}", "--site-root",
                   str(self.settings.site_root), "--no-commit", "--yes", "--live"]
        if not os.access(self.settings.site_root, os.W_OK):
            helper = shutil.which(self.settings.helper) or self.settings.helper
            command = [helper, "filter-remove", "--selector", f"{geometry}@{design}",
                       "--site-root", str(self.settings.site_root), "--script", str(script)]
            if os.geteuid() != 0:
                command = ["sudo", "-n", *command]
        self.run_command(job, command)

    def cards(self) -> list[dict]:
        return linux_cards() if platform.system() == "Linux" else freebsd_cards(self.env())

    def apply_audio(self, job: Job, dac: str, capture: str) -> None:
        identities = {card["identity"]: card for card in self.cards()}
        if dac not in identities or not identities[dac].get("playback"):
            raise ValueError("the selected DAC is no longer attached or cannot play")
        if identities[dac].get("ambiguous"):
            raise ValueError("identical DACs have no serial numbers; unplug one before applying")
        if capture:
            if platform.system() == "Linux":
                raise ValueError("capture selection is not operational on Linux")
            if capture not in identities or not identities[capture].get("capture"):
                raise ValueError("the selected capture interface is no longer attached")
            if identities[capture].get("ambiguous"):
                raise ValueError("identical capture interfaces have no serial numbers; unplug one before applying")
            if capture == dac:
                raise ValueError("DAC and capture interface must be different cards")
        helper = shutil.which(self.settings.helper) or self.settings.helper
        argv = [helper, "apply", "--dac", dac, "--capture", capture,
                "--timeout", str(self.settings.apply_timeout)]
        if os.geteuid() != 0:
            argv = ["sudo", "-n", *argv]
        self.run_command(job, argv, self.settings.apply_timeout + 15)


def _usb_identity(vid: str, pid: str, serial: str = "") -> str:
    value = f"0x{vid.lower().removeprefix('0x')}:0x{pid.lower().removeprefix('0x')}"
    return value + (f":{serial}" if serial and serial != "-" else "")


def _disambiguate(rows: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for row in rows:
        base = _usb_identity(row["vid"], row["pid"])
        row["identity"] = base
        counts[base] = counts.get(base, 0) + 1
    for row in rows:
        if counts[row["identity"]] > 1:
            if not row.get("serial"):
                row["ambiguous"] = True
            else:
                row["identity"] += ":" + row["serial"].lower()
    return rows


def linux_cards() -> list[dict]:
    rows = []
    for card_dir in sorted(Path("/sys/class/sound").glob("card[0-9]*")):
        number = card_dir.name[4:]
        device = card_dir / "device"
        usb = device.resolve()
        while usb != usb.parent and not (usb / "idVendor").is_file():
            usb = usb.parent
        if not (usb / "idVendor").is_file():
            continue
        read = lambda name, default="": ((usb / name).read_text(errors="replace").strip()
                                          if (usb / name).is_file() else default)
        vid, pid, serial = read("idVendor"), read("idProduct"), read("serial")
        name = read("product") or read("manufacturer") or f"ALSA card {number}"
        playback_nodes = sorted(Path("/dev/snd").glob(f"pcmC{number}D*p"))
        capture_nodes = sorted(Path("/dev/snd").glob(f"pcmC{number}D*c"))
        rows.append({"name": name, "identity": _usb_identity(vid, pid, serial),
                     "vid": f"0x{vid}", "pid": f"0x{pid}", "serial": serial,
                     "unit": f"card{number}", "device": str(playback_nodes[0]) if playback_nodes else "",
                     "playback": bool(playback_nodes), "capture": bool(capture_nodes),
                     "playback_nodes": [str(p) for p in playback_nodes],
                     "capture_nodes": [str(p) for p in capture_nodes]})
    return _disambiguate(rows)


def freebsd_cards(env: dict) -> list[dict]:
    try:
        result = subprocess.run(["service", "omdrc_audio", "status"],
                                capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in result.stdout.splitlines():
        match = _FREEBSD_CARD.match(line.strip())
        if not match or not match.group("vid"):
            continue
        item = match.groupdict()
        caps = item["caps"].lower()
        identity = _usb_identity(item["vid"], item["pid"], item.get("serial") or "")
        rows.append({"name": item["name"], "identity": identity,
                     "vid": item["vid"], "pid": item["pid"],
                     "serial": item.get("serial") or "",
                     "unit": f"pcm{item['unit']}", "device": f"/dev/dsp{item['unit']}",
                     "role": item["role"], "playback": "play" in caps,
                     "capture": "rec" in caps})
    return _disambiguate(rows)
