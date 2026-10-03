"""craft.py's station-spot choice (craft._sitting, craft.py ~426): the spot skillcore.free_spots offers for placing
a new table/furnace must pass nav.reach's own stand test (P2/K1) -- never one the gate then refuses."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import nav  # noqa: E402
from bonobo.skillcore import free_spots  # noqa: E402
from bonobo.world import Inventory  # noqa: E402
from tests.world import FakeRegion, inventory, state  # noqa: E402

FEET = (0, 64, 0)


def _floor(lo=(-6, 60, -6), hi=(6, 70, 6)):
    return {(x, 63, z): "stone" for x in range(lo[0], hi[0] + 1) for z in range(lo[2], hi[2] + 1)}, lo, hi


def _flat():
    blocks, lo, hi = _floor()
    return FakeRegion(lo, hi, blocks), ()


def _sealed_box():
    """Walled and capped on every side but the body's own 2-high cell, the walls undiggable (`protected`): no
    cell in reach has an approach the gate accepts, dig or no dig."""
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


def _state():
    return state(x=FEET[0] + .5, y=FEET[1], z=FEET[2] + .5)


class StationSpotPassesTheGate(unittest.TestCase):
    def test_flat_offers_a_reachable_spot(self):
        region, protected = _flat()
        inv = Inventory(inventory())
        spots = free_spots(region, _state(), reach=4, limit=1, inv=inv, protected=protected)
        self.assertTrue(spots, "a flat spot: offered")
        self.assertIsNotNone(nav.reach(region, FEET, spots[0], "place", inv, protected).stand,
                              "must fail: offered a spot the gate then refuses")

    def test_sealed_offers_nothing_rather_than_a_refused_spot(self):
        region, protected = _sealed_box()
        inv = Inventory(inventory())
        # must fail: a boxed-in cell offered though every one is refused (undiggable walls, nav.reach)
        self.assertEqual(free_spots(region, _state(), reach=4, limit=1, inv=inv, protected=protected), [])
        # sanity: the same geometry, walls left diggable, does offer one -- the box alone wasn't the filter
        self.assertTrue(free_spots(region, _state(), reach=4, limit=1, inv=inv, protected=()),
                         "the sealed-box fixture must itself have a candidate were it reachable")

    def test_inv_none_keeps_the_old_geometry_only_offer(self):
        """Every other caller (survive.py, store.py) passes no inv: free_spots must not start refusing for them."""
        region, protected = _sealed_box()
        self.assertTrue(free_spots(region, _state(), reach=4, limit=1),
                        "must fail: the gate check ran though inv was never passed")


if __name__ == "__main__":
    unittest.main()
