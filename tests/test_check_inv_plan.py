"""check/inv/plan.py oracles over hand-made ctx."""
import unittest

from bonobo import memory
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
    """A bag with tools: (kind, tier, durability)."""

    def __init__(self, tools=(), items=None):
        self.rows = list(tools)
        self.items = items or {}

    def tools(self, kind):
        from bonobo.data import TOOL_MATERIAL_FOR_TIER
        return [(t, d, f"minecraft:{TOOL_MATERIAL_FOR_TIER[t]}_{k}") for k, t, d in self.rows if k == kind]

    def count(self, item):
        from bonobo.data import GROUPS, mid
        ids = GROUPS.get(item, [mid(item)])               # a group counts its members, as world.Inventory.count
        return sum(n for i, n in self.items.items() if mid(i) in ids or i == item)


class Mem:
    """memory.Memory's readers over rows: stored (pos, item, n), stations by block, a home's beds by cell."""

    def __init__(self, stored=(), stations=(), home_beds=()):
        self.rows = list(stored)
        self.data = {"stations": [{"block": b, "pos": [i, 64, 0], "dimension": "minecraft:overworld"}
                                  for i, b in enumerate(stations)]}
        self.home_rows = [{"parts": {"beds": [list(c) for c in home_beds]}}] if home_beds else []

    def homes(self, dimension):
        return self.home_rows

    stations = memory.Memory.stations
    known_stations = memory.Memory.known_stations

    def stored(self, token, dimension):
        return [r for r in self.rows if r[1].endswith(token.removeprefix("minecraft:"))]


PRICE = {"gather": 80, "mine": 60, "craft": 60, "withdraw": 40}


def price(s):
    return PRICE.get(s.kind, 20)


def fires(got):
    return got is not None and not isinstance(got, oracle.Unchecked)


TWO_BEEF = {"minecraft:beef": 2, "coal": 4}
BEEF_IN_ORDER = [step("smelt", "minecraft:cooked_beef", 35, 2, inputs={"minecraft:beef": 2, "coal": 1}),
                 step("hunt", "minecraft:beef", 45, 6, types=["minecraft:cow"]),
                 step("smelt", "minecraft:cooked_beef", 75, 6, inputs={"minecraft:beef": 6, "coal": 1})]
BEEF_MERGED = [step("smelt", "minecraft:cooked_beef", 95, 8, inputs={"minecraft:beef": 8, "coal": 2}),
               step("hunt", "minecraft:beef", 45, 6, types=["minecraft:cow"])]
