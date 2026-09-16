"""The kernel is the whole planner, so these are the only things it can get wrong: what it charges, what it lets
through, and which of the two planners it is being. Everything domain-specific is tested where the domain lives."""
import unittest

from bonobo import kernel


class Act:
    def __init__(self, name, after, cost_s=0.0, commitment_s=None):
        self.name, self._after, self.cost_s = name, after, cost_s
        if commitment_s is not None:
            self.commitment_s = commitment_s

    def effect(self, state):
        return dict(state, price=self._after)


class Model:
    """Prices are handed in directly: the kernel must not care where seconds come from."""

    def __init__(self, actions, default=None, refuse=()):
        self._actions, self.default, self._refuse = list(actions), default, set(refuse)
        if default is not None:
            self._actions.append(default)

    def price(self, state):
        return state["price"]

    def actions(self, state):
        return self._actions

    def admissible(self, state, action):
        return (False, "refused") if action.name in self._refuse else (True, "")


class Scoring(unittest.TestCase):
    def test_score_is_seconds_saved_minus_seconds_spent(self):
        m = Model([Act("cheap", after=80.0, cost_s=5.0)])
        c = kernel.choose(m, {"price": 100.0})
        self.assertEqual(c.name, "cheap")
        self.assertAlmostEqual(c.value_s, 20.0)
        self.assertAlmostEqual(c.cost_s, 5.0)
        self.assertAlmostEqual(c.score, 15.0)

    def test_the_big_saving_can_lose_to_the_cheap_one(self):
        """The whole point of subtracting instead of dividing: two hours saved for three hours of work is a loss."""
        m = Model([Act("grand", after=0.0, cost_s=500.0), Act("small", after=90.0, cost_s=1.0)])
        self.assertEqual(kernel.choose(m, {"price": 100.0}).name, "small")

    def test_an_action_that_saves_less_than_it_costs_is_not_offered(self):
        idle = Act("idle", after=100.0)
        m = Model([Act("busywork", after=99.0, cost_s=10.0)], default=idle)
        self.assertEqual(kernel.choose(m, {"price": 100.0}).name, "idle")

    def test_making_things_worse_is_not_offered(self):
        idle = Act("idle", after=100.0)
        m = Model([Act("harmful", after=150.0)], default=idle)
        self.assertEqual(kernel.choose(m, {"price": 100.0}).name, "idle")


class Veto(unittest.TestCase):
    def test_refused_actions_are_not_ranked_but_are_reported(self):
        m = Model([Act("best", after=0.0), Act("worse", after=50.0)], refuse=["best"])
        c = kernel.choose(m, {"price": 100.0})
        self.assertEqual(c.name, "worse")
        self.assertEqual(c.rejected, [("best", "refused")])

    def test_the_default_is_exempt_from_the_veto(self):
        """A veto that can refuse "get into cover" leaves no answer, and no answer is standing still."""
        idle = Act("idle", after=99.0)
        m = Model([Act("a", after=0.0)], default=idle, refuse=["a", "idle"])
        self.assertEqual(kernel.choose(m, {"price": 100.0}).name, "idle")

    def test_everything_refused_is_a_fault_not_a_decision(self):
        m = Model([Act("a", after=0.0)], default=Act("idle", after=100.0), refuse=["a"])
        self.assertTrue(any(k == "no action" for k, _ in kernel.choose(m, {"price": 100.0}).fault))


class Commitment(unittest.TestCase):
    def test_commitment_defaults_to_the_whole_action(self):
        """Unsaid means atomic — the safe direction to be wrong in."""
        self.assertEqual(kernel.commitment(Act("a", after=0.0, cost_s=6.0)), 6.0)

    def test_an_interruptible_action_commits_only_to_its_segment(self):
        self.assertEqual(kernel.commitment(Act("dig", after=0.0, cost_s=6.0, commitment_s=0.8)), 0.8)

    def test_the_choice_carries_the_commitment_not_the_cost(self):
        """The next decision point is when the atomic part ends, not when the action does."""
        m = Model([Act("dig", after=50.0, cost_s=6.0, commitment_s=0.8)])
        c = kernel.choose(m, {"price": 100.0})
        self.assertEqual((c.cost_s, c.commitment_s), (6.0, 0.8))


class Markers(unittest.TestCase):
    """The markers are the acceptance criteria themselves, so the judging has to be right even when no game is
    running: a marker that passes an empty log would let everything through."""

    def setUp(self):
        from bonobo import scenarios
        self.sc = scenarios

    def rounds(self, *picks, top=2):
        return [{"pick": p, "top": [None] * top} for p in picks]

    def test_no_rounds_is_not_a_pass(self):
        for check in (self.sc._never_idle, self.sc._knows_how, self.sc._multitasks, self.sc._plans_far):
            self.assertFalse(check([])[0], check.__name__)

    def test_idling_fails_even_once(self):
        rounds = self.rounds(*(["mine"] * 30))
        self.assertTrue(self.sc._never_idle(rounds)[0])
        self.assertFalse(self.sc._never_idle(rounds + [{"pick": None, "top": []}])[0])

    def test_all_five_abilities_are_required(self):
        did = ["sleep", "eat anything", "craft table", "threat:fight", "threat:evade"]
        self.assertTrue(self.sc._knows_how(self.rounds(*did))[0])
        for drop in range(len(did)):
            self.assertFalse(self.sc._knows_how(self.rounds(*(did[:drop] + did[drop + 1:])))[0])

    def test_a_queue_is_not_multitasking(self):
        """Doing one thing over and over, with nothing else in the pool, is what this must catch."""
        self.assertFalse(self.sc._multitasks(self.rounds(*(["mine"] * 20), top=1))[0])

    def test_near_term_work_alone_is_not_far_sighted(self):
        self.assertFalse(self.sc._plans_far(self.rounds(*(["stone pickaxe", "food (≥8)"] * 10)))[0])
        self.assertTrue(self.sc._plans_far(self.rounds("blaze rods (7)"))[0])


if __name__ == "__main__":
    unittest.main()


class BackgroundShare(unittest.TestCase):
    """A side project is worth a quarter of itself — all of itself, unlocks included."""

    def make(self, share):
        from bonobo import priority
        return priority.Candidate("stock blocks", 0, 200, lambda: None, seconds=2.0,
                                  unlocks=[(850.0, 0.5)], share=share)

    def test_the_discount_covers_what_it_unlocks(self):
        from bonobo import priority
        full, side = self.make(1.0), self.make(priority.BACKGROUND)
        self.assertAlmostEqual(side.benefit_s, full.benefit_s * priority.BACKGROUND)

    def test_a_side_project_loses_to_running_from_a_zombie(self):
        """The failure this comes from: 2 s of blocks, +425 s 'unlocked', outscoring an escape by two to one."""
        from bonobo import priority
        escape = priority.Candidate("threat:evade", 0, 160, lambda: None, seconds=209.0, kind="maintenance")
        self.assertGreater(escape.score, self.make(priority.BACKGROUND).score)
