"""Pure functions the offline suite never called (sys.monitoring tracer): a table each, with a must-fail row."""
import unittest
from unittest import mock

from bonobo.api import NotAvailable
from tests.world import bag, inventory


class Route(unittest.TestCase):
    """roads.route: the fastest chain of known legs start → goal, the unknown parts walked at WALK_S a block."""

    def test_rows(self):
        from bonobo import roads
        start, goal = (0, 64, 0), (200, 64, 0)
        fast = {"a": [10, 64, 0], "b": [190, 64, 0], "s": 1.0}      # 180 blocks for 1 s: a road worth taking
        # (situation, known legs) → the waypoints after the start (the goal last)
        rows = [("no road known: straight to the goal", [], [goal]),
                ("a fast road on the way: walked to it, ridden, walked off", [fast], [(10, 64, 0), (190, 64, 0), goal]),
                ("the road's ends within SNAP of start and goal: they are the same points", [
                    {"a": [2, 64, 0], "b": [198, 64, 0], "s": 1.0}], [goal]),
                ("must fail: a road slower than walking is not taken", [dict(fast, s=1000.0)], [goal]),
                ("must fail: a road leading away is not taken", [{"a": [10, 64, 0], "b": [10, 64, 300], "s": 0.1}],
                 [goal])]
        for name, legs, want in rows:
            with self.subTest(name):
                self.assertEqual(roads.route(legs, start, goal), want)


class SomethingToStore(unittest.TestCase):
    """store._has_something_to_store (deposit's precondition): refused when every stack is kept (bag.store_plan)."""

    def test_rows(self):
        from bonobo import store
        pick = {"id": "minecraft:iron_pickaxe", "count": 1, "damage": 0, "maxDamage": 250}
        # (situation, the bag) → refused?
        rows = [("junk beyond the keep list: something to store", inventory(bone=40), False),
                ("a working pickaxe and junk: the junk", inventory(pick, bone=40), False),
                ("must fail: an empty bag", inventory(), True),
                ("must fail: only a working tool (kept)", inventory(pick), True),
                ("must fail: building blocks within the floor (64 kept)", inventory(cobblestone=64), True)]
        for name, answer, refused in rows:
            with self.subTest(name), mock.patch.object(store, "Inventory", lambda _a=answer: bag(_a)):
                try:
                    store._has_something_to_store(None)
                    got = False
                except NotAvailable:
                    got = True
                self.assertEqual(got, refused)


class ReviewPlans(unittest.TestCase):
    """review.plans: each task's latest plan and how often its steps failed or were interrupted, in the window."""

    def test_rows(self):
        from bonobo import review
        now = 10_000.0
        task = {"id": "t1", "goal": "have", "args": {"n": 3}}
        r = lambda t, plan, *outs: {"t": t, "task": task, "plan": plan,          # noqa: E731
                                    "events": [{"outcome": o} for o in outs]}
        # (situation, tape rows) → the report
        rows = [("one round: the plan", [r(now - 60, ["mine", "smelt"])], "- t1 have {'n': 3}: mine → smelt"),
                ("the latest plan wins, trouble counted", [r(now - 90, ["a"], "failed"), r(now - 30, ["b"], "failed",
                                                                                            "interrupted")],
                 "- t1 have {'n': 3}: b (failed ×2, interrupted ×1)"),
                ("no plan yet", [r(now - 10, [])], "- t1 have {'n': 3}: no plan"),
                ("must fail: rounds older than the window are not the queue's now", [r(now - 3600, ["old"])],
                 "- no rounds on the tape"),
                ("must fail: rounds with no task", [{"t": now - 5, "task": None}], "- no rounds on the tape")]
        for name, tape, want in rows:
            with self.subTest(name):
                self.assertEqual(review.plans(tape, 30, now), want)



class SpentCells(unittest.TestCase):
    """gather.spent_cells: a mined vein's notes are retired only for cells a fresh read shows gone."""

    def test_rows(self):
        from bonobo import gather
        ores = ["diamond_ore", "deepslate_diamond_ore"]
        a, b = (0, 60, 0), (1, 60, 0)
        # (situation, sent, the fresh read) → the cells whose notes are spent
        rows = [("broken: air now", {a}, {a: "air"}, [a]),
                ("broken and the other kind of the same ore still there", {a, b},
                 {a: "air", b: "minecraft:deepslate_diamond_ore"}, [a]),
                ("filled with something else since (lava, a block): gone as ore", {a}, {a: "lava"}, [a]),
                ("must fail: sent but still ore — the note kept", {a}, {a: "minecraft:diamond_ore"}, []),
                ("must fail: no fresh read — nothing judged spent", {a}, None, [])]
        for name, sent, read, want in rows:
            with self.subTest(name):
                self.assertEqual(gather.spent_cells(sent, None if read is None else read.get, ores), want)


if __name__ == "__main__":
    unittest.main()
