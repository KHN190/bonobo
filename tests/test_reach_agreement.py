"""terrain x act x way-blocks carried, plan verdict == run verdict.

Run-side ground truth is now one production function: nav.reach (nav.py:1468) -- the gate's own loop (stands_for,
nav.py:341, else plan_way's way, its place_budget/building_of, nav.py:233/256, tried up to nav.ways_for(act, block),
nav.py:314). `_run_reachable` below is a thin wrapper over it (`.stand is not None`), not a re-implementation: T3
retired the old hand-rolled mirror once nav.reach existed to call directly.

Plan-side rows call the real production functions where they now ask nav.reach themselves (wood.trunk_batch,
wood.py:14; fluids.fill_spot, fluids.py:44; cost.Cost.refused/station_near/_entity, cost.py:199/~330/235) -- so most
rows are expected GREEN today, proving the fix holds. CumulativeBudget stays a deliberate gap: a step priced off a
fresh bag (as gather._cheapest_seed and most callers still do) vs the real leftover bag after the step before it
actually spent (D6) -- a live finding, not a bug in this test.
"""
import unittest

from bonobo import cost as _cost, fluids, nav, wood, world
from bonobo.skillcore import free_spots
from bonobo.world import Inventory
from tests.world import FakeRegion, inventory, memory, state

FEET = (0, 64, 0)
STOCK = {0: (), 8: (("cobblestone", 8),), 32: (("cobblestone", 32),)}


def _inv(blocks):
    return Inventory(inventory(*STOCK[blocks]))


def _floor(lo=(-10, 50, -10), hi=(10, 80, 10)):
    return {(x, 63, z): "stone" for x in range(lo[0], hi[0] + 1) for z in range(lo[2], hi[2] + 1)}, lo, hi


# ---------------------------------------------------------------- the run-side oracle: production nav.reach itself

def _run_reachable(region, feet, target, act, inv, protected=(), down=False, tries=None):
    """nav.reach (nav.py:1468): the gate's own loop, as the one run-side predicate every row compares against."""
    return nav.reach(region, feet, target, act, inv, protected, down, tries).stand is not None


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
    """A sunken, walled pool: the anchor sits at the water's own level with no dry rim beside it, but the wall
    around it (already solid, no material needed) can be stood on from above."""
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
        base = (anchor[0], 64, anchor[2])
        overhead = [(anchor[0], y, anchor[2]) for y in range(65, anchor[1] + 1)]

        def plan_of(blocks):
            # plan-side: wood.trunk_batch (wood.py:14) -- it asks nav.reach itself now, with this same region/inv
            included = {(t["x"], t["y"], t["z"]) for t in wood.trunk_batch(base, overhead, 99, region, feet, _inv(blocks))}
            return anchor in included or anchor == base

        def run_of(blocks):
            return _run_reachable(region, feet, anchor, "mine", _inv(blocks))
        return region, feet, plan_of, run_of
    region = _with_anchor(region, anchor, "iron_ore")

    def plan_of(blocks):
        # plan-side: cost.Cost.refused (cost.py:199), which wraps nav.reach (nav.py:1468) over the round's read
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), _inv(blocks), {}, [], region)
        return _cost.Cost(snap, memory()).refused(anchor, "mine") is None

    def run_of(blocks):
        return _run_reachable(region, feet, anchor, "mine", _inv(blocks))
    return region, feet, plan_of, run_of


def _any_place_reachable(region, feet, inv, radius=4):
    """Run-side when free_spots offered nothing: is ANY cell in its search box actually placeable (nav.reach), so
    a no-offer is only agreement if nothing is really reachable either."""
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            for dy in (-1, 0, 1, 2, -2):
                c = (feet[0] + dx, feet[1] + dy, feet[2] + dz)
                if _run_reachable(region, feet, c, "place", inv):
                    return True
    return False


def _place_row_for(terrain, limit):
    # same cell compared both sides: free_spots' own first offer (or, offering none, any cell nav.reach accepts).
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
    # candidate count than Station's own (craft.py:90, limit=3)
    return _place_row_for(terrain, limit=1)


def _use_row(terrain):
    region, feet, anchor = TERRAINS[terrain]()
    region = _with_anchor(region, anchor, "crafting_table")

    def plan_of(blocks):
        # plan-side: cost.Cost.station_near, fed one sighting of the table at `anchor` -- it asks "use" via
        # distance/refused, which wraps nav.reach the same way the mine row's Cost.refused does
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), _inv(blocks),
                                            {"crafting_table": [{"x": anchor[0], "y": anchor[1], "z": anchor[2],
                                                                 "distance": float(sum(abs(anchor[i] - feet[i]) for i in range(3)))}]},
                                            [], region)
        return _cost.Cost(snap, memory()).station_near("minecraft:crafting_table")

    def run_of(blocks):
        return _run_reachable(region, feet, anchor, "use", _inv(blocks))
    return region, feet, plan_of, run_of


