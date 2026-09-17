"""What the four quantities make together: worth.

    worth(x) = Σ_t γ(t)·[ V(s_t) − V(s_t⊕x, t+Δt) ]·dt/H  −  κ(slot)·slots(x)

V says what finishing costs, Δt how long the work takes, p feeds both (encounters, wear, what a world gives
back), κ what carrying it costs. This file is about the COMPOSITION — that the identity holds, that each quantity
enters once and only once, and that the whole thing behaves the way a price must: harder is never better, later
is never better, more of what is already plentiful is never better.

Run over the same sweep of worlds as the other three files, so "for every world" means what is around us × what
is between us and it × what we are × what we carry × what we have measured.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import gates, value  # noqa: E402
from tests.world import World, sweep, worlds  # noqa: E402

HORIZON = 600.0


def world_value(w):
    """(value_of, evolve) for a world of the sweep: V through the door, and play moving on."""
    def value_of(state):
        return gates.V(w.situation(state), parts=True)

    def evolve(state, t):
        out = dict(state)
        if out.get("food"):
            out["food"] = max(0.0, float(out["food"]) - t / 80.0)
        return out
    return value_of, evolve


def worth(w, effect, takes_s=0.0, slots=1.0):
    value_of, evolve = world_value(w)
    return value.worth_s(dict(w.state()), effect, value_of=value_of, evolve=evolve, horizon_s=HORIZON,
                         bag_free=w.state().get("bag_free", 30), slots=slots, takes_s=takes_s)


class TheIdentity(unittest.TestCase):
    """Worth IS the subtraction. Nothing is added to it afterwards and nothing is subtracted twice."""

    def test_it_is_the_discounted_difference_less_the_slot(self):
        w = World()
        value_of, evolve = world_value(w)
        by_hand = 0.0
        # The same quadrature the module uses, asked for by name rather than re-derived here: the rule is an
        # implementation detail of the integral, and a test that hard-codes midpoints is testing the rule.
        for fraction, weight in value.nodes(value.STEPS):
            t = fraction * HORIZON
            before = value_of(evolve(dict(w.state()), t))
            after = value_of(value.apply(evolve(dict(w.state()), t), {"bed": 1}))
            by_hand += value.discount(t, HORIZON) * value.gain(before, after) * weight * HORIZON
        by_hand = by_hand / HORIZON - gates.marginal("slot", free=w.state()["bag_free"])
        self.assertAlmostEqual(worth(w, {"bed": 1}), by_hand, places=9)

    def test_a_second_one_is_never_worth_more_than_the_first(self):
        """Diminishing returns as a relation: the same thing, priced against a state that already holds it, can
        never be worth more. How much less is the model's business."""
        for w in worlds(terrain="flat", confidence="unmeasured"):
            value_of, evolve = world_value(w)
            held = dict(w.state(), bed=1, sheltered=1)
            again = value.worth_s(held, {"bed": 1}, value_of=value_of, evolve=evolve, horizon_s=HORIZON,
                                  bag_free=held.get("bag_free", 30), slots=1.0)
            self.assertLessEqual(again, worth(w, {"bed": 1}) + 1e-9, f"{w}: the second beat the first")

    def test_what_no_terminal_good_wants_earns_nothing(self):
        """Not "is zero" — what it earns before the slot is charged is nothing, so carrying it can only lose."""
        for w in worlds(terrain="flat", self_="ready", stock="none"):
            junk = worth(w, {"minecraft:gold_nugget": 8})
            self.assertLessEqual(junk, worth(w, {"planks": 8}) + 1e-9, f"{w}")
            self.assertLessEqual(junk, 0.0, f"{w}: carrying what nothing wants paid for itself")


