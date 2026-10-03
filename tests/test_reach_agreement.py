"""K1/P2 (docs/refactor.md 问题 1 站位, item 0): terrain x act x way-blocks carried, plan verdict == run verdict.

Run-side ground truth (what the body actually does): nav.gate (nav.py:424) -> nav.unstandable (nav.py:405) ->
nav.stands_for (nav.py:331); when no stand holds, nav.reach_stand's loop (nav.py:1294-1318) calls nav.plan_way,
whose pillar/bridge material is nav.building_of (nav.py:233) and budget is nav.place_budget (nav.py:256). Offline we
cannot run api.run_chain, so `_run_reachable` below is a pure re-implementation of that exact retry loop (same
calls, same order), simulating each returned step against a FakeRegion copy instead of sending it to the mod.

use_item and attack/interact are not in nav.APPROACHING = ("mine", "place", "use") (nav.py:309): `nav.gate` never
checks them at all today. For those two acts the "run-side" column below is the invariant's own oracle (what P2
demands if they were gated the same way "use" is) via `_run_reachable(..., kind="use")`, not a function production
actually calls. Rows there are expected to go red whenever today's un-gated plan-side predicate disagrees -- that
mismatch *is* the finding, not a test bug.
"""
import math
import unittest

from bonobo import cost as _cost, fluids, gather, nav, wood, world
from bonobo.skillcore import free_spots
from bonobo.world import Inventory
from tests.world import FakeRegion, inventory, memory, state

FEET = (0, 64, 0)
STOCK = {0: (), 8: (("cobblestone", 8),), 32: (("cobblestone", 32),)}


def _inv(blocks):
    return Inventory(inventory(*STOCK[blocks]))


def _floor(lo=(-10, 50, -10), hi=(10, 80, 10)):
    return {(x, 63, z): "stone" for x in range(lo[0], hi[0] + 1) for z in range(lo[2], hi[2] + 1)}, lo, hi


# ---------------------------------------------------------------- the run-side oracle (nav.gate's retry loop, pure)

def _apply(region, steps, feet):
    """Pure: a region + feet after one way's steps ran (mine -> air, place -> the item, goto -> feet moves)."""
    blocks = dict(region.blocks)
    at = feet
    for t in steps:
        c = (t["x"], t["y"], t["z"])
        if t["type"] == "mine":
            blocks[c] = "air"
        elif t["type"] == "place":
            blocks[c] = t.get("item", "minecraft:cobblestone").rsplit(":", 1)[-1]
        elif t["type"] == "goto":
            at = c
    return FakeRegion(region.lo, region.hi, blocks), at


def _run_reachable(region, feet, target, kind, inv, protected=(), tries=None):
    """Pure mirror of nav.gate (nav.py:424) + nav.reach_stand (nav.py:1294-1318): stands_for first, else plan_way
    (place_budget/building_of live inside it), simulate the way, retry up to WAY_TRIES."""
    tries = tries or nav.WAY_TRIES
    feet = tuple(feet)
    for _ in range(tries):
        if nav.stands_for(kind, region, feet, target):
            return True
        steps, why, _secs = nav.plan_way(region, feet, target, kind, inv, protected)
        if steps is None:
            return False
        if not steps:
            return True
        region, feet = _apply(region, steps, feet)
    return nav.stands_for(kind, region, feet, target)


# ---------------------------------------------------------------- terrain: (region, feet, anchor) for a 5-away target

def _tree(logs_high):
    blocks, lo, hi = _floor()
    anchor = (3, 64, 0)
    for y in range(64, 64 + logs_high):
        blocks[(3, y, 0)] = "oak_log"
    return FakeRegion(lo, hi, blocks), FEET, (3, 64 + logs_high - 1, 0)


def _flat():
    blocks, lo, hi = _floor()
    return FakeRegion(lo, hi, blocks), FEET, (5, 64, 0)


def _one_step():
    blocks, lo, hi = _floor()
    for x in range(2, 7):
        blocks[(x, 64, 0)] = "stone"   # a 1-block rise partway, jumpable, no material needed
    return FakeRegion(lo, hi, blocks), FEET, (5, 65, 0)


def _cliff():
    blocks, lo, hi = _floor()
    for x in range(1, 4):
        del blocks[(x, 63, 0)]         # a 3-wide chasm: no floor at all
    return FakeRegion(lo, hi, blocks), FEET, (5, 64, 0)


def _overhang():
    # TODO(unsure): meant to model a leaning branch (LOS blocked straight-on, open from one side, no material
    # needed either way) -- only calibrated for the tree/mine row; the other acts' anchor content below is a guess.
    blocks, lo, hi = _floor()
    blocks[(4, 64, 0)] = "stone"       # the direct approach cell is walled; (4, 64, 1) is still open beside it
    return FakeRegion(lo, hi, blocks), FEET, (5, 64, 0)


def _water_edge():
    """A sunken, walled pool: the anchor sits at the water's own level with no dry rim within fill_spot's near-level
    search box, but the wall around it (already solid, no material needed) can be stood on from above."""
    blocks, lo, hi = _floor()
    for x in range(2, 7):
        for z in range(-2, 3):
            for y in range(61, 65):
                blocks[(x, y, z)] = "stone"
    for x in range(3, 6):
        for z in range(-1, 2):
            blocks[(x, 61, z)] = "water"
            for y in (62, 63, 64):
                blocks.pop((x, y, z), None)
    return FakeRegion(lo, hi, blocks), FEET, (4, 61, 0)


