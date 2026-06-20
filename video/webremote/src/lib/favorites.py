"""Persisted list of favourite folders pinned to the main page.

Pure storage: a JSON list of absolute paths, with atomic writes under a lock.
Validation (inside the whitelist, still a directory) is the caller's job, so
this module stays decoupled from the roots policy.
"""
import json
import os
import threading

_lock = threading.Lock()


def load(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [p for p in data if isinstance(p, str)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(path: str, items: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1)
    os.replace(tmp, path)


def add(path: str, p: str) -> bool:
    """Add p to the favourites file; return True if it was newly added."""
    with _lock:
        items = load(path)
        if p in items:
            return False
        items.append(p)
        _save(path, items)
        return True


def remove(path: str, p: str) -> bool:
    """Remove p from the favourites file; return True if it was present."""
    with _lock:
        items = load(path)
        if p not in items:
            return False
        items = [x for x in items if x != p]
        _save(path, items)
        return True
