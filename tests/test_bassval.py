"""The validation suite for the bass depth detector (tools/bassval): its
geometry conventions, the room model against theory, part-name parsing,
the renderer's spot microphone, and the report."""
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from bassval import evaluate, geometry, render, room, scenes, stems  # noqa: E402
from bassval import __main__ as cli

FS = 16000


def _towards(src, mic):
    d = np.asarray(src, float) - mic.pos
    return d / np.linalg.norm(d)


class Geometry(unittest.TestCase):
    def test_left_channel_hears_the_first_violins_right_the_basses(self):
        hall = geometry.Hall()
        for setup in ("ORTF", "XY", "Blumlein"):
            left, right = geometry.main_pair(setup, [hall.width / 2, 14, 3.5])
            vl1 = geometry.section_centre("vl1", hall)
            kb = geometry.section_centre("kb", hall)
            self.assertGreater(abs(left.gain(_towards(vl1, left))), abs(right.gain(_towards(vl1, right))), setup)
            self.assertGreater(abs(right.gain(_towards(kb, right))), abs(left.gain(_towards(kb, left))), setup)

    def test_players_stay_in_their_section_and_follow_the_shift(self):
        hall = geometry.Hall()
        rng = np.random.default_rng(0)
        x0, x1, y0, y1 = geometry.SECTIONS["kb"]
        p = geometry.positions("kb", 5, hall, rng, shift=(0.0, -2.0))
        self.assertTrue(np.all((p[:, 0] >= hall.width / 2 + x0) & (p[:, 0] <= hall.width / 2 + x1)))
        self.assertTrue(np.all((p[:, 1] >= y0 - 2.0) & (p[:, 1] <= y1 - 2.0)))

    def test_spot_points_at_its_section(self):
        c = np.array([6.0, 4.5, 1.2])
        m = geometry.spot_mic(c)
        self.assertAlmostEqual(float(m.gain(_towards(c, m))), 1.0, places=6)

    def test_hall_rt_scaling_keeps_the_shape(self):
        h = geometry.hall_with_rt(2.6)
        self.assertAlmostEqual(h.rt60_mid(), 2.6)
        base = geometry.Hall()
        self.assertAlmostEqual(h.rt60[63] / h.rt60[1000], base.rt60[63] / base.rt60[1000])


class Room(unittest.TestCase):
    def test_generated_tails_have_the_requested_diffuse_coherence(self):
        hall = geometry.hall_with_rt(1.0)
        for setup in ("AB", "XY", "Blumlein"):
            mics = geometry.main_pair(setup, [12, 14, 3.5])
            ll = rr = lr = 0
            # A decaying tail has few independent samples in a narrow band.
            # Ensemble averaging is essential, especially near a sinc zero.
            for seed in range(128):
                tails = room.late_tails(mics, hall, FS, FS * 3, 0, np.random.default_rng(seed))
                L, R = np.fft.rfft(tails, axis=1)
                ll = ll + abs(L) ** 2
                rr = rr + abs(R) ** 2
                lr = lr + np.conj(L) * R
            frequencies = np.fft.rfftfreq(FS * 3, 1 / FS)
            for centre in (100, 250, 500):
                band = abs(frequencies - centre) < 15
                measured = lr[band].sum() / np.sqrt(ll[band].sum() * rr[band].sum())
                expected = room.diffuse_coherence(*mics, np.array([centre]))[0]
                self.assertLess(abs(measured - expected), .1, (setup, centre, measured, expected))

    def test_spaced_omnis_have_the_diffuse_coherence_of_theory(self):
        left, right = geometry.main_pair("AB", [12, 14, 3.5])
        f = np.array([50.0, 150.0, 400.0])
        k = 2 * np.pi * f / room.C_SOUND
        np.testing.assert_allclose(room.diffuse_coherence(left, right, f).real,
                                   np.sin(k * 0.6) / (k * 0.6), atol=0.01)

    def test_coincident_cardioids_at_90_degrees(self):
        left, right = geometry.main_pair("XY", [12, 14, 3.5])
        # (a² + (1−a)²·cos 90°/3) / (a² + (1−a)²/3) with a = 0.5
        self.assertAlmostEqual(float(room.diffuse_coherence(left, right, [100.0])[0].real), 0.75, delta=0.001)

    def test_tilted_blumlein_remains_orthogonal(self):
        for tilt in (0, -15, -45):
            left, right = geometry.main_pair("Blumlein", [12, 14, 3.5], tilt)
            self.assertAlmostEqual(float(left.axis @ right.axis), 0, places=12)
            self.assertAlmostEqual(float(room.diffuse_coherence(left, right, [100.])[0].real),
                                   0, delta=0.001)

    def test_direct_sound_timing_and_level(self):
        hall = geometry.Hall()
        mic = geometry.Mic(np.array([12.0, 14.0, 3.5]), np.array([0.0, -1.0, 0.0]), 1.0)
        src = np.array([12.0, 4.0, 3.5])
        h, t0, e_direct, _ = room.early_response(src, mic, hall, FS, FS)
        self.assertAlmostEqual(t0, 10.0 / room.C_SOUND)
        self.assertAlmostEqual(e_direct, 1.0 / 100.0, places=6)          # (1/r)², r = 10 m
        self.assertEqual(int(np.argmax(np.abs(h))), int(round(t0 * FS)))

    def test_direct_to_reverberant_ratio_follows_sabine(self):
        hall = geometry.Hall()
        mic = geometry.Mic(np.array([12.0, 14.0, 3.5]), np.array([0.0, -1.0, 0.0]), 1.0)
        rng = np.random.default_rng(3)
        for y in (4.0, 8.0):
            src = np.array([12.0, y, 1.2])
            r = float(np.linalg.norm(src - mic.pos))
            out = room.pair_responses(src, [mic], hall, FS, rng)
            drr = 10 * math.log10(out["direct_e"][0] / out["reverb_e"][0])
            self.assertAlmostEqual(drr, 20 * math.log10(room.critical_distance(hall) / r), delta=2.0)

    def test_image_sources_include_the_source_once_unreflected(self):
        hall = geometry.Hall()
        src = np.array([5.0, 6.0, 1.2])
        pos, bounces = room.image_sources(src, hall, 30.0)
        direct = pos[bounces == 0]
        self.assertEqual(len(direct), 1)
        np.testing.assert_allclose(direct[0], src)