def _crop(region, lo, hi):
    return FakeRegion(lo, hi, {c: n for c, n in region.blocks.items() if all(lo[i] <= c[i] <= hi[i] for i in range(3))})


def _use_item_row(terrain):
    region, feet, anchor = TERRAINS[terrain]()
    if terrain != "water_edge":
        region = _with_anchor(region, anchor, "water")
    lo, hi = nav.read_bounds([feet, anchor])     # the box a round's read actually covers (nav.py:480)
    small = _crop(region, lo, hi)

    def plan_of(blocks):
        # plan-side: fluids.fill_spot (fluids.py:44) -- it asks nav.reach("use_item", inv) itself now, over the
        # same read box production reads one of these from
        return fluids.fill_spot(small, feet, "water", _inv(blocks)) is not None

    def run_of(blocks):
        # same region as plan (both asked the same question, nav.py:480's box), same act ("use_item", nav.py:309)
        return _run_reachable(small, feet, anchor, "use_item", _inv(blocks))
    return region, feet, plan_of, run_of


def _attack_row(terrain):
    region, feet, anchor = TERRAINS[terrain]()

    def plan_of(blocks):
        # plan-side: cost.Cost._entity (cost.py:235) skips prey the door refuses -- self.refused(cell, "attack"),
        # i.e. nav.reach's own "attack" branch (nav.py:341 ENTITY_ACTS); same function both sides ask now
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), _inv(blocks), {}, [], region)
        return _cost.Cost(snap, memory()).refused(anchor, "attack") is None

    def run_of(blocks):
        # run-side: nav.reach's own "attack" branch (nav.py:341 ENTITY_ACTS; now in APPROACHING, nav.py:309)
        return _run_reachable(region, feet, anchor, "attack", _inv(blocks))
    return region, feet, plan_of, run_of


ROW_BUILDERS = {
    "mine": _mine_row, "place": _place_row, "place_sitting": _place_sitting_row, "use": _use_row,
    "use_item": _use_item_row, "attack": _attack_row,
}


class ReachAgreement(unittest.TestCase):
    """P2: the plan-side verdict for an act equals nav.reach's run-side verdict, for every terrain x blocks carried."""

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
        print(f"rows={rows} red={len(red)}: {red}")


def _single_gap(width):
    """One walled, 1-wide chasm of `width`, far from any other (its own private corridor): a throwaway scene used
    only to MEASURE what nav.reach actually spends on a gap this wide -- not an assumed tread-per-cell count."""
    lo, hi = (-10, 50, -10), (width + 20, 80, 10)
    blocks = {(x, 63, 0): "stone" for x in range(lo[0], hi[0] + 1)}
    for z in (-1, 1):
        for x in range(lo[0], hi[0] + 1):
            for y in range(63, 68):
                blocks[(x, y, z)] = "stone"
    for x in range(1, 1 + width):
        blocks.pop((x, 63, 0), None)
    target = (1 + width, 64, 0)
    blocks[target] = "iron_ore"
    return FakeRegion(lo, hi, blocks), FEET, target


def _spend(width, carried):
    """What nav.reach actually spends crossing a `width`-wide gap off a fresh `carried`, or None if unreachable
    fresh (production pricing, not an assumed width-to-tread ratio: a gap's real spend can be less than its width)."""
    region, feet, target = _single_gap(width)
    got = nav.reach(region, feet, target, "mine", _inv(carried))
    return got.spent if got.stand is not None else None


def _starving_widths(carried, max_width=40):
    """(gap1, gap2) measured, not assumed: gap1 the widest gap that still prices reachable fresh off `carried`
    (so it spends as much of place_budget(carried) as a single gap can); gap2 the narrowest gap that ALSO prices
    reachable fresh off the full `carried`, yet spends more than what's left after gap1's real spend (D6: plan
    re-prices every step off a fresh bag; only a sequential run sees gap1's spend starve gap2)."""
    spend1 = width1 = None
    for w in range(1, max_width + 1):
        s = _spend(w, carried)
        if s is None:
            break               # unreachable fresh past here (budget exceeded): the widest reachable gap was `w - 1`
        spend1, width1 = s, w
    assert width1 is not None, f"not even a 1-wide gap crosses fresh off {carried}"
    remaining_budget = nav.place_budget(carried - spend1)
    for w in range(1, max_width + 1):
        s = _spend(w, carried)
        if s is not None and s > remaining_budget:
            return width1, w
    raise AssertionError(f"no width <= {max_width} both prices fresh off {carried} and exceeds the leftover budget "
                         f"{remaining_budget} after gap1 (width={width1}, spend={spend1}) -- widen max_width")


