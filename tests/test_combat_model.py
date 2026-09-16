"""Offline tests for the combat model: synthetic frames, no game.

The model is what the planner and the controller both believe about the fight, so its arithmetic is checked against
hand-computable cases before any tape is fitted to it. A wrong time-to-impact is not a wrong number, it is a death.
"""
import math
import unittest

from bonobo import combat_model as cm
from bonobo import threat


def frame(tick=0, pos=(0, 64, 0), vel=(0, 0, 0), hp=20, phase=6, dragon=True,
          head=(0, 66, 5), breath=(), endermen=(), damage=()):
    """One tape frame. `vel` is per tick, as the recorder writes it."""
    d = {"present": False}
    if dragon:
        d = {"present": True, "phase": phase, "health": 200.0,
             "pos": {"x": 0, "y": 70, "z": 0},
             "head": {"x": head[0], "y": head[1], "z": head[2]}, "parts": []}
    return {
        "tick": tick, "dimension": "minecraft:the_end",
        "player": {"pos": {"x": pos[0], "y": pos[1], "z": pos[2]},
                   "vel": {"x": vel[0], "y": vel[1], "z": vel[2]},
                   "hp": hp, "food": 20, "onGround": True, "yaw": 0, "pitch": 0},
        "dragon": d,
        "breath": [{"type": "minecraft:area_effect_cloud", "pos": {"x": b[0], "y": b[1], "z": b[2]},
                    "distance": 0, "radius": b[3], "age": 0} for b in breath],
        "endermen": [{"pos": {"x": e[0], "y": e[1], "z": e[2]}, "distance": 0, "angry": e[3]} for e in endermen],
        "damage": [{"amount": a, "hurtTime": 10, "nearest": n} for a, n in damage],
    }


class TimeToImpact(unittest.TestCase):
    def test_standing_still_is_never_hit(self):
        self.assertEqual(cm.tti((0, 64, 0), (0, 0, 0), (0, 64, 10), 3.0), float("inf"))

    def test_already_inside_is_zero(self):
        self.assertEqual(cm.tti((0, 64, 0), (0, 0, 0), (0, 64, 1), 3.0), 0.0)

    def test_walking_into_it(self):
        # 10 blocks away, radius 3, closing at 2 blocks/s → the rim is reached after (10-3)/2 = 3.5 s.
        self.assertAlmostEqual(cm.tti((0, 64, 0), (0, 0, 2), (0, 64, 10), 3.0), 3.5, places=2)

    def test_walking_away(self):
        self.assertEqual(cm.tti((0, 64, 0), (0, 0, -2), (0, 64, 10), 3.0), float("inf"))

    def test_beyond_the_horizon_is_infinite(self):
        # Closing, but it would take 35 s: past the horizon nothing is predictable anyway.
        self.assertEqual(cm.tti((0, 64, 0), (0, 0, 0.2), (0, 64, 10), 3.0, horizon=4.0), float("inf"))


class Threats(unittest.TestCase):
    def test_neutral_enderman_is_not_a_threat(self):
        # The rule that matters most: looking at a neutral enderman is what starts the fight, so the model must not
        # even list one as a hazard to route around.
        f = frame(endermen=[(1, 64, 1, False)], dragon=False)
        self.assertEqual([t[0] for t in cm.threats(f)], [])

    def test_angry_enderman_is_a_threat(self):
        f = frame(endermen=[(1, 64, 1, True)], dragon=False)
        kinds = [t[0] for t in cm.threats(f)]
        self.assertEqual(kinds, ["minecraft:enderman"])

    def test_sorted_soonest_first(self):
        f = frame(pos=(0, 64, 0), vel=(0, 0, 0.1),
                  breath=[(0, 64, 30, 3.0), (0, 64, 2, 3.0)], dragon=False)
        ttis = [t[1] for t in cm.threats(f)]
        self.assertEqual(ttis, sorted(ttis))
        self.assertEqual(ttis[0], 0.0)      # the near cloud already covers us

    def test_head_counts_as_its_own_hazard(self):
        f = frame(head=(0, 64, 2))
        self.assertIn("dragon_head", [t[0] for t in cm.threats(f)])

    def test_exposure_sums_what_we_stand_in(self):
        f = frame(pos=(0, 64, 0), breath=[(0, 64, 1, 3.0)], head=(0, 64, 1))
        self.assertEqual(cm.exposure(f), cm.DEFAULT_DPS["minecraft:area_effect_cloud"]
                         + cm.DEFAULT_DPS["dragon_head"])


