"""S5 inside the plan (F1c): a hunt of a mob that fights back is planned only above the fight line, judged by
brain.fight_line_holds with the weapon the plan holds when the hunt runs (made by an earlier step or carried).
The health of each row is read off the judge itself: the least at which an iron sword holds and a stone one does not."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain, gather, planner  # noqa: E402
from bonobo import cost as cost_mod  # noqa: E402
from bonobo.planner import Unplannable  # noqa: E402
from tests.world import bag, cost, inventory, snapshot, state  # noqa: E402

SPIDER = (None, "minecraft:string", 1, ["minecraft:spider"], False)


def holds(hp, carried):
    return brain.fight_line_holds(gather.hunt.contract, SPIDER, state(health=float(hp)), bag(inventory(*carried)))[0]


SPLIT = next(hp for hp in range(1, 21) if holds(hp, [("iron_sword", 1)]) and not holds(hp, [("stone_sword", 1)]))
TABLE = [("stick", 1), ("crafting_table", 1)]


class HuntAboveTheLine(unittest.TestCase):
    # (situation, health, carried, the steps' kinds and tokens; None: unplannable on the fight line)
    ROWS = [("full health, a stone sword: hunted", 20, [("stone_sword", 1)], [("hunt", "minecraft:string")]),
            ("an iron sword carried holds where stone does not", SPLIT, [("iron_sword", 1)],
             [("hunt", "minecraft:string")]),
            ("the sword the plan makes first is the one judged", SPLIT, [("iron_ingot", 2)] + TABLE,
             [("craft", "minecraft:iron_sword"), ("hunt", "minecraft:string")]),
            ("full health, cobblestone: a stone sword made, hunted", 20, [("cobblestone", 2)] + TABLE,
             [("craft", "minecraft:stone_sword"), ("hunt", "minecraft:string")]),
            # (…, a tuple: what the plan ends with) — the iron is mined and smelted first
            ("must fail: a stone sword carried under the line: an iron one made before the hunt", SPLIT,
             [("stone_sword", 1)], (("craft", "minecraft:iron_sword"), ("hunt", "minecraft:string"))),
            ("must fail: the sword the bag makes is under the line: an iron one made before the hunt", SPLIT,
             [("cobblestone", 2)] + TABLE, (("craft", "minecraft:iron_sword"), ("hunt", "minecraft:string"))),
            ("must fail: no kit clears the line (critical health + a spider's loss above 5): refused, said", 5,
             [("stone_sword", 1)], None),
            ("must fail: a stone sword carried under the line, iron at hand: the plan raises the line itself",
             SPLIT, [("stone_sword", 1), ("iron_ingot", 2)] + TABLE,
             [("craft", "minecraft:iron_sword"), ("hunt", "minecraft:string")])]

    def test_rows(self):
        for name, hp, carried, want in self.ROWS:
            with self.subTest(name):
                inv = inventory(*carried)
                c = cost(snapshot(state(health=float(hp)), inv), spider=10)
                if want is None:
                    with self.assertRaisesRegex(Unplannable, "fight line"):
                        planner.plan_needs(bag(inv), [("minecraft:string", 1)], c)
                else:
                    got = [(s.kind, s.token) for s in planner.plan_needs(bag(inv), [("minecraft:string", 1)], c)]
                    self.assertEqual(tuple(got[-len(want):]) if isinstance(want, tuple) else got, want)


class TheLineHoldsWhenTheRoundIsSpent(unittest.TestCase):
    """The round's steps spent (planner.ROUND_STEPS): a choice takes its first way that holds, never its first way."""

    def test_rows(self):
        # (situation, health, carried, the steps' kinds and tokens; None: unplannable on the fight line)
        rows = [("must fail: the first way (hunt as carried) under the line taken: the next, an iron sword made first",
                 SPLIT, [("stone_sword", 1), ("iron_ingot", 2)] + TABLE,
                 [("craft", "minecraft:iron_sword"), ("hunt", "minecraft:string")]),
                ("must fail: no way holds: a plan anyway, not refused with the line's reason", 5, [("stone_sword", 1)],
                 None)]
        saved = dict(planner.SPENT)
        try:
            for name, hp, carried, want in rows:
                with self.subTest(name):
                    planner.SPENT.update(round=0, steps=planner.ROUND_STEPS)
                    inv = inventory(*carried)
                    c = cost(snapshot(state(health=float(hp)), inv), spider=10)
                    if want is None:
                        with self.assertRaisesRegex(Unplannable, "fight line"):
                            planner.plan_needs(bag(inv), [("minecraft:string", 1)], c)
                    else:
                        got = planner.plan_needs(bag(inv), [("minecraft:string", 1)], c)
                        self.assertEqual([(s.kind, s.token) for s in got], want)
        finally:
            planner.SPENT.update(saved)


class AHeldHuntIsJudgedAgain(unittest.TestCase):
    def test_rows(self):
        inv = inventory(("stone_sword", 1))
        held = planner.plan_needs(bag(inv), [("minecraft:string", 1)], cost(snapshot(state(health=20.0), inv), spider=10))
        # (health now, the held hunt still the incumbent?)
        for hp, want in ((20.0, True), (float(SPLIT), False)):    # must fail: a hunt under the line replayed as the bar
            with self.subTest(hp=hp):
                c = cost(snapshot(state(health=hp), inv), spider=10)
                search = planner.Search(c)
                root = planner.Node(planner.from_bag(bag(inv), None, None, c.reserved, c.facts()), [], [])
                self.assertEqual(search.replay(root, [("minecraft:string", 1)], held) is not None, want)


class PlannedBag(unittest.TestCase):
    """cost.planned_bag: the plan's tools replace the bag's, the rest stays (what the judge reads)."""

    def test_rows(self):
        rows = [("a planned iron sword over a stone one", [("stone_sword", 1), ("cobblestone", 4)], {"sword": 2},
                 {"minecraft:iron_sword": 1, "minecraft:stone_sword": 0, "minecraft:cobblestone": 4}),
                ("nothing planned: no tool", [("stone_sword", 1), ("dirt", 3)], {},
                 {"minecraft:stone_sword": 0, "minecraft:dirt": 3}),
                ("a tool only the plan holds (must fail on a bag without slots)", [], {"pickaxe": 1},
                 {"minecraft:stone_pickaxe": 1})]
        for name, carried, held, want in rows:
            with self.subTest(name):
                got = cost_mod.planned_bag(bag(inventory(*carried)), held)
                self.assertEqual({k: got.count(k) for k in want}, want)


if __name__ == "__main__":
    unittest.main()
