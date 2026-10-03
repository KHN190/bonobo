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
    elif name == "slope":            # high on a column, no tread beside it (accept2's slope tree)
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
    """Cost.refused(cell, kind) ⇔ the door's plan_way over the same read ground finds no way and names a read cell."""

    def test_rows(self):
        for kind in KINDS:
            for geo in ("flat", "slope", "overhang", "hole", "pillar"):
                for carried in (0, CARRIED):
                    with self.subTest(kind=kind, geo=geo, carried=carried):
                        blocks, t = geometry(geo, kind)
                        region, feet = FakeRegion(LO, HI, blocks), feet_of(geo)
                        c = cost(region, feet, carried)
                        sk = "use" if kind == "chest" else stand_kind([kind])       # a container: Cost.stored's use
                        steps, why, _s = nav.plan_way(region, feet, t, sk, c.snap.inv, set())
                        door_fails = steps is None and getattr(why, "cell", None) is not None
                        # must fail (accept2): the plan prices a source the door then finds no tread to
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
                    task = {"type": "mine" if sk == "mine" else "use", "x": t[0], "y": t[1], "z": t[2]}
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

        class Unread(FakeRegion):         # the plan's look: the cause's cell not read
            def inside(self, p):
                return tuple(p) != why.cell and super().inside(p)
        region = Unread(LO, HI, {p: n for p, n in blocks.items() if p != why.cell})
        self.assertIsNone(cost(region, feet, 0).refused(t, "mine"))            # unknown: possible
        bans = Versioned()
        bans[why.cell] = float("inf")
        # must fail: the same cause failing again for the next target behind it
        self.assertIsNotNone(cost(region, feet, 0, bans).refused(t, "mine"))


def inventory_of(carried):
    from bonobo.world import Inventory
    return Inventory(inventory(("cobblestone", carried)) if carried else inventory())


if __name__ == "__main__":
    unittest.main()
