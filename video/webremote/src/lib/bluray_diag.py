"""Read-only Blu-ray readiness checks for the video remote."""

import ctypes.util
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess
import urllib.request

from . import makemkv_key

KEY_SOURCE = "https://fvonline-db.bplaced.net/"
KEY_ARCHIVE = KEY_SOURCE + "export/keydb_eng.zip"
STALE_DAYS = 90


def diagnose(disc_enabled: bool, disc_device: str, mpv_running: bool,
             now: datetime | None = None, disc_cache: str = "bd") -> dict:
    """Inspect the service user's environment without touching the disc or keys."""
    now = now or datetime.now(timezone.utc)
    rows = []

    def add(label: str, status: str, detail: str, fix: str = "", info: str = "") -> None:
        rows.append({"label": label, "status": status, "detail": detail, "fix": fix, "info": info})

    system = platform.system()
    package_hint = ("sudo pkg install mpv libbluray libaacs libudfread libbdplus"
                    if system == "FreeBSD" else
                    "Install mpv, libbluray, libaacs, libudfread and libbdplus from your distribution")
    mmbd = makemkv_key.libmmbd()
    uses_mmbd = makemkv_key.players_use_libmmbd()
    if uses_mmbd:
        add("MakeMKV (libmmbd)", "ok", f"{mmbd}: mpv and Kodi decrypt through MakeMKV; "
            "KEYDB.cfg and libaacs are only the fallback")
    elif system == "Linux":
        add("MakeMKV (libmmbd)", "warning",
            "Not installed: decryption falls back to libaacs + KEYDB.cfg, which misses discs not in "
            "FindVUK and fails on drives that reject its host certificate",
            "Install MakeMKV (Arch: the makemkv AUR package), then restart the idle mpv")
    else:
        add("Decryption", "ok", f"libaacs + KEYDB.cfg (on {system} MakeMKV is not used, by design)")
    add("Disc playback", "ok" if disc_enabled else "error",
        "Enabled in webremote.conf" if disc_enabled else "Disabled in webremote.conf",
        "Set [disc] enabled = yes in webremote.conf" if not disc_enabled else "")
    for name, library, required in (("libbluray", "bluray", True),
                                    ("libaacs", "aacs", True),
                                    ("libudfread", "udfread", False),
                                    ("libbdplus", "bdplus", False)):
        found = ctypes.util.find_library(library)
        if uses_mmbd and library in ("aacs", "bdplus"):
            required = False   # libmmbd stands in for both
        add(name, "ok" if found else ("error" if required else "warning"),
            found or ("Not found; needed for some Blu-ray discs" if not required else "Not found"),
            package_hint if not found else "")
    for binary, required in (("mpv", True), ("bd_list_titles", False)):
        found = shutil.which(binary)
        add(binary, "ok" if found else ("error" if required else "warning"),
            found or ("Not found; longest-title selection will fall back to the default" if not required else "Not found"),
            package_hint if not found else "")
    add("Idle mpv", "ok" if mpv_running else "warning",
        "Running" if mpv_running else "Not running; the web remote cannot start playback",
        "Start the desktop session's mpv-idle.sh" if not mpv_running else "",
        "From the top-level project, sudo cmake --install build installs mpv-idle.sh and its autostart entry. "
        "Run cmake --build build --target user-install as the audio/desktop user to link that entry. "
        f"Log into the graphical desktop, or start {Path(__file__).resolve().parents[2] / 'mpv-idle.sh'} there now. "
        "The web service cannot launch mpv into that desktop session.")

    try:
        account_home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    except KeyError:
        account_home = Path.home()
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(account_home / ".config")
    key_file = Path(config_home) / "aacs" / "KEYDB.cfg"
    key = {"path": str(key_file), "source": KEY_SOURCE, "fallback": uses_mmbd,
           "archive": KEY_ARCHIVE, "age_days": None, "remote_updated": None}
    keydb_start = len(rows)
    if not key_file.is_file():
        add("AACS keys", "error", f"Missing {key_file} (the filename is KEYDB.cfg, not KEYS.db)",
            "Download the English KEYDB.cfg archive from the link below and extract KEYDB.cfg to this path")
    elif not os.access(key_file, os.R_OK):
        add("AACS keys", "error", f"The video service user cannot read {key_file}",
            "Give the video service user read permission")
    else:
        stat = key_file.stat()
        key["age_days"] = max(0, int((now.timestamp() - stat.st_mtime) / 86400))
        with key_file.open("rb") as file:
            sample = file.read(65536)
        if not stat.st_size or sample.startswith(b"PK") or b"|" not in sample:
            add("AACS keys", "error", f"{key_file} is empty, still zipped, or does not look like a key database",
                "Extract KEYDB.cfg from the downloaded archive to this path")
        else:
            updated = None
            try:
                request = urllib.request.Request(KEY_ARCHIVE, method="HEAD")
                with urllib.request.urlopen(request, timeout=3) as response:
                    updated = parsedate_to_datetime(response.headers["Last-Modified"])
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
            except (OSError, KeyError, TypeError, ValueError):
                pass
            if updated:
                key["remote_updated"] = updated.date().isoformat()
            newer = updated and updated.timestamp() > stat.st_mtime + 86400
            stale = newer or (not updated and key["age_days"] >= STALE_DAYS)
            detail = f"{key_file} · {key['age_days']} days old"
            if updated:
                detail += f"; FindVUK archive updated {key['remote_updated']}"
            elif stale:
                detail += "; latest archive could not be checked"
            add("AACS keys", "warning" if stale else "ok", detail,
                "A newer database is available; download and replace KEYDB.cfg" if newer else
                "Check the linked database for updates" if stale else "")

    if uses_mmbd:
        # MakeMKV decrypts; the AACS key file matters only if libmmbd is removed.
        for row in rows[keydb_start:]:
            row["label"] = "AACS keys (fallback)"
            if row["status"] != "ok":
                row["detail"] += " (not used while MakeMKV decrypts)"
                row["status"], row["fix"] = "ok", ""

    device = Path("/dev") / disc_device
    exists = device.exists()
    add("Optical drive", "ok" if exists else "warning",
        f"{device} is present" if exists else f"{device} is absent; connect the drive to test a physical disc",
        "Check [disc] device in webremote.conf" if not exists else "")
    if system == "FreeBSD":
        for binary in ("gcache", "kldload"):
            found = shutil.which(binary)
            add(binary, "ok" if found else "error", found or "Missing FreeBSD cache helper",
                "Install the FreeBSD base utility" if not found else "")
        if os.geteuid() != 0:
            sudo = shutil.which("sudo")
            block = os.environ.get("DISC_BLOCK", "1048576")
            size = os.environ.get("DISC_SIZE", "268435456")
            commands = (("kldload", ["-n", "geom_cache"]),
                        ("gcache", ["destroy", disc_cache]),
                        ("gcache", ["create", "-b", block, "-s", size, disc_cache, disc_device]))
            for binary, args in commands:
                path = shutil.which(binary)
                if not sudo or not path:
                    allowed = False
                else:
                    try:
                        result = subprocess.run([sudo, "-n", "-l", path, *args],
                                                capture_output=True, timeout=2)
                        allowed = result.returncode == 0
                    except (OSError, subprocess.TimeoutExpired):
                        allowed = False
                add(f"sudo {binary} {' '.join(args)}", "ok" if allowed else "error",
                    "Permitted without a password" if allowed else "Passwordless sudo permission is missing",
                    "Grant the video service user this exact command in sudoers" if not allowed else "")
    return {"ok": True, "status": "needs attention" if any(r["status"] == "error" for r in rows)
            else "warning" if any(r["status"] == "warning" for r in rows) else "ready",
            "checks": rows, "keydb": key,
            "note": "A configuration check cannot prove that a particular disc is decryptable; play an inserted disc to verify its key and drive access."}
