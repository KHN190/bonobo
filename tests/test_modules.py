import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bonobo import arbiter, hazard, nav, planner
from bonobo.data import place_signature
from bonobo.planner import NullCost, Step, Node, from_bag, plan_needs
from tests.world import FakeRegion, bag, inventory


class TestReflexSafety(unittest.TestCase):
    def test_arbiter_owns_faster_layer_during_fight(self):
        """During fight lease (tactic), safety and reflex rescues own the body."""
        motion = arbiter.Motion()
        tactic_intent = arbiter.Intent("tactic", lambda: None, "fight", at=0.0, key="fight")
        safety_intent = arbiter.Intent("safety", lambda: None, "lava rescue", at=0.0, key="lava rescue")
        plan_intent = arbiter.Intent("plan", lambda: None, "mine ore", at=0.0, key="mine ore")

        # Acquire a tactic lease
        motion.preempt("tactic", lambda: None, "fight", release=lambda: False)
        self.assertIsNotNone(motion.lease)

        # Safety must own the body while tactic holds a lease
        safety_owns = motion.carry(safety_intent, lambda: motion.owns("api.post"))
        self.assertTrue(safety_owns)

        # Slower plan intent must NOT own the body while tactic holds a lease
        plan_owns = motion.carry(plan_intent, lambda: motion.owns("api.post"))
        self.assertFalse(plan_owns)

        # When safety preempts, the previous tactic lease is cleared
        ran = []
        motion.preempt("safety", lambda: ran.append("rescued"), "lava rescue")
        self.assertIsNone(motion.lease)
        self.assertEqual(ran, ["rescued"])

    def test_perception_watcher_resilience_and_restart(self):
        """Watcher thread can be restarted when absent or dead."""
        from bonobo import perception

        with mock.patch("bonobo.perception.start_watching") as mock_start:
            perception.STATE.watcher = None
            perception.ensure_watching()
            mock_start.assert_called_once()

    def test_hazard_water_clutch_and_ladder_climbing(self):
        """Falling triggers water clutch rescue and climbing a ladder is not falling."""
        # 1. Climbing ladder moving downwards is not falling
        climbing_state = {
            "health": 20, "food": 20, "control": {}, "inWater": False, "air": 300,
            "onGround": False, "climbing": True, "dy": -0.5, "dimension": "minecraft:overworld",
            "x": 0.5, "y": 64.0, "z": 0.5, "blockX": 0, "blockY": 64, "blockZ": 0, "timeOfDay": 2000
        }
        self.assertFalse(hazard.falling(climbing_state, fallen=5.0))

        # 2. Free falling triggers falling state
        falling_state = dict(climbing_state)
        falling_state["climbing"] = False
        self.assertTrue(hazard.falling(falling_state, fallen=5.0))
        self.assertFalse(hazard.falling(falling_state, fallen=2.0))

        # 3. Falling triggers rescue_due and handle executes water clutch
        due = hazard.rescue_due(falling_state, buried=False, fallen=5.0)
        self.assertEqual(due, "falling")

        clutch_fn = mock.MagicMock()
        with mock.patch.dict(hazard.SKILLS, {"water_clutch": clutch_fn}):
            handled = hazard.handle("test_ctx", falling_state, lambda name, act: act(), lambda name: True, fallen=5.0)
            self.assertTrue(handled)
            clutch_fn.assert_called_once_with("test_ctx")


