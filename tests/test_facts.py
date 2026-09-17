"""What the four quantities are GIVEN: the facts a state vector and a memory are supposed to carry.

V, Δt, p and κ are only as good as their arguments. This file is about the arguments — the half of the planner
that turns a world into a state:

  * being AT something means being able to work on it, not being near it in a straight line;
  * what memory knows is read before anything goes exploring, and one disappointing look retires ONE note;
  * a station standing in the world is the same fact as one in the bag;
  * a thing that already exists can be taken, and is priced like anything else;
  * ground to build on is something you MAKE, at a price, not something you must find;
  * what we built is never a resource — the protection is on the near side of the one door that breaks blocks;
  * what is worth taking out of a chest is decided by price, never by a list of names.

Property-shaped and parameterised where the sweep applies, so each of these is one statement rather than a file.
"""
import inspect
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions, brain, knowledge, loot, memory, nav, skillcore  # noqa: E402
from bonobo.solve import solve  # noqa: E402
from tests.world import DIMS, FakeRegion, PricingSnap, flat, worlds  # noqa: E402


def mem():
    return memory.Memory(os.path.join(tempfile.mkdtemp(prefix="facts"), "notes.json"))


class Snap:
    dimension = "minecraft:overworld"
    night = False
    ticks_until_dusk = 6000

    def __init__(self, feet=(0, 64, 0)):
        self.feet = feet
        self.inv = PricingSnap().inv

    def get(self, key, default=None):
        return {"skyLight": 15, "health": 20, "food": 20}.get(key, default)


# ------------------------------------------------------------------------------------------- being there at all

class BeingAtSomethingMeansBeingAbleToWorkOnIt(unittest.TestCase):
    """Radius cannot tell a step from a swim. `at:` is what the mine step requires, so it must mean reachable —
    standing on the rim of a flooded pit with the coal five blocks away is not being at the coal."""

    def vector(self, reachable=None, kind="stone", pos=(1, 64, 1)):
        m = mem()
        m.note_resource(kind, pos, "minecraft:overworld")
        return actions.state_of(Snap(), m, reachable=reachable)

    def test_within_reach_and_reachable_is_arrival(self):
        self.assertEqual(self.vector(reachable=lambda kinds: True).get(actions.at("stone")), 1)

    def test_within_reach_but_unreachable_is_not(self):
        self.assertIsNone(self.vector(reachable=lambda kinds: False).get(actions.at("stone")))

    def test_far_away_is_not_arrival_however_reachable(self):
        self.assertIsNone(self.vector(reachable=lambda kinds: True, pos=(300, 64, 300)).get(actions.at("stone")))

    def test_arriving_removes_the_walk_from_the_plan(self):
        m = mem()
        cost = actions.Costs(lambda kinds: 40.0)
        far = solve(actions.table(cost, {}), {"tool:pickaxe:0": 1}, {"minecraft:cobblestone": 1})
        self.assertIn("seek:stone", far.counts)
        m.note_resource("stone", (1, 64, 1), "minecraft:overworld")
        state = actions.state_of(Snap(), m) | {"tool:pickaxe:0": 1}
        near = solve(actions.table(cost, state), state, {"minecraft:cobblestone": 1})
        self.assertNotIn("seek:stone", near.counts, "we are standing on it; walking to it is not work")
        self.assertLess(near.cost_s, far.cost_s)


# ----------------------------------------------------------------------------------- what memory is for

