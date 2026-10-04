"""Regression tests for performance optimizations:
1. Crafting batching & station clustering (combining crafts in one table sitting).
2. Async furnace decoupling (no idle waiting at furnace when cooking > 3s).
3. Inventory auto-clear guard (ensuring free slots < 2 triggers drop of low-value trash).
4. Sprint navigation enabled on travel legs.
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain, craft, farming, nav, planner, shapes
from bonobo.bench.words.est import scene_cost
from tests.world import inventory, memory, snapshot, state


class CraftBatchingAndClustering(unittest.TestCase):
    """Craft steps for station tools and furnace are clustered into one sitting."""

    def test_furnace_and_stone_tools_clustered(self):
        c = scene_cost({
            "blocks": {(0, 64, 5): "oak_log", (0, 64, 2): "stone", (0, 64, 3): "iron_ore", (0, 64, 4): "coal_ore"},
            "feet": (0, 64, 0), "slots": [], "mobs": [], "time": 1000
        })
        needs = [["tool", "pickaxe", 1], ["tool", "sword", 1], ["tool", "axe", 1], ["tool", "pickaxe", 2]]
        steps = planner.plan_needs(c.snap.inv, needs, c)
        
        # Verify stone tools and furnace are consecutive
        craft_indices = [i for i, s in enumerate(steps) if s.kind == "craft" and s.token in (
            "minecraft:stone_pickaxe", "minecraft:stone_sword", "minecraft:stone_axe", "minecraft:furnace"
        )]
        self.assertEqual(len(craft_indices), 4)
        # All 4 craft indices must be strictly consecutive
        self.assertEqual(craft_indices, list(range(craft_indices[0], craft_indices[0] + 4)))

        # Verify brain.craft_run batches all 4 crafts together
        first_craft = steps[craft_indices[0]]
        run = brain.craft_run(steps, first_craft)
        self.assertEqual(len(run), 4)
        run_tokens = [s.token for s in run]
        self.assertIn("minecraft:stone_pickaxe", run_tokens)
        self.assertIn("minecraft:furnace", run_tokens)


class AsyncFurnaceDecoupling(unittest.TestCase):
    """Furnace smelting does not stall the bot in place when cooking takes > 3s."""

    def test_furnace_awaitable_short_threshold(self):
        mem = memory()
        now = time.time()
        # Furnace job cooking 3 iron ingots takes 30s
        mem.add_job("furnace", (0, 64, 0), "minecraft:overworld", "minecraft:iron_ingot", 3, now + 30.0, False)
        
        # Should NOT be awaitable at 30s remaining (bot must walk away and do other work)
        self.assertFalse(farming.awaitable(mem, "minecraft:overworld", "minecraft:iron_ingot", now))

        # When almost ready (2s left), should become awaitable
        self.assertTrue(farming.awaitable(mem, "minecraft:overworld", "minecraft:iron_ingot", now + 28.5))

    def test_crop_preserves_longer_await_threshold(self):
        mem = memory()
        now = time.time()
        # Crop job due in 20s (under AWAIT_MAX_S of 45s)
        mem.add_job("crop", (0, 64, 0), "minecraft:overworld", "minecraft:wheat", 1, now + 20.0, False)
        self.assertTrue(farming.awaitable(mem, "minecraft:overworld", "minecraft:wheat", now))


class InventoryAutoClearGuard(unittest.TestCase):
    """Inventory drops low-value clutter when free slots < 2."""

    def test_free_slots_craft_trigger(self):
        # Full bag: 36 slots
        full_slots = [{"slot": i, "id": "minecraft:cobblestone", "count": 64} for i in range(35)]
        snap = snapshot(state(), inventory())
        snap.inv.slots = full_slots
        # 35/36 used -> 1 free slot (< 2)
        self.assertLess(snap.inv.free_slots(), 2)

    @mock.patch("bonobo.craft.make_bag_room")
    def test_sitting_clears_space_when_free_slots_under_two(self, mock_make_room):
        mock_make_room.return_value = [{"slot": 0, "action": "THROW"}]
        # Craft sitting with only 1 free slot
        ctx = mock.Mock()
        ctx.dimension = "minecraft:overworld"
        ctx.policy = mock.Mock(protected=mock.Mock(boxes=()))
        with mock.patch("bonobo.craft.Inventory") as mock_inv_cls:
            inv_mock = mock.Mock()
            inv_mock.free_slots.return_value = 1
            inv_mock.used_slots.return_value = 35
            mock_inv_cls.return_value = inv_mock
            with mock.patch("bonobo.craft.craft_plan", return_value=([], None, {})):
                with mock.patch("bonobo.craft.run_split"):
                    craft._sitting(ctx, [("minecraft:stick", 1)])
                    mock_make_room.assert_called()


class SprintNavigation(unittest.TestCase):
    """Sprint is enabled on travel legs."""

    @mock.patch("bonobo.api.run")
    def test_leg_adds_sprint(self, mock_run):
        mock_run.return_value = {"status": "succeeded"}
        task = {"type": "travel", "x": 10.0, "y": 64.0, "z": 20.0}
        nav._leg(task, "awaits arrival")
        self.assertTrue(task.get("sprint"))


if __name__ == "__main__":
    unittest.main()