def _cave_pocket():
    """The anchor sealed behind one stone wall: diggable for free (no material), not yet open-faced."""
    blocks, lo, hi = _floor()
    for y in (64, 65):
        blocks[(4, y, 0)] = "stone"    # the one wall between the approach and the pocket
    return FakeRegion(lo, hi, blocks), FEET, (5, 64, 0)


TERRAINS = {
    "flat": _flat, "one_step": _one_step, "cliff": _cliff, "overhang": _overhang,
    "water_edge": _water_edge, "tall_tree": lambda: _tree(6), "cave_pocket": _cave_pocket,
}
BLOCKS = (0, 8, 32)


# ---------------------------------------------------------------- per-act plan-side (today's production call) vs run-side

def _with_anchor(region, anchor, name):
    blocks = dict(region.blocks)
    blocks[anchor] = name
    return FakeRegion(region.lo, region.hi, blocks)


def _mine_row(terrain):
    region, feet, anchor = TERRAINS[terrain]()
    if terrain == "tall_tree":
        # plan-side: wood.trunk_batch (wood.py:16), TRUNK_REACH-only -- no region, no material at all
        base = (anchor[0], 64, anchor[2])
        overhead = [(anchor[0], y, anchor[2]) for y in range(65, anchor[1] + 1)]
        included = {(t["x"], t["y"], t["z"]) for t in wood.trunk_batch(base, overhead, want=99)}
        plan = anchor in included or anchor == base
        return region, feet, anchor, (lambda blocks: plan)
    region = _with_anchor(region, anchor, "iron_ore")

    def plan_of(blocks):
        # plan-side: cost.Cost.refused (cost.py:177), which wraps nav.known_refusal (nav.py:1322) over the round's read
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), _inv(blocks), {}, [], region)
        return _cost.Cost(snap, memory()).refused(anchor, "mine") is None
    return region, feet, anchor, plan_of


def _place_row(terrain):
    # place act never travels to a target (craft.Station.__enter__, craft.py:90): the comparison is local to feet.
    region, feet, _anchor = TERRAINS[terrain]()
    st = state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5)

    def plan_of(blocks):
        # plan-side: skillcore.free_spots (skillcore.py:300), used via free_spots_here (skillcore.py:322)
        return bool(free_spots(region, st, reach=4))
    return region, feet, feet, plan_of


def _use_row(terrain):
    region, feet, anchor = TERRAINS[terrain]()
    region = _with_anchor(region, anchor, "crafting_table")

    def plan_of(blocks):
        # plan-side: cost.Cost.station_near (cost.py:321), fed one sighting of the table at `anchor`
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), _inv(blocks),
                                            {"crafting_table": [{"x": anchor[0], "y": anchor[1], "z": anchor[2],
                                                                 "distance": float(sum(abs(anchor[i] - feet[i]) for i in range(3)))}]},
                                            [], region)
        return _cost.Cost(snap, memory()).station_near("minecraft:crafting_table")
    return region, feet, anchor, plan_of


def _use_item_row(terrain):
    region, feet, anchor = TERRAINS[terrain]()
    if terrain != "water_edge":
        region = _with_anchor(region, anchor, "water")

    def plan_of(blocks):
        # plan-side: fluids.fill_spot (fluids.py:69) -- its own standable()/clear_line(), never nav.plan_way
        return fluids.fill_spot(region, feet) is not None
    return region, feet, anchor, plan_of


def _attack_row(terrain):
    region, feet, anchor = TERRAINS[terrain]()

    def plan_of(blocks):
        # plan-side: gather.HUNT_REACH (gather.py:548), the distance nav.chase closes to before gather.hunt attacks
        return math.dist(feet, anchor) <= gather.HUNT_REACH
    return region, feet, anchor, plan_of


ROW_BUILDERS = {
    "mine": _mine_row, "place": _place_row, "use": _use_row,
    "use_item": _use_item_row, "attack": _attack_row,
}
# run-side kind per act: mine/place/use are nav.gate's own kinds (nav.py:309); use_item/attack have none today
# (not in APPROACHING) so they borrow stands_for's "use" ray check as the invariant's substitute oracle.
RUN_KIND = {"mine": "mine", "place": "place", "use": "use", "use_item": "use", "attack": "use"}


class ReachAgreement(unittest.TestCase):
    """P2: the plan-side verdict for an act equals nav.gate's run-side verdict, for every terrain x blocks carried."""

    def test_rows(self):
        rows, red = 0, []
        for terrain in TERRAINS:
            for act, build in ROW_BUILDERS.items():
                region, feet, target, plan_of = build(terrain)
                for blocks in BLOCKS:
                    rows += 1
                    name = f"{terrain} x {act} x blocks={blocks}"
                    with self.subTest(name):
                        plan = plan_of(blocks)
                        run = _run_reachable(region, feet, target, RUN_KIND[act], _inv(blocks))
                        if plan != run:
                            red.append(name)
                        self.assertEqual(plan, run, f"{name}: plan={plan} run={run}")
        # not reached once a subTest fails the whole test, but kept for a future non-failing run of the sweep
        print(f"rows={rows} red={len(red)}: {red}")


if __name__ == "__main__":
    unittest.main()
