"""User-managed media roots and a directory-only server folder picker."""

import json
import os
from pathlib import Path
import pwd
import tempfile

MAX_ROOTS = 24


def settings_path() -> Path:
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    return home / ".config" / "omdrcvideo" / "media-roots.json"


def load() -> list[str] | None:
    """None means to use webremote.conf; [] is a deliberate empty selection."""
    try:
        data = json.loads(settings_path().read_text())
        roots = data["roots"]
        if isinstance(roots, list) and all(isinstance(p, str) and os.path.isabs(p) for p in roots):
            return roots[:MAX_ROOTS]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def validate(paths: object) -> list[str]:
    if not isinstance(paths, list) or len(paths) > MAX_ROOTS:
        raise ValueError(f"Choose at most {MAX_ROOTS} media folders")
    roots = []
    for path in paths:
        if not isinstance(path, str) or not os.path.isabs(path) or "\x00" in path:
            raise ValueError("Each media folder needs an absolute server path")
        real = os.path.realpath(path)
        if real == "/" or not os.path.isdir(real):
            raise ValueError(f"Not a selectable folder: {path}")
        if not os.access(real, os.R_OK | os.X_OK):
            raise ValueError(f"The video service cannot read: {real}")
        if real not in roots:
            roots.append(real)
    return roots


def save(roots: list[str]) -> None:
    target = settings_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".media-roots-", dir=target.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump({"roots": roots}, file)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(name, 0o600)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def list_folders(path: str) -> dict:
    if not os.path.isabs(path) or "\x00" in path:
        raise ValueError("Choose an absolute server folder")
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise ValueError("Folder does not exist")
    folders = []
    with os.scandir(real) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=True):
                    folders.append({"name": entry.name, "path": os.path.realpath(entry.path)})
            except OSError:
                continue
    folders.sort(key=lambda item: item["name"].casefold())
    return {"path": real, "parent": os.path.dirname(real) if real != "/" else None,
            "folders": folders[:500], "truncated": len(folders) > 500}
