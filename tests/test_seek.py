"""One column for "go to where one of these is", whatever it is.

Ore had `prospect` (dig until you hit some); animals had nothing, so "no sheep recorded" meant sheep do not exist —
wool unreachable, therefore no bed, no food, and seven goals that wanted a furnace unplannable in one go. The fix is
not another special case: finding something is the same action whether it is iron, a sheep, water or a village.

    seek:<what>  →  at:<what>
    cost = known ? walk there : measured search distance, else the declared prior

The prior is a fixed guess in play.toml, used only when there is no sample. One real find replaces it outright — it
does not decay, compound, or grow with failures, because none of those are measurements.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import actions  # noqa: E402
from bonobo.solve import Unsolvable, cost_of, reach_cost, solve  # noqa: E402


class Nothing(actions.Costs):
    """A world the resource map knows nothing about."""

    def __init__(self):
        super().__init__(lambda kinds: None)


class Knows(actions.Costs):
    """A world where everything asked for is `at` blocks away."""

    def __init__(self, at=20.0, searched=None):
        super().__init__(lambda kinds: at)
        self._searched = searched

    def searched(self, kinds):
        return self._searched


class OneColumnForFinding(unittest.TestCase):
    def test_every_resource_can_be_sought_even_when_none_is_known(self):
        table = actions.table(Nothing(), {})
        names = {a.name for a in table}
        for what in ("minecraft:sheep", "minecraft:cow", "coal_ore", "iron_ore"):
            self.assertIn(f"seek:{what}", names, f"nothing can find {what}")

    def test_seeking_produces_being_there(self):
        table = actions.table(Nothing(), {})
        seek = next(a for a in table if a.name == "seek:minecraft:sheep")
        self.assertEqual(seek.effect.get(actions.at("minecraft:sheep")), 1)

    def test_a_known_one_is_cheaper_than_an_unknown_one(self):
        near = next(a for a in actions.table(Knows(at=20.0), {}) if a.name == "seek:minecraft:sheep")
        blind = next(a for a in actions.table(Nothing(), {}) if a.name == "seek:minecraft:sheep")
        self.assertLess(near.cost_s, blind.cost_s)

    def test_the_prior_is_used_only_without_a_sample_and_never_moves(self):
        blind = next(a for a in actions.table(Nothing(), {}) if a.name == "seek:minecraft:sheep")
        again = next(a for a in actions.table(Nothing(), {}) if a.name == "seek:minecraft:sheep")
        self.assertEqual(blind.cost_s, again.cost_s, "the prior is a constant, not a counter")

    def test_a_measurement_replaces_the_prior_outright(self):
        measured = next(a for a in actions.table(Knows(at=None, searched=40.0), {})
                        if a.name == "seek:minecraft:sheep")
        blind = next(a for a in actions.table(Nothing(), {}) if a.name == "seek:minecraft:sheep")
        self.assertNotEqual(measured.cost_s, blind.cost_s)


class WhatItUnblocks(unittest.TestCase):
    """The failure that prompted this, end to end: no sheep recorded, therefore no bed in the world."""

    def test_wool_is_reachable_without_ever_having_seen_a_sheep(self):
        self.assertIsNotNone(cost_of(actions.table(Nothing(), {}), {}, {"wool": 3}))

    def test_a_bed_is_plannable_from_nothing(self):
        plan = solve(actions.table(Nothing(), {}), {}, {"bed": 1})
        self.assertIn("craft:bed", plan.counts)
        self.assertTrue(any(n.startswith("seek:") for n in plan.counts), plan.counts)

    def test_a_furnace_is_plannable_from_nothing(self):
        self.assertIsNotNone(cost_of(actions.table(Nothing(), {}), {}, {"minecraft:furnace": 1}))

    def test_seeing_one_makes_it_cheaper_not_possible(self):
        blind = cost_of(actions.table(Nothing(), {}), {}, {"wool": 3})
        seen = cost_of(actions.table(Knows(at=10.0), {}), {}, {"wool": 3})
        self.assertLess(seen, blind, "knowing where the sheep are should only ever be an advantage")


class NoSpecialCases(unittest.TestCase):
    def test_ore_and_animals_use_the_same_column(self):
        table = actions.table(Nothing(), {})
        kinds = {a.tag[0] for a in table if a.name.startswith("seek:")}
        self.assertEqual(kinds, {"seek"}, "finding is one kind of action, not two")

    def test_prospect_and_travel_are_gone(self):
        names = {a.name.split(":")[0] for a in actions.table(Nothing(), {})}
        self.assertNotIn("prospect", names)
        self.assertNotIn("travel", names)


if __name__ == "__main__":
    unittest.main()
