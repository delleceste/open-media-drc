"""Music magazines' recommendations, read from uploaded issues by the configured AI.

Issues (Gramophone, Diapason, BBC Music Magazine, ...) are uploaded as PDFs.  A
background job reads each one: the AI recognises the magazine and its cover month,
then reads the review pages for the recordings the magazine recommends (editors'
choices, awards, enthusiastic reviews; mixed and bad reviews are left out) and
writes, in English, a short artistic and a short sound summary of each.  Each
recommendation is looked up on Qobuz so it can be played.  Recommendations of the
same recording in different magazines are cross-referenced, and the month's
document (digest.md) puts the ones they agree on first.

    <root>/inbox/<upload>.pdf          uploaded, not read yet (or reading failed)
    <root>/<YYYY>/<MM>/<issue>.pdf     the issue, filed under its cover month
    <root>/<YYYY>/<MM>/<issue>.json    what was read from it
    <root>/<YYYY>/<MM>/digest.json     the shared verdicts of the agreed picks
    <root>/<YYYY>/<MM>/digest.md       the month's document

Magazines review the same release a month or two apart, so agreement is counted
over the issues of the two months either side as well; a month lists the
recordings reviewed in its own issues.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
from pathlib import Path

import qobuz_ai
from qobuz_ai import AIError
from qobuz_search import QobuzError

MAX_UPLOAD = 400 * 1024 * 1024
FREE_MARGIN = 1024 * 1024 * 1024        # never fill the disk the archive is on
MAX_TEXT = 300_000                       # review pages sent per issue, characters
CHUNK = 55_000                           # per AI call
WINDOW = 2                               # months either side counted as agreement
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")
_MONTH_WORDS = {
    1: "january janvier januar gennaio enero jan janv", 2: "february fevrier februar febbraio febrero feb fevr",
    3: "march mars marz marzo mar", 4: "april avril aprile abril apr avr",
    5: "may mai maggio mayo", 6: "june juin juni giugno junio jun",
    7: "july juillet juli luglio julio jul juil", 8: "august aout agosto aug",
    9: "september septembre settembre septiembre sep sept", 10: "october octobre oktober ottobre octubre oct okt",
    11: "november novembre noviembre nov", 12: "december decembre dezember dicembre diciembre dec dez"}
# A page naming one of these is read whatever else it holds: it lists the month's choices.
AWARD = re.compile(
    r"editor[’'`]?s[’'`]?\s+choice|recording\s+of\s+the\s+month|disc\s+of\s+the\s+month|diapason\s*d[’'`]\s*or"
    r"|\bchocs?\b|disques?\s+du\s+mois|stern\s+des\s+monats|cinq\s+diapasons|gramophone\s+choice"
    r"|\bclef\s+resmusica|★\s*★\s*★\s*★\s*★", re.I)
# What a review page is full of, in the magazines' languages.
REVIEW = re.compile(
    r"\b(?:record(?:ing|ed)|sound|engineer|balance|acoustic|enregistr\w*|prise\s+de\s+son|aufnahme|klang"
    r"|registrazione|orchestr\w*|symphon\w*|sinfoni\w*|concerto|konzert|sonat\w*|quartet\w*|quatuor"
    r"|piano|violin\w*|violon\w*|cello|violoncelle|soprano|mezzo|baritone?|tenor|conduct\w*|chef"
    r"|dirig\w*|opera|op[ée]ra|lieder|songs?|m[ée]lodies|cantat\w*|motets?|messe|mass|requiem"
    r"|hyperion|chandos|bis|harmonia\s+mundi|alpha|pentatone|decca|deutsche\s+grammophon|erato|warner"
    r"|sony|naxos|ecm|linn|channel|ondine|signum|onyx|orfeo|cpo|ricercar|apart[ée]|mirare|evidence"
    r"|la\s+dolce\s+volta|outhere|glossa|arcana|accent|challenge|capriccio|chateau|ramée|aparte|naive|naïve)\b",
    re.I)
CATALOGUE = re.compile(r"\b[A-Z]{2,6}[ -]?\d{3,7}\b")

PICK_FIELDS = ("composer", "works", "performers", "label", "catalogue", "album_title", "award",
               "rating", "artistic", "sound", "reviewer")
PICKS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"picks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {**{key: {"type": "string"} for key in PICK_FIELDS},
                       "artistic_score": {"type": "integer"}, "sound_score": {"type": "integer"},
                       "sound_exceptional": {"type": "boolean"}, "page": {"type": "integer"}},
        "required": [*PICK_FIELDS, "artistic_score", "sound_score", "sound_exceptional", "page"]}}},
    "required": ["picks"]}
ISSUE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"magazine": {"type": "string"}, "language": {"type": "string"},
                   "year": {"type": "integer"}, "month": {"type": "integer"}, "issue": {"type": "string"}},
    "required": ["magazine", "language", "year", "month", "issue"]}
CONSENSUS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"key": {"type": "string"}, "artistic": {"type": "string"}, "sound": {"type": "string"}},
        "required": ["key", "artistic", "sound"]}}},
    "required": ["verdicts"]}
# The card fields kept for a recommendation found on Qobuz (qobuz_search.album_card).
CARD_FIELDS = ("id", "title", "version", "artist", "composer", "label", "label_id", "genre", "date",
               "year", "image", "image_large", "tracks", "duration", "bits", "rate", "url", "streamable")

_lock = threading.Lock()
_job = {"running": False, "items": [], "detail": "", "error": "", "finished": 0}
_cancel = threading.Event()


class MagazineError(QobuzError):
    pass


# ── where ────────────────────────────────────────────────────────────────────

def root_dir(configured: str, state_dir: str) -> Path:
    return Path(configured).expanduser() if configured else Path(state_dir) / "magazines"


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _month_dir(root: Path, key: str) -> Path:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", key or ""):
        raise MagazineError("Invalid month.")
    return root / key[:4] / key[5:]


def month_label(key: str) -> str:
    return f"{MONTH_NAMES[int(key[5:]) - 1]} {key[:4]}"


def _shift(key: str, months: int) -> str:
    n = int(key[:4]) * 12 + int(key[5:]) - 1 + months
    return _month_key(n // 12, n % 12 + 1)


def months(root: Path) -> list[str]:
    """The months holding read issues, newest first."""
    found = set()
    for path in root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.json"):
        if path.name != "digest.json":
            found.add(f"{path.parent.parent.name}-{path.parent.name}")
    return sorted((m for m in found if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m)), reverse=True)


def issues(root: Path, key: str) -> list[dict]:
    folder = _month_dir(root, key)
    out = []
    for path in sorted(folder.glob("*.json")):
        if path.name == "digest.json":
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("picks"), list):
            data["month_key"] = key
            out.append(data)
    return out


def inbox(root: Path) -> list[Path]:
    return sorted((root / "inbox").glob("*.pdf"))


# The magazines' own names, whatever the AI or a file name calls them ("BBC Music",
# "Gramophone Magazine UK", "DIAPASON N° 745"): one name per magazine, so its
# issues file alike and two spellings never count as two magazines agreeing.
KNOWN_MAGAZINES = (
    (r"\bgramophone\b", "Gramophone"), (r"\bdiapason\b", "Diapason"),
    (r"\bbbc\b", "BBC Music Magazine"), (r"\bclassica\b", "Classica"),
    (r"\bfono\s*forum\b", "Fono Forum"), (r"\bfanfare\b", "Fanfare"),
    (r"\bamerican\s+record\s+guide\b", "American Record Guide"), (r"\bpizzicato\b", "Pizzicato"),
    (r"\bopernwelt\b", "Opernwelt"), (r"\bcrescendo\b", "Crescendo"), (r"\bamadeus\b", "Amadeus"),
    (r"\bmusica\b", "Musica"), (r"\bscherzo\b", "Scherzo"), (r"\britmo\b", "Ritmo"),
    (r"\blimelight\b", "Limelight"), (r"\bstereophile\b", "Stereophile"),
    (r"\babsolute\s+sound\b", "The Absolute Sound"), (r"\bhi\W?fi\s+news\b", "Hi-Fi News"),
    (r"\bresmusica\b", "ResMusica"), (r"\bopera\s+magazine\b|^opera$", "Opera"))


def canonical_magazine(name: str) -> str:
    """One name per magazine; an unknown one is cleaned of issue numbers, dates and
    "magazine", and capitalised as it was written (or title-cased if all one case)."""
    folded = _fold(name).replace("_", " ")
    for pattern, canonical in KNOWN_MAGAZINES:
        if re.search(pattern, folded.strip()):
            return canonical
    months = "|".join(w for words in _MONTH_WORDS.values() for w in words.split())
    text = re.sub(r"\b(?:n[°o]\.?|no\.?|nr\.?|issue|num[eé]ro)\s*\d+\b|\b(?:19|20)\d\d\b|\b\d+\b", " ", name or "", flags=re.I)
    text = re.sub(rf"\b(?:{months})\b\.?", " ", text, flags=re.I)
    text = re.sub(r"\b(?:magazine|uk|edition|pdf)\b", " ", text, flags=re.I)
    text = re.sub(r"[_\s\-–—·|/,.]+", " ", text).strip(" -")
    if not text:
        return "Magazine"
    return text.title() if text.isupper() or text.islower() else text


def issue_slug(magazine: str, key: str) -> str:
    """The issue's file name: <magazine>-<YYYY>-<MM>, lower case ASCII and hyphens."""
    return f"{_safe(canonical_magazine(magazine), 50)}-{key}"


