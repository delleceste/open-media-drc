"""Provider contract, secret storage, catalog validation and Hi-Res filtering."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omdrc-ctrl/src"))
from flask import Flask
import qobuz_ai as ai
import qobuz_search as qs
import qobuz_web as web


def raw(album_id, bits=24, rate=96, streamable=True):
    return {"id": album_id, "title": "Beethoven: Symphony No. 5", "artist": {"name": "Orchestra"},
            "label": {"name": "BIS"}, "maximum_bit_depth": bits, "maximum_sampling_rate": rate,
            "release_date_original": "2025-01-01", "streamable": streamable, "tracks": {"items": []}}


def catalog(items, details=None, **options):
    calls = []

    def fetch(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "catalog/search":
            offset = params["offset"]
            return {"albums": {"items": items[offset:offset + params["limit"]], "total": len(items)}}
        return (details or {i["id"]: i for i in items})[params["album_id"]]
    return qs.QobuzCatalog(qs.Settings(max_enrich=0, **options), fetch=fetch), calls


class HiResTest(unittest.TestCase):
    def test_only_exact_cd_quality_is_excluded_in_final_and_partial_results(self):
        items = [raw("cd", 16, 44.1), raw("hz", "16", "44100"), raw("dvd", 16, 48),
                 raw("hi", 24, 44.1), raw("unknown", None, None)]
        cat, _ = catalog(items)
        partials = []
        answer = cat.search("Beethoven", exclude_cd=True, progress=partials.append)
        self.assertEqual([a["id"] for a in answer["results"]], ["dvd", "hi", "unknown"])
        self.assertTrue(partials)
        self.assertEqual([a["id"] for a in partials[0]["results"]], ["dvd", "hi", "unknown"])
        self.assertEqual(cat.search("Beethoven")["count"], 5)

    def test_filter_reads_deeper_when_first_page_is_all_cd(self):
        items = [raw(str(i), 16, 44.1) for i in range(50)] + [raw("good")]
        cat, calls = catalog(items, scan=50, want=1, auto_scan=100)
        self.assertEqual([a["id"] for a in cat.search("Beethoven", exclude_cd=True)["results"]], ["good"])
        self.assertTrue(any(p.get("offset") == 50 for _, p in calls))


class AITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.source = {"type": "web_search_result", "url": "https://reviews.example/beethoven", "title": "Recording review"}
        self.picks = [{"id": "good", "reason": "Excellent engineering; mastering unconfirmed.", "source_indices": [0]}]
        self.sent = []

    def configure(self, provider="claude"):
        return ai.save_settings(self.root, {"provider": provider, "key": "test-secret"})

    def post(self, cfg, body, timeout):
        self.sent.append(body)
        self.assertGreater(timeout, 0)
        if len(self.sent) == 1:
            if cfg["provider"] == "claude":
                return {"content": [{"type": "text", "text": "Review supports Orchestra's recording."},
                                    {"type": "web_search_tool_result", "content": [self.source]}]}
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Review evidence.",
                    "annotations": [{**self.source, "type": "url_citation"}]}]}]}
        name = "plan_searches" if len(self.sent) == 2 else "select_albums"
        answer = {"search_queries": ["Beethoven 5 Orchestra"]} if len(self.sent) == 2 else {"summary": "Three recommendations requested.", "picks": self.picks}
        if cfg["provider"] == "claude":
            return {"content": [{"type": "tool_use", "name": name, "input": answer}]}
        return {"output": [{"type": "function_call", "name": name, "arguments": json.dumps(answer)}]}

    def run_recommend(self, items=None, details=None, **filters):
        cat, calls = catalog(items or [raw("good")], details)
        return ai.recommend(self.root, cat, "Three Beethoven 5 recordings for sound engineering", filters, post=self.post), calls

    def test_claude_and_openai_return_real_playable_cards_with_sources(self):
        for provider in ("claude", "openai"):
            with self.subTest(provider=provider):
                self.sent = []
                self.configure(provider)
                answer, calls = self.run_recommend(exclude_cd=True)
                self.assertEqual(answer["results"][0]["id"], "good")
                self.assertEqual(answer["results"][0]["ai"]["sources"][0]["url"], self.source["url"])
                self.assertFalse(answer["more"])
                self.assertEqual(answer["ai"]["provider"], provider)
                self.assertTrue(any(e == "album/get" for e, _ in calls))
                self.assertEqual(len(self.sent), 3)
                self.assertNotIn("test-secret", json.dumps(self.sent))

    def test_settings_keep_each_providers_key_private_and_owner_only(self):
        cfg = self.configure()
        self.assertTrue(cfg["configured"])
        self.assertNotIn("key", cfg)
        self.assertEqual((Path(self.root) / "qobuz-ai.json").stat().st_mode & 0o777, 0o600)
        self.configure("openai")
        ai.save_settings(self.root, {"provider": "claude", "model": "claude-sonnet-4-6", "key": ""})
        self.assertEqual(ai.configuration(self.root)["key"], "test-secret")
        self.assertNotIn("test-secret", json.dumps(ai.public_settings(self.root)))

    def test_listening_guide_maps_compositions_and_track_notes(self):
        self.configure("claude")
        calls = []
        def post(cfg, body, timeout):
            calls.append(body)
            if len(calls) == 1:
                return {"content": [{"type": "text", "text": "The third and fourth symphonies have distinct histories."},
                                    {"type": "web_search_tool_result", "content": [self.source]}]}
            return {"content": [{"type": "tool_use", "name": "listening_guide", "input": {
                "overview": "Two symphonies on one release.",
                "compositions": [{"title": "Symphony No. 3", "text": "Historical context", "tracks": [1, 2]},
                                 {"title": "Symphony No. 4", "text": "Different work", "tracks": [3, 99]}],
                "track_notes": [{"track": 2, "text": "Second movement"}, {"track": 99, "text": "Invalid"}],
                "composers": [{"name": "Dmitri Shostakovich", "text": "Born in St Petersburg."},
                              {"name": "", "text": "Nameless"}],
                "performers": [{"name": "Orchestra", "text": "Founded in 1930."}]}}]}
        answer = ai.listening_research(self.root, {"title": "Shostakovich Symphonies", "artist": "Orchestra"},
                                       [{"title": "Symphony 3: I"}, {"title": "Symphony 3: II"},
                                        {"title": "Symphony 4: I"}], post=post)
        self.assertEqual(answer["compositions"][1]["tracks"], [3])
        self.assertEqual(answer["track_notes"], [{"track": 2, "text": "Second movement"}])
        self.assertEqual(answer["composers"], [{"name": "Dmitri Shostakovich", "text": "Born in St Petersburg."}])
        self.assertEqual(answer["performers"][0]["name"], "Orchestra")
        self.assertIn("biography", calls[0]["messages"][0]["content"])
        self.assertIn("composers", calls[1]["tools"][0]["input_schema"]["required"])
        self.assertEqual(answer["sources"][0]["url"], self.source["url"])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("test-secret", json.dumps(calls))

    def test_custom_prompt_files_replace_defaults_and_keep_placeholders(self):
        self.configure("claude")
        calls = []
        def post(cfg, body, timeout):
            calls.append(body)
            if len(calls) == 1:
                return {"content": [{"type": "text", "text": "Research notes."}]}
            return {"content": [{"type": "tool_use", "name": "listening_guide", "input": {
                "form": "single_work", "overview": "Context", "compositions": [], "track_notes": []}}]}
        custom = Path(self.root) / "prompts"
        custom.mkdir()
        (custom / "listening-research.txt").write_text("# Mine: ${metadata}\n\nRispondi in italiano, $5 budget.\n${metadata}\n")
        ai.listening_research(self.root, {"title": "Requiem"}, [{"title": "Introitus"}], post=post)
        research = calls[0]["messages"][0]["content"]
        self.assertTrue(research.startswith("Rispondi in italiano, $5 budget.\n{"))
        self.assertIn('"Requiem"', research)
        self.assertIn("Research:\nResearch notes.", calls[1]["messages"][0]["content"])
        (custom / "listening-research.txt").write_text("# Needs ${metadata}\nResearch this album.\n")
        calls.clear()
        with self.assertRaisesRegex(ai.AIError, r"\$\{metadata\}"):
            ai.listening_research(self.root, {"title": "Requiem"}, [{"title": "Introitus"}], post=post)
        self.assertEqual(calls, [])

    def test_default_prompts_declare_their_placeholders(self):
        for name, keys in ai.PROMPTS.items():
            with self.subTest(prompt=name):
                text = ai._prompt("", name, **{k: "<" + k + ">" for k in keys})
                self.assertNotIn("${", text)
                self.assertNotIn("Placeholders", text)
                self.assertIn("# Used:", (ai.PROMPT_DIR / f"{name}.txt").read_text())
                self.assertEqual(ai.prompt_path(self.root, name), ai.PROMPT_DIR / f"{name}.txt")

    def test_cancelled_listening_guide_does_not_contact_provider(self):
        self.configure("claude")
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(ai.AIError, "stopped"):
            ai.listening_research(self.root, {"title": "The Wall"}, [{"title": "In the Flesh?"}],
                                  post=lambda *_: self.fail("provider was called"), cancel=cancelled)

    def test_real_album_shapes_keep_the_right_work_boundaries(self):
        """Qobuz metadata snapshots: jazz, concept album, live anthology, classical works."""
        self.configure("claude")
        cases = json.loads((Path(__file__).with_name("listening_album_cases.json")).read_text())
        expected = {"sonny": [1] * 5, "wall": [26], "thunder": [1] * 15,
                    "richter": [5, 3, 1]}
        for name, case in cases.items():
            with self.subTest(album=name):
                sent = []
                form = {"sonny": "song_collection", "wall": "concept_album",
                        "thunder": "song_collection", "richter": "multi_work"}[name]
                count = len(case["tracks"])
                def post(_cfg, body, _timeout):
                    sent.append(body)
                    if len(sent) == 1:
                        return {"content": [{"type": "text", "text": "Album and individual work research."}]}
                    return {"content": [{"type": "tool_use", "name": "listening_guide", "input": {
                        "form": form, "overview": "Album context", "compositions": [
                            {"title": "Album", "text": "Context", "tracks": list(range(1, count + 1))}],
                        "track_notes": [{"track": n, "text": "Track context"} for n in range(1, count + 1)]}}]}
                answer = ai.listening_research(self.root, case["album"], case["tracks"], post=post)
                self.assertEqual([len(s["tracks"]) for s in answer["compositions"]], expected[name])
                self.assertEqual({n for s in answer["compositions"] for n in s["tracks"]}, set(range(1, count + 1)))
                self.assertEqual(len(answer["track_notes"]), count)
                self.assertEqual(len(sent), 2)

    def test_web_timeout_uses_labeled_unsourced_fallback(self):
        self.configure("claude")
        calls = []
        def post(_cfg, body, _timeout):
            calls.append(body)
            if len(calls) == 1:
                raise ai.AIError("Claude account research timed out. Try a more specific request.")
            return {"content": [{"type": "tool_use", "name": "listening_guide", "input": {
                "form": "single_work", "overview": "Context", "compositions": [], "track_notes": []}}]}
        answer = ai.listening_research(self.root, {"title": "A work"}, [{"title": "Movement I"}], post=post)
        self.assertEqual(len(calls), 2)
        self.assertEqual(answer["sources"], [])
        self.assertIn("timed out", answer["research_status"])

    def test_missing_key_and_invalid_inputs_do_not_call_provider(self):
        ai.save_settings(self.root, {"provider": "claude"})
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ai.AIError, "API key"):
                self.run_recommend()
        self.assertEqual(self.sent, [])
        self.configure()
        cat, _ = catalog([raw("good")])
        for prompt, count in (("", 3), ("x" * 2001, 3), ("Music", True), ("Music", 7)):
            with self.assertRaises(ai.AIError):
                ai.recommend(self.root, cat, prompt, {}, count, post=self.post)
        with self.assertRaises(ai.AIError):
            ai.save_settings(self.root, {"provider": []})

    def test_invented_ids_duplicates_and_unverified_sources_are_discarded(self):
        self.configure()
        self.picks = [{"id": "invented", "reason": "Fake", "source_indices": [0]},
                      {"id": "good", "reason": "Uncertain", "source_indices": [-1, 999, "0", True]},
                      {"id": "good", "reason": "Duplicate", "source_indices": [0]}]
        answer, _ = self.run_recommend()
        self.assertEqual(answer["count"], 1)
        self.assertTrue(answer["results"][0]["ai"]["uncertain"])
        self.assertEqual(answer["results"][0]["ai"]["sources"], [])

    def test_changed_availability_and_changed_format_cannot_be_selected(self):
        self.configure()
        for changed in (raw("good", streamable=False), raw("good", 16, 44.1)):
            self.sent = []
            with self.assertRaisesRegex(ai.AIError, "verified"):
                self.run_recommend(details={"good": changed}, exclude_cd=True)

    def test_search_sources_reject_unsafe_links_and_generated_urls(self):
        data = {"content": [{"url": "https://invented.example"},
                            {"type": "web_search_result", "url": "javascript:alert(1)"},
                            {"type": "web_search_result", "url": "https://["}, self.source]}
        self.assertEqual(ai._sources(data), [{"url": self.source["url"], "title": self.source["title"]}])

    def test_provider_http_errors_do_not_expose_keys_or_response_body(self):
        self.configure()
        with patch.object(ai.urllib.request, "urlopen", side_effect=urllib.error.HTTPError(
                "https://api.example", 401, "test-secret", {}, None)):
            with self.assertRaisesRegex(ai.AIError, "HTTP 401") as error:
                self.run_recommend_with_real_post()
        self.assertNotIn("test-secret", str(error.exception))

    def run_recommend_with_real_post(self):
        cat, _ = catalog([raw("good")])
        return ai.recommend(self.root, cat, "Music", {})

    def test_concurrent_paid_requests_are_rejected(self):
        self.configure()
        ai._research_lock.acquire()
        try:
            with self.assertRaisesRegex(ai.AIError, "already running"):
                self.run_recommend()
        finally:
            ai._research_lock.release()


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        app = Flask(__name__)
        app.register_blueprint(web.bp)
        self.client = app.test_client()
        for name, value in (("_state_dir", lambda: self.temp.name),
                            ("_settings", lambda: qs.Settings()), ("renderer_running", lambda: True)):
            patcher = patch.object(web, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_settings_never_return_secrets_and_require_same_origin_header(self):
        body = {"provider": "claude", "key": "test-secret"}
        self.assertEqual(self.client.post("/qobuz/ai/settings", json=body).status_code, 403)
        self.assertEqual(self.client.post("/qobuz/ai/settings", json=body,
                         headers={"X-Qobuz-AI": "1", "Origin": "https://other.example"}).status_code, 403)
        response = self.client.post("/qobuz/ai/settings", json=body, headers={"X-Qobuz-AI": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("test-secret", response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("test-secret", self.client.get("/qobuz/ai/settings").get_data(as_text=True))

    def test_recommendation_passes_filters_and_checks_renderer(self):
        with patch.object(web, "catalog", return_value=object()), patch.object(ai, "recommend", return_value={"results": []}) as recommend:
            response = self.client.post("/qobuz/ai/recommend?hires=1&label=BIS&from=2020&awarded=1",
                                        json={"prompt": "Three Beethoven 5 recordings", "count": 3}, headers={"X-Qobuz-AI": "1"})
            self.assertEqual(response.status_code, 200)
            args = recommend.call_args.args
            self.assertEqual(args[3]["exclude_cd"], True)
            self.assertEqual(args[3]["labels"], ["BIS"])
            self.assertEqual(args[3]["from_year"], 2020)
            self.assertEqual(args[3]["awarded_only"], True)
        with patch.object(web, "renderer_running", return_value=False):
            self.assertEqual(self.client.post("/qobuz/ai/recommend", json={"prompt": "Music"}, headers={"X-Qobuz-AI": "1"}).status_code, 409)

    def test_listening_route_requires_guard_and_passes_cancel_event(self):
        job = "a38cbe40-f3af-44af-a76f-59826db907b1"
        body = {"job": job, "album": {"title": "The Wall"}, "tracks": [{"title": "In the Flesh?"}]}
        self.assertEqual(self.client.post("/qobuz/ai/listening", json=body).status_code, 403)
        headers = {"X-Qobuz-AI": "1"}
        with patch.object(ai, "listening_research", return_value={"overview": "Album overview", "compositions": []}) as research:
            result = self.client.post("/qobuz/ai/listening", json=body, headers=headers)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.get_json()["overview"], "Album overview")
            self.assertEqual(result.headers["Cache-Control"], "no-store")
            self.assertFalse(research.call_args.kwargs["cancel"].is_set())
        self.assertEqual(self.client.post("/qobuz/ai/listening", json={**body, "job": []}, headers=headers).status_code, 400)
        self.assertEqual(self.client.post("/qobuz/ai/listening/cancel", json={"job": []}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/qobuz/ai/listening/cancel", json={"job": job}, headers=headers).status_code, 200)
        with patch.object(ai, "listening_research", return_value={"overview": "Cancelled"}) as research:
            self.client.post("/qobuz/ai/listening", json=body, headers=headers)
            self.assertTrue(research.call_args.kwargs["cancel"].is_set())


class ClaudeAccountTest(unittest.TestCase):
    def test_freebsd_package_is_found_with_a_minimal_service_path(self):
        with patch.dict("os.environ", {"OMDRC_CLAUDE_BIN": "", "PATH": "/usr/bin:/bin"}), patch.object(ai.shutil, "which", return_value=None), patch.object(ai.Path, "is_file", lambda p: str(p) == "/usr/local/bin/claude"), patch.object(ai.os, "access", return_value=True):
            self.assertEqual(ai._claude_binary(), "/usr/local/bin/claude")

    def test_per_user_install_uses_the_service_users_home(self):
        with tempfile.TemporaryDirectory() as home:
            binary = Path(home) / ".local/bin/claude"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
            with patch.dict("os.environ", {"HOME": home, "OMDRC_CLAUDE_BIN": ""}), patch.object(ai.shutil, "which", return_value=None):
                self.assertEqual(ai._claude_binary(), str(binary))

    def test_explicit_binary_override_is_used_and_invalid_overrides_do_not_fall_back(self):
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root) / "claude-wrapper"
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
            with patch.dict("os.environ", {"OMDRC_CLAUDE_BIN": str(binary)}), patch.object(ai.shutil, "which") as which:
                self.assertEqual(ai._claude_binary(), str(binary))
                which.assert_not_called()
            for invalid in ("relative/claude", str(Path(root) / "missing")):
                with patch.dict("os.environ", {"OMDRC_CLAUDE_BIN": invalid}), patch.object(ai.shutil, "which") as which:
                    self.assertIsNone(ai._claude_binary())
                    which.assert_not_called()

    def test_existing_profile_environment_is_preserved_without_reading_secrets(self):
        env = {"HOME": "/usr/home/listener", "CLAUDE_CONFIG_DIR": "/usr/home/listener/.claude", "OMDRC_CLAUDE_BIN": "/usr/local/bin/claude", "ANTHROPIC_API_KEY": "do-not-use"}
        with patch.dict("os.environ", env, clear=True):
            actual = ai._account_environment()
        self.assertEqual(actual["HOME"], env["HOME"])
        self.assertEqual(actual["CLAUDE_CONFIG_DIR"], env["CLAUDE_CONFIG_DIR"])
        self.assertNotIn("ANTHROPIC_API_KEY", actual)

    def test_account_readiness_requires_account_auth_not_an_api_key(self):
        for method, expected in (("claude.ai", True), ("api_key", False)):
            result = MagicMock(returncode=0, stdout=json.dumps({"loggedIn": True, "authMethod": method}))
            with patch.object(ai, "_account_status", (0, False)), patch.object(ai, "_claude_binary", return_value="/bin/claude"), patch.object(ai.subprocess, "run", return_value=result):
                self.assertEqual(ai.account_ready(), expected)

    def test_account_mode_uses_login_without_api_key_and_keeps_tools_restricted(self):
        events = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch", "id": "search1"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "search1",
                "content": 'Web search results. Links: [{"title":"Review", "url":"https://reviews.example/5"}]\nEvidence.'}]}},
            {"type": "result", "is_error": False, "result": "Engineering review."}]
        process = MagicMock(returncode=0)
        process.communicate.return_value = ("\n".join(json.dumps(e) for e in events), "")
        cfg = {"provider": "claude_account", "model": "claude-sonnet-4-6", "key": "unused"}
        with patch.object(ai, "_claude_binary", return_value="/bin/claude"), patch.object(ai.subprocess, "Popen", return_value=process) as popen, patch.dict("os.environ", {"ANTHROPIC_API_KEY": "must-not-use", "CLAUDECODE": "1"}):
            answer = ai._claude_post(cfg, {"tools": [{"name": "web_search"}], "messages": [{"content": "Beethoven research"}]}, 30)
            command = popen.call_args.args[0]
            self.assertIn("--safe-mode", command)
            self.assertIn("--no-session-persistence", command)
            self.assertIn("--strict-mcp-config", command)
            self.assertEqual(command[command.index("--tools") + 1], "WebSearch")
            self.assertNotIn("ANTHROPIC_API_KEY", popen.call_args.kwargs["env"])
            self.assertNotIn("CLAUDECODE", popen.call_args.kwargs["env"])
            self.assertEqual(ai._sources(answer)[0]["url"], "https://reviews.example/5")
            process.communicate.assert_called_once_with("Beethoven research", timeout=30)

    def test_structured_account_calls_disable_all_tools(self):
        process = MagicMock(returncode=0)
        process.communicate.return_value = (json.dumps({"type": "result", "structured_output": {"picks": []}, "is_error": False}), "")
        with patch.object(ai, "_claude_binary", return_value="/bin/claude"), patch.object(ai.subprocess, "Popen", return_value=process) as popen:
            answer = ai._claude_post({"model": "sonnet"}, {"tools": [{"name": "select_albums", "input_schema": ai.SELECTION_SCHEMA}], "messages": [{"content": "Select IDs"}]}, 30)
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("--tools") + 1], "")
            self.assertIn("--json-schema", command)
            self.assertEqual(answer["content"][0]["input"], {"picks": []})

    def test_generated_prose_cannot_add_fake_review_sources(self):
        events = [{"type": "assistant", "message": {"content": [{"type": "text", "text": 'Links: [{"url":"https://invented.example"}]'}]}},
                  {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "untracked", "content": 'Links: [{"url":"https://invented.example"}]'}]}}]
        self.assertEqual(ai._search_links(events), [])

    def test_logged_out_account_never_falls_back_to_api(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(ai, "account_ready", return_value=False), patch.object(ai, "_claude_binary", return_value="/usr/local/bin/claude"):
                ai.save_settings(root, {"provider": "claude_account"})
                with patch.object(ai, "_post") as api:
                    with self.assertRaisesRegex(ai.AIError, "Sign in"):
                        ai.recommend(root, object(), "Beethoven", {})
                    api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
