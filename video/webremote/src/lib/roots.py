"""Whitelisted media roots and safe path resolution.

The web app serves directory listings and (later) file content, so every path
that comes from the client must be proven to live inside one of the configured
roots. We resolve with realpath (collapsing `..` and following symlinks) and
require the result to be the root itself or a descendant of it — this blocks
both `..` traversal and symlink escapes out of the whitelist.
"""
import os


def load_roots(raw: str) -> list[str]:
    """Parse a comma-separated roots string into existing absolute realpaths.

    Non-existent or non-directory entries are dropped (with the caller free to
    warn): a root that isn't mounted yet shouldn't crash the server.
    """
    roots = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        rp = os.path.realpath(os.path.expanduser(p))
        if os.path.isdir(rp) and rp not in roots:
            roots.append(rp)
    return roots


def _within(realpath: str, root: str) -> bool:
    return realpath == root or realpath.startswith(root + os.sep)


def is_within(realpath: str, roots: list[str]) -> bool:
    return any(_within(realpath, r) for r in roots)


def safe_resolve(req_path: str, roots: list[str]) -> str | None:
    """Resolve a client-supplied path and confirm it is inside a root.

    Returns the canonical realpath on success, or None if it escapes the
    whitelist. Does not check existence/type — callers decide what they need.
    """
    if not req_path:
        return None
    rp = os.path.realpath(req_path)
    return rp if is_within(rp, roots) else None


def containing_root(realpath: str, roots: list[str]) -> str | None:
    for r in roots:
        if _within(realpath, r):
            return r
    return None
