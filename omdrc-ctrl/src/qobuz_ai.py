"""Bounded, read-only AI research and selection of real Qobuz releases.

Provider secrets live in the service state directory, never in browser storage.
The model can select only candidates found by the catalog; citations must come
from the provider's web search, and selected releases are checked once more.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
from string import Template
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from qobuz_search import QobuzError, is_cd_quality

DEFAULT_MODELS = {"openai": "gpt-5.4", "claude": "claude-sonnet-4-6",
                  "claude_account": "claude-sonnet-4-6"}
ENDPOINTS = {"openai": "https://api.openai.com/v1/responses",
             "claude": "https://api.anthropic.com/v1/messages"}
MAX_BYTES = 2 * 1024 * 1024
_settings_lock = threading.Lock()
_research_lock = threading.Lock()
_account_status = (0.0, False)
_account_lock = threading.Lock()
_account_usage = None


class AIError(QobuzError):
    pass


def recommendation_prompt(state_dir, request, count=None):
    """Return the exact first user message used for recommendation research."""
    if not isinstance(request, str) or not 1 <= len(request.strip()) <= 2000:
        raise AIError("Enter a request of up to 2,000 characters.")
    if count is not None and (type(count) is not int or not 1 <= count <= 6):
        raise AIError("Ask for between one and six albums.")
    quantity = (f"Select up to {count} recordings." if count is not None else
                "Use the number of recordings requested by the user; if unspecified, choose a suitable number. "
                "Return at most 20 recordings.")
    return _prompt(state_dir, "recommend-research", quantity=quantity, request=request.strip())


# Prompt templates and the ${placeholders} each one must keep. The shipped
# defaults sit next to this module; <state_dir>/prompts/<name>.txt overrides one.
PROMPT_DIR = Path(__file__).resolve().with_name("prompts")
PROMPTS = {"system": (), "listening-research": ("metadata",),
           "listening-guide": ("metadata", "research"),
           "recommend-research": ("quantity", "request"),
           "recommend-queries": ("research",), "recommend-select": ("quantity", "data")}


def prompt_path(state_dir, name):
    """The file a prompt is read from: the box's override if present, else the default."""
    if state_dir:
        custom = Path(state_dir) / "prompts" / f"{name}.txt"
        if custom.is_file():
            return custom
    return PROMPT_DIR / f"{name}.txt"


def _prompt(state_dir, name, **values):
    path = prompt_path(state_dir, name)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise AIError(f"Could not read the AI prompt {path}.") from None
    # The leading '#' lines say where the prompt is used; they are never sent.
    while lines and (lines[0].startswith("#") or not lines[0].strip()):
        lines.pop(0)
    text = "\n".join(lines).strip()
    template = Template(text)
    found = {m.group("named") or m.group("braced") for m in template.pattern.finditer(text)}
    missing = [key for key in PROMPTS[name] if key not in found]
    if missing or not text:
        raise AIError(f"The AI prompt {path} must contain " +
                      (", ".join("${" + key + "}" for key in missing) or "text") + ".")
    # safe_substitute leaves a stray $ or an unknown ${name} in custom text as written.
    return template.safe_substitute(values)


def _claude_binary():
    # rc.d and systemd have smaller PATHs than an interactive login shell.
    # A host-specific override belongs to the service environment, not the UI.
    override = os.environ.get("OMDRC_CLAUDE_BIN", "").strip()
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_absolute() and path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which("claude")
    if found:
        return found
    for path in (Path.home() / ".local/bin/claude", Path("/usr/local/bin/claude")):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _account_environment():
    env = os.environ.copy()
    # This mode deliberately uses account login, never a separately billed key.
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                 "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                 "CLAUDE_CODE_USE_FOUNDRY", "CLAUDECODE", "CLAUDE_CODE_SIMPLE"):
        env.pop(name, None)
    return env


def account_ready():
    global _account_status
    with _account_lock:
        if time.monotonic() - _account_status[0] < 15:
            return _account_status[1]
        binary = _claude_binary()
        ready = False
        if binary:
            try:
                result = subprocess.run([binary, "auth", "status"], capture_output=True,
                                        timeout=6, env=_account_environment(), cwd="/tmp")
                data = json.loads(result.stdout)
                ready = result.returncode == 0 and data.get("loggedIn") is True and data.get("authMethod") == "claude.ai"
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        _account_status = (time.monotonic(), ready)
        return ready


