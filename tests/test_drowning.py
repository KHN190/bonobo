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


# ------------------------------------------------------------------------------------------ where to go to breathe
from bonobo import skills  # noqa: E402
from bonobo.terrain import air_route  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

LO, HI = (-8, 56, -8), (8, 72, 8)
PILLAR_WHY = "no land within reach: a block placed underfoot at the surface"


def ground(top=63):
    """Stone up to `top` over the whole box."""
    return {(x, y, z): "stone" for x in range(-8, 9) for z in range(-8, 9) for y in range(56, top + 1)}


def shaft(top=63, bottom=57):
    """A 1×1 water shaft at x=z=0 from `bottom` to `top` (the rim at top + 1)."""
    return ground(top) | {(0, y, 0): "water" for y in range(bottom, top + 1)}


def pool(x_hi, top=63, bottom=58, bank=63):
    """Water from x=-8 to `x_hi` (all z), a stone shore beyond it up to `bank`."""
    blocks = {(x, y, z): "stone" for x in range(-8, 9) for z in range(-8, 9) for y in range(56, bottom)}
    blocks.update({(x, y, z): "water" for x in range(-8, x_hi + 1) for z in range(-8, 9) for y in range(bottom, top + 1)})
    blocks.update({(x, y, z): "stone" for x in range(x_hi + 1, 9) for z in range(-8, 9) for y in range(bottom, bank + 1)})
    return blocks


class AirRoute(unittest.TestCase):
    """terrain.air_route: the nearest dry cell to stand on, not the water's surface (in a 1-wide shaft the body
    surfaced, sank back and the walker found no path)."""

    # (situation, blocks, head) → (kind, cell, why) or None
    ROWS = [("deep in a flooded 1×1 shaft: the rim beside the top", shaft(), (0, 59, 0),
             ("land", (1, 64, 0), "")),
            ("the shaft's water one below the rim: still the rim", shaft(top=62) | {(0, 63, 0): "air"}, (0, 59, 0),
             ("land", (1, 63, 0), "")),
            ("a lake with a shore to the east: the shore, not the surface above", pool(3), (0, 60, 0),
             ("land", (4, 64, 0), "")),
            ("open water, no shore in reach: the surface, to stand on a block there", pool(8), (0, 60, 0),
             ("pillar", (0, 64, 0), PILLAR_WHY)),
            ("the bank two above the water: not climbable, the surface", pool(3, bank=65), (0, 60, 0),
             ("pillar", (0, 64, 0), PILLAR_WHY)),
            ("water capped by stone: dig the cap", ground(66) | {(0, y, 0): "water" for y in range(58, 64)},
             (0, 59, 0), ("dig", (0, 64, 0), "water capped, no air within reach: dig the cap")),
            ("water to the top of what was read: no way", pool(8, top=72), (0, 60, 0), None)]

    def test_route_over_the_table(self):
        for name, blocks, head, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(air_route(FakeRegion(LO, HI, blocks), head), want)


class Breathed(unittest.TestCase):
    """skills.breathed, the verify of find_air: lungs full and the head out without a break for BREATH_HOLD_S."""

    # (situation, [(t, head under water, air)]) → breathing
    ROWS = [("out 2 s, lungs full", [(0.0, False, 200), (1.0, False, 280), (2.0, False, 300)], True),
            ("out 2 s, lungs not yet full", [(0.0, False, 150), (1.0, False, 230), (2.0, False, 290)], False),
            ("full, out only 1.5 s", [(0.5, False, 290), (1.0, False, 300), (2.0, False, 300)], False),
            ("surfaced, sank back, out again 1 s", [(0.0, False, 300), (1.0, True, 300), (1.5, False, 300),
                                                    (2.5, False, 300)], False),
            ("back under at the last read", [(0.0, False, 300), (2.0, False, 300), (2.5, True, 300)], False),
            ("nothing read", [], False)]

    def test_breathed_over_the_table(self):
        for name, samples, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(skills.breathed(samples), want)



class Surface(unittest.TestCase):
    """L0's drowning rescue is find_air and only find_air: `due` decides when (once), find_air where to. The old
    second branch swam straight up six blocks — to the top of a shaft's column, and back under."""

    # (situation, state) → the hazard due, and the calls its rescue makes
    ROWS = [("head under, air nearly gone", wet(60), "drowning", [("post", "/stop"), "find_air"]),
            ("on the bottom digging, air at the floor", wet(110, on_ground=True), "drowning",
             [("post", "/stop"), "find_air"]),
            ("air for 7 s between tasks (slack ≤ 2)", wet(140), "drowning", [("post", "/stop"), "find_air"]),
            ("full lungs, swimming: nothing due", wet(300), None, []),
            ("dry land: nothing due", DRY, None, [])]

    def test_rescue_over_the_table(self):
        from unittest import mock
        for name, st, due, want in self.ROWS:
            calls = []
            with self.subTest(name), mock.patch.dict(hazard._W, WATER), \
                    mock.patch.dict(hazard.SKILLS, find_air=lambda ctx: calls.append("find_air")), \
                    mock.patch.object(api, "post", side_effect=lambda path, body=None: calls.append(("post", path))), \
                    mock.patch.object(api, "run", side_effect=lambda t, wait=0, awaits=None: calls.append(("run", t["type"]))):
                got = hazard.due(st, buried=False)
                self.assertEqual(got, due)
                if got:
                    hazard.RESCUE[got](None, st)
                self.assertEqual(calls, want)


class Slabs(unittest.TestCase):
    """world.slabs: a box past the jar's one-read limit is read in x-slabs (reach_land asked 49×17×49 at once)."""

    def test_slabs_over_the_table(self):
        from bonobo.world import REGION_MAX, slabs
        rows = [("small: one read", (0, 60, 0), (9, 69, 9), [((0, 60, 0), (9, 69, 9))]),
                ("reach_land's box: 49 wide, 17 high, 49 deep → slabs of 39 then 10",
                 (-24, 58, -24), (24, 74, 24), [((-24, 58, -24), (14, 74, 24)), ((15, 58, -24), (24, 74, 24))]),
                ("corners given backwards: the same box", (9, 69, 9), (0, 60, 0), [((0, 60, 0), (9, 69, 9))]),
                ("one cell", (5, 5, 5), (5, 5, 5), [((5, 5, 5), (5, 5, 5))]),
                ("must fail: one x-face (256×256) is already past a read", (0, 0, 0), (0, 255, 255), ValueError)]
        for name, lo, hi, want in rows:
            with self.subTest(name):
                if want is ValueError:
                    with self.assertRaisesRegex(ValueError, "past the 32768-cell read"):
                        slabs(lo, hi)
                    continue
                got = slabs(lo, hi)
                self.assertEqual(got, want)
                self.assertTrue(all((b[0] - a[0] + 1) * (b[1] - a[1] + 1) * (b[2] - a[2] + 1) <= REGION_MAX
                                    for a, b in got))

if __name__ == "__main__":
    unittest.main()