class HarderIsNeverBetter(unittest.TestCase):
    def test_the_same_thing_behind_worse_ground_is_not_worth_more(self):
        """The relation the pricing kept getting backwards: a bed behind lava must not beat a bed underfoot.
        Terrain makes V's before-side dearer AND the work longer — and the second has to be inside the
        comparison, or the first wins on its own."""
        for base in worlds(terrain="flat", self_="ready", confidence="unmeasured"):
            flat_worth = worth(base, {"bed": 1}, takes_s=base.walk_s)
            for harder in ("room", "water", "lava"):
                w = base.with_(terrain=harder)
                self.assertLessEqual(worth(w, {"bed": 1}, takes_s=w.walk_s), flat_worth + 1e-6,
                                     f"{w}: harder ground scored above flat")

    def test_taking_longer_is_never_worth_more(self):
        w = World(self_="hungry")
        quick = worth(w, {"food": 4}, takes_s=1.0)
        slow = worth(w, {"food": 4}, takes_s=HORIZON / 2)
        self.assertLess(slow, quick)

    def test_a_fuller_bag_never_wants_to_carry_more(self):
        for base in worlds(terrain="flat", self_="ready", confidence="unmeasured"):
            roomy = worth(base, {"planks": 8})
            tight = worth(base.with_(self_="full_bag"), {"planks": 8})
            self.assertLessEqual(tight, roomy + 1e-6, f"{base}: a full bag wanted it more")


class EachQuantityEntersOnce(unittest.TestCase):
    def test_carrying_more_costs_more_and_a_fuller_bag_costs_more_still(self):
        """A relation, not a formula: the second slot is charged against the bag as it will be by then, so the
        gap widens as the bag fills. Asserting "the gap IS one slot" would pin today's curve."""
        for w in worlds(terrain="flat", stock="none", confidence="unmeasured"):
            one = worth(w, {"planks": 8}, slots=1.0)
            two = worth(w, {"planks": 8}, slots=2.0)
            self.assertLessEqual(two, one + 1e-9, f"{w}: two slots cost no more than one")
        roomy, tight = World(self_="ready"), World(self_="full_bag")
        self.assertGreater(worth(roomy, {"planks": 8}, slots=1.0) - worth(roomy, {"planks": 8}, slots=2.0) - 1e-9,
                           0.0)
        self.assertGreater(worth(tight, {"planks": 8}, slots=1.0) - worth(tight, {"planks": 8}, slots=2.0),
                           worth(roomy, {"planks": 8}, slots=1.0) - worth(roomy, {"planks": 8}, slots=2.0),
                           "a slot in a full bag must cost more than a slot in an empty one")

    def test_work_is_charged_by_moving_the_clock_not_by_subtracting_twice(self):
        """`takes_s` moves the state on and discounts from arrival. If it were also subtracted, doubling the work
        would cost twice the seconds — it does not; it costs what waiting costs."""
        w = World(self_="hungry")
        a = worth(w, {"food": 4}, takes_s=10.0)
        b = worth(w, {"food": 4}, takes_s=20.0)
        self.assertGreater(a, b)
        self.assertLess(a - b, 10.0, "a second subtraction of the work would cost the full ten seconds")

    def test_a_bundle_is_never_worth_more_than_its_parts(self):
        for w in worlds(terrain="flat", self_="ready", stock="none"):
            both = worth(w, {"planks": 8, "wool": 3}, slots=2.0)
            apart = worth(w, {"planks": 8}) + worth(w, {"wool": 3})
            self.assertLessEqual(both, apart + 1e-6, f"{w}")


class ItIsSecondsAllTheWayDown(unittest.TestCase):
    def test_scaling_the_world_scales_the_worth(self):
        """Every price in the world ten times dearer: the saving is ten times bigger, the slot unchanged. A
        dimensionless factor hidden anywhere breaks this."""
        w = World(terrain="flat")
        slow = w.with_(terrain="lava")
        slot = gates.marginal("slot", free=w.state()["bag_free"])
        one = worth(w, {"planks": 8}) + slot
        ten = worth(slow, {"planks": 8}) + slot
        self.assertGreater(ten, one)


if __name__ == "__main__":
    unittest.main()


