"""A source's worth is the seconds to get its yield again were that source not there — memory's cap and a chest's
loot priced so, through the production round and the looter's own body."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain  # noqa: E402,F401  (every skill registered)
from bonobo import api, lifecycle, loot, memory, world  # noqa: E402
from tests.world import brain_fixture, inventory, round_ctx, state  # noqa: E402

D = "minecraft:overworld"
LONE = (5, 64, 0)
CHEST = (10, 64, 0)


def scene():
    lifecycle.reset_all(caches=False)
    b = brain_fixture()
    b.mem.clock = 0
    blocks = {(x, y, z): "dirt" if y >= 62 else "stone" for x in range(-8, 40) for z in range(-8, 9)
              for y in range(58, 64)}
    snap = world.Snapshot.from_readings(state(x=.5, y=64, z=.5, gameTime=0),
                                        world.Inventory(inventory(("stone_axe", 1))), {}, [],
                                        world.Region.of((-8, 58, -8), (40, 70, 8), blocks))
    return b, snap


def grove(i):
    """The i-th tree of a grove far east, each beyond the next's merge."""
    return 300 + (i % 15) * 13, 64, (i // 15) * 13


class Stop(Exception):
    pass


class TheLoneTreeOutlivesTheGroves(unittest.TestCase):
    """Past the cap, the one tree in reach (hidden, logs are a long walk) is worth more than any grove tree (hidden,
    the lone one still serves): a grove tree goes, though the lone one is the oldest."""

    def test_rows(self):
        rows = [("the lone tree oldest, the grove filling the cap", LONE)]
        for name, lone in rows:
            with self.subTest(name):
                b, snap = scene()
                b.mem.note_seen("tree", lone, D)
                for i in range(memory.MEMORY_CAP - 1):
                    b.mem.note_seen("tree", grove(i), D)
                with mock.patch.object(api, "api", side_effect=AssertionError("the round read the world")), \
                        mock.patch.object(b, "invariants"), \
                        mock.patch.object(world.Snapshot, "read", return_value=snap), \
                        mock.patch.object(b, "context", side_effect=Stop), \
                        mock.patch.object(api, "GATE"), mock.patch.object(api, "HOLD"):
                    with self.assertRaises(Stop):
                        b._round_body(None)
                    b.mem.note_seen("tree", grove(memory.MEMORY_CAP), D)
                kept = [tuple(r["pos"]) for r in b.mem.data["seen"]]
                self.assertEqual(len(kept), memory.MEMORY_CAP)
                # must fail on the base: every tree priced alike, the oldest (the lone one) went
                self.assertIn(lone, kept)


class AChestsLootIsPricedWithTheChestHidden(unittest.TestCase):
    """What the looter takes is weighed at its price were this chest not there, not at the chest's own take."""

    def test_rows(self):
        rows = [("two diamonds, the only ones known", "minecraft:diamond", 2)]
        for name, item, n in rows:
            with self.subTest(name):
                b, snap = scene()
                b.mem.note_container(CHEST, D, [{"id": item, "count": n, "slot": 0}])
                b.round_snap = snap
                ctx = round_ctx(b, snap)
                hidden = b.hidden_prices(snap, types.SimpleNamespace(kind="take"), CHEST).get(item)
                plain = b.price_table(snap).get(item)
                self.assertNotEqual(hidden, plain, "the scene must price the two apart")
                slots = {"slots": [{"slot": 0, "id": item, "count": n, "owner": "chest"}]}
                err = None
                with mock.patch.object(api, "api", side_effect=AssertionError("the looter read the world")), \
                        mock.patch.object(loot, "find", return_value=[{"x": CHEST[0], "y": CHEST[1], "z": CHEST[2]}]), \
                        mock.patch.object(world, "feet", return_value=(0, 64, 0)), \
                        mock.patch.object(loot.nav, "arrived_near", return_value=True), \
                        mock.patch.object(api, "run", return_value={"status": "succeeded",
                                                                    "result": {"screen": "chest"}}), \
                        mock.patch.object(world, "container", return_value=slots), \
                        mock.patch.object(api, "post"), \
                        mock.patch.object(loot, "Inventory", return_value=types.SimpleNamespace(free_slots=lambda: 30)), \
                        mock.patch.object(loot, "loot_plan", wraps=loot.loot_plan) as plan:
                    try:
                        list(loot.loot_chest.__wrapped__(ctx))
                    except Exception as e:     # shown in the failure below
                        err = e
                # must fail on the base: the looter never priced the chest
                self.assertTrue(plan.called, repr(err))
                self.assertEqual(plan.call_args[0][1].get(item), hidden)


if __name__ == "__main__":
    unittest.main()
