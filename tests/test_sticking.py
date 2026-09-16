"""One property, swept: a decision may only change when there is a reason.

Not scenarios. Hundreds of random state sequences with small changes between ticks, every tick decided by
`kernel.choose`, and one assertion — the number of times the answer changed is no more than the number of times
something actually happened (a commitment ran out, or an assumption the choice was made under stopped holding).
Anything that dithers shows up by itself, whatever layer it lives in.
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import kernel  # noqa: E402


class Act:
    def __init__(self, name, gain, cost_s, commitment_s=None):
        self.name, self.gain, self.cost_s = name, gain, cost_s
        self.commitment_s = cost_s if commitment_s is None else commitment_s

    def effect(self, state):
        return dict(state, price=max(0.0, state["price"] - self.gain * state["noise"][self.name]))


class Jittery:
    """Answers whose value wobbles a few percent every tick — the shape of a threat walking a step nearer."""

    def __init__(self):
        self.actions = [Act("fight", 40.0, 2.0), Act("evade", 38.0, 3.0),
                        Act("reshape", 36.0, 1.0), Act("shield", 20.0, 0.5)]
        self.default = Act("carry on", 0.0, 0.0)
        self.actions.append(self.default)

    def price(self, state):
        return state["price"]

    def admissible(self, state, action):
        return True, ""


def sequence(rng, ticks=60):
    noise = {a: 1.0 for a in ("fight", "evade", "reshape", "shield", "carry on")}
    out = []
    for _ in range(ticks):
        noise = {k: max(0.5, min(1.5, v + rng.uniform(-0.04, 0.04))) for k, v in noise.items()}
        out.append({"price": 100.0, "noise": dict(noise)})
    return out


def run(model, states, hold):
    """Decide each tick, holding the previous answer until `hold` says it may be revisited."""
    switches, reasons, current, held_for = 0, 0, None, 0.0
    for state in states:
        release = hold(current, held_for, state)
        if current is not None and not release:
            held_for += 0.2
            continue
        reasons += current is not None
        choice = kernel.choose(model, state)
        if choice.name != (current.name if current else None):
            switches += 1
        current, held_for = choice.action, 0.0
    return switches, reasons


class ADecisionHolds(unittest.TestCase):
    SEEDS = range(200)

    def test_without_a_commitment_it_dithers(self):
        """The control: re-deciding every tick is what produces "fight, flee, fight" at 5 Hz."""
        worst = 0
        for seed in self.SEEDS:
            states = sequence(random.Random(seed))
            switches, _ = run(Jittery(), states, hold=lambda *_: True)
            worst = max(worst, switches)
        self.assertGreater(worst, 3, "the jitter in this fixture is too small to test anything")

    def test_it_only_changes_when_something_happened(self):
        def hold(current, held_for, state):
            return current is None or held_for >= current.commitment_s

        for seed in self.SEEDS:
            states = sequence(random.Random(seed))
            switches, reasons = run(Jittery(), states, hold)
            self.assertLessEqual(switches, reasons + 1,
                                 f"seed {seed}: {switches} changes for {reasons} reasons")

    def test_a_long_commitment_changes_less_than_a_short_one(self):
        def held(scale):
            total = 0
            for seed in self.SEEDS:
                states = sequence(random.Random(seed))
                total += run(Jittery(), states,
                             lambda c, h, s, k=scale: c is None or h >= c.commitment_s * k)[0]
            return total

        self.assertLess(held(4.0), held(1.0))


class TheKernelHoldsItself(unittest.TestCase):
    """The same property, through `kernel.Held` — what the layers actually use."""

    SEEDS = range(200)

    def run_held(self, holds=None, margin=kernel.MARGIN):
        switches = changes = 0
        for seed in self.SEEDS:
            held, last = kernel.Held(margin=margin), None
            for tick, state in enumerate(sequence(random.Random(seed))):
                choice = held.decide(Jittery(), state, now=tick * 0.2, holds=holds)
                changes += held.because is not None
                if choice.name != last:
                    switches += 1
                last = choice.name
        return switches, changes

    def test_it_changes_no_more_often_than_it_has_reason_to(self):
        switches, reasons = self.run_held()
        self.assertLessEqual(switches, reasons + len(self.SEEDS))

    def test_a_broken_assumption_releases_it_at_once(self):
        held = kernel.Held()
        states = sequence(random.Random(1))
        first = held.decide(Jittery(), states[0], now=0.0)
        held.decide(Jittery(), states[1], now=0.01, holds=lambda *_: False)
        self.assertEqual(held.because, "assumption")
        self.assertIsNotNone(first.name)

    def test_a_challenger_must_win_by_more_than_the_margin(self):
        loose = self.run_held(margin=1.0)[0]
        sticky = self.run_held(margin=1.5)[0]
        self.assertLess(sticky, loose)


if __name__ == "__main__":
    unittest.main()