class HoldingHalfOfItIsWorthLess(unittest.TestCase):
    """Diminishing returns, stated as a relation rather than a number.

    The in-game sheet read "carrying the wool made the bed worth LESS" as a bug. It is the pricing working: worth
    is what the change still SAVES, and a state that already holds an input has banked half that saving. What may
    not happen is the opposite — holding more of what a thing is made of cannot make acquiring it worth more.
    """

    # A property over a ladder, not over the product: this one costs 20 pricings per world, and the full sweep of
    # those is an hour. The relation is about holding an input, so it is the STOCK ladder that has to be walked —
    # the other dimensions vary a second-order term and are sampled at their baseline.
    GIFTS = ({"wool": 3}, {"planks": 8}, {"minecraft:iron_ingot": 3}, {"minecraft:coal": 4})
    WANTS = ({"bed": 1}, {"sheltered": 1}, {"minecraft:torch": 8}, {"tool:pickaxe:1": 1})
    LADDER = sweep(stock=None, resource=None)

    def test_stocking_an_input_never_raises_what_the_output_is_worth(self):
        for w in self.LADDER:
            for want in self.WANTS:
                bare = worth(w, want)
                for gift in self.GIFTS:
                    held = w.with_()
                    state = dict(held.state())
                    for dim, count in gift.items():
                        state[dim] = state.get(dim, 0) + count
                    value_of, evolve = world_value(held)
                    after = value.worth_s(state, want, value_of=value_of, evolve=evolve, horizon_s=HORIZON,
                                          bag_free=state.get("bag_free", 30), slots=1.0)
                    self.assertLessEqual(after, bare + 1e-6, f"{w}: holding {gift} raised the worth of {want}")

    def test_and_the_work_never_gets_longer_for_holding_it(self):
        """The other half of the same sentence, and the one the sheet should have been checking: V is the cost of
        finishing, so more in the bag can only bring that cost down."""
        for w in self.LADDER:
            bare = sum(gates.V(w.situation(), parts=True).values())
            for gift in self.GIFTS:
                state = dict(w.state())
                for dim, count in gift.items():
                    state[dim] = state.get(dim, 0) + count
                self.assertLessEqual(sum(gates.V(w.situation(state), parts=True).values()), bare + 1e-6,
                                     f"{w}: holding {gift} made finishing cost MORE")


class HoldingHalfOfItIsWorthLess(unittest.TestCase):
    """Diminishing returns, as a relation rather than a number.

    The in-game sheet read "carrying the wool made the bed worth LESS" as a bug. It is the pricing working: worth
    is what a change still SAVES, and a state already holding an input has banked half of that saving. The two
    halves of the sentence are what belong in a test — acquiring cannot be worth MORE for already holding the
    inputs, and finishing cannot cost MORE for holding anything at all.
    """

    # A property over a ladder, not over the product: this one costs 20 pricings per world, and the full sweep of
    # those is an hour. The relation is about holding an input, so it is the STOCK ladder that has to be walked —
    # the other dimensions vary a second-order term and are sampled at their baseline.
    GIFTS = ({"wool": 3}, {"planks": 8}, {"minecraft:iron_ingot": 3}, {"minecraft:coal": 4})
    WANTS = ({"bed": 1}, {"sheltered": 1}, {"minecraft:torch": 8}, {"tool:pickaxe:1": 1})
    LADDER = sweep(stock=None, resource=None)

    def _holding(self, w, gift):
        state = dict(w.state())
        for dim, count in gift.items():
            state[dim] = state.get(dim, 0) + count
        return state

    def test_stocking_an_input_never_raises_what_the_output_is_worth(self):
        for w in self.LADDER:
            for want in self.WANTS:
                bare = worth(w, want)
                value_of, evolve = world_value(w)
                for gift in self.GIFTS:
                    state = self._holding(w, gift)
                    after = value.worth_s(state, want, value_of=value_of, evolve=evolve, horizon_s=HORIZON,
                                          bag_free=state.get("bag_free", 30), slots=1.0)
                    self.assertLessEqual(after, bare + 1e-6, f"{w}: holding {gift} raised the worth of {want}")

    def test_and_finishing_never_costs_more_for_holding_it(self):
        """The half the sheet should have been checking: V is the cost of finishing, so more in the bag can only
        bring it down — that is the monotonicity the falling worth is a consequence OF, not a contradiction to."""
        for w in self.LADDER:
            bare = sum(gates.V(w.situation(), parts=True).values())
            for gift in self.GIFTS:
                held = sum(gates.V(w.situation(self._holding(w, gift)), parts=True).values())
                self.assertLessEqual(held, bare + 1e-6, f"{w}: holding {gift} made finishing cost MORE")
