"""Drowning, with reflexes in place, still happened. Two reasons, both in one line of the old `danger()`:

    if state["inWater"] and state["air"] < 120 and not state["onGround"]:

`not onGround` reads as "swimming", but digging underwater puts you on the bottom — on ground, in water, breathing
nothing — and that state was classed as safe all the way to zero air. And 120 ticks is a threshold, not a decision:
what matters is whether the air left covers getting out plus noticing in time.

One table, swept through every reader of it: the clock (`drowning_in`), the verdict inside a task (`drowning`,
`danger`), the verdict between tasks (`hazard.due`, which allows REFLEX_SLACK_S more) and the brain's first layer.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, hazard, retry  # noqa: E402
from bonobo import brain as brainmod  # noqa: E402
from bonobo import perception as P  # noqa: E402
from bonobo.world import Snapshot  # noqa: E402

DRY = {"health": 20, "food": 20, "control": {}, "inWater": False, "air": 300, "onGround": True,
       "dimension": "minecraft:overworld", "blockX": 0, "blockY": 64, "blockZ": 0, "timeOfDay": 2000}
# The water beliefs the table below is computed from (play.toml [water]): 4.0 s to surface, 1.5 s to notice, a floor
# at 120 ticks. Slack = air / 20 − 5.5; inside a task: slack ≤ 0 or air < 120; between tasks: slack ≤ 2.0.
WATER = {"surface_s": 4.0, "reaction_s": 1.5, "air_floor": 120}          # fixture: the water beliefs the clock uses


def wet(air, on_ground=False):
    return {**DRY, "inWater": True, "air": air, "onGround": on_ground}


INF = float("inf")
# (situation, state, slack s, leave now inside a task, danger kind, due between tasks)
ROWS = [
    ("dry land", DRY, INF, False, None, None),
    ("full lungs, swimming", wet(300), 9.5, False, None, None),
    ("air for 8 s: fine inside a task and between", wet(160), 2.5, False, None, None),
    ("air for 7.5 s: between tasks it is time", wet(150), 2.0, False, None, "drowning"),
    ("the floor, one tick above it", wet(120), 0.5, False, None, "drowning"),
    ("under the floor while the clock says 0.45 s", wet(119), 0.45, True, "drowning", "drowning"),
    ("on the bottom, digging: on ground is not safe", wet(90, on_ground=True), -1.0, True, "drowning", "drowning"),
    ("swimming at 90 ticks", wet(90), -1.0, True, "drowning", "drowning"),
    ("no air at all", wet(0), -5.5, True, "drowning", "drowning"),
]


class TheClockAndTheFloor(unittest.TestCase):
    def test_the_table_is_computed_from_these_beliefs(self):
        self.assertEqual({k: hazard._W[k] for k in WATER}, WATER)
        self.assertEqual(hazard.REFLEX_SLACK_S, 2.0)

    def test_every_reader_over_the_table(self):
        for name, st, slack, leave, danger, due in ROWS:
            with self.subTest(name):
                self.assertEqual(P.drowning_in(st), slack)
                self.assertEqual(P.drowning(st), leave)
                self.assertEqual(P.danger(st), danger)
                self.assertEqual(hazard.due(st, buried=False), due)

    def test_the_floor_catches_what_a_wrong_clock_would_miss(self):
        """With surface_s and reaction_s wrongly zero the clock says 5 s of slack at 100 ticks; the floor still fires."""
        saved = dict(hazard._W)
        try:
            hazard._W.update(surface_s=0.0, reaction_s=0.0)
            for air, slack, leave in ((100, 5.0, True), (119, 5.95, True), (120, 6.0, False)):
                with self.subTest(air=air):
                    self.assertEqual(P.drowning_in(wet(air)), slack)
                    self.assertEqual(P.drowning(wet(air)), leave)
        finally:
            hazard._W.update(saved)


class TheBrainAsksTheSameTable(unittest.TestCase):
    """The brain's first layer is `hazard.due`: every row that is due is a rescue before anything else, named by the
    same kind — no second air threshold above it decides first."""

    def test_due_rows_are_rescued_first(self):
        self.assertEqual(api.MODE, "normal")
        for name, st, _slack, _leave, _danger, due in ROWS:
            if due is None:
                continue
            with self.subTest(name):
                b = brainmod.Brain.__new__(brainmod.Brain)
                b.retry, b.place, b.blacklist = retry.Retry(), None, {}
                snap = Snapshot.from_readings(st, {"slots": [], "equipment": {}})
                act = b.decide(snap, ctx=None)
                self.assertEqual((act.layer, act.name), ("L0", f"rescue {due}"))


if __name__ == "__main__":
    unittest.main()