class Phases(unittest.TestCase):
    def test_spans_collapse_runs(self):
        frames = [frame(tick=t, phase=6) for t in range(10)] + [frame(tick=t, phase=5) for t in range(10, 30)]
        self.assertEqual(cm.phase_spans(frames), [(6, 0, 9), (5, 10, 29)])

    def test_absent_dragon_is_its_own_span(self):
        frames = [frame(tick=t, dragon=False) for t in range(4)]
        self.assertEqual(cm.phase_spans(frames), [(None, 0, 3)])

    def test_stats_measure_duration_in_seconds(self):
        frames = [frame(tick=t, phase=6) for t in range(20)]      # 20 ticks = 1.0 s
        self.assertEqual(cm.phase_stats(frames)[6]["mean_s"], 1.0)


class Damage(unittest.TestCase):
    def test_fit_groups_by_source(self):
        frames = [frame(tick=0, damage=[(10.0, "minecraft:ender_dragon")]),
                  frame(tick=1, damage=[(6.0, "minecraft:ender_dragon")]),
                  frame(tick=2, damage=[(7.0, "minecraft:enderman")])]
        fit = cm.fit_damage(frames)
        self.assertEqual(fit["minecraft:ender_dragon"]["n"], 2)
        self.assertEqual(fit["minecraft:ender_dragon"]["mean"], 8.0)
        self.assertEqual(fit["minecraft:ender_dragon"]["max"], 10.0)
        self.assertEqual(fit["minecraft:enderman"]["mean"], 7.0)


class Postmortem(unittest.TestCase):
    def test_escape_found_when_one_step_clears_the_cloud(self):
        # One cloud, radius 3, sitting on us; open ground all around → a step out exists.
        frames = [frame(tick=t, hp=20, breath=[(0, 64, 0, 3.0)], dragon=False) for t in range(60)]
        frames.append(frame(tick=60, hp=0, breath=[(0, 64, 0, 3.0)], dragon=False))
        [out] = cm.could_have_lived(frames)
        self.assertIsNotNone(out["escape"])

    def test_no_escape_when_everything_is_covered(self):
        # Clouds blanketing the whole reachable area: the mistake was earlier, in the plan.
        cover = [(x, 64, z, 4.0) for x in (-4, 0, 4) for z in (-4, 0, 4)]
        frames = [frame(tick=t, hp=20, breath=cover, dragon=False) for t in range(60)]
        frames.append(frame(tick=60, hp=0, breath=cover, dragon=False))
        [out] = cm.could_have_lived(frames)
        self.assertIsNone(out["escape"])

    def test_no_death_no_report(self):
        frames = [frame(tick=t, hp=20) for t in range(60)]
        self.assertEqual(cm.could_have_lived(frames), [])