class WhatWasWrittenDownIsReadBack(unittest.TestCase):
    def test_the_seek_walks_to_what_memory_knows_before_exploring(self):
        import inspect
        src = inspect.getsource(brain.Brain.go_find)
        self.assertIn("remembered_spot", src)
        self.assertLess(src.index("remembered_spot"), src.index("self.explore("),
                        "exploring is the last resort, after what is already known")

    def test_both_maps_answer_where_was_one_of_these(self):
        b = brain.Brain.__new__(brain.Brain)
        b.mem = mem()
        b.mem.add_sighting("minecraft:sheep", (100, 64, 0), "minecraft:overworld")
        b.mem.add_sighting("minecraft:sheep", (20, 64, 0), "minecraft:overworld")
        b.mem.note_resource("tree", (40, 64, 0), "minecraft:overworld")
        self.assertEqual(b.remembered_spot(["minecraft:sheep"], "minecraft:overworld", (0, 64, 0)), (20, 64, 0))
        self.assertEqual(b.remembered_spot(["tree"], "minecraft:overworld", (0, 64, 0)), (40, 64, 0))

    def test_a_note_under_our_feet_is_not_somewhere_to_walk(self):
        b = brain.Brain.__new__(brain.Brain)
        b.mem = mem()
        b.mem.add_sighting("minecraft:sheep", (0, 64, 1), "minecraft:overworld")
        self.assertIsNone(b.remembered_spot(["minecraft:sheep"], "minecraft:overworld", (0, 64, 0)))

    def test_one_look_retires_one_note(self):
        """Retiring every note within a radius is how "could not find stone" survived a memory holding fourteen
        stone points."""
        m = mem()
        for pos in ((10, 64, 10), (40, 64, 10), (200, 64, 200)):
            m.note_resource("stone", pos, "minecraft:overworld")
        before = {tuple(p) for p in m.resources("stone", "minecraft:overworld")}
        m.confirm("stone", (10, 64, 10), "minecraft:overworld", found=False)
        left = {tuple(p) for p in m.resources("stone", "minecraft:overworld")}
        self.assertEqual(before - left, {(10, 64, 10)}, "exactly the note we stood on, and no other")

    def test_a_walk_that_failed_bans_the_route_and_keeps_the_note(self):
        import inspect
        src = inspect.getsource(brain.Brain.go_find)
        failed = src[src.index("remembered_spot"):src.index("confirm")]
        self.assertIn("self.ban(", failed, "could not get there is about the route, not about the note")

    def test_an_old_sighting_is_kept_and_priced_rather_than_deleted(self):
        import time
        m = mem()
        m.add_sighting("minecraft:sheep", (10, 64, 10), "minecraft:overworld")
        for s in m.data["sightings"]["minecraft:sheep"]:
            s["at"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 24 * 3600))
        m.save()
        self.assertTrue(m.sightings("minecraft:sheep", "minecraft:overworld"))
        self.assertEqual(m.sightings("minecraft:sheep", "minecraft:overworld", max_age_min=2), [])


class AStationStandingThereIsOneWeHave(unittest.TestCase):
    def test_a_furnace_within_reach_counts(self):
        m = mem()
        m.add_machine("furnace-1", (2, 64, 0), 0, "minecraft:overworld", ("smelting",))
        self.assertEqual(actions.state_of(Snap(), m).get("minecraft:furnace"), 1)

    def test_one_across_the_valley_or_in_another_world_does_not(self):
        far, elsewhere = mem(), mem()
        far.add_machine("furnace-1", (300, 64, 300), 0, "minecraft:overworld", ("smelting",))
        elsewhere.add_machine("furnace-1", (2, 64, 0), 0, "minecraft:the_nether", ("smelting",))
        self.assertIsNone(actions.state_of(Snap(), far).get("minecraft:furnace"))
        self.assertIsNone(actions.state_of(Snap(), elsewhere).get("minecraft:furnace"))


# --------------------------------------------------------------------------- what the world already has made

class WhatExistsCanBeTaken(unittest.TestCase):
    """A village is a bag of finished goods. Taking one is a column like any other, priced like any other."""

    def test_every_takeable_thing_has_a_column_that_produces_what_it_gives(self):
        table = {a.name: a for a in actions.table(actions.Costs(lambda kinds: 20.0), {})}
        for token, row in knowledge.TAKEABLE.items():
            action = table.get(f"take:{token}")
            self.assertIsNotNone(action, token)
            self.assertGreater(action.cost_s, 0.0, token)
            self.assertTrue(any(v > 0 for v in action.effect.values()), token)
            self.assertTrue(any(d.startswith("at:") for d in action.requires), token)

    def test_the_tool_a_block_needs_is_required_and_no_other(self):
        table = {a.name: a for a in actions.table(actions.Costs(lambda kinds: 20.0), {})}
        self.assertTrue(any(d.startswith("tool:pickaxe") for d in table["take:minecraft:furnace"].requires))
        self.assertFalse(any(d.startswith("tool:") for d in table["take:bed"].requires))

    def test_what_it_gives_is_something_the_planner_can_use(self):
        from bonobo.data import GROUPS, RECIPES
        for token, row in knowledge.TAKEABLE.items():
            for given in row["gives"]:
                self.assertTrue(given in GROUPS or given in RECIPES or given.startswith("minecraft:"),
                                f"{token} gives {given}, which nothing else names")

    def test_memory_can_see_them(self):
        scanned = {b for blocks in brain.Brain.RESOURCE_KINDS.values() for b in blocks}
        for token, row in knowledge.TAKEABLE.items():
            self.assertTrue(set(row["blocks"]) & scanned, f"{token}: nothing in the scan ever notes it")

    def test_near_is_taken_and_far_is_made(self):
        village = set(knowledge.TAKEABLE["bed"]["blocks"])

        def plan(village_s):
            cost = actions.Costs(lambda kinds: village_s if village & set(kinds) else 30.0)
            state = {"bag_free": 20, "tool:pickaxe:0": 1, "uses:pickaxe": 100}
            return [a.name for a, _n in solve(actions.table(cost, state), state, {"bed": 1}).steps()]
        self.assertIn("take:bed", plan(10.0))
        self.assertIn("craft:bed", plan(4000.0))