def _record_account_usage(stdout):
    """Keep only the rate-limit figures Claude Code reports, never account IDs."""
    global _account_usage
    latest = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "rate_limit_event":
            continue
        info = event.get("rate_limit_info") or {}
        windows = info.get("unifiedWindows") or {}
        usage = {}
        for key in ("five_hour", "seven_day"):
            window = windows.get(key) or {}
            value, reset = window.get("utilization"), window.get("resetsAt")
            if type(value) in (int, float) and 0 <= value <= 1:
                usage[key] = {"used_percent": round(value * 100),
                              "resets_at": reset if type(reset) is int else None}
        if usage:
            latest = {"status": info.get("status") if info.get("status") in ("allowed", "rejected") else "unknown",
                      "windows": usage, "checked_at": int(time.time())}
    if latest:
        with _account_lock:
            _account_usage = latest
    return latest


def _search_links(events):
    """Read links from actual WebSearch tool results, not generated prose."""
    ids, sources = set(), []
    for event in events:
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "WebSearch":
                ids.add(block.get("id"))
            if block.get("type") != "tool_result" or block.get("tool_use_id") not in ids:
                continue
            content = block.get("content", "")
            if isinstance(content, list):
                content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
            if not isinstance(content, str):
                continue
            # Claude Code's WebSearch result has a Links: [...] JSON array.
            marker = content.find("Links:")
            if marker < 0:
                continue
            start = content.find("[", marker)
            if start < 0:
                continue
            try:
                links, _ = json.JSONDecoder().raw_decode(content[start:])
            except ValueError:
                continue
            if isinstance(links, list):
                sources.extend({"type": "web_search_result", "url": link.get("url"),
                                "title": link.get("title")} for link in links if isinstance(link, dict))
    return sources


def _claude_post(cfg, body, timeout, cancel=None):
    binary = _claude_binary()
    if not binary:
        raise AIError("Install Claude Code and sign in with your Claude account on this server first.")
    tool = body["tools"][0]
    research = tool.get("name") == "web_search"
    command = [binary, "-p", "--model", cfg["model"], "--effort", "low", "--output-format", "stream-json", "--verbose",
               "--permission-prompts", "none",
               "--safe-mode", "--no-session-persistence", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--setting-sources", "", "--tools", "WebSearch,WebFetch" if research else "",
               "--system-prompt", _prompt(cfg.get("state_dir"), "system")]
    if research:
        command.extend(["--allowedTools", "WebSearch,WebFetch"])
    else:
        command.extend(["--json-schema", json.dumps(tool["input_schema"])])
    prompt = body["messages"][0]["content"]
    try:
        with tempfile.TemporaryDirectory(prefix="omdrc-ai-") as work:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, cwd=work,
                                       env=_account_environment(), start_new_session=True)
            deadline = time.monotonic() + timeout
            pending = prompt
            while True:
                if cancel is not None and cancel.is_set():
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    raise AIError("Research stopped.")
                try:
                    wait = timeout if cancel is None else min(1, max(.01, deadline - time.monotonic()))
                    stdout, _ = process.communicate(pending, timeout=wait)
                    break
                except subprocess.TimeoutExpired as expired:
                    pending = None
                    if time.monotonic() >= deadline:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate()
                        error = AIError(f"Claude account research timed out after {round(timeout)} seconds.")
                        partial = expired.output or b""
                        if isinstance(partial, bytes):
                            partial = partial.decode("utf-8", errors="replace")
                        error.account_usage = _record_account_usage(partial)
                        error.reply = _claude_activity(partial)
                        raise error from None
        if len(stdout.encode()) > MAX_BYTES:
            raise AIError("The Claude account response was too large.")
        events = [json.loads(line) for line in stdout.splitlines() if line.strip().startswith("{")]
        usage_status = _record_account_usage(stdout)
        result = next((event for event in reversed(events) if event.get("type") == "result"), {})
        if process.returncode != 0 or result.get("is_error") or not result:
            error = AIError("Claude account request failed. Check Claude login, permissions and account usage limits on this server.")
            error.reply = (str(result.get("result") or "") + "\n" + _claude_activity(stdout)).strip()[:16000]
            error.account_usage = usage_status
            raise error
        if research:
            return {"content": [{"type": "text", "text": result.get("result", "")},
                                {"type": "web_search_tool_result", "content": _search_links(events)}],
                    "activity": _claude_activity(stdout), "usage": result.get("usage"),
                    "account_usage": usage_status}
        value = result.get("structured_output")
        if not isinstance(value, dict):
            value = _json(result.get("result", ""))
        return {"content": [{"type": "tool_use", "name": tool["name"], "input": value}],
                "activity": _claude_activity(stdout), "usage": result.get("usage"),
                "account_usage": usage_status}
    except (OSError, ValueError):
        raise AIError("Could not run Claude with the server's account login.") from None


