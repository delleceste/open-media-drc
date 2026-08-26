#!/usr/bin/env python3
"""The dir-driven design command: naming rules, verbatim traces, runtime audit."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
import numpy as np
from pathlib import Path
import struct
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
import filter_workflow_next


def write_rew_txt(path: Path, frequencies, magnitudes, phases, *,
                  measurement="M", smoothing="None", source="Log Swept Sine",
                  fmt="512k Log Swept Sine, 1 sweep using an acoustic timing reference"):
    lines = [
        "* Measurement data measured by REW V5.40",
        f"* Source: {source}",
        f"* Format: {fmt}",
        "* Dated: Aug 10, 2026, 4:21:50 PM",
        "* Note: ; fixture",
        f"* Measurement: {measurement}",
        f"* Smoothing: {smoothing}",
        "* Frequency Step: 1.0 Hz",
        f"* Start Frequency: {frequencies[0]} Hz",
        "*",
        "* Freq(Hz) SPL(dB) Phase(degrees)",
    ]
    lines += [f"{f:.6f} {m:.3f} {p:.4f}"
              for f, m, p in zip(frequencies, magnitudes, phases)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_float_wav(path: Path, rate: int, samples: np.ndarray) -> None:
    """Mono IEEE-float WAV, the shape REW writes and SoX decodes."""
    payload = samples.astype("<f4").tobytes()
    header = b"".join((
        b"RIFF", struct.pack("<I", 36 + len(payload)), b"WAVEfmt ",
        struct.pack("<IHHIIHH", 16, 3, 1, rate, rate * 4, 4, 32),
        b"data", struct.pack("<I", len(payload)),
    ))
    path.write_bytes(header + payload)


def build_design_dir(root: Path, *, aggregate="LR", corrected="filtered",
                     rate=48000, delay=64, mdat=True, commit=False) -> Path:
    """A complete design directory in the layout REW and the projects use.

    ``DRC-<geometry>/<geometry>.<session>.txts`` beside
    ``<geometry>.<session>.mdat`` — the same shape a real measurement project
    has, so the naming, the session lookup and the identity rules are all
    exercised against it.
    """
    directory = root / "DRC-120.blue" / "120.blue.fixture-design.txts"
    directory.mkdir(parents=True)
    if mdat:
        (directory.parent / "120.blue.fixture-design.mdat").write_bytes(
            b"REW measurement session fixture")
    size = 2048
    impulse = np.zeros(size, dtype=np.float64)
    impulse[delay] = 10.0 ** (-3.0 / 20.0)
    frequencies = np.fft.rfftfreq(size, 1.0 / rate)
    band = (frequencies >= 100.0) & (frequencies <= 20_000.0)
    frequencies = frequencies[band]
    zeros = np.zeros(frequencies.size)

    names = {
        "L.txt": "L", "R.txt": "R",
        f"{aggregate}.txt": aggregate,
        "L.Filtered.txt": "L.Filtered", "R.Filtered.txt": "R.Filtered",
        f"{aggregate}.{corrected}.txt": f"{aggregate}.{corrected}",
    }
    for index, (name, measurement) in enumerate(names.items()):
        write_rew_txt(directory / name, frequencies,
                      zeros + index, zeros + index, measurement=measurement)
    # The filter TXTs must be the exported response of the WAVs: flat 0 dB /
    # 0 deg once the causal delay and the -3 dB export gain are removed.
    for name in ("FLX-trimmed.txt", "FRX-trimmed.txt"):
        write_rew_txt(directory / name, frequencies, zeros, zeros,
                      measurement=name[:3])
    for name in (f"FLX-trimmed-{rate // 1000}k.wav", f"FRX-trimmed-{rate // 1000}k.wav"):
        write_float_wav(directory / name, rate, impulse)
    if commit:
        git_init(directory.parent)
    return directory


def git_init(repo: Path) -> None:
    """A committed project, the state a design has to be in to be deployed."""
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    for arguments in (["init", "-q", "-b", "main"], ["add", "-A"],
                      ["commit", "-q", "-m", "measurements"]):
        subprocess.run(["git", "-C", str(repo), *arguments],
                       check=True, env=environment, capture_output=True)


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


class DesignIdentityTest(unittest.TestCase):
    def test_nested_geometry_and_design_directories(self):
        self.assertEqual(
            MODULE.design_identity(Path("/src/120.blue/rscreen-20260812")),
            ("120.blue", "rscreen-20260812"))

    def test_single_directory_named_geometry_at_design(self):
        self.assertEqual(
            MODULE.design_identity(Path("/src/120.blue@rscreen-20260812")),
            ("120.blue", "rscreen-20260812"))

    def test_rew_export_directory_beside_its_mdat(self):
        """DRC-<geometry>/<geometry>.<session>.txts is what REW actually leaves."""
        self.assertEqual(
            MODULE.design_identity(Path("/src/DRC-120.blue/120.blue.Rscreen.txts")),
            ("120.blue", "Rscreen"))
        self.assertEqual(
            MODULE.design_identity(Path("/src/DRC-185/185-screens.txts")),
            ("185", "screens"))

    def test_explicit_names_override_the_path(self):
        self.assertEqual(
            MODULE.design_identity(
                Path("/src/DRC-120.blue/120.blue.Rscreen.txts"),
                "120.blue", "rscreen-20260812"),
            ("120.blue", "rscreen-20260812"))

    def test_reserved_and_malformed_identities_are_refused(self):
        for path in (Path("/src/120.blue/default"), Path("/src/120.blue@"),
                     Path("/src/@design"), Path("/src/geo/-bad")):
            with self.subTest(path=path):
                with self.assertRaises(deploy_filter.AuditError):
                    MODULE.design_identity(path)


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="omdrc-discover-")
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_complete_directory_maps_every_role(self):
        directory = build_design_dir(self.root)
        paths, aggregate, rate, _ = MODULE.discover(directory)
        self.assertEqual(set(paths), set(deploy_filter.ARTIFACT_ROLES))
        self.assertEqual(aggregate, {"style": "LR", "corrected": "filtered"})
        self.assertEqual(rate, 48000)
        self.assertEqual(paths["original_sum"].name, "LR.txt")
        self.assertEqual(paths["corrected_sum"].name, "LR.filtered.txt")

    def test_case_and_txt_suffix_are_both_optional(self):
        directory = build_design_dir(self.root)
        (directory / "L.Filtered.txt").rename(directory / "l.FILTERED")
        paths, _, _, _ = MODULE.discover(directory)
        self.assertEqual(paths["corrected_left"].name, "l.FILTERED")

    def test_sum_style_switches_labels_without_any_flag(self):
        directory = build_design_dir(self.root, aggregate="L+R")
        _, aggregate, _, _ = MODULE.discover(directory)
        self.assertEqual(aggregate["style"], "L+R")
        self.assertEqual(
            deploy_filter.aggregate_labels(aggregate),
            ("Original L+R", "Corrected L+R"))

    def test_remeasured_aggregate_replaces_the_filtered_one(self):
        """A room measured again is not the same claim as a filtered result."""
        directory = build_design_dir(
            self.root, aggregate="L+R", corrected="remeasured")
        paths, aggregate, _, notes = MODULE.discover(directory)
        self.assertEqual(paths["corrected_sum"].name, "L+R.remeasured.txt")
        self.assertEqual(aggregate["corrected"], "remeasured")
        self.assertEqual(notes, [])
        self.assertEqual(
            deploy_filter.aggregate_labels(aggregate)[1],
            "Corrected L+R (re-measured in the room)")

    def test_legacy_measured_name_is_accepted_with_a_warning(self):
        directory = build_design_dir(
            self.root, aggregate="L+R", corrected="measured")
        paths, aggregate, _, notes = MODULE.discover(directory)
        self.assertEqual(paths["corrected_sum"].name, "L+R.measured.txt")
        self.assertEqual(aggregate["corrected"], "remeasured")
        self.assertTrue(any("L+R.remeasured" in note for note in notes))

    def test_a_vector_average_has_no_remeasured_form(self):
        """Both speakers playing is a sum; it can never be a vector average."""
        directory = build_design_dir(self.root)
        (directory / "LR.filtered.txt").rename(directory / "L+R.remeasured.txt")
        with self.assertRaisesRegex(deploy_filter.AuditError, "does not belong with"):
            MODULE.discover(directory)

    def test_filtered_and_remeasured_aggregates_together_are_refused(self):
        directory = build_design_dir(self.root, aggregate="L+R")
        (directory / "L+R.remeasured.txt").write_text(
            (directory / "L+R.filtered.txt").read_text())
        with self.assertRaisesRegex(deploy_filter.AuditError, "more than one file"):
            MODULE.discover(directory)

    def test_both_aggregate_styles_present_is_an_error(self):
        directory = build_design_dir(self.root)
        (directory / "L+R.txt").write_text((directory / "LR.txt").read_text())
        with self.assertRaisesRegex(deploy_filter.AuditError, "both LR.txt and"):
            MODULE.discover(directory)

    def test_corrected_aggregate_must_match_the_original_style(self):
        directory = build_design_dir(self.root)
        (directory / "LR.filtered.txt").rename(directory / "L+R.filtered.txt")
        with self.assertRaisesRegex(deploy_filter.AuditError, "does not belong with"):
            MODULE.discover(directory)

    def test_missing_role_names_what_was_expected(self):
        directory = build_design_dir(self.root)
        (directory / "R.Filtered.txt").unlink()
        with self.assertRaisesRegex(deploy_filter.AuditError, "R.filtered.txt"):
            MODULE.discover(directory)

    def test_ambiguous_spellings_are_refused(self):
        directory = build_design_dir(self.root)
        (directory / "l.filtered").write_text(
            (directory / "L.Filtered.txt").read_text())
        with self.assertRaisesRegex(deploy_filter.AuditError, "more than one file"):
            MODULE.discover(directory)

    def test_impulse_wav_is_found_by_its_rate_suffix(self):
        directory = build_design_dir(self.root, rate=96000)
        paths, _, rate, _ = MODULE.discover(directory)
        self.assertEqual(rate, 96000)
        self.assertEqual(paths["filter_left_wav"].name, "FLX-trimmed-96k.wav")

    def test_two_candidate_wavs_are_refused(self):
        directory = build_design_dir(self.root)
        (directory / "FLX-trimmed-96k.wav").write_bytes(
            (directory / "FLX-trimmed-48k.wav").read_bytes())
        with self.assertRaisesRegex(deploy_filter.AuditError, "more than one file"):
            MODULE.discover(directory)


class MeasurementSessionTest(unittest.TestCase):
    """The .mdat the exports came from is part of the bundle's identity."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="omdrc-mdat-")
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def test_sibling_mdat_matching_the_export_directory_is_found(self):
        directory = build_design_dir(self.root)
        self.assertEqual(
            MODULE.find_mdat(directory, None).name, "120.blue.fixture-design.mdat")

    def test_an_mdat_inside_the_directory_wins(self):
        directory = build_design_dir(self.root)
        (directory / "session.mdat").write_bytes(b"inside")
        self.assertEqual(MODULE.find_mdat(directory, None).name, "session.mdat")

    def test_two_mdats_inside_must_be_disambiguated(self):
        directory = build_design_dir(self.root)
        (directory / "a.mdat").write_bytes(b"a")
        (directory / "b.mdat").write_bytes(b"b")
        with self.assertRaisesRegex(deploy_filter.AuditError, "--mdat"):
            MODULE.find_mdat(directory, None)

    def test_a_design_without_a_session_is_refused(self):
        directory = build_design_dir(self.root, mdat=False)
        with self.assertRaisesRegex(deploy_filter.AuditError, "cannot tell which"):
            MODULE.find_mdat(directory, None)

    def test_the_session_record_carries_hash_and_git_blob(self):
        directory = build_design_dir(self.root, commit=True)
        paths, _, _, _ = MODULE.discover(directory)
        mdat = MODULE.find_mdat(directory, None)
        console = MODULE.Console(io.StringIO(), color=False)
        project, blobs = MODULE.source_provenance(
            console, directory, paths, mdat, allow_uncommitted=False)
        record = MODULE.measurement_record(mdat, project, blobs)
        self.assertEqual(record["file"], "120.blue.fixture-design.mdat")
        self.assertEqual(record["path"], "120.blue.fixture-design.mdat")
        self.assertEqual(record["sha256"], deploy_filter.sha256_file(mdat))
        self.assertRegex(record["git_blob"], r"^[0-9a-f]{40}$")