class GroundIsSomethingYouMake(unittest.TestCase):
    """"no clear spot for shelter within 6 blocks" — in a forest, with a bag of blocks and a pickaxe. Levelling is
    work, so it belongs in the price, not in a yes/no."""

    def options(self, region, radius=6):
        from bonobo import blueprints, building
        return building.spot_options(blueprints.SHELTER, (0, 64, 0), region, nav.Policy(), radius=radius)

    def test_ready_ground_costs_nothing_and_wins(self):
        best = self.options(flat())
        self.assertTrue(best)
        self.assertEqual(best[0][0], 0)
        self.assertEqual(best[0][3], ())

    def test_something_in_the_way_is_a_price(self):
        region = flat()
        for y in (64, 65, 66):
            region.blocks[(1, y, 0)] = "oak_log"
        best = self.options(region, radius=0)
        self.assertTrue(best)
        self.assertGreater(best[0][0], 0)
        self.assertTrue(any(kind == "break" for kind, _cell in best[0][3]))

    def test_a_hole_is_filled_rather_than_avoided(self):
        region = flat()
        for x in range(0, 2):
            del region.blocks[(x, 63, 0)]
        best = self.options(region, radius=0)
        self.assertTrue(best)
        self.assertTrue(any(kind == "fill" for kind, _cell in best[0][3]))

    def test_what_cannot_be_broken_is_not_offered(self):
        region = flat()
        for x in range(-8, 9):
            for z in range(-8, 9):
                for y in (64, 65, 66):
                    region.blocks[(x, y, z)] = "bedrock"
        self.assertEqual(self.options(region), [])


class WhatWeBuiltIsNotAResource(unittest.TestCase):
    """The night the shelter went up, the agent mined it for the cobblestone. One door breaks a single cell, and
    the protection is on this side of it."""

    class Policy:
        def __init__(self, protected=()):
            self.protected = set(protected)

    def test_the_door_refuses_our_own_blocks(self):
        from bonobo.api import NotAvailable
        with self.assertRaises(NotAvailable):
            skillcore.mine_cell(self.Policy({(1, 64, 1)}), (1, 64, 1))

    def test_no_module_posts_a_bare_mine_task(self):
        import ast
        pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bonobo")
        allowed = {"building.py", "end.py", "wood.py", "farming.py", "nav.py", "skillcore.py"}
        offenders = []
        for name in sorted(os.listdir(pkg)):
            if not name.endswith(".py") or name in allowed:
                continue
            for node in ast.walk(ast.parse(open(os.path.join(pkg, name)).read())):
                if isinstance(node, ast.Dict):
                    for key, val in zip(node.keys, node.values):
                        if isinstance(key, ast.Constant) and key.value == "type" \
                                and isinstance(val, ast.Constant) and val.value == "mine":
                            offenders.append(f"{name}:{node.lineno}")
        self.assertEqual(offenders, [], f"these break blocks without the protection door: {offenders}")


class ANoteIsAClueNotAFact(unittest.TestCase):
    """Things vanish: a felled tree, a looted chest, a herd that wandered. Arriving settles a note either way —
    left to a timer, the same sixty-block walk is priced again next round."""

    def test_arriving_and_finding_nothing_retires_it(self):
        m = mem()
        m.note_resource("tree", (10, 64, 10), "minecraft:overworld")
        m.confirm("tree", (10, 64, 10), "minecraft:overworld", found=False)
        self.assertEqual(m.resources("tree", "minecraft:overworld"), [])

    def test_arriving_and_finding_it_keeps_it(self):
        m = mem()
        m.note_resource("tree", (10, 64, 10), "minecraft:overworld")
        m.confirm("tree", (10, 64, 10), "minecraft:overworld", found=True)
        self.assertTrue(m.resources("tree", "minecraft:overworld"))

    def test_what_we_emptied_ourselves_comes_back_only_when_it_regrows(self):
        m = mem()
        m.note_resource("tree", (10, 64, 10), "minecraft:overworld", depleted=True)
        self.assertEqual(m.resources("tree", "minecraft:overworld"), [])
        later = m.resources("tree", "minecraft:overworld", now=9e12)
        self.assertTrue(later, "a grove that has had twenty minutes is a grove again")


