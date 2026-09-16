"""Drowning, with reflexes in place, still happened. Two reasons, both in one line of `danger()`:

    if state["inWater"] and state["air"] < 120 and not state["onGround"]:

`not onGround` reads as "swimming", but digging underwater puts you on the bottom — on ground, in water, breathing
nothing — and that state was classed as safe all the way to zero air. And 120 ticks is a threshold, not a decision:
what matters is whether the air left covers getting out plus noticing in time.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import perception as P  # noqa: E402

DRY = {"health": 20, "food": 20, "control": {}, "inWater": False, "air": 300, "onGround": True}


def wet(air, on_ground=False):
    return {**DRY, "inWater": True, "air": air, "onGround": on_ground}


class TheDeathThatKeptHappening(unittest.TestCase):
    def test_standing_on_the_bottom_is_drowning_too(self):
        self.assertTrue(P.drowning(wet(90, on_ground=True)))
        self.assertEqual(P.danger(wet(90, on_ground=True)), "drowning")

    def test_out_of_the_water_is_never_drowning(self):
        self.assertFalse(P.drowning(DRY))
        self.assertEqual(P.drowning_in(DRY), float("inf"))


class TwoClocks(unittest.TestCase):
    def test_the_computed_clock_fires_before_the_floor(self):
        # Air enough to leave, but not enough to leave AND notice: that gap is where the deaths were.
        slack_gone = int((P._W["surface_s"] + P._W["reaction_s"]) * P.TICKS_PER_S)
        self.assertTrue(P.drowning(wet(slack_gone - 1)))

    def test_the_floor_catches_what_the_clock_would_miss(self):
        # Pretend the clock's constants are wrong (a shallower surface_s): the floor must still fire.
        saved = dict(P._W)
        try:
            P._W["surface_s"], P._W["reaction_s"] = 0.0, 0.0
            self.assertGreater(P.drowning_in(wet(100)), 0.0, "the clock alone would say this is fine")
            self.assertTrue(P.drowning(wet(100)), "the floor is what makes it safe to be wrong")
        finally:
            P._W.update(saved)

    def test_full_lungs_are_not_an_emergency(self):
        self.assertFalse(P.drowning(wet(300)))
        self.assertIsNone(P.danger(wet(300)))


class OneDefinition(unittest.TestCase):
    def test_the_brain_reflex_asks_the_same_function(self):
        import inspect
        from bonobo import brain
        src = inspect.getsource(brain.Brain.reflexes)
        self.assertIn("drowning", src)
        self.assertNotIn('s["air"] < 150', src, "a second air threshold is a second opinion about the same death")


if __name__ == "__main__":
    unittest.main()