class TestPlanning(unittest.TestCase):
    def test_duplicate_production_not_mined_twice(self):
        """When an item like cobblestone is needed multiple times, extra mined is reused."""
        needs = [("minecraft:cobblestone", 8), ("minecraft:cobblestone", 32)]
        steps = plan_needs(bag(inventory()), needs, NullCost())

        # Total cobblestone mined for the targets should be 40 (plus 3 for stone pickaxe = 43)
        cobble_mined = sum(s.count for s in steps if s.kind == "mine" and s.token.endswith("cobblestone"))
        self.assertEqual(cobble_mined, 43)

    def test_search_replay_requires_non_tool_call_items(self):
        """Replaying a held plan verifies non-tool call items (e.g. obsidian for nether portal)."""
        cost = NullCost()
        portal_step = Step("build", "nether_portal", 1, {"call": "build nether_portal"})
        plan = [portal_step]

        # Case without obsidian: must fail replay
        inv_without_obsidian = inventory(
            ("minecraft:flint_and_steel", 1),
            ("minecraft:cobblestone", 4)
        )
        root_no_obs = Node(from_bag(bag(inv_without_obsidian), [], [], cost.reserved, cost.facts()), [], [])
        search_no_obs = planner.Search(cost)
        result_no_obs = search_no_obs.replay(root_no_obs, [("nether_portal", 1)], plan)
        self.assertIsNone(result_no_obs)

        # Case with 10 obsidian: replay succeeds
        inv_with_obsidian = inventory(
            ("minecraft:obsidian", 10),
            ("minecraft:flint_and_steel", 1),
            ("minecraft:cobblestone", 4)
        )
        root_obs = Node(from_bag(bag(inv_with_obsidian), [], [], cost.reserved, cost.facts()), [], [])
        search_obs = planner.Search(cost)
        result_obs = search_obs.replay(root_obs, [("nether_portal", 1)], plan)
        self.assertIsNotNone(result_obs)


class TestExecutionNavigation(unittest.TestCase):
    def test_negative_coordinates_place_signature_match(self):
        """Negative float positions match block integer coordinates in place_signature."""
        block_pos = (-17, 64, -1)
        float_pos = (-16.3, 64.0, -0.5)

        sig_block = place_signature(block_pos, night=False)
        sig_float = place_signature(float_pos, night=False)
        self.assertEqual(sig_block, sig_float)
        self.assertEqual(sig_block, ((-2, 4, -1), False))

    def test_retry_also_keys_cleared_on_step_success(self):
        """brain.attempt clears all also retry keys on successful execution."""
        from bonobo import brain as brainmod

        brain_obj = brainmod.Brain()
        brain_obj.retry.failed("step_child_task", "stuck", "cannot reach", time.time())
        self.assertEqual(brain_obj.retry.causes("step_child_task"), ["stuck"])

        # Execute parent task with also=["step_child_task"] succeeding
        brain_obj.attempt("parent_task", lambda: None, also=["step_child_task"])

        # Both parent and also keys should now have their retry failures cleared
        self.assertEqual(brain_obj.retry.causes("step_child_task"), [])
        self.assertEqual(brain_obj.retry.causes("parent_task"), [])

    def test_landing_excludes_lava_and_hazards(self):
        """nav.landing never chooses lava cells as landing spots."""
        lo, hi = (-18, 185, -3), (18, 205, 3)

        def floor_blocks(xs, y, name="stone"):
            return {(x, y, z): name for x in xs for z in range(-3, 4)}

        # 1. Lava pool on top of stone floor: walk must stop before lava
        blocks_lava_path = {
            **floor_blocks(range(-3, 19), 199),
            **floor_blocks(range(4, 19), 200, "lava")
        }
        reg_lava = FakeRegion(lo, hi, blocks_lava_path)
        land_lava = nav.landing(reg_lava, (0.5, 200.0, 0.5), (16, 200, 0))
        self.assertEqual(land_lava, (3, 200, 0))

        # 2. Deep drop over edge into lava pool: walk must not jump into lava
        blocks_lava_drop = {
            **floor_blocks(range(-3, 4), 199),
            **floor_blocks(range(4, 18), 190, "lava")
        }
        reg_lava_drop = FakeRegion(lo, hi, blocks_lava_drop)
        land_lava_drop = nav.landing(reg_lava_drop, (0.5, 200.0, 0.5), (16, 200, 0))
        self.assertEqual(land_lava_drop, (3, 200, 0))

        # 3. Deep drop over edge into water pool: walk successfully drops into water
        blocks_water_drop = {
            **floor_blocks(range(-3, 4), 199),
            **floor_blocks(range(4, 18), 190, "water")
        }
        reg_water_drop = FakeRegion(lo, hi, blocks_water_drop)
        land_water_drop = nav.landing(reg_water_drop, (0.5, 200.0, 0.5), (16, 200, 0))
        self.assertEqual(land_water_drop, (16, 191, 0))


if __name__ == "__main__":
    unittest.main()