def _two_gaps(carried):
    """A single 1-wide, walled corridor (z = -1/+1 solid up to y67: no sideways detour around a gap) with two
    chasms in it, gap2 past gap1 (a real sequential trip: cross gap1 to reach A, then continue past it to reach
    B through gap2) -- sized by `_starving_widths` so each gap alone prices reachable fresh off `carried`."""
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
    """D6: a plan's second step must be priced off the bag the FIRST step actually leaves. Plan-side points at the
    real threading: cost.Cost.reach/refused(cell, kind, at, spent) -- `at` the place step A left the body, `spent`
    its way blocks (cost.py:183/199, step_bag cost.py:173) -- the same plumbing planner.price_as_run (planner.py:
    1593) carries through a real plan via Cost.way_spent (cost.py:505). If this still disagrees with the run, the
    threading itself has a gap, not this test."""

    def test_second_step_sees_the_first_steps_spend(self):
        carried = 8
        region, feet, (a, b) = _two_gaps(carried)
        inv = _inv(carried)
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), inv, {}, [], region)
        cost = _cost.Cost(snap, memory())

        plan_a = cost.refused(a, "mine") is None
        got_a = cost.reach(a, "mine")           # the real production call step A's own price comes from
        self.assertEqual(plan_a, got_a.stand is not None)

        # plan-side for B: the real D6-threaded question -- reachable from where A left the body, with the bag A
        # actually spent (cost.py:199's `at`/`spent`, not a fresh Cost.refused(b, "mine"))
        plan_b = cost.refused(b, "mine", at=got_a.stand, spent=got_a.spent) is None

        # run-side: nav.reach itself, step A first (spending real blocks), step B from where A actually left the
        # body with what nav.reach says was actually spent (Reached.spent) -- the one true sequential execution
        run_a = nav.reach(region, feet, a, "mine", inv)
        self.assertIsNotNone(run_a.stand)
        inv_after_a = nav.less_way_blocks(inv, run_a.spent)
        run_b = nav.reach(region, run_a.stand, b, "mine", inv_after_a)

        self.assertEqual(plan_b, run_b.stand is not None,
                         "D6: Cost.refused(b, at=A's stand, spent=A's spend) must agree with the real "
                         "sequential run -- if not, the threading itself is the bug, not this test")


def _deep_vein(depth):
    """A solid stone column from the surface down to well past `depth`, an ore cell `depth` below the start --
    reached only by a staircase (nav.py:1217 stair_steps descends STAIR_STEPS=8 per plan_way call), so a vein this
    deep needs ceil(depth / STAIR_STEPS) plan_way calls -- nav.reach's own ways, one per try."""
    lo, hi = (-30, 10, -30), (30, 80, 30)
    blocks = {(x, y, z): "stone" for x in range(-5, 10) for z in range(-5, 10) for y in range(10, 64)}
    target = (0, 64 - depth, 0)
    blocks[target] = "iron_ore"
    return FakeRegion(lo, hi, blocks), FEET, target


class ManyWays(unittest.TestCase):
    """P2/nav.ways_for(act, block): an ore gets MINE_PASSES + WAY_TRIES tries (nav.py:314-320), not the gate's own
    WAY_TRIES=3 -- a vein needing more than 3 but no more than that many ways must still agree, on both sides,
    that it is reachable."""

    def test_deep_vein_needs_more_than_way_tries(self):
        depth = 8 * 4 + 1   # 33: ceil(33/8) = 5 plan_way calls -- > WAY_TRIES(3), <= ways_for("mine", ore)=13
        region, feet, target = _deep_vein(depth)
        inv = _inv(32)
        tries_needed = -(-depth // nav.STAIR_STEPS)
        self.assertGreater(tries_needed, nav.WAY_TRIES)
        self.assertLessEqual(tries_needed, nav.ways_for("mine", "iron_ore"))

        # plan-side: cost.Cost.refused (cost.py:199) -> nav.reach with no explicit tries -> ways_for(act, block)
        snap = world.Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), inv, {}, [], region)
        plan = _cost.Cost(snap, memory()).refused(target, "mine") is None

        # run-side: nav.reach's own tries for this act+block (nav.ways_for("mine", "iron_ore")), tries=None default
        run_full = _run_reachable(region, feet, target, "mine", inv)
        # and the point of ways_for existing at all: capped at the generic WAY_TRIES, this same vein is NOT reached
        run_capped = _run_reachable(region, feet, target, "mine", inv, tries=nav.WAY_TRIES)

        self.assertEqual(plan, run_full, f"plan={plan} run(ways_for)={run_full}")
        self.assertFalse(run_capped, "a vein needing its 4th way must fail under the gate's OWN WAY_TRIES=3 cap -- "
                                      "proof ways_for(act, block), not a hardcoded WAY_TRIES, is what must be shared")


if __name__ == "__main__":
    unittest.main()