class Windows(unittest.TestCase):
    """The three numbers the planner's value model runs on, measured off a tape rather than assumed."""

    def _fight(self, exposed):
        """A window: 40 ticks of phase 6 during which the player loses 4 hp and the dragon loses 45.

        `exposed` puts a breath cloud on the player for the first half, so exposure is measurable and distinct from
        the window's own length — the whole point of the bunker is to drive those apart.
        """
        out = [frame(tick=t, phase=3, hp=20) for t in range(10)]
        for i in range(40):
            f = frame(tick=10 + i, phase=6, hp=20 - 4.0 * i / 39,
                      breath=[(0, 64, 0, 3.0)] if (exposed and i < 20) else (),
                      head=(0, 64, 40))
            f["dragon"]["health"] = 200.0 - 45.0 * i / 39
            out.append(f)
        out.append(frame(tick=50, phase=4, hp=16))
        return out

    def test_one_window_per_sitting_span(self):
        ws = cm.windows(self._fight(exposed=True))
        self.assertEqual(len(ws), 1)
        self.assertEqual(ws[0]["phase"], 6)

    def test_duration_is_the_span_length(self):
        [w] = cm.windows(self._fight(exposed=True))
        self.assertAlmostEqual(w["duration_s"], 40 * cm.TICK, places=2)

    def test_exposure_is_shorter_than_the_window(self):
        [w] = cm.windows(self._fight(exposed=True))
        self.assertAlmostEqual(w["exposure_s"], 20 * cm.TICK, places=2)
        self.assertLess(w["exposure_s"], w["duration_s"])

    def test_no_hazard_means_no_exposure(self):
        [w] = cm.windows(self._fight(exposed=False))
        self.assertEqual(w["exposure_s"], 0.0)

    def test_costs_and_gains_are_both_recorded(self):
        [w] = cm.windows(self._fight(exposed=True))
        self.assertAlmostEqual(w["hp_lost"], 4.0, places=1)
        self.assertAlmostEqual(w["dragon_hp_lost"], 45.0, places=1)

    def test_summary_estimates_the_windows_a_fight_needs(self):
        # 45 hp a window → a 200 hp dragon takes between four and five. That estimate is what makes an indirect
        # benefit ("this structure saves 2 s of exposure per window") computable at all.
        s = cm.window_summary(self._fight(exposed=True))
        self.assertEqual(s["windows"], 1)
        self.assertAlmostEqual(s["windows_for_200hp"], 4.4, places=1)

    def test_summary_is_none_without_a_window(self):
        self.assertIsNone(cm.window_summary([frame(tick=t, phase=0) for t in range(10)]))

class UnionAndSlack(unittest.TestCase):
    """The safety layer's arithmetic: earliest arrival across all threats, and honest slack.

    Both properties here were absent and the veto passed nearly everything: a static hazard reported inf because
    it never "arrives", and slack was clamped at the horizon so every safe-looking option scored identically.
    """

    def test_a_spot_already_covered_has_no_time(self):
        self.assertEqual(cm.min_tti((0, 64, 0), [((0, 64, 0), 3.0, (0, 0, 0))]), 0.0)

    def test_walking_through_a_static_hazard_counts(self):
        # The hazard sits between us and the destination. It never moves, so an arrival-only test calls the walk
        # safe — which is how a retreat crossed the dragon's reach and died.
        hazards = [((5.0, 64.0, 0.0), 3.0, (0, 0, 0))]
        through = cm.min_tti((10.0, 64.0, 0.0), hazards, here=(0.0, 64.0, 0.0))
        self.assertLess(through, float("inf"), "crossing a static hazard must cost time, not read as safe")

    def test_a_detour_around_it_does_not(self):
        hazards = [((5.0, 64.0, 0.0), 3.0, (0, 0, 0))]
        around = cm.min_tti((0.0, 64.0, 10.0), hazards, here=(0.0, 64.0, 0.0))
        self.assertEqual(around, float("inf"))

    def test_a_closing_hazard_shortens_the_slack(self):
        near_miss = cm.slack_at((0, 64, 0), [((10.0, 64.0, 0.0), 2.0, (-4.0, 0.0, 0.0))], (0, 64, 0))
        far = cm.slack_at((0, 64, 0), [((80.0, 64.0, 0.0), 2.0, (0, 0, 0))], (0, 64, 0))
        self.assertLess(near_miss, far, "something arriving in two seconds cannot score like an empty horizon")

    def test_slack_is_not_clamped(self):
        self.assertEqual(cm.slack_at((0, 64, 0), [((80.0, 64.0, 0.0), 2.0, (0, 0, 0))], (0, 64, 0)),
                         float("inf"))

    def test_the_union_takes_the_earliest(self):
        slow = ((40.0, 64.0, 0.0), 2.0, (-2.0, 0.0, 0.0))
        fast = ((12.0, 64.0, 0.0), 2.0, (-8.0, 0.0, 0.0))
        self.assertLess(cm.min_tti((0, 64, 0), [slow, fast]), cm.min_tti((0, 64, 0), [slow]))

    def test_best_step_leaves_a_covered_spot(self):
        on_us = [((0.0, 64.0, 0.0), 3.0, (0, 0, 0))]
        spot, slack = cm.best_step((0.0, 64.0, 0.0), on_us)
        self.assertGreater(math.dist(spot, (0.0, 64.0, 0.0)), 0.0, "standing still inside a hazard is not a choice")
        self.assertGreater(slack, 0.0)


