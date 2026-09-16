"""A full bag is a resource problem, not an errand.

The golden `bag_full_in_shaft` caught this: the agent stood in a shaft with 36/36 slots and the model had nothing
to say about it. Tidying was worth a legacy three points — thirty seconds — and lost to whatever else was going,
so the bag stayed full and everything mined after that fell on the floor.

Room in the bag is now a dimension: picking anything up requires a free slot, and `room:tidy` / `room:deposit` are
columns that provide them. Making room is therefore part of whatever plan needs it, in the right order, rather than
a separate candidate competing with the goal it serves.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions, survival as sv  # noqa: E402
from bonobo.solve import cost_of, solve  # noqa: E402

EVERYWHERE = actions.Costs(lambda kinds: 20.0)


def table():
    return actions.table(EVERYWHERE, {})


class ItCosts(unittest.TestCase):
    def state(self, free):
        return sv.make_state(pickaxe=1, sword=1, food_items=8, bed=True, bag_free=free)

    def test_a_comfortable_bag_costs_nothing(self):
        self.assertEqual(sv.bag_loss(self.state(20)), 0.0)

    def test_a_full_bag_costs_the_work_whose_output_is_lost(self):
        self.assertGreater(sv.bag_loss(self.state(0)), 0.0)

    def test_it_gets_worse_as_the_bag_fills(self):
        losses = [sv.bag_loss(self.state(f)) for f in (8, 4, 2, 0)]
        self.assertEqual(losses, sorted(losses), losses)

    def test_making_room_is_worth_what_the_full_bag_costs(self):
        full = self.state(0)
        self.assertGreater(sv.benefit(full, {"bag_free": 16}), 0)


class ItIsPlanned(unittest.TestCase):
    def test_picking_things_up_requires_somewhere_to_put_them(self):
        mine = next(a for a in table() if a.name == "mine:minecraft:coal")
        self.assertEqual(mine.requires.get("bag_free"), 1)

    def test_there_are_columns_that_make_room(self):
        names = {a.name for a in table()}
        self.assertIn("room:tidy", names)
        self.assertIn("room:deposit", names)

    def test_a_full_bag_plans_its_way_out(self):
        """The behaviour the golden was really about: the plan contains making room, in front of the digging."""
        plan = solve(table(), {"bag_free": 0, "tool:pickaxe:0": 1}, {"minecraft:coal": 4})
        self.assertTrue(any(n.startswith("room:") for n in plan.counts), plan.counts)
        order = [a.name for a, _n in plan.steps()]
        self.assertLess(next(i for i, n in enumerate(order) if n.startswith("room:")),
                        next(i for i, n in enumerate(order) if n.startswith("mine:")),
                        f"room must be made before the digging: {order}")

    def test_a_bag_with_room_does_not_plan_to_tidy(self):
        plan = solve(table(), {"bag_free": 20, "tool:pickaxe:0": 1}, {"minecraft:coal": 4})
        self.assertFalse([n for n in plan.counts if n.startswith("room:")], plan.counts)

    def test_a_full_bag_makes_everything_dearer_not_impossible(self):
        full = cost_of(table(), {"bag_free": 0, "tool:pickaxe:0": 1}, {"minecraft:coal": 4})
        room = cost_of(table(), {"bag_free": 20, "tool:pickaxe:0": 1}, {"minecraft:coal": 4})
        self.assertIsNotNone(full)
        self.assertGreater(full, room)


if __name__ == "__main__":
    unittest.main()
