"""A mining batch that steps into a mined cell (nav.dig_order's "step") stands there next: its head room is broken
first, so the chain's stand is 2 high and the door's stand test holds for the mines after it (accept9 02:41:52: "no
stand reached" for stone 1–3 cells from the feet — the stepped-into cell's head in stone, the eye inside it). Built
by the production batch (gather.mine_segment_commands), judged by the gate's own test (nav.unstandable); imports
only what the base has, so the row is red there by assertion."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import gather, nav  # noqa: E402
from bonobo.world import Inventory  # noqa: E402
from tests.world import FakeRegion, inventory  # noqa: E402

FEET = (12988, 98, 13084)
BROKEN = [(12989, 99, 13084), (12989, 98, 13084), (12988, 99, 13084), (12988, 98, 13084), (12988, 99, 13083),
          (12988, 98, 13083), (12987, 99, 13083), (12987, 98, 13083), (12988, 99, 13082), (12988, 98, 13082),
          (12987, 99, 13082), (12987, 98, 13082), (12987, 98, 13081), (12988, 98, 13081)]
CELLS = [(12987, 98, 13084), (12989, 98, 13082), (12988, 99, 13081)]


def accept9_ground():
    """The hill as detail.log broke it (02:41:17–02:41:36): stone to y97, dirt to y99, the broken cells open."""
    lo, hi = (12980, 90, 13075), (12996, 106, 13092)
    blocks = {(x, y, z): "stone" if y <= 97 else "dirt" for x in range(lo[0], hi[0] + 1)
              for y in range(lo[1], 100) for z in range(lo[2], hi[2] + 1)}
    for c in BROKEN:
        blocks.pop(c, None)
    blocks[(12987, 100, 13085)] = "crafting_table"
    return FakeRegion(lo, hi, blocks)


class AStepIsTwoHigh(unittest.TestCase):

    def chain(self, region):
        state = {"inv": Inventory(inventory()), "feet": FEET, "region": region}
        return gather.mine_segment_commands(state, (CELLS, "minecraft:cobblestone", 0))

    def test_accept9(self):
        region = accept9_ground()
        chain = nav.standable_order(self.chain(region), region, FEET)
        stands = [s for _t, s in nav.task_stands(chain, FEET)]
        self.assertIn((12989, 98, 13082), stands, "fixture: the batch steps into the mined cell")
        # must fail (accept9): the stepped-into cell's head in stone, the 3rd mine refused at the gate
        self.assertIsNone(nav.unstandable(chain, region, FEET))

    def test_head_room_only_where_it_is_solid(self):
        """A head already open is not mined (a mine on air fails the chain: "nothing to mine (air)")."""
        region = accept9_ground()
        region.blocks.pop((12989, 99, 13082), None)
        mines = [(t["x"], t["y"], t["z"]) for t in self.chain(region) if t["type"] == "mine"]
        self.assertNotIn((12989, 99, 13082), mines)
        self.assertEqual(sorted(mines), sorted(CELLS))

    def test_the_head_before_the_cell(self):
        """Top down: the head room broken before the cell under it is stepped into."""
        tasks = [(t["type"], (t.get("x"), t.get("y"), t.get("z"))) for t in self.chain(accept9_ground())]
        head, cell = ("mine", (12989, 99, 13082)), ("goto", (12989, 98, 13082))
        self.assertLess(tasks.index(head), tasks.index(cell))


if __name__ == "__main__":
    unittest.main()
