"""The planner's invariants as tables (design-f1 §6, §13): R1 tools by price, D4 a held plan kept unless the fresh
one pays the switch, D7 no cycle (deterministic, a fixpoint), G1 the dragon regressed through the contracts. Each
row's expectation is read off the planner's own prices or the game's tables; each table has must-fail rows."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered: the producing tables)
from bonobo import goals, kernel, skill  # noqa: E402
from bonobo.cost import Cost  # noqa: E402
from bonobo.data import TICKS_PER_S, bare  # noqa: E402
from bonobo.knowledge import tool_item  # noqa: E402
from bonobo.planner import NullCost, Unplannable, plan_needs  # noqa: E402
from tests.world import bag, cost, inventory, snapshot  # noqa: E402

TABLE = [("stick", 2), ("crafting_table", 1)]


def kinds(steps):
    return [(s.kind, s.token) for s in steps]


def made_tools(steps):
    return [bare(s.token) for s in steps if s.kind == "craft" and bare(s.token).rpartition("_")[2] in
            ("pickaxe", "axe", "shovel", "sword", "hoe")]


class R1ToolsByPrice(unittest.TestCase):
    """The fastest plan wins; a carried tool needs nothing bought; equal seconds → the lowest tier; a reserved
    material is never spent; a tool at its last use is no tool."""

    def test_rows(self):
        kit = [("cobblestone", 3), ("iron_ingot", 3)] + TABLE
        # (situation, carried, needs, reserved, the tools the plan makes)
        rows = [("a stone pickaxe carried: cobblestone mined with it, nothing made", [("stone_pickaxe", 1)],
                 [("minecraft:cobblestone", 3)], (), []),
                ("a tier-1 pickaxe asked, stone and iron at hand: the lowest tier (equal seconds)", kit,
                 [("tool", "pickaxe", 1)], (), ["stone_pickaxe"]),
                ("the cobblestone reserved: iron is what is left", kit, [("tool", "pickaxe", 1)],
                 ("minecraft:cobblestone",), ["iron_pickaxe"]),
                ("must fail: a higher tier at equal seconds (planks and cobblestone, any pickaxe asked)",
                 [("oak_planks", 3), ("cobblestone", 3)] + TABLE, [("tool", "pickaxe", 0)], (), ["wooden_pickaxe"]),
                ("must fail: a pickaxe at its last use counted as working",
                 [("stone_pickaxe", 1, 130), ("cobblestone", 3)] + TABLE, [("minecraft:cobblestone", 6)], (),
                 None)]
        for name, carried, needs, reserved, want in rows:
            with self.subTest(name):
                steps = plan_needs(bag(inventory(*carried)), needs, Cost(None, reserved=reserved))
                if want is None:
                    self.assertTrue(any(t.endswith("pickaxe") for t in made_tools(steps)), kinds(steps))
                else:
                    self.assertEqual(made_tools(steps), want)
                for item in reserved:
                    self.assertFalse(any(item in (s.detail.get("inputs") or {}) for s in steps), name)


class D4KeepUnlessItPays(unittest.TestCase):
    """PLAN's held plan, re-priced on the world as it is now, against the fresh plan (kernel.switches): a switch only
    when the gain pays the work thrown away and the estimates' noise; a tie keeps."""

    NEEDS = [("minecraft:cobblestone", 3)]
    SEEN = {"stone": 3, "oak_log": 6}

    def prices(self, before, now):
        held = plan_needs(bag(before), self.NEEDS, cost(snapshot(inv=before), **self.SEEN))
        c = cost(snapshot(inv=now), **self.SEEN)
        fresh = plan_needs(bag(now), self.NEEDS, c)
        staying = sum(c.estimate(s) for s in held) / TICKS_PER_S
        return sum(s.est for s in fresh) / TICKS_PER_S, staying

    def test_rows(self):
        empty, pick = inventory(), inventory(("stone_pickaxe", 1))
        fresh, staying = self.prices(empty, pick)
        gain = staying - fresh
        # (situation, bags before/now, work lost by switching, noise, switch?)
        rows = [("a pickaxe turned up: the fresh plan pays", empty, pick, 0.0, 0.0, True),
                ("must fail: the gain inside the noise", empty, pick, 0.0, gain + 1.0, False),
                ("must fail: the gain spent on the work thrown away", empty, pick, gain, 0.0, False),
                ("must fail: nothing changed: a tie keeps", empty, empty, 0.0, 0.0, False)]
        for name, before, now, lost, noise, want in rows:
            with self.subTest(name):
                f, s = self.prices(before, now)
                self.assertIs(kernel.switches(-f, -s, lost, noise), want)


