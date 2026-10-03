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
        return region, feet, (lambda blocks: plan), (lambda blocks: _run_reachable(region, feet, anchor, "mine", _inv(blocks)))
    region = _with_anchor(region, anchor, "iron_ore")

    def plan_of(blocks):
        # plan-side: gather._cheapest_seed (gather.py:187) picks among candidates by nav.plan_way's own price, but
        # the boolean "is there a way at all" it and cost.Cost.refused (cost.py:177) both ask is nav.known_refusal
        # (nav.py:1322/1328): same question the run side answers, asked over the SAME region (fed the round's read)
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), _inv(blocks), {}, [], region)
        return _cost.Cost(snap, memory()).refused(anchor, "mine") is None

    def run_of(blocks):
        return _run_reachable(region, feet, anchor, "mine", _inv(blocks))
    return region, feet, plan_of, run_of


def _any_place_reachable(region, feet, inv, radius=4):
    """Run-side when free_spots offered nothing: is ANY cell in its search box actually placeable (stands_for/
    plan_way), so a no-offer is only agreement if nothing is really reachable either."""
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            for dy in (-1, 0, 1, 2, -2):
                c = (feet[0] + dx, feet[1] + dy, feet[2] + dz)
                if _run_reachable(region, feet, c, "place", inv):
                    return True
    return False


def _place_row_for(terrain, limit):
    # same cell compared both sides: free_spots' own first offer (or, offering none, any cell stands_for accepts).
    region, feet, _anchor = TERRAINS[terrain]()
    st = state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5)

    def plan_of(blocks):
        # plan-side: skillcore.free_spots (skillcore.py:300); limit distinguishes the two real call sites
        return bool(free_spots(region, st, reach=4, limit=limit))

    def run_of(blocks):
        spots = free_spots(region, st, reach=4, limit=limit)
        if spots:
            return _run_reachable(region, feet, spots[0], "place", _inv(blocks))
        return _any_place_reachable(region, feet, _inv(blocks))
    return region, feet, plan_of, run_of


def _place_row(terrain):
    # plan-side: free_spots_here(limit=3) via craft.Station.__enter__ (craft.py:90), no travel (it places at feet)
    return _place_row_for(terrain, limit=3)


def _place_sitting_row(terrain):
    # plan-side: free_spots_here(limit=1) via craft._sitting's keep_table branch (craft.py:376) -- a narrower
    # candidate count than Station's own (craft.py:90, limit=3): the accept4-a06 "craft.py:78 vs :366 radius" note
    return _place_row_for(terrain, limit=1)


def _use_row(terrain):
    region, feet, anchor = TERRAINS[terrain]()
    region = _with_anchor(region, anchor, "crafting_table")

    def plan_of(blocks):
        # plan-side: cost.Cost.station_near (cost.py:321), fed one sighting of the table at `anchor` -- it already
        # travels (station_near -> distance -> ... -> nav.known_refusal), so this one is expected to mostly agree
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), _inv(blocks),
                                            {"crafting_table": [{"x": anchor[0], "y": anchor[1], "z": anchor[2],
                                                                 "distance": float(sum(abs(anchor[i] - feet[i]) for i in range(3)))}]},
                                            [], region)
        return _cost.Cost(snap, memory()).station_near("minecraft:crafting_table")

    def run_of(blocks):
        return _run_reachable(region, feet, anchor, "use", _inv(blocks))
    return region, feet, plan_of, run_of


def _crop(region, centre, pad=(5, 3, 5)):
    """Pure: the small box fluids.py:127 actually reads around one water candidate -- not the full world. A cell
    outside it is invisible to fill_spot, however far travel could still reach it (fix 1: the same question, not a
    narrower one, on the plan side; this crop is where that narrowing genuinely lives in production)."""
    lo = tuple(centre[i] - pad[i] for i in range(3))
    hi = tuple(centre[i] + pad[i] for i in range(3))
    return FakeRegion(lo, hi, {c: n for c, n in region.blocks.items() if all(lo[i] <= c[i] <= hi[i] for i in range(3))})


def _use_item_row(terrain):
    region, feet, anchor = TERRAINS[terrain]()
    if terrain != "water_edge":
        region = _with_anchor(region, anchor, "water")
    small = _crop(region, anchor)

    def plan_of(blocks):
        # plan-side: fluids.fill_water_bucket's own region read (fluids.py:122-127) -- fill_spot (fluids.py:69) then
        # only ever sees `small`, never a stand that needs building/bridging beyond that box
        return fluids.fill_spot(small, feet) is not None

    def run_of(blocks):
        # run-side travels on the FULL region (gate re-reads the box live, nav.py:479), not the pre-cropped one
        return _run_reachable(region, feet, anchor, "use", _inv(blocks))
    return region, feet, plan_of, run_of


def _no_dig_steps(region, feet, target, inv, protected=()):
    """The way nav.chase would take (gather.py:586 'walk and bridge to animals, never tunnel'): plan_way's own
    answer, discarded if it contains a mine step."""
    steps, _why, _secs = nav.plan_way(region, feet, target, "mine", inv, protected)
    if steps is None or any(t["type"] == "mine" for t in steps):
        return None
    return steps


