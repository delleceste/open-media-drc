"""MakeMKV beta key: look up the current key online, remember expiries, apply it.

MakeMKV's libmmbd decrypts Blu-rays for libbluray (see ../../BLURAY-DECRYPTION.md)
but only while makemkvcon holds a valid registration key.  The free beta key is
published on a forum page together with a sentence saying until when it is
valid.  This module reads that page with regular expressions, hoping the
phrasing does not change radically:

    The current beta key is  T-xxxx...  and is valid until end of October 2026.

Every lookup records the expiry of the key it saw, so the expiry of the key
installed in ~/.MakeMKV/settings.conf is known later without the network (the
page banner reads only the stored state).  A lookup that returns a different
key with a later expiry means a new key is out; applying it is always an
explicit user action.
"""

import calendar
import csv
from datetime import date, datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request

FORUM_URL = "https://forum.makemkv.com/forum/viewtopic.php?t=1053"
NOTIFY_DAYS = 7     # page banner from one week before the expiry
URGENT_DAYS = 2     # the check offers the update this close to the expiry
LIBMMBD_PATHS = ("/usr/lib/libmmbd.so.0", "/usr/local/lib/libmmbd.so.0")

KEY_RE = re.compile(r"\bT-[A-Za-z0-9@_]{40,}")
_CODE_RE = re.compile(r"<code[^>]*>(.*?)</code>", re.I | re.S)
_SETTING_RE = re.compile(r'^\s*app_Key\s*=\s*"([^"]*)"\s*$', re.M)
# "valid until end of October 2026", "valid till October 31, 2026", "valid through 2026-10-31"
_VALID_RE = re.compile(r"\bvalid\s+(?:until|till|through|thru|to)\s+(?P<when>[^.\n]{3,60}?)(?:\.\s|\.$|\n|$)",
                       re.I)
_MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): number for number, name in enumerate(calendar.month_abbr) if name})
_MONTHS["sept"] = 9
_MONTH = r"(?P<month>%s)\.?" % "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?P<year>20\d\d)"
_DATE_PATTERNS = (
    re.compile(r"(?P<year>20\d\d)-(?P<num>\d{1,2})-(?P<day>\d{1,2})"),
    re.compile(_MONTH + r"\s+" + _DAY + r",?\s+" + _YEAR, re.I),          # October 31, 2026
    re.compile(_DAY + r"\s+(?:of\s+)?" + _MONTH + r",?\s+" + _YEAR, re.I),  # 31st of October 2026
    re.compile(r"(?:(?:the\s+)?end\s+of\s+)?" + _MONTH + r",?\s+" + _YEAR, re.I),  # end of October 2026
)

_lock = threading.Lock()


class KeyLookupError(Exception):
    """The page could not be fetched or no longer reads as expected."""


def settings_path() -> Path:
    """MakeMKV's settings of the account running the players (this service's account)."""
    try:
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    except KeyError:
        home = Path.home()
    return home / ".MakeMKV" / "settings.conf"


def libmmbd() -> str | None:
    return next((path for path in LIBMMBD_PATHS if os.path.exists(path)), None)


def supported() -> bool:
    """MakeMKV decryption is a Linux feature.  FreeBSD decrypts with libaacs and
    KEYDB.cfg by design: the FreeBSD MakeMKV port's libmmbd is a Linux library
    (Linuxulator) that the native libbluray of mpv and Kodi cannot load."""
    return platform.system() == "Linux"


