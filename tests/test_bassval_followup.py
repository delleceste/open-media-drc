"""Independent invariants for supplementary validation measurements."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import bass_depth as bass
import bassval_followup as followup
from bassval_summarize import cue_summary
from bassval_spot_levels import remix
from bassval import render, scenes


class Followup(unittest.TestCase):
    def test_spot_gain_and_delay_remixes_match_full_renderer(self):
        rng = np.random.default_rng(22)
        parts = {'kb': [rng.normal(size=8000)], 'vl1': [rng.normal(size=8000)]}
        components = {}
        base = scenes.Scene('control', rt_mid=.4, spot='unaligned')
        x, truth = render.render_scene(base, parts, bass.SR, components)
        spot_pair = x / components['output_scale'] - components['main']
        for aligned in (False, True):
            for level in (-6., 0., 6., 12.):
                scene = scenes.Scene('control', rt_mid=.4, spot='aligned' if aligned else 'unaligned',
                                     spot_db=level)
                expected, _ = render.render_scene(scene, parts, bass.SR)
                actual = remix(components['main'], spot_pair, truth['spot_delta_ms'], level, aligned)
                np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)

    def test_summary_counts_missing_and_constant_groups_without_dropping_them(self):
        groups = {key: [{'distance': i, 'cues': {'cue': v}} for i, v in enumerate(values)]
                  for key, values in {'right': [4, 3, 2, 1], 'wrong': [1, 2, 3, 4],
                                      'constant': [1, 1, 1, 1], 'missing': [4, 3, None, 1]}.items()}
        result = cue_summary(groups, 'cue', -1, lambda r: r['distance'])
        self.assertEqual(result['groups'], 4)
        self.assertEqual(result['complete'], 3)
        self.assertEqual(result['varying'], 2)
        self.assertEqual(result['strictly_monotonic_right'], 1)
        self.assertAlmostEqual(result['mean_rho'], 0)

    def test_radial_sweep_preserves_each_bearing_and_sets_mean_range(self):
        centre = np.array([12., 14., 3.5])
        original = np.array([[6., 4., 1.2], [7., 5., 1.2]])
        direction = original - centre
        direction /= np.linalg.norm(direction, axis=1)[:, None]
        for distance in (8., 10., 12., 14.):
            moved = followup.radial_positions(original, centre, distance) - centre
            self.assertAlmostEqual(np.linalg.norm(moved, axis=1).mean(), distance)
            np.testing.assert_allclose(moved / np.linalg.norm(moved, axis=1)[:, None], direction)

    def test_channel_power_control_rejects_mono_phase_confound(self):
        t = np.arange(bass.SR * 4) / bass.SR
        L = sum(np.sin(2*np.pi*80*k*t)/k for k in range(1, 7))
        R = sum(np.sin(2*np.pi*80*k*(t-.00175))/k for k in range(1, 7))
        frames, pitches = np.arange(5, 50), np.full(60, 80.)
        a = followup.channel_power_fund(L, L, pitches, frames)
        b = followup.channel_power_fund(L, R, pitches, frames)
        self.assertAlmostEqual(a, b, places=4)
        mono_a = bass.context_cues(L, L, pitches, frames, bass.SR/bass.NFFT, None)['fund_db']
        mono_b = bass.context_cues(L, R, pitches, frames, bass.SR/bass.NFFT, None)['fund_db']
        self.assertGreater(mono_b-mono_a, 4.)

    def test_annotation_denominator_excludes_out_of_range_and_note_edges(self):
        t = np.arange(bass.SR * 4) / bass.SR
        x = sum(np.sin(2*np.pi*55*k*t)/k for k in range(1, 7))
        result = followup.annotation_metrics(x, [(0., 4., 55.)])
        self.assertGreater(result['correct_pitch_detection_recall'], .95)
        outside = followup.annotation_metrics(x, [(0., 4., 110.)])
        self.assertEqual(outside['eligible_interior_frames'], 0)
        self.assertIsNone(outside['in_range_interior_recall'])
        short = followup.annotation_metrics(x, [(1., 1.1, 55.)])
        self.assertEqual(short['eligible_interior_frames'], 0)
        self.assertAlmostEqual(followup.note_hz('A1'), 55.)


if __name__ == '__main__':
    unittest.main()
