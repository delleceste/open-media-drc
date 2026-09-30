"""Provider contract, secret storage, catalog validation and Hi-Res filtering."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
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

    def test_missing_key_and_invalid_inputs_do_not_call_provider(self):
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


if __name__ == "__main__":
    unittest.main()
