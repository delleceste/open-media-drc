#!/usr/bin/env python3

from contextlib import redirect_stdout
import importlib.util
import io
import os
import numpy as np
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "new_filter_design", ROOT / "scripts/new_filter_design.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)
import deploy_filter
import headroom_calc
import filter_design_suggest
import filter_workflow_next

DECLARE_SPEC = importlib.util.spec_from_file_location(
    "declare_filter_design", ROOT / "scripts/declare_filter_design.py")
DECLARE = importlib.util.module_from_spec(DECLARE_SPEC)
assert DECLARE_SPEC.loader
DECLARE_SPEC.loader.exec_module(DECLARE)


class HeadroomCalculationTest(unittest.TestCase):
    def test_existing_filter_attenuation_counts_towards_margin(self):
        cases = (
            (3.0, 1.0, 4.0),
            (1.2, 1.0, 2.2),
            (0.0, 1.0, 1.0),
            (-0.4, 1.0, 0.6),
            (-0.9, 1.0, 0.1),
            (-1.0, 1.0, 0.0),
            (-3.0, 1.0, 0.0),
            (0.0, 0.0, 0.0),
        )
        for peak_db, margin_db, expected in cases:
            with self.subTest(peak_db=peak_db, margin_db=margin_db):
                self.assertEqual(
                    deploy_filter.required_attenuation(peak_db, margin_db), expected)
                self.assertEqual(
                    headroom_calc.suggested_attenuation(peak_db, margin_db), expected)


class FilterAlignmentTest(unittest.TestCase):
    def test_fixed_delay_and_gain_are_detected_without_filename_assumptions(self):
        rate = 48000
        sample_count = 4096
        delay = 1000
        gain_db = -3.0
        impulse = np.zeros(sample_count, dtype=np.float64)
        impulse[delay] = 10.0 ** (gain_db / 20.0)
        frequencies = np.fft.rfftfreq(sample_count, 1.0 / rate)
        audible = (frequencies >= 100.0) & (frequencies <= 20_000.0)
        result = deploy_filter.detect_filter_alignment(
            frequencies[audible],
            np.zeros(np.count_nonzero(audible)),
            np.zeros(np.count_nonzero(audible)),
            rate,
            impulse,
        )
        self.assertEqual(result["delay_samples"], delay)
        self.assertAlmostEqual(result["txt_to_wav_gain_db"], gain_db, places=6)
        self.assertLess(result["metrics"]["rms_magnitude_db"], 1e-6)
        self.assertLess(result["metrics"]["rms_phase_deg"], 1e-6)

    def test_runtime_progress_shows_sox_headroom_and_config_bake(self):
        class TtyBuffer(io.StringIO):
            def isatty(self):
                return True

        recipe = {
            "geometry": "120.blue",
            "filter": {"sample_rate": 48000},
            "runtime": {
                "safety_margin_db": 1.0,
                "selector": "@test",
                "generate_configs": True,
                "attenuation_db": "auto",
                "format": "FLOAT64_LE",
                "rates": {
                    "96000": "configs/120.blue/brutefir-96000@test.conf.in",
                },
            },
        }

        def fake_rew2raw(arguments, cwd=None):
            del cwd
            np.array([1.0, 0.0], dtype="<f8").tofile(Path(arguments[4]))
            return ""

        with tempfile.TemporaryDirectory(prefix="omdrc-runtime-progress-") as name:
            output = TtyBuffer()
            with (mock.patch.object(deploy_filter, "run", side_effect=fake_rew2raw),
                  mock.patch.dict(os.environ, {"TERM": "xterm"}, clear=True),
                  redirect_stdout(output)):
                deploy_filter.generate_runtime(
                    recipe,
                    {
                        "filter_left_wav": Path("FLX-trimmed-48k.wav"),
                        "filter_right_wav": Path("FRX-trimmed-48k.wav"),
                    },
                    Path(name),
                )
        rendered = output.getvalue()
        self.assertIn("\x1b[1;35m[SoX 1/2]", rendered)
        self.assertIn(
            "FIR scale 48000/96000 = 0.500000000; "
            "applied gain -6.020600 dB",
            rendered,
        )
        self.assertIn("\x1b[1;33m[HEADROOM 96,000 Hz]", rendered)
        self.assertIn("\x1b[1;34mCONFIG", rendered)
        self.assertIn("baked attenuation: 1.0 dB", rendered)
        self.assertIn("config read-back verified 1.0 dB", rendered)


