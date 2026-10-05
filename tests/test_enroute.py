"""En-route as one pass over the round's plan: each round A is the normal held plan; C is A with the one en-route
candidate's step put before A's first leg (never a second search); C is held only when its seconds cost less
than A's by more than P x bag.item_value(item) (P: 1 for the held plan's own want, discounted for a later
milestone). Rows go through the production round
(brain.Brain.plan_proposals/round_for), not a helper function."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo import api, arbiter, goals, lifecycle, tasks, world  # noqa: E402
from bonobo.knowledge import members  # noqa: E402
from tests.world import brain_fixture, inventory, memory, round_ctx, state  # noqa: E402
from tests.test_plan_run_order import brought_before  # noqa: E402  (the one order check, reused)

D = "minecraft:overworld"
ORE = (40, 64, 0)
KIT = (("stone_pickaxe", 1), ("stone_sword", 1), ("stone_axe", 1), ("crafting_table", 1), ("furnace", 1), ("bread", 8))


def ground(chest=None):
    """Dirt over stone from the feet to an iron ore 40 east: the leg the plan's next step walks; a `chest` on it."""
    blocks = {(x, y, z): "dirt" if y >= 62 else "stone" for x in range(-8, 49) for z in range(-8, 9)
              for y in range(58, 64)}
    blocks[ORE] = "iron_ore"
    if chest is not None:
        blocks[chest] = "chest"
    return world.Region.of((-8, 58, -8), (48, 70, 8), blocks)


def proposals(mobs, chest=None, contents=None, bag=()):
    """The round's plan proposals for a raw iron task, the iron ore 40 east, with `mobs` in sight, a `chest` beside
    the leg (its `contents` remembered, or unopened), `bag` held besides the kit. Returns (proposals, the brain)."""
    tmp = tempfile.mkdtemp()
    lifecycle.reset_all(caches=False)
    with mock.patch.object(tasks, "FILE", os.path.join(tmp, "tasks.json")), \
            mock.patch.object(api, "api", side_effect=AssertionError("the round read the world")):
        b = brain_fixture()
        b.mem.clock = 0
        hits = {"iron_ore": [{"x": ORE[0], "y": ORE[1], "z": ORE[2], "distance": 40.0, "block": "minecraft:iron_ore"}]}
        if chest is not None:
            hits["chest"] = [{"x": chest[0], "y": chest[1], "z": chest[2], "distance": float(chest[0]),
                              "block": "minecraft:chest"}]
        if contents is not None:
            b.mem.note_container(chest, D, [{"id": i, "count": n, "slot": k} for k, (i, n) in enumerate(contents)])
        snap = world.Snapshot.from_readings(state(x=.5, y=64, z=.5), world.Inventory(inventory(*KIT, *bag)), hits,
                                            mobs, ground(chest))
        b.round_snap = snap
        tasks.add(goals.have(("minecraft:raw_iron", 1)))
        got = b.plan_proposals(snap, round_ctx(b, snap))
        return got, b


def sheep(x, z):
    return {"id": 9, "type": "minecraft:sheep", "x": x + .5, "y": 64, "z": z + .5, "distance": float(x)}


def held_tokens(b):
    """The tokens the round's held plan provides (planner.Step.token of every step), in order -- plan_proposals
    sets `needs_plan` to the round's held plan whether it came from a queued task or a needs goal."""
    return [] if b.needs_plan is None else [st.token for st in b.needs_plan["steps"]]


def provides(b, group):
    """Does the round's held plan provide an item of `group` -- a step's own token naming `group` itself (the
    planner often holds the group token, e.g. "wool", not one color), or one of its concrete members
    (knowledge.members, the same grouping the planner resolves a token by)."""
    held = set(held_tokens(b))
    return group in held or bool(held & set(members(group)))


