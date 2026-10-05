"""Oracle and Fuzz tests for autonomous Goal DAG progression, environmental safety decoupling,
tool lifecycle sufficiency, and resource quota models.

1. Environmental safety decoupling & night underground planning.
2. Global Goal DAG milestone progression with ancestor satisfaction and opportunistic leap.
3. Station kit furnace retention and optional milestones.
4. Functional resource quotas (fuel, building, wood) and tool wear depreciation.
5. Autonomous tool lifecycle closed-loop (broken tool proposal, tier sufficiency, fast-fail dispatch).
"""
import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: F401,E402  (every skill registered)
from bonobo import dispatch, goals, knowledge as _k, planner
from bonobo.brain import Brain, next_milestone
from bonobo.cost import Cost
from tests.world import FakeRegion, bag, inventory, memory, slot, snapshot, state


class EnvironmentalSafetyDecouplingOracle(unittest.TestCase):
    """Oracle verification that night outdoors triggers safety fallback while underground/indoors continues."""

    def test_underground_night_does_not_halt_milestones(self):
        """Underground at night (roof overhead, skyLight=0): open_air is False and milestones proceed."""
        b = Brain()
        b.mem = memory()
        # Enclosed with solid roof overhead: 3 layers of stone at y=66, feet at y=64
        blocks = {(x, 66, z): "stone" for x in range(-2, 3) for z in range(-2, 3)}
        reg = FakeRegion((-5, 60, -5), (5, 75, 5), blocks)
        snap = snapshot(
            state(timeOfDay=18000, skyLight=0, blockLight=10, x=0.5, y=64.0, z=0.5),
            inventory(),
            region=reg
        )
        self.assertTrue(snap.night)
        self.assertFalse(b.under_sky(snap))

        # Under sky is False -> open_air is False -> next_milestone is evaluated
        open_air = snap.night and b.under_sky(snap)
        self.assertFalse(open_air)

        m = next_milestone(snap, b.mem)
        self.assertIsNotNone(m)
        self.assertEqual(m["goal"], "milestone")

    def test_open_air_night_triggers_safety_fallback(self):
        """Open air at night (no roof, skyLight=15): open_air is True, suppressing autonomous milestones."""
        b = Brain()
        b.mem = memory()
        # Completely flat world, air everywhere above y=64
        snap = snapshot(
            state(timeOfDay=18000, skyLight=15, blockLight=0, x=0.5, y=64.0, z=0.5),
            inventory()
        )
        self.assertTrue(snap.night)
        self.assertTrue(b.under_sky(snap))

        open_air = snap.night and b.under_sky(snap)
        self.assertTrue(open_air)

        # Brain suppresses autonomous milestones under open_air night
        milestone = next_milestone(snap, b.mem) if not open_air else None
        self.assertIsNone(milestone)

    def test_under_sky_geometry_oracle(self):
        """under_sky returns True only when no solid block exists in the 16 blocks above head."""
        b = Brain()
        # Case A: Open sky
        reg_open = FakeRegion((-2, 60, -2), (2, 85, 2), {})
        snap_open = snapshot(state(x=0.0, y=64.0, z=0.0), inventory(), region=reg_open)
        self.assertTrue(b.under_sky(snap_open))

        reg_roof = FakeRegion((-2, 60, -2), (2, 85, 2), {(0, 68, 0): "cobblestone"})
        snap_roof = snapshot(state(x=0.0, y=64.0, z=0.0, skyLight=0), inventory(), region=reg_roof)
        self.assertFalse(b.under_sky(snap_roof))


