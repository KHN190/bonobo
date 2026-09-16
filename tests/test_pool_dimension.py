"""A goal whose work is in another dimension is a candidate only when a way there is known."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import pool  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_pool import ctx  # noqa: E402


class G:
    feasible = None
    background = False

    def __init__(self, dimension):
        self.dimension = dimension


class OtherDimension(unittest.TestCase):
    def test_the_end_without_a_portal_room_is_refused(self):
        c = ctx(way_into=lambda dim: "no way into the End yet: the portal room comes first"
                if dim == "minecraft:the_end" else None)
        why = pool.goal_reason(G("minecraft:the_end"), [], c, lambda inv: [], "minecraft:the_nether", set())
        self.assertEqual(why, "no way into the End yet: the portal room comes first")

    def test_the_end_with_a_portal_room_is_offered(self):
        c = ctx(way_into=lambda dim: None)
        self.assertIsNone(pool.goal_reason(G("minecraft:the_end"), [], c, lambda inv: [], "minecraft:the_nether", set()))

    def test_own_dimension_never_asks(self):
        c = ctx(way_into=lambda dim: "never")
        self.assertIsNone(pool.goal_reason(G(c.snap.dimension), [], c, lambda inv: [], "minecraft:the_nether", set()))


class HuntingAFighter(unittest.TestCase):
    def test_string_plans_a_sword_before_the_spiders(self):
        from bonobo.planner import Planner, NullCost, runnable
        p = Planner({}, [], NullCost())
        p.need("minecraft:string", 3)
        kinds = [(s.kind, s.token) for s in p.merged()]
        self.assertIn(("craft", "minecraft:stone_sword"), kinds)
        self.assertLess(kinds.index(("craft", "minecraft:stone_sword")), kinds.index(("hunt", "minecraft:string")))
        hunt = next(s for s in p.merged() if s.kind == "hunt")

        class Bare:
            def tools(self, kind):
                return []

            def count(self, tok):
                return 0
        self.assertFalse(runnable(hunt, Bare()), "bare-handed, the hunt is not a step that can start")

    def test_animals_need_no_weapon(self):
        from bonobo.planner import Planner, NullCost
        p = Planner({}, [], NullCost())
        p.need("minecraft:beef", 2)
        self.assertEqual([s.kind for s in p.merged()], ["hunt"])


class Admissible(unittest.TestCase):
    """Refusals, not preferences: things no benefit may buy."""

    class Step:
        def __init__(self, kind="mine", token="stone", est=100, **detail):
            self.kind, self.token, self.est, self.detail = kind, token, est, detail

    def gate(self, step, plan=None, **over):
        from bonobo.data import bare
        goal = type("G", (), {"background": False, "dimension": "minecraft:overworld"})()
        return pool.gate_step(step, goal, plan or [step], ctx(**over), {"craft", "smelt", "mine"},
                              lambda inv: True, bare)

    def test_a_step_inside_a_threat_is_refused_outright(self):
        near = self.Step(pos=(10.0, 64.0, 0.0))
        self.assertIsNone(self.gate(near))
        why = self.gate(near, no_go=[((10.0, 64.0, 0.0), 4.0)])
        self.assertIn("threat", str(why))

    def test_a_step_outside_every_circle_is_fine(self):
        far = self.Step(pos=(100.0, 64.0, 0.0))
        self.assertIsNone(self.gate(far, no_go=[((10.0, 64.0, 0.0), 4.0)]))

    def test_work_that_cannot_finish_before_dark_is_refused_when_the_night_would_catch_us(self):
        long_job = self.Step(est=20000)
        self.assertIsNotNone(self.gate(long_job, has_pickaxe=False))

    def test_the_same_work_is_fine_with_a_way_to_survive_the_night(self):
        long_job = self.Step(est=20000)
        self.assertIsNone(self.gate(long_job, has_pickaxe=False, sheltered=True))
        self.assertIsNone(self.gate(long_job, has_pickaxe=True))


if __name__ == "__main__":
    unittest.main()