STONE_PICK = step("mine", "minecraft:cobblestone", 60, blocks=["stone"], tier=1, breaks=1)
IRON_PICK = step("mine", "minecraft:raw_iron", 60, blocks=["iron_ore"], tier=2, breaks=1)


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
        # 64 breaks, 59 uses left: the second pickaxe is worn through, not a detour (task=blocks)
        ("R1", {"plan": [step("craft", "minecraft:wooden_pickaxe", 60),
                         step("mine", "minecraft:cobbled_deepslate", 300, 64, blocks=["deepslate"], tier=0, breaks=64)],
                "inv": Bag([("pickaxe", 0, 59)]), "task_goal": None}, False),
        # must fail: 10 breaks, 59 uses left: the carried one does them
        ("R1", {"plan": [step("craft", "minecraft:wooden_pickaxe", 60),
                         step("mine", "minecraft:cobbled_deepslate", 60, 10, blocks=["deepslate"], tier=0, breaks=10)],
                "inv": Bag([("pickaxe", 0, 59)]), "task_goal": None}, True),
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
        # the candidates the search priced, the chosen first: the fewest seconds, a tie the lowest tier
        ("R1", {"candidates": [("stone", 3.0, [STONE_PICK]), ("iron", 5.0, [IRON_PICK])]}, False),
        ("R2", {"candidates": [("iron", 5.0, [IRON_PICK]), ("stone", 3.0, [STONE_PICK])]}, True),   # must fail: dearer
        ("R4", {"candidates": [("iron", 3.0, [IRON_PICK]), ("stone", 3.0, [STONE_PICK])]}, True),   # must fail: tie, higher tier
        ("R1", {"candidates": [("stone", 3.0, [STONE_PICK]), ("iron", 3.0, [IRON_PICK])]}, False),
        ("D6", {"candidates": [("stick", 3.0, [step("craft", "minecraft:stick", 60)])], "price": price}, False),
        # must fail: the chosen plan's seconds are not its steps' prices
        ("D6", {"candidates": [("stick", 1.0, [step("craft", "minecraft:stick", 60)])], "price": price}, True),
        # S8: the bar against the plan's clock (est ticks)
        ("S8", {"plan": [step("mine", "minecraft:cobblestone", 400)], "food_left_s": 10.0}, True),   # must fail: starves
        ("S8", {"plan": [step("smelt", "minecraft:cooked_beef", 100), step("mine", "minecraft:cobblestone", 400)],
                "food_left_s": 10.0}, False),
        ("S8", {"plan": [step("mine", "minecraft:cobblestone", 400)], "food_left_s": None}, False),   # food carried
        ("R4", {"way": (3.0, 5.0, 3.0)}, False),
        ("R4", {"way": (6.0, 5.0, 3.0)}, True),          # must fail: a dug way taken over a cheaper walk
        ("R4", {"way": (None, 5.0, None)}, True),        # must fail: a way exists, none taken
        ("D4", {"switches": [(10.0, 2.0, 3.0, 1.0, True)], "holds": [("a", "b", "better")]}, False),
        ("D4", {"switches": [(4.0, 2.0, 3.0, 1.0, True)], "holds": [("a", "b", "better")]}, True),   # must fail
        ("D4", {"switches": [], "holds": [("a", "b", "better")]}, True),     # must fail: changed without weighing
        ("D4", {"switches": [], "holds": [("a", "b", "assumption")]}, False),
        ("P4", {"search_steps": 200}, False),
        ("P4", {"search_steps": plan.ROUND_STEPS}, False),                 # boundary: the budget itself
        ("P4", {"search_steps": 34000}, True),                             # must fail: a round searching on (GammaRoundTrip)
        ("P5", {"plan": BEEF_IN_ORDER, "exact_s": 7.75}, False),            # the plan's own 155 ticks
        ("P5", {"plan": BEEF_IN_ORDER, "exact_s": 5.0}, True),             # must fail: the budget cut a faster plan
        ("P5", {"plan": BEEF_IN_ORDER, "exact_s": 5.0, "plan_hand_made": True}, False),
        ("P3", {"plan": BEEF_IN_ORDER, "bound": 150}, False),
        ("P3", {"plan": BEEF_IN_ORDER, "bound": 155}, False),            # boundary: the plan's own price
        ("P3", {"plan": BEEF_IN_ORDER, "bound": 400}, True),             # must fail: the bound above what is paid
        ("P3", {"plan": BEEF_IN_ORDER, "bound": 400, "plan_hand_made": True}, False),   # a hand-made plan: not judged
        # a held plan against the round's chosen one: (held_s, chosen_s, lost_s, switched)
        ("D4", {"plan_switch": (30.0, 10.0, 5.0, True)}, False),
        ("D4", {"plan_switch": (30.0, 28.0, 5.0, True)}, True),     # must fail: the switch throws away more than it saves
        ("D4", {"plan_switch": (30.0, 28.0, 5.0, False)}, False),
        ("D4", {"plan_switch": (30.0, 10.0, 5.0, False)}, True),    # must fail: kept a plan dearer by more than the switch
        ("P2", {"plan": BEEF_IN_ORDER, "inv": Bag(items=TWO_BEEF), "mem": Mem(stations=["furnace"]),
                "dimension": "minecraft:overworld"}, False),
        # must fail: the two smelts merged before the hunt that feeds them (night_first__low 055858)
        ("P2", {"plan": BEEF_MERGED, "inv": Bag(items=TWO_BEEF), "mem": Mem(stations=["furnace"]),
                "dimension": "minecraft:overworld"}, True),
        # the plan_held dimension's hand-made plan (craft_run: short by design): not the planner's, not judged
        ("P2", {"plan": BEEF_MERGED, "inv": Bag(items=TWO_BEEF), "mem": Mem(stations=["furnace"]),
                "dimension": "minecraft:overworld", "plan_hand_made": True}, False),
        # must fail: a seek finds the stone, it does not add one (7 carried, the furnace wants 8)
        ("P2", {"plan": [step("seek", "stone", 600), step("craft", "minecraft:furnace", 60, inputs={"stone": 8})],
                "inv": Bag(items={"stone": 7}), "mem": Mem(stations=["crafting_table"]),
                "dimension": "minecraft:overworld"}, True),
        # must fail: a sleep with no bed carried, made or standing (its contract's station)
        ("P2", {"plan": [step("sleep", "bed", 400)], "inv": Bag(), "mem": Mem(), "dimension": "minecraft:overworld"}, True),
        ("P2", {"plan": [step("sleep", "bed", 400)], "inv": Bag(items={"minecraft:white_bed": 1}), "mem": Mem(),
                "dimension": "minecraft:overworld"}, False),
        ("P2", {"plan": [step("sleep", "bed", 400)], "inv": Bag(), "mem": Mem(stations=["white_bed"]),
                "dimension": "minecraft:overworld"}, False),
        # only the home's bed: a known station all the same (must fail before memory.known_stations)
        ("P2", {"plan": [step("sleep", "bed", 400)], "inv": Bag(), "mem": Mem(home_beds=[(5, 64, 5)]),
                "dimension": "minecraft:overworld"}, False),
        # must fail: a smelt with no furnace held, made or remembered
        ("P2", {"plan": BEEF_IN_ORDER[:1], "inv": Bag(items=TWO_BEEF), "mem": Mem(),
                "dimension": "minecraft:overworld"}, True),
    ]

    def test_rows(self):
        for inv, ctx, want in self.ROWS:
            with self.subTest(inv=inv, ctx={k: v for k, v in ctx.items() if k != "price"}):
                self.assertEqual(fires(plan.CHECKS[inv](F, D, F, ctx)), want)

    def test_missing_readings_are_said(self):
        for inv in plan.CHECKS:
            with self.subTest(inv=inv):
                self.assertIsInstance(plan.CHECKS[inv](F, D, F, {}), oracle.Unchecked)


class StationsDeclared(unittest.TestCase):
    """A contract that works at a block, carried or standing, says so (`station`): the planner and P2 read it."""

    def test_every_station_user_declares_it(self):
        import inspect
        from bonobo import knowledge
        from bonobo.skill import REGISTRY
        knowledge.producers()
        users = [c.name for c in REGISTRY.values() if "Station(ctx" in inspect.getsource(c.fn) and c.station is None]
        self.assertEqual(users, [], "these work at a station they do not declare")
        self.assertEqual(REGISTRY["sleep"].station, "bed")          # must fail: a sleep planned with no bed


class RoundPrices(unittest.TestCase):
    """C13: a held step's price read after the round equals its est."""

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