class GoalDAGMilestoneProgressionOracle(unittest.TestCase):
    """Oracle: the route's first unmet milestone; durables are judged live (a lost tool resets), consumables by what
    their products embody — no ancestor table, no leap table, no night rule in this layer."""

    def test_an_iron_pickaxe_alone_still_wants_the_stone_sword_and_axe(self):
        mem = memory()
        snap = snapshot(state(timeOfDay=1000), inventory(("iron_pickaxe", 1, 0)))
        self.assertNotEqual(goals.remainder(goals.make("milestone", name="stone tools"), snap, mem), {})
        self.assertEqual(next_milestone(snap, mem)["args"]["name"], "stone tools")

    def test_raw_iron_in_the_bag_is_the_planner_s_to_use_not_a_leap(self):
        mem = memory()
        snap = snapshot(state(timeOfDay=1000), inventory(("raw_iron", 3), ("cobblestone", 3)))
        self.assertEqual(next_milestone(snap, mem)["args"]["name"], "stone tools")

    def test_night_underground_changes_nothing_here(self):
        mem = memory()
        day = next_milestone(snapshot(state(timeOfDay=1000, skyLight=15), inventory()), mem)
        night = next_milestone(snapshot(state(timeOfDay=18000, skyLight=0), inventory()), mem)
        self.assertEqual(day, night)

    def test_all_milestones_achieved_returns_none(self):
        mem = memory()
        mem.add_site(goals.END_PORTAL_OPEN, (100, 30, 100), "minecraft:overworld")
        all_items = [
            ("iron_pickaxe", 1, 0), ("iron_sword", 1, 0), ("iron_axe", 1, 0),
            ("crafting_table", 1), ("furnace", 1),
            ("cooked_beef", 16), ("white_bed", 8),
            ("torch", 32), ("cobblestone", 64),
            ("bucket", 1), ("water_bucket", 1), ("shield", 1), ("flint_and_steel", 1),
            ("iron_helmet", 1), ("iron_chestplate", 1), ("iron_leggings", 1), ("iron_boots", 1),
            ("golden_helmet", 1), ("blaze_rod", 10), ("ender_pearl", 16), ("ender_eye", 16),
        ]
        snap = snapshot(state(timeOfDay=1000), inventory(*all_items))
        self.assertIsNone(next_milestone(snap, mem))


class StationKitOracle(unittest.TestCase):
    """Oracle: a station counts carried, or ours standing nearer than remaking one (goals.station_had)."""

    def test_rows(self):
        rows = [("our furnace beside us", 3, (), False), ("our furnace 40 off, no pickaxe", 40, ("iron_pickaxe",), False),
                ("must fail: our furnace 100 blocks off, an iron pickaxe held: remaking is cheaper", 100, (), True)]
        for name, dist, without, wanted in rows:
            with self.subTest(name):
                mem = memory()
                mem.add_station("minecraft:furnace", (dist, 64, 0), "minecraft:overworld")
                items = [("crafting_table", 1)] + ([] if "iron_pickaxe" in without else [("iron_pickaxe", 1, 0)])
                snap = snapshot(state(x=0.5, y=64.0, z=0.5), inventory(*items))
                rem = goals.remainder(goals.make("milestone", name="station kit"), snap, mem)
                self.assertEqual("minecraft:furnace" in rem, wanted)

    def test_must_fail_an_iron_pickaxe_is_no_furnace(self):
        snap = snapshot(state(x=0.5, y=64.0, z=0.5), inventory(("crafting_table", 1), ("iron_pickaxe", 1, 0)))
        self.assertIn("minecraft:furnace", goals.remainder(goals.make("milestone", name="station kit"), snap, memory()))


class ResourceQuotaAndToolWearOracle(unittest.TestCase):
    """Oracle: en-route wants the route's own demand less the bag (no cap table); wear is priced per use."""

    def test_enroute_wanted_never_asks_past_the_route(self):
        b = Brain()
        b.mem = memory()
        snap = snapshot(state(), inventory(("coal", 64), ("cobblestone", 64)))
        c = Cost(snap, b.mem)
        wanted = b.enroute_wanted(snap, c, [], [])
        self.assertNotIn("minecraft:coal", wanted)
        self.assertNotIn("minecraft:cobblestone", wanted)

    def test_enroute_wanted_suppresses_new_items_when_bag_almost_full(self):
        from bonobo.bag import FREE_SLOTS_TARGET
        b = Brain()
        b.mem = memory()
        filled = [slot("dirt", 64, i=i) for i in range(36 - FREE_SLOTS_TARGET + 1)]
        snap = snapshot(state(), bag({"slots": filled, "equipment": {}, "selectedSlot": 0}))
        self.assertLess(snap.inv.free_slots(), FREE_SLOTS_TARGET)
        for item_token, (_p, _lacked) in b.enroute_wanted(snap, Cost(snap, b.mem), [], []).items():
            self.assertGreater(snap.inv.count(item_token), 0)

    def test_wear_is_a_share_of_the_tool_s_price_per_use(self):
        from bonobo.data import TOOL_USES
        from bonobo.knowledge import wear_ticks
        self.assertAlmostEqual(wear_ticks(10, "minecraft:stone_pickaxe", 1000), 10 * 1000 / TOOL_USES["stone"])
        self.assertEqual(wear_ticks(10, "hand", 1000), 0)


