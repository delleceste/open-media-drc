#!/usr/bin/env python3
"""Taking a design away is as complete as putting it there.

A half-removed design is worse than a deployed one: still listed, still offered
by the web remote, no longer verifiable.  These tests pin down what a design
owns, what it must never touch, and that a redeployment leaves no file behind
that its manifest does not name.
"""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import deploy_filter  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "remove_filter_design", ROOT / "scripts/remove_filter_design.py")
REMOVE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(REMOVE)

RATES = (44100, 48000)
GEOMETRY = "120.blue"


def build_site(root: Path, designs=("keep", "drop")) -> Path:
    """A room tree holding a default set plus one directory per design."""
    filters = root / "filters" / GEOMETRY
    configs = root / "configs" / GEOMETRY
    configs.mkdir(parents=True)
    (filters / "provenance").mkdir(parents=True)
    (filters / "analysis").mkdir(parents=True)
    for rate in RATES:
        (filters / str(rate)).mkdir(parents=True)
        for name in ("L.raw", "R.raw"):
            (filters / str(rate) / name).write_bytes(b"\x00" * 16)
        (filters / str(rate) / "sox.txt").write_text("default conversion log\n")
        (configs / f"brutefir-{rate}.conf.in").write_text("default\n")
    for design in designs:
        (filters / "source" / design).mkdir(parents=True)
        for name in ("L.txt", "R.txt"):
            (filters / "source" / design / name).write_text(f"{design} {name}\n")
        (filters / "analysis" / f"{design}.json").write_text("{}\n")
        (filters / "provenance" / f"{design}.json").write_text(json.dumps({
            "geometry": GEOMETRY, "design_id": design, "bundle_id": "a" * 64,
            "description": f"{GEOMETRY} {design}",
            "verification": {"audited_at": "2026-08-20"},
            "source": {"project": {"name": "DRC-120.blue", "commit": "b" * 40},
                       "measurements": {"file": "s.mdat", "sha256": "c" * 64}},
            "runtime": {"rates": {str(rate): {} for rate in RATES}},
        }) + "\n")
        (filters / "provenance" / f"{design}.source.json").write_text("{}\n")
        for rate in RATES:
            (filters / str(rate) / f"@{design}").mkdir()
            for name in ("L.raw", "R.raw"):
                (filters / str(rate) / f"@{design}" / name).write_bytes(b"\x01" * 16)
            (configs / f"brutefir-{rate}@{design}.conf.in").write_text(f"{design}\n")
    return root


class SelectorTest(unittest.TestCase):
    def test_geometry_at_design_is_the_selector_the_ui_shows(self):
        self.assertEqual(REMOVE.parse_selector("120.blue@rscreen-20260812"),
                         ("120.blue", "rscreen-20260812"))

    def test_a_bare_geometry_is_not_a_design(self):
        with self.assertRaisesRegex(deploy_filter.AuditError, "does not name a design"):
            REMOVE.parse_selector("120.blue")

    def test_the_default_set_cannot_be_removed_this_way(self):
        with self.assertRaisesRegex(deploy_filter.AuditError, "base filter set"):
            REMOVE.parse_selector("120.blue@default")


class OwnershipTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="omdrc-remove-")
        self.root = build_site(Path(self._temp.name))
        self.addCleanup(self._temp.cleanup)

    def owned(self, design="drop"):
        return {REMOVE.relative(path, self.root)
                for path in REMOVE.owned_paths(self.root, GEOMETRY, design)}

    def test_every_piece_of_one_design_is_claimed(self):
        self.assertEqual(self.owned(), {
            "filters/120.blue/provenance/drop.json",
            "filters/120.blue/provenance/drop.source.json",
            "filters/120.blue/analysis/drop.json",
            "filters/120.blue/source/drop",
            "filters/120.blue/44100/@drop",
            "filters/120.blue/48000/@drop",
            "configs/120.blue/brutefir-44100@drop.conf.in",
            "configs/120.blue/brutefir-48000@drop.conf.in",
        })

    def test_the_shared_default_set_is_never_claimed(self):
        owned = self.owned()
        for shared in ("filters/120.blue/44100/L.raw", "filters/120.blue/44100/sox.txt",
                       "configs/120.blue/brutefir-44100.conf.in"):
            self.assertNotIn(shared, owned)

    def test_another_design_is_never_claimed(self):
        self.assertFalse({item for item in self.owned() if "keep" in item})

    def test_listing_offers_only_removable_designs(self):
        self.assertEqual(REMOVE.deployed_designs(self.root), {GEOMETRY: ["drop", "keep"]})

    def test_removal_leaves_the_other_design_and_the_default_intact(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/remove_filter_design.py"),
             f"{GEOMETRY}@drop", "--site-root", str(self.root), "--yes"],
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.root.rglob("*drop*")), [])
        for survivor in ("filters/120.blue/provenance/keep.json",
                         "filters/120.blue/48000/@keep/L.raw",
                         "filters/120.blue/48000/L.raw",
                         "filters/120.blue/48000/sox.txt",
                         "configs/120.blue/brutefir-48000@keep.conf.in",
                         "configs/120.blue/brutefir-48000.conf.in"):
            self.assertTrue((self.root / survivor).exists(), survivor)

    def test_a_missing_design_is_refused_with_the_list_of_real_ones(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/remove_filter_design.py"),
             f"{GEOMETRY}@never-deployed", "--site-root", str(self.root), "--yes"],
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing deployed", result.stderr)
        self.assertIn("120.blue@keep", result.stderr)

    def test_dry_run_deletes_nothing(self):
        before = sorted(str(item) for item in self.root.rglob("*"))
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/remove_filter_design.py"),
             f"{GEOMETRY}@drop", "--site-root", str(self.root), "--dry-run"],
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing was deleted", result.stdout)
        self.assertEqual(sorted(str(item) for item in self.root.rglob("*")), before)


class PruneTest(unittest.TestCase):
    """A published design owns its directories: no unnamed file may survive there."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="omdrc-prune-")
        self.root = build_site(Path(self._temp.name), designs=("drop",))
        self.geometry_root = self.root / "filters" / GEOMETRY
        self.addCleanup(self._temp.cleanup)
        self.artifacts = {
            "original_left": {"bundle_path": "source/drop/L.txt"},
            "original_right": {"bundle_path": "source/drop/R.txt"},
        }
        self.runtime = {
            str(rate): {"channels": {
                "left": {"path": f"{rate}/@drop/L.raw"},
                "right": {"path": f"{rate}/@drop/R.raw"}}}
            for rate in RATES
        }

    def stale(self):
        return sorted(str(path.relative_to(self.geometry_root))
                      for path in deploy_filter.unreferenced_files(
                          self.geometry_root, "drop", self.artifacts, self.runtime))

    def test_a_matching_bundle_has_nothing_to_prune(self):
        self.assertEqual(self.stale(), [])

    def test_exports_from_an_earlier_naming_scheme_are_found(self):
        (self.geometry_root / "source/drop/measurement-L.txt").write_text("old\n")
        (self.geometry_root / "source/drop/filter-L.wav").write_bytes(b"old")
        self.assertEqual(self.stale(),
                         ["source/drop/filter-L.wav", "source/drop/measurement-L.txt"])

    def test_a_stray_file_in_a_design_rate_directory_is_found(self):
        (self.geometry_root / "48000/@drop/L.raw.bak").write_bytes(b"old")
        self.assertEqual(self.stale(), ["48000/@drop/L.raw.bak"])

    def test_the_shared_rate_directory_is_never_pruned(self):
        runtime = {"48000": {"channels": {"left": {"path": "48000/L.raw"},
                                          "right": {"path": "48000/R.raw"}}}}
        stale = deploy_filter.unreferenced_files(
            self.geometry_root, "drop", self.artifacts, runtime)
        self.assertEqual([path.name for path in stale], [])
        self.assertTrue((self.geometry_root / "48000/sox.txt").exists())


if __name__ == "__main__":
    unittest.main()
