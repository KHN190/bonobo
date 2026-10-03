"""craft.py's station-spot choice (craft._sitting, craft.py ~428): the spot skillcore.free_spots_here offers for
placing a new table must pass nav.reach's own stand test (P2/K1) -- never one the gate then refuses.

The rows below drive the real production path (craft._sitting, through the real free_spots/free_spots_here, not a
stand-in): on the base commit (6ba6f1b), free_spots_here(limit=1) ignores reachability, so the sealed-box row's
place task is queued at a spot nav.reach refuses -- red by assertion. On this branch, free_spots_here is given a
`reachable` predicate (craft.placeable) and filters it out, so no place is queued at all (StationMissing instead)
-- green."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import craft, nav, skillcore  # noqa: E402
from bonobo.skillcore import free_spots  # noqa: E402
from bonobo.world import Inventory  # noqa: E402
from tests.world import FakeRegion, inventory, state  # noqa: E402

FEET = (0, 64, 0)
RECIPE = [("minecraft:stone_pickaxe", 1)]
# a table already carried: craft_commands only ever queues a place when a spot was offered (isolates the row from
# the separate "no table to place" path)
INV_ITEMS = (("cobblestone", 8), ("stick", 4), ("crafting_table", 1))


def _floor(lo=(-10, 50, -10), hi=(10, 80, 10)):
    return {(x, 63, z): "stone" for x in range(lo[0], hi[0] + 1) for z in range(lo[2], hi[2] + 1)}, lo, hi


def _flat():
    blocks, lo, hi = _floor()
    return FakeRegion(lo, hi, blocks), set()


def _sealed_box():
    """Walled and capped on every side but the body's own 2-high cell, the walls undiggable (`protected`): no
    cell in reach has an approach the gate accepts, dig or no dig -- yet free_spots' own geometry (floor, open
    air, clear of the body) still finds one outside the box, the same gap a remembered ore outside a sealed vein shows."""
    blocks, lo, hi = _floor()
    walls = set()
    for x in range(-1, 2):
        for z in range(-1, 2):
            for y in (64, 65, 66):
                if (x, z) == (0, 0) and y in (64, 65):
                    continue
                blocks[(x, y, z)] = "stone"
                walls.add((x, y, z))
    return FakeRegion(lo, hi, blocks), walls


def _run_sitting(region, protected):
    """craft._sitting(ctx, RECIPE) over `region`, its world reads swapped for the fixture (api.get("/state"),
    Region(...)) at the boundary only -- free_spots, nav.reach, craft_plan etc. all run for real. Returns (the
    tasks it queued, whether it gave up with StationMissing instead)."""
    st = state(x=FEET[0] + .5, y=FEET[1], z=FEET[2] + .5)
    inv = Inventory(inventory(*INV_ITEMS))
    mem = mock.Mock()
    mem.home_part = lambda *a, **k: None       # no home table: the new-spot branch is the one under test
    ctx = mock.Mock(mem=mem, dimension="minecraft:overworld", policy=nav.Policy(protected=protected))
    sent = []
    missing = False
    with mock.patch.object(craft, "Inventory", lambda: inv), \
            mock.patch.object(craft, "find", lambda *a, **k: []), \
            mock.patch.object(craft, "feet", lambda: FEET), \
            mock.patch.object(craft, "close_screen", lambda: None), \
            mock.patch.object(craft, "_standing", lambda *a: False), \
            mock.patch.object(craft, "run_split", lambda tasks, wait: sent.extend(tasks)), \
            mock.patch.object(craft.api, "get", lambda path: st), \
            mock.patch.object(craft, "Region", lambda *a, **k: region), \
            mock.patch.object(skillcore.api, "get", lambda path: st), \
            mock.patch.object(skillcore, "Region", lambda *a, **k: region):
        try:
            craft._sitting(ctx, RECIPE)
        except craft.StationMissing:
            missing = True
    return sent, missing, inv


class StationSpotPassesTheGate(unittest.TestCase):
    def test_flat_offers_a_reachable_spot(self):
        region, protected = _flat()
        sent, missing, inv = _run_sitting(region, protected)
        self.assertFalse(missing, "a flat room: a station must be placeable")
        place = next(t for t in sent if t["type"] == "place")
        spot = (place["x"], place["y"], place["z"])
        self.assertIsNotNone(nav.reach(region, FEET, spot, "place", inv, protected).stand,
                              "must fail: offered a spot the gate then refuses")

    def test_sealed_never_queues_a_place_the_gate_refuses(self):
        region, protected = _sealed_box()
        sent, missing, inv = _run_sitting(region, protected)
        place = next((t for t in sent if t.get("type") == "place"), None)
        if place is None:
            self.assertTrue(missing, "no place queued: must fail silently rather than vanish without a reason")
            return
        spot = (place["x"], place["y"], place["z"])
        # must fail (base 6ba6f1b): free_spots_here ignored reach, queuing a place at an unreachable spot
        self.assertIsNotNone(nav.reach(region, FEET, spot, "place", inv, protected).stand,
                              "a place was queued at a spot the gate then refuses")


class FreeSpotsKeepsTheOldOfferWithNoReachable(unittest.TestCase):
    """Every other free_spots_here caller (store.tidy_inventory, survive.move_to_open_space's verify) passes no
    `reachable`: free_spots must not start refusing for them."""

    def test_reachable_none_skips_the_gate_check(self):
        region, protected = _sealed_box()
        st = state(x=FEET[0] + .5, y=FEET[1], z=FEET[2] + .5)
        self.assertTrue(free_spots(region, st, reach=4, limit=1),
                         "must fail: the gate check ran though reachable was never passed")


if __name__ == "__main__":
    unittest.main()
