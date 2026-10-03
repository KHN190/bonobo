"""K1/K2 over every source kind and geometry: the plan refuses a source by the door's own way predicate (nav.plan_way:
Cost.refused), only on what was read, and a failed way names the cell its cause names (NavFailed.pos), the key a
second target behind the same cell is refused by."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, nav  # noqa: E402
from bonobo.cost import Cost, stand_kind  # noqa: E402
from bonobo.data import HARDNESS  # noqa: E402
from bonobo.knowledge import MINE  # noqa: E402
from bonobo.world import Snapshot, Versioned  # noqa: E402
from tests.world import FakeRegion, inventory, memory, state  # noqa: E402
from bonobo.skillcore import Ban  # noqa: E402

FEET = (0, 64, 0)
LO, HI = (-8, 56, -8), (8, 74, 8)
FLOOR = "stone"
ORES = sorted({b for blocks, _tier in MINE.values() for b in blocks if b in HARDNESS and b.endswith("_ore")})
KINDS = ["oak_log", "stone", "sand", "gravel", "dirt", "water", "chest"] + ORES
CARRIED = 2 * nav.BLOCK_RESERVE + 2          # blocks enough that place_budget lets a way place treads


def ground():
    return {(x, y, z): FLOOR for x in range(LO[0], HI[0] + 1) for z in range(LO[2], HI[2] + 1) for y in range(LO[1], FEET[1])}


def geometry(name, kind):
    """(blocks, target): the kind at a target in one shape round the feet on a stone floor."""
    b = ground()
    if name == "flat":
        t = (2, FEET[1], 0)
    elif name == "slope":            # high on a column, no tread beside it
        t = (3, FEET[1] + 5, 0)
        b.update({(3, y, 0): "dirt" for y in range(FEET[1], t[1])})
    elif name == "overhang":         # under a ledge, nothing under it
        t = (3, FEET[1] + 2, 0)
        b.update({(x, t[1] + 1, z): FLOOR for x in range(2, 5) for z in range(-1, 2)})
    elif name == "hole":             # at the bottom of a shaft in the floor
        t = (0, FEET[1] - 4, 3)
        for y in range(t[1] + 1, FEET[1]):
            b.pop((0, y, 3), None)
    elif name == "pillar":           # the feet on a pillar, the target on the floor far below
        t = (4, FEET[1], 0)
        b.update({(0, y, 0): FLOOR for y in range(FEET[1], FEET[1] + 6)})
    else:
        raise KeyError(name)
    b[t] = kind
    return b, t


def feet_of(name):
    return (0, FEET[1] + 6, 0) if name == "pillar" else FEET


def cost(region, feet, carried, blacklist=None):
    inv = inventory(("cobblestone", carried)) if carried else inventory()
    snap = Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), inv, {}, [], region)
    return Cost(snap, memory(), blacklist if blacklist is not None else Versioned())


class ThePlanRefusesByTheDoorsPredicate(unittest.TestCase):
    """Cost.refused(cell, kind) ⇔ the door's loop (nav.reach: plan_way's ways taken, again) over the same read ground
    finds no stand and names a read cell."""

    def test_rows(self):
        for kind in KINDS:
            for geo in ("flat", "slope", "overhang", "hole", "pillar"):
                for carried in (0, CARRIED):
                    with self.subTest(kind=kind, geo=geo, carried=carried):
                        blocks, t = geometry(geo, kind)
                        region, feet = FakeRegion(LO, HI, blocks), feet_of(geo)
                        c = cost(region, feet, carried)
                        sk = "use" if kind == "chest" else stand_kind([kind])       # a container: Cost.stored's use
                        got = nav.reach(region, feet, t, sk, c.snap.inv, set())     # the door's loop (gate)
                        why = got.why
                        door_fails = got.stand is None and getattr(why, "cell", None) is not None
                        # must fail: the plan prices a source the door then finds no tread to
                        self.assertEqual(c.refused(t, sk) is not None, door_fails, why)

    def test_unknown_is_possible(self):
        blocks, t = geometry("pillar", "oak_log")
        region = FakeRegion(LO, HI, blocks)
        c = cost(region, feet_of("pillar"), 0)
        self.assertIsNotNone(c.refused(t, "mine"))
        outside = (HI[0] + 4, FEET[1], 0)
        # must fail: a cell past the read refused (the bot gives up on all just past its view)
        self.assertIsNone(c.refused(outside, "mine"))

    def test_a_refused_source_is_not_the_site(self):
        blocks, t = geometry("pillar", "oak_log")
        feet = feet_of("pillar")
        near = (1, feet[1], 0)                 # beside the feet on the pillar top
        blocks[near] = "oak_log"
        region = FakeRegion(LO, HI, blocks)
        c = cost(region, feet, 0)
        c.snap.hits = {"oak_log": [{"x": p[0], "y": p[1], "z": p[2], "distance": d} for p, d in ((t, 1.0), (near, 2.0))]}
        from bonobo.planner import Step
        self.assertEqual(c.site(Step("gather", "log", 1, {})), near)    # must fail: the nearer log below, no way down, chosen


class AFailedWayIsKeyedByItsCause(unittest.TestCase):
    """reach_stand's NavFailed names the cell plan_way's why names; banned, it refuses every target behind it."""

    def test_rows(self):
        for kind in KINDS:
            for geo in ("slope", "pillar"):
                with self.subTest(kind=kind, geo=geo):
                    blocks, t = geometry(geo, kind)
                    region, feet = FakeRegion(LO, HI, blocks), feet_of(geo)
                    sk = "use" if kind == "chest" else stand_kind([kind])
                    steps, why, _s = nav.plan_way(region, feet, t, sk, inventory_of(0), set())
                    if steps is not None:
                        continue                  # a way: nothing fails
                    task = {"type": sk, "x": t[0], "y": t[1], "z": t[2]}
                    with mock.patch.object(nav, "feet", lambda: feet), \
                            mock.patch.object(nav, "_read_box", lambda cells: region), \
                            mock.patch.object(nav, "inventory_now", lambda: inventory_of(0)):
                        with self.assertRaises(api.NavFailed) as e:
                            nav.reach_stand(task, nav.Policy())
                    # must fail: the failure keyed by its target (each log of one tree a new failure)
                    self.assertEqual(e.exception.pos, why.cell)

    def test_the_cause_banned_bars_what_lies_behind_it(self):
        blocks, t = geometry("pillar", "oak_log")
        feet = feet_of("pillar")
        why = nav.plan_way(FakeRegion(LO, HI, blocks), feet, t, "mine", inventory_of(0), set())[1]
        # the plan's look: the drop beside the pillar not read (every tread the door's ways would need: its cause's
        # cell and the ones past it, nav.reach takes them all)
        unread = {(x, y, z) for x in range(1, HI[0] + 1) for y in range(FEET[1], feet[1]) for z in range(LO[2], HI[2] + 1)} - {t}

        class Unread(FakeRegion):
            def inside(self, p):
                return tuple(p) not in unread and super().inside(p)
        region = Unread(LO, HI, {p: n for p, n in blocks.items() if p not in unread})
        self.assertIsNone(cost(region, feet, 0).refused(t, "mine"))            # unknown: possible
        bans = Versioned()
        bans[why.cell] = Ban(float("inf"))
        # must fail: the same cause failing again for the next target behind it
        self.assertIsNotNone(cost(region, feet, 0, bans).refused(t, "mine"))


class ABuildSpotTheDoorReaches(unittest.TestCase):
    """K1 for builds (a hut, a smelter, a nether portal): the spot chosen is one whose access spot the door's way
    predicate does not refuse — the same known_refusal, over the same ground, with the same bag."""

    def test_rows(self):
        from bonobo import blueprints, building
        for name, bp in sorted(blueprints.REGISTRY.items()):
            for geo in ("flat", "pillar"):
                for carried in (0, CARRIED):
                    with self.subTest(bp=name, geo=geo, carried=carried):
                        blocks, _t = geometry(geo, "stone")
                        region, feet = FakeRegion(LO, HI, blocks), feet_of(geo)
                        inv = inventory_of(carried)
                        got = building.spot_options(bp, FEET, region, nav.Policy(), radius=3, body=feet, inv=inv)
                        if not got:
                            continue
                        _c, origin, turns, _p = got[0]
                        # must fail: a spot whose access the door then finds no way to (pillar, empty bag)
                        self.assertIsNone(nav.known_refusal(region, feet, blueprints.access_spot(bp, origin, turns),
                                                            "stand", inv, set()))


class ADigInThePlanIsTheRunsDigIn(unittest.TestCase):
    """K1 for the night's dig-in: the plan offers it (dig_in_site with the bag) exactly when its own commands can be
    built (dig_in_plan: a safe column, a lid to seal with, room for it)."""

    def test_rows(self):
        from bonobo import survive
        from bonobo.world import BAG_SLOTS, Inventory
        full = Inventory(inventory(*[("stick", 64)] * BAG_SLOTS))      # no block to seal with, no room for one dug
        for geo in ("flat", "hole", "pillar"):
            for bag_name, inv in (("empty", inventory_of(0)), ("blocks", inventory_of(CARRIED)), ("full", full)):
                with self.subTest(geo=geo, bag=bag_name):
                    blocks, _t = geometry(geo, "stone")
                    region, feet = FakeRegion(LO, HI, blocks), feet_of(geo)
                    runs = survive.dig_in_plan({"region": region, "inv": inv, "feet": feet, "protected": set()})[0] is not None
                    # must fail: offered by its safe depth alone, refused by the run for want of a lid
                    self.assertIs(survive.dig_in_site(region, feet, inv=inv), runs)


class APlanReadNeverRaises(unittest.TestCase):
    """A fact read (needs.night_facts → night_ground → dig_in_site → dig_in_plan) says "not here", never raises:
    a column with a fluid under the feet."""

    def test_rows(self):
        from bonobo import survive
        from bonobo.data import HAZARD
        for under in sorted(HAZARD & {"lava", "water"}):
            with self.subTest(under=under):
                blocks, _t = geometry("flat", "stone")
                blocks[(FEET[0], FEET[1] - 1, FEET[2])] = under
                region = FakeRegion(LO, HI, blocks)
                # must fail: NotAvailable("unsafe to dig down here") out of a fact read
                self.assertIs(survive.dig_in_site(region, FEET, inv=inventory_of(CARRIED)), False)
                self.assertIs(survive.dig_in_site(region, FEET), False)
                state_ = {"region": region, "inv": inventory_of(CARRIED), "feet": FEET, "protected": set()}
                _tasks, why = survive.dig_in_plan(state_)
                with self.assertRaises(api.NotAvailable) as e:
                    survive.dig_in_commands(state_)
                # the plan names the fluid; the run's refusal cools where the body stands (no target to plan around)
                self.assertEqual((why.cell, e.exception.pos), ((FEET[0], FEET[1] - 1, FEET[2]), None))


class AWallInThePlanIsTheRunsWallIn(unittest.TestCase):
    """K1 for the night's pod: the plan's needs for it (survive.pod_needs on the snapshot) are its run's own count
    (pod_plan: walls, roof and the supports they stand on), so the plan offers it exactly when the run builds it."""

    def test_rows(self):
        from bonobo import survive
        from bonobo.data import POD_BLOCKS
        for geo in ("flat", "hole", "overhang", "pillar"):
            for carried in (0, POD_BLOCKS, CARRIED):
                with self.subTest(geo=geo, carried=carried):
                    blocks, _t = geometry(geo, "stone")
                    region, feet = FakeRegion(LO, HI, blocks), feet_of(geo)
                    inv = inventory_of(carried)
                    snap = Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), inv, {}, [], region)
                    _tasks, placed, have = survive.pod_plan({"feet": feet, "region": region, "inv": inv, "protected": set()})
                    offered = all(inv.count(item) >= n for item, n in survive.pod_needs(snap))
                    # must fail (pillar, POD_BLOCKS carried): offered by the static count, refused for its supports
                    self.assertEqual(offered, placed <= have)


def inventory_of(carried):
    from bonobo.world import Inventory
    return Inventory(inventory(("cobblestone", carried)) if carried else inventory())


if __name__ == "__main__":
    unittest.main()
