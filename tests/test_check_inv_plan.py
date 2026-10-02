"""check/inv/plan.py: each plan invariant on a holding row and a must-fail row, over hand-made ctx (the round fills
the same keys from production: check/round.plan_ctx)."""
import unittest

from bonobo.planner import Step
from check import oracle
from check.facts import of
from check.inv import plan
from check.round import Decision

D = Decision("plan", "task", None, None, (), None, "task t1", ())
F = of()


def step(kind, token, est, count=1, **detail):
    s = Step(kind, token, count, detail)
    s.est = est
    return s


class Bag:
    """A bag with tools: (kind, tier, durability) rows (world.Inventory's tools())."""

    def __init__(self, tools=()):
        self.rows = list(tools)

    def tools(self, kind):
        from bonobo.data import TOOL_MATERIAL_FOR_TIER
        return [(t, d, f"minecraft:{TOOL_MATERIAL_FOR_TIER[t]}_{k}") for k, t, d in self.rows if k == kind]

    def count(self, _item):
        return 0


class Mem:
    def __init__(self, stored=()):
        self.rows = list(stored)

    def stored(self, token, dimension):
        return [r for r in self.rows if r[1].endswith(token.removeprefix("minecraft:"))]


PRICE = {"gather": 80, "mine": 60, "craft": 60, "withdraw": 40}


def price(s):
    return PRICE.get(s.kind, 20)


def fires(got):
    return got is not None and not isinstance(got, oracle.Unchecked)


class Plan(unittest.TestCase):
    # (invariant, ctx, fires?)
    ROWS = [
        ("D6", {"plan": [step("craft", "minecraft:stick", 60)], "price": price}, False),
        ("D6", {"plan": [step("craft", "minecraft:stick", 0)], "price": price}, True),     # must fail: free work
        ("D6", {"plan": [step("craft", "minecraft:stick", 30)], "price": price}, True),    # must fail: priced apart
        ("R1", {"plan": [step("craft", "minecraft:stone_pickaxe", 60)], "inv": Bag(), "task_goal": None}, False),
        # must fail: a stone pickaxe crafted while an iron one works
        ("R1", {"plan": [step("craft", "minecraft:stone_pickaxe", 60)], "inv": Bag([("pickaxe", 2, 200)]),
                "task_goal": None}, True),
        ("R1", {"plan": [step("craft", "minecraft:iron_pickaxe", 60)], "inv": Bag([("pickaxe", 2, 200)]),
                "task_goal": {"goal": "have", "args": {"needs": [["tool", "pickaxe", 2]]}}}, False),   # asked for
        ("R2", {"plan": [step("gather", "minecraft:oak_log", 80, 4)], "price": price, "mem": Mem(),
                "dimension": "minecraft:overworld", "feet": (0, 64, 0)}, False),
        # must fail: logs gathered while a chest holds them, taking cheaper
        ("R2", {"plan": [step("gather", "minecraft:oak_log", 80, 4)], "price": price,
                "mem": Mem([((3, 64, 3), "minecraft:oak_log", 8)]), "dimension": "minecraft:overworld",
                "feet": (0, 64, 0)}, True),
        # the chest's logs already taken by the same plan: none left to take instead
        ("R2", {"plan": [step("withdraw", "minecraft:oak_log", 40, 8, pos=[3, 64, 3]),
                         step("gather", "minecraft:oak_log", 80, 4)], "price": price,
                "mem": Mem([((3, 64, 3), "minecraft:oak_log", 8)]), "dimension": "minecraft:overworld",
                "feet": (0, 64, 0)}, False),
        # must fail: only some taken, the rest still there
        ("R2", {"plan": [step("withdraw", "minecraft:oak_log", 40, 2, pos=[3, 64, 3]),
                         step("gather", "minecraft:oak_log", 80, 4)], "price": price,
                "mem": Mem([((3, 64, 3), "minecraft:oak_log", 8)]), "dimension": "minecraft:overworld",
                "feet": (0, 64, 0)}, True),
        ("R4", {"way": (3.0, 5.0, 3.0)}, False),
        ("R4", {"way": (6.0, 5.0, 3.0)}, True),          # must fail: a dug way taken over a cheaper walk
        ("R4", {"way": (None, 5.0, None)}, True),        # must fail: a way exists, none taken
        ("D4", {"switches": [(10.0, 2.0, 3.0, 1.0, True)], "holds": [("a", "b", "better")]}, False),
        ("D4", {"switches": [(4.0, 2.0, 3.0, 1.0, True)], "holds": [("a", "b", "better")]}, True),   # must fail
        ("D4", {"switches": [], "holds": [("a", "b", "better")]}, True),     # must fail: changed without weighing
        ("D4", {"switches": [], "holds": [("a", "b", "assumption")]}, False),
    ]

    def test_rows(self):
        for inv, ctx, want in self.ROWS:
            with self.subTest(inv=inv, ctx={k: v for k, v in ctx.items() if k != "price"}):
                self.assertEqual(fires(plan.CHECKS[inv](F, D, F, ctx)), want)

    def test_missing_readings_are_said(self):
        for inv in plan.CHECKS:
            with self.subTest(inv=inv):
                self.assertIsInstance(plan.CHECKS[inv](F, D, F, {}), oracle.Unchecked)


class RoundPrices(unittest.TestCase):
    """The round's price of a held step (ctx["price"]) is asked after the round, when the stub no longer answers world
    reads: it must still be the price the plan was made at (C13: a hunt priced after the round lost the mob's
    distance and read the unknown walk)."""

    def test_a_hunt_priced_after_the_round(self):
        import contextlib
        import io
        from check import round as rnd
        f = of(quarry="spider", kit="sword")          # a spider in sight, string queued: hunt it
        with contextlib.redirect_stdout(io.StringIO()):
            _d, _got, ctx = rnd.decide(f, fail_then_again=False)
        steps = ctx.get("plan") or []
        self.assertTrue(any(st.kind == "hunt" for st in steps), [str(st) for st in steps])
        for st in steps:
            with self.subTest(step=str(st)):
                self.assertLessEqual(abs(int(ctx["price"](st)) - int(st.est)), plan.TOL_TICKS)   # must fail before C13


if __name__ == "__main__":
    unittest.main()
