#!/usr/bin/env python3
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from werkzeug.datastructures import FileStorage

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "omdrc-ctrl/src"
sys.path.insert(0, str(SRC))
import configuration

SPEC = importlib.util.spec_from_file_location("omdrc_configuration_app", SRC / "app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)

HELPER_SPEC = importlib.util.spec_from_file_location(
    "omdrc_config_helper", ROOT / "scripts/omdrc-config-helper.py")
HELPER = importlib.util.module_from_spec(HELPER_SPEC)
assert HELPER_SPEC.loader
HELPER_SPEC.loader.exec_module(HELPER)


class CardIdentityTest(unittest.TestCase):
    def test_serial_is_added_only_for_duplicate_vid_pid(self):
        rows = configuration._disambiguate([
            {"vid": "0x1234", "pid": "0xabcd", "serial": "one"},
            {"vid": "0x1234", "pid": "0xabcd", "serial": "two"},
            {"vid": "0x9999", "pid": "0x0001", "serial": "irrelevant"},
        ])
        self.assertEqual(rows[0]["identity"], "0x1234:0xabcd:one")
        self.assertEqual(rows[1]["identity"], "0x1234:0xabcd:two")
        self.assertEqual(rows[2]["identity"], "0x9999:0x0001")

    def test_freebsd_status_exposes_dsp_mapping(self):
        output = "/dev/dsp1   dac      Example DAC (uaudio1, play/rec, 0x1234:0xabcd)\n"
        done = mock.Mock(stdout=output)
        with mock.patch.object(configuration.subprocess, "run", return_value=done):
            cards = configuration.freebsd_cards({})
        self.assertEqual(cards[0]["device"], "/dev/dsp1")
        self.assertEqual(cards[0]["identity"], "0x1234:0xabcd")


class UploadTest(unittest.TestCase):
    def manager_and_job(self, root: str):
        settings = configuration.Settings(state_root=Path(root))
        manager = configuration.ConfigurationManager(settings, lambda: {})
        return manager, manager._new_job("upload")

    def test_upload_keeps_only_basenames_inside_private_job(self):
        with tempfile.TemporaryDirectory() as name:
            manager, job = self.manager_and_job(name)
            files = [FileStorage(stream=io.BytesIO(b"left"),
                                 filename="120.blue.test.txts/L.txt")]
            mdat = FileStorage(stream=io.BytesIO(b"session"), filename="120.blue.test.mdat")
            directory = manager.save_uploads(job, files, mdat)
            self.assertEqual((directory / "L.txt").read_bytes(), b"left")
            self.assertEqual((directory / "120.blue.test.mdat").read_bytes(), b"session")

    def test_upload_rejects_mdat_from_another_session(self):
        with tempfile.TemporaryDirectory() as name:
            manager, job = self.manager_and_job(name)
            files = [FileStorage(stream=io.BytesIO(b"left"),
                                 filename="abc.txts/L.txt")]
            mdat = FileStorage(stream=io.BytesIO(b"session"), filename="def.mdat")
            with self.assertRaisesRegex(ValueError, "must be abc.mdat"):
                manager.save_uploads(job, files, mdat)

    def test_upload_rejects_multiple_export_folders(self):
        with tempfile.TemporaryDirectory() as name:
            manager, job = self.manager_and_job(name)
            files = [
                FileStorage(stream=io.BytesIO(b"left"), filename="abc.txts/L.txt"),
                FileStorage(stream=io.BytesIO(b"right"), filename="def.txts/R.txt"),
            ]
            mdat = FileStorage(stream=io.BytesIO(b"session"), filename="abc.mdat")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                manager.save_uploads(job, files, mdat)


class RepositoryFirstInstallTest(unittest.TestCase):
    def make_manager(self, root: Path, *, git: bool = False):
        design_root = root / "design-store"
        site_root = root / "live-site"
        state_root = root / "jobs"
        (design_root / "configs/120.blue").mkdir(parents=True)
        site_root.mkdir()
        if git:
            subprocess.run(["git", "init", "-q", str(design_root)], check=True)
        settings = configuration.Settings(
            site_root=site_root, design_root=design_root, state_root=state_root,
            tools_root=ROOT / "scripts", helper="omdrc-config-helper")
        manager = configuration.ConfigurationManager(settings, lambda: {})
        return manager, design_root, site_root

    @staticmethod
    def publish_fixture(design_root: Path) -> Path:
        analysis = design_root / "filters/120.blue/analysis/Rscreen.json"
        analysis.parent.mkdir(parents=True, exist_ok=True)
        analysis.write_bytes(b"{}\n")
        digest = hashlib.sha256(analysis.read_bytes()).hexdigest()
        manifest = {
            "geometry": "120.blue", "design_id": "Rscreen", "variant": "Rscreen",
            "bundle_id": "fixture", "source": {"artifacts": {}, "measurements": {}},
            "analysis": {"path": "analysis/Rscreen.json", "sha256": digest},
            "runtime": {"rates": {}},
        }
        path = design_root / "filters/120.blue/provenance/Rscreen.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest) + "\n")
        return path

    def exercise_install(self, git: bool):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manager, design_root, site_root = self.make_manager(root, git=git)
            export = root / "upload/120.blue.Rscreen.txts"
            export.mkdir(parents=True)
            job = manager._new_job("install")
            calls = []

            def fake_run(current, argv, timeout=None):
                del current, timeout
                calls.append(argv)
                if "new_filter_design.py" in " ".join(argv):
                    self.publish_fixture(design_root)
                elif "filter-publish" in argv:
                    staged = Path(argv[argv.index("--staged") + 1])
                    shutil.copytree(staged, site_root, dirs_exist_ok=True)

            with mock.patch.object(manager, "run_command", side_effect=fake_run):
                manager.install_filter(job, export)
            publication = next(argv for argv in calls
                               if "new_filter_design.py" in " ".join(argv))
            self.assertIn("--geometry", publication)
            self.assertEqual(publication[publication.index("--geometry") + 1], "120.blue")
            self.assertEqual(publication[publication.index("--design") + 1], "Rscreen")
            self.assertNotIn("--live", publication)
            self.assertIn("--require-commit" if git else "--no-commit", publication)
            self.assertEqual("--allow-uncommitted" in publication, not git)
            self.assertEqual("--require-clean-site" in publication, git)
            self.assertEqual(sum("verify_filter_bundle.py" in " ".join(argv)
                                 for argv in calls), 2)
            self.assertTrue((site_root / "filters/120.blue/provenance/Rscreen.json").is_file())

    def test_non_git_design_store_is_authoritative_without_forcing_git(self):
        self.exercise_install(False)

    def test_existing_git_design_store_requires_the_history_commit(self):
        self.exercise_install(True)

    def test_existing_git_design_store_must_be_clean_before_publication(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manager, design_root, _ = self.make_manager(root, git=True)
            (design_root / "unfinished.txt").write_text("operator work\n")
            export = root / "upload/120.blue.Rscreen.txts"
            export.mkdir(parents=True)
            job = manager._new_job("install")
            with mock.patch.object(manager, "run_command") as run:
                with self.assertRaisesRegex(RuntimeError, "uncommitted work"):
                    manager.install_filter(job, export)
            run.assert_not_called()

    def test_temporary_upload_parent_never_becomes_geometry(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manager, _, _ = self.make_manager(root)
            export = root / "jobs/random/upload/120.blue.Rscreen.txts"
            export.mkdir(parents=True)
            self.assertEqual(manager.design_identity(export), ("120.blue", "Rscreen"))

    def test_first_design_in_empty_store_can_use_explicit_identity(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manager, design_root, _ = self.make_manager(root)
            shutil.rmtree(design_root / "configs")
            export = root / "upload/session.txts"
            export.mkdir(parents=True)
            self.assertEqual(
                manager.design_identity(export, "new-room", "first-design"),
                ("new-room", "first-design"))

    def test_parent_project_folder_identifies_first_design_without_git(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manager, design_root, _ = self.make_manager(root)
            shutil.rmtree(design_root / "configs")
            export = root / "upload/120.blue.Rscreen.txts"
            export.mkdir(parents=True)
            self.assertEqual(
                manager.design_identity(export, project_folder="DRC-120.blue"),
                ("120.blue", "Rscreen"))

    def test_design_list_distinguishes_authority_runtime_and_drift(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manager, design_root, site_root = self.make_manager(root)
            authoritative = json.loads(self.publish_fixture(design_root).read_text())
            live_manifest = site_root / "filters/120.blue/provenance/Rscreen.json"
            live_manifest.parent.mkdir(parents=True)
            live_manifest.write_text(json.dumps(authoritative))
            orphan = site_root / "filters/legacy/provenance/old.json"
            orphan.parent.mkdir(parents=True)
            orphan.write_text(json.dumps({
                "geometry": "legacy", "design_id": "old", "bundle_id": "runtime",
                "source": {}, "runtime": {"rates": {}},
            }))

            rows = {(row["geometry"], row["design"]): row
                    for row in manager.designs()}
            self.assertEqual(
                rows[("120.blue", "Rscreen")]["location"],
                "OUT OF SYNC: design store differs from runtime")
            self.assertFalse(rows[("120.blue", "Rscreen")]["installed"])
            self.assertEqual(rows[("legacy", "old")]["location"],
                             "runtime-only legacy")

    def test_saved_design_is_selected_and_cannot_be_removed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manager, design_root, _ = self.make_manager(root)
            self.publish_fixture(design_root)
            manager.active_design = lambda: {
                "running": True, "geometry": "120.blue", "design": "default",
                "saved": {"geometry": "120.blue", "design": "@Rscreen"},
            }
            row = next(item for item in manager.designs()
                       if item["geometry"] == "120.blue" and item["design"] == "Rscreen")
            self.assertTrue(row["selected"])
            job = manager._new_job("remove")
            with self.assertRaisesRegex(RuntimeError, "running and saved"):
                manager.remove_filter(job, "120.blue", "Rscreen")


class PrivilegedPublicationTest(unittest.TestCase):
    @staticmethod
    def write(path: Path, data: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def test_bound_template_is_preserved_and_runtime_config_is_derived(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            staged, site = root / "staged", root / "site"
            geometry_root = staged / "filters/room"
            analysis_hash = self.write(geometry_root / "analysis/design.json", b"{}\n")
            left_hash = self.write(geometry_root / "48000/@design/L.raw", b"left")
            right_hash = self.write(geometry_root / "48000/@design/R.raw", b"right")
            template_relative = Path("configs/room/brutefir-48000@design.conf.in")
            template = (
                'sampling_rate: 48000;\n'
                'coeff "c-l" { filename: "@REPO_DIR@/filters/room/48000/@design/L.raw"; };\n'
                'coeff "c-r" { filename: "@REPO_DIR@/filters/room/48000/@design/R.raw"; };\n')
            template_hash = self.write(staged / template_relative, template.encode())
            manifest = {
                "schema": 2,
                "geometry": "room", "design_id": "design", "variant": "design",
                "description": "fixture", "source": {"artifacts": {}, "measurements": {}},
                "analysis": {"path": "analysis/design.json", "sha256": analysis_hash},
                "runtime": {"rates": {"48000": {
                    "config": str(template_relative), "config_sha256": template_hash,
                    "format": "FLOAT64_LE", "attenuation_db": 1.0,
                    "channels": {
                        "left": {"path": "48000/@design/L.raw", "sha256": left_hash},
                        "right": {"path": "48000/@design/R.raw", "sha256": right_hash},
                    },
                }}}, "verification": {"status": "verified"},
            }
            manifest["bundle_id"] = HELPER.canonical_hash(HELPER.bundle_identity(manifest))
            manifest_path = geometry_root / "provenance/design.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(manifest) + "\n")
            site.mkdir()
            with mock.patch.object(HELPER, "allowed_site_root", return_value=site):
                HELPER.filter_publish(str(staged), str(site))
            self.assertEqual((site / template_relative).read_text(), template)
            rendered = site / "configs/room/brutefir-48000@design.conf"
            self.assertNotIn("@REPO_DIR@", rendered.read_text())
            self.assertIn(str(site / "filters/room/48000/@design/L.raw"),
                          rendered.read_text())
            self.assertEqual(
                (site / "filters/room/provenance/design.json").read_text(),
                manifest_path.read_text())

class RoutesTest(unittest.TestCase):
    def setUp(self):
        self.client = APP.app.test_client()

    def test_page_renders_instructions_and_csrf_token(self):
        response = self.client.get("/configuration")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Export all measurements as text", response.data)
        self.assertIn(b"FLX-trimmed-48k.wav", response.data)
        self.assertIn(b"Publication authority", response.data)
        self.assertIn(b"[configuration] design_root", response.data)

    def test_form_data_is_captured_before_inputs_are_disabled(self):
        text = (SRC / "templates/configuration.html").read_text()
        handler = text[text.index("q('install-form').onsubmit"):]
        self.assertLess(handler.index("new FormData(e.target)"),
                        handler.index("freeze(true)"))

    def test_browser_enforces_and_auto_pairs_matching_session(self):
        text = (SRC / "templates/configuration.html").read_text()
        self.assertIn("actual.toLowerCase()!==expected.toLowerCase()", text)
        self.assertIn("if(sibling)setMdatFile(sibling)", text)
        self.assertIn("data.append('mdat',selectedMdat,selectedMdat.name)", text)
        self.assertIn("data.set('project_folder',projectFolder)", text)

    def test_mutation_requires_token(self):
        response = self.client.delete("/configuration/api/filters/room/design")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()


class JobPayloadCleanupTest(unittest.TestCase):
    """Finished jobs must not keep their bulk working files.

    A filter install writes the browser's upload (a REW .mdat plus every
    exported TXT/WAV) and a staged site copy under the job directory.  Nothing
    read them again, and nothing deleted them: five installs had accumulated
    901 MB in ~/.local/state/omdrc/configuration and filled the disk.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="jobclean."))
        self.manager = configuration.ConfigurationManager(
            configuration.Settings(state_root=self.root), dict)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _payload(self, job):
        (job.directory / "upload").mkdir(parents=True, exist_ok=True)
        (job.directory / "upload" / "session.mdat").write_bytes(b"x" * 4096)
        (job.directory / "staged-site").mkdir(parents=True, exist_ok=True)
        (job.directory / "staged-site" / "L.raw").write_bytes(b"y" * 4096)

    def test_a_succeeded_job_keeps_its_log_and_drops_its_payload(self):
        job = self.manager.start("filter-install", self._payload)
        for _ in range(200):
            if job.phase in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        self.assertEqual(job.phase, "succeeded", job.error)
        self.assertTrue((job.directory / "job.json").is_file())
        self.assertFalse((job.directory / "upload").exists())
        self.assertFalse((job.directory / "staged-site").exists())

    def test_a_failed_job_also_drops_its_payload(self):
        def boom(job):
            self._payload(job)
            raise RuntimeError("nope")

        job = self.manager.start("filter-install", boom)
        for _ in range(200):
            if job.phase in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        self.assertEqual(job.phase, "failed")
        self.assertTrue((job.directory / "job.json").is_file())
        self.assertFalse((job.directory / "upload").exists())

    def test_startup_sweeps_payloads_orphaned_by_an_earlier_run(self):
        """Jobs live in memory, so a panel restart orphans every directory."""
        stale = self.root / "oldjob"
        (stale / "upload").mkdir(parents=True)
        (stale / "upload" / "big.wav").write_bytes(b"z" * 8192)
        (stale / "job.json").write_text(json.dumps({"phase": "succeeded"}))
        running = self.root / "livejob"
        (running / "upload").mkdir(parents=True)
        (running / "upload" / "big.wav").write_bytes(b"z" * 8192)
        (running / "job.json").write_text(json.dumps({"phase": "running"}))

        configuration.ConfigurationManager(
            configuration.Settings(state_root=self.root), dict)

        self.assertFalse((stale / "upload").exists())
        self.assertTrue((stale / "job.json").is_file())
        # Not in a terminal state: never guess, leave it alone.
        self.assertTrue((running / "upload").exists())

    def test_job_history_is_capped_at_retain_jobs(self):
        """Even shed of their payload, one directory per job accumulates
        forever on a box that never restarts; the newest `retain_jobs` are
        kept and older logs are reclaimed as each job finishes."""
        manager = configuration.ConfigurationManager(
            configuration.Settings(state_root=self.root, retain_jobs=3), dict)
        directories = []
        for _ in range(6):
            job = manager.start("filter-install", self._payload)
            for _ in range(200):
                if job.phase in {"succeeded", "failed"}:
                    break
                time.sleep(0.05)
            self.assertEqual(job.phase, "succeeded", job.error)
            directories.append(job.directory)
        surviving = [d for d in self.root.iterdir() if (d / "job.json").is_file()]
        self.assertEqual(len(surviving), 3)
        # The three most recent jobs are the ones kept.
        self.assertEqual(sorted(d.name for d in surviving),
                         sorted(d.name for d in directories[-3:]))

    def test_history_is_capped_and_keeps_the_newest(self):
        """discard_payload sheds the bulk; this stops one small directory per
        job accumulating forever on a long-lived box."""
        self.manager.settings.retain_jobs = 3
        for n in range(6):
            d = self.root / f"job{n}"
            d.mkdir()
            (d / "job.json").write_text(json.dumps({"phase": "succeeded"}))
            os.utime(d / "job.json", (1000 + n, 1000 + n))
        self.manager.prune_job_history()
        left = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(left, ["job3", "job4", "job5"])

    def test_a_running_job_is_never_pruned(self):
        self.manager.settings.retain_jobs = 1
        for n in range(3):
            d = self.root / f"job{n}"
            d.mkdir()
            (d / "job.json").write_text(json.dumps({"phase": "succeeded"}))
            os.utime(d / "job.json", (1000 + n, 1000 + n))
        live = self.root / "live"
        live.mkdir()
        (live / "job.json").write_text(json.dumps({"phase": "running"}))
        os.utime(live / "job.json", (1, 1))          # oldest by mtime
        self.manager.prune_job_history()
        self.assertTrue(live.is_dir())
