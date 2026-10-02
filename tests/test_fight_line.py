"""S5: an optional fight (one a goal asks for) starts only with the health above critical to cover its loss's quantile
(estimate.fight_line_ok over fight_cost's own numbers); threat fights never read it. One predicate: hunt, the blaze
rods and the dragon's veto call it."""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import beliefs, brain, combat, data, estimate, gather, knowledge  # noqa: E402
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
    """A fight is priced at the sword carried (data.weapon_hit)."""

    def test_the_brain_prices_the_weapon_held(self):
        # (carried, the sword melee_loss must be asked with); must fail: a wooden sword (tool tier 0) asked as a fist
        rows = [("a wooden sword is a wooden sword, not a fist", inventory(("wooden_sword", 1)), "minecraft:wooden_sword"),
                ("nothing carried is the hand", inventory(), None),
                ("stone is stone, not wood", inventory(("stone_sword", 1)), "minecraft:stone_sword"),
                ("must fail: of several, the one the attack holds (knowledge.attack_weapon), not the hardest hitter",
                 inventory(("stone_sword", 1), ("iron_sword", 1)),
                 knowledge.attack_weapon(bag(inventory(("stone_sword", 1), ("iron_sword", 1))), beliefs.COMMON_FOE_HP)),
                ("netherite (tier 4, not craftable) is still carried", inventory(("netherite_sword", 1)),
                 "minecraft:netherite_sword")]
        args = (None, "minecraft:spider_eye", 1, ["minecraft:spider"], False)
        for name, inv, want in rows:
            with self.subTest(name):
                seen = []
                real = estimate.melee_loss
                with mock.patch.object(estimate, "melee_loss",
                                       lambda kinds, sword, prot: seen.append(sword) or real(kinds, sword, prot)):
                    brain.fight_line_holds(gather.hunt.contract, args, state(health=20.0), bag(inv))
                self.assertEqual(seen, [want])

    def test_each_sword_costs_its_own(self):
        loss = {s: estimate.melee_loss(["minecraft:zombie"], s, 0.0)[0]
                for s in (None, "minecraft:wooden_sword", "minecraft:stone_sword")}
        self.assertLess(loss["minecraft:wooden_sword"], loss[None], "must fail: a sword priced as bare hands")
        self.assertLess(loss["minecraft:stone_sword"], loss["minecraft:wooden_sword"],
                        "must fail: a stone sword priced as a wooden one (one level, two swords)")

    def test_hits_come_from_the_weapon_data(self):
        self.assertNotIn("dps", beliefs.PLAYER, "must fail: a second, hand-written dps table")
        rows = [(None, (data.HAND_DAMAGE, data.HAND_ATTACKS_PER_S)),
                ("hand", (data.HAND_DAMAGE, data.HAND_ATTACKS_PER_S)),
                ("minecraft:iron_pickaxe", (data.HAND_DAMAGE, data.HAND_ATTACKS_PER_S)),
                ("minecraft:stone_sword", (data.WEAPON_DAMAGE["sword"]["stone"], data.ATTACKS_PER_S["sword"]["stone"])),
                ("minecraft:netherite_sword",
                 (data.WEAPON_DAMAGE["sword"]["netherite"], data.ATTACKS_PER_S["sword"]["netherite"])),
                ("minecraft:iron_axe", (data.WEAPON_DAMAGE["axe"]["iron"], data.ATTACKS_PER_S["axe"]["iron"]))]
        for item, want in rows:
            with self.subTest(item=item):
                self.assertEqual(data.weapon_hit(item), tuple(float(x) for x in want))
        # must fail when the cost reads anything but the data: a harder stone hit kills the zombie sooner
        before = estimate.melee_loss(["minecraft:zombie"], "minecraft:stone_sword", 0.0)[0]
        with mock.patch.dict(data.WEAPON_DAMAGE["sword"], {"stone": 10}):
            self.assertLess(estimate.melee_loss(["minecraft:zombie"], "minecraft:stone_sword", 0.0)[0], before)
        # and knowledge.kill_s (the weapon choice) reads the same lookup
        self.assertEqual(knowledge.kill_s("minecraft:stone_sword", 20), math.ceil(20 / 5) / 1.6)


class FightLine(unittest.TestCase):
    def test_rows(self):
        mean, hit = estimate.melee_loss(["minecraft:blaze"], "minecraft:diamond_sword", beliefs.protection(0))
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
        sword = "minecraft:iron_sword"
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