def players_use_libmmbd() -> bool:
    """Mirrors lib/makemkv-env.sh: the players switch to libmmbd only on Linux."""
    return supported() and libmmbd() is not None


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _page_text(page: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", page, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def parse_expiry(when: str) -> date | None:
    """A date phrase -> the last day the key is valid ("October 2026" = 31 Oct)."""
    for pattern in _DATE_PATTERNS:
        match = pattern.search(when)
        if not match:
            continue
        groups = match.groupdict()
        year = int(groups["year"])
        month = int(groups["num"]) if groups.get("num") else _MONTHS[groups["month"].lower().rstrip(".")]
        try:
            if groups.get("day"):
                return date(year, month, int(groups["day"]))
            return date(year, month, calendar.monthrange(year, month)[1])
        except ValueError:
            return None
    return None


def parse_page(page: str) -> dict:
    """Extract the key and its expiry from the forum page."""
    keys = [m.group(0) for code in _CODE_RE.findall(page) for m in KEY_RE.finditer(html.unescape(code))]
    text = _page_text(page)
    if not keys:
        keys = KEY_RE.findall(text)
    if not keys:
        raise KeyLookupError("No beta key (T-...) found on the MakeMKV page; its layout may have changed")
    key = keys[0]
    after = text[text.find(key) + len(key):] if key in text else text
    match = _VALID_RE.search(after) or _VALID_RE.search(text)
    phrase = " ".join(match.group("when").split()) if match else None
    expires = parse_expiry(phrase) if phrase else None
    return {"key": key, "expires": expires.isoformat() if expires else None,
            "expiry_text": f"valid until {phrase}" if phrase else None}


def fetch(timeout: float = 10) -> dict:
    request = urllib.request.Request(FORUM_URL, headers={"User-Agent": "Mozilla/5.0 (omdrcvideo)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            page = response.read(2 * 1024 * 1024).decode("utf-8", "replace")
    except OSError as error:
        raise KeyLookupError(f"Cannot reach the MakeMKV forum: {error}") from error
    return parse_page(page)


def installed_key() -> str | None:
    try:
        match = _SETTING_RE.search(settings_path().read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return match.group(1) if match and match.group(1) else None


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as file:
            state = json.load(file)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=1)
    os.replace(tmp, path)


def record(path: str, found: dict, now: datetime | None = None) -> dict:
    """Save a lookup: the online key and the expiry of every key seen so far."""
    now = now or datetime.now(timezone.utc)
    with _lock:
        state = load_state(path)
        seen = state.setdefault("keys", {})
        entry = seen.setdefault(fingerprint(found["key"]), {"first_seen": now.isoformat(timespec="seconds")})
        if found.get("expires"):
            entry["expires"] = found["expires"]
        state["online"] = {**found, "checked_at": now.isoformat(timespec="seconds")}
        state.pop("last_error", None)
        _save_state(path, state)
        return state


def record_error(path: str, message: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    with _lock:
        state = load_state(path)
        state["last_error"] = {"message": message, "at": now.isoformat(timespec="seconds")}
        _save_state(path, state)
        return state


def apply(key: str) -> dict:
    """Set app_Key in settings.conf, keeping every other line; back up the old file."""
    if not KEY_RE.fullmatch(key):
        raise ValueError("That does not look like a MakeMKV key")
    target = settings_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        old = target.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        old = None
    line = f'app_Key = "{key}"'
    if old is None:
        new = line + "\n"
    elif _SETTING_RE.search(old):
        new = _SETTING_RE.sub(lambda _: line, old, count=1)
    else:
        new = old + ("" if not old or old.endswith("\n") else "\n") + line + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(new)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(tmp, 0o600)
        if old is not None:
            backup = target.with_name("settings.conf.previous")
            shutil.copy2(target, backup)
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return {"path": str(target), "backup": str(target.with_name("settings.conf.previous")) if old else None}


def registration_problem(timeout: float = 20) -> str | None:
    """Ask makemkvcon itself; return its complaint about the key or version, if any.

    Best effort: makemkvcon's messages are matched loosely, and silence means
    only that it raised no recognisable complaint.  disc:9999 reads no disc."""
    binary = shutil.which("makemkvcon")
    if not binary:
        return None
    try:
        result = subprocess.run([binary, "-r", "--cache=1", "info", "disc:9999"],
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines():
        if not line.startswith("MSG:"):
            continue
        fields = next(csv.reader([line[4:]]), [])
        message = fields[3] if len(fields) > 3 else ""
        if re.search(r"expired|too old|out of date|registration key|not registered|invalid key", message, re.I):
            return message
    return None


def status(path: str, today: date | None = None) -> dict:
    """What the page and the check show, from stored state and settings.conf only."""
    today = today or date.today()
    state = load_state(path)
    online = state.get("online") or {}
    seen = state.get("keys") or {}
    key = installed_key()
    result = {"supported": supported(), "settings": str(settings_path()), "libmmbd": libmmbd(),
              "players_use_libmmbd": players_use_libmmbd(),
              "makemkvcon": shutil.which("makemkvcon"),
              "installed": bool(key), "installed_expires": None, "days_left": None,
              "online_expires": online.get("expires"), "online_expiry_text": online.get("expiry_text"),
              "checked_at": online.get("checked_at"), "last_error": state.get("last_error"),
              "update_available": False, "level": "ok", "summary": "", "notify": False,
              "forum": FORUM_URL}
    if key:
        expires = (seen.get(fingerprint(key)) or {}).get("expires")
        result["installed_expires"] = expires
        if expires:
            result["days_left"] = (date.fromisoformat(expires) - today).days
    online_key = online.get("key")
    if online_key and online_key != key:
        newer = not result["installed_expires"] or not online.get("expires") \
            or online["expires"] > result["installed_expires"]
        result["update_available"] = newer
    days = result["days_left"]
    if not key:
        result["level"] = "error"
        result["summary"] = "No beta key in " + result["settings"]
    elif days is not None and days < 0:
        result["level"] = "error"
        result["summary"] = f"Installed key expired on {result['installed_expires']}"
    elif days is not None:
        result["level"] = "warning" if days <= URGENT_DAYS or result["update_available"] else "ok"
        result["summary"] = (f"Installed key valid until {result['installed_expires']} "
                             f"({days} day{'s' if days != 1 else ''} left)")
    else:
        result["level"] = "warning"
        result["summary"] = "Installed key's expiry is unknown (it was not seen online by this box)"
    if result["update_available"] and key:
        result["summary"] += "; a newer key is available" + (
            f", valid until {online['expires']}" if online.get("expires") else "")
    result["offer_update"] = bool(online_key) and (
        result["update_available"] or not key or (days is not None and days <= URGENT_DAYS))
    if not result["libmmbd"]:
        # Nothing here decrypts with the key: report, do not alarm or push an install.
        result["level"] = "warning" if result["level"] == "error" else result["level"]
        result["offer_update"] = False
    # The page banner: one week before the expiry, only where the players depend on the key.
    result["notify"] = result["players_use_libmmbd"] and (
        not key or (days is not None and days <= NOTIFY_DAYS))
    return result
