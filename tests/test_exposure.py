"""Each action knows what standing there costs; the pool must not guess it.

Exposure is now computed once for the whole candidate — the threat pressure where we stand, times how long the
work takes — which assumes the body sits still under a constant rate for the duration. Neither is true: walking
away sheds the pressure within a second or two, a fight raises it, and mining underground has none of it. The
action is the only thing that knows its own shape.

Red first: an action declares `exposure(state) -> seconds`, and the pool adds them up instead of multiplying.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions, threat  # noqa: E402
from bonobo.solve import Action as _Action  # noqa: E402


def Action(*a, **kw):
    """A column built the way `actions.table` builds them — taught how to price its own exposure.

    Constructing one by hand and expecting it to know about zombies is exactly the coupling this design avoids:
    `solve` is arithmetic, the threat pricing is injected. So the test goes through the same door the planner does.
    """
    return actions.with_exposure(_Action(*a, **kw))

HERE = (0.0, 64.0, 0.0)


def hostile(kind, distance):
    pos = (HERE[0] + float(distance), HERE[1], HERE[2])
    return threat.row(pos, float(threat.MOBS[kind]["reach"]), (0.0, 0.0, 0.0), kind)


def state(**kw):
    base = {"here": HERE, "hp": 20.0, "protection": 0.0, "hazards": [], "sword": 1}
    base.update(kw)
    return base


class ActionsPriceTheirOwnExposure(unittest.TestCase):
    def test_an_action_can_say_what_being_there_costs(self):
        a = Action("mine", {"stone": 1}, 4.0)
        self.assertTrue(hasattr(a, "exposure"), "an action must be able to price its own exposure")
        self.assertEqual(a.exposure(state()), 0.0, "nothing around, nothing to pay")

    def test_standing_work_pays_for_the_whole_time(self):
        a = Action("mine", {"stone": 1}, 10.0)
        with_mob = state(hazards=[hostile("minecraft:zombie", 2)])
        self.assertGreater(a.exposure(with_mob), 0.0)

    def test_leaving_pays_only_until_it_is_out_of_reach(self):
        """The whole point: the pool's flat rate charged the escape for its entire duration, which made running
        away from a zombie look as lethal as fighting it bare-handed."""
        staying = Action("mine", {"stone": 1}, 10.0)
        leaving = Action("evade", {"safe": 1}, 10.0, tag=("threat", "evade"))
        with_mob = state(hazards=[hostile("minecraft:zombie", 2)])
        self.assertLess(leaving.exposure(with_mob), staying.exposure(with_mob))

    def test_longer_standing_work_is_more_exposed(self):
        short = Action("mine", {"stone": 1}, 2.0)
        long = Action("mine", {"stone": 1}, 20.0)
        with_mob = state(hazards=[hostile("minecraft:zombie", 2)])
        self.assertGreater(long.exposure(with_mob), short.exposure(with_mob))


class ThePoolAddsThemUp(unittest.TestCase):
    def test_the_brain_sums_action_exposure_rather_than_multiplying_a_rate(self):
        import inspect
        from bonobo.brain import LiveCost
        src = inspect.getsource(LiveCost.risk_s)
        self.assertIn("exposure", src,
                      "risk must come from the actions' own exposure, not from pressure x duration")


if __name__ == "__main__":
    unittest.main()
