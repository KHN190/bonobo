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
from tests.world import FakeRegion, bag, inventory, memory, snapshot, state


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
                with mock.patch("bonobo.craft.run_split"), mock.patch("bonobo.craft.close_screen"):
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


class ParkourNavigation(unittest.TestCase):
    """Parkour continuous movement: 1-block ledge clearance, gap jumping without placing blocks, and sprint-jump transit."""

    def test_can_step_up(self):
        blocks = {
            (0, 63, 0): "stone", (0, 64, 0): "air", (0, 65, 0): "air", (0, 66, 0): "air",
            (1, 63, 0): "stone", (1, 64, 0): "stone", (1, 65, 0): "air", (1, 66, 0): "air",
        }
        reg = FakeRegion((-2, 60, -2), (4, 70, 2), blocks)
        self.assertTrue(nav.can_step_up(reg, (0, 64, 0), (1, 0)))

        # 2-block ledge cannot be stepped up
        blocks[(1, 65, 0)] = "stone"
        reg2 = FakeRegion((-2, 60, -2), (4, 70, 2), blocks)
        self.assertFalse(nav.can_step_up(reg2, (0, 64, 0), (1, 0)))

        # Low ceiling blocks step up
        blocks[(1, 65, 0)] = "air"
        blocks[(0, 66, 0)] = "stone"
        reg3 = FakeRegion((-2, 60, -2), (4, 70, 2), blocks)
        self.assertFalse(nav.can_step_up(reg3, (0, 64, 0), (1, 0)))

    def test_can_gap_jump(self):
        # 1-block gap: x=1 has air floor at y=63, x=2 has solid stone floor
        blocks = {
            (0, 63, 0): "stone", (0, 64, 0): "air", (0, 65, 0): "air", (0, 66, 0): "air",
            (1, 63, 0): "air", (1, 64, 0): "air", (1, 65, 0): "air", (1, 66, 0): "air",
            (2, 63, 0): "stone", (2, 64, 0): "air", (2, 65, 0): "air", (2, 66, 0): "air",
        }
        reg = FakeRegion((-2, 60, -2), (4, 70, 2), blocks)
        self.assertTrue(nav.can_gap_jump(reg, (0, 64, 0), (1, 0), gap_len=1))

        # 2-block gap: x=1, x=2 are air floors, x=3 is stone
        blocks[(2, 63, 0)] = "air"
        blocks.update({(3, 63, 0): "stone", (3, 64, 0): "air", (3, 65, 0): "air", (3, 66, 0): "air"})
        reg_gap2 = FakeRegion((-2, 60, -2), (5, 70, 2), blocks)
        self.assertTrue(nav.can_gap_jump(reg_gap2, (0, 64, 0), (1, 0), gap_len=2, food=20))
        # Food < 6 cannot sprint -> cannot jump 2-block gap
        self.assertFalse(nav.can_gap_jump(reg_gap2, (0, 64, 0), (1, 0), gap_len=2, food=5))

        # 3-block gap cannot be jumped
        self.assertFalse(nav.can_gap_jump(reg_gap2, (0, 64, 0), (1, 0), gap_len=3))

    def test_parkour_way_clears_ledge(self):
        # (0, 64, 0) -> (2, 65, 0) over a 1-block ledge at x=1
        blocks = {
            (0, 63, 0): "stone", (0, 64, 0): "air", (0, 65, 0): "air", (0, 66, 0): "air",
            (1, 63, 0): "stone", (1, 64, 0): "stone", (1, 65, 0): "air", (1, 66, 0): "air",
            (2, 64, 0): "stone", (2, 65, 0): "air", (2, 66, 0): "air", (2, 67, 0): "air",
        }
        reg = FakeRegion((-2, 60, -2), (5, 70, 2), blocks)
        tasks, why = nav.parkour_way(reg, (0, 64, 0), (2, 65, 0))
        self.assertIsNone(why)
        self.assertTrue(any(t.get("type") == "goto" and t.get("y") == 65 for t in tasks))
        # 0 mined blocks, 0 placed blocks
        self.assertEqual([t for t in tasks if t["type"] in ("mine", "place")], [])

    def test_parkour_way_clears_gap(self):
        # (0, 64, 0) -> (2, 64, 0) over 1-block gap at x=1
        blocks = {
            (0, 63, 0): "stone", (0, 64, 0): "air", (0, 65, 0): "air", (0, 66, 0): "air",
            (1, 63, 0): "air", (1, 64, 0): "air", (1, 65, 0): "air", (1, 66, 0): "air",
            (2, 63, 0): "stone", (2, 64, 0): "air", (2, 65, 0): "air", (2, 66, 0): "air",
        }
        reg = FakeRegion((-2, 60, -2), (4, 70, 2), blocks)
        tasks, why = nav.parkour_way(reg, (0, 64, 0), (2, 64, 0))
        self.assertIsNone(why)
        # Active input leap + goto landing at x=2, y=64
        types = [t["type"] for t in tasks]
        self.assertIn("input", types)
        self.assertIn("goto", types)
        goto_task = next(t for t in tasks if t["type"] == "goto")
        self.assertEqual((goto_task["x"], goto_task["y"], goto_task["z"]), (2, 64, 0))
        self.assertTrue(goto_task.get("sprint"))

    def test_plan_way_prefers_parkour_over_bridging_for_1_block_gap(self):
        blocks = {
            (0, 63, 0): "stone", (0, 64, 0): "air", (0, 65, 0): "air", (0, 66, 0): "air",
            (1, 63, 0): "air", (1, 64, 0): "air", (1, 65, 0): "air", (1, 66, 0): "air",
            (2, 63, 0): "stone", (2, 64, 0): "air", (2, 65, 0): "air", (2, 66, 0): "air",
        }
        reg = FakeRegion((-2, 60, -2), (4, 70, 2), blocks)
        inv = bag(inventory({"id": "minecraft:cobblestone", "count": 64}))
        steps, why, secs = nav.plan_way(reg, (0, 64, 0), (2, 64, 0), "stand", inv, ())
        self.assertIsNone(why)
        # Does NOT place a cobblestone to bridge 1-block gap: jumps it!
        places = [t for t in steps if t["type"] == "place"]
        self.assertEqual(places, [])

    def test_sprint_jump_transit_tasks(self):
        # Long open distance >= 6 blocks: sprint-jump input chain
        tasks = nav.sprint_jump_transit_tasks((0, 64, 0), (10, 64, 0), min_dist=6.0, food=20)
        types = [t["type"] for t in tasks]
        self.assertIn("look", types)
        self.assertIn("input", types)
        self.assertIn("goto", types)
        input_task = next(t for t in tasks if t["type"] == "input")
        self.assertEqual(input_task["keys"], ["forward", "sprint", "jump"])

        # Short distance: simple sprint goto
        short_tasks = nav.sprint_jump_transit_tasks((0, 64, 0), (3, 64, 0), min_dist=6.0, food=20)
        self.assertEqual(len(short_tasks), 1)
        self.assertEqual(short_tasks[0]["type"], "goto")
        self.assertTrue(short_tasks[0].get("sprint"))

    def test_parkour_way_step_down_safe(self):
        # 3-block vertical drop from (0, 64, 0) to (2, 61, 0) without stairs
        blocks = {
            (0, 63, 0): "stone", (0, 64, 0): "air", (0, 65, 0): "air",
            (1, 60, 0): "stone", (1, 61, 0): "air", (1, 62, 0): "air", (1, 63, 0): "air", (1, 64, 0): "air",
            (2, 60, 0): "stone", (2, 61, 0): "air", (2, 62, 0): "air",
        }
        reg = FakeRegion((-2, 58, -2), (4, 70, 2), blocks)
        tasks, why = nav.parkour_way(reg, (0, 64, 0), (2, 61, 0))
        self.assertIsNone(why)
        types = [t["type"] for t in tasks]
        self.assertIn("input", types)
        # Verify descent input holds forward without jumping to prevent extra fall damage
        input_task = next(t for t in tasks if t["type"] == "input")
        self.assertEqual(input_task["keys"], ["forward"])
        self.assertTrue(any(t.get("type") == "goto" and t.get("y") == 61 for t in tasks))

    def test_parkour_way_water_drop(self):
        # 6-block vertical drop into water from (0, 66, 0) to (2, 60, 0)
        blocks = {
            (0, 65, 0): "stone", (0, 66, 0): "air", (0, 67, 0): "air",
            (1, 59, 0): "water", (1, 60, 0): "air", (1, 61, 0): "air", (1, 62, 0): "air",
            (1, 63, 0): "air", (1, 64, 0): "air", (1, 65, 0): "air", (1, 66, 0): "air",
            (2, 59, 0): "water", (2, 60, 0): "air", (2, 61, 0): "air",
        }
        reg = FakeRegion((-2, 58, -2), (4, 70, 2), blocks)
        # Without water bucket: refused (> 3 blocks)
        _, why_no_water = nav.parkour_way(reg, (0, 66, 0), (2, 60, 0), has_water=False)
        self.assertIsNotNone(why_no_water)

        # With water bucket: cleared via deep drop
        tasks, why = nav.parkour_way(reg, (0, 66, 0), (2, 60, 0), has_water=True)
        self.assertIsNone(why)
        self.assertTrue(any(t.get("type") == "goto" and t.get("y") == 60 for t in tasks))

    def test_parkour_course_full(self):
        # Full course: 1-block gap (x=1), 1-block ledge (x=3), 2-block drop (x=5), 2-block gap (x=7..8)
        blocks = {
            (0, 63, 0): "stone", (0, 64, 0): "air", (0, 65, 0): "air",
            # gap at x=1
            (1, 63, 0): "air", (1, 64, 0): "air", (1, 65, 0): "air",
            (2, 63, 0): "stone", (2, 64, 0): "air", (2, 65, 0): "air",
            # ledge at x=3
            (3, 64, 0): "stone", (3, 65, 0): "air", (3, 66, 0): "air",
            (4, 64, 0): "stone", (4, 65, 0): "air", (4, 66, 0): "air",
            # drop to y=63 at x=5
            (5, 62, 0): "stone", (5, 63, 0): "air", (5, 64, 0): "air", (5, 65, 0): "air",
            (6, 62, 0): "stone", (6, 63, 0): "air", (6, 64, 0): "air",
            # 2-block gap at x=7, 8
            (7, 62, 0): "air", (7, 63, 0): "air", (7, 64, 0): "air",
            (8, 62, 0): "air", (8, 63, 0): "air", (8, 64, 0): "air",
            (9, 62, 0): "stone", (9, 63, 0): "air", (9, 64, 0): "air",
            (10, 62, 0): "stone", (10, 63, 0): "air", (10, 64, 0): "air",
        }
        reg = FakeRegion((-2, 55, -2), (12, 70, 2), blocks)
        tasks, why = nav.parkour_way(reg, (0, 64, 0), (10, 63, 0))
        self.assertIsNone(why)
        self.assertEqual([t for t in tasks if t["type"] in ("mine", "place")], [])
        self.assertTrue(any(t.get("type") == "goto" and (t.get("x"), t.get("y")) == (10, 63) for t in tasks))

    def test_parkour_master_course(self):
        # Unified grand route linking 1-gap, 1-ledge, 2-gap, 3-drop, 6-water-drop in sequence
        # Fitted cleanly inside arena BOX x in [-8, 14], y in [-10, 4]
        blocks = {
            # Start run x=-7..-5 (stone at y=-1, air y=0..2)
            **{(x, -1, 0): "stone" for x in range(-7, -4)},
            **{(x, y, 0): "air" for x in range(-7, -4) for y in (0, 1, 2)},
            # 1-block gap at x=-4
            **{(-4, y, 0): "air" for y in range(-4, 3)},
            # Landing x=-3..-2
            **{(x, -1, 0): "stone" for x in (-3, -2)},
            **{(x, y, 0): "air" for x in (-3, -2) for y in (0, 1, 2)},
            # Ledge x=-1..1 (stone at y=0, standing at y=1)
            **{(x, 0, 0): "stone" for x in range(-1, 2)},
            **{(x, y, 0): "air" for x in range(-1, 2) for y in (1, 2, 3)},
            # 2-block gap at x=2, 3
            **{(x, y, 0): "air" for x in (2, 3) for y in range(-4, 4)},
            # Landing x=4..5 (stone at y=0, standing at y=1)
            **{(x, 0, 0): "stone" for x in (4, 5)},
            **{(x, y, 0): "air" for x in (4, 5) for y in (1, 2, 3)},
            # 3-block safe drop at x=6..7 (stone at y=-3, standing at y=-2)
            **{(x, -3, 0): "stone" for x in (6, 7)},
            **{(x, y, 0): "air" for x in (6, 7) for y in range(-2, 4)},
            # 6-block cliff drop into water pool at x=8..9 (water at y=-9, standing at y=-8)
            **{(x, -9, 0): "water" for x in (8, 9)},
            **{(x, y, 0): "air" for x in (8, 9) for y in range(-8, 3)},
            # Finish run x=10..13 (stone at y=-9, standing at y=-8)
            **{(x, -9, 0): "stone" for x in range(10, 14)},
            **{(x, y, 0): "air" for x in range(10, 14) for y in (-8, -7, -6)},
        }
        reg = FakeRegion((-10, -12, -2), (16, 6, 2), blocks)
        tasks, why = nav.parkour_way(reg, (-7, 0, 0), (12, -8, 0), has_water=True)
        self.assertIsNone(why)
        self.assertEqual([t for t in tasks if t["type"] in ("mine", "place")], [])
        self.assertTrue(any(t.get("type") == "goto" and (t.get("x"), t.get("y")) == (12, -8) for t in tasks))

    def test_can_corner_cut(self):
        # Open corner from (0, 64, 0) to (1, 64, 1): corner cells (1, 0) and (0, 1) are air
        blocks = {
            (0, 63, 0): "stone", (0, 64, 0): "air", (0, 65, 0): "air",
            (1, 63, 1): "stone", (1, 64, 1): "air", (1, 65, 1): "air",
            (1, 63, 0): "stone", (1, 64, 0): "air", (1, 65, 0): "air",
            (0, 63, 1): "stone", (0, 64, 1): "air", (0, 65, 1): "air",
        }
        reg = FakeRegion((-1, 60, -1), (3, 70, 3), blocks)
        self.assertTrue(nav.can_corner_cut(reg, (0, 64, 0), (1, 1)))

        # Blocked corner: cell (1, 64, 0) is a solid stone wall
        blocked_blocks = dict(blocks)
        blocked_blocks[(1, 64, 0)] = "stone"
        reg_blocked = FakeRegion((-1, 60, -1), (3, 70, 3), blocked_blocks)
        self.assertFalse(nav.can_corner_cut(reg_blocked, (0, 64, 0), (1, 1)))

    def test_can_dynamic_bridge(self):
        # 3-block gap at x=1..3 between x=0 and x=4
        blocks = {
            (0, 63, 0): "stone", (0, 64, 0): "air", (0, 65, 0): "air",
            (1, 63, 0): "air", (1, 64, 0): "air", (1, 65, 0): "air",
            (2, 63, 0): "air", (2, 64, 0): "air", (2, 65, 0): "air",
            (3, 63, 0): "air", (3, 64, 0): "air", (3, 65, 0): "air",
            (4, 63, 0): "stone", (4, 64, 0): "air", (4, 65, 0): "air",
        }
        reg = FakeRegion((-1, 60, -1), (6, 70, 2), blocks)
        places = ["dirt"]
        self.assertTrue(nav.can_dynamic_bridge(reg, (0, 64, 0), (1, 0), gap_len=3, places=places))

        tasks = nav.dynamic_bridge_tasks((0, 64, 0), (1, 0), gap_len=3, places=list(places))
        self.assertEqual(len([t for t in tasks if t["type"] == "place"]), 1)
        self.assertEqual(tasks[0]["x"], 1)
        self.assertEqual(tasks[0]["y"], 63)
        self.assertEqual(tasks[-1]["x"], 4)

    def test_is_straight_walkable_and_smooth_way(self):
        # Straight stone path x=0..6
        blocks = {
            **{(x, 63, 0): "stone" for x in range(7)},
            **{(x, y, 0): "air" for x in range(7) for y in (64, 65)},
        }
        reg = FakeRegion((-1, 60, -1), (8, 70, 2), blocks)
        self.assertTrue(nav.is_straight_walkable(reg, (0, 64, 0), (6, 64, 0)))

        # Pit at x=3 makes it not straight walkable
        pit_blocks = dict(blocks)
        pit_blocks[(3, 63, 0)] = "air"
        reg_pit = FakeRegion((-1, 60, -1), (8, 70, 2), pit_blocks)
        self.assertFalse(nav.is_straight_walkable(reg_pit, (0, 64, 0), (6, 64, 0)))

        # Waypoint smoothing: 6-block run collapses into sprint jump
        step_tasks = [{"type": "goto", "x": x, "y": 64, "z": 0, "range": 0.5, "sprint": True} for x in range(1, 7)]
        smoothed = nav.smooth_way(reg, step_tasks, start=(0, 64, 0))
        types = [t["type"] for t in smoothed]
        self.assertIn("input", types)
        self.assertEqual(smoothed[-1]["x"], 6)


if __name__ == "__main__":
    unittest.main()

