"""Offline tests for the fight planner: constructed states in, intents out. No game, no tape, milliseconds.

These are the tests that decide whether the planner is allowed near a fight. Each one names a way a dragon fight has
actually been lost — starting something that could not be finished, walking into the open with no cover to return to,
standing still because everything was refused — and asserts the planner refuses it for the stated reason.
"""
import unittest

from bonobo import fight_plan as fp


def state(**kw):
    """A fight state with sane defaults: tunnel dug, in cover, healthy, mid sitting phase."""
    base = {
        "phase": 6, "phase_elapsed_s": 0.0,
        "hp": 20.0, "hp_floor": 6.0, "incoming_dps": 0.0,
        "dragon_hp": 200.0, "beds": 6, "obsidian": 0, "crystals_open": 0, "water": True,
        "angry_endermen": 0, "tunnel_ready": True, "bed_placed": True, "reinforced": False,
        "in_cover": True, "exposure_s": 1.0,
    }
    base.update(kw)
    return base


class TimeModel(unittest.TestCase):
    def test_remaining_shrinks_as_the_phase_runs(self):
        early = fp.remaining(6, 0.0)
        late = fp.remaining(6, 4.0)
        self.assertGreater(early, late)

    def test_remaining_is_conditional_not_marginal(self):
        # 4 s into a 4.95 s phase there is about 0.95 s left, not 4.95. Getting this wrong is what starts an action
        # that cannot finish.
        self.assertLess(fp.remaining(6, 4.0), 1.5)

    def test_expired_phase_has_nothing_left(self):
        self.assertEqual(fp.remaining(6, 10.0), 0.0)

    def test_p10_is_pessimistic_against_the_median(self):
        self.assertLessEqual(fp.remaining(0, 0.0, q=0.1), fp.remaining(0, 0.0, q=0.5))

    def test_windows_left_falls_as_the_dragon_does(self):
        self.assertGreater(fp.windows_left(200.0), fp.windows_left(40.0))
        self.assertEqual(fp.windows_left(0.0), 0)


class Veto(unittest.TestCase):
    def test_no_action_that_outlasts_the_phase(self):
        # A bomb is atomic: 0.4 s of work plus 0.4 s back into cover, and half a bomb is a death. Late in a sitting
        # phase there is not room for it, and the refusal must be the time rule rather than a low score.
        ok, why = fp.admissible(state(phase=6, phase_elapsed_s=4.9), fp.ACTIONS["fire_window"])
        self.assertFalse(ok)
        self.assertIn("s left", why)

    def test_interruptible_work_is_judged_by_its_segment(self):
        # Digging takes 6 s and the holding pattern's p10 is 2.0 s, so judging it whole would starve it on every
        # cycle and the planner would stand around doing nothing. One block is 0.8 s and survives interruption.
        s = state(phase=0, tunnel_ready=False, in_cover=False, exposure_s=3.0)
        self.assertEqual(fp.commitment(fp.ACTIONS["dig_tunnel"]), 0.8)
        ok, why = fp.admissible(s, fp.ACTIONS["dig_tunnel"])
        self.assertTrue(ok, why)

    def test_atomic_work_is_judged_whole(self):
        self.assertEqual(fp.commitment(fp.ACTIONS["fire_window"]), fp.ACTIONS["fire_window"]["duration_s"])

    def test_window_refused_without_a_tunnel(self):
        # The safety invariant: never step into reach of something we cannot retreat from.
        ok, why = fp.admissible(state(tunnel_ready=False), fp.ACTIONS["fire_window"])
        self.assertFalse(ok)
        self.assertIn("tunnel", why)

    def test_window_refused_without_beds(self):
        ok, why = fp.admissible(state(beds=0), fp.ACTIONS["fire_window"])
        self.assertFalse(ok)
        self.assertIn("beds", why)

    def test_action_refused_when_the_damage_would_not_fit(self):
        ok, why = fp.admissible(state(hp=8.0, incoming_dps=12.0), fp.ACTIONS["fire_window"])
        self.assertFalse(ok)
        self.assertIn("damage", why)

    def test_phase_restricted_actions(self):
        ok, why = fp.admissible(state(phase=6), fp.ACTIONS["shoot_crystal"])
        self.assertFalse(ok)
        self.assertIn("phase", why)

    def test_retreat_is_never_vetoed(self):
        for s in (state(), state(hp=1.0, tunnel_ready=False, beds=0, phase=0),
                  state(phase=4, phase_elapsed_s=0.8)):
            ok, _ = fp.admissible(s, fp.ACTIONS["retreat"])
            self.assertTrue(ok, "the default action must always survive the veto")


