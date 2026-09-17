"""Who holds the body, as properties.

Three claims, and nothing about which answer wins:

    layering is hard        a faster layer takes the body from a slower one, always, whatever anything is worth
    the price of stopping   is what abandoning throws away, never how long the work has been running
    a refusal has a reason  and exactly one, from a closed set — "it did not happen" is not an observation

The first two are one bug, found live: `interrupt_cost_s` was the sunk cost, which grows with time, so a plan that
had been walking for a minute could not be interrupted by anything, and the agent was beaten to death holding a
pickaxe. Sunk seconds are sunk whichever way the decision goes.
"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import arbiter  # noqa: E402
from tests.world import (CHALLENGE, CHALLENGES, ELAPSED, HELD_WORTH, HOLDER, INTENT, LAYERS, WORTH,
                         faster_than, intent)  # noqa: E402

FASTER = ("reflex", "safety", "tactic")


def body_with(running, now=0.0):
    """A body with `running` already submitted and about to be defended."""
    body = arbiter.Motion()
    body.pending = [running]
    return body


class LayeringIsHard(unittest.TestCase):
    """Subsumption is the architecture: a faster layer does not bid for the body, it takes it. Whatever the work
    below is worth, however long it has run, and whatever the answer above claims to save."""

    def test_a_faster_layer_always_takes_the_body(self):
        for kind, elapsed, worth in itertools.product(INTENT, ELAPSED, WORTH):
            for fast in FASTER:
                body = body_with(intent(kind, layer="plan", at=-ELAPSED[elapsed]))
                taken, why = body.preempt(fast, lambda: None, "answer", worth_s=WORTH[worth], now=0.0)
                self.assertIsNotNone(taken, f"{fast} over plan/{kind}/{elapsed}/{worth}: refused ({why})")
                self.assertIsNone(why, f"{fast} over plan/{kind}: taken with a reason {why!r}")

    def test_a_slower_layer_never_takes_the_body_from_a_faster_one(self):
        for kind, elapsed, worth in itertools.product(INTENT, ELAPSED, WORTH):
            body = arbiter.Motion()
            body.preempt("safety", lambda: None, "emergency", now=0.0, release=lambda: False)
            taken, why = body.preempt("plan", lambda: None, "mine", worth_s=WORTH[worth], now=0.0)
            self.assertIsNone(taken, f"plan took the body from safety ({kind}/{elapsed}/{worth})")
            self.assertEqual(why, "layer")

    def test_an_emergency_is_never_asked_what_it_is_worth(self):
        for kind, elapsed in itertools.product(INTENT, ELAPSED):
            body = body_with(intent(kind, layer="plan", at=-ELAPSED[elapsed]))
            taken, why = body.preempt("safety", lambda: None, "lava", worth_s=0.0, now=0.0)
            self.assertIsNotNone(taken, f"safety had to argue its case over {kind}/{elapsed} ({why})")


class ThePriceOfStoppingIsWhatIsThrownAway(unittest.TestCase):
    def test_it_does_not_move_with_how_long_the_work_has_run(self):
        for kind in INTENT:
            seen = [intent(kind, at=-seconds).interrupt_cost_s(now=0.0) for seconds in ELAPSED.values()]
            self.assertEqual(len(set(seen)), 1, f"{kind}: the price of stopping moved with the clock: {seen}")

    def test_resumable_work_costs_nothing_to_stop(self):
        for kind, spec in INTENT.items():
            if spec["resumable"]:
                self.assertEqual(intent(kind).interrupt_cost_s(now=0.0), 0.0, kind)

    def test_open_loop_work_costs_what_it_would_have_to_redo(self):
        for kind, spec in INTENT.items():
            if not spec["resumable"]:
                self.assertEqual(intent(kind).interrupt_cost_s(now=0.0), spec["redo_s"], kind)

    def test_what_has_been_spent_is_reported_and_never_compared(self):
        """`spent_s` exists for the log and the tape; nothing in the arbiter may read it to decide."""
        import inspect
        running = intent("walk", at=-100.0)
        self.assertGreater(running.spent_s(now=0.0), 0.0)
        source = inspect.getsource(arbiter.Motion.preempt)
        self.assertNotIn("spent_s", source, "the arbiter weighed sunk cost again")


class ARefusalHasExactlyOneReason(unittest.TestCase):
    REASONS = {"price", "lease", "layer", "expired"}

    def test_every_answer_is_taken_or_refused_for_a_named_reason(self):
        for kind, elapsed, worth, layer in itertools.product(INTENT, ELAPSED, WORTH, LAYERS):
            body = body_with(intent(kind, layer="plan", at=-ELAPSED[elapsed]))
            taken, why = body.preempt(layer, lambda: None, "answer", worth_s=WORTH[worth], now=0.0)
            if taken is None:
                self.assertIn(why, self.REASONS, f"{layer}/{kind}/{elapsed}/{worth}: {why!r}")
            else:
                self.assertIsNone(why, f"{layer}/{kind}: taken and refused at once ({why!r})")

    def test_a_lease_refuses_as_a_lease_and_not_as_a_price(self):
        for worth in WORTH.values():
            body = arbiter.Motion()
            body.preempt("tactic", lambda: None, "answer", worth_s=1e6, now=0.0, release=lambda: False)
            _taken, why = body.preempt("plan", lambda: None, "mine", worth_s=worth, now=0.1)
            self.assertEqual(why, "layer", f"worth {worth}: {why!r}")

    def test_only_the_same_layer_can_be_refused_on_price(self):
        seen = set()
        for kind, elapsed, worth, layer in itertools.product(INTENT, ELAPSED, WORTH, LAYERS):
            body = body_with(intent(kind, layer="plan", at=-ELAPSED[elapsed]))
            taken, why = body.preempt(layer, lambda: None, "answer", worth_s=WORTH[worth], now=0.0)
            if why == "price":
                seen.add(layer)
        self.assertFalse(seen - {"plan"}, f"a faster layer was refused on price: {sorted(seen)}")


class HoldingIsADecisionNotALock(unittest.TestCase):
    """A held answer is `kernel.Held` wearing the body: kept while its assumption holds and nothing clearly beats
    it, replaced when either stops being true. The arbiter used to hold the body with the LAYER — so the second
    answer of one fight was refused as if it were an intruder, and a fight got one swing."""

    def held(self, layer="tactic", paying=True, worth_s=HELD_WORTH):
        body = arbiter.Motion()
        body.preempt(layer, lambda: None, "held answer", worth_s=worth_s, now=0.0,
                     release=lambda: not paying)
        return body

    def test_a_faster_layer_takes_it_whatever_is_held(self):
        for challenge in CHALLENGES:
            faster = faster_than("tactic")
            body = self.held()
            taken, why = body.preempt(faster, lambda: None, "emergency", worth_s=CHALLENGE(challenge), now=1.0)
            self.assertIsNotNone(taken, f"{faster} over a held tactic answer: {why}")

    def test_a_slower_layer_never_takes_it(self):
        for challenge in CHALLENGES:
            slower = faster_than("tactic", +1)
            body = self.held()
            taken, why = body.preempt(slower, lambda: None, "mine", worth_s=CHALLENGE(challenge), now=1.0)
            self.assertIsNone(taken, f"{slower} took the body from a held tactic answer")
            self.assertEqual(why, "layer")

    def test_the_same_layer_replaces_only_what_it_clearly_beats(self):
        for challenge in CHALLENGES:
            body = self.held()
            taken, why = body.preempt("tactic", lambda: None, "another answer",
                                      worth_s=CHALLENGE(challenge), now=1.0)
            if challenge == "better":
                self.assertIsNotNone(taken, f"a clearly better answer was refused ({why})")
            else:
                self.assertIsNone(taken, f"{challenge} replaced a held answer")
                self.assertEqual(why, "margin")

    def test_an_answer_that_has_stopped_paying_is_handed_over_at_once(self):
        for challenge in CHALLENGES:
            body = self.held(paying=False)
            taken, why = body.preempt("tactic", lambda: None, "another answer",
                                      worth_s=CHALLENGE(challenge), now=1.0)
            self.assertIsNotNone(taken, f"a held answer that stopped paying kept the body ({why})")

    def test_replacement_does_not_move_with_how_long_it_has_been_held(self):
        for elapsed in ELAPSED.values():
            for challenge in CHALLENGES:
                body = self.held()
                taken, _why = body.preempt("tactic", lambda: None, "another", worth_s=CHALLENGE(challenge),
                                           now=elapsed)
                self.assertEqual(taken is not None, challenge == "better", f"{elapsed}s/{challenge}")

    def test_there_is_one_margin_in_the_agent(self):
        """The rule for keeping a decision belongs to `kernel`; an arbiter with its own constant is the second
        set of rules that made a lease behave like a lock."""
        import inspect
        source = inspect.getsource(arbiter)
        self.assertNotIn("MARGIN =", source, "the arbiter grew its own margin")
        self.assertIn("kernel", source, "the arbiter must reach for the one margin it does not own")


if __name__ == "__main__":
    unittest.main()
