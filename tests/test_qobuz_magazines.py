"""Magazine issues: recognising the month, reading the picks, cross-referencing them."""
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "omdrc-ctrl/src"))
from flask import Flask
import qobuz_ai as ai
import qobuz_magazines as mags
import qobuz_search as qs
import qobuz_web as web

REVIEW = ("Beethoven Symphony No 5. Orchestra conducted by the maestro, recorded in a warm acoustic. "
          "Hyperion CDA68400. The sound engineer gives depth; the piano and violin recording is superb. ") * 12


def pick(**values):
    base = {"composer": "Beethoven", "works": "Symphony No 5 in C minor", "performers": "Les Siècles, François-Xavier Roth",
            "label": "Harmonia Mundi", "catalogue": "HMM902500", "album_title": "", "award": "", "rating": "",
            "artistic": "Fiery and lean.", "sound": "Deep, detailed soundstage.", "reviewer": "",
            "artistic_score": 9, "sound_score": 9, "sound_exceptional": False, "page": 3}
    return {**base, **values}


class FakeCatalog:
    def __init__(self, results=None):
        self.results = results or []
        self.queries = []

    def search(self, text, **_):
        self.queries.append(text)
        return {"results": self.results}


class ReadingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = self.temp.name
        self.root = Path(self.state) / "magazines"
        ai.save_settings(self.state, {"provider": "claude", "key": "test-secret"})
        self.cfg = ai.configuration(self.state)

    def test_review_pages_are_chosen_award_pages_first_within_the_budget(self):
        pages = ["Advertisement: buy speakers", "Contents and editorial " * 30, REVIEW,
                 "Editor's Choice: the month's ten best discs. " + "x" * 600, REVIEW]
        chosen = mags.select_pages(pages)
        self.assertEqual([n for n, _ in chosen], [3, 4, 5])
        self.assertEqual([n for n, _ in mags.select_pages(pages, budget=700)], [4], "the award page is kept first")
        parts = mags.chunks(chosen, size=len(REVIEW) + 50)
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[0].startswith("[Page 3]"))

    def test_month_is_guessed_from_the_name_or_cover_in_several_languages(self):
        self.assertEqual(mags.guess_issue("Diapason_Decembre_2026.pdf", []), {"year": 2026, "month": 12})
        self.assertEqual(mags.guess_issue("x.pdf", ["NOVEMBRE 2026 · N° 745"]), {"year": 2026, "month": 11})
        self.assertEqual(mags.guess_issue("gramophone 2026-03.pdf", []), {"year": 2026, "month": 3})
        self.assertEqual(mags.guess_issue("issue.pdf", ["no date"]), {"year": 0, "month": 0})

    def test_an_issue_is_read_filed_under_its_month_and_bad_reviews_dropped(self):
        sent = []

        def post(_cfg, body, _timeout):
            sent.append(body)
            name = body["tools"][0]["name"]
            self.assertNotIn("web_search", name)
            if name == "magazine_issue":
                return {"content": [{"type": "tool_use", "name": name, "input": {
                    "magazine": "DIAPASON n° 999", "language": "fr", "year": 2026, "month": 12, "issue": "Décembre 2026"}}]}
            return {"content": [{"type": "tool_use", "name": name, "input": {"picks": [
                pick(award="Diapason d'or", page=4),
                pick(artistic="Short.", sound="", page=40),               # the same disc, from the summary page
                pick(composer="Ravel", works="Boléro", performers="Some Band", catalogue="", artistic_score=3),
                pick(composer="", works="", performers="Nobody")]}}]}
        inbox = self.root / "inbox"
        inbox.mkdir(parents=True)
        pdf = inbox / "20261201-101010-abcdef-diapason.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        card = {"id": "abc123", "title": "Beethoven: Symphony No. 5", "artist": "Les Siècles, François-Xavier Roth",
                "composer": "Ludwig van Beethoven", "label": "Harmonia Mundi", "year": 2026, "streamable": True}
        found = FakeCatalog([{"id": "zzz", "title": "Other", "artist": "Somebody", "year": 2026}, card])
        pages = ["DIAPASON décembre 2026", "Sommaire " * 60, "pub", REVIEW, REVIEW]
        with patch.object(mags, "_pdf_info", return_value=""):
            issue = mags.read_issue(self.root, self.cfg, pdf, lambda: found, post=post, pages=pages)
        self.assertEqual(issue["month_key"], "2026-12")
        self.assertEqual(len(issue["picks"]), 1, "a duplicate is merged, a bad review and an empty one dropped")
        merged = issue["picks"][0]
        self.assertEqual(merged["award"], "Diapason d'or")
        self.assertEqual(merged["qobuz"]["id"], "abc123")
        self.assertIn("Roth", found.queries[0])
        self.assertTrue((self.root / "2026/12/diapason-2026-12.pdf").is_file())
        self.assertFalse(pdf.exists())
        self.assertIn("English", sent[1]["messages"][0]["content"])

    def test_roles_singles_and_other_albums_are_not_matched(self):
        self.assertEqual(mags._people("Pavel Kolesnikov pf"), "Pavel Kolesnikov")
        self.assertEqual(mags._surnames("Bruno de Sá counterten; Cyrille Dubois ten"), {"sa", "dubois"})
        nocturnes = pick(composer="Chopin", works="Complete Nocturnes", performers="Pavel Kolesnikov pf",
                         label="Harmonia Mundi", catalogue="")
        single = {"id": "s1", "title": "Nocturne Op 9 No 2", "artist": "Pavel Kolesnikov", "composer": "Chopin",
                  "label": "Harmonia Mundi", "year": 2026, "tracks": 1, "duration": 280, "streamable": True}
        album = {**single, "id": "a1", "title": "Chopin: Nocturnes", "tracks": 22, "duration": 6400}
        found = FakeCatalog([single, album])
        self.assertEqual(mags.find_on_qobuz(found, nocturnes, 2026)["id"], "a1")
        self.assertEqual(found.queries[0], "Pavel Kolesnikov Chopin Complete Nocturnes")
        concerto = pick(composer="Nielsen", works="Clarinet Concerto, Op 57", catalogue="",
                        performers="Stanley Drucker; New York Philharmonic / Leonard Bernstein", label="Sony Classical")
        symphonies = {"id": "b1", "title": "Nielsen: Symphony No. 3 & Symphony No. 5", "artist": "Leonard Bernstein",
                      "composer": "Carl Nielsen", "label": "Sony Classical", "year": 2017, "tracks": 6, "streamable": True}
        self.assertIsNone(mags.find_on_qobuz(FakeCatalog([symphonies]), concerto, 2026))

    def test_magazine_names_and_file_names_are_normalised(self):
        for given, name in (("BBC Music", "BBC Music Magazine"), ("Gramophone Magazine UK", "Gramophone"),
                            ("DIAPASON N° 745 - décembre 2026", "Diapason"), ("FONO FORUM", "Fono Forum"),
                            ("Revue XYZ 12 2026", "Revue XYZ"), ("", "Magazine")):
            self.assertEqual(mags.canonical_magazine(given), name)
        self.assertEqual(mags.issue_slug("BBC Music Magazine, Dec 2026", "2026-12"), "bbc-music-magazine-2026-12")
        self.assertEqual(mags.issue_slug("Crescendo Magazin", "2027-01"), "crescendo-2027-01")

    def test_a_long_work_title_falls_back_to_the_names(self):
        card = {"id": "x1", "title": "Sibelius: Complete Symphonies", "artist": "Klaus Mäkelä",
                "label": "Decca Music Group Ltd.", "year": 2022, "streamable": True}

        class Picky(FakeCatalog):
            def search(self, text, **_):
                self.queries.append(text)
                return {"results": [card] if "Nos" not in text else []}
        found = Picky()
        p = pick(composer="Sibelius", works="Symphonies Nos 1-7", performers="Oslo Philharmonic Orchestra / Klaus Mäkelä",
                 label="Decca", catalogue="")
        self.assertEqual(mags.find_on_qobuz(found, p, 2026)["id"], "x1")
        self.assertEqual(found.queries[1], "Mäkelä Sibelius")

    def test_a_scan_is_read_by_ocr_and_refused_only_if_that_finds_nothing(self):
        inbox = self.root / "inbox"
        inbox.mkdir(parents=True)
        pdf = inbox / "scan.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with (patch.object(mags, "ocr_pages", return_value=["", ""]) as ocr,
              self.assertRaisesRegex(mags.MagazineError, "even by OCR")):
            mags.read_issue(self.root, self.cfg, pdf, FakeCatalog, pages=["", "  "])
        ocr.assert_called_once()
        self.assertTrue(pdf.exists(), "a failed upload stays in the inbox")

    def test_ocr_resumes_from_the_pages_already_read(self):
        pdf = Path(self.state) / "scan.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        mags._ocr_cache(pdf).write_text(json.dumps({"count": 3, "languages": "fra+eng",
                                                     "pages": ["cover", None, None]}))
        read = []

        def page(_pdf, number, languages, _folder):
            read.append((number, languages))
            return f"page {number}"
        with (patch.object(mags, "_page_count", return_value=3), patch.object(mags, "_ocr_page", side_effect=page),
              patch.object(mags.subprocess, "run") as run):
            run.return_value.stdout = b"List of available languages\neng\nfra\nosd\n"
            pages = mags.ocr_pages(pdf)
        self.assertEqual(pages, ["cover", "page 2", "page 3"])
        self.assertEqual(read, [(2, "fra+eng"), (3, "fra+eng")])

    def write_issue(self, key, magazine, picks):
        folder = self.root / key[:4] / key[5:]
        folder.mkdir(parents=True, exist_ok=True)
        slug = f"{mags._safe(magazine)}-{key}"
        (folder / f"{slug}.json").write_text(json.dumps({"magazine": magazine, "issue": "", "language": "en",
                                                          "slug": slug, "picks": picks}))
        (folder / f"{slug}.pdf").write_bytes(b"%PDF-1.4")
        return slug

    def test_agreement_across_magazines_and_neighbouring_months_ranks_first(self):
        qobuz = {"id": "q1", "title": "Beethoven 5", "artist": "Roth", "streamable": True}
        self.write_issue("2026-12", "Gramophone", [
            pick(qobuz=qobuz, award="Editor's Choice", sound_score=8),
            pick(composer="Bach", works="Goldberg Variations", performers="Jane Pianist", catalogue="",
                 artistic_score=10, sound_score=0, qobuz=None)])
        self.write_issue("2026-12", "Diapason", [pick(catalogue="HMM 902500", performers="Les Siecles, F.-X. Roth",
                                                      works="Symphonie n° 5", sound_score=10, sound_exceptional=True)])
        self.write_issue("2027-01", "BBC Music Magazine", [pick(catalogue="", works="Symphony No. 5", sound_score=9)])
        self.write_issue("2026-06", "Fono Forum", [pick(catalogue="")])           # too far away to count
        view = mags.month_view(self.root, "2026-12")
        self.assertEqual([i["magazine"] for i in view["issues"]], ["Diapason", "Gramophone"])
        first, second = view["entries"]
        self.assertEqual(first["id"], "q1")
        self.assertEqual(first["mag"]["agreement"], 3)
        self.assertEqual(first["mag"]["quality"], 9.0)
        self.assertTrue(first["mag"]["q"])
        self.assertEqual(first["mag"]["awards"], ["Gramophone: Editor's Choice"])
        self.assertEqual(second["id"], "")
        self.assertIsNone(second["mag"]["quality"])
        self.assertEqual(view["agreed"], 1)
        self.assertEqual([m for m in mags.months(self.root)], ["2027-01", "2026-12", "2026-06"])

        asked = []

        def post(_cfg, body, _timeout):
            data = body["messages"][0]["content"]
            asked.append(data)
            key = mags._signature(next(g for g in mags.clusters(self.root, "2026-12") if len(g) > 1))
            return {"content": [{"type": "tool_use", "name": "magazine_consensus", "input": {"verdicts": [
                {"key": key, "artistic": "All three praise its fire.", "sound": "Deep and detailed."}]}}]}
        mags.consensus(self.root, self.cfg, "2026-12", post=post)
        mags.consensus(self.root, self.cfg, "2026-12", post=post)
        self.assertEqual(len(asked), 1, "a shared verdict is asked for once")
        view = mags.month_view(self.root, "2026-12")
        self.assertEqual(view["entries"][0]["mag"]["artistic"], "All three praise its fire.")
        text = mags.write_document(self.root, "2026-12")
        self.assertTrue((self.root / "2026/12/digest.md").is_file())
        self.assertLess(text.index("several magazines"), text.index("Goldberg"))
        self.assertIn("Shared verdict", text)
        self.assertIn("sound 9.0/10 (Q)", text)

        self.assertTrue(mags.remove_issue(self.root, "2026-12", "diapason-2026-12"))
        self.assertEqual(mags.month_view(self.root, "2026-12")["entries"][0]["mag"]["agreement"], 2)
        with self.assertRaises(mags.MagazineError):
            mags.remove_issue(self.root, "2026-12", "../x")
        with self.assertRaises(mags.MagazineError):
            mags.pdf_path(self.root, "../../etc", "passwd")

    def test_albums_missing_a_day_ago_are_looked_up_again(self):
        day = mags.REMATCH_AFTER
        single = {"id": "s1", "title": "Symphony No. 8: I. Adagio", "tracks": 1, "image": "i.jpg"}
        whole = {"id": "a1", "title": "Shostakovich: Symphony No. 8", "artist": "Alain Altinoglu", "tracks": 5,
                 "image": "c.jpg", "streamable": True}
        slug = self.write_issue("2026-11", "Gramophone", [
            pick(works="Symphony No 8", composer="Shostakovich", performers="Frankfurt RSO / Alain Altinoglu",
                 catalogue="", qobuz=single, matched_at=int(time.time()) - day - 5),
            pick(works="Nocturnes", composer="Chopin", performers="Pavel Kolesnikov", catalogue="",
                 qobuz=None, matched_at=int(time.time()) - 60),                 # checked a minute ago
            pick(works="Goldberg Variations", composer="Bach", performers="Jane Pianist", catalogue="",
                 qobuz={**whole, "id": "g1", "image": ""}, matched_at=0)])       # no cover: looked up too
        (self.root / "2026/11" / f"{slug}.json").write_text(json.dumps({
            **json.loads((self.root / "2026/11" / f"{slug}.json").read_text()), "read_at": 1}))
        asked = []

        def find(_catalog, p, _year):
            asked.append(p["works"])
            return whole if p["works"] == "Symphony No 8" else None
        with patch.object(mags, "find_on_qobuz", side_effect=find):
            mags._job["running"] = True
            self.assertEqual(mags.refresh_matches(self.root, "2026-11", FakeCatalog), 0, "not while a job reads")
            mags._job["running"] = False
            self.assertEqual(mags.refresh_matches(self.root, "2026-11", FakeCatalog), 2)
            for _ in range(200):
                if "2026-11" not in mags._refreshing:
                    break
                time.sleep(.01)
        self.assertEqual(sorted(asked), ["Goldberg Variations", "Symphony No 8"])
        picks = json.loads((self.root / "2026/11" / f"{slug}.json").read_text())["picks"]
        self.assertEqual(picks[0]["qobuz"]["id"], "a1", "the whole album replaces the single")
        self.assertEqual(picks[2]["qobuz"]["id"], "g1", "a lookup finding nothing keeps what was there")
        self.assertGreater(picks[2]["matched_at"], 0)
        self.assertEqual(mags.refresh_matches(self.root, "2026-11", FakeCatalog), 0, "not again the same day")
        self.assertIn("Shostakovich: Symphony No. 8", (self.root / "2026/11/digest.md").read_text())

    def test_results_are_shared_with_other_boxes_but_not_the_pdfs(self):
        shared = Path(self.state) / "dr-sync" / "magazines"
        shared.mkdir(parents=True)
        mags.set_share(str(shared), "arki")
        self.addCleanup(mags.set_share, "", "")
        self.write_issue("2026-12", "Gramophone", [pick(award="Editor's Choice")])
        rows = mags.export_rows(self.root)
        self.assertEqual([r["kind"] for r in rows], ["issue"])
        self.assertEqual(rows[0]["slug"], "gramophone-2026-12")
        self.assertNotIn("pdf", json.dumps(rows).lower().replace("pdf_", ""), "no PDF travels")

        diapason = {"kind": "issue", "month": "2026-12", "slug": "diapason-2026-12", "magazine": "Diapason",
                    "issue": "Décembre 2026", "read_at": 5, "picks": [pick(catalogue="HMM 902500", award="Diapason d'or")]}
        older = {**diapason, "read_at": 1, "picks": []}
        again = {**rows[0], "read_at": 99, "picks": []}           # our own issue, read on bee too
        lines = [diapason, older, again]
        (shared / "bee.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
        (shared / "arki.jsonl").write_text(json.dumps({**diapason, "slug": "ours-2026-12"}) + "\n")
        mags.set_share(str(shared), "arki")
        view = mags.month_view(self.root, "2026-12")
        self.assertEqual([(i["magazine"], i["remote"], i["box"]) for i in view["issues"]],
                         [("Gramophone", False, None), ("Diapason", True, "bee")])
        self.assertEqual(view["entries"][0]["mag"]["agreement"], 2, "bee's newest Diapason agrees with ours")
        remote = [r for r in view["entries"][0]["mag"]["reviews"] if r["remote"]]
        self.assertEqual(remote[0]["box"], "bee")
        self.assertEqual(mags.months(self.root), ["2026-12"])
        with self.assertRaises(mags.MagazineError):
            mags.pdf_path(self.root, "2026-12", "diapason-2026-12")

        key = view["entries"][0]["mag"]["key"]
        (shared / "bee.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines) + json.dumps(
            {"kind": "verdicts", "month": "2026-12", "verdicts": {key: {"artistic": "Both love it.", "sound": ""}}}) + "\n")
        asked = []
        mags.consensus(self.root, self.cfg, "2026-12", post=lambda *a: asked.append(a))
        self.assertEqual(asked, [], "a verdict made on another box is not asked for again")
        self.assertEqual(mags.month_view(self.root, "2026-12")["entries"][0]["mag"]["artistic"], "Both love it.")
        self.assertFalse(any(r["kind"] == "verdicts" for r in mags.export_rows(self.root)), "bee's verdict stays bee's")

    def test_same_recording_needs_shared_performers(self):
        a = pick(catalogue="")
        self.assertTrue(mags.same_recording(a, pick(catalogue="", works="Symphonie n° 5 en ut mineur")))
        self.assertFalse(mags.same_recording(a, pick(catalogue="", performers="Berliner Philharmoniker, Karajan")))
        self.assertFalse(mags.same_recording(a, pick(catalogue="", composer="Brahms", works="Symphony No 1")))
        self.assertTrue(mags.same_recording(a, pick(catalogue="", performers="Other", qobuz=None)) is False)


