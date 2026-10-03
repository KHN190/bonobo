"""accept_fresh_iron_pickaxe: a crafting table left standing ~17 blocks off was in sight; the plan
took it, but the walk order put "take crafting_table" after "craft wooden_pickaxe" (a craft's station was no dependency
of the order: only what it consumes was), so the craft ran with no table near ("no crafting_table nearby or carried")."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo import craft, planner  # noqa: E402
from bonobo.bench.words.est import scene_cost  # noqa: E402
from bonobo.planner import Step  # noqa: E402


def brought_before(steps):
    """The first step that runs before what brings its station (the executor's own test: craft.recipe_needs_table, a
    smelt's furnace), or a pickaxe for a mine, is had; else None."""
    have = set()
    for s in steps:
        station = ("minecraft:crafting_table" if s.kind == "craft" and craft.recipe_needs_table(s.token)
                   else "minecraft:furnace" if s.kind == "smelt" else None)
        if station and station not in have:
            return s
        if s.kind == "mine" and s.detail.get("tier") and not any(i.endswith("_pickaxe") for i in have):
            return s
        have |= planner._ids_made(s)
    return None


def plan(table_at, logs_at):
    blocks = {(logs_at + x, 64, 0): "oak_log" for x in range(4)} | {(0, 63, z): "stone" for z in range(3, 20)}
    if table_at is not None:
        blocks[(-table_at, 64, 3)] = "crafting_table"
    c = scene_cost({"blocks": blocks, "feet": (0, 64, 0), "slots": [], "mobs": [], "time": 1000})
    return planner.plan_needs(c.snap.inv, [("minecraft:stone_pickaxe", 1)], c)


class AStationComesBeforeItsUse(unittest.TestCase):
    """The held plan runs in order — every step whose recipe works at a station, or that needs a tool, runs after
    the step that brings it, however it is brought (craft, take, withdraw) and wherever the walk order puts sites."""

    def test_plans(self):
        # (situation, table distance or None, logs distance): a table in sight near, far or none; logs near or far
        rows = [("no table: one crafted", None, 5),
                ("a table in use reach", 6, 5),
                ("must fail: a table ~17 off with logs far (taken, ordered after its first use)", 17, 25),
                ("a table ~17 off with logs near", 17, 5),
                ("a table out of a take's worth", 30, 25)]
        for name, table_at, logs_at in rows:
            with self.subTest(name):
                steps = plan(table_at, logs_at)
                self.assertIsNone(brought_before(steps), " → ".join(map(str, steps)))

    def test_step_needs(self):
        """step_needs: (situation, steps in run order, j, the i it must wait for)."""
        table, pick = "minecraft:crafting_table", "minecraft:wooden_pickaxe"
        craft = Step("craft", pick, 1, {"inputs": {"minecraft:oak_planks": 3, "minecraft:stick": 2}, "station": table})
        rows = [("must fail: a 3×3 craft waits for the take that brings its table",
                 [Step("take", table, 1, {}), craft], 1, 0),
                ("a 3×3 craft waits for a withdraw that brings its table",
                 [Step("withdraw", table, 1, {"pos": [1, 64, 1]}), craft], 1, 0),
                ("a smelt waits for the furnace crafted before it",
                 [Step("craft", "minecraft:furnace", 1, {"inputs": {"minecraft:cobblestone": 8}, "station": table}),
                  Step("smelt", "minecraft:iron_ingot", 1, {"input": "minecraft:raw_iron",
                                                            "inputs": {"minecraft:raw_iron": 1},
                                                            "station": "minecraft:furnace"})], 1, 0),
                ("a mine waits for a pickaxe taken from a chest (a tool however brought)",
                 [Step("withdraw", "minecraft:stone_pickaxe", 1, {"pos": [1, 64, 1]}),
                  Step("mine", "minecraft:raw_iron", 1, {"tier": 2})], 1, 0)]
        for name, steps, j, i in rows:
            with self.subTest(name):
                self.assertIn(i, planner.step_needs(steps)[j])


if __name__ == "__main__":
    unittest.main()
