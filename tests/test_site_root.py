#!/usr/bin/env python3
"""The site-root seam: where configs/<geo> and filters/<geo> are read and written.

The engine checkout and the room data may live in one repository (the default,
historical layout) or in two, so that personal measurements stay out of a public
engine repository.  These tests pin both the resolution order and the fact that
the operator handoff names the right working directory for each step.
"""

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
import io


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SPEC = importlib.util.spec_from_file_location(
    "deploy_filter", ROOT / "scripts/deploy_filter.py")
deploy_filter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(deploy_filter)

import filter_workflow_next  # noqa: E402


class SiteRootResolutionTest(unittest.TestCase):
    def test_defaults_to_the_engine_checkout(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(deploy_filter.resolve_site_root(), deploy_filter.ROOT)

    def test_environment_overrides_the_default(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-site-") as name:
            with mock.patch.dict(os.environ, {"OMDRC_SITE_ROOT": name}, clear=True):
                self.assertEqual(
                    deploy_filter.resolve_site_root(), Path(name).resolve())

    def test_explicit_argument_beats_the_environment(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-site-") as name:
            with mock.patch.dict(os.environ, {"OMDRC_SITE_ROOT": "/nonexistent"}, clear=True):
                self.assertEqual(
                    deploy_filter.resolve_site_root(Path(name)), Path(name).resolve())

    def test_missing_site_root_is_an_audit_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(deploy_filter.AuditError):
                deploy_filter.resolve_site_root(Path("/nonexistent/omdrc-site"))


class SplitHandoffTest(unittest.TestCase):
    """The published handoff must not send git and cmake to the same place."""

    BUNDLE = {
        "geometry": "120.blue",
        "design_id": "test-design",
        "bundle_id": "abc123",
        "manifest": Path("filters/120.blue/provenance/test-design.json"),
        "release": {"name": "120.blue-test-design"},
        "source_commit": "deadbeef",
    }

    def _render(self, **kwargs) -> str:
        output = io.StringIO()
        console = filter_workflow_next.Console(stream=output, color=False)
        # Ignore whatever this checkout's build/ happens to be configured for:
        # the handoff text under test must not depend on local CMake state.
        with (mock.patch.object(filter_workflow_next, "CONSOLE", console),
              mock.patch.object(filter_workflow_next, "_cache_values", return_value={})):
            filter_workflow_next.print_deployed_next(
                ROOT, [dict(self.BUNDLE)], include_verification=True, **kwargs)
        return output.getvalue()

    def test_single_repository_names_source_and_build_directories(self):
        rendered = self._render()
        self.assertIn(f"Run from: {ROOT}", rendered)
        self.assertIn(f"Run from: {ROOT / 'build'}", rendered)
        self.assertNotIn("--site-root", rendered)

    def test_split_layout_names_a_directory_per_step(self):
        with tempfile.TemporaryDirectory(prefix="omdrc-site-") as name:
            site = Path(name).resolve()
            rendered = self._render(site_root=site)
        # git commits in the site repository, cmake builds in the engine one.
        self.assertIn(f"Run from: {site}", rendered)
        self.assertIn(f"Run from: {ROOT}", rendered)
        self.assertIn(f"Run from: {ROOT / 'build'}", rendered)
        # The checker runs from the engine checkout, so it needs both the site
        # root and an absolute manifest path to reach the published bundle.
        self.assertIn(f"--site-root {site}", rendered)
        self.assertIn(f"{site}/filters/120.blue/provenance/test-design.json", rendered)
        # A first configure of a split layout must carry the search path.
        self.assertIn(f"-DOMDRC_SITE_DATA_DIRS={ROOT};{site}", rendered)


if __name__ == "__main__":
    unittest.main()
