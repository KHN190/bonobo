"""κ — what one more unit of something scarce costs.

The fourth: a slot in the bag, a second of drift on an old note, picking one more item up, a step out of the way,
a point of health. Each is a price in seconds, and each rises with scarcity — that is the whole content of the
quantity, and the reason no rule anywhere says "keep four slots free" or "never go more than thirty blocks off
route".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import gates  # noqa: E402
from tests.world import World, worlds  # noqa: E402


class ScarcityIsTheOnlyThingThatMovesIt(unittest.TestCase):
    def test_a_slot_costs_more_as_the_bag_fills(self):
        costs = [gates.marginal("slot", free=f) for f in (36, 20, 12, 6, 3, 1)]
        self.assertEqual(costs, sorted(costs), "the curve must be monotone in how full the bag is")
        self.assertGreater(costs[-1], costs[0])

    def test_room_to_spare_is_free(self):
        self.assertEqual(gates.marginal("slot", free=36000), 0.0)

    def test_an_old_note_costs_more_than_a_fresh_one(self):
        self.assertGreater(gates.marginal("staleness", age_s=86400), gates.marginal("staleness", age_s=60))
        self.assertEqual(gates.marginal("staleness", age_s=0), 0.0)

    def test_blood_is_dearer_when_there_is_less_of_it(self):
        healthy, hurt = World(self_="ready"), World(self_="hurt")
        self.assertGreater(gates.marginal("blood", sstate=hurt.sstate()),
                           gates.marginal("blood", sstate=healthy.sstate()))

    def test_picking_one_more_up_costs_something_but_not_much(self):
        self.assertGreater(gates.marginal("pickup"), 0.0)
        self.assertLess(gates.marginal("pickup"), 5.0)

    def test_a_detour_is_what_it_adds_to_a_journey_not_a_round_trip(self):
        far = gates.marginal("detour", distance=40.0, here=(0, 64, 0), there=(40, 64, 0), via=None)
        on_the_way = gates.marginal("detour", distance=40.0, here=(0, 64, 0), there=(40, 64, 0), via=(80, 64, 0))
        self.assertLessEqual(on_the_way, far, "something we pass anyway must not cost the full trip")

    def test_an_unknown_scarcity_is_an_error_not_a_guess(self):
        with self.assertRaises(KeyError):
            gates.marginal("goodwill")


class EveryWorldPricesItsOwnScarcity(unittest.TestCase):
    def test_the_bag_of_each_world_prices_a_slot(self):
        for w in worlds(terrain="flat", stock="none", confidence="unmeasured"):
            slot = gates.marginal("slot", free=w.state()["bag_free"])
            self.assertGreaterEqual(slot, 0.0, f"{w}")
            if w.self_ == "full_bag":
                self.assertGreater(slot, gates.marginal("slot", free=30), f"{w}: a full bag charged no more")


if __name__ == "__main__":
    unittest.main()
