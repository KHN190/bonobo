"""The planner's speed tools over the work a step makes (cost.work_of): a tool of each kind it uses is an option, made
when the whole plan is cheaper with it — its saving on that work (knowledge.work_s over break_ticks) against making
it; the staircase's soil counts (C5)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo.planner import NullCost, plan_needs  # noqa: E402
from bonobo.knowledge import break_ticks, own_work  # noqa: E402
from tests.world import bag, inventory  # noqa: E402

KIT = [("oak_planks", 8), ("stick", 4), ("crafting_table", 1), ("diamond_pickaxe", 1)]   # the stone is fast already


class Work(NullCost):
    def __init__(self, breaks):
        super().__init__()
        self.breaks = breaks

    def work_of(self, step):
        """The step's own work, then the digging to it (as cost.work_of): the staircase's blocks."""
        own, kills = own_work(step)
        return own + (list(self.breaks) if step.kind == "mine" else []), kills


class ToolByWorkload(unittest.TestCase):
    def test_rows(self):
        soil_save = (break_ticks("dirt", "hand") - break_ticks("dirt", "minecraft:wooden_shovel")) / 20   # 0.35 s
        # (situation, the work's blocks) → the shovel made first?
        rows = [("a staircase through 12 soil, 45 stone: a shovel first (12 × %.2f s > its making)" % soil_save,
                 ["dirt"] * 12 + ["stone"] * 45, "minecraft:wooden_shovel"),
                ("must fail: 4 soil: 1.4 s saved, less than making it", ["dirt"] * 4 + ["stone"] * 45, None),
                ("must fail: only stone: a shovel saves nothing", ["stone"] * 60, None)]
        for name, breaks, want in rows:
            with self.subTest(name):
                steps = plan_needs(bag(inventory(*KIT)), [("minecraft:coal", 1)], Work(breaks))
                made = [s.token for s in steps if s.kind == "craft" and s.token.endswith("_shovel")]
                self.assertEqual(made[0] if made else None, want, [str(s) for s in steps])


class EveryProducerLoaded(unittest.TestCase):
    def test_a_tool_is_plannable_after_one_skill_module(self):
        """knowledge.producers loads every skill module, not only when none is registered yet (must fail: with
        gather imported first, no craft producer — no tool plannable)."""
        import subprocess
        import sys
        code = ("from bonobo import gather; from bonobo import knowledge as k; "
                "print(bool(k.sources('minecraft:wooden_shovel')))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=__file__.rsplit("/tests/", 1)[0])
        self.assertEqual(out.stdout.strip(), "True", out.stderr)


if __name__ == "__main__":
    unittest.main()