class D7NoCycle(unittest.TestCase):
    """Same facts, same plan (deterministic); the plan's end state plans nothing more (a fixpoint); a plan never
    makes what it takes apart again."""

    ROWS = [("a stone pickaxe from nothing", [], [("tool", "pickaxe", 1)]),
            ("torches, coal carried", [("coal", 2), ("oak_planks", 4)], [("minecraft:torch", 8)]),
            ("a bucket, ingots carried", [("iron_ingot", 3), ("crafting_table", 1)], [("minecraft:bucket", 1)])]

    def test_deterministic(self):
        for name, carried, needs in self.ROWS:
            with self.subTest(name):
                once = plan_needs(bag(inventory(*carried)), needs, NullCost())
                again = plan_needs(bag(inventory(*carried)), needs, NullCost())
                self.assertEqual(kinds(once), kinds(again))
                self.assertEqual([s.est for s in once], [s.est for s in again])

    def test_fixpoint(self):
        for name, carried, needs in self.ROWS:
            with self.subTest(name):
                steps = plan_needs(bag(inventory(*carried)), needs, NullCost())
                self.assertTrue(plan_needs(bag(inventory(*carried)), needs, NullCost()))   # must fail: nothing to do
                done = [(bare(tool_item(n[1], n[2])), 1) if n[0] == "tool" else (bare(n[0]), n[1]) for n in needs]
                self.assertEqual(plan_needs(bag(inventory(*(carried + done))), needs, NullCost()), [])

    def test_nothing_undone(self):
        # must fail: a step making X from Y and another making Y from X (a recipe cycle chosen)
        for name, carried, needs in self.ROWS:
            with self.subTest(name):
                steps = plan_needs(bag(inventory(*carried)), needs, NullCost())
                made_from = {(bare(s.token), bare(i)) for s in steps for i in (s.detail.get("inputs") or {})}
                self.assertFalse({(a, b) for a, b in made_from if (b, a) in made_from})


class G1TheDragon(unittest.TestCase):
    """state:dragon_dead regressed through the contracts: the End reached through an activated portal, the eyes made
    first; every milestone on the way plannable or refused with its reason (D1)."""

    def test_the_dragon_from_nothing(self):
        steps = plan_needs(bag(inventory()), [("state:dragon_dead", 1)], NullCost())
        sets = [skill.sets_of_step(s) for s in steps]
        self.assertTrue(sets[-1].get("state:dragon_dead"), kinds(steps))
        firsts = [lambda i: steps[i].kind == "craft" and bare(steps[i].token) == "ender_eye",
                  lambda i: steps[i].kind == "activate",
                  lambda i: sets[i].get("dimension") == "minecraft:the_end"]
        order = [next((i for i in range(len(steps)) if first(i)), None) for first in firsts]
        self.assertNotIn(None, order, kinds(steps))
        self.assertEqual(order, sorted(order))

    def test_must_fail_the_dragon_without_the_end(self):
        steps = plan_needs(bag(inventory()), [("state:dragon_dead", 1)], NullCost())
        self.assertTrue(any(skill.sets_of_step(s).get("dimension") == "minecraft:the_end" for s in steps),
                        kinds(steps))

    def test_every_milestone_plans_or_says_why(self):
        for name, needs in goals.MILESTONES.items():
            if not isinstance(needs, list):
                continue
            with self.subTest(name):
                try:
                    steps = plan_needs(bag(inventory()), [tuple(n) for n in needs], NullCost())
                except Unplannable as e:
                    self.assertTrue(str(e).strip(), name)
                else:
                    self.assertTrue(steps, name)


if __name__ == "__main__":
    unittest.main()
