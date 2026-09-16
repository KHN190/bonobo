"""The path from tape to parameters. Declaring numbers unmeasured is honest; this is what makes them stop being so."""
import os
import tempfile
import unittest

from bonobo import combat_model as cm
from bonobo.tools import report


def frame(tick, phase, hp, dragon_hp, breath_on_us=False):
    return {"tick": tick, "dimension": "minecraft:the_end",
            "player": {"pos": {"x": 0, "y": 64, "z": 0}, "vel": {"x": 0, "y": 0, "z": 0}, "hp": hp, "food": 20},
            "dragon": {"present": True, "phase": phase, "health": dragon_hp, "pos": {"x": 0, "y": 70, "z": 0},
                       "head": {"x": 0, "y": 64, "z": 40}, "parts": []},
            "breath": [{"type": "minecraft:area_effect_cloud", "pos": {"x": 0, "y": 64, "z": 0}, "radius": 3.0}]
            if breath_on_us else [],
            "endermen": [], "damage": []}


def tape_with_window(exposed_ticks=10, dragon_lost=45.0):
    frames = [frame(t, 3, 20.0, 200.0) for t in range(10)]
    for i in range(40):
        frames.append(frame(10 + i, 6, 20.0 - 3.0 * i / 39, 200.0 - dragon_lost * i / 39, breath_on_us=i < exposed_ticks))
    frames.append(frame(50, 4, 17.0, 200.0 - dragon_lost))
    return {"name": "t", "gaps": [], "frames": frames}


class Fit(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        import json
        self.path = os.path.join(self.dir, "fight-1.json")
        with open(self.path, "w") as f:
            json.dump(tape_with_window(), f)

    def test_bed_damage_is_fitted_from_windows(self):
        values = report.fit([self.path])
        self.assertAlmostEqual(values["bed_damage"], 45.0, places=0)

    def test_exposure_is_fitted_per_cover_class(self):
        values = report.fit([self.path])
        self.assertIn("exposure_in_cover_s", values)      # 10 ticks = 0.5 s exposed → the "in cover" class
        self.assertAlmostEqual(values["exposure_in_cover_s"], 0.5, places=1)

    def test_no_deaths_means_the_risk_slope_stays_a_guess(self):
        values = report.fit([self.path])
        self.assertNotIn("death_risk_per_exposed_s", values, "a fitted-looking number with no evidence is worse than a guess")

    def test_write_fit_updates_the_config_and_the_unmeasured_list(self):
        cfg = os.path.join(self.dir, "fight.toml")
        with open(cfg, "w") as f:
            f.write('[combat]\nbed_damage = 40.0\nexposure_in_cover_s = 1.0\n'
                    'unmeasured = ["bed_damage", "exposure_in_cover_s", "reaction_s"]\n')
        changed = report.write_fit({"bed_damage": 45.0, "exposure_in_cover_s": 0.5}, path=cfg)
        text = open(cfg).read()
        self.assertEqual(set(changed), {"bed_damage", "exposure_in_cover_s"})
        self.assertIn("bed_damage = 45.0", text)
        self.assertIn('unmeasured = ["reaction_s"]', text)


if __name__ == "__main__":
    unittest.main()


class ThreatShape(unittest.TestCase):
    """notice_r and follow_p, the two numbers `play.toml` declares unmeasured. Offline: synthetic rounds, because
    what is being tested is the estimator, not the world."""

    def setUp(self):
        from bonobo import fit
        self.fit = fit

    def test_the_radius_is_where_being_hurt_stops_being_likely(self):
        close = [(d, True) for d in (2, 3, 5, 6, 9, 11, 13, 15)]
        far = [(d, False) for d in (18, 20, 22, 25, 28, 30, 33, 36)]
        self.assertEqual(self.fit.notice_radius(close + far), 16.0)

    def test_too_few_sightings_is_not_a_number(self):
        self.assertIsNone(self.fit.notice_radius([(2, True), (3, True)]))
        self.assertIsNone(self.fit.follow_fraction([(3.0, 1.0)]))

    def test_the_follow_share_is_the_median_ratio(self):
        self.assertAlmostEqual(self.fit.follow_fraction([(4.0, 2.0)] * 9), 0.5)

    def test_more_pressure_after_is_a_new_mob_not_harder_following(self):
        self.assertAlmostEqual(self.fit.follow_fraction([(2.0, 8.0)] * 9), 1.0)

    def test_sightings_come_out_of_rounds_with_their_outcome(self):
        rounds = [{"t": 0.0, "hp": 20.0, "pos": (0, 0, 0), "near": [{"x": 3, "y": 0, "z": 0}]},
                  {"t": 2.0, "hp": 17.0, "pos": (0, 0, 0), "near": []},
                  {"t": 30.0, "hp": 17.0, "pos": (0, 0, 0), "near": [{"x": 30, "y": 0, "z": 0}]},
                  {"t": 32.0, "hp": 17.0, "pos": (0, 0, 0), "near": []}]
        got = self.fit.sightings_from(rounds, lambda r: r["hp"], lambda r: r["near"],
                                      lambda r: r["pos"], lambda r: r["t"])
        self.assertEqual(got, [(3.0, True), (30.0, False)])

    def test_escapes_pair_the_pressure_before_and_after(self):
        rounds = [{"t": 0.0, "pick": "threat:evade", "p": 4.0},
                  {"t": 3.0, "pick": "mine", "p": 3.0},
                  {"t": 9.0, "pick": "mine", "p": 1.0}]
        got = self.fit.escapes_from(rounds, lambda r: r["pick"], lambda r: r["p"], lambda r: r["t"])
        self.assertEqual(got, [(4.0, 1.0)])
