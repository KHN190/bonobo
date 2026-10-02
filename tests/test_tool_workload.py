"""planner.speed_up over the work a step makes (cost.work_of): the next tier of each tool kind it uses is made when its
saving on that work (knowledge.work_s over break_ticks) beats making it — the staircase's soil counts (C5)."""
import unittest

from bonobo.planner import NullCost, Planner
from bonobo.knowledge import break_ticks

KIT = {"minecraft:oak_planks": 8, "minecraft:stick": 4, "minecraft:crafting_table": 1}
PICK = [("pickaxe", 3, 1000)]                     # a diamond pickaxe held: the stone is fast already


class Work(NullCost):
    def __init__(self, breaks):
        self.breaks = breaks

    def work_of(self, step):
        return list(self.breaks), []


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
                steps = Planner(dict(KIT), list(PICK), Work(breaks)).plan([("minecraft:coal", 1)])
                made = [s.token for s in steps if s.kind == "craft" and s.token.endswith("_shovel")]
                self.assertEqual(made[0] if made else None, want, [str(s) for s in steps])


class EveryProducerLoaded(unittest.TestCase):
    def test_a_tool_is_plannable_after_one_skill_module(self):
        """knowledge.producers loads every skill module, not only when none is registered yet (must fail: with
        gather imported first, no craft producer — no tool plannable)."""
        import subprocess
        import sys
        code = ("from bonobo import gather; from bonobo import knowledge as k; "
                "print(k.source('minecraft:wooden_shovel') is not None)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=__file__.rsplit("/tests/", 1)[0])
        self.assertEqual(out.stdout.strip(), "True", out.stderr)


if __name__ == "__main__":
    unittest.main()
