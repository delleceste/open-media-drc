#!/usr/bin/env python3
"""The web remote releases stored REW exports only for the exact active bytes.

The bundle under test is built here rather than read from a site checkout, so
the guarantees are checked on every clone and the fixture states the contract
in full: eight traces, each one a REW export, released unmodified.
"""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("omdrc_app", ROOT / "omdrc-ctrl/src/app.py")
APP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(APP)

sys.path.insert(0, str(ROOT / "scripts"))
from deploy_filter import TRACE_SPECS, canonical_hash, bundle_identity_from_manifest  # noqa: E402

RATE = 48000
GEOMETRY = "120.blue"
DESIGN = "rscreen.v2"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_bundle(root: Path) -> dict:
    """Write a complete, self-consistent schema 2 bundle under `root`."""
    geometry_root = root / "filters" / GEOMETRY
    rate_dir = geometry_root / str(RATE) / f"@{DESIGN}"
    rate_dir.mkdir(parents=True)
    source_dir = geometry_root / "source" / DESIGN
    source_dir.mkdir(parents=True)

    coefficients = {}
    for channel, name in (("left", "L.raw"), ("right", "R.raw")):
        payload = struct.pack("<4d", 1.0, 0.5, 0.25, 0.125)
        (rate_dir / name).write_bytes(payload)
        coefficients[channel] = {
            "path": f"{RATE}/@{DESIGN}/{name}",
            "sha256": _sha256_bytes(payload),
            "bytes": len(payload),
            "samples": 4,
            "peak_gain_db": 5.0,
        }

    grid = "0" * 16
    frequencies = [round(20.0 * (1.0 + index / 100.0), 6) for index in range(64)]
    traces, artifacts, inputs = [], {}, {}
    for role, identifier, label, color, group, visible in TRACE_SPECS:
        name = f"{identifier}.txt"
        payload = "\n".join(
            f"{frequency} {-index / 10:.3f} {index:.4f}"
            for index, frequency in enumerate(frequencies)).encode("utf-8")
        (source_dir / name).write_bytes(payload)
        digest = _sha256_bytes(payload)
        artifacts[role] = {
            "role": role, "path": name,
            "bundle_path": f"source/{DESIGN}/{name}", "sha256": digest,
            "bytes": len(payload), "measurement": identifier, "smoothing": "None",
        }
        inputs[role] = digest
        traces.append({
            "id": identifier, "label": label or "Aggregate", "color": color,
            "group": group, "default_visible": visible, "grid": grid,
            "magnitude_db": [round(-index / 10, 3) for index in range(64)],
            "phase_deg": [round(float(index), 4) for index in range(64)],
            "source_file": name,
        })
    for role in ("filter_left_wav", "filter_right_wav"):
        name = f"{role}.wav"
        payload = role.encode("utf-8")
        (source_dir / name).write_bytes(payload)
        digest = _sha256_bytes(payload)
        artifacts[role] = {
            "role": role, "path": name,
            "bundle_path": f"source/{DESIGN}/{name}", "sha256": digest,
            "bytes": len(payload),
        }
        inputs[role] = digest

    analysis = {
        "schema": 2, "geometry": GEOMETRY, "variant": DESIGN, "design_id": DESIGN,
        "description": f"{GEOMETRY} {DESIGN} correction",
        "frequency_grids": {grid: frequencies},
        "traces": traces,
        "calculation": {"note": "None.", "aggregate": {"style": "LR", "corrected": "filtered"},
                        "smoothing_applied": "none"},
        "inputs": inputs,
        "source_headers": {},
        "validation": {"filter_txt_to_wav": {}},
    }
    analysis_bytes = (json.dumps(analysis, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    (geometry_root / "analysis").mkdir()
    (geometry_root / "analysis" / f"{DESIGN}.json").write_bytes(analysis_bytes)

    source = {
        "directory": "/somewhere/DRC-120.blue/120.blue.rscreen.txts",
        "project": {
            "name": "DRC-120.blue", "repository": "/somewhere/DRC-120.blue",
            "remote": "", "branch": "main", "commit": "a" * 40,
            "committed_at": "2026-08-19T17:17:00+02:00", "subject": "measurements",
            "path": "120.blue.rscreen.txts", "clean": True, "uncommitted": [],
        },
        "measurements": {
            "file": "120.blue.rscreen.mdat", "path": "120.blue.rscreen.mdat",
            "sha256": "d" * 64, "bytes": 80703516, "git_blob": "e" * 40,
        },
        "artifacts": artifacts,
    }
    runtime = {str(RATE): {
        "config": f"configs/{GEOMETRY}/brutefir-{RATE}@{DESIGN}.conf.in",
        "config_sha256": "1" * 64, "format": "FLOAT64_LE",
        "attenuation_db": 3.0, "required_attenuation_db": 3.0,
        "safety_margin_db": 1.0, "channels": coefficients,
    }}
    manifest = {
        "schema": 2, "geometry": GEOMETRY, "variant": DESIGN, "design_id": DESIGN,
        "description": f"{GEOMETRY} {DESIGN} correction",
        "verification": {"status": "verified", "audited_at": "2026-08-19",
                         "claims": [], "prediction": "none"},
        "source": source,
        "aggregate": {"style": "LR", "corrected": "filtered"},
        "filter_validation": {},
        "runtime": {"rates": runtime},
        "analysis": {"path": f"analysis/{DESIGN}.json",
                     "sha256": _sha256_bytes(analysis_bytes),
                     "bytes": len(analysis_bytes)},
    }
    manifest["bundle_id"] = canonical_hash(bundle_identity_from_manifest(manifest))
    (geometry_root / "provenance").mkdir()
    (geometry_root / "provenance" / f"{DESIGN}.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


class FilterBundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="omdrc-bundle-test-")
        cls.root = Path(cls._temp.name)
        cls.manifest = build_bundle(cls.root)
        cls.analysis = json.loads(
            (cls.root / "filters" / GEOMETRY / "analysis" / f"{DESIGN}.json")
            .read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def parsed(self, attenuation=3.0):
        base = self.root / "filters" / GEOMETRY / str(RATE) / f"@{DESIGN}"
        return {
            "rate": RATE,
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

    def test_wrong_runtime_attenuation_withholds_measurements(self):
        bundle, verdict = APP._verified_filter_bundle(self.parsed(attenuation=2.9))
        self.assertIsNone(bundle)
        self.assertEqual(verdict["status"], "mismatch")
        self.assertIn("attenuation differs", verdict["message"])

    def test_bundle_identity_binds_sources_analysis_and_config(self):
        baseline = APP._canonical_hash(APP._bundle_identity(self.manifest))

        changed = copy.deepcopy(self.manifest)
        changed["description"] = "A different human-readable design name"
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

        changed = copy.deepcopy(self.manifest)
        changed["source"]["directory"] = "/elsewhere"
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

        # The project a design came from and the REW session behind it are part
        # of what the bundle claims, so neither can be edited after the fact.
        changed = copy.deepcopy(self.manifest)
        changed["source"]["project"]["commit"] = "9" * 40
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

        changed = copy.deepcopy(self.manifest)
        changed["source"]["measurements"]["sha256"] = "8" * 64
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

        changed = copy.deepcopy(self.manifest)
        changed["source"]["artifacts"]["original_sum"]["sha256"] = "3" * 64
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

        changed = copy.deepcopy(self.manifest)
        changed["analysis"]["sha256"] = "4" * 64
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

        changed = copy.deepcopy(self.manifest)
        changed["runtime"]["rates"][str(RATE)]["config_sha256"] = "2" * 64
        self.assertNotEqual(APP._canonical_hash(APP._bundle_identity(changed)), baseline)

    def test_schema_1_bundles_are_rejected(self):
        legacy = copy.deepcopy(self.manifest)
        legacy["schema"] = 1
        with self.assertRaises(ValueError):
            APP._bundle_identity(legacy)

    def test_stored_traces_are_released_unmodified(self):
        """No scaling, offset or resampling stands between the file and the page."""
        bundle, _ = APP._verified_filter_bundle(self.parsed())
        returned = {trace["id"]: trace for trace in bundle["analysis"]["traces"]}
        stored = {trace["id"]: trace for trace in self.analysis["traces"]}
        self.assertEqual(set(returned), set(stored))
        for identifier, trace in returned.items():
            self.assertEqual(trace, stored[identifier])
        self.assertEqual(bundle["analysis"]["frequency_grids"],
                         self.analysis["frequency_grids"])

    def test_every_trace_names_the_export_it_came_from(self):
        for trace in self.analysis["traces"]:
            self.assertTrue(trace["source_file"])
            self.assertIn(trace["grid"], self.analysis["frequency_grids"])

    def test_default_curves_are_the_two_aggregates(self):
        defaults = {
            trace["id"] for trace in self.analysis["traces"]
            if trace.get("default_visible")
        }
        self.assertEqual(defaults, {"original-sum", "corrected-sum"})

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


if __name__ == "__main__":
    unittest.main()
