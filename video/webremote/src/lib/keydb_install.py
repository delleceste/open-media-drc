"""Validate and atomically install a user-uploaded AACS KEYDB.cfg or ZIP."""

import os
from pathlib import Path
import pwd
import shutil
import tempfile
import zipfile

MAX_UPLOAD = 128 * 1024 * 1024
MAX_EXTRACTED = 256 * 1024 * 1024


def target_path() -> Path:
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    config = os.environ.get("XDG_CONFIG_HOME") or str(home / ".config")
    return Path(config) / "aacs" / "KEYDB.cfg"


def _copy_limited(source, dest, maximum: int) -> int:
    total = 0
    while chunk := source.read(1024 * 1024):
        total += len(chunk)
        if total > maximum:
            raise ValueError("The selected file is too large")
        dest.write(chunk)
    return total


def install(upload) -> dict:
    """Keep the old file untouched until the new file passes basic validation."""
    target = target_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, raw_name = tempfile.mkstemp(prefix=".keydb-upload-", dir=target.parent)
    candidate_name = None
    try:
        with os.fdopen(fd, "wb") as raw:
            size = _copy_limited(upload.stream, raw, MAX_UPLOAD)
        if size == 0:
            raise ValueError("The selected file is empty")
        candidate_name = raw_name
        if zipfile.is_zipfile(raw_name):
            try:
                with zipfile.ZipFile(raw_name) as archive:
                    matches = [item for item in archive.infolist()
                               if not item.is_dir() and Path(item.filename).name.lower() == "keydb.cfg"]
                    if len(matches) != 1:
                        raise ValueError("The ZIP must contain exactly one KEYDB.cfg")
                    if matches[0].file_size > MAX_EXTRACTED:
                        raise ValueError("The KEYDB.cfg inside the ZIP is too large")
                    fd, candidate_name = tempfile.mkstemp(prefix=".keydb-ready-", dir=target.parent)
                    with os.fdopen(fd, "wb") as candidate, archive.open(matches[0]) as source:
                        size = _copy_limited(source, candidate, MAX_EXTRACTED)
            except (zipfile.BadZipFile, RuntimeError) as error:
                raise ValueError("The ZIP could not be read") from error
        with open(candidate_name, "rb") as candidate:
            sample = candidate.read(65536)
        if size < 16 or sample.startswith(b"PK") or b"|" not in sample or b"\x00" in sample:
            raise ValueError("This does not look like an extracted KEYDB.cfg")
        with open(candidate_name, "rb+") as candidate:
            os.fsync(candidate.fileno())
        os.chmod(candidate_name, 0o600)
        had_previous = target.exists()
        if had_previous:
            backup = target.with_name("KEYDB.cfg.previous")
            shutil.copy2(target, backup)
            os.chmod(backup, 0o600)
        os.replace(candidate_name, target)
        candidate_name = None
        return {"path": str(target), "bytes": size,
                "backup": str(target.with_name("KEYDB.cfg.previous")) if had_previous else None}
    finally:
        if os.path.exists(raw_name):
            os.unlink(raw_name)
        if candidate_name and os.path.exists(candidate_name):
            os.unlink(candidate_name)