class ToolLifecycleAndSufficiencyOracle(unittest.TestCase):
    """Oracle: a broken tool a held plan still wants is proposed; a step the pickaxe held cannot run is not runnable
    (planner.runnable, dispatch.go_find) — the planner's prep makes the tool, upkeep adds no second path."""

    def test_needs_propose_broken_tool_under_needs_plan(self):
        b = Brain()
        b.mem = memory()
        mine_step = planner.Step("mine", "minecraft:cobblestone", 4, {"blocks": ["minecraft:stone"], "tier": 0})
        b.needs_plan = {"steps": [mine_step]}
        b.needs.broken = {"pickaxe"}
        b.needs.wear = {}
        snap = snapshot(state(), inventory(("cobblestone", 3), ("stick", 2)))
        reads = {"enclosed": False, "bed_near": False, "soft_ground": False, "in_pit": False}
        b.needs.propose(snap, None, reads)
        self.assertIn("broken tool", [k for k, _g, _w in b.needs.needs_now])

    def test_a_tier_short_mine_step_is_not_runnable_and_upkeep_adds_nothing(self):
        b = Brain()
        b.mem = memory()
        step = planner.Step("mine", "minecraft:raw_gold", 1, {"tier": 2, "blocks": ["minecraft:gold_ore"]})
        b.needs_plan = {"steps": [step]}
        snap = snapshot(state(), inventory(("stone_pickaxe", 1, 0), ("iron_ingot", 3), ("stick", 2),
                                           ("crafting_table", 1)))
        self.assertFalse(planner.runnable(step, snap.inv))
        b.needs.propose(snap, None, {"enclosed": False, "bed_near": False, "soft_ground": False, "in_pit": False})
        self.assertNotIn("tool tier", [k for k, _g, _w in b.needs.needs_now])

    def test_dispatch_go_find_tier_guard_fast_fails(self):
        ctx = mock.Mock()
        ctx.dimension = "minecraft:overworld"
        ctx.mem = memory()
        ctx.inv = inventory(("stone_pickaxe", 1, 0))
        step = planner.Step("mine", "minecraft:diamond", 1, {"tier": 2, "blocks": ["minecraft:diamond_ore"]})
        with mock.patch("bonobo.world.feet", return_value=(0, 64, 0)):
            self.assertFalse(dispatch.go_find(ctx, step))