def _safe(text: str, limit: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "issue"


# ── upload ───────────────────────────────────────────────────────────────────

def check_space(root: Path, size: int | None) -> None:
    if size is not None and size > MAX_UPLOAD * 20:
        raise MagazineError("Upload fewer issues at once (at most 20, 400 MB each).")
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        raise MagazineError(f"The magazine folder {root} cannot be reached.") from None
    if free - (size or 0) < FREE_MARGIN:
        raise MagazineError(f"Not enough free space for the issues in {root} "
                            f"({free // (1024 * 1024)} MB free); set magazine_dir in [qobuz_search].")


def save_upload(root: Path, stream, filename: str) -> Path:
    """Write one uploaded PDF into the inbox; `stream` is a file-like object."""
    folder = root / "inbox"
    folder.mkdir(parents=True, exist_ok=True)
    stem = _safe(Path(filename or "issue").stem)
    target = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{stem}.pdf"
    fd, name = tempfile.mkstemp(prefix=".upload-", dir=folder)
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            head = stream.read(5)
            if head != b"%PDF-":
                raise MagazineError(f"{filename or 'The file'} is not a PDF.")
            out.write(head)
            size = len(head)
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > MAX_UPLOAD:
                    raise MagazineError(f"{filename} is larger than 400 MB.")
                out.write(block)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return target


def remove_upload(root: Path, name: str) -> bool:
    if not re.fullmatch(r"[0-9A-Za-z._-]{1,120}\.pdf", name or ""):
        raise MagazineError("Invalid file.")
    with _lock:
        if _job["running"] and any(i["file"] == name and i["state"] == "reading" for i in _job["items"]):
            raise MagazineError("That issue is being read; stop first.")
    path = root / "inbox" / name
    if not path.is_file():
        return False
    path.unlink()
    with _lock:
        _job["items"] = [i for i in _job["items"] if i["file"] != name]
    return True


def remove_issue(root: Path, key: str, slug: str) -> bool:
    if not re.fullmatch(r"[a-z0-9-]{1,90}", slug or ""):
        raise MagazineError("Invalid issue.")
    folder = _month_dir(root, key)
    found = False
    for path in (folder / f"{slug}.json", folder / f"{slug}.pdf"):
        if path.is_file():
            path.unlink()
            found = True
    if found:
        for near in range(-WINDOW, WINDOW + 1):
            near_key = _shift(key, near)
            if _month_dir(root, near_key).is_dir():
                write_document(root, near_key)
        if not any(p.name != "digest.json" for p in folder.glob("*.json")):
            for leftover in ("digest.json", "digest.md"):
                (folder / leftover).unlink(missing_ok=True)
            for empty in (folder, folder.parent):
                try:
                    empty.rmdir()
                except OSError:
                    break
    return found


def pdf_path(root: Path, key: str, slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,90}", slug or ""):
        raise MagazineError("Invalid issue.")
    path = _month_dir(root, key) / f"{slug}.pdf"
    if not path.is_file():
        raise MagazineError("That issue's PDF is not on the box.")
    return path


# ── reading an issue ─────────────────────────────────────────────────────────

def pdf_pages(path: Path) -> list[str]:
    """The text of each page, in reading order (a magazine's columns stay apart)."""
    try:
        result = subprocess.run(["nice", "-n", "19", "pdftotext", "-enc", "UTF-8", str(path), "-"],
                                capture_output=True, timeout=300, check=True)
    except FileNotFoundError:
        raise MagazineError("pdftotext (poppler) is not installed on the box.") from None
    except (OSError, subprocess.SubprocessError):
        raise MagazineError("Could not read the PDF's text.") from None
    text = result.stdout.decode("utf-8", errors="replace").replace("\x00", "")
    return [re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", page)).strip() for page in text.split("\f")]


def _pdf_info(path: Path) -> str:
    try:
        out = subprocess.run(["pdfinfo", str(path)], capture_output=True, timeout=20).stdout
        return "\n".join(line for line in out.decode("utf-8", "replace").splitlines()
                         if line.split(":", 1)[0] in ("Title", "Subject", "Author", "CreationDate"))[:600]
    except (OSError, subprocess.SubprocessError):
        return ""


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def guess_issue(filename: str, pages: list[str]) -> dict:
    """The month and year from the file name or the cover's text, without the AI."""
    for source in (filename, "\n".join(pages[:3])[:6000]):
        text = _fold(source).replace("_", " ")
        for match in re.finditer(r"\b([a-z]{3,10})\.?[\s_./-]*(20\d\d)\b", text):
            month = next((n for n, words in _MONTH_WORDS.items() if match[1] in words.split()), 0)
            if month:
                return {"year": int(match[2]), "month": month}
        match = re.search(r"\b(20\d\d)[-_.](0[1-9]|1[0-2])\b", text)
        if match:
            return {"year": int(match[1]), "month": int(match[2])}
    return {"year": 0, "month": 0}


def select_pages(pages: list[str], budget: int = MAX_TEXT) -> list[tuple[int, str]]:
    """The review pages, by their likeness to reviews, within `budget` characters:
    every page naming an award first, then the densest review pages."""
    scored = []
    for number, text in enumerate(pages, 1):
        if len(text) < 400:
            continue
        award = bool(AWARD.search(text))
        density = (len(REVIEW.findall(text)) + 2 * len(CATALOGUE.findall(text))) * 1000 / len(text)
        if award or density >= 4:
            scored.append((award, density, number, text))
    scored.sort(key=lambda s: (not s[0], -s[1]))
    chosen, used = [], 0
    for award, _density, number, text in scored:
        if used + len(text) > budget:
            continue
        chosen.append((number, text))
        used += len(text)
    return sorted(chosen)


def chunks(selected: list[tuple[int, str]], size: int = CHUNK) -> list[str]:
    parts, current = [], ""
    for number, text in selected:
        block = f"[Page {number}]\n{text[:size - 20]}\n\n"
        if current and len(current) + len(block) > size:
            parts.append(current)
            current = ""
        current += block
    if current:
        parts.append(current)
    return parts


def _structured(cfg, prompt, name, schema, cancel, post=None, max_tokens=12000, timeout=300):
    if cancel is not None and cancel.is_set():
        raise AIError("Reading stopped.")
    if post is not None:
        transport = post
    elif cfg["provider"] == "claude_account":
        transport = lambda config, body, limit: qobuz_ai._claude_post(config, body, limit, cancel=cancel)  # noqa: E731
    else:
        transport = qobuz_ai._post
    if cfg["provider"] == "openai":
        result = transport(cfg, {"model": cfg["model"], "store": False, "max_output_tokens": max_tokens,
                                 "input": prompt, "tools": [{"type": "function", "name": name, "strict": True,
                                                             "parameters": schema}],
                                 "tool_choice": {"type": "function", "name": name}}, timeout)
        calls = [x for x in result.get("output", []) if x.get("type") == "function_call" and x.get("name") == name]
        answer = qobuz_ai._json(calls[0].get("arguments", "")) if calls else {}
    else:
        result = transport(cfg, {"model": cfg["model"], "max_tokens": max_tokens,
                                 "messages": [{"role": "user", "content": prompt}],
                                 "tools": [{"name": name, "input_schema": schema}],
                                 "tool_choice": {"type": "tool", "name": name}}, timeout)
        calls = [x for x in result.get("content", []) if x.get("type") == "tool_use" and x.get("name") == name]
        answer = calls[0].get("input", {}) if calls else {}
    if cancel is not None and cancel.is_set():
        raise AIError("Reading stopped.")
    return answer if isinstance(answer, dict) else {}


def _ready(cfg):
    if cfg["provider"] == "claude_account" and not qobuz_ai.account_ready():
        raise AIError("Sign in to Claude Code on this server as the web service user first.")
    if cfg["provider"] != "claude_account" and not cfg["key"]:
        raise AIError("Add your API key in AI settings first.")


def identify(cfg, path: Path, original: str, pages: list[str], cancel=None, post=None) -> dict:
    guess = guess_issue(original, pages)
    first = "\n\n".join(f"[Page {n}]\n{p[:3000]}" for n, p in enumerate(pages[:4], 1))
    prompt = qobuz_ai._prompt(cfg["state_dir"], "magazine-identify",
                              file=f"File name: {original}\n{_pdf_info(path)}", pages=first)
    try:
        answer = _structured(cfg, prompt, "magazine_issue", ISSUE_SCHEMA, cancel, post, max_tokens=1000, timeout=180)
    except AIError as error:
        if "stopped" in str(error):
            raise
        answer = {}
    year, month = answer.get("year"), answer.get("month")
    if not (type(year) is int and 1950 <= year <= 2100 and type(month) is int and 1 <= month <= 12):
        year, month = guess["year"], guess["month"]
    magazine = canonical_magazine(str(answer.get("magazine") or "").strip()[:80] or Path(original).stem[:80])
    language = str(answer.get("language") or "").strip().lower()[:2]
    return {"magazine": magazine, "language": language, "year": year, "month": month,
            "issue": str(answer.get("issue") or "").strip()[:120]}


def _clean_pick(raw: dict, pages: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    pick = {key: str(raw.get(key) or "").strip()[:2000 if key in ("artistic", "sound") else 400]
            for key in PICK_FIELDS}
    if not (pick["works"] or pick["album_title"]) or not (pick["performers"] or pick["composer"]):
        return None
    clamp = lambda v, lo: v if type(v) is int and lo <= v <= 10 else lo  # noqa: E731
    pick["artistic_score"] = clamp(raw.get("artistic_score"), 1)
    pick["sound_score"] = clamp(raw.get("sound_score"), 0)
    pick["sound_exceptional"] = raw.get("sound_exceptional") is True
    page = raw.get("page")
    pick["page"] = page if type(page) is int and 1 <= page <= pages else 0
    # Bad reviews never come through, whatever slipped into the answer.
    if pick["artistic_score"] < 6 and not pick["award"]:
        return None
    return pick


def extract(cfg, issue: dict, pages: list[str], cancel=None, post=None, progress=None) -> list[dict]:
    parts = chunks(select_pages(pages))
    if not parts:
        raise MagazineError("No review pages were found in this PDF (is it a scan without text?).")
    name = f"{issue['magazine']} ({issue['language'] or 'unknown language'}), " + (
        issue["issue"] or (month_label(_month_key(issue["year"], issue["month"])) if issue["month"] else ""))
    picks = []
    for n, part in enumerate(parts, 1):
        if progress:
            progress(f"Reading the recommendations, part {n} of {len(parts)}")
        prompt = qobuz_ai._prompt(cfg["state_dir"], "magazine-picks", magazine=name, pages=part)
        answer = _structured(cfg, prompt, "magazine_picks", PICKS_SCHEMA, cancel, post)
        for raw in answer.get("picks") if isinstance(answer.get("picks"), list) else []:
            pick = _clean_pick(raw, len(pages))
            if pick:
                picks.append(pick)
    # The same recording from a summary page and its review: keep the fuller one.
    kept: list[dict] = []
    for pick in picks:
        twin = next((k for k in kept if same_recording(k, pick)), None)
        if twin is None:
            kept.append(pick)
        else:
            richer = pick if len(pick["artistic"]) + len(pick["sound"]) > len(twin["artistic"]) + len(twin["sound"]) else twin
            merged = {**richer, "award": twin["award"] or pick["award"], "rating": twin["rating"] or pick["rating"],
                      "artistic_score": max(twin["artistic_score"], pick["artistic_score"]),
                      "sound_score": max(twin["sound_score"], pick["sound_score"]),
                      "sound_exceptional": twin["sound_exceptional"] or pick["sound_exceptional"]}
            kept[kept.index(twin)] = merged
    return kept


# ── the same recording in two reviews ────────────────────────────────────────

_GENERIC = set("""orchestra orchestre orchester orchestre orquesta orchestra philharmonic philharmonie philharmoniker
symphony symphonique sinfonie sinfonieorchester chamber chambre kammer choir chorus choeur chor coro ensemble
quartet quatuor quartett trio consort players soloists singers band baroque barocca baroque academy academie
akademie national nationale radio de du des la le les the of and et und di del della von van der
piano violin cello soprano tenor baritone bass mezzo conductor direction dir various artists""".split())
_WORK_STOP = set("""no nr op bwv kv k hob d rv in major minor dur moll majeur mineur and et und the de la le les des
du of for pour fur a an complete integrale works oeuvres werke vol volume sonata sonatas symphony symphonies
concerto concertos quartet quartets string piano music musique""".split())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _fold(text))


def _surnames(names: str) -> set[str]:
    text = re.sub(r"\([^)]*\)", " ", names or "")
    out = set()
    for part in re.split(r"[,;/&+]|\band\b|\bet\b|\bund\b|\bwith\b", text, flags=re.I):
        words = [w for w in _words(part) if len(w) >= 3 and w not in _GENERIC]
        if words:
            out.add(words[-1])
    return out


def _work_words(pick: dict) -> set[str]:
    return {w for w in _words(pick.get("works", "") + " " + pick.get("album_title", ""))
            if w not in _WORK_STOP and (len(w) >= 3 or w.isdigit())}


def _catalogue(pick: dict) -> str:
    value = re.sub(r"[^a-z0-9]", "", _fold(pick.get("catalogue", "")))
    return value if len(value) >= 5 and re.search(r"\d{3}", value) else ""


def same_recording(a: dict, b: dict) -> bool:
    qa, qb = (a.get("qobuz") or {}).get("id"), (b.get("qobuz") or {}).get("id")
    if qa and qb:
        return qa == qb
    ca, cb = _catalogue(a), _catalogue(b)
    if ca and cb and ca == cb:
        return True
    performers = _surnames(a.get("performers", "")) & _surnames(b.get("performers", ""))
    if not performers:
        return False
    composers_a, composers_b = _surnames(a.get("composer", "")), _surnames(b.get("composer", ""))
    if composers_a and composers_b and not composers_a & composers_b:
        return False
    wa, wb = _work_words(a), _work_words(b)
    if not wa or not wb:
        return bool(composers_a & composers_b)
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.3


# ── Qobuz ────────────────────────────────────────────────────────────────────

def _query(pick: dict) -> str:
    performers = re.sub(r"\([^)]*\)", " ", pick["performers"])
    names = [n.strip() for n in re.split(r"[,;/&]|\band\b|\bet\b", performers) if n.strip()]
    # an orchestra and its conductor, a singer and the pianist: both are how Qobuz names the album
    lead = " ".join(names[:1] + [n.split()[-1] for n in names[1:2]])
    composer = re.split(r"[,;/&]", pick["composer"])[0].strip()
    composer = composer.split()[-1] if composer else ""
    title = pick["album_title"] or pick["works"]
    title = " ".join(w for w in re.sub(r"[^\w\s'-]", " ", title).split()[:5])
    return " ".join(x for x in (lead, composer, title if not composer or len(title) < 40 else "") if x)[:180]


def _card_score(card: dict, pick: dict, year: int) -> int:
    hay = _fold(" ".join(str(card.get(k) or "") for k in ("title", "version", "artist", "composer")))
    words = set(re.findall(r"[a-z0-9]+", hay))
    performers = _surnames(pick["performers"]) & words
    if not performers:
        return 0
    score = 3 * min(2, len(performers))
    if _surnames(pick["composer"]) & words:
        score += 2
    label_words = {w for w in _words(pick["label"]) if len(w) >= 3 and w not in ("records", "classics", "music")}
    if label_words & set(_words(card.get("label", ""))):
        score += 2
    score += min(4, len(_work_words(pick) & set(_words(str(card.get("title", ""))))))
    card_year = card.get("year")
    if year and isinstance(card_year, int):
        score += 1 if card_year >= year - 2 else -2 if card_year < year - 6 else 0
    return score


def _queries(pick: dict) -> list[str]:
    """From the most precise to the broadest: Qobuz wants every word of a query, so a
    work's printed title ("Symphonies Nos 1-7") can hide the album a name finds."""
    composer = re.split(r"[,;/&]", pick["composer"])[0].strip()
    composer = composer.split()[-1] if composer else ""
    names = [n.strip() for n in re.split(r"[,;/&]|\band\b|\bet\b", re.sub(r"\([^)]*\)", " ", pick["performers"]))
             if n.strip()]
    found = [_query(pick)] + [f"{n.split()[-1]} {composer}".strip() for n in reversed(names[:3])]
    if pick["album_title"]:
        found.append(f"{pick['album_title'][:80]} {names[0].split()[-1] if names else ''}".strip())
    return [q for i, q in enumerate(found) if q and q not in found[:i]][:4]


def find_on_qobuz(catalog, pick: dict, year: int) -> dict | None:
    for query in _queries(pick):
        try:
            answer = catalog.search(text=query, enrich=False, scan=50)
        except QobuzError:
            continue
        best, best_score = None, 4
        for card in answer.get("results", [])[:30]:
            if card.get("source") == "local" or card.get("streamable") is False or not card.get("id"):
                continue
            score = _card_score(card, pick, year)
            if score > best_score:
                best, best_score = card, score
        if best:
            return {k: best.get(k) for k in CARD_FIELDS}
    return None


# ── the job ──────────────────────────────────────────────────────────────────

def status(root: Path) -> dict:
    waiting = {p.name for p in inbox(root)} if root else set()
    with _lock:
        items = [dict(i) for i in _job["items"] if i["file"] in waiting or i["state"] in ("done", "error")]
        known = {i["file"] for i in items}
        items += [{"file": name, "name": _display(name), "state": "waiting", "detail": ""}
                  for name in sorted(waiting - known)]
        return {"running": _job["running"], "detail": _job["detail"], "error": _job["error"],
                "items": items, "finished": _job["finished"]}


def _display(name: str) -> str:
    return re.sub(r"^\d{8}-\d{6}-[0-9a-f]{6}-", "", name)


def _set(**values):
    with _lock:
        _job.update(values)


def _item(name: str, **values):
    with _lock:
        for item in _job["items"]:
            if item["file"] == name:
                item.update(values)


def start(root: Path, state_dir: str, catalog_fn, post=None) -> dict:
    """Read every PDF waiting in the inbox, one after another, in the background.
    Issues uploaded while a job runs join its queue."""
    files = inbox(root)
    if not files:
        raise MagazineError("No uploaded issue is waiting to be read.")
    cfg = qobuz_ai.configuration(state_dir)
    _ready(cfg)
    with _lock:
        running = _job["running"]
        if not running:
            _cancel.clear()
            _job.update(running=True, detail="Starting", error="", items=[])
        pending = {i["file"] for i in _job["items"] if i["state"] in ("queued", "reading")}
        _job["items"] = [i for i in _job["items"] if i["file"] not in {p.name for p in files} or i["file"] in pending]
        _job["items"] += [{"file": p.name, "name": _display(p.name), "state": "queued", "detail": ""}
                          for p in files if p.name not in pending]
    if not running:
        threading.Thread(target=_run, args=(root, cfg, catalog_fn, post), name="magazines", daemon=True).start()
    return {"started": True, "queued": running}


def stop() -> None:
    _cancel.set()


def _log(text: str) -> None:
    print(f"magazines: {text}", file=sys.stderr, flush=True)


def _next() -> str | None:
    with _lock:
        for item in _job["items"]:
            if item["state"] == "queued":
                item.update(state="reading", detail="Reading the PDF")
                return item["file"]
    return None


def _run(root: Path, cfg, catalog_fn, post):
    touched = set()
    try:
        while True:
            while not _cancel.is_set() and (name := _next()):
                path = root / "inbox" / name
                if not path.is_file():
                    _item(name, state="error", detail="The upload is no longer on the box.")
                    continue
                _log(f"reading {name}")
                try:
                    issue = read_issue(root, cfg, path, catalog_fn, cancel=_cancel, post=post,
                                       progress=lambda text, n=name: (_item(n, detail=text), _set(detail=text)))
                    touched.add(issue["month_key"])
                    _item(name, state="done", detail=f"{issue['magazine']}, {month_label(issue['month_key'])}: "
                          f"{len(issue['picks'])} recommendations", month=issue["month_key"])
                    _log(f"{name}: {issue.get('slug', issue['month_key'])}, {len(issue['picks'])} recommendations")
                except (QobuzError, OSError) as error:
                    stopped = _cancel.is_set()
                    _item(name, state="waiting" if stopped else "error",
                          detail="Stopped" if stopped else str(error)[:300])
                    _log(f"{name}: {'stopped' if stopped else error}")
            for key in sorted(touched):
                for near in range(-WINDOW, WINDOW + 1):
                    near_key = _shift(key, near)
                    if not _month_dir(root, near_key).is_dir() or _cancel.is_set():
                        continue
                    _set(detail=f"Cross-referencing {month_label(near_key)}")
                    try:
                        consensus(root, cfg, near_key, cancel=_cancel, post=post)
                    except (QobuzError, OSError) as error:
                        _set(error=f"Shared verdicts for {month_label(near_key)}: {str(error)[:200]}")
                        _log(f"shared verdicts {near_key}: {error}")
                    write_document(root, near_key)
            touched.clear()
            with _lock:
                # an upload made meanwhile was queued: read it too, else this job ends here
                if _cancel.is_set() or not any(i["state"] == "queued" for i in _job["items"]):
                    for item in _job["items"]:
                        if item["state"] == "queued":
                            item.update(state="waiting", detail="Stopped")
                    _job.update(running=False, detail="Stopped" if _cancel.is_set() else "",
                                finished=int(time.time()))
                    return
    except Exception as error:      # noqa: BLE001 - never leave the job marked running
        _log(f"job failed: {error!r}")
        raise
    finally:
        with _lock:
            if _job["running"]:
                _job.update(running=False, detail="", finished=int(time.time()))


def read_issue(root: Path, cfg, path: Path, catalog_fn, cancel=None, post=None, progress=None,
               pages: list[str] | None = None) -> dict:
    """Read one uploaded PDF, file it under its month, and return what was read."""
    say = progress or (lambda text: None)
    original = _display(path.name)
    pages = pdf_pages(path) if pages is None else pages
    if not any(len(p) >= 400 for p in pages):
        raise MagazineError("This PDF has almost no text (a scan without a text layer cannot be read).")
    say("Recognising the magazine and its month")
    issue = identify(cfg, path, original, pages, cancel, post)
    picks = extract(cfg, issue, pages, cancel, post, say)
    catalog = catalog_fn()
    for n, pick in enumerate(picks, 1):
        if cancel is not None and cancel.is_set():
            raise AIError("Reading stopped.")
        say(f"Finding the recordings on Qobuz, {n} of {len(picks)}")
        pick["query"] = _query(pick)
        pick["qobuz"] = find_on_qobuz(catalog, pick, issue["year"])
    if issue["month"]:
        key = _month_key(issue["year"], issue["month"])
        folder = _month_dir(root, key)
    else:
        key, folder = "", root / "undated"
    slug = issue_slug(issue["magazine"], key) if key else issue_slug(issue["magazine"], "undated-" + path.stem[16:22])
    folder.mkdir(parents=True, exist_ok=True)
    data = {**issue, "slug": slug, "uploaded": original, "pages": len(pages), "read_at": int(time.time()),
            "provider": cfg["provider"], "model": cfg["model"], "picks": picks}
    qobuz_ai._write_json(folder / f"{slug}.json", data)
    shutil.move(str(path), str(folder / f"{slug}.pdf"))
    if not key:
        raise MagazineError(f"Read {issue['magazine']}, but its month could not be recognised: "
                            f"filed under {folder}.")
    return {**data, "month_key": key}


# ── a month: the recommendations, cross-referenced ───────────────────────────

def _review(issue: dict, index: int, pick: dict) -> dict:
    return {"id": f"{issue['slug']}#{index}", "magazine": issue.get("magazine", ""), "month": issue["month_key"],
            "issue": issue.get("issue") or month_label(issue["month_key"]), "slug": issue["slug"],
            **{k: pick.get(k) for k in ("award", "rating", "artistic", "sound", "reviewer", "page",
                                         "artistic_score", "sound_score", "sound_exceptional",
                                         "composer", "works", "performers", "label", "catalogue",
                                         "album_title", "query")},
            "qobuz": pick.get("qobuz")}


def clusters(root: Path, key: str) -> list[list[dict]]:
    """The recordings reviewed in this month's issues, each with every review of it
    within WINDOW months either side."""
    reviews = []
    for near in range(-WINDOW, WINDOW + 1):
        near_key = _shift(key, near)
        if not _month_dir(root, near_key).is_dir():
            continue
        for issue in issues(root, near_key):
            reviews += [_review(issue, i, p) for i, p in enumerate(issue["picks"]) if isinstance(p, dict)]
    groups: list[list[dict]] = []
    for review in reviews:
        hits = [g for g in groups if any(same_recording(r, review) for r in g)]
        if not hits:
            groups.append([review])
            continue
        merged = [review] + [r for g in hits for r in g]
        groups = [g for g in groups if g not in hits] + [merged]
    return [g for g in groups if any(r["month"] == key for r in g)]


def _signature(group: list[dict]) -> str:
    return hashlib.sha1("|".join(sorted(r["id"] for r in group)).encode()).hexdigest()[:16]


def _digest(root: Path, key: str) -> dict:
    try:
        data = json.loads((_month_dir(root, key) / "digest.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def entry(group: list[dict], key: str, verdicts: dict) -> dict:
    group = sorted(group, key=lambda r: (r["month"] != key, -(r["artistic_score"] or 0)))
    magazines = []
    for r in group:
        if r["magazine"] not in magazines:
            magazines.append(r["magazine"])
    sounds = [r["sound_score"] for r in group if r.get("sound_score")]
    quality = round(sum(sounds) / len(sounds), 1) if sounds else None
    lead = max(group, key=lambda r: (bool(r.get("qobuz")), r["month"] == key, r["artistic_score"] or 0))
    card = dict(lead["qobuz"]) if lead.get("qobuz") else {
        "id": "", "title": lead.get("album_title") or lead.get("works") or "", "version": "",
        "artist": lead.get("performers", ""), "composer": lead.get("composer", ""), "label": lead.get("label", ""),
        "year": None, "image": "", "streamable": False}
    verdict = verdicts.get(_signature(group)) if len(magazines) > 1 else None
    best = max(group, key=lambda r: (r["artistic_score"] or 0, len(r["artistic"] or "")))
    sound_best = max(group, key=lambda r: (r["sound_score"] or 0, len(r["sound"] or "")))
    card["mag"] = {
        "key": _signature(group), "agreement": len(magazines), "magazines": magazines,
        "awards": [f"{r['magazine']}: {r['award']}" for r in group if r.get("award")],
        "quality": quality, "q": any(r.get("sound_exceptional") for r in group) or (quality or 0) >= 9,
        "artistic": (verdict or {}).get("artistic") or best["artistic"],
        "sound": (verdict or {}).get("sound") or sound_best["sound"],
        "shared": bool(verdict), "query": lead.get("query") or "",
        "works": lead.get("works", ""), "performers": lead.get("performers", ""), "composer": lead.get("composer", ""),
        "reviews": [{k: r.get(k) for k in ("magazine", "issue", "month", "slug", "award", "rating", "artistic",
                                           "sound", "reviewer", "page", "artistic_score", "sound_score",
                                           "sound_exceptional", "catalogue")} for r in group]}
    return card


def _rank(card: dict):
    m = card["mag"]
    scores = [r["artistic_score"] or 0 for r in m["reviews"]]
    return (-m["agreement"], -len(m["awards"]), -(sum(scores) / len(scores) + 0.5 * (m["quality"] or 0)),
            not card.get("id"))


def month_view(root: Path, key: str) -> dict:
    verdicts = _digest(root, key).get("verdicts") or {}
    cards = sorted((entry(g, key, verdicts) for g in clusters(root, key)), key=_rank)
    shown = [{k: i.get(k) for k in ("slug", "magazine", "issue", "language", "uploaded", "pages", "read_at")}
             | {"picks": len(i["picks"])} for i in issues(root, key)]
    return {"month": key, "label": month_label(key), "issues": shown, "entries": cards,
            "agreed": sum(1 for c in cards if c["mag"]["agreement"] > 1)}


def consensus(root: Path, cfg, key: str, cancel=None, post=None) -> None:
    """A shared English verdict for each recording more than one magazine recommends;
    kept by the set of reviews it was made from, so it is asked for once."""
    digest = _digest(root, key)
    verdicts = digest.get("verdicts") if isinstance(digest.get("verdicts"), dict) else {}
    wanted = {}
    for group in clusters(root, key):
        if len({r["magazine"] for r in group}) < 2:
            continue
        sig = _signature(group)
        if sig in verdicts:
            continue
        wanted[sig] = {"key": sig, "recording": {k: group[0].get(k) for k in ("composer", "works", "performers")},
                       "reviews": [{k: r.get(k) for k in ("magazine", "issue", "award", "rating", "artistic", "sound")}
                                   for r in group]}
    live = {_signature(g) for g in clusters(root, key)}
    verdicts = {k: v for k, v in verdicts.items() if k in live}
    if wanted:
        prompt = qobuz_ai._prompt(cfg["state_dir"], "magazine-consensus",
                                  data=json.dumps(list(wanted.values()), ensure_ascii=False)[:60000])
        answer = _structured(cfg, prompt, "magazine_consensus", CONSENSUS_SCHEMA, cancel, post)
        for v in answer.get("verdicts") if isinstance(answer.get("verdicts"), list) else []:
            if isinstance(v, dict) and v.get("key") in wanted and isinstance(v.get("artistic"), str):
                verdicts[v["key"]] = {"artistic": v["artistic"].strip()[:2000],
                                      "sound": str(v.get("sound") or "").strip()[:1500]}
    folder = _month_dir(root, key)
    folder.mkdir(parents=True, exist_ok=True)
    qobuz_ai._write_json(folder / "digest.json", {"verdicts": verdicts, "made_at": int(time.time())})


def document(root: Path, key: str) -> str:
    """The month's recommendations as a Markdown document, in English."""
    view = month_view(root, key)
    lines = [f"# Magazine recommendations — {view['label']}", ""]
    lines += ["Issues read: " + "; ".join(f"{i['magazine']} ({i['issue'] or view['label']})"
                                           for i in view["issues"]), ""]
    lines += [f"Recordings recommended by more than one magazine come first. Agreement also counts the issues of "
              f"the {WINDOW} months before and after. Sound quality is the reviews' rating of the recording "
              "(depth, detail, soundstage, warmth), 10 being the best; Q marks an outstanding recording.", ""]
    agreed = [c for c in view["entries"] if c["mag"]["agreement"] > 1]
    single = [c for c in view["entries"] if c["mag"]["agreement"] == 1]
    for title, cards in (("Recommended by several magazines", agreed), ("Recommended by one magazine", single)):
        if not cards:
            continue
        lines += [f"## {title}", ""]
        for card in cards:
            m = card["mag"]
            head = " — ".join(x for x in (m["composer"], m["works"] or card.get("title"), m["performers"]) if x)
            lines.append(f"### {head}")
            facts = [", ".join(m["magazines"])]
            if m["quality"] is not None:
                facts.append(f"sound {m['quality']}/10" + (" (Q)" if m["q"] else ""))
            elif m["q"]:
                facts.append("sound: Q")
            if card.get("id"):
                facts.append(f"[Qobuz: {card.get('title')} — {card.get('artist')}]({card.get('url')})")
            else:
                facts.append("not found on Qobuz")
            lines += ["*" + " · ".join(facts) + "*", ""]
            if m["awards"]:
                lines += ["**Awards:** " + "; ".join(m["awards"]), ""]
            if m["shared"]:
                lines += [f"**Shared verdict:** {m['artistic']}", ""]
                if m["sound"]:
                    lines += [f"**Sound:** {m['sound']}", ""]
            for r in m["reviews"]:
                where = f"{r['magazine']}, {r['issue']}" + (f", p. {r['page']}" if r.get("page") else "")
                extra = " · ".join(x for x in (r.get("award"), r.get("rating"), r.get("reviewer")) if x)
                lines.append(f"- **{where}**" + (f" ({extra})" if extra else "") + f": {r.get('artistic') or ''}"
                             + (f" *Sound:* {r['sound']}" if r.get("sound") else ""))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_document(root: Path, key: str) -> str:
    text = document(root, key)
    folder = _month_dir(root, key)
    if folder.is_dir():
        fd, name = tempfile.mkstemp(prefix=".digest-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(text)
            os.replace(name, folder / "digest.md")
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return text