class SourceProvenanceTest(unittest.TestCase):
    """A deployed bundle has to name the project and commit it came from."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="omdrc-provenance-")
        self.root = Path(self._temp.name)
        self.console = MODULE.Console(io.StringIO(), color=False)
        self.addCleanup(self._temp.cleanup)

    def resolve(self, directory, **kwargs):
        paths, _, _, _ = MODULE.discover(directory)
        return MODULE.source_provenance(
            self.console, directory, paths, MODULE.find_mdat(directory, None),
            **kwargs)

    def test_a_committed_project_is_named_by_its_commit(self):
        directory = build_design_dir(self.root, commit=True)
        project, blobs = self.resolve(directory, allow_uncommitted=False)
        self.assertTrue(project["clean"])
        self.assertEqual(project["uncommitted"], [])
        self.assertRegex(project["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(project["name"], "DRC-120.blue")
        self.assertEqual(project["path"], "120.blue.fixture-design.txts")
        self.assertEqual(len(blobs), len(deploy_filter.ARTIFACT_ROLES) + 1)

    def test_an_uncommitted_export_stops_the_deployment(self):
        directory = build_design_dir(self.root, commit=True)
        (directory / "L.txt").write_text("* edited after the commit\n")
        with self.assertRaises(deploy_filter.AuditError) as caught:
            self.resolve(directory, allow_uncommitted=False)
        message = str(caught.exception)
        self.assertIn("not committed", message)
        self.assertIn("git -C", message)

    def test_an_untracked_export_stops_the_deployment(self):
        directory = build_design_dir(self.root, commit=True)
        (directory / "L.txt").unlink()
        (directory / "L").write_text("* never added\n")
        with self.assertRaisesRegex(deploy_filter.AuditError, "not committed"):
            self.resolve(directory, allow_uncommitted=False)

    def test_the_override_records_the_gap_instead_of_hiding_it(self):
        directory = build_design_dir(self.root, commit=True)
        (directory / "R.txt").write_text("* edited after the commit\n")
        project, _ = self.resolve(directory, allow_uncommitted=True)
        self.assertFalse(project["clean"])
        self.assertIn("120.blue.fixture-design.txts/R.txt", project["uncommitted"])

    def test_a_project_without_git_is_refused_by_default(self):
        directory = build_design_dir(self.root)
        with self.assertRaisesRegex(
                deploy_filter.AuditError, "not inside a Git repository"):
            self.resolve(directory, allow_uncommitted=False)
        project, blobs = self.resolve(directory, allow_uncommitted=True)
        self.assertEqual(project["commit"], "")
        self.assertEqual(blobs, {})


class RoomHistoryTest(unittest.TestCase):
    """The room repository is the history: one commit per deployment."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="omdrc-room-")
        self.room = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.manifest = {
            "geometry": "120.blue", "variant": "d1", "design_id": "d1",
            "bundle_id": "f" * 64,
            "aggregate": {"style": "L+R", "corrected": "remeasured"},
            "runtime": {"rates": {"48000": {}, "96000": {}}},
            "source": {
                "project": {"name": "DRC-120.blue", "commit": "a" * 40,
                            "clean": True, "path": "120.blue.fixture.txts",
                            "remote": ""},
                "measurements": {"file": "s.mdat", "sha256": "b" * 64,
                                 "git_blob": "c" * 40},
            },
        }

    def test_no_repository_is_reported_not_fabricated(self):
        result = deploy_filter.commit_site(self.room, self.manifest)
        self.assertEqual(result["status"], "no-repo")

    def test_a_deployment_becomes_one_retrievable_commit(self):
        (self.room / "filters/120.blue").mkdir(parents=True)
        (self.room / "configs/120.blue").mkdir(parents=True)
        (self.room / "filters/120.blue/L.raw").write_bytes(b"\x00" * 8)
        git_init(self.room)
        (self.room / "configs/120.blue/brutefir-48000@d1.conf.in").write_text("x\n")
        result = deploy_filter.commit_site(self.room, self.manifest)
        self.assertEqual(result["status"], "committed")
        body = subprocess.run(
            ["git", "-C", str(self.room), "log", "-1", "--format=%B"],
            check=True, text=True, capture_output=True).stdout
        self.assertIn("Deploy 120.blue @d1", body)
        self.assertIn("f" * 64, body)
        self.assertIn("DRC-120.blue @ " + "a" * 12, body)
        self.assertIn("s.mdat", body)
        self.assertIn("48000 96000", body)

    def test_redeploying_identical_files_makes_no_empty_commit(self):
        (self.room / "filters/120.blue").mkdir(parents=True)
        (self.room / "filters/120.blue/L.raw").write_bytes(b"\x00" * 8)
        git_init(self.room)
        self.assertEqual(
            deploy_filter.commit_site(self.room, self.manifest)["status"],
            "unchanged")