def _claude_activity(stdout):
    """Show assistant text and tool requests, without dumping internal CLI events."""
    lines = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                lines.append(block["text"])
            elif block.get("type") == "tool_use":
                lines.append(f"{block.get('name', 'Tool')}: " + json.dumps(block.get("input", {}), ensure_ascii=False))
    return "\n".join(lines)[:16000]


def _load(state_dir):
    try:
        data = json.loads((Path(state_dir) / "qobuz-ai.json").read_text())
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise AIError("Could not read AI settings; save them again in AI settings.") from None


def configuration(state_dir):
    data = _load(state_dir)
    provider = data.get("provider", "claude_account")
    if not isinstance(provider, str) or provider not in DEFAULT_MODELS:
        raise AIError("Unknown AI provider; save AI settings again.")
    env = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    saved = data.get(provider) or {}
    return {"provider": provider, "model": saved.get("model") or DEFAULT_MODELS[provider],
            "key": saved.get("key") or os.environ.get(env, ""), "state_dir": state_dir}


def public_settings(state_dir):
    cfg = configuration(state_dir)
    ready = account_ready()
    with _account_lock:
        usage = _account_usage
    return {"provider": cfg["provider"], "model": cfg["model"],
            "configured": ready if cfg["provider"] == "claude_account" else bool(cfg["key"]),
            "account_ready": ready, "account_usage": usage, "defaults": DEFAULT_MODELS}


def save_settings(state_dir, body):
    provider = body.get("provider", "")
    model = body.get("model", "")
    key = body.get("key", "")
    if not isinstance(provider, str) or provider not in DEFAULT_MODELS:
        raise AIError("Choose a supported AI provider.")
    if not isinstance(model, str) or len(model) > 100 or any(c.isspace() for c in model):
        raise AIError("Enter a valid model name.")
    if not isinstance(key, str) or len(key) > 512 or any(c.isspace() for c in key):
        raise AIError("Enter a valid API key.")
    with _settings_lock:
        data = _load(state_dir)
        saved = data.get(provider) or {}
        data["provider"] = provider
        data[provider] = {"model": model or DEFAULT_MODELS[provider],
                          "key": key or saved.get("key", "")}
        root = Path(state_dir)
        root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".qobuz-ai-", dir=root)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, root / "qobuz-ai.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return public_settings(state_dir)