class Stems(unittest.TestCase):
    def test_part_names(self):
        self.assertEqual(stems.parse_name("mahler_vla_6.mp3"), ("vla", "6"))
        self.assertEqual(stems.parse_name("beethoven_kb_6.mp3"), ("kb", "6"))
        self.assertEqual(stems.parse_name("bruckner_kbb_6.mp3"), ("kb", "6"))
        self.assertEqual(stems.parse_name("beethoven_vl1a_6.mp3"), ("vl1", "6"))
        self.assertEqual(stems.parse_name("bruckner_trb2_6.mp3"), ("brass", "6"))
        self.assertEqual(stems.parse_name("mahler_corno3_8.mp3"), ("brass", "8"))
        self.assertIsNone(stems.parse_name("mp3info.txt"))

    def test_section_players_are_offset_copies(self):
        x = np.zeros(FS)
        x[100] = 1.0
        players = stems.section_players([x], 3, FS, np.random.default_rng(0))
        self.assertEqual(int(np.argmax(players[0])), 100)
        for p in players[1:]:
            lag = (int(np.argmax(np.abs(p))) - 100) / FS
            self.assertTrue(0.005 <= lag <= 0.030)


class Render(unittest.TestCase):
    def parts(self):
        rng = np.random.default_rng(5)
        n = FS // 2
        burst = lambda: rng.standard_normal(n) * np.exp(-np.arange(n) / 800)   # noqa: E731
        return {"kb": [burst()], "vl1": [burst()]}

    def test_an_unaligned_spot_arrives_before_the_main_pair(self):
        sc = scenes.Scene("test", setup="ORTF", rt_mid=0.4, spot="unaligned", spot_db=0.0)
        x, truth = render.render_scene(sc, self.parts(), FS)
        self.assertEqual(x.shape[0], 2)
        self.assertGreater(truth["spot_delta_ms"], 15.0)        # ~10 m vs 1.5 m: ~25 ms
        self.assertLess(truth["bass_drr_db"], 10.0)

    def test_aligned_and_unaligned_differ_only_in_the_spot_delay(self):
        a = render.render_scene(scenes.Scene("t", rt_mid=0.4, spot="aligned"), self.parts(), FS)
        u = render.render_scene(scenes.Scene("t", rt_mid=0.4, spot="unaligned"), self.parts(), FS)
        self.assertAlmostEqual(a[1]["spot_delta_ms"], u[1]["spot_delta_ms"])
        self.assertFalse(np.allclose(a[0], u[0]))

    def test_moving_the_basses_back_lowers_their_drr(self):
        near = render.render_scene(scenes.Scene("t", rt_mid=1.0, bass_shift=4.0), self.parts(), FS)[1]
        far = render.render_scene(scenes.Scene("t", rt_mid=1.0, bass_shift=-2.0), self.parts(), FS)[1]
        self.assertGreater(far["bass_distance_m"], near["bass_distance_m"])
        self.assertLess(far["bass_drr_db"], near["bass_drr_db"])

    def test_pan_gains_keep_power(self):
        for p in (-1.0, -0.3, 0.0, 0.7, 1.0):
            gl, gr = render.pan_gains(p)
            self.assertAlmostEqual(gl * gl + gr * gr, 1.0)
        self.assertGreater(render.pan_gains(1.0)[0], render.pan_gains(1.0)[1])   # +1 = left

    def test_muted_spot_does_not_change_the_main_recording(self):
        for mode in ("recorded", "copies"):
            plain, muted = {}, {}
            a, ta = render.render_scene(scenes.Scene("t", rt_mid=.4, section_mode=mode),
                                       self.parts(), FS, plain)
            b, tb = render.render_scene(scenes.Scene("t", rt_mid=.4, spot="unaligned",
                                       spot_db=-300, section_mode=mode), self.parts(), FS, muted)
            np.testing.assert_array_equal(plain["main"], muted["main"])
            np.testing.assert_allclose(a, b, atol=1e-14)
            self.assertEqual(ta["bass_distance_m"], tb["bass_distance_m"])

    def test_moving_bass_preserves_other_sources_and_performance(self):
        near, far = {}, {}
        render.render_scene(scenes.Scene("t", rt_mid=.4, bass_shift=4, section_mode="copies"),
                            self.parts(), FS, near)
        render.render_scene(scenes.Scene("t", rt_mid=.4, bass_shift=-2, section_mode="copies"),
                            self.parts(), FS, far)
        np.testing.assert_array_equal(near["other_main"], far["other_main"])
        np.testing.assert_array_equal(near["dry_bass"], far["dry_bass"])
        self.assertFalse(np.allclose(near["bass_main"], far["bass_main"]))

    def test_hall_change_preserves_player_layout_and_performance(self):
        original = geometry.positions
        layouts = []
        def capture(*args, **kwargs):
            positions = original(*args, **kwargs)
            layouts.append(positions.copy())
            return positions
        a, b = {}, {}
        with patch.object(geometry, "positions", capture):
            render.render_scene(scenes.Scene("t", rt_mid=.4, section_mode="copies"), self.parts(), FS, a)
            render.render_scene(scenes.Scene("t", rt_mid=.6, section_mode="copies"), self.parts(), FS, b)
        np.testing.assert_array_equal(layouts[0], layouts[2])
        np.testing.assert_array_equal(layouts[1], layouts[3])
        np.testing.assert_array_equal(a["dry_bass"], b["dry_bass"])

    def test_unrelated_sections_do_not_change_bass_realisation(self):
        parts = self.parts()
        a, b = {}, {}
        sc = scenes.Scene("t", rt_mid=.4, section_mode="copies")
        render.render_scene(sc, parts, FS, a)
        render.render_scene(sc, {"brass": parts["vl1"], **parts}, FS, b)
        np.testing.assert_array_equal(a["bass_main"], b["bass_main"])