class AnalysisTest(unittest.TestCase):
    """Every trace must be one export, carried through untouched."""

    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="omdrc-analysis-")
        cls.directory = build_design_dir(Path(cls._temp.name))
        paths, aggregate, named_rate, _ = MODULE.discover(cls.directory)
        facts = MODULE.inspect(paths, named_rate)
        cls.paths, cls.facts = paths, facts
        recipe = {
            "geometry": "120.blue", "variant": "fixture-design",
            "design_id": "fixture-design", "description": "fixture",
            "aggregate": aggregate,
            "source": {"directory": str(cls.directory), "artifacts": {
                role: {"path": paths[role].name,
                       "bundle_path": f"source/fixture-design/{paths[role].name}",
                       "sha256": deploy_filter.sha256_file(paths[role])}
                for role in deploy_filter.ARTIFACT_ROLES}},
            "filter": {
                "sample_rate": facts["sample_rate"],
                "delay_samples": facts["delay_samples"],
                "txt_to_wav_gain_db": {
                    channel: round(float(item["txt_to_wav_gain_db"]), 6)
                    for channel, item in facts["alignments"].items()},
                "txt_wav_limits": deploy_filter.DEFAULT_LIMITS,
            },
        }
        cls.analysis, _ = deploy_filter.build_analysis(recipe, paths)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_eight_traces_each_naming_its_export(self):
        self.assertEqual(self.analysis["schema"], 2)
        self.assertEqual(len(self.analysis["traces"]), 8)
        expected = {self.paths[role].name for role in deploy_filter.TRACE_ROLES}
        self.assertEqual({t["source_file"] for t in self.analysis["traces"]}, expected)

    def test_trace_values_equal_the_exported_columns(self):
        for trace in self.analysis["traces"]:
            with self.subTest(trace=trace["id"]):
                rows = [line.split() for line
                        in (self.directory / trace["source_file"])
                        .read_text(encoding="utf-8").splitlines()
                        if line and not line.startswith("*")]
                grid = self.analysis["frequency_grids"][trace["grid"]]
                self.assertEqual(len(rows), len(grid))
                for index, (frequency, magnitude, phase) in enumerate(rows):
                    self.assertEqual(grid[index], float(frequency))
                    self.assertEqual(trace["magnitude_db"][index], float(magnitude))
                    self.assertEqual(trace["phase_deg"][index], float(phase))

    def test_identical_grids_are_stored_once(self):
        self.assertEqual(len(self.analysis["frequency_grids"]), 1)

    def test_analysis_states_that_nothing_is_calculated(self):
        calculation = self.analysis["calculation"]
        self.assertEqual(calculation["smoothing_applied"], "none")
        self.assertEqual(calculation["exports_carrying_rew_smoothing"], [])
        self.assertNotIn("sum_mode", calculation)
        self.assertNotIn("formula", calculation)

    def test_the_analysis_records_the_two_export_guarantees(self):
        calculation = self.analysis["calculation"]
        self.assertEqual(calculation["exports_carrying_rew_smoothing"], [])
        self.assertEqual(calculation["measurement_rate_limit_hz"], 48000)
        self.assertLessEqual(calculation["highest_exported_frequency_hz"], 24000.0)

    def test_mismatched_filter_wav_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-bad-filter-") as name:
            directory = build_design_dir(Path(name))
            paths, _, rate, _ = MODULE.discover(directory)
            write_float_wav(paths["filter_left_wav"], 48000,
                            np.random.default_rng(1).normal(0, 0.01, 2048))
            with self.assertRaisesRegex(
                    deploy_filter.AuditError, "is not the exported response of"):
                MODULE.inspect(paths, rate)