class TheRoundTakesWhatIsOnTheWay(unittest.TestCase):
    """C (the plan with the detour's item added to the goal needs) is held only when it pays; else A (the plan
    without it) is held, unchanged."""

    def test_a_sheep_on_the_leg_is_taken(self):
        """Wool is a later milestone's (a bed's): a sheep 2 off the leg pays its hunt back -- C held."""
        got, b = proposals([sheep(20, 2)])
        # must fail on the base: the old side-act mechanism never merges wool into the held plan itself
        self.assertTrue(provides(b, "wool"), [(i.kind, i.key) for i in got])
        self.assertEqual(len(got), 1, "one held plan proposed, not a second plan beside it")

    def test_a_sheep_far_off_the_leg_is_not(self):
        """A sheep far enough off the leg that its detour plainly costs more than the wool is worth -- A held,
        unchanged; whenever the round still weighed it (enroute_choice recorded), the margin it was kept over is
        shown, never assumed from a fixed offset the model may since have repriced."""
        got, b = proposals([sheep(20, 120)])
        choice = b.enroute_choice
        if choice is not None:
            self.assertGreaterEqual(choice.C_s - choice.A_s, choice.P * choice.value)
        self.assertFalse(provides(b, "wool"), [(i.kind, i.key) for i in got])

    def test_a_chest_beside_the_leg_gives_its_diamond_not_its_logs(self):
        """The chest beside the iron leg, 64 logs already held: seen to hold diamond and logs, the diamond (the
        diamond tools' later need) is taken into the held plan; the logs (the bag already covers that need) are
        not."""
        chest, bag = (20, 64, 1), [("oak_log", 64)]
        got, b = proposals([], chest, [("minecraft:diamond", 2), ("minecraft:oak_log", 10)], bag)
        # must fail on the base: there, the chest's take is a separate "enroute" intent, never folded into the plan
        self.assertTrue(provides(b, "minecraft:diamond"), [(i.kind, i.key) for i in got])
        self.assertFalse(provides(b, "minecraft:oak_log"))
        self.assertEqual(len(got), 1)


class ThePickedCCostsLessThanItsValue(unittest.TestCase):
    """Whenever C is held over A, its seconds beat A's by less than P x bag.item_value(item) -- never a
    detour that costs more than what it is worth -- and only one extra plan is priced a round (never a chain of
    them)."""

    def test_rows(self):
        rows = [("a sheep 2 off: C must actually be cheaper enough to hold", [sheep(20, 2)]),
                ("the same sheep far off: A held, so there is nothing to check", [sheep(20, 120)])]
        for name, mobs in rows:
            with self.subTest(name):
                got, b = proposals(mobs)
                # the round-held record (brain.EnrouteChoice: A_s, C_s, P, value), read by field name
                choice = getattr(b, "enroute_choice", None)
                if not provides(b, "wool"):
                    continue      # A held: nothing was swapped in, nothing to check against its own price
                self.assertIsNotNone(choice, "C held: the round must still record what it was weighed against")
                self.assertLess(choice.C_s - choice.A_s, choice.P * choice.value)
                self.assertEqual(len(got), 1, "one held plan a round, never two priced")


def smelting(mobs, wait_s):
    """The round's held plan for an iron pickaxe whose 3 ingots a furnace is still smelting (`wait_s` left): await
    then craft, nothing to walk to; `mobs` in sight. Returns the brain."""
    tmp = tempfile.mkdtemp()
    lifecycle.reset_all(caches=False)
    with mock.patch.object(tasks, "FILE", os.path.join(tmp, "tasks.json")), \
            mock.patch.object(api, "api", side_effect=AssertionError("the round read the world")):
        b = brain_fixture()
        b.mem.clock = 0
        b.mem.add_job("furnace", (2, 64, 0), D, "minecraft:iron_ingot", 3, time.time() + wait_s, True)
        snap = world.Snapshot.from_readings(state(x=.5, y=64, z=.5), world.Inventory(inventory(*KIT, ("stick", 2))),
                                            {}, mobs, ground())
        b.round_snap = snap
        tasks.add(goals.have(("minecraft:iron_pickaxe", 1)))
        b.plan_proposals(snap, round_ctx(b, snap))
        return b


