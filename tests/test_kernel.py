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
    # (actions offered, the default, refused) at price 100 → (chosen, value_s, cost_s, score, rejected, fault kinds)
    ROWS = [
        ("seconds saved minus seconds spent", [("cheap", 80.0, 5.0)], None, (),
         ("cheap", 20.0, 5.0, 15.0, [], [])),
        ("two hours saved for three of work loses to a small cheap saving",
         [("grand", 0.0, 500.0), ("small", 90.0, 1.0)], None, (), ("small", 10.0, 1.0, 9.0, [], [])),
        ("must fail: saving less than it costs is not offered", [("busywork", 99.0, 10.0)], ("idle", 100.0, 0.0), (),
         ("idle", 0.0, 0.0, 0.0, [], [])),
        ("making things worse is not offered", [("harmful", 150.0, 0.0)], ("idle", 100.0, 0.0), (),
         ("idle", 0.0, 0.0, 0.0, [], [])),
        ("refused actions are reported, not ranked", [("best", 0.0, 0.0), ("worse", 50.0, 0.0)], None, ("best",),
         ("worse", 50.0, 0.0, 50.0, [("best", "refused")], [])),
        ("the default is exempt from the veto", [("a", 0.0, 0.0)], ("idle", 99.0, 0.0), ("a", "idle"),
         ("idle", 1.0, 0.0, 1.0, [("a", "refused")], ["no action"])),
        ("everything productive refused is a fault, not a decision", [("a", 0.0, 0.0)], ("idle", 100.0, 0.0), ("a",),
         ("idle", 0.0, 0.0, 0.0, [("a", "refused")], ["no action"])),
    ]

    def test_choose_over_the_table(self):
        for name, acts, default, refuse, (pick, value, cost, score, rejected, faults) in self.ROWS:
            with self.subTest(name):
                m = Model([Act(n, after=a, cost_s=c) for n, a, c in acts],
                          default=Act(default[0], after=default[1], cost_s=default[2]) if default else None,
                          refuse=refuse)
                c = kernel.choose(m, {"price": 100.0})
                self.assertEqual(c.name, pick)
                self.assertAlmostEqual(c.value_s, value)
                self.assertAlmostEqual(c.cost_s, cost)
                self.assertAlmostEqual(c.score, score)
                self.assertEqual(c.rejected, rejected)
                self.assertEqual([k for k, _ in c.fault], faults)


class Commitment(unittest.TestCase):
    # (cost_s, declared commitment or None) → what the kernel commits to: unsaid means atomic
    ROWS = [(6.0, None, 6.0), (6.0, 0.8, 0.8), (0.0, None, 0.0), (6.0, 6.0, 6.0), (6.0, 0.0, 0.0)]  # must fail: a declared zero commits to nothing, not to the cost

    def test_commitment_over_the_table(self):
        for cost, commit, want in self.ROWS:
            with self.subTest(cost=cost, commit=commit):
                act = Act("a", after=50.0, cost_s=cost, commitment_s=commit)
                self.assertEqual(kernel.commitment(act), want)
                c = kernel.choose(Model([act]), {"price": 100.0})
                self.assertEqual((c.cost_s, c.commitment_s), (cost, want),
                                 "the choice carries the commitment, not the cost")