class ExportQualityTest(unittest.TestCase):
    """Smoothed or too-wide exports are refused, not annotated."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="omdrc-exports-")
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)

    def parsed(self, directory):
        paths, _, _, _ = MODULE.discover(directory)
        return paths, {role: deploy_filter.parse_rew_txt(paths[role])
                       for role in deploy_filter.TRACE_ROLES}

    def rewrite(self, path, *, smoothing="None", top=None):
        headers, freqs, mags, phases = deploy_filter.parse_rew_txt(path)
        freqs = list(freqs)
        if top is not None:
            freqs[-1] = top
        write_rew_txt(path, freqs, mags, phases,
                      measurement=headers.get("measurement", "M"), smoothing=smoothing)

    def test_a_clean_set_has_no_defects(self):
        _, parsed = self.parsed(build_design_dir(self.root))
        self.assertEqual(deploy_filter.export_defects(parsed), [])

    def test_a_smoothed_export_stops_the_run(self):
        directory = build_design_dir(self.root)
        paths, _, rate, _ = MODULE.discover(directory)
        self.rewrite(paths["original_left"], smoothing="Psychoacoustic")
        with self.assertRaisesRegex(deploy_filter.AuditError, "Psychoacoustic"):
            MODULE.inspect(paths, rate)

    def test_an_export_that_states_no_smoothing_is_refused(self):
        directory = build_design_dir(self.root)
        paths, parsed = self.parsed(directory)
        self.rewrite(paths["corrected_sum"], smoothing="")
        _, parsed = self.parsed(directory)
        defects = deploy_filter.export_defects(parsed)
        self.assertEqual([role for role, _ in defects], ["corrected_sum"])
        self.assertIn("states no smoothing", defects[0][1])

    def test_a_measurement_above_48_khz_is_refused(self):
        directory = build_design_dir(self.root)
        paths, _ = self.parsed(directory)
        self.rewrite(paths["original_right"], top=30_000.0)
        _, parsed = self.parsed(directory)
        defects = deploy_filter.export_defects(parsed)
        self.assertEqual([role for role, _ in defects], ["original_right"])
        self.assertIn("60,000 Hz or more", defects[0][1])

    def test_exactly_24_khz_is_still_allowed(self):
        directory = build_design_dir(self.root)
        paths, _ = self.parsed(directory)
        self.rewrite(paths["original_left"], top=24_000.0)
        _, parsed = self.parsed(directory)
        self.assertEqual(deploy_filter.export_defects(parsed), [])

    def test_the_builder_refuses_them_too(self):
        """The library gate, so no caller can publish around the check."""
        directory = build_design_dir(self.root)
        paths, aggregate, named_rate, _ = MODULE.discover(directory)
        facts = MODULE.inspect(paths, named_rate)
        recipe = {
            "geometry": "120.blue", "variant": "d", "design_id": "d",
            "description": "d", "aggregate": aggregate,
            "source": {"directory": str(directory), "artifacts": {
                role: {"path": paths[role].name,
                       "bundle_path": f"source/d/{paths[role].name}",
                       "sha256": deploy_filter.sha256_file(paths[role])}
                for role in deploy_filter.ARTIFACT_ROLES}},
            "filter": {
                "sample_rate": facts["sample_rate"],
                "delay_samples": facts["delay_samples"],
                "txt_to_wav_gain_db": {
                    channel: round(float(item["txt_to_wav_gain_db"]), 6)
                    for channel, item in facts["alignments"].items()},
                "txt_wav_limits": deploy_filter.DEFAULT_LIMITS,
            },
        }
        self.rewrite(paths["original_sum"], smoothing="1/3 octave")
        with self.assertRaisesRegex(deploy_filter.AuditError, "cannot be deployed"):
            deploy_filter.build_analysis(recipe, paths)


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
                    deploy_filter.ROOT,
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
    def test_command_takes_a_directory_and_no_role_flags(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/new_filter_design.py"), "--help"],
            check=True, text=True, capture_output=True,
        )
        self.assertIn("directory", result.stdout)
        for removed in ("--measurement-left", "--filter-left-txt",
                        "--sum-mode", "--declaration", "--source-ref",
                        "--source-root", "--write"):
            self.assertNotIn(removed, result.stdout)

    def test_missing_directory_argument_is_refused(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/new_filter_design.py")],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("directory", result.stderr)

    def test_non_interactive_run_without_yes_refuses_to_assume(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-confirm-") as name:
            directory = build_design_dir(Path(name), commit=True)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/new_filter_design.py"),
                 str(directory), "--site-root", name, "--rates", "48000"],
                text=True, capture_output=True, stdin=subprocess.DEVNULL,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("--yes", result.stderr)
        # Everything before the prompt still ran and was reported.
        self.assertIn("120.blue.fixture-design.mdat", result.stdout)
        self.assertIn("WHERE THE MEASUREMENTS COME FROM", result.stdout)

    def test_uncommitted_exports_are_refused_before_anything_is_written(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-dirty-") as name:
            directory = build_design_dir(Path(name), commit=True)
            with (directory / "L.txt").open("a", encoding="utf-8") as stream:
                stream.write("21000.000000 0.000 0.0000\n")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/new_filter_design.py"),
                 str(directory), "--site-root", name, "--rates", "48000", "--yes"],
                text=True, capture_output=True, stdin=subprocess.DEVNULL,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not committed", result.stderr)
        self.assertFalse((Path(name) / "filters").exists())

    def test_live_upload_mode_archives_mdat_and_writes_runnable_config(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-live-") as name:
            root = Path(name)
            directory = build_design_dir(root)
            command = [
                sys.executable, str(ROOT / "scripts/new_filter_design.py"),
                str(directory), "--site-root", str(root), "--rates", "48000",
                "--allow-uncommitted", "--no-commit", "--yes", "--live",
                "--archive-mdat", "--upload-provenance"]
            result = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = root / "configs/120.blue/brutefir-48000@fixture-design.conf"
            self.assertTrue(config.is_file())
            self.assertIn(str(root / "filters/120.blue/48000/@fixture-design/L.raw"),
                          config.read_text())
            archived = root / "filters/120.blue/source/fixture-design/120.blue.fixture-design.mdat"
            self.assertEqual(archived.read_bytes(), b"REW measurement session fixture")
            manifest = json.loads((root / "filters/120.blue/provenance/fixture-design.json").read_text())
            self.assertEqual(manifest["source"]["measurements"]["bundle_path"],
                             "source/fixture-design/120.blue.fixture-design.mdat")
            before = {path: path.stat().st_mtime_ns for path in (config, archived)}
            repeated = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertIn("already installed", repeated.stdout)
            self.assertEqual(before, {path: path.stat().st_mtime_ns
                                      for path in (config, archived)})

    def test_verifier_help_documents_handoff_suppression(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/verify_filter_bundle.py"),
             "--help"],
            check=True, text=True, capture_output=True,
        )
        self.assertIn("--no-next", result.stdout)
        self.assertIn("CMake/CI/internal", result.stdout)
        self.assertIn("invocation", result.stdout)

    def test_console_colors_only_when_enabled(self):
        colored_stream = io.StringIO()
        colored = MODULE.Console(colored_stream, color=True)
        colored.stage(1, 5, "Find files")
        colored.warn("an export carries REW smoothing")
        self.assertIn("\x1b[", colored_stream.getvalue())

        plain_stream = io.StringIO()
        plain = MODULE.Console(plain_stream, color=False)
        plain.stage(1, 5, "Find files")
        plain.warn("an export carries REW smoothing")
        self.assertNotIn("\x1b[", plain_stream.getvalue())

    def test_next_step_cmake_keeps_existing_geometry_sets(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-cmake-cache-") as name:
            root = Path(name)
            (root / "build").mkdir()
            (root / "build/CMakeCache.txt").write_text(
                "CMAKE_INSTALL_PREFIX:PATH=/opt/omdrc\n"
                "GEOMETRY:STRING=flat\nGEOMETRIES:STRING=185\n",
                encoding="utf-8",
            )
            command = filter_workflow_next.cmake_configure_command(
                root, ["120.blue"])
        self.assertIn("-DGEOMETRIES=185;120.blue", command)

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
            }
            try:
                with mock.patch.object(
                        filter_workflow_next, "_scoped_status", return_value=""):
                    filter_workflow_next.print_deployed_next(
                        root, [bundle], include_verification=True)
            finally:
                filter_workflow_next.CONSOLE = previous
        output = stream.getvalue()
        self.assertIn("verify_filter_bundle.py", output)
        self.assertIn("sudo make install", output)
        self.assertIn("design @test-design", output)
        self.assertIn("bundle abc123", output)
        # The Git tag/commit anchor is gone; nothing should still promise one.
        self.assertNotIn("annotated tag", output)
        self.assertNotIn("source commit", output)


if __name__ == "__main__":
    unittest.main()