class WhatIsWorthTakingIsDecidedByPrice(unittest.TestCase):
    """A hand-written list of loot had no wheat in it, so the agent opened a village chest and took nothing."""

    def slots(self, *items):
        return [{"slot": i, "id": item, "count": n, "owner": owner}
                for i, (item, n, owner) in enumerate(items)]

    PRICES = {"minecraft:iron_ingot": 120.0, "minecraft:wheat": 6.0, "minecraft:stick": 0.2,
              "minecraft:diamond": 900.0}

    def test_value_decides_and_the_dearest_comes_first(self):
        plan = loot.loot_plan(self.slots(("minecraft:wheat", 20, "chest"), ("minecraft:diamond", 1, "chest")),
                              self.PRICES, bag_free=20)
        self.assertEqual(plan, [1, 0])

    def test_what_is_worth_less_than_the_slot_it_eats_is_left(self):
        tight = loot.loot_plan(self.slots(("minecraft:stick", 1, "chest")), self.PRICES, bag_free=2)
        roomy = loot.loot_plan(self.slots(("minecraft:stick", 1, "chest")), self.PRICES, bag_free=30)
        self.assertEqual(tight, [])
        self.assertEqual(roomy, [0])

    def test_our_own_slots_and_unpriced_things_are_left(self):
        self.assertEqual(loot.loot_plan(self.slots(("minecraft:diamond", 1, "player")), self.PRICES, 30), [])
        self.assertEqual(loot.loot_plan(self.slots(("minecraft:mystery", 4, "chest")), self.PRICES, 30), [])

    def test_no_hand_written_list_survives(self):
        import inspect
        self.assertNotIn("WANTED_SUFFIX", inspect.getsource(loot))


if __name__ == "__main__":
    unittest.main()


class TheBodyIsAStateLikeAnyOther(unittest.TestCase):
    """What the body can do where it stands is dimensions, not special cases — so it is swept, not spot-checked.

    Swimming, drowning and falling are values of the `self` dimension (`tests/world.py`), which means every
    relation the other files state is already asked about them. What belongs HERE is what only this idea can be
    wrong about: that the dimensions exist, that the columns which need them say so, and that a body missing one
    can always plan its way back — otherwise the pool empties and the agent floats there deciding nothing, which
    is exactly what happened.
    """

    NEEDS = {"craft:minecraft:furnace": ("hands_free", "footing"),   # 3x3: a station has to be put down
             "mine:minecraft:coal": ("hands_free", "footing"),
             "take:bed": ("hands_free", "footing"),
             "smelt:minecraft:iron_ingot": ("hands_free", "footing"),
             "gather:log": ("hands_free",),                          # cutting a tree needs no floor
             "hunt:wool": ("hands_free",)}                           # a fight can happen in the water

    def columns(self, state):
        from bonobo import actions
        return {a.name: a for a in actions.table(actions.Costs(lambda kinds: 6.0), state)}

    def test_every_column_that_touches_the_world_says_what_body_it_needs(self):
        whole = self.columns({"bag_free": 30, "footing": 1, "hands_free": 1})
        for name, needs in self.NEEDS.items():
            with self.subTest(column=name):
                requires = whole[name].requires
                for dim in needs:
                    self.assertEqual(requires.get(dim), 1, f"{name} does not say it needs {dim}")

    def test_a_body_that_cannot_work_can_always_plan_its_way_back(self):
        """For every way of losing a precondition, the table offers a way of getting it back — and the pool is
        never empty because of it. Swept over the `self` dimension rather than written out per case."""
        from bonobo import actions, solve
        for w in worlds(self_=list(DIMS["self_"]), resource="bare", stock="none"):
            state = w.state()
            with self.subTest(body=w.dims["self_"]):
                table = self.columns(state)
                missing = [d for d in actions.BODY_DIMS if not state.get(d)]
                mends = [a for a in table.values() if a.tag and a.tag[0] == "reach"]
                self.assertEqual(bool(missing), bool(mends),
                                 f"{missing} missing but {[a.name for a in mends]} offered")
                for dim in missing:
                    self.assertTrue(any(a.effect.get(dim) for a in mends), f"nothing restores {dim}")
                # And the mend is priced, not free or infinite: it competes with the work it unblocks.
                prices = solve.reach_cost(list(table.values()), state)
                for dim in missing:
                    self.assertGreater(prices.get(dim, 0.0), 0.0, dim)
                    self.assertLess(prices.get(dim, float("inf")), 600.0, f"{dim}: priced as good as impossible")

    def test_the_mends_are_alternatives_and_the_cheapest_is_what_it_costs(self):
        """More than one way back (swim to the shore, or put a block underfoot) — so the price of footing is the
        cheaper of them, whichever the world makes cheap here."""
        from bonobo import actions, solve
        swimming = {"bag_free": 30, "hands_free": 1, "building": 8}
        table = list(self.columns(swimming).values())
        ways = [a for a in table if a.effect.get("footing")]
        self.assertGreater(len(ways), 1, "one way back is not a choice")
        cheapest = min(a.cost_s for a in ways)
        self.assertLessEqual(solve.reach_cost(table, swimming).get("footing", 1e9), cheapest + 1e-6)