def _post(cfg, body, timeout):
    # The same system brief as account mode: Anthropic calls it system, OpenAI instructions.
    body = {**body, "instructions" if cfg["provider"] == "openai" else "system":
            _prompt(cfg.get("state_dir"), "system")}
    headers = {"Content-Type": "application/json"}
    if cfg["provider"] == "openai":
        headers["Authorization"] = "Bearer " + cfg["key"]
    else:
        headers.update({"x-api-key": cfg["key"], "anthropic-version": "2023-06-01"})
    req = urllib.request.Request(ENDPOINTS[cfg["provider"]],
                                 data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise AIError("The AI response was too large.")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except urllib.error.HTTPError as error:
        # Do not surface provider bodies: they can contain account data or keys.
        hint = {401: "check the API key", 403: "check API permissions and web-search access",
                429: "check API credit or retry later"}.get(error.code, "check the model and retry")
        raise AIError(f"{cfg['provider']} returned HTTP {error.code}: {hint}.") from None
    except (OSError, ValueError):
        raise AIError("Could not reach the AI provider or read its response. Try again.") from None


def _sources(data):
    """Accept URLs only from actual provider citation/search-result objects."""
    found = {}

    def walk(obj):
        if isinstance(obj, dict):
            if obj.get("type") in ("url_citation", "web_search_result", "web_search_result_location"):
                url = obj.get("url", "")
                try:
                    valid = isinstance(url, str) and urlsplit(url).scheme == "https" and bool(urlsplit(url).netloc)
                except ValueError:
                    valid = False
                if valid:
                    found[url] = {"url": url, "title": str(obj.get("title") or obj.get("cited_text") or url)[:200]}
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(data)
    return list(found.values())[:30]


def _text(data, provider):
    if provider != "openai":
        blocks = data.get("content", [])
    else:
        blocks = [block for item in data.get("output", []) if item.get("type") == "message"
                  for block in item.get("content", [])]
    return "".join(b.get("text", "") for b in blocks if b.get("type") in ("text", "output_text"))


def _json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except ValueError:
        raise AIError("The AI returned an invalid recommendation. Try again.") from None


LISTENING_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "form": {"type": "string", "enum": ["concept_album", "song_collection", "multi_work", "single_work"]},
        "overview": {"type": "string"},
        "compositions": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"title": {"type": "string"}, "text": {"type": "string"},
                           "tracks": {"type": "array", "items": {"type": "integer"}}},
            "required": ["title", "text", "tracks"]}},
        "track_notes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"track": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["track", "text"]}},
        "composers": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"name": {"type": "string"}, "text": {"type": "string"}},
            "required": ["name", "text"]}},
        "performers": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": {"name": {"type": "string"}, "text": {"type": "string"}},
            "required": ["name", "text"]}}},
    "required": ["form", "overview", "compositions", "track_notes", "composers", "performers"]}