def _chase_reachable(region, feet, target, inv, protected=(), tries=None):
    """Plan-side for attack: does nav.chase (nav.py:1381, never tunnelling) actually close to gather.HUNT_REACH
    (gather.py:548) of the target -- the real decider gather.hunt uses before it attacks."""
    tries = tries or nav.WAY_TRIES
    feet = tuple(feet)
    for _ in range(tries):
        if math.dist(feet, target) <= gather.HUNT_REACH:
            return True
        steps = _no_dig_steps(region, feet, target, inv, protected)
        if not steps:
            return False
        region, feet = _apply(region, steps, feet)
    return math.dist(feet, target) <= gather.HUNT_REACH


def _attack_row(terrain):
    region, feet, anchor = TERRAINS[terrain]()

    def plan_of(blocks):
        return _chase_reachable(region, feet, anchor, _inv(blocks))

    def run_of(blocks):
        # run-side: the invariant's oracle (attack isn't in nav.APPROACHING, nav.py:309 -- nothing gates it today)
        return _run_reachable(region, feet, anchor, "use", _inv(blocks))
    return region, feet, plan_of, run_of


ROW_BUILDERS = {
    "mine": _mine_row, "place": _place_row, "place_sitting": _place_sitting_row, "use": _use_row,
    "use_item": _use_item_row, "attack": _attack_row,
}


class ReachAgreement(unittest.TestCase):
    """P2: the plan-side verdict for an act equals nav.gate's run-side verdict, for every terrain x blocks carried."""

    def test_rows(self):
        rows, red = 0, []
        for terrain in TERRAINS:
            for act, build in ROW_BUILDERS.items():
                region, feet, plan_of, run_of = build(terrain)
                for blocks in BLOCKS:
                    rows += 1
                    name = f"{terrain} x {act} x blocks={blocks}"
                    with self.subTest(name):
                        plan = plan_of(blocks)
                        run = run_of(blocks)
                        if plan != run:
                            red.append(name)
                        self.assertEqual(plan, run, f"{name}: plan={plan} run={run}")
        # not reached once a subTest fails the whole test, but kept for a future non-failing run of the sweep
        print(f"rows={rows} red={len(red)}: {red}")


def _starving_widths(carried):
    """Pure: (gap1, gap2) so that EACH gap looks affordable priced alone, fresh, off `carried` (gap1 <= budget,
    gap2 <= budget), but the two together truly starve the second once the first has actually spent its share
    (gap1 + gap2 > budget): gap1 spends the whole of place_budget(carried) (nav.py:256) in one go, so the carried
    stock left for gap2 is `carried - budget`, whose OWN budget (place_budget of what's left) is what gap2 must
    then exceed by 1. TODO(unsure): assumes one tread spent per gap cell (tunnel_steps) -- not measured here."""
    budget = nav.place_budget(carried)
    gap1 = budget
    gap2 = nav.place_budget(carried - gap1) + 1
    assert gap2 <= budget, f"gap2={gap2} must still look affordable off a fresh {carried} (budget={budget})"
    return gap1, gap2


def _two_gaps(carried):
    """A single 1-wide, walled corridor (z = -1/+1 solid up to y67: no sideways detour around a gap) with two
    chasms in it, sized by `_starving_widths` so cost.Cost.refused/gather._cheapest_seed (which price each step off
    a FRESH Inventory(carried), gather.py:196, cost.py:188 -- never decremented across steps in one plan) call both
    reachable, while the real sequential run starves the second once the first has actually spent its share (D6)."""
    gap1, gap2 = _starving_widths(carried)
    lo, hi = (-10, 50, -10), (60, 80, 10)
    blocks = {(x, 63, 0): "stone" for x in range(lo[0], hi[0] + 1)}
    for z in (-1, 1):
        for x in range(lo[0], hi[0] + 1):
            for y in range(63, 68):
                blocks[(x, y, z)] = "stone"          # the corridor wall: no stepping around a gap sideways
    targets, start = [], 1
    for width in (gap1, gap2):
        for x in range(start, start + width):
            blocks.pop((x, 63, 0), None)
        ore = (start + width, 64, 0)
        blocks[ore] = "iron_ore"
        targets.append(ore)
        start += width + 6                            # solid ground between the two gaps
    return FakeRegion(lo, hi, blocks), FEET, targets


