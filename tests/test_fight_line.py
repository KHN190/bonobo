"""S5: an optional fight (one a goal asks for) starts only with the health above critical to cover its loss's quantile
(estimate.fight_line_ok over fight_cost's own numbers); threat fights never read it. One predicate: hunt, the blaze
rods and the dragon's veto call it."""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import beliefs, brain, combat, estimate, gather, knowledge  # noqa: E402
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
        sword = knowledge.held_tiers(carried).get("sword", 0)
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
