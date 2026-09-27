"""The fight's wiring, by behaviour: each rule is shown doing its work on controlled input through the real
modules — what the planner picks is what the fight posts, the bunker is dug where the geometry says, the threat
model's arrival times decide, the safe step is the model's, perception bids and interrupts on pressure, the one
answer loop carries a fight, threat rows are differenced in one place. (These replace a scan of who calls whom in
the source: a name being called somewhere is not the rule being kept.)"""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import (bunker, combat_model, end, fight_loop, fight_plan, nav, perception, threat)  # noqa: E402
from tests.test_fight_plan import BOMB, DRAGON, view as dragon_view, state as fight_state  # noqa: E402

INF = float("inf")


class ThePlannerDrivesTheDragon(unittest.TestCase):
    """fight_plan.Fight.plan picks the intent; fight_loop.dragon_answer turns it into what is posted."""

    ROWS = [("sitting, healthy, a bomb window from the pit: the bomb", {}, dict(bomb=BOMB), "bed_bomb"),
            ("sitting, healthy, no bed to bomb with: melee on the dragon", {}, {}, "fight"),
            ("everything refused by the planner: back into cover",
             {"phase": 4, "elapsed": 0.8, "beds": 0, "tunnel": False, "crystals": 0, "obsidian": 0},
             dict(cover=(5, 62, 0)), "evade"),
            ("the dragon dead: nothing posted", {}, dict(dead=True), None)]

    def test_plan_to_post(self):
        fight = fight_plan.Fight()
        for name, st, v, want in self.ROWS:
            with self.subTest(name):
                answer = fight_loop.dragon_answer(fight.plan(fight_state(**st)), dragon_view(**v))
                self.assertEqual(answer.kind if answer else None, want)


class TheBunkerIsDug(unittest.TestCase):
    """bunker.dig_plan: the shaft at the mouth first, then out along the side; feet then head, cell by cell."""

    def test_dig_plan_over_the_sides(self):
        for side in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            with self.subTest(side=side):
                cells = bunker.dig_plan(side, 64)
                self.assertEqual(cells[0], bunker.mouth(side, 64))
                self.assertEqual(cells[::2], bunker.tunnel(side, 64))
                self.assertEqual(cells[1::2], [(x, y + 1, z) for x, y, z in bunker.tunnel(side, 64)])

    def test_no_side_is_no_tunnel(self):
        """Must fail to be a tunnel: a side of (0, 0) digs one column over and over."""
        feet = bunker.dig_plan((0, 0), 64)[::2]
        self.assertEqual(len(set(feet)), 1)


class TheThreatModelDecides(unittest.TestCase):
    """combat_model.tti — the arrival time the fight's veto compares — and the union over threats (min_tti)."""

    ROWS = [("already inside the reach", (1, 0, 0), (0, 0, 0), 0.0),
            ("at rest outside: never", (10, 0, 0), (0, 0, 0), INF),
            ("coming at 7 b/s from 10 with a reach of 3: in 1 s", (10, 0, 0), (-7, 0, 0), 1.0),
            ("going away: never", (10, 0, 0), (7, 0, 0), INF),
            ("coming, but past the horizon (27 s): not now", (30, 0, 0), (-1, 0, 0), INF)]

    def test_tti(self):
        for name, pos, vel, want in self.ROWS:
            with self.subTest(name):
                self.assertEqual(combat_model.tti(pos, vel, (0, 0, 0), 3.0), want)

    def test_the_first_of_several_arrives(self):
        near = ((10, 0, 0), 3.0, (-7, 0, 0))
        far = ((0, 0, 20), 3.0, (0, 0, -1))
        self.assertEqual(combat_model.min_tti((0, 0, 0), [far, near]), 1.0)


class TheSafeStepIsTheModels(unittest.TestCase):
    """nav.safe_destination: every walk's target is kept out of what hurts, by combat_model.best_step."""

    def test_destinations(self):
        here = (0.0, 64.0, 0.0)
        rows = [("nothing around: go there", [], "same"),
                ("a still hazard 20 away: go there", [((20.0, 64.0, 0.0), 3.0, (0.0, 0.0, 0.0))], "same"),
                ("the target inside a small hazard: a step out of it", [(here, 1.5, (0.0, 0.0, 0.0))], "out"),
                ("everything within a step is inside: nowhere (must fail)", [(here, 10.0, (0.0, 0.0, 0.0))], None)]
        for name, hazards, want in rows:
            with self.subTest(name):
                got = nav.safe_destination(here, hazards)
                if want == "same":
                    self.assertEqual(got, here)
                elif want == "out":
                    self.assertNotEqual(got, here)
                    self.assertGreater(math.dist(got, here), 1.5)
                else:
                    self.assertIsNone(got)


def _row(kind, x):
    return threat.row((x, 64.0, 0.0), threat.MOBS[kind]["reach"], (0.0, 0.0, 0.0), kind)