class SourceReleaseTest(unittest.TestCase):
    def git(self, root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=root, check=True, text=True,
            capture_output=True,
        )
        return result.stdout.strip()

    def test_annotated_tag_is_required_and_object_is_pinned(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-source-tag-") as name:
            root = Path(name)
            self.git(root, "init", "-q")
            self.git(root, "config", "user.name", "Filter Test")
            self.git(root, "config", "user.email", "filter-test@example.invalid")
            (root / "input.txt").write_text("filter source\n", encoding="utf-8")
            self.git(root, "add", "input.txt")
            self.git(root, "commit", "-q", "-m", "source")

            with self.assertRaises(MODULE.AuditError):
                MODULE.resolve_release(root, "HEAD", allow_commit=False)

            self.git(root, "tag", "lightweight")
            with self.assertRaises(MODULE.AuditError):
                MODULE.resolve_release(root, "lightweight", allow_commit=False)

            self.git(root, "tag", "-a", "120.blue-test", "-m", "audited filter design")
            release = MODULE.resolve_release(root, "120.blue-test", allow_commit=False)
            self.assertEqual(release["kind"], "annotated_tag")
            self.assertEqual(release["name"], "120.blue-test")
            self.assertEqual(release["commit"], self.git(root, "rev-parse", "HEAD"))
            self.assertEqual(
                release["tag_object"],
                self.git(root, "rev-parse", "refs/tags/120.blue-test^{tag}"),
            )

    def test_versioned_config_uses_isolated_runtime_paths(self):
        config = deploy_filter.render_config(
            "120.blue", 96000, "@2026-08-target-a", 2.3, "FLOAT64_LE")
        self.assertIn("sampling_rate: 96000", config)
        self.assertIn(
            "/filters/120.blue/96000/@2026-08-target-a/L.raw", config)
        self.assertIn(
            "/filters/120.blue/96000/@2026-08-target-a/R.raw", config)
        self.assertEqual(config.count("attenuation: 2.3"), 2)


class CommandHelpTest(unittest.TestCase):
    def test_runtime_handoff_includes_install_selector_and_ui_identity(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-next-") as name:
            root = Path(name)
            (root / "build").mkdir()
            (root / "build/CMakeCache.txt").write_text(
                "CMAKE_INSTALL_PREFIX:PATH=/opt/omdrc\n"
                "GEOMETRY:STRING=flat\nGEOMETRIES:STRING=185\n",
                encoding="utf-8",
            )
            stream = io.StringIO()
            previous = filter_workflow_next.CONSOLE
            filter_workflow_next.CONSOLE = filter_workflow_next.Console(
                stream, color=False)
            bundle = {
                "geometry": "120.blue",
                "design_id": "test-design",
                "bundle_id": "abc123",
                "manifest": Path("filters/120.blue/provenance/test-design.json"),
                "release": {"kind": "annotated_tag", "name": "120.blue-test-design"},
                "source_commit": "deadbeef",
            }
            try:
                with mock.patch.object(
                        filter_workflow_next, "_scoped_status", return_value=""), \
                     mock.patch.object(
                         filter_workflow_next.platform, "system", return_value="Linux"):
                    filter_workflow_next.print_deployed_next(
                        root, [bundle], include_verification=True)
            finally:
                filter_workflow_next.CONSOLE = previous
            output = stream.getvalue()
            self.assertIn("verify_filter_bundle.py", output)
            self.assertIn("--no-next", output)
            self.assertIn("-DGEOMETRIES=185;120.blue", output)
            self.assertIn("sudo cmake --install build", output)
            self.assertIn("/opt/omdrc/bin/omdrc design @test-design", output)
            self.assertIn("tag 120.blue-test-design", output)
            self.assertIn("source commit deadbeef", output)
            self.assertIn("bundle abc123", output)
            self.assertIn("green verified identity", output)

    def test_runtime_handoff_uses_base_selector_for_default_design(self):
        stream = io.StringIO()
        previous = filter_workflow_next.CONSOLE
        filter_workflow_next.CONSOLE = filter_workflow_next.Console(
            stream, color=False)
        bundle = {
            "geometry": "120.blue",
            "design_id": "default",
            "bundle_id": "abc123",
            "manifest": Path("filters/120.blue/provenance/default.json"),
            "release": {},
            "source_commit": "deadbeef",
        }
        try:
            with mock.patch.object(
                    filter_workflow_next, "_scoped_status", return_value=""):
                filter_workflow_next.print_deployed_next(
                    ROOT, [bundle], include_verification=False)
        finally:
            filter_workflow_next.CONSOLE = previous
        output = stream.getvalue()
        self.assertIn("/usr/local/bin/omdrc design default", output)
        self.assertNotIn("design @default", output)

    def test_dry_run_handoff_gives_ready_write_command(self):
        stream = io.StringIO()
        previous = filter_workflow_next.CONSOLE
        filter_workflow_next.CONSOLE = filter_workflow_next.Console(
            stream, color=False)
        try:
            filter_workflow_next.print_dry_run_next(
                ROOT, ["python3", "scripts/new_filter_design.py", "--write"])
        finally:
            filter_workflow_next.CONSOLE = previous
        output = stream.getvalue()
        self.assertIn("Run from:", output)
        self.assertIn("python3 scripts/new_filter_design.py --write", output)
        self.assertIn("remaining verification", output)

    def test_verifier_help_documents_handoff_suppression(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/verify_filter_bundle.py"),
             "--help"],
            check=True, text=True, capture_output=True,
        )
        self.assertIn("--no-next", result.stdout)
        self.assertIn("CMake/CI/internal", result.stdout)
        self.assertIn("invocation", result.stdout)

    def test_declaration_console_colors_only_when_enabled(self):
        colored_stream = io.StringIO()
        colored = DECLARE.Console(colored_stream, color=True)
        colored.stage(1, 8, "Hash inputs")
        colored.directory(Path("/source/repository"))
        self.assertIn("\x1b[", colored_stream.getvalue())
        self.assertIn("Run from:", colored_stream.getvalue())

        plain_stream = io.StringIO()
        plain = DECLARE.Console(plain_stream, color=False)
        plain.stage(1, 8, "Hash inputs")
        plain.directory(Path("/source/repository"))
        self.assertNotIn("\x1b[", plain_stream.getvalue())

    def test_suggestion_prefers_pair_matching_aggregate_grid(self):
        bad = (10.0, {"frequencies": np.array([1.0, 2.0])},
               {"frequencies": np.array([1.0, 2.0])})
        good = (5.0, {"frequencies": np.array([1.0, 2.0, 3.0])},
                {"frequencies": np.array([1.0, 2.0, 3.0])})
        aggregate = {
            "frequencies": np.array([1.0, 2.0, 3.0]),
            "path": Path("LR.orig.txt"),
        }
        ordered = filter_design_suggest.prioritize_aggregate_grid(
            [bad, good], aggregate)
        self.assertIs(ordered[0], good)

    def test_next_step_cmake_keeps_existing_geometry_sets(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-cmake-cache-") as name:
            root = Path(name)
            (root / "build").mkdir()
            (root / "build/CMakeCache.txt").write_text(
                "CMAKE_INSTALL_PREFIX:PATH=/opt/omdrc\n"
                "GEOMETRY:STRING=flat\nGEOMETRIES:STRING=185\n",
                encoding="utf-8",
            )
            command = DECLARE._cmake_configure_command(root, "120.blue")
            self.assertIn("-DGEOMETRIES=185;120.blue", command)
            self.assertEqual(DECLARE._installed_omdrc(root), "/opt/omdrc/bin/omdrc")

    def test_next_steps_include_open_media_publication_and_install(self):
        stream = io.StringIO()
        previous = DECLARE.CONSOLE
        DECLARE.CONSOLE = DECLARE.Console(stream, color=False)
        try:
            DECLARE.print_next_steps(
                Path("/source/DRC-120.blue"),
                Path("omdrc-designs/120.blue/test/design.json"),
                ["exports/L.txt", "exports/R.txt"],
                "120.blue", "test", wrote=True,
            )
        finally:
            DECLARE.CONSOLE = previous
        output = stream.getvalue()
        self.assertIn("Run from:", output)
        self.assertIn("scripts/new_filter_design.py", output)
        self.assertIn("scripts/verify_filter_bundle.py", output)
        self.assertIn("sudo cmake --install build", output)
        self.assertIn("design @test", output)

    def test_declaration_help_lists_every_required_role(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/declare_filter_design.py"),
             "--help"],
            check=True, text=True, capture_output=True,
        )
        self.assertIn("required role assignments:", result.stdout)
        for option in (
                "--source-root", "--geometry", "--design-id",
                "--description", "--measurement-left",
                "--measurement-right", "--measurement-sum",
                "--filter-left-txt", "--filter-right-txt",
                "--filter-left-wav", "--filter-right-wav",
                "--sum-mode"):
            self.assertIn(option, result.stdout)
        self.assertIn("--suggest-from-source-root", result.stdout)

    def test_low_level_builder_has_no_hidden_default_recipe(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/deploy_filter.py")],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--recipe", result.stderr)
        self.assertIn("--source-root", result.stderr)

    def test_newest_mdat_uses_modification_time_only_as_naming_hint(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-suggest-") as name:
            root = Path(name)
            older = root / "older.mdat"
            newer = root / "newer.mdat"
            older.write_bytes(b"not parsed")
            newer.write_bytes(b"also not parsed")
            os.utime(older, ns=(1_000_000_000, 1_000_000_000))
            os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
            self.assertEqual(filter_design_suggest.newest_mdat(root), newer)


if __name__ == "__main__":
    unittest.main()
