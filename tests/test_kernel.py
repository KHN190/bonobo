"""The kernel is the whole planner, so these are the only things it can get wrong: what it charges, what it lets
through, and which of the two planners it is being. Everything domain-specific is tested where the domain lives."""
import random
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


class HoldingADecision(unittest.TestCase):
    """A decision may only change when there is a reason: its commitment ran out, an assumption failed, or a
    challenger beat it by the margin. Random sequences with a few percent of jitter per tick — the shape of a
    threat walking one step nearer — so anything that dithers shows up without a scenario being written for it.
    """

    SEEDS = range(200)

    class Act:
        def __init__(self, name, gain, cost_s):
            self.name, self.gain, self.cost_s, self.commitment_s = name, gain, cost_s, cost_s

        def effect(self, state):
            return dict(state, price=max(0.0, state["price"] - self.gain * state["noise"][self.name]))

    class Jittery:
        def __init__(self, acts):
            self.actions = list(acts)
            self.default = acts[-1]

        def price(self, state):
            return state["price"]

        def admissible(self, state, action):
            return True, ""

    def model(self):
        A = self.Act
        return self.Jittery([A("fight", 40.0, 2.0), A("evade", 38.0, 3.0), A("reshape", 36.0, 1.0),
                             A("carry on", 0.0, 0.0)])

    def states(self, seed, ticks=60):
        rng = random.Random(seed)
        noise = {n: 1.0 for n in ("fight", "evade", "reshape", "carry on")}
        out = []
        for _ in range(ticks):
            noise = {k: max(0.5, min(1.5, v + rng.uniform(-0.04, 0.04))) for k, v in noise.items()}
            out.append({"price": 100.0, "noise": dict(noise)})
        return out

    def sweep(self, holds=None, margin=kernel.MARGIN):
        switches = reasons = 0
        for seed in self.SEEDS:
            held, last = kernel.Held(margin=margin), None
            for tick, state in enumerate(self.states(seed)):
                choice = held.decide(self.model(), state, now=tick * 0.2, holds=holds)
                reasons += held.because is not None
                switches += choice.name != last
                last = choice.name
        return switches, reasons

    def test_it_changes_no_more_often_than_it_has_reason_to(self):
        switches, reasons = self.sweep()
        self.assertLessEqual(switches, reasons + len(self.SEEDS))

    def test_re_deciding_every_tick_would_dither(self):
        """The control: without holding, this fixture really does flip about — otherwise the test above is empty."""
        flips = 0
        for seed in self.SEEDS:
            last = None
            for state in self.states(seed):
                name = kernel.choose(self.model(), state).name
                flips += name != last
                last = name
        self.assertGreater(flips, len(self.SEEDS), "the jitter is too small to prove anything")

    def test_a_broken_assumption_releases_it_at_once(self):
        held = kernel.Held()
        states = self.states(1)
        held.decide(self.model(), states[0], now=0.0)
        held.decide(self.model(), states[1], now=0.01, holds=lambda *_: False)
        self.assertEqual(held.because, "assumption")

    def test_a_challenger_must_win_by_more_than_the_margin(self):
        self.assertLess(self.sweep(margin=1.5)[0], self.sweep(margin=1.0)[0])


if __name__ == "__main__":
    unittest.main()
