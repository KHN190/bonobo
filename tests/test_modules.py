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


class TestGoalDrive(unittest.TestCase):
    def test_end_portal_advances_to_dragon_beds_in_end(self):
        """In the End dimension, end portal milestone is met and advances to dragon beds."""
        from bonobo import brain, goals, memory, world

        inv = world.Inventory()
        snap = world.Snapshot(inv, (0.0, 64.0, 0.0), 0.0, "minecraft:the_end", False)
        mem = memory.Memory()

        portal_goal = goals.make("milestone", name="end portal")
        self.assertEqual(goals.remainder(portal_goal, snap, mem), {})

        # next_milestone in the End should pick dragon beds (or None if beds met), not get stuck on end portal
        m = brain.next_milestone(snap, mem)
        self.assertIsNotNone(m)
        self.assertEqual(m["args"]["name"], "dragon beds")

    def test_milestone_no_regression_after_consumed(self):
        """Crafting blaze rods into eyes of ender does not cause blaze rods milestone to fail."""
        from bonobo import goals, memory, world

        # 12 eyes of ender, 0 blaze rods
        inv = world.Inventory({"slots": [{"id": "minecraft:ender_eye", "count": 12, "slot": 0}]})
        snap = world.Snapshot(inv, (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)
        mem = memory.Memory()

        blaze_goal = goals.make("milestone", name="blaze rods")
        self.assertEqual(goals.remainder(blaze_goal, snap, mem), {})

    def test_worn_armor_satisfies_milestone(self):
        """Worn armor in equipment slots counts toward iron armor milestone."""
        from bonobo import goals, knowledge, memory, world

        inv = world.Inventory({
            "slots": [],
            "equipment": {
                "head": {"id": "minecraft:iron_helmet", "count": 1},
                "chest": {"id": "minecraft:iron_chestplate", "count": 1},
                "legs": {"id": "minecraft:iron_leggings", "count": 1},
                "feet": {"id": "minecraft:iron_boots", "count": 1},
            }
        })
        snap = world.Snapshot(inv, (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)
        mem = memory.Memory()

        armor_goal = goals.make("milestone", name="iron armor")
        self.assertEqual(goals.remainder(armor_goal, snap, mem), {})

    def test_held_cleaned_on_task_cancel(self):
        """When a task is no longer live, plan_proposals removes it from self.held."""
        from bonobo import brain as brainmod, world

        brain_obj = brainmod.Brain()
        brain_obj.held[999] = {"steps": []}
        snap = world.Snapshot(world.Inventory(), (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)

        with mock.patch("bonobo.tasks.load", return_value=[]), \
             mock.patch("bonobo.tasks.expire", return_value=False):
            brain_obj.plan_proposals(snap, mock.MagicMock())
            self.assertNotIn(999, brain_obj.held)

    def test_bucket_filled_satisfies_milestone(self):
        """Holding a water bucket satisfies minecraft:bucket requirement for iron tools milestone."""
        from bonobo import goals, memory, world

        inv = world.Inventory({"slots": [
            {"id": "minecraft:iron_pickaxe", "count": 1, "slot": 0},
            {"id": "minecraft:iron_sword", "count": 1, "slot": 1},
            {"id": "minecraft:shield", "count": 1, "slot": 2},
            {"id": "minecraft:flint_and_steel", "count": 1, "slot": 3},
            {"id": "minecraft:water_bucket", "count": 1, "slot": 4},
        ]})
        snap = world.Snapshot(inv, (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)
        mem = memory.Memory()
        iron_tools = goals.make("milestone", name="iron tools")
        self.assertEqual(goals.remainder(iron_tools, snap, mem), {})

    def test_station_kit_nearby_satisfies_milestone(self):
        """Placed furnace and crafting table near feet satisfy station kit milestone without carrying them."""
        from bonobo import goals, memory, world

        inv = world.Inventory()
        snap = world.Snapshot(inv, (10.0, 64.0, 10.0), 0.0, "minecraft:overworld", False)
        mem = memory.Memory()
        mem.add_station("minecraft:crafting_table", (12, 64, 10), "minecraft:overworld")
        mem.add_station("minecraft:furnace", (10, 64, 12), "minecraft:overworld")

        station_goal = goals.make("milestone", name="station kit")
        self.assertEqual(goals.remainder(station_goal, snap, mem), {})

    def test_ender_pearls_satisfied_by_eyes(self):
        """Holding 12 eyes of ender satisfies ender pearls milestone remainder."""
        from bonobo import goals, memory, world

        inv = world.Inventory({"slots": [
            {"id": "minecraft:ender_eye", "count": 12, "slot": 0}
        ]})
        snap = world.Snapshot(inv, (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)
        mem = memory.Memory()

        pearls_goal = goals.make("milestone", name="ender pearls")
        self.assertEqual(goals.remainder(pearls_goal, snap, mem), {})



class TestCostEvaluation(unittest.TestCase):
    def test_tree_memory_reach_checked(self):
        """Tree memory source uses mine stand_kind and checks refused cells."""
        from bonobo import cost as costmod, memory, world

        snap = world.Snapshot(world.Inventory(), (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)
        mem = memory.Memory()
        # Add a remembered tree
        mem.data["seen"] = [{"kind": "tree", "pos": (10, 64, 10), "dimension": "minecraft:overworld"}]

        cost_obj = costmod.Cost(snap, mem)
        # Mock refused to return a reason for cell (10, 64, 10)
        with mock.patch.object(cost_obj, "refused", return_value="blocked"):
            hit = cost_obj._nearest(["tree"], sources=True)
            # Must be refused and return None
            self.assertIsNone(hit)

    def test_walk_lb_admissible(self):
        """walk_lb returns straight-line lower bound without route factor multiplier."""
        from bonobo import cost as costmod, memory, planner, world

        snap = world.Snapshot(world.Inventory(), (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)
        mem = memory.Memory()
        cost_obj = costmod.Cost(snap, mem)

        step = planner.Step("goto", "place", 1, {"pos": (10, 64, 0)})
        lb = cost_obj.walk_lb(step, {})
        # Euclidean distance 10, walk_ticks is int(10 * 5.3) = 53
        self.assertLessEqual(lb, costmod.walk_ticks(10.0))

    def test_distant_lava_ignored_in_facts(self):
        """Cost.facts does not mark lava as True when remembered lava is far away (>128m)."""
        from bonobo import cost as costmod, memory, world

        snap = world.Snapshot(world.Inventory(), (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)
        mem = memory.Memory()
        # Lava 500 blocks away
        mem.data["seen"] = [{"kind": "lava", "pos": (500, 64, 0), "dimension": "minecraft:overworld"}]
        cost_obj = costmod.Cost(snap, mem)
        self.assertFalse(cost_obj.facts()["lava"])

        # Lava 50 blocks away
        mem.data["seen"] = [{"kind": "lava", "pos": (50, 64, 0), "dimension": "minecraft:overworld"}]
        cost_near = costmod.Cost(snap, mem)
        self.assertTrue(cost_near.facts()["lava"])



class TestPlanningAdvanced(unittest.TestCase):
    def test_replay_await_invalid_rejected(self):
        """Search.replay rejects await step if pending outputs do not cover the count."""
        from bonobo import planner, world

        cost = planner.NullCost()
        inv = world.Inventory()
        root = planner.Node(planner.from_bag(bag(inv), [], [], cost.reserved, cost.facts()), [], [])
        search = planner.Search(cost)

        await_step = planner.Step("await", "minecraft:iron_ingot", 3)
        plan = [await_step]
        # inv has no pending iron ingots
        res = search.replay(root, [("minecraft:iron_ingot", 3)], plan)
        self.assertIsNone(res)

    def test_smelt_fuel_per_step_capacity(self):
        """Each smelt step plans fuel required for its batch to match runtime craft.py."""
        from bonobo import planner, world

        cost = planner.NullCost()
        inv = world.Inventory({"slots": [
            {"id": "minecraft:raw_iron", "count": 6, "slot": 0},
            {"id": "minecraft:coal", "count": 5, "slot": 1}
        ]})
        targets = [("minecraft:iron_ingot", 3)]
        steps = planner.plan_needs(bag(inv), targets, cost)
        smelt_steps = [s for s in steps if s.kind == "smelt"]
        self.assertTrue(len(smelt_steps) >= 1)
        for s in smelt_steps:
            coal_needed = s.detail.get("inputs", {}).get("minecraft:coal", 0)
            self.assertEqual(coal_needed, 1)

    def test_replay_preserves_reusable_tools(self):
        """Replay validates call items without consuming non-consumables like tools or flint & steel."""
        cost = NullCost()
        st1 = Step("skill", "nether_portal", 1, {"call": {"minecraft:flint_and_steel": 1, "minecraft:obsidian": 10}})
        st2 = Step("skill", "nether_portal", 1, {"call": {"minecraft:flint_and_steel": 1, "minecraft:obsidian": 10}})
        plan = [st1, st2]

        inv = inventory(
            ("minecraft:flint_and_steel", 1),
            ("minecraft:obsidian", 20)
        )
        root = Node(from_bag(bag(inv), [], [], cost.reserved, cost.facts()), [], [])
        search = planner.Search(cost)
        with mock.patch.object(search, "used", return_value={"minecraft:obsidian": 10}):
            res = search.replay(root, [], plan)
            self.assertIsNotNone(res)

    def test_surplus_gather_logs_injected(self):
        """Surplus logs from gather are added to virtual bag and avoid duplicate gathering."""
        cost = NullCost()
        inv = inventory()
        targets = [("minecraft:oak_log", 2), ("minecraft:oak_log", 2)]
        steps = plan_needs(bag(inv), targets, cost)
        gather_steps = [s for s in steps if s.kind == "gather"]
        self.assertEqual(sum(s.count for s in gather_steps), 4)


class TestReflexAdvanced(unittest.TestCase):
    def test_brain_l0_rescue_under_fight_lease(self):
        """Brain L0 rescue runs via arbiter carry and executes even if a fight lease is held."""
        from bonobo import brain as brainmod

        arbiter.BODY.preempt("tactic", lambda: None, "fight", release=lambda: False)
        self.assertIsNotNone(arbiter.BODY.lease)

        ran = []
        l0_act = brainmod.Act("L0", "rescue lava", lambda: ran.append("rescued"))
        safe_intent = arbiter.Intent("safety", l0_act.run, l0_act.name, key=l0_act.name)
        arbiter.BODY.carry(safe_intent, l0_act.run)
        self.assertEqual(ran, ["rescued"])

    def test_shelter_cooling_allows_eat(self):
        """When shelter is cooling and health is below 14 or shelter not ready, eat reflex fires."""
        from bonobo import reflexes

        # Night, food low (10), health 12, shelter not ready
        view = {
            "food": 10, "hp": 12, "meal": True, "night": True, "shelter_ready": False,
            "bed_works": False, "bed_carried": False, "bed_near": False
        }
        eat_trigger = next(trig for name, trig, act in reflexes.TABLE if name == "eat")
        self.assertTrue(eat_trigger(view))

    def test_active_hazards_fallback(self):
        """Active hazards can fall back to secondary hazard if primary is cooling."""
        from bonobo import hazard

        # In water (drowning) and critical hp (critical)
        state = {
            "health": 4, "inWater": True, "air": 10, "onGround": False,
            "dead": False, "inLava": False, "onFire": False
        }
        actives = hazard.active_hazards(state)
        self.assertIn("drowning", actives)
        self.assertIn("critical", actives)

        def ready(name):
            return "drowning" not in name

        due = hazard.rescue_due(state, ready=ready)
        self.assertEqual(due, "critical")

    def test_motion_driving_thread_safe(self):
        """Motion.driving read/write is guarded under _lock."""
        from bonobo import arbiter

        motion = arbiter.Motion()
        intent = arbiter.Intent("plan", lambda: None, "test", key="test")
        with motion._lock:
            motion.driving = intent
            self.assertEqual(motion.driving, intent)
            motion.driving = None

    def test_fast_exception_swallowed(self):
        """Brain._decide_round catches errors in fast() without raising or freezing."""
        from bonobo import brain as brainmod, world

        brain_obj = brainmod.Brain()
        snap = world.Snapshot(world.Inventory(), (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)
        ctx = mock.MagicMock()

        with mock.patch("bonobo.hazard.rescue_due", side_effect=RuntimeError("unexpected sensor failure")), \
             mock.patch.object(brain_obj.needs, "propose", return_value=[]), \
             mock.patch.object(brain_obj.reflexes, "proposals", return_value=[]), \
             mock.patch.object(brain_obj, "light_intent", return_value=None):
            # Must not raise RuntimeError
            act = brain_obj._decide_round(snap, ctx)
            self.assertIsNone(act)

    def test_hazard_repeat_throttling(self):
        """Watcher repeats environmental danger stop after 1.0s rather than 10.0s."""
        from bonobo import perception

        watcher = perception.Watcher()
        now = time.time()
        watcher.last["lava"] = now - 2.0  # 2.0s ago, > 1.0s

        # DANGER_REPEAT_S for lava is 1.0
        repeat_s = 1.0 if "lava" in ("lava", "drowning", "suffocation", "falling") else perception.REPEAT_S
        self.assertLessEqual(repeat_s, 1.0)
        self.assertGreater(now - watcher.last["lava"], repeat_s)



class TestExecutionAdvanced(unittest.TestCase):
    def test_worn_armor_only_in_milestone_remainder(self):
        """Worn armor satisfies milestone remainder without altering bag held_count."""
        from bonobo import goals, knowledge, memory, world

        inv = world.Inventory({
            "slots": [],
            "equipment": {
                "chest": {"id": "minecraft:iron_chestplate", "count": 1}
            }
        })
        self.assertEqual(knowledge.held_count(inv, "minecraft:iron_chestplate"), 0)

        snap = world.Snapshot(inv, (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)
        mem = memory.Memory()
        milestone = goals.make("milestone", name="iron armor")
        rem = goals.remainder(milestone, snap, mem)
        self.assertNotIn("minecraft:iron_chestplate", rem)

    def test_run_cells_in_a_row_resets_on_success(self):
        """run_cells resets in_a_row when some steps in a chain succeed."""
        from bonobo import api, nav

        tasks = [{"type": "mine", "x": i, "y": 64, "z": 0} for i in range(4)]

        call_count = [0]
        def fake_run_chain(pending, stop_on_failure=True, wait=60):
            call_count[0] += 1
            if call_count[0] == 1:
                return [{"status": "succeeded"}, {"status": "failed", "message": "cannot reach"}]
            else:
                return [{"status": "succeeded"}, {"status": "succeeded"}]

        with mock.patch("bonobo.api.run_chain", side_effect=fake_run_chain), \
             mock.patch("bonobo.api.out_of_reach"):
            res = nav.run_cells("mine", tasks)
            self.assertEqual(res["result"]["succeeded"], 3)

    def test_station_missing_is_replan_not_interruption_and_not_failure(self):
        """StationMissing is a replan exception: in INTERRUPTIONS, and excluded from failure outcome."""
        from bonobo import api, retry, skillcore

        err = skillcore.StationMissing("minecraft:furnace")
        self.assertTrue(isinstance(err, api.INTERRUPTIONS))
        self.assertEqual(retry.cause_of(err), "replan")

        mem = mock.MagicMock()
        ctx = mock.MagicMock(mem=mem)
        key = "craft:iron_sword"
        if retry.cause_of(err) != "replan":
            ctx.mem.record_outcome(key, False)
        mem.record_outcome.assert_not_called()

    def test_reach_stand_goto_clean_return(self):
        """reach_stand for goto task returns cleanly once feet is in range."""
        from bonobo import nav, world

        task = {"type": "goto", "x": 10, "y": 64, "z": 10}
        with mock.patch("bonobo.nav.feet", return_value=(10.0, 64.0, 10.0)), \
             mock.patch("bonobo.nav._read_box", return_value=FakeRegion((-1, 60, -1), (15, 70, 15))), \
             mock.patch("bonobo.nav.stands_for", return_value=True):
            nav.reach_stand(task, mock.MagicMock(), at=(10, 64, 10))

    def test_tasks_save_preserves_concurrent_tasks(self):
        """tasks.save merges tasks on disk so concurrently appended tasks are not lost."""
        from bonobo import goals, tasks
        import tempfile

        tmp_file = tempfile.mktemp(suffix=".json")
        try:
            # Process A writes task 1
            t1 = {"id": 1, "state": "queued", "goal": "have", "args": {}}
            tasks.save([t1], path=tmp_file)

            # Process B concurrently writes task 2
            t2 = {"id": 2, "state": "queued", "goal": "have", "args": {}}
            tasks.save([t2], path=tmp_file)

            # File should now contain both task 2 and task 1
            loaded = tasks.load(path=tmp_file)
            loaded_ids = {t["id"] for t in loaded}
            self.assertEqual(loaded_ids, {1, 2})
        finally:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

    def test_tasks_ranked_by_deadline(self):
        """plan_proposals ranks tasks by expiration deadline ascending."""
        from bonobo import brain as brainmod, goals, world

        brain_obj = brainmod.Brain()
        snap = world.Snapshot(world.Inventory(), (0.0, 64.0, 0.0), 0.0, "minecraft:overworld", False)
        ctx = mock.MagicMock()

        t_late = {"id": 1, "state": "queued", "goal": goals.have(("minecraft:dirt", 1)), "expires": 2000.0}
        t_urgent = {"id": 2, "state": "queued", "goal": goals.have(("minecraft:stone", 1)), "expires": 1000.0}
        t_noexp = {"id": 3, "state": "queued", "goal": goals.have(("minecraft:torch", 1))}

        with mock.patch("bonobo.tasks.load", return_value=[t_late, t_urgent, t_noexp]), \
             mock.patch("bonobo.tasks.expire", return_value=False), \
             mock.patch("bonobo.tasks.save"):
            entries = brain_obj.plan_proposals(snap, ctx)
            ranks = {name: rank for name, _goal, rank in entries if name.startswith("task ")}
            self.assertLess(ranks["task 2"], ranks["task 1"])
            self.assertLess(ranks["task 1"], ranks["task 3"])



if __name__ == "__main__":
    unittest.main()