class UploadTest(unittest.TestCase):
    def setUp(self):
        mags._job.update(running=False, items=[], detail="", error="", finished=0)
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

    def test_upload_needs_the_app_header_and_a_pdf(self):
        data = lambda: {"files": (io.BytesIO(b"%PDF-1.4 hello"), "Gramophone Dec 2026.pdf"), "start": "0"}  # noqa: E731
        refused = self.client.post("/qobuz/magazines/upload", data=data(), content_type="multipart/form-data")
        self.assertEqual(refused.status_code, 403)
        answer = self.client.post("/qobuz/magazines/upload", data=data(), content_type="multipart/form-data",
                                  headers={"X-Qobuz-AI": "1"}).get_json()
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["job"]["items"][0]["name"], "gramophone-dec-2026.pdf")
        bad = self.client.post("/qobuz/magazines/upload", content_type="multipart/form-data",
                               data={"files": (io.BytesIO(b"<html>"), "x.pdf"), "start": "0"},
                               headers={"X-Qobuz-AI": "1"}).get_json()
        self.assertFalse(bad["ok"])
        self.assertIn("not a PDF", bad["error"])
        view = self.client.get("/qobuz/magazines").get_json()
        self.assertEqual(view["month"], "")
        self.assertEqual(view["job"]["items"][0]["state"], "waiting")
        name = view["job"]["items"][0]["file"]
        gone = self.client.post("/qobuz/magazines/remove", json={"upload": name},
                                headers={"X-Qobuz-AI": "1"}).get_json()
        self.assertTrue(gone["ok"])
        self.assertEqual(self.client.get("/qobuz/magazines").get_json()["job"]["items"], [])

    def test_the_job_reads_the_inbox_in_the_background(self):
        ai.save_settings(self.temp.name, {"provider": "claude", "key": "test-secret"})
        root = Path(self.temp.name) / "magazines"
        mags.save_upload(root, io.BytesIO(b"%PDF-1.4 x"), "BBC Music November 2026.pdf")

        def read(root_, cfg, path, catalog_fn, **_):
            folder = root_ / "2026/11"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "bbc-2026-11.json").write_text(json.dumps({"magazine": "BBC", "slug": "bbc-2026-11", "picks": []}))
            path.unlink()
            return {"magazine": "BBC", "month_key": "2026-11", "picks": []}
        with patch.object(mags, "read_issue", side_effect=read):
            answer = self.client.post("/qobuz/magazines/job", json={"action": "start"},
                                      headers={"X-Qobuz-AI": "1"}).get_json()
            self.assertTrue(answer["ok"], answer)
            for _ in range(100):
                if not mags.status(root)["running"]:
                    break
                time.sleep(.02)
        job = mags.status(root)
        self.assertFalse(job["running"])
        self.assertEqual(job["items"][0]["state"], "done")
        self.assertTrue((root / "2026/11/digest.md").is_file())
        view = self.client.get("/qobuz/magazines").get_json()
        self.assertEqual(view["month"], "2026-11")
        self.assertEqual(view["months"], [{"key": "2026-11", "label": "November 2026"}])

    def test_an_upload_during_a_job_joins_its_queue(self):
        import threading
        ai.save_settings(self.temp.name, {"provider": "claude", "key": "test-secret"})
        root = Path(self.temp.name) / "magazines"
        mags.save_upload(root, io.BytesIO(b"%PDF-1.4 x"), "Gramophone.pdf")
        gate, read = threading.Event(), []

        def slow(root_, cfg, path, catalog_fn, **_):
            gate.wait(5)
            read.append(path.name)
            path.unlink()
            raise mags.MagazineError("no text")
        with patch.object(mags, "read_issue", side_effect=slow):
            self.assertEqual(mags.start(root, self.temp.name, None), {"started": True, "queued": False})
            mags.save_upload(root, io.BytesIO(b"%PDF-1.4 y"), "Diapason.pdf")
            self.assertEqual(mags.start(root, self.temp.name, None), {"started": True, "queued": True})
            gate.set()
            for _ in range(200):
                if not mags.status(root)["running"]:
                    break
                time.sleep(.02)
        self.assertEqual(len(read), 2, "the second upload was read by the same job")
        self.assertEqual([i["state"] for i in mags.status(root)["items"]], ["error", "error"])
        self.assertEqual(mags.status(root)["items"][1]["detail"], "no text")


if __name__ == "__main__":
    unittest.main()