class TheFurnaceWaitIsFree(unittest.TestCase):
    """While a furnace smelts what the plan awaits, the plan has no site to walk to: what lies beside the feet is
    still weighed, and C's extra seconds within the wait cost nothing."""

    def test_rows(self):
        rows = [("a sheep 3 off, 35s left: C", sheep(3, 1), 35.0, True),
                ("a sheep 45 off, 35s left: its detour fits the wait, C", sheep(45, 1), 35.0, True),
                ("the same sheep, 1s left: the detour is paid in full, A", sheep(45, 1), 1.0, False)]
        for name, mob, wait_s, taken in rows:
            with self.subTest(name):
                b = smelting([mob], wait_s)
                # must fail on the base: an await/craft plan has no site, so no candidate is ever weighed
                self.assertEqual(provides(b, "wool"), taken, held_tokens(b))


class AWorthIsWithItsSourceHidden(unittest.TestCase):
    """A candidate's value is the seconds to get it again were it not there (brain.hidden_prices), in the cheap
    bound as in the final weighing: priced with the sheep in sight, wool costs only its hunt and never pays."""

    def test_rows(self):
        got, b = proposals([sheep(3, 5)])
        snap, at = b.round_snap, (3, 64, 5)
        hunt = next((st for st in b.needs_plan["steps"] if st.kind == "hunt"), None)
        # must fail on the base: its bound priced wool with this sheep in sight, so the sheep never reached C
        self.assertIsNotNone(hunt, [(i.kind, i.key) for i in got])
        hidden, plain = b.hidden_prices(snap, hunt, at).get("wool"), b.price_table(snap).get("wool")
        self.assertGreater(hidden, plain)
        self.assertEqual(b.enroute_choice.value, hidden)


class NeverIronBeforeAPickaxe(unittest.TestCase):
    """An empty-bag start whose immediate goal is a wooden pickaxe, with iron ore in
    sight 2 off the first step's own site (wanted later, for stone/iron tools) -- the held plan never mines iron
    before a pickaxe exists, and the round's chosen act is never a dig at the ore itself. Must fail on the base:
    its old side act (cost.enroute) picks the ore as a detour by proximity alone, with no tool check at all, and
    sends a raw mine task at it by hand."""

    def test_rows(self):
        tmp = tempfile.mkdtemp()
        lifecycle.reset_all(caches=False)
        with mock.patch.object(tasks, "FILE", os.path.join(tmp, "tasks.json")), \
                mock.patch.object(api, "api", side_effect=AssertionError("the round read the world")):
            b = brain_fixture()
            b.mem.clock = 0
            tree, ore = (5, 71, 0), (5, 67, 2)
            blocks = {(x, y, z): "dirt" if y == 70 else "stone" for x in range(-8, 20) for z in range(-8, 8)
                      for y in range(40, 71)}
            for dy in range(4):
                blocks[(tree[0], tree[1] + dy, tree[2])] = "oak_log"
            blocks[ore] = "iron_ore"
            hits = {"log": [{"x": tree[0], "y": tree[1], "z": tree[2], "distance": 5.0, "block": "minecraft:oak_log"}],
                    "iron_ore": [{"x": ore[0], "y": ore[1], "z": ore[2], "distance": 2.0, "block": "minecraft:iron_ore"}]}
            snap = world.Snapshot.from_readings(state(x=.5, y=71, z=.5), world.Inventory(inventory()), hits, [],
                                                world.Region.of((-8, 40, -8), (20, 75, 8), blocks))
            b.round_snap = snap
            tasks.add(goals.have(("minecraft:wooden_pickaxe", 1)))
            got = b.plan_proposals(snap, round_ctx(b, snap))
        held = b.needs_plan
        self.assertIsNotNone(held, "an empty-bag start must still plan something")
        self.assertIsNone(brought_before(held["steps"]), [str(s) for s in held["steps"]])
        chosen = arbiter.arbitrate(got)
        first = chosen.action.step if chosen is not None else None
        # must fail on the base: the old side act's own chosen intent is a raw mine at the ore, no pickaxe held
        self.assertFalse(first is not None and first.kind == "mine" and first.token == "minecraft:raw_iron",
                         [(i.kind, i.key) for i in got])


if __name__ == "__main__":
    unittest.main()