class PerceptionBidsThreats(unittest.TestCase):
    """fight_loop.bid: what perception offers the body for the rows it sees (threat.options → decide)."""

    STATE = {"x": 0.0, "y": 64.0, "z": 0.0, "health": 20, "armor": 15, "sword_tier": 2, "blocks": 0,
             "food_items": 0}
    ROWS = [("nothing seen: nothing offered", [], None),
            ("a zombie 4 off, an iron sword: fight it", [_row("minecraft:zombie", 4.0)], "fight"),
            ("a zombie 40 off: nothing owed yet", [_row("minecraft:zombie", 40.0)], None),
            ("a creeper 4 off: never traded with — away", [_row("minecraft:creeper", 4.0)], "evade")]

    def test_bids(self):
        sstate = threat.price_state(hp=20, armor=15)
        price = lambda dhp: threat.hp_seconds(sstate, dhp)   # noqa: E731
        for name, rows, want in self.ROWS:
            with self.subTest(name), mock.patch.object(fight_loop, "HELD", None):
                got = fight_loop.bid(self.STATE, rows, price, ids=list(range(len(rows))))
                self.assertEqual(got[0].kind if got else None, want)


class PerceptionInterruptsOnPressure(unittest.TestCase):
    """perception.danger: the model's time to die interrupts at full health; low health alone above the floor
    does not."""

    BASE = {"health": 20, "dimension": "minecraft:overworld", "control": {}, "inWater": False, "air": 300,
            "onGround": True}
    ROWS = [("full health, dead in 0.1 s at this pressure: interrupt", {}, 0.1, "hostiles"),
            ("8 hp, nothing pressing (dead in 10 min): no interrupt (must not)", {"health": 8}, 600.0, None),
            ("3 hp: the floor, whatever presses", {"health": 3}, 600.0, "critical_health"),
            ("dead in 0.1 s but swinging already: the fight answers, no interrupt",
             {"control": {"task": {"type": "attack"}}}, 0.1, None)]

    def test_danger(self):
        for name, changes, ttd, want in self.ROWS:
            with self.subTest(name):
                got = perception.danger(dict(self.BASE, **changes), time_to_die=lambda t=ttd: t)
                self.assertEqual(got, want)


class OneLoopCarriesTheFight(unittest.TestCase):
    """fight_loop.carry posts each answer's batch (fight_loop.batch), keeps it while it holds, re-posts on change."""

    A = fight_loop.Answer
    BODY = {"feet": (0, 64, 0), "inv": type("Bag", (), {"count": lambda self, i: 0,
                                                         "offhand": lambda self: "minecraft:air"})(),
            "protected": set(), "state": {"x": 0.0, "y": 64.0, "z": 0.0}}
    ROWS = [("the same fight twice: one attack posted", [A("fight", 7), A("fight", 7)], [["attack"]]),
            ("fight, then away: attack, stopped, a walk", [A("fight", 7), A("evade", (10, 64, 0))],
             [["attack"], "/stop", ["travel"]]),
            ("a walk re-aimed a block off: the same walk", [A("evade", (10, 64, 0)), A("evade", (11, 64, 0))],
             [["travel"]]),
            ("nothing wanted: nothing posted", [], [])]

    def test_passes(self):
        for name, wants, want in self.ROWS:
            posted, left = [], list(wants)

            def answer(a):
                posted.append([t["type"] for t in fight_loop.batch(a, self.BODY)])
                return {"id": len(posted)}
            with self.subTest(name), \
                    mock.patch("bonobo.api.post", side_effect=lambda p, b=None: posted.append(p)), \
                    mock.patch("bonobo.api.get", return_value={"status": "running"}), \
                    mock.patch.object(fight_loop.time, "sleep"):
                list(fight_loop.carry(lambda: left.pop(0) if left else None, answer, lambda: True,
                                      {"done": None, "task_id": None}))
                self.assertEqual(posted, want)


class RowsAreDifferencedOnce(unittest.TestCase):
    """The dragon fight's threat rows are threat.rows's (end._threats): velocity differenced between readings."""

    def zombie(self, x):
        return {"id": 7, "type": "minecraft:zombie", "x": x, "y": 64.0, "z": 0.0}

    def test_velocity_over_readings(self):
        rows = [("first sight: at rest", [(1000.0, 10.0)], (0.0, 0.0, 0.0)),
                ("2 blocks nearer in 1 s", [(1000.0, 12.0), (1001.0, 10.0)], (-2.0, 0.0, 0.0)),
                ("the last reading 3 s old: at rest", [(1000.0, 20.0), (1003.0, 10.0)], (0.0, 0.0, 0.0)),
                ("a neutral enderman: not a row at all", None, None)]
        for name, readings, want in rows:
            with self.subTest(name), mock.patch.dict(end._LAST_SEEN, {}, clear=True):
                if readings is None:
                    got = end._threats([{"id": 9, "type": "minecraft:enderman", "x": 3.0, "y": 64.0, "z": 0.0}],
                                       None, now=1000.0)
                    self.assertEqual(got, [])
                    continue
                for now, x in readings:
                    got = end._threats([self.zombie(x)], None, now=now)
                self.assertEqual(tuple(round(v, 6) for v in got[0][2]), want)


if __name__ == "__main__":
    unittest.main()