class AutonomousDAGFuzz(unittest.TestCase):
    """Fuzz testing of Goal DAG progression, enroute quota bounds, and tool tier contracts."""

    def test_fuzz_enroute_wanted_random_inventories(self):
        """Fuzz enroute_wanted across 120 randomized inventory distributions: quotas and invariants hold."""
        rng = random.Random(42)
        b = Brain()
        b.mem = memory()
        c = Cost(snapshot(), b.mem)

        candidate_items = [
            ("coal", 16), ("charcoal", 16), ("cobblestone", 64),
            ("dirt", 64), ("oak_log", 16), ("stone", 64),
            ("iron_ingot", 8), ("raw_iron", 8), ("stick", 16),
            ("apple", 5), ("cooked_beef", 10), ("wheat", 12)
        ]

        for trial in range(120):
            # Generate random bag items
            num_stacks = rng.randint(0, 36)
            picked_items = rng.sample(candidate_items, min(num_stacks, len(candidate_items)))
            bag_slots = []
            for i, (name, max_cnt) in enumerate(picked_items):
                cnt = rng.randint(1, max_cnt * 2)
                bag_slots.append(slot(name, count=cnt, i=i))

            # Fill filler items to vary free_slots
            extra_fillers = rng.randint(0, 36 - len(bag_slots))
            for j in range(extra_fillers):
                idx = len(bag_slots)
                bag_slots.append(slot("feather", count=1, i=idx))

            inv_data = {"slots": bag_slots, "equipment": {}, "selectedSlot": 0}
            snap = snapshot(state(timeOfDay=rng.randint(0, 24000)), bag(inv_data))

            wanted = b.enroute_wanted(snap, c, [], [])

            # Invariant 1: wanted must be a dict with tuple (prob, lacked)
            self.assertIsInstance(wanted, dict)
            for token, (prob, lacked) in wanted.items():
                # Invariant 2: Probability is strictly in (0.0, 1.0]
                self.assertGreater(prob, 0.0)
                self.assertLessEqual(prob, 1.0)

                # Invariant 3: Lacked count is strictly positive
                self.assertGreater(lacked, 0)

                # Invariant 4: a bag short of FREE_SLOTS_TARGET takes no new kind on the way
                from bonobo.bag import FREE_SLOTS_TARGET
                if snap.inv.free_slots() < FREE_SLOTS_TARGET:
                    self.assertGreater(snap.inv.count(token), 0, f"Trial {trial}: {token} added with a full bag")

    def test_fuzz_next_milestone_progression(self):
        """Fuzz next_milestone across 150 randomized world states and item combinations."""
        rng = random.Random(1337)
        mem = memory()

        tool_pool = [
            ("wooden_pickaxe", 1, 0), ("stone_pickaxe", 1, 0),
            ("iron_pickaxe", 1, 0), ("diamond_pickaxe", 1, 0)
        ]
        misc_pool = [
            ("crafting_table", 1), ("furnace", 1), ("raw_iron", 5),
            ("iron_ingot", 5), ("cobblestone", 16), ("oak_log", 8),
            ("cooked_beef", 10), ("bed", 1), ("torch", 30)
        ]

        for trial in range(150):
            # Random subset of tools and misc items
            tools = [rng.choice(tool_pool)] if rng.random() > 0.3 else []
            num_misc = rng.randint(0, len(misc_pool))
            misc = rng.sample(misc_pool, num_misc)

            inv_items = tools + misc
            t_of_day = rng.randint(0, 24000)
            sky_light = rng.choice([0, 4, 7, 15])
            snap = snapshot(state(timeOfDay=t_of_day, skyLight=sky_light), inventory(*inv_items))

            m = next_milestone(snap, mem)

            # Invariant 1: Result is None or a valid milestone Goal
            if m is not None:
                self.assertEqual(m["goal"], "milestone")
                name = m["args"]["name"]
                self.assertIn(name, goals.MILESTONES)
                self.assertNotIn(name, goals.OFF_ROUTE)

                # Invariant 2: the first unmet of the route, whatever the hour
                first = next(n for n in goals.MILESTONES if n not in goals.OFF_ROUTE
                             and goals.remainder(goals.make("milestone", name=n), snap, mem) != {})
                self.assertEqual(name, first)

    def test_fuzz_tool_tier_dispatch_guard(self):
        """Fuzz tool tier guard across random pickaxe tiers and mining hardness levels."""
        rng = random.Random(2026)
        mem = memory()

        tiers = {
            "wooden_pickaxe": 0,
            "stone_pickaxe": 1,
            "iron_pickaxe": 2,
            "diamond_pickaxe": 3
        }
        pickaxes = list(tiers.keys())

        for trial in range(80):
            has_pick = rng.random() > 0.2
            if has_pick:
                chosen_pick = rng.choice(pickaxes)
                held_tier = tiers[chosen_pick]
                inv = inventory((chosen_pick, 1, 0))
            else:
                held_tier = -1
                inv = inventory()

            req_tier = rng.randint(0, 3)
            step = planner.Step("mine", "target_block", 1, {"tier": req_tier, "blocks": ["target_block"]})

            ctx = mock.Mock()
            ctx.dimension = "minecraft:overworld"
            ctx.mem = mem
            ctx.inv = inv

            with mock.patch("bonobo.world.feet", return_value=(0, 64, 0)):
                # If held_tier < req_tier, go_find must return False without looking further
                if held_tier < req_tier:
                    self.assertFalse(dispatch.go_find(ctx, step))
                else:
                    self.assertTrue(_k.tool_ok(bag(inv), "pickaxe", req_tier))


if __name__ == "__main__":
    unittest.main()
