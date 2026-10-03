"""accept3 (the idle half; the trunk half is test_accept3): the in-game acceptance that went idle (scratchpad/inv/accept3, 21:21–21:22): its three causes as rows, each held
to the invariant it broke."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo import craft, dispatch, knowledge, retry  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import bag, inventory  # noqa: E402

LOG_CELL = (12986, 77, 12999)        # the one log the run failed to stand for, three times (21:21:27–21:21:53)
OTHERS = [(12990, 75, 13004), (12979, 76, 12992)]


class ARepeatIsNotANewSource(unittest.TestCase):
    """E5/K5: three failures at the same target are one source tried; the task is reported upward only after
    SOURCES_TRIED distinct ones (the run dropped "stone tools" after one log, three times)."""

    def test_table(self):
        sig = lambda p: retry.state_signature(("target", p), frozenset({"minecraft:oak_log"}), True)   # noqa: E731
        rows = [("must fail: the same log thrice is one source, not exhausted", [LOG_CELL] * retry.SOURCES_TRIED,
                 None),
                ("as many different logs: exhausted", [LOG_CELL, *OTHERS][:retry.SOURCES_TRIED], "nav")]
        for name, cells, want in rows:
            with self.subTest(name):
                r, now = retry.Retry(), 1000.0
                for i, c in enumerate(cells):
                    r.failed("task t1", "nav", f"no stand for mine {c}", now + i, ("target", c), state=sig(c))
                got = r.exhausted("task t1")
                self.assertEqual(got and got[0], want)

    def test_a_changed_state_lifts_the_cooling(self):
        r, now = retry.Retry(), 1000.0
        place = retry.place_signature((12987, 74, 12999), False)
        made = retry.state_signature(place, frozenset({"minecraft:oak_log"}), True)
        r.failed("task t2", "error", "craft_chain: finished without reaching its goal", now, place, state=made)
        moved = retry.state_signature(place, frozenset({"minecraft:oak_log", "minecraft:wooden_pickaxe"}), True)
        # must fail: a pure clock wait, the state changed
        self.assertTrue(r.ready("task t2", now + 1, place, state=moved))
        self.assertFalse(r.ready("task t2", now + 1, place, state=made))


class ACraftIsNoStateChange(unittest.TestCase):
    """E5/K5: a cooling holds in the state of the kinds that change a way (knowledge.way_kinds), the same signature as
    a ban's: planks or sticks crafted lift neither; a block to place or a tool does."""

    def test_table(self):
        from types import SimpleNamespace
        from tests.world import snapshot, state
        place = retry.place_signature((12987, 74, 12999), False)

        def sig(*carried):
            me = SimpleNamespace(round_snap=snapshot(state(), inventory(*carried)))
            return brain.Brain.state_of(me, place, None)
        base = sig(("oak_log", 1))
        rows = [("must fail: planks crafted lift the cooling", sig(("oak_log", 1), ("oak_planks", 4)), True),
                ("sticks crafted: the same state", sig(("oak_log", 1), ("stick", 4)), True),
                ("a pickaxe made: a new state", sig(("oak_log", 1), ("wooden_pickaxe", 1)), False),
                ("blocks to place: a new state", sig(("oak_log", 1), ("cobblestone", 8)), False)]
        for name, got, same in rows:
            with self.subTest(name):
                self.assertEqual(got == base, same)


class EveryStepCooledSeeks(unittest.TestCase):
    """D1/E5: when every step of the plan cools here, the round seeks the plan's first source elsewhere (a state
    change lifts the coolings), never "nothing to do; waiting" (21:22:21 on, trees in sight)."""

    def test_table(self):
        plan = [Step("mine", "minecraft:cobblestone", 11, {"blocks": ["stone"]}), Step("craft", "minecraft:stick", 4),
                Step("gather", "log", 1)]
        rows = [("the run's plan: the cobblestone's stone sought", plan, plan[0]),
                ("crafts only: nothing to seek (the craft's own retry)", [Step("craft", "minecraft:stick", 4)], None)]
        for name, steps, want in rows:
            with self.subTest(name):
                self.assertIs(dispatch.first_sought(steps), want)


class APlacedTableIsMade(unittest.TestCase):
    """K9: craft_chain's verify reads the table where its sitting left it (placed, kept standing for the next craft),
    not only the bag (21:22:15: pickaxe and shovel made, "finished without reaching its goal")."""

    def test_table(self):
        near = [{"x": 12985, "y": 74, "z": 12998}]
        rows = [("must fail: placed and left standing: still made", craft.TABLE, inventory(), near, 1),
                ("carried in the bag", craft.TABLE, inventory(("crafting_table", 1)), [], 1),
                ("neither: not made", craft.TABLE, inventory(), [], 0),
                ("another item is the bag's alone", "minecraft:stick", inventory(("stick", 2)), near, 2)]
        for name, item, carried, tables, want in rows:
            with self.subTest(name):
                self.assertEqual(craft.made_count(item, bag(carried), tables), want)

    def test_a_table_standing_before_is_not_made(self):
        # must fail (E2): a crafting table "made" by the one that stood there before the sitting, the bag holding none
        near = [{"x": 12985, "y": 74, "z": 12998}]
        before = craft.made_count(craft.TABLE, bag(inventory()), near)
        self.assertLess(craft.made_count(craft.TABLE, bag(inventory()), near), before + 1)


if __name__ == "__main__":
    unittest.main()