def listening_research(state_dir, album, tracks, post=None, cancel=None):
    """Research one release, returning sections indexed to its supplied track list."""
    if not isinstance(album, dict) or not isinstance(tracks, list) or not 1 <= len(tracks) <= 120:
        raise AIError("Album and track list are required.")
    def field(value):
        if isinstance(value, str):
            return value.strip()[:240]
        return str(value)[:240] if type(value) in (int, float) else ""
    album = {key: field(album.get(key)) for key in ("title", "artist", "composer", "label", "year", "genre", "release_type")}
    tracks = [{key: field(t.get(key)) for key in ("title", "work", "composer")}
              for t in tracks if isinstance(t, dict)]
    if not album["title"] or not tracks or not any(t["title"] for t in tracks):
        raise AIError("An album title and its tracks are required.")
    cfg = configuration(state_dir)
    if cfg["provider"] == "claude_account" and not account_ready():
        raise AIError("Sign in to Claude Code on this server as the web service user first.")
    if cfg["provider"] != "claude_account" and not cfg["key"]:
        raise AIError("Add your API key in AI settings first.")
    base_transport = post or (_claude_post if cfg["provider"] == "claude_account" else _post)
    def transport(config, body, timeout):
        if cancel is not None and cancel.is_set():
            raise AIError("Research stopped.")
        if post is None and cfg["provider"] == "claude_account":
            result = base_transport(config, body, timeout, cancel=cancel)
        else:
            result = base_transport(config, body, timeout)
        if cancel is not None and cancel.is_set():
            raise AIError("Research stopped.")
        return result
    metadata = json.dumps({"album": album, "tracks": [dict(number=i+1, **t) for i, t in enumerate(tracks)]}, ensure_ascii=False)
    prompt = _prompt(state_dir, "listening-research", metadata=metadata[:18000])
    if not _research_lock.acquire(blocking=False):
        raise AIError("An AI research request is already running. Please wait.")
    try:
        if cancel is not None and cancel.is_set():
            raise AIError("Research stopped.")
        research_status = ""
        try:
            if cfg["provider"] == "openai":
                raw = transport(cfg, {"model": cfg["model"], "store": False, "tools": [{"type": "web_search"}],
                                      "max_tool_calls": 5, "max_output_tokens": 8000, "input": prompt}, 240)
            else:
                raw = transport(cfg, {"model": cfg["model"], "max_tokens": 8000,
                                      "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
                                      "messages": [{"role": "user", "content": prompt}]}, 240)
            research = _text(raw, cfg["provider"])
            if not research:
                raise AIError("The AI returned no research. Try again.")
        except AIError as error:
            if "timed out" not in str(error).lower():
                raise
            raw = {}
            research = ("Web search timed out. Use established knowledge and the supplied album metadata. "
                        "Do not claim any review, award, recording detail or source that is not verified.")
            research_status = "Web search timed out; this guide uses model knowledge and album metadata."
        structure = _prompt(state_dir, "listening-guide", metadata=metadata[:18000],
                            research=research[:24000])
        if cfg["provider"] == "openai":
            result = transport(cfg, {"model": cfg["model"], "store": False, "max_output_tokens": 10000,
                                     "input": structure, "tools": [{"type": "function", "name": "listening_guide",
                                     "strict": True, "parameters": LISTENING_SCHEMA}],
                                     "tool_choice": {"type": "function", "name": "listening_guide"}}, 240)
            calls = [x for x in result.get("output", []) if x.get("type") == "function_call" and x.get("name") == "listening_guide"]
            guide = _json(calls[0].get("arguments", "")) if calls else {}
        else:
            result = transport(cfg, {"model": cfg["model"], "max_tokens": 10000,
                                     "messages": [{"role": "user", "content": structure}],
                                     "tools": [{"name": "listening_guide", "input_schema": LISTENING_SCHEMA}],
                                     "tool_choice": {"type": "tool", "name": "listening_guide"}}, 240)
            calls = [x for x in result.get("content", []) if x.get("type") == "tool_use" and x.get("name") == "listening_guide"]
            guide = calls[0].get("input", {}) if calls else {}
    finally:
        _research_lock.release()
    if not isinstance(guide, dict) or not isinstance(guide.get("overview"), str):
        raise AIError("The AI returned an invalid listening guide. Try again.")
    def valid_numbers(values):
        return [x for x in values if type(x) is int and 1 <= x <= len(tracks)] if isinstance(values, list) else []
    sections = [{"title": field(s.get("title")), "text": str(s.get("text", ""))[:6000],
                 "tracks": valid_numbers(s.get("tracks"))}
                for s in (guide.get("compositions") if isinstance(guide.get("compositions"), list) else [])[:30] if isinstance(s, dict)]
    notes = [{"track": n["track"], "text": n["text"][:3000]}
             for n in (guide.get("track_notes") if isinstance(guide.get("track_notes"), list) else [])[:120] if isinstance(n, dict)
             and type(n.get("track")) is int and 1 <= n["track"] <= len(tracks)
             and isinstance(n.get("text"), str)]
    note_by_track = {n["track"]: n["text"] for n in notes}
    def people(key):
        return [{"name": field(p.get("name")), "text": p["text"][:8000]}
                for p in (guide.get(key) if isinstance(guide.get(key), list) else [])[:12]
                if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip() and field(p.get("name"))]
    form = guide.get("form")
    works = {}
    for number, track in enumerate(tracks, 1):
        if track["work"]:
            works.setdefault(track["work"], []).append(number)
    if len(works) > 1 and sum(map(len, works.values())) == len(tracks):
        # Qobuz's work field is a stronger boundary than a model's guessed grouping.
        def work_section(work, numbers):
            candidates = [s for s in sections if set(s["tracks"]) & set(numbers)]
            match = max(candidates, key=lambda s: len(set(s["tracks"]) & set(numbers)),
                        default={"text": ""})
            return {"title": work, "tracks": numbers, "text": match["text"]}
        sections = [work_section(work, numbers) for work, numbers in works.items()]
        form = "multi_work"
    elif len(works) == 1 and sum(map(len, works.values())) == len(tracks):
        sections = [{"title": next(iter(works)), "tracks": list(range(1, len(tracks) + 1)),
                     "text": sections[0]["text"] if sections else guide["overview"][:6000]}]
        form = "single_work"
    elif form == "concept_album":
        sections = [{"title": album["title"], "tracks": list(range(1, len(tracks) + 1)),
                     "text": sections[0]["text"] if sections else guide["overview"][:6000]}]
    elif form == "song_collection" and len(tracks) > 1:
        # Each song is independently selectable on jazz records and live anthologies.
        sections = [{"title": track["title"], "tracks": [number],
                     "text": note_by_track.get(number) or next(
                         (s["text"] for s in sections if number in s["tracks"]), "")}
                    for number, track in enumerate(tracks, 1)]
    else:
        assigned = {n for s in sections for n in s["tracks"]}
        sections.extend({"title": track["work"] or track["title"], "tracks": [number],
                         "text": note_by_track.get(number, "")}
                        for number, track in enumerate(tracks, 1) if number not in assigned)
    return {"form": form or "song_collection", "overview": guide["overview"][:10000], "compositions": sections,
            "track_notes": notes, "composers": people("composers"),
            "performers": people("performers"), "sources": _sources(raw), "research_status": research_status,
            "provider": cfg["provider"]}


SELECTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "picks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "reason": {"type": "string"},
                           "source_indices": {"type": "array", "items": {"type": "integer"}}},
            "required": ["id", "reason", "source_indices"]}}},
    "required": ["summary", "picks"]}


def recommend(state_dir, catalog, prompt, filters, count=None, post=None):
    recommendation_prompt(state_dir, prompt, count)
    cfg = configuration(state_dir)
    if cfg["provider"] == "claude_account" and not account_ready():
        if not _claude_binary():
            raise AIError("Claude Code was not found for the web service user. Put claude on the service PATH "
                          "or set OMDRC_CLAUDE_BIN to its absolute executable path.")
        raise AIError("Sign in to Claude Code on this server as the web service user first.")
    if cfg["provider"] != "claude_account" and not cfg["key"]:
        raise AIError("Add your API key in AI settings first.")
    if not _research_lock.acquire(blocking=False):
        raise AIError("An AI recommendation is already running. Please wait.")
    try:
        transport = _claude_post if cfg["provider"] == "claude_account" else _post
        trace = []
        try:
            return _recommend(cfg, catalog, prompt.strip(), filters, count, post or transport, trace)
        except QobuzError as error:
            error.trace = trace
            raise
    finally:
        _research_lock.release()


def _recommend(cfg, catalog, prompt, filters, count, post, trace):
    deadline = time.monotonic() + 360

    def call(body, stage, source=None):
        prompt_text = body.get("input") or body["messages"][0]["content"]
        item = {"stage": stage, "prompt": prompt_text, "reply": ""}
        item["system_prompt"] = _prompt(cfg["state_dir"], "system")
        item["system_prompt_source"] = str(prompt_path(cfg["state_dir"], "system"))
        if source:
            item["prompt_source"] = str(prompt_path(cfg["state_dir"], source))
        trace.append(item)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AIError("AI research timed out. Try a more specific request.")
        stage_limit = 240 if cfg["provider"] == "claude_account" else 90
        try:
            answer = post(cfg, body, min(stage_limit, remaining))
        except AIError as error:
            item["reply"] = getattr(error, "reply", "")
            if getattr(error, "account_usage", None):
                item["account_usage"] = error.account_usage
            raise
        item["reply"] = _text(answer, cfg["provider"])
        if not item["reply"]:
            if cfg["provider"] == "openai":
                calls = [b.get("arguments", "") for b in answer.get("output", []) if b.get("type") == "function_call"]
            else:
                calls = [json.dumps(b.get("input", {}), ensure_ascii=False) for b in answer.get("content", []) if b.get("type") == "tool_use"]
            item["reply"] = "\n".join(calls)
        if answer.get("activity"):
            item["activity"] = answer["activity"]
        if answer.get("account_usage"):
            item["account_usage"] = answer["account_usage"]
        usage = answer.get("usage")
        if isinstance(usage, dict):
            item["usage"] = {key: usage[key] for key in ("input_tokens", "output_tokens")
                             if type(usage.get(key)) is int}
        item["reply"] = item["reply"][:16000]
        return answer

    def structured(prompt_text, name, schema, stage, source):
        if cfg["provider"] == "openai":
            answer = call({"model": cfg["model"], "store": False, "max_output_tokens": 4000,
                           "input": prompt_text,
                           "tools": [{"type": "function", "name": name, "strict": True,
                                      "description": "Return the requested structured result.", "parameters": schema}],
                           "tool_choice": {"type": "function", "name": name}}, stage, source)
            blocks = [b for b in answer.get("output", []) if b.get("type") == "function_call" and b.get("name") == name]
            return _json(blocks[0].get("arguments", "")) if blocks else {}
        answer = call({"model": cfg["model"], "max_tokens": 4000,
                       "messages": [{"role": "user", "content": prompt_text}],
                       "tools": [{"name": name, "description": "Return the requested structured result.", "input_schema": schema}],
                       "tool_choice": {"type": "tool", "name": name}}, stage, source)
        blocks = [b for b in answer.get("content", []) if b.get("type") == "tool_use" and b.get("name") == name]
        return blocks[0].get("input", {}) if blocks else {}

    quantity = (f"Select up to {count} recordings." if count is not None else
                "Use the number of recordings requested by the user; if unspecified, choose a suitable number. "
                "Return at most 20 recordings.")
    research_prompt = recommendation_prompt(cfg["state_dir"], prompt, count)
    provider = cfg["provider"]
    if provider == "openai":
        research = call({"model": cfg["model"], "store": False,
                         "tools": [{"type": "web_search"}], "max_tool_calls": 4,
                         "max_output_tokens": 4000, "input": research_prompt}, "Web research", "recommend-research")
    else:
        research = call({"model": cfg["model"], "max_tokens": 4000,
                         "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
                         "messages": [{"role": "user", "content": research_prompt}]}, "Web research", "recommend-research")
    research_text = _text(research, provider)
    if not research_text:
        raise AIError("The AI returned no research. Check web-search access and try again.")
    plan = structured(_prompt(cfg["state_dir"], "recommend-queries", research=research_text[:16000]),
                      "plan_searches",
                      {"type": "object", "additionalProperties": False,
                       "properties": {"search_queries": {"type": "array", "items": {"type": "string"}}},
                       "required": ["search_queries"]}, "Qobuz search plan", "recommend-queries")
    if not isinstance(plan, dict):
        raise AIError("The AI returned an invalid search plan. Try again.")
    queries = plan.get("search_queries")
    if not isinstance(queries, list) or not queries:
        raise AIError("AI research completed, but found no supported recording titles to search in Qobuz. Review the research reply below.")
    sources = _sources(research)
    candidates, searches = {}, []
    for query in queries[:4]:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            continue
        if time.monotonic() >= deadline:
            raise AIError("AI research timed out. Try a more specific request.")
        answer = catalog.search(text=query.strip(), **filters)
        searches.extend(answer["queries"])
        for card in answer["results"][:30]:
            if card.get("streamable") is not False and not card.get("lowered"):
                candidates[card["id"]] = card
    if not candidates:
        raise AIError("AI research completed, but no playable Qobuz titles matched its suggestions and your filters. Review the AI reply and search plan below.")
    # These are the only album IDs the selector may return. No generated ID is trusted.
    fields = ("id", "title", "version", "artist", "composer", "label", "date", "bits", "rate", "performers")
    data = json.dumps({"request": prompt, "research": research_text[:14000], "sources": sources,
                       "candidates": [{k: card.get(k) for k in fields} for card in candidates.values()]},
                      ensure_ascii=False)
    selection_prompt = _prompt(cfg["state_dir"], "recommend-select", quantity=quantity, data=data)
    picks = structured(selection_prompt, "select_albums", SELECTION_SCHEMA, "Album selection", "recommend-select")
    if not isinstance(picks, dict) or not isinstance(picks.get("picks"), list):
        raise AIError("The AI did not return a valid album selection. Try again.")
    results, seen = [], set()
    for pick in picks["picks"][:count if count is not None else 20]:
        if not isinstance(pick, dict):
            continue
        album_id = pick.get("id")
        if not isinstance(album_id, str) or album_id not in candidates or album_id in seen:
            continue
        seen.add(album_id)
        try:
            card = catalog.album(album_id)
        except QobuzError:
            continue
        if card.get("streamable") is False or (filters.get("exclude_cd") and is_cd_quality(card)):
            continue
        refs = pick.get("source_indices", [])
        linked = [sources[i] for i in refs[:5] if type(i) is int and 0 <= i < len(sources)] if isinstance(refs, list) else []
        card["ai"] = {"rank": len(results), "reason": str(pick.get("reason", ""))[:1800], "sources": linked,
                      "uncertain": not bool(linked)}
        results.append(card)
    if not results:
        raise AIError("No recommended album could be verified as playable in Qobuz.")
    return {"query": prompt, "labels": [], "window": {"from": "", "to": ""},
            "sort": "ai", "results": results, "count": len(results), "considered": len(candidates),
            "queries": searches, "labels_seen": [], "more": False, "unstreamable": 0,
            "enriched": len(results), "lowered": 0, "scan": 0,
            "ai": {"provider": provider, "summary": str(picks.get("summary", ""))[:2000],
                   "requested": count, "trace": trace}, "exclude_cd": bool(filters.get("exclude_cd"))}
