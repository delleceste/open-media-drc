"""Share the DR log between boxes through a git repository.

Two boxes (home, office) cannot reach each other, but both can reach GitHub.
The repository is the meeting place, and every box writes exactly one file in
it, `boxes/<box>.jsonl`: what it measured or read itself (DrStore.export_rows).
It never touches another box's file, so pushes never conflict -- a rejected
push only means another box pushed first, and a rebase onto it is trivial --
and either box may be away for weeks.  Each round:

  1. clone the repository once, then pull (rebase) what the others pushed;
  2. write this box's file; commit and push it if it changed;
  3. import every other box's file whose content changed since last time
     (DrStore.import_box replaces that box's rows as a whole).

Git runs non-interactively with a timeout: credentials come from the service
user's own git setup (a credential helper for HTTPS, or a deploy key for SSH);
a missing one is reported on the DR page, never prompted for.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time

BOX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
GIT_TIMEOUT = 90
BRANCH = "main"


class SyncError(RuntimeError):
    pass


def safe_repo(url: str) -> str:
    """The URL without credentials, for showing."""
    return re.sub(r"//[^/@]*@", "//", url)


def _last_line(text: str, fallback: str) -> str:
    lines = (text or "").strip().splitlines()
    return safe_repo(lines[-1]) if lines else fallback


# Never prompt: a box has no one at its terminal.
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true",
            "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=20"}


class GitSync:
    def __init__(self, store, repo: str, box: str, workdir: str,
                 interval: float = 600.0, git: str = "git") -> None:
        if not BOX_RE.match(box or ""):
            raise SyncError(f"box name {box!r}: letters, digits, - and _ only")
        self.store = store
        self.repo = repo
        self.box = box
        self.workdir = workdir
        self.interval = max(60.0, interval)
        self.git_bin = git
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._imported: dict[str, str] = self._load_imported()
        self.state = {"last": 0.0, "ok": None, "error": "", "pushed": 0.0,
                      "boxes": {}, "running": False}

    # -- bookkeeping --

    @property
    def _imported_file(self) -> str:
        return self.workdir.rstrip("/") + ".imported.json"

    def _load_imported(self) -> dict:
        try:
            with open(self._imported_file, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_imported(self) -> None:
        tmp = self._imported_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._imported, f)
        os.replace(tmp, self._imported_file)

    # -- git --

    def _run(self, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        cmd = [self.git_bin, "-c", f"user.name=omdrc {self.box}",
               "-c", f"user.email=omdrc-{self.box}@localhost", *args]
        try:
            return subprocess.run(cmd, cwd=cwd or self.workdir, env=dict(os.environ, **_GIT_ENV),
                                  capture_output=True, text=True, timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise SyncError(f"git {args[0]} timed out")
        except OSError as error:
            raise SyncError(f"git: {error}")

    def _git(self, *args: str, cwd: str | None = None) -> str:
        done = self._run(*args, cwd=cwd)
        if done.returncode:
            raise SyncError(f"git {args[0]}: " + _last_line(done.stderr or done.stdout, "failed"))
        return done.stdout

    def _remote_has_branch(self) -> bool:
        return bool(self._git("ls-remote", "--heads", "origin", BRANCH).strip())

    def _ensure_clone(self) -> None:
        if os.path.isdir(os.path.join(self.workdir, ".git")):
            current = self._git("remote", "get-url", "origin").strip()
            if current != self.repo:
                self._git("remote", "set-url", "origin", self.repo)
            return
        os.makedirs(os.path.dirname(self.workdir) or ".", exist_ok=True)
        self._git("clone", "--quiet", self.repo, self.workdir, cwd=os.path.dirname(self.workdir) or ".")
        # an empty repository has no branch yet: the first push creates it
        self._git("checkout", "--quiet", "-B", BRANCH)

    # -- one round --

    def _write_own(self) -> bool:
        rows = self.store.export_rows()
        body = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
                       for r in rows)
        path = os.path.join(self.workdir, "boxes", f"{self.box}.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8") as f:
                if f.read() == body:
                    return False
        except OSError:
            pass
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(path + ".tmp", path)
        return True

    def _push(self) -> None:
        self._git("add", "boxes")
        if not self._git("status", "--porcelain", "boxes").strip():
            return
        self._git("commit", "--quiet", "-m", f"{self.box}: DR log")
        for _ in range(3):
            done = self._run("push", "--quiet", "origin", f"HEAD:{BRANCH}")
            if done.returncode == 0:
                self.state["pushed"] = time.time()
                return
            # another box pushed first: its file is not ours, the rebase is clean
            self._git("pull", "--quiet", "--rebase", "origin", BRANCH)
        raise SyncError("git push: " + _last_line(done.stderr, "rejected"))

    def _import_others(self) -> None:
        folder = os.path.join(self.workdir, "boxes")
        boxes = {}
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            box, ext = os.path.splitext(name)
            if ext != ".jsonl" or box == self.box or not BOX_RE.match(box):
                continue
            path = os.path.join(folder, name)
            with open(path, "rb") as f:
                raw = f.read()
            digest = hashlib.sha256(raw).hexdigest()
            info = {"at": os.path.getmtime(path)}
            if self._imported.get(box) != digest:
                rows = []
                for line in raw.decode("utf-8", "replace").splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
                info.update(self.store.import_box(box, rows))
                self._imported[box] = digest
                self._save_imported()
            boxes[box] = info
        self.state["boxes"] = boxes

    def run_once(self) -> dict:
        with self._lock:
            self.state["running"] = True
            try:
                self._ensure_clone()
                if self._remote_has_branch():
                    self._git("pull", "--quiet", "--rebase", "origin", BRANCH)
                self._write_own()
                self._push()
                self._import_others()
                self.state.update(ok=True, error="")
            except Exception as error:                  # noqa: BLE001 - shown on the page
                self.state.update(ok=False, error=str(error)[:300])
            finally:
                self.state.update(last=time.time(), running=False)
            return self.status()

    # -- the background loop --

    def status(self) -> dict:
        return {"configured": True, "box": self.box, "repo": safe_repo(self.repo),
                "interval": self.interval, **{k: v for k, v in self.state.items()}}

    def trigger(self) -> None:
        self._wake.set()

    def start(self, first_delay: float = 30.0) -> None:
        if self._thread and self._thread.is_alive():
            return

        def loop() -> None:
            self._wake.wait(first_delay)
            while True:
                self._wake.clear()
                self.run_once()
                self._wake.wait(self.interval)

        self._thread = threading.Thread(target=loop, name="dr-sync", daemon=True)
        self._thread.start()
