"""accept_fresh_iron_pickaxe (accept3, 21:21): a trunk's base logs (y74, y75) cut from inside the column, the body
standing in it; the chop batch asked log y77 before y76 — no stand sees y77 past y76 ("no stand for mine … after 3
ways") — and a craft between rounds lifted that ban, so the round asked the same cell 4 times more."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import nav, skillcore, wood  # noqa: E402
from bonobo.cost import Cost  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from bonobo.world import Inventory, Snapshot, Versioned  # noqa: E402
from tests.world import FakeRegion, inventory, memory, state  # noqa: E402

X, Z, FLOOR_Y = 12986, 12999, 74             # the trunk's column; the body's feet level (the cut base)
LOGS = [(X, y, Z) for y in range(76, 80)]
LO, HI = (X - 8, FLOOR_Y - 6, Z - 8), (X + 8, FLOOR_Y + 10, Z + 8)


def scene():
    blocks = {(x, y, z): "dirt" for x in range(LO[0], HI[0] + 1) for z in range(LO[2], HI[2] + 1)
              for y in range(LO[1], FLOOR_Y)}
    blocks.update({c: "oak_log" for c in LOGS})
    return FakeRegion(LO, HI, blocks)


def cost(region, feet, carried, bans):
    inv = inventory(*carried)
    hits = {"oak_log": [{"x": c[0], "y": c[1], "z": c[2], "distance": float(c[1] - feet[1])} for c in LOGS]}
    snap = Snapshot.from_readings(state(x=feet[0] + .5, y=feet[1], z=feet[2] + .5), inv, hits, [], region)
    return Cost(snap, memory(), bans)


class TheChopChainHasAStand(unittest.TestCase):
    """P2/K1: a trunk with a log the door can stand for is chopped — the batch's order passes the door's own stand
    test (nav.standable_order: stands_for over what its earlier mines dug), from inside the column or beside it."""

    def test_rows(self):
        region = scene()
        for feet in ((X, FLOOR_Y, Z), (X + 1, FLOOR_Y, Z)):
            for want in (2, len(LOGS)):
                with self.subTest(feet=feet, want=want):
                    chain = wood.trunk_batch(LOGS[0], LOGS[1:], want)
                    # must fail (accept3): y77 sent before y76 from inside the column, no stand reached
                    self.assertIsNone(nav.unstandable(nav.standable_order(chain, region, feet), region, feet))


class ARefusedCellIsNotAskedAgain(unittest.TestCase):
    """P2/K1 with E5: a cell the door could not reach stays refused until a state that changes a way changes (blocks
    to place, a tool): a craft of planks and sticks between rounds is not one."""

    def test_rows(self):
        feet, banned = (X, FLOOR_Y, Z), LOGS[1]
        before = [("oak_log", 2)]
        crafted = [("oak_planks", 4), ("stick", 4)]
        rows = [("must fail (accept3): planks and sticks crafted lift the no-stand ban", before + crafted, True),
                ("blocks to place change the way: asked again", before + [("cobblestone", 16)], False)]
        for name, carried, still in rows:
            with self.subTest(name):
                bans = Versioned()
                kinds_then = frozenset(Inventory(inventory(*before)).slots[i]["id"] for i in range(len(before)))
                bans[banned] = skillcore.Ban(float("inf"), skillcore.ban_state(feet, kinds_then))
                c = cost(scene(), feet, carried, bans)
                self.assertEqual(banned in c.not_there(True), still)
                if still:
                    self.assertNotEqual(c.site(Step("gather", "log", 1, {})), banned)


if __name__ == "__main__":
    unittest.main()