class Pincer(unittest.TestCase):
    """Two threats that are each escapable and jointly are not.

    This is why the composition is a union and the statistic is the earliest arrival. Reasoning about the most
    dangerous threat alone answers "step away from that one" — and a run died taking exactly that step, straight
    into the other one.
    """

    HERE = (0.0, 64.0, 0.0)

    def test_each_alone_leaves_the_middle_open(self):
        west = [((-4.0, 64.0, 0.0), 3.0, (0, 0, 0))]
        east = [((4.0, 64.0, 0.0), 3.0, (0, 0, 0))]
        self.assertEqual(cm.min_tti(self.HERE, west), float("inf"))
        self.assertEqual(cm.min_tti(self.HERE, east), float("inf"))

    def test_together_they_close_the_line(self):
        pair = [((-4.0, 64.0, 0.0), 3.0, (4.0, 0.0, 0.0)),        # closing from the west
                ((4.0, 64.0, 0.0), 3.0, (-4.0, 0.0, 0.0))]        # and from the east
        self.assertLess(cm.min_tti(self.HERE, pair), float("inf"),
                        "converging threats must show an arrival time; a single-threat test sees none")

    def test_the_escape_is_the_third_direction(self):
        # Closing along the x axis from both sides: the only lasting escape is across it.
        pair = [((-4.0, 64.0, 0.0), 3.0, (4.0, 0.0, 0.0)),
                ((4.0, 64.0, 0.0), 3.0, (-4.0, 0.0, 0.0))]
        spot, slack = cm.best_step(self.HERE, pair)
        self.assertGreater(abs(spot[2]), abs(spot[0]),
                           f"must step across the closing axis, not along it; chose {spot}")
        self.assertGreater(slack, 0.0)

    def test_a_static_pair_covering_us_still_finds_the_way_out(self):
        pair = [((-2.0, 64.0, 0.0), 3.0, (0, 0, 0)), ((2.0, 64.0, 0.0), 3.0, (0, 0, 0))]
        spot, slack = cm.best_step(self.HERE, pair)
        self.assertGreater(math.dist(spot, self.HERE), 0.0, "standing inside both is never the answer")


class SeveralFutures(unittest.TestCase):
    """Waymo's rule: predict several trajectories per threat and be safe against all of them."""

    def test_a_still_enderman_is_assumed_able_to_pursue(self):
        h = threat.row((10.0, 64.0, 0.0), 3.0, (0.0, 0.0, 0.0), "minecraft:enderman")
        kinds = cm.hypotheses(h, here=(0.0, 64.0, 0.0))
        self.assertEqual(len(kinds), 3, "continue, stop, pursue")
        self.assertTrue(any(v[2][0] < 0 for v in kinds), "one future must come toward us")

    def test_a_cloud_does_not_get_a_pursuit_future(self):
        h = threat.row((10.0, 64.0, 0.0), 3.0, (0.0, 0.0, 0.0), "minecraft:area_effect_cloud")
        self.assertEqual(len(cm.hypotheses(h, here=(0.0, 64.0, 0.0))), 2)

    def test_safety_is_the_worst_future(self):
        # Walking away at 4 b/s: the "continue" future never arrives, the "pursue" future does. best_step must
        # score the spot by the pursuing one.
        here = (0.0, 64.0, 0.0)
        away = [threat.row((6.0, 64.0, 0.0), 3.0, (4.0, 0.0, 0.0), "minecraft:enderman")]
        raw_only = cm.slack_at(here, away, here)
        expanded = cm.slack_at(here, cm.expand(away, here), here)
        self.assertEqual(raw_only, float("inf"))
        self.assertLess(expanded, float("inf"), "a threat that could turn on us is not 'never arriving'")

    def test_best_step_uses_the_expanded_set(self):
        here = (0.0, 64.0, 0.0)
        away = [threat.row((6.0, 64.0, 0.0), 3.0, (4.0, 0.0, 0.0), "minecraft:enderman")]
        spot, slack = cm.best_step(here, away)
        # A straight pursuer is best escaped sideways, not backwards; what must hold is "better than standing here"
        # and "not toward it".
        self.assertGreater(slack, cm.slack_at(here, cm.expand(away, here), here))
        self.assertLessEqual(spot[0], 0.0 + 1e-9, f"must not step toward it; chose {spot}")


if __name__ == "__main__":
    unittest.main()
