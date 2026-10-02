"""S5: an optional fight (one a goal asks for) starts only with the health above critical to cover its loss's quantile
(estimate.fight_line_ok over fight_cost's own numbers); threat fights never read it. One predicate: hunt, the blaze
rods and the dragon's veto call it."""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import beliefs, brain, combat, data, estimate, gather, knowledge, perception  # noqa: E402
from bonobo.data import critical_hp  # noqa: E402
from tests.world import bag, inventory, state  # noqa: E402

Q = float(beliefs.CONFIG["engage"]["fight_line_q"])


def cdf(n, lam):
    return sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(n + 1))


class LossQuantile(unittest.TestCase):
    def test_rows(self):
        # (mean hp lost, the hit) → the q quantile is the least n hits with P(N ≤ n) ≥ q (a Poisson count)
        for mean, hit in [(3.0, 3.0), (10.0, 4.0), (0.5, 5.0), (25.0, 5.0)]:
            with self.subTest(mean=mean, hit=hit):
                got = estimate.loss_q(mean, hit)
                n = round(got / hit)
                self.assertGreaterEqual(cdf(n, mean / hit), Q)
                if n:
                    self.assertLess(cdf(n - 1, mean / hit), Q, "must fail: a quantile one hit too high")
        self.assertEqual(estimate.loss_q(0.0, 3.0), 0.0)
        self.assertGreater(estimate.loss_q(10.0, 4.0), 10.0, "the quantile is above the mean (p90 not p50)")


class SwordPrice(unittest.TestCase):
    """B5/B6: a fight is priced at the sword level perception reads (wood is not a fist), each hit from the game's
    weapon data at its attack cooldown — no second dps table."""

    def test_the_brain_prices_the_sword_level_not_the_tool_tier(self):
        # (carried, the level melee_loss must be asked at); must fail: a wooden sword (tool tier 0) asked as a fist
        rows = [("a wooden sword is level 1, not a fist", inventory(("wooden_sword", 1)), 1),
                ("nothing carried is a fist", inventory(), 0),
                ("stone is level 1", inventory(("stone_sword", 1)), 1),
                ("netherite (tier 4) is level 3", inventory(("netherite_sword", 1)), 3)]
        args = (None, "minecraft:spider_eye", 1, ["minecraft:spider"], False)
        for name, inv, want in rows:
            with self.subTest(name):
                seen = []
                real = estimate.melee_loss
                with mock.patch.object(estimate, "melee_loss",
                                       lambda kinds, sword, prot: seen.append(sword) or real(kinds, sword, prot)):
                    brain.fight_line_holds(gather.hunt.contract, args, state(health=20.0), bag(inv))
                self.assertEqual(seen, [want])

    def test_a_wooden_sword_costs_less_than_a_fist(self):
        fist = estimate.melee_loss(["minecraft:zombie"], 0, 0.0)[0]
        wood = estimate.melee_loss(["minecraft:zombie"], 1, 0.0)[0]
        self.assertLess(wood, fist, "must fail: a sword priced as bare hands")

    def test_hits_come_from_the_weapon_data(self):
        self.assertNotIn("dps", beliefs.PLAYER, "must fail: a second, hand-written dps table")
        rows = [(0, (data.HAND_DAMAGE, data.HAND_ATTACKS_PER_S)),
                (1, (data.WEAPON_DAMAGE["sword"]["wooden"], data.ATTACKS_PER_S["sword"]["wooden"])),
                (2, (data.WEAPON_DAMAGE["sword"]["iron"], data.ATTACKS_PER_S["sword"]["iron"])),
                (3, (data.WEAPON_DAMAGE["sword"]["diamond"], data.ATTACKS_PER_S["sword"]["diamond"]))]
        for level, want in rows:
            with self.subTest(level=level):
                self.assertEqual(estimate.sword_hit(level), tuple(float(x) for x in want))
        # must fail when the cost reads anything but the data: harder level-1 hits kill the zombie sooner
        before = estimate.melee_loss(["minecraft:zombie"], 1, 0.0)[0]
        with mock.patch.dict(data.WEAPON_DAMAGE["sword"], {"wooden": 10, "golden": 10, "stone": 10}):
            self.assertLess(estimate.melee_loss(["minecraft:zombie"], 1, 0.0)[0], before)


class FightLine(unittest.TestCase):
    def test_rows(self):
        mean, hit = estimate.melee_loss(["minecraft:blaze"], 3, beliefs.protection(0))
        line = critical_hp({}) + estimate.loss_q(mean, hit)
        rows = [("exactly the line: ok", line, True),
                ("must fail: one under the line: refused", line - 1, False),
                ("full health: ok", 20.0, line <= 20.0)]
        for name, hp, want in rows:
            with self.subTest(name):
                self.assertIs(estimate.fight_line_ok(hp, critical_hp({}), mean, hit), want)

    def test_the_skills_declare_and_the_brain_judges(self):
        # each optional fight's contract declares its mobs (fights=); brain.fight_line_holds judges them where the
        # step is offered; a skill without a fight passes
        carried = bag(inventory(("iron_sword", 1)))
        sword = perception.sword_level([t for t, d, _ in carried.tools("sword") if knowledge.usable(d)])
        cases = [(gather.hunt, (None, "minecraft:spider_eye", 1, ["minecraft:spider"], False), "minecraft:spider"),
                 (combat.collect_blaze_rods, (None, 1), "minecraft:blaze")]
        for fn, args, mob in cases:
            mean, hit = estimate.melee_loss([mob], sword, beliefs.protection(0))
            line = critical_hp({}) + estimate.loss_q(mean, hit)
            for hp, want in ((line - 1, False), (line, True)):
                with self.subTest(f"{fn.__name__} at {hp}"):
                    ok, why = brain.fight_line_holds(fn.contract, args, state(health=hp), carried)
                    self.assertIs(ok, want, why)
                    if not want:
                        self.assertIn("fight line", why or "")
        cow = (None, "minecraft:beef", 1, ["minecraft:cow"], False)
        self.assertEqual(brain.fight_line_holds(gather.hunt.contract, cow, state(health=1.0), carried), (True, None))

    def test_valid_delegates(self):
        # brain.valid offers no step whose skill fails the line (must fail: offered at 1 hp)
        from bonobo import dispatch, planner
        step = planner.Step("hunt", "minecraft:spider_eye", 1, {"types": ["minecraft:spider"]})
        snap = mock.Mock(state=state(health=1.0), inv=bag(inventory(("iron_sword", 1))))
        me = mock.Mock(ready=lambda key: True)
        with mock.patch.object(brain, "runnable", lambda st, inv: True), \
                mock.patch.object(dispatch, "runner_for",
                                  lambda ctx, st: (gather.hunt, ("minecraft:spider_eye", 1, ["minecraft:spider"], False))), \
                mock.patch.object(dispatch, "can_start", lambda ctx, st: True):
            self.assertFalse(brain.Brain.valid(me, step, snap, ctx=object()))


if __name__ == "__main__":
    unittest.main()
