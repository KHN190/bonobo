"""The action table: the properties the solver relies on, checked without a game.

The table is generated from the same recipe data the old planner used, so these tests are not about Minecraft —
they are about the translation. Every bug found here was a translation bug: a group dimension that never met its
member ("torch is unplannable because mining makes `minecraft:coal` and the recipe wants `coal`"), a tool tier that
added up (two wooden pickaxes making an iron one), a walk folded into the per-unit cost.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions  # noqa: E402
from bonobo.solve import Unsolvable, cost_of, solve  # noqa: E402

EVERYWHERE = actions.Costs(lambda kinds: 20.0)          # every resource 20 blocks away
NOWHERE = actions.Costs(lambda kinds: None)             # nothing known anywhere


def table(cost=EVERYWHERE):
    return actions.table(cost, {})


class Dimensions(unittest.TestCase):
    def test_an_item_produces_its_groups_too(self):
        out = actions.produce("minecraft:coal", 1)
        self.assertEqual(out["minecraft:coal"], 1)
        self.assertEqual(out.get("coal"), 1, "recipes ask for the group; mining hands out the member")

    def test_spending_a_member_spends_the_group(self):
        out = actions.consume("minecraft:coal", 1)
        self.assertLess(out["minecraft:coal"], 0)
        self.assertLess(out["coal"], 0, "or the same coal could be spent twice, once under each name")

    def test_tool_tiers_are_levels_not_amounts(self):
        wood = next(a for a in table() if a.name == "craft:minecraft:wooden_pickaxe")
        iron = next(a for a in table() if a.name == "craft:minecraft:iron_pickaxe")
        self.assertEqual(wood.effect.get(actions.tool_dim("pickaxe", 1)), None)
        self.assertEqual(iron.effect.get(actions.tool_dim("pickaxe", 2)), 1)
        self.assertEqual(iron.effect.get(actions.tool_dim("pickaxe", 0)), 1, "a better tool satisfies lesser needs")


class FixedVersusVariable(unittest.TestCase):
    """Getting there is paid once; the work is paid per unit. That split is what `travel:` columns exist for."""

    def test_the_walk_is_a_column_of_its_own(self):
        names = {a.name for a in table()}
        self.assertIn("seek:coal_ore", names)
        mine = next(a for a in table() if a.name == "mine:minecraft:coal")
        self.assertIn(actions.at("coal_ore"), mine.requires)
        self.assertNotIn(actions.at("coal_ore"), mine.effect, "arriving is not consumed by mining")

    def test_mining_more_does_not_pay_the_walk_again(self):
        tbl = table()
        one = solve(tbl, {"tool:pickaxe:0": 1}, {"minecraft:coal": 1})
        ten = solve(tbl, {"tool:pickaxe:0": 1}, {"minecraft:coal": 10})
        self.assertEqual(one.counts.get("seek:coal_ore"), 1)
        self.assertEqual(ten.counts.get("seek:coal_ore"), 1)
        walk = next(a for a in tbl if a.name == "seek:coal_ore").cost_s
        self.assertAlmostEqual(ten.cost_s - one.cost_s, ten.counts["mine:minecraft:coal"] * 3.0 - 3.0, places=1)
        self.assertGreater(walk, 0)

    def test_a_place_the_world_does_not_offer_is_dear_not_absent(self):
        """Not knowing where something is used to delete the column, which made the thing itself impossible —
        no sheep recorded, therefore no wool, no bed, and seven goals unplannable. Now it has a price."""
        blind = next(a for a in table(NOWHERE) if a.name == "seek:coal_ore")
        known = next(a for a in table() if a.name == "seek:coal_ore")
        self.assertGreater(blind.cost_s, known.cost_s)


class Reachability(unittest.TestCase):
    def test_a_torch_is_plannable(self):
        # The regression that proved the group dimensions were not meeting: coal from mining, coal in the recipe.
        self.assertIsNotNone(cost_of(table(), {}, {"minecraft:torch": 8}))

    def test_everything_is_plannable_with_nowhere_known_to_go(self):
        # No trees, no ore, no animals RECORDED. Searching is what it costs, not what it forbids.
        blind, known = cost_of(table(NOWHERE), {}, {"bed": 1}), cost_of(table(), {}, {"bed": 1})
        self.assertIsNotNone(blind)
        self.assertGreater(blind, known)

    def test_what_is_already_held_needs_no_plan(self):
        self.assertEqual(cost_of(table(), {"bed": 1}, {"bed": 1}), 0.0)


class Shelter(unittest.TestCase):
    """Four ways to survive a night, and the solver picks by price — the if-chain inside the skill is gone."""

    def test_blocks_in_the_bag_mean_walling_in(self):
        p = solve(table(), {"building": 20}, {"sheltered": 1})
        self.assertEqual(p.counts, {"shelter:wall in": 1})

    def test_a_pickaxe_and_no_blocks_mean_digging_in(self):
        p = solve(table(), {"tool:pickaxe:0": 1}, {"sheltered": 1})
        self.assertEqual(p.counts, {"shelter:dig in": 1})

    def test_with_neither_it_plans_its_way_to_one_of_them(self):
        p = solve(table(), {}, {"sheltered": 1})
        self.assertTrue({"shelter:dig in", "shelter:wall in", "shelter:hut"} & set(p.counts))
        self.assertGreater(p.cost_s, 0)

    def test_sleeping_needs_a_bed_and_a_roof(self):
        sleep = next(a for a in table() if a.name == "sleep")
        self.assertEqual(sleep.requires.get("bed"), 1)
        self.assertEqual(sleep.requires.get("sheltered"), 1)


class Weapons(unittest.TestCase):
    def test_hunting_something_that_fights_back_needs_a_sword(self):
        spider = next(a for a in table() if a.name == "hunt:minecraft:string")
        self.assertEqual(spider.requires.get(actions.tool_dim("sword", 1)), 1)

    def test_hunting_a_cow_does_not(self):
        cow = next(a for a in table() if a.name == "hunt:minecraft:beef")
        self.assertNotIn(actions.tool_dim("sword", 1), cow.requires)

    def test_string_therefore_plans_a_sword_first(self):
        p = solve(table(), {}, {"minecraft:string": 1})
        self.assertTrue(any("sword" in n for n in p.counts), p.counts)


class Costs(unittest.TestCase):
    def test_a_walk_costs_more_the_further_it_is(self):
        near = actions.Costs(lambda kinds: 10.0).walk_s(["x"])
        far = actions.Costs(lambda kinds: 100.0).walk_s(["x"])
        self.assertGreater(far, near)

    def test_an_unknown_place_costs_nothing_because_it_has_no_column(self):
        self.assertIsNone(actions.Costs(lambda kinds: None).walk_s(["x"]))

    def test_every_column_costs_time(self):
        for a in table():
            self.assertGreater(a.cost_s, 0, f"{a.name} is free, which makes every plan that uses it infinite")


if __name__ == "__main__":
    unittest.main()
