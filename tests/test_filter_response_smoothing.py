#!/usr/bin/env python3
"""Browser response smoothing stays selectable, bounded and display-only."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOOTHING = ROOT / "omdrc-ctrl/src/static/filter-response-smoothing.js"
TEMPLATE = ROOT / "omdrc-ctrl/src/templates/filter_response.html"
NODE = shutil.which("node")


class FilterResponseSmoothingTest(unittest.TestCase):
    def test_page_exposes_every_requested_smoothing(self):
        page = TEMPLATE.read_text(encoding="utf-8")
        for value in ("none", "variable", "psychoacoustic", "octave-6", "octave-3"):
            self.assertIn(f'value="{value}"', page)
        self.assertIn("verified source arrays and hashes remain unchanged", page)

    @unittest.skipUnless(NODE, "node is required for the JavaScript behavior test")
    def test_smoothing_math_and_phase_wrapping(self):
        program = f"""
require({json.dumps(str(SMOOTHING))});
const api = globalThis.FilterResponseSmoothing;
const fixed = api.smoothTrace([100, 200, 400, 800], [-6, -6, -6, -6], 'magnitude_db', 'octave-3');
const phase = api.smoothTrace([100, 101], [179, -179], 'phase_deg', 'octave-3');
const rawF = [100, 200]; const rawV = [1, 2];
const raw = api.smoothTrace(rawF, rawV, 'magnitude_db', 'none');
process.stdout.write(JSON.stringify({{
  fixed: fixed.values,
  phase: phase.values,
  rawIdentity: raw.frequencies === rawF && raw.values === rawV,
  variable: [api.bandwidthOctaves('variable', 50), api.bandwidthOctaves('variable', 1000), api.bandwidthOctaves('variable', 20000)],
  psycho: [api.bandwidthOctaves('psychoacoustic', 50), api.bandwidthOctaves('psychoacoustic', 2000)]
}}));
"""
        result = subprocess.run(
            [NODE, "-e", program], check=True, text=True, capture_output=True)
        data = json.loads(result.stdout)
        self.assertTrue(data["rawIdentity"])
        self.assertTrue(all(abs(value + 6) < 1e-9 for value in data["fixed"]))
        self.assertTrue(all(abs(value) > 170 for value in data["phase"]))
        self.assertEqual(data["variable"], [1 / 48, 1 / 6, 1 / 3])
        self.assertEqual(data["psycho"], [1 / 3, 1 / 6])


if __name__ == "__main__":
    unittest.main()