class CumulativeBudget(unittest.TestCase):
    """D6: a plan's second step must be priced off the bag the FIRST step actually leaves, not the bag the plan
    started with. gather._cheapest_seed/cost.Cost.refused re-derive a fresh Inventory(blocks=8) for every step they
    price (gather.py:196 `inv, priced = Inventory(), []`; cost.py:188 `self.snap.inv`, never decremented across
    steps in the same plan) -- expected red today."""

    def test_second_step_sees_the_first_steps_spend(self):
        carried = 8
        region, feet, (a, b) = _two_gaps(carried)
        # plan-side: both steps priced independently off the SAME starting inv (what gather._cheapest_seed/
        # cost.Cost.refused actually do -- neither is handed the other step's planned spend)
        plan_a = _cost.Cost(world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5),
                                                         _inv(carried), {}, [], region), memory()).refused(a, "mine") is None
        plan_b = _cost.Cost(world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5),
                                                         _inv(carried), {}, [], region), memory()).refused(b, "mine") is None
        self.assertTrue(plan_a and plan_b, "both steps must look doable off the starting bag for this to be a real plan")
        # run-side: step A actually runs first (spending its blocks out of the SAME inventory), then step B is
        # priced/run off what is actually left -- the one true sequential execution nav.gate performs
        inv = _inv(carried)
        ok_a, region2, feet2, inv2 = self._run_and_spend(region, feet, a, inv)
        self.assertTrue(ok_a)
        ok_b, _region3, _feet3, _inv3 = self._run_and_spend(region2, feet2, b, inv2)
        # must fail: plan said both reachable off 8; run finds the second starved by the first's actual spend
        self.assertEqual((plan_a, plan_b), (True, ok_b), "D6: plan must price step 2 off step 1's leftover bag")

    @staticmethod
    def _run_and_spend(region, feet, target, inv):
        """_run_reachable, but also returns the bag after the placed blocks it actually spent (nav.building_of/
        place_budget, nav.py:233/256) -- what the NEXT step in the same plan should be priced with (D6)."""
        tries, feet = nav.WAY_TRIES, tuple(feet)
        for _ in range(tries):
            if nav.stands_for("mine", region, feet, target):
                return True, region, feet, inv
            steps, why, _secs = nav.plan_way(region, feet, target, "mine", inv, ())
            if steps is None:
                return False, region, feet, inv
            if not steps:
                return True, region, feet, inv
            spent_now = sum(1 for t in steps if t["type"] == "place")  # this way's own spend, not the running total
            region, feet = _apply(region, steps, feet)
            left = max(0, inv.count("minecraft:cobblestone") - spent_now)
            inv = Inventory(inventory(("cobblestone", left))) if left else Inventory(inventory())
        return nav.stands_for("mine", region, feet, target), region, feet, inv


def _deep_vein(depth):
    """A solid stone column from the surface down to well past `depth`, an ore cell `depth` below the start --
    reached only by a staircase (nav.py:1217 stair_steps descends STAIR_STEPS=8 per plan_way call), so a vein this
    deep needs ceil(depth / STAIR_STEPS) plan_way calls -- `reach`'s own ways (nav.py:1456), one per try."""
    lo, hi = (-30, 10, -30), (30, 80, 30)
    blocks = {(x, y, z): "stone" for x in range(-5, 10) for z in range(-5, 10) for y in range(10, 64)}
    target = (0, 64 - depth, 0)
    blocks[target] = "iron_ore"
    return FakeRegion(lo, hi, blocks), FEET, target


class ManyWays(unittest.TestCase):
    """P2/nav.ways_for: a mined source gets MINE_PASSES + WAY_TRIES tries (nav.py:314-317), not the gate's own
    WAY_TRIES=3 -- a vein needing more than 3 but no more than that many ways must still agree, on both sides,
    that it is reachable (and both sides must be asking `ways_for("mine")`, not a hardcoded WAY_TRIES, or they'd
    disagree the moment a vein needs its 4th way)."""

    def test_deep_vein_needs_more_than_way_tries(self):
        depth = 8 * 4 + 1   # 33: ceil(33/8) = 5 plan_way calls -- > WAY_TRIES(3), <= ways_for("mine")=13
        region, feet, target = _deep_vein(depth)
        inv = _inv(32)
        self.assertGreater(-(-depth // nav.STAIR_STEPS), nav.WAY_TRIES)
        self.assertLessEqual(-(-depth // nav.STAIR_STEPS), nav.ways_for("mine"))

        # plan-side: cost.Cost.refused (cost.py:199) -> nav.reach with no explicit tries -> ways_for(kind) inside it
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), inv, {}, [], region)
        plan = _cost.Cost(snap, memory()).refused(target, "mine") is None

        # run-side: the gate's own tries for this act (nav.ways_for("mine")), not the generic WAY_TRIES
        run_full = _run_reachable(region, feet, target, "mine", inv, tries=nav.ways_for("mine"))
        # and the point of ways_for existing at all: capped at the generic WAY_TRIES, this same vein is NOT reached
        run_capped = _run_reachable(region, feet, target, "mine", inv, tries=nav.WAY_TRIES)

        self.assertEqual(plan, run_full, f"plan={plan} run(ways_for)={run_full}")
        self.assertFalse(run_capped, "a vein needing its 4th way must fail under the gate's OWN WAY_TRIES=3 cap -- "
                                      "proof ways_for(act), not a hardcoded WAY_TRIES, is what must be shared")


if __name__ == "__main__":
    unittest.main()