class HoldingADecision(unittest.TestCase):
    """A decision may only change when there is a reason: an assumption failed, or a challenger's gain over it pays
    the work the switch throws away (kernel.switches). Random sequences with a few percent of jitter per tick — the shape of a
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

    def sweep(self, holds=None):
        switches = reasons = 0
        for seed in self.SEEDS:
            held, last = kernel.Held(), None
            for tick, state in enumerate(self.states(seed)):
                choice = held.decide(self.model(), state, now=tick * 0.2, holds=holds)
                reasons += held.because is not None
                switches += choice.name != last
                last = choice.name
        return switches, reasons

    # the seeded sweep (200 runs × 60 ticks of jitter; 200 = never changed after the first choice): every switch
    # one whose gain over the held action's remainder paid the work thrown away ("better"), each with its reason
    SWEEP = (335, 135)
    OLD_MARGIN = 379        # the same sweep under the old keeper (MARGIN 1.05 after the commitment ran out)

    def test_it_changes_no_more_often_than_it_has_reason_to(self):
        switches, reasons = self.sweep()
        self.assertEqual((switches, reasons), self.SWEEP)
        self.assertLessEqual(switches, self.OLD_MARGIN)      # must fail: gain without the estimates' noise (541)

    def test_a_clearly_better_answer_still_wins(self):
        """Steady readings, then one action becomes far better: the switch comes at once (noise ~0, gain large)."""
        held = kernel.Held()
        calm = {"price": 100.0, "noise": {"fight": 1.0, "evade": 1.0, "reshape": 1.0, "carry on": 1.0}}
        for tick in range(5):
            first = held.decide(self.model(), calm, now=tick * 0.2)
        better = dict(calm, noise=dict(calm["noise"], evade=3.0))
        self.assertEqual(first.name, "fight")
        self.assertEqual(held.decide(self.model(), better, now=1.2).name, "evade")

    def test_re_deciding_every_tick_would_dither(self):
        """The control: without holding, this fixture really does flip about — otherwise the test above is empty."""
        flips = 0
        for seed in self.SEEDS:
            last = None
            for state in self.states(seed):
                name = kernel.choose(self.model(), state).name
                flips += name != last
                last = name
        self.assertEqual(flips, 1239)

    # (does the held choice's assumption still stand, seconds later) → why it was re-decided (None: it was kept)
    RELEASE = [(True, 0.01, None), (False, 0.01, "assumption"),  # must fail: an assumption standing, early: kept
               (False, 60.0, "assumption"), (None, 0.01, None)]

    def test_what_releases_a_held_decision(self):
        states = self.states(1)
        for holds, later, because in self.RELEASE:
            with self.subTest(holds=holds, later=later):
                held = kernel.Held()
                first = held.decide(self.model(), states[0], now=0.0)
                ask = None if holds is None else (lambda *_, h=holds: h)
                again = held.decide(self.model(), states[1], now=later, holds=ask)
                self.assertEqual(held.because, because)
                if because is None:
                    self.assertIs(again, first)


class SwitchRule(unittest.TestCase):
    """kernel.switches / lost_s: a new choice replaces the held one only when its gain over the held one re-priced
    now pays for the work an abandon throws away (live 17:23:31-36: evade 80 s against the fight's 132 s, kept)."""

    def test_rows(self):
        # (fresh score, held re-priced, lost) → switches
        rows = [("must fail: evade 80 against the fight's 132 (17:23:32)", 80.0, 132.0, 0.0, False),
                ("a fight 132 against an evade 82 that walked 1 s: the gain pays", 132.0, 82.0, 1.0, True),
                ("must fail: a tie keeps", 50.0, 50.0, 0.0, False),
                ("gain 2 s, 3 s of work thrown away: kept", 52.0, 50.0, 3.0, False),
                ("gain 4 s, 3 s thrown away: switched", 54.0, 50.0, 3.0, True)]
        for name, fresh, staying, lost, want in rows:
            with self.subTest(name):
                self.assertEqual(kernel.switches(fresh, staying, lost), want)
        # the estimates' own noise is part of the cost: a gain inside it is no gain
        self.assertFalse(kernel.switches(54.0, 50.0, 0.0, noise=4.0))    # must fail: noise ignored
        self.assertTrue(kernel.switches(55.0, 50.0, 0.0, noise=4.0))

    def test_spread(self):
        for values, want in [([], 0.0), ([5.0], 0.0), ([1.0, 3.0], 1.0), ([2.0, 2.0, 2.0], 0.0)]:
            with self.subTest(values=values):
                self.assertAlmostEqual(kernel.spread(values), want)

    def test_lost(self):
        plain = type("A", (), {"cost_s": 2.0})()
        dug = type("D", (), {"cost_s": 2.0, "kept": 1.0})()           # a dig: the cells stay dug
        # (action, elapsed) → seconds an abandon throws away
        rows = [("0.5 s into a 2 s action", plain, 0.5, 0.5), ("1.9 s in", plain, 1.9, 1.9),
                ("run its course", plain, 2.0, 0.0),
                ("must fail: a dig keeps its cells, nothing lost", dug, 1.5, 0.0)]
        for name, act, elapsed, want in rows:
            with self.subTest(name):
                self.assertAlmostEqual(kernel.lost_s(act, elapsed), want)


if __name__ == "__main__":
    unittest.main()
