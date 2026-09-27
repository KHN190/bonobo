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
    """skills._has_something_to_store (deposit's precondition): refused when every stack is kept (bag.store_plan)."""

    def test_rows(self):
        from bonobo import skills
        pick = {"id": "minecraft:iron_pickaxe", "count": 1, "damage": 0, "maxDamage": 250}
        # (situation, the bag) → refused?
        rows = [("junk beyond the keep list: something to store", inventory(bone=40), False),
                ("a working pickaxe and junk: the junk", inventory(pick, bone=40), False),
                ("must fail: an empty bag", inventory(), True),
                ("must fail: only a working tool (kept)", inventory(pick), True),
                ("must fail: building blocks within the floor (64 kept)", inventory(cobblestone=64), True)]
        for name, answer, refused in rows:
            with self.subTest(name), mock.patch.object(skills, "Inventory", lambda _a=answer: bag(_a)):
                try:
                    skills._has_something_to_store(None)
                    got = False
                except NotAvailable:
                    got = True
                self.assertEqual(got, refused)


class DragonDead(unittest.TestCase):
    """end.dragon_dead: the dragon gone (or at 0 health) AND the exit portal open."""

    def test_rows(self):
        from bonobo import end
        dragon = lambda hp: {"type": "minecraft:ender_dragon", "health": hp}      # noqa: E731
        part = {"type": "minecraft:ender_dragon"}                                  # a body part: no health
        # (situation, entities near, exit portal open) → dead
        rows = [("gone and the portal open", [], True, True),
                ("health 0, the portal open", [dragon(0)], True, True),
                ("only a body part left (no health), the portal open", [part], True, True),
                ("must fail: alive", [dragon(120)], True, False),
                ("must fail: not seen, the portal closed (out of range is not dead)", [], False, False)]
        for name, near, portal, want in rows:
            with self.subTest(name), mock.patch.object(end, "exit_portal_open", lambda centre=(0, 0), _p=portal: _p):
                self.assertEqual(end.dragon_dead(near), want)


class Reinforced(unittest.TestCase):
    """end._reinforced: every cell bunker.reinforce_cells names for the pit's side is solid."""

    def test_rows(self):
        from bonobo import bunker, end
        pit_x = [(3, 60, 0), None, None, None, 60]          # the pit's first cell east of the centre, its floor y
        pit_z = [(0, 60, -3), None, None, None, 60]         # south of the centre: the z side
        cells_x = bunker.reinforce_cells((1, 0), 60)
        # (situation, pit, the cells standing solid) → reinforced
        rows = [("no pit: not reinforced", None, set(), False),
                ("east side, every cell solid", pit_x, set(cells_x), True),
                ("a z-side pit, its own cells solid", pit_z, set(bunker.reinforce_cells((0, -1), 60)), True),
                ("must fail: one cell still open", pit_x, set(cells_x[1:]), False),
                ("must fail: the other side's cells solid, not this one's", pit_x,
                 set(bunker.reinforce_cells((-1, 0), 60)) - set(cells_x), False)]
        for name, pit, solid, want in rows:
            with self.subTest(name), mock.patch.object(end, "_solid", lambda c, _s=solid: c in _s):
                self.assertEqual(end._reinforced(pit), want)


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


if __name__ == "__main__":
    unittest.main()
