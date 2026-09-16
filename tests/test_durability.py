"""A pickaxe with three points left is not a pickaxe for a tunnel.

Bag space became a dimension because a full bag does not stop the digging, it stops the keeping. Durability is the
same shape: a tool that will break in three blocks does not stop the plan being written, it stops it being carried
out — and the planner, which prices the whole tunnel against one pickaxe, has no way to see that. The result is a
plan that dies in the middle and a `ToolMissing` discovered by failing.

So: `tool:pickaxe:N` says a tool of that tier exists; `uses:pickaxe` says how many blocks it has left. Mining
spends one, crafting a pickaxe supplies a toolful, and the solver puts "make another" in the plan when the tunnel
is longer than what is in hand.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions  # noqa: E402
from bonobo.solve import cost_of, solve  # noqa: E402

EVERYWHERE = actions.Costs(lambda kinds: 20.0)


def table():
    return actions.table(EVERYWHERE, {})


class WearIsSpent(unittest.TestCase):
    def test_mining_spends_a_use(self):
        mine = next(a for a in table() if a.name == "mine:minecraft:coal")
        self.assertLess(mine.effect.get(actions.uses_dim("pickaxe"), 0), 0,
                        "digging must consume the tool it digs with")

    def test_making_a_tool_supplies_uses(self):
        craft = next(a for a in table() if a.name == "craft:minecraft:stone_pickaxe")
        self.assertGreater(craft.effect.get(actions.uses_dim("pickaxe"), 0), 0)

    def test_a_worn_tool_plans_a_replacement(self):
        """Twenty blocks of tunnel, three uses left: the plan must contain another pickaxe."""
        state = {"tool:pickaxe:0": 1, "tool:pickaxe:1": 1, actions.uses_dim("pickaxe"): 3,
                 "at:stone": 1, "bag_free": 20}
        plan = solve(table(), state, {"minecraft:cobblestone": 20})
        self.assertTrue(any("pickaxe" in n and n.startswith("craft:") for n in plan.counts),
                        f"twenty blocks on three uses: {plan.counts}")

    def test_a_fresh_tool_plans_no_replacement(self):
        state = {"tool:pickaxe:0": 1, "tool:pickaxe:1": 1, actions.uses_dim("pickaxe"): 200,
                 "at:stone": 1, "bag_free": 20}
        plan = solve(table(), state, {"minecraft:cobblestone": 20})
        self.assertFalse([n for n in plan.counts if "pickaxe" in n and n.startswith("craft:")], plan.counts)

    def test_wearing_out_makes_work_dearer_not_impossible(self):
        """Asked of the PLAN, not of `cost_of`: the price table is a per-unit lower bound and cannot see a
        quantity constraint — "this tunnel is longer than what is left in the tool" only appears once the columns
        are actually combined."""
        def price(uses):
            return solve(table(), {"tool:pickaxe:0": 1, "tool:pickaxe:1": 1, actions.uses_dim("pickaxe"): uses,
                                   "at:stone": 1, "bag_free": 20}, {"minecraft:cobblestone": 20}).cost_s
        self.assertGreater(price(1), price(200))


class TheWorldReportsIt(unittest.TestCase):
    def test_the_state_vector_carries_the_remaining_uses(self):
        import inspect
        src = inspect.getsource(actions.state_of)
        self.assertIn("uses_dim", src, "the vector must say how much tool is left, not just that one exists")


if __name__ == "__main__":
    unittest.main()