class Scenes(unittest.TestCase):
    def test_fractional_parameters_do_not_collide(self):
        self.assertNotEqual(scenes.Scene("a", bass_shift=.1).key, scenes.Scene("a", bass_shift=.2).key)
        self.assertNotEqual(scenes.Scene("a", rt_mid=1.91).key, scenes.Scene("a", rt_mid=1.92).key)
    def test_grid_keys_are_unique(self):
        for name in scenes.GRIDS:
            g = scenes.grid(name, ["a", "b"])
            self.assertEqual(len({s.key for s in g}), len(g), name)


class Report(unittest.TestCase):
    def rows(self, seeds=(1,)):
        return [{"scene": scenes.Scene("p", rt_mid=rt, bass_shift=shift, seed=seed).as_dict(),
                 "truth": {"bass_distance_m": 10 - shift, "bass_drr_db": shift},
                 "cues": {"score": rt * 2 - shift}}
                for seed in seeds for rt in (1.4, 1.9, 2.6) for shift in (-2., 2.)]

    def test_hall_comparison_uses_the_same_distance_span(self):
        rows = self.rows()
        before = evaluate.report_hall(rows)
        rows.append({"scene": scenes.Scene("p", bass_shift=4).as_dict(),
                     "truth": {}, "cues": {"score": 1000}})
        self.assertEqual(before, evaluate.report_hall(rows))
        self.assertIn("0.60", "\n".join(before))

    def test_repeated_seeds_are_separate_distance_groups(self):
        rows = []
        for seed in (1, 2):
            for shift in (-2., 0., 2., 4.):
                rows.append({"scene": scenes.Scene("p", bass_shift=shift, seed=seed).as_dict(),
                             "truth": {"bass_distance_m": 10 - shift, "bass_drr_db": shift},
                             "cues": {"score": 10 - shift}})
        self.assertIn("2/2", "\n".join(evaluate.report_distance(rows)))

    def test_duplicate_controls_are_counted_once_and_conflicts_rejected(self):
        rows = self.rows()
        self.assertEqual(evaluate.report(rows), evaluate.report(rows + rows))
        changed = dict(rows[0], cues={"score": 123})
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            evaluate.report(rows + [changed])

    def test_a_cue_that_follows_distance_is_reported_the_right_way(self):
        rows = []
        for setup in ("AB", "ORTF"):
            for shift, dist in ((-2.0, 13.0), (0.0, 11.0), (2.0, 9.0), (4.0, 7.0)):
                sc = scenes.Scene("p", setup=setup, bass_shift=shift).as_dict()
                rows.append({"scene": sc, "truth": {"bass_distance_m": dist, "bass_drr_db": -dist / 2},
                             "cues": {"score": dist / 2, "coh_ratio": 2.0 - dist / 20, "fund_db": 0.0}})
        text = "\n".join(evaluate.report_distance(rows))
        score_line = next(line for line in text.splitlines() if line.startswith("score "))
        self.assertIn("+1.00", score_line)
        self.assertIn("2/2", score_line)
        coh_line = next(line for line in text.splitlines() if line.startswith("coh_ratio "))
        self.assertIn("-1.00", coh_line)
        self.assertTrue(json.dumps(rows))