class Planning(unittest.TestCase):
    def test_sitting_phase_fires(self):
        p = fp.plan(state(phase=6, phase_elapsed_s=0.0))
        self.assertEqual(p["intent"], "fire_window")

    def test_late_in_the_window_it_refuses_to_start(self):
        # 4.8 s into a 4.95 s phase: a 0.4 s bomb plus a 0.4 s retreat does not fit, so it must not be started.
        p = fp.plan(state(phase=6, phase_elapsed_s=4.8))
        self.assertNotEqual(p["intent"], "fire_window")
        self.assertIn("fire_window", [n for n, _ in p["rejected"]])

    def test_circling_digs_the_tunnel_first(self):
        p = fp.plan(state(phase=0, tunnel_ready=False, bed_placed=False, in_cover=False, exposure_s=3.0))
        self.assertEqual(p["intent"], "dig_tunnel")

    def test_tunnel_stops_being_worth_it_on_the_last_window(self):
        # The same arithmetic that builds a bunker early declines to build one late: with one window left there is
        # nothing left to amortise it over. No rule says so — the seconds do.
        many = fp.ACTIONS["dig_tunnel"]["benefit"](state(dragon_hp=200.0, tunnel_ready=False, in_cover=False,
                                                         exposure_s=3.0))
        few = fp.ACTIONS["dig_tunnel"]["benefit"](state(dragon_hp=30.0, tunnel_ready=False, in_cover=False,
                                                        exposure_s=3.0))
        self.assertGreater(many, few)

    def test_deadline_is_returned_with_the_intent(self):
        p = fp.plan(state(phase=6, phase_elapsed_s=1.0))
        self.assertGreaterEqual(p["deadline_s"], 0.0)
        self.assertEqual(p["duration_s"], fp.ACTIONS[p["intent"]]["duration_s"])

    def test_always_answers_something(self):
        # A planner that can return "nothing" leaves the agent standing in the open, which is how the bench runs died.
        p = fp.plan(state(phase=4, phase_elapsed_s=0.5, hp=3.0, beds=0, tunnel_ready=False))
        self.assertEqual(p["intent"], "retreat")

    def test_rejections_carry_reasons(self):
        p = fp.plan(state(phase=6, beds=0, tunnel_ready=False))
        reasons = dict(p["rejected"])
        self.assertTrue(all(r for r in reasons.values()), "every refusal must say why")

    def test_zero_benefit_never_beats_retreating(self):
        # Every action that survives the veto but buys nothing must lose to the default. The planner once emptied a
        # water bucket at no endermen, in the open, with a dragon perched, because both scored 0.0.
        p = fp.plan(state(phase=6, tunnel_ready=False, angry_endermen=0, in_cover=False))
        self.assertEqual(p["intent"], "retreat")

    def test_preparation_is_interruptible_too(self):
        # Placing a bed is preparation, not a commitment: the holding pattern's p10 is 2.0 s and placing takes 2 s,
        # so judged whole it would never be allowed while the dragon circles — the one phase it should happen in.
        s = state(phase=0, bed_placed=False, tunnel_ready=True)
        ok, why = fp.admissible(s, fp.ACTIONS["place_bed"])
        self.assertTrue(ok, why)


class Objective(unittest.TestCase):
    def test_cover_is_worth_seconds(self):
        exposed = fp.total_seconds(state(in_cover=False, exposure_s=3.0))
        covered = fp.total_seconds(state(in_cover=True, exposure_s=1.0))
        self.assertLess(covered, exposed)

    def test_a_hurt_dragon_is_closer_to_done(self):
        self.assertLess(fp.total_seconds(state(dragon_hp=40.0)), fp.total_seconds(state(dragon_hp=200.0)))

    def test_firing_reduces_the_estimate(self):
        s = state()
        self.assertGreater(fp.ACTIONS["fire_window"]["benefit"](s), 0.0)


if __name__ == "__main__":
    unittest.main()
