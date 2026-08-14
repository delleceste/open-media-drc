#!/usr/bin/env python3

import importlib.util
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("omdrc_app", ROOT / "omdrc-ctrl/src/app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)

# This is an integration test against a real published bundle, and room data
# does not have to live in this checkout: it is resolved through the site root
# (OMDRC_SITE_ROOT, else here).  A clone without any room set skips rather than
# fails — the engine repository ships only the generic `flat` set.
sys.path.insert(0, str(ROOT / "scripts"))
from deploy_filter import resolve_site_root  # noqa: E402

SITE = resolve_site_root()
BUNDLE = SITE / "filters/120.blue"


@unittest.skipUnless(
    (BUNDLE / "provenance/default.json").is_file(),
    f"no 120.blue bundle under {SITE}; set OMDRC_SITE_ROOT to the site checkout")
class FilterBundleTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(
            (BUNDLE / "provenance/default.json").read_text(encoding="utf-8")
        )
        self.analysis = json.loads(
            (BUNDLE / "analysis/default.json").read_text(encoding="utf-8")
        )

    def parsed(self, rate=48000, attenuation=3.0):
        base = BUNDLE / str(rate)
        return {
            "rate": rate,
            "coeffs": [
                {"label": "c-l", "filename": str(base / "L.raw"),
                 "format": "FLOAT64_LE", "attenuation": attenuation},
                {"label": "c-r", "filename": str(base / "R.raw"),
                 "format": "FLOAT64_LE", "attenuation": attenuation},
            ],
        }

    def test_bundle_id_and_active_bytes_verify(self):
        self.assertEqual(
            APP._canonical_hash(APP._bundle_identity(self.manifest)),
            self.manifest["bundle_id"],
        )
        bundle, verdict = APP._verified_filter_bundle(self.parsed())
        self.assertIsNotNone(bundle)
        self.assertEqual(verdict["status"], "verified")
        self.assertEqual(verdict["bundle_id"], self.manifest["bundle_id"])
        self.assertIn(self.manifest["source"]["repository_head"], verdict["message"])

    def test_wrong_runtime_attenuation_withholds_measurements(self):
        bundle, verdict = APP._verified_filter_bundle(self.parsed(attenuation=2.9))
        self.assertIsNone(bundle)
        self.assertEqual(verdict["status"], "mismatch")
        self.assertIn("attenuation differs", verdict["message"])

    def test_bundle_identity_binds_commit_tag_and_config(self):
        baseline = APP._canonical_hash(APP._bundle_identity(self.manifest))
        changed = copy.deepcopy(self.manifest)
        changed["source"]["repository_head"] = "0" * 40
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

        changed = copy.deepcopy(self.manifest)
        changed["description"] = "A human-readable design name"
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

        changed = copy.deepcopy(self.manifest)
        changed["source"]["release"] = {
            "kind": "annotated_tag", "name": "test", "tag_object": "1" * 40,
            "commit": self.manifest["source"]["repository_head"],
        }
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

        changed = copy.deepcopy(self.manifest)
        changed["source"]["lineage"] = ["unbound replacement lineage"]
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

        changed = copy.deepcopy(self.manifest)
        changed["runtime"]["rates"]["48000"]["config_sha256"] = "2" * 64
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

    def test_design_selector_keeps_dots_in_immutable_id(self):
        rate, selector = APP._design_selector_from_conf(
            "/etc/open-media-drc/configs/120.blue/brutefir-48000@rscreen.v2.conf")
        self.assertEqual(rate, 48000)
        self.assertEqual(selector, "@rscreen.v2")

    def test_saved_session_must_match_exact_active_identity(self):
        session = {
            "power": "on", "geometry": "120.blue", "rate": 48000,
            "design": "@rscreen.v2",
        }
        active = {
            "running": True, "geometry": "120.blue", "rate": 48000,
            "design": "@rscreen.v2",
        }
        self.assertTrue(APP._session_matches_active(session, active))
        active["design"] = "default"
        self.assertFalse(APP._session_matches_active(session, active))
        self.assertTrue(APP._session_matches_active({"power": "off"}, {"running": False}))

    def test_design_switch_with_unverified_active_bytes_is_not_green(self):
        before_session = {
            "power": "on", "geometry": "120.blue", "rate": 48000,
            "design": "default",
        }
        after_session = {**before_session, "design": "@rscreen.v2"}
        before = {
            "running": True, "geometry": "120.blue", "rate": 48000,
            "design": "default",
        }
        after = {
            "running": True, "geometry": "120.blue", "rate": 48000,
            "design": "@rscreen.v2",
            "verification": {"status": "mismatch", "message": "RAW SHA-256 differs"},
        }
        with (mock.patch.object(APP, "_drc_script", return_value="/fake/omdrc"),
              mock.patch.object(APP, "_drc_designs",
                                return_value=["default", "@rscreen.v2"]),
              mock.patch.object(APP, "_drc_saved_session",
                                side_effect=[before_session, after_session]),
              mock.patch.object(APP, "_active_design_identity",
                                side_effect=[before, after]),
              mock.patch.object(APP, "_run_drc_switch",
                                return_value={"ok": True, "output": "started"})):
            response = APP.app.test_client().post(
                "/drc/design", json={"design": "@rscreen.v2"})
        result = response.get_json()
        self.assertFalse(result["ok"])
        self.assertEqual(result["active"]["design"], "@rscreen.v2")
        self.assertIn("could not be verified", result["error"])

    def test_runtime_attenuation_is_applied_only_to_filter_and_prediction(self):
        bundle, _ = APP._verified_filter_bundle(self.parsed())
        returned = {trace["id"]: trace for trace in bundle["analysis"]["traces"]}
        stored = {trace["id"]: trace for trace in self.analysis["traces"]}
        index = 500
        self.assertEqual(returned["original-left"]["magnitude_db"][index],
                         stored["original-left"]["magnitude_db"][index])
        self.assertAlmostEqual(returned["filter-left"]["magnitude_db"][index],
                               stored["filter-left"]["magnitude_db"][index] - 3.0, places=3)
        self.assertAlmostEqual(returned["corrected-sum"]["magnitude_db"][index],
                               stored["corrected-sum"]["magnitude_db"][index] - 3.0, places=3)

    def test_default_curves_are_measured_and_corrected_sum(self):
        defaults = {
            trace["id"] for trace in self.analysis["traces"]
            if trace.get("default_visible")
        }
        self.assertEqual(defaults, {"original-sum-measured", "corrected-sum"})

if __name__ == "__main__":
    unittest.main()