class Provenance(unittest.TestCase):
    def test_resume_rejects_old_algorithms_and_changed_excerpt_or_input(self):
        algorithm = {"detector": "test", "code": "hash"}
        identity = {"algorithm": algorithm, "seconds": 5, "sources": {"kb": "hash"}}
        row = {"scene": scenes.Scene("p").as_dict(), "provenance": identity}
        cli.validate_resume([row], {"p": identity}, algorithm)
        for changed in (dict(identity, seconds=6), dict(identity, sources={"kb": "new"})):
            with self.assertRaises(ValueError):
                cli.validate_resume([row], {"p": changed}, algorithm)
        with self.assertRaises(ValueError):
            cli.validate_resume([row], {"p": identity}, {"detector": "new"})
        with self.assertRaises(ValueError):
            cli.validate_resume([{"scene": row["scene"]}], {"p": identity}, algorithm)

    def test_source_identity_uses_contents_not_just_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "test_kb_6.mp3"
            p.write_bytes(b"one")
            first = cli.input_identity(tmp, 5, {})
            p.write_bytes(b"two")
            self.assertNotEqual(first, cli.input_identity(tmp, 5, {}))


class FixedSelection(unittest.TestCase):
    def test_fixed_measurement_uses_source_frames_and_survives_mixture_changes(self):
        t = np.arange(FS * 4) / FS
        dry = sum(np.sin(2 * np.pi * 50 * k * t) / k for k in range(1, 7))
        source = np.vstack([dry, dry * .8])
        a = evaluate.fixed_cues({"dry_bass": dry, "bass_main": source, "main": source})
        b = evaluate.fixed_cues({"dry_bass": dry, "bass_main": source,
                                "main": source + np.random.default_rng(1).standard_normal(source.shape) * 10})
        self.assertGreater(a["fixed.frames"], 20)
        self.assertEqual(a["fixed.selection_sha256"], b["fixed.selection_sha256"])
        self.assertEqual(a["fixed.bass.coh_comb"], b["fixed.bass.coh_comb"])
        self.assertGreater(a["fixed.mix.coh_comb"], b["fixed.mix.coh_comb"])


if __name__ == "__main__":
    unittest.main()
