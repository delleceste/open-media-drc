#!/usr/bin/env python3
"""The /bitperfect page: routing, guards, and the refusals that matter.

Two of these are safety properties rather than features. The DRC path is not
bit-perfect by design, so a run there must not quietly produce a verdict; and
the one token-free route (the asset MPD and upmpdcli fetch by URL) must not be
usable to read anything outside the asset cache.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import wave

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))

import bitperfect as BP  # noqa: E402

SPEC = importlib.util.spec_from_file_location("omdrc_bitperfect_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)

LIB = ROOT / "scripts/bitperfect-lib.py"


def counter_wav(path: Path, frames: int = 4000) -> None:
    buf = bytearray()
    for i in range(frames):
        buf += struct.pack("<ii", i & 0xFFFF, (i * 40503 + (i >> 16)) & 0xFFFF)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(4)
        w.setframerate(44100)
        w.writeframes(bytes(buf))


class PageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bppage."))
        self.results = self.tmp / "results"
        self.assets = self.tmp / "assets"
        self.music = self.tmp / "music"
        for d in (self.results, self.assets, self.music):
            d.mkdir(parents=True)
        APP.BITPERFECT = BP.Settings(
            tools_root=ROOT / "scripts",
            generator=ROOT / "tests/gen-bitperfect-wav.py",
            results_root=self.results, assets_root=self.assets,
            state_root=self.tmp / "jobs", music_root=self.music)
        APP._BITPERFECT_MANAGER = None
        self.client = APP.app.test_client()
        self.headers = {"X-OMDRC-CSRF": APP._CONFIGURATION_CSRF}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed_run(self, name="20260101-000000-mpd", corrupt_at=None):
        wav = self.tmp / "src.wav"
        counter_wav(wav)
        ref = self.tmp / "ref.raw"
        subprocess.run([sys.executable, str(LIB), "prep", wav, ref], check=True,
                       capture_output=True)
        data = bytearray(ref.read_bytes())
        if corrupt_at is not None:
            data[corrupt_at] ^= 0xFF
        cap = self.tmp / "cap.raw"
        cap.write_bytes(b"\0" * 5648 + bytes(data) + b"\0" * 2000)
        subprocess.run([sys.executable, str(LIB), "finalize", ref, cap, "44100",
                        "2", self.results / name, "test/0", str(wav)],
                       capture_output=True)
        return name

    # ── routing ─────────────────────────────────────────────────────────────

    def test_page_renders_and_is_linked_from_the_panel(self):
        r = self.client.get("/bitperfect")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Bit-perfect check", r.data)
        index = (SRC / "templates/index.html").read_text()
        self.assertIn('href="/bitperfect"', index)

    def test_state_reports_readiness_assets_and_runs(self):
        d = self.client.get("/bitperfect/api/state").get_json()
        self.assertTrue(d["ok"])
        for key in ("brutefir", "sudo", "rates", "blocking"):
            self.assertIn(key, d["readiness"])
        self.assertTrue(d["assets"])

    def test_disabled_page_is_not_served(self):
        APP.BITPERFECT = BP.Settings(enabled=False,
                                     results_root=self.results,
                                     assets_root=self.assets,
                                     state_root=self.tmp / "jobs")
        APP._BITPERFECT_MANAGER = None
        self.assertEqual(self.client.get("/bitperfect").status_code, 404)
        self.assertEqual(
            self.client.get("/bitperfect/api/state").status_code, 404)

    # ── guards ──────────────────────────────────────────────────────────────

    def test_mutations_require_the_csrf_token(self):
        for url, body in (("/bitperfect/api/run", {"source": "mpd"}),
                          ("/bitperfect/api/assets/generate", {"rate": 44100})):
            self.assertEqual(self.client.post(url, json=body).status_code, 403)
            self.assertEqual(
                self.client.post(url, json=body,
                                 headers={"X-OMDRC-CSRF": "wrong"}).status_code,
                403)

    def test_a_cross_origin_mutation_is_refused(self):
        r = self.client.post("/bitperfect/api/run", json={"source": "mpd"},
                             headers={**self.headers,
                                      "Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_the_asset_route_serves_basenames_from_the_cache_only(self):
        """It is deliberately token-free — MPD's curl plugin and upmpdcli fetch
        it themselves — so path escape is the thing that must not work."""
        (self.assets / "ok.wav").write_bytes(b"RIFF")
        secret = self.tmp / "secret.wav"
        secret.write_bytes(b"nope")
        self.assertEqual(
            self.client.get("/bitperfect/api/asset/ok.wav").status_code, 200)
        for bad in ("../secret.wav", "..%2Fsecret.wav", "nosuch.wav"):
            self.assertNotEqual(
                self.client.get(f"/bitperfect/api/asset/{bad}").status_code, 200)

    def test_a_path_outside_the_music_root_is_refused(self):
        outside = self.tmp / "elsewhere.wav"
        counter_wav(outside)
        r = self.client.post("/bitperfect/api/material",
                             data={"path": str(outside)}, headers=self.headers)
        self.assertEqual(r.status_code, 400)
        self.assertIn("must be inside", r.get_json()["error"])

    def test_a_path_inside_the_music_root_is_accepted(self):
        inside = self.music / "track.wav"
        counter_wav(inside)
        r = self.client.post("/bitperfect/api/material",
                             data={"path": str(inside)}, headers=self.headers)
        self.assertEqual(r.status_code, 202)
        # The job runs on a background thread; let it finish before tearDown
        # removes the state directory under it.
        job = APP._bitperfect().get_job(r.get_json()["job"])
        for _ in range(200):
            if job.phase in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        self.assertEqual(job.phase, "succeeded", job.error)

    # ── the DRC refusal ─────────────────────────────────────────────────────

    def _fake_pgrep(self, found: bool) -> dict:
        """A PATH with a pgrep that answers as we choose, so the guard can be
        exercised without stopping or starting the real chain."""
        import os
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir(exist_ok=True)
        pgrep = fakebin / "pgrep"
        pgrep.write_text(f"#!/bin/sh\nexit {0 if found else 1}\n")
        pgrep.chmod(0o755)
        return {**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}"}

    def test_the_runner_refuses_to_judge_the_drc_path(self):
        """brutefir convolves the FIR filter, so its output is SUPPOSED to
        differ. A verdict there would look like a failure and mean nothing."""
        wav = self.music / "t.wav"
        counter_wav(wav)
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts/bitperfect_runner.py"),
             "--source", "mpd", "--input", str(wav), "--out", str(self.tmp / "x")],
            capture_output=True, text=True, env=self._fake_pgrep(True))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not bit-perfect by design",
                      (r.stdout + r.stderr).lower())
        self.assertFalse((self.tmp / "x.json").exists(),
                         "a refused run must not leave a verdict behind")

    def test_allow_drc_overrides_the_refusal(self):
        """The guard is a guard, not a wall: an operator who knows what the
        DRC path is may still want the capture."""
        wav = self.music / "t.wav"
        counter_wav(wav)
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts/bitperfect_runner.py"),
             "--source", "mpd", "--input", str(wav), "--out", str(self.tmp / "y"),
             "--allow-drc"], capture_output=True, text=True,
            env=self._fake_pgrep(True))
        self.assertNotIn("not bit-perfect by design",
                         (r.stdout + r.stderr).lower())

    def test_readiness_blocks_while_brutefir_runs(self):
        manager = APP._bitperfect()
        real = BP.subprocess.run

        def fake(argv, *a, **kw):
            if argv[:2] == ["pgrep", "-x"]:
                return subprocess.CompletedProcess(argv, 0, "123\n", "")
            return real(argv, *a, **kw)

        with mock.patch.object(BP.subprocess, "run", fake):
            state = manager.readiness()
        self.assertTrue(state["brutefir"])
        self.assertTrue(any("brutefir" in b for b in state["blocking"]))

    # ── the byte view ───────────────────────────────────────────────────────

    def test_report_window_and_scan_agree_on_a_clean_run(self):
        name = self.seed_run()
        report = self.client.get(
            f"/bitperfect/api/runs/{name}/report").get_json()["report"]
        self.assertEqual(report["kind"], "BIT-PERFECT")
        self.assertIsNone(report["first_mismatch"])
        view = self.client.get(
            f"/bitperfect/api/runs/{name}/window?offset=0&frames=16").get_json()
        self.assertTrue(view["ok"])
        self.assertTrue(all(row["eq"] for row in view["rows"]))
        scan = self.client.get(
            f"/bitperfect/api/runs/{name}/scan?buckets=32").get_json()
        self.assertTrue(all(c["s"] == "equal" for c in scan["cells"]))

    def test_a_corrupt_run_is_locatable_from_the_report_alone(self):
        name = self.seed_run("20260101-000001-mpd", corrupt_at=16000)
        report = self.client.get(
            f"/bitperfect/api/runs/{name}/report").get_json()["report"]
        self.assertEqual(report["first_mismatch"], 16000)
        view = self.client.get(
            f"/bitperfect/api/runs/{name}/window"
            f"?offset={report['first_mismatch']}&frames=1").get_json()
        self.assertFalse(view["rows"][0]["eq"])
        self.assertEqual(view["rows"][0]["d"], [0])

    def test_an_unknown_or_unsafe_run_id_is_refused(self):
        self.assertEqual(
            self.client.get("/bitperfect/api/runs/nosuch/report").status_code, 404)
        for bad in ("..", "../../etc/passwd"):
            self.assertNotEqual(
                self.client.get(
                    f"/bitperfect/api/runs/{bad}/window").status_code, 200)

    def test_a_run_without_payloads_does_not_offer_a_byte_view(self):
        (self.results / "20260101-000002-live.json").write_text(
            json.dumps({"verdict": "NO CAPTURE", "kind": "NO CAPTURE"}))
        runs = self.client.get("/bitperfect/api/state").get_json()["runs"]
        row = next(r for r in runs if r["id"] == "20260101-000002-live")
        self.assertFalse(row["has_bytes"])
        r = self.client.get(
            "/bitperfect/api/runs/20260101-000002-live/window").get_json()
        self.assertFalse(r["ok"])


class SharedLockTest(unittest.TestCase):
    def test_a_filter_install_and_a_tap_run_contend_for_one_lock(self):
        """One box, one DAC: these must never overlap."""
        import configuration
        self.assertIs(configuration.OPERATION_LOCK,
                      configuration.ConfigurationManager(
                          configuration.Settings(
                              state_root=Path(tempfile.mkdtemp())),
                          dict).lock)


if __name__ == "__main__":
    unittest.main()
