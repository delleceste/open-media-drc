"""Regression checks for reference bias, context leakage and score semantics."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import bass_depth as bass


class Reference(unittest.TestCase):
    def test_pitch_variety_does_not_create_coherence_in_noise(self):
        rng = np.random.default_rng(17)
        L, R = rng.standard_normal((2, 16000 * 90))
        nfr = 1 + (len(L) - bass.NFFT) // bass.HOP
        frames, rest = np.arange(400), np.arange(500, nfr)
        ratios = []
        for count in (1, 4, 16):
            f0 = np.resize(np.repeat(np.geomspace(30, 65, count), 8), nfr)
            ref = bass.MatchedReference(L, R, rest, bass.SR / bass.NFFT)
            c = bass.context_cues(L, R, f0, frames, bass.SR / bass.NFFT, ref)
            ratios.append(c["coh_ratio"])
        np.testing.assert_allclose(ratios, 1, atol=0.2)
        self.assertLess(np.ptp(ratios), 0.2)

    def test_draws_preserve_long_runs_without_reusing_frames(self):
        x = np.zeros(2000 * bass.HOP)
        ref = bass.MatchedReference(x, x, np.arange(100, 1800), bass.SR / bass.NFFT)
        frames = np.r_[np.arange(200), np.arange(300, 500)]
        picked = ref._draw(frames, np.random.default_rng(1))
        self.assertEqual(len(np.unique(picked)), len(picked))
        np.testing.assert_array_equal(np.diff(picked[:200]), 1)
        np.testing.assert_array_equal(np.diff(picked[200:]), 1)

    def test_short_reference_abstains_instead_of_splitting_or_repeating(self):
        x = np.ones(300 * bass.HOP)
        ref = bass.MatchedReference(x, x, np.arange(100, 120), bass.SR / bass.NFFT)
        self.assertIsNone(ref(np.arange(30)))

    def test_short_gaps_preserve_fft_overlap(self):
        x = np.zeros(1000 * bass.HOP)
        ref = bass.MatchedReference(x, x, np.arange(100, 900), bass.SR / bass.NFFT)
        frames = np.array([0, 1, 3, 4, 7, 8, 9])
        picked = ref._draw(frames, np.random.default_rng(1))
        np.testing.assert_array_equal(picked - picked[0], frames)

    def test_query_does_not_depend_on_previous_queries(self):
        L, R = np.random.default_rng(1).standard_normal((2, 1000 * bass.HOP))
        ref = bass.MatchedReference(L, R, np.arange(100, 900), bass.SR / bass.NFFT)
        a = ref(np.arange(20))
        ref(np.arange(50))
        self.assertEqual(a, ref(np.arange(20)))

    def test_pizzicato_and_overlapping_windows_are_not_reference(self):
        masks = {name: np.zeros(50, bool) for name in bass.CONTEXTS}
        masks["pizzicato"][20:25] = True
        rest = bass.reference_frames(np.ones(50, bool), masks)
        self.assertFalse(np.isin(np.arange(17, 28), rest).any())
        self.assertIn(16, rest)
        self.assertIn(28, rest)


class Score(unittest.TestCase):
    def test_short_solo_is_flagged_even_with_long_other_context(self):
        d = bass.depth_score([{"solo": {"seconds": 2, "coh_ratio": 1.2, "fund_db": -16},
                               "with-sections": {"seconds": 23, "coh_ratio": 1.2}}])
        self.assertTrue(d["low_material"])
        self.assertEqual(d["fund_seconds"], 2)
        self.assertTrue(d["experimental"])

    def test_passages_use_the_track_rule_and_keep_unscored_balance(self):
        L, R = np.random.default_rng(1).standard_normal((2, 40 * bass.HOP))
        n = 1 + (len(L) - bass.NFFT) // bass.HOP
        f0 = {name: np.full(n, 50.) for name in bass.CONTEXTS}
        masks = {name: np.ones(n, bool) for name in bass.CONTEXTS}
        t = (np.arange(n) * bass.HOP + bass.NFFT / 2) / bass.SR
        blocks = bass.context_blocks(L, R, t, f0, masks, 0.5)
        for blk in blocks:
            c = bass.context_cues(L, R, f0[blk["context"]], np.arange(n), bass.SR / bass.NFFT, 0.5)
            d = bass.depth_score([{blk["context"]: {"seconds": blk["seconds"], **c}}])
            self.assertIn("balance_db", blk)
            if d is None:
                self.assertNotIn("score", blk)
            else:
                self.assertAlmostEqual(blk["score"], d["score"], places=2)
        blocks = bass.context_blocks(L, R, t, f0, masks, None)
        self.assertTrue(all("balance_db" in blk and "score" not in blk for blk in blocks))


if __name__ == "__main__":
    unittest.main()
