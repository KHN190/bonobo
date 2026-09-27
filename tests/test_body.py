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
from tests.world import (CHALLENGE, CHALLENGES, ELAPSED, FRESHNESS, FakeHeld, HELD_WORTH, HOLDER, INTENT,
                         LAYERS, WORTH, faster_than, intent)  # noqa: E402

# Every (slower, faster) pair of layers in the subsumption order, not only "something over plan".
ORDER = sorted(arbiter.SCALES, key=lambda name: arbiter.SCALES[name])
PAIRS = [(slow, fast) for i, slow in enumerate(ORDER) for fast in ORDER[:i]]
FASTER = tuple(fast for slow, fast in PAIRS if slow == "plan")


def body_with(running):
    """A body `running` is driving (what Motion._run holds while ordinary play's action runs on its thread)."""
    body = arbiter.Motion()
    body.driving = running
    return body


class LayeringIsHard(unittest.TestCase):
    """Subsumption is the architecture: a faster layer does not bid for the body, it takes it. Whatever the work
    below is worth, however long it has run, and whatever the answer above claims to save."""

    def test_a_faster_layer_always_takes_the_body(self):
        self.assertEqual((ORDER, len(PAIRS), FASTER), (["reflex", "safety", "tactic", "maintain", "plan"], 10,
                                                       ("reflex", "safety", "tactic", "maintain")))
        for kind, elapsed, worth in itertools.product(INTENT, ELAPSED, WORTH):
            for slow, fast in PAIRS:
                if slow == "plan":
                    body = body_with(intent(kind, layer="plan", at=-ELAPSED[elapsed]))
                else:
                    body = arbiter.Motion()
                    body.preempt(slow, lambda: None, "held", now=0.0, release=lambda: False)
                taken, why = body.preempt(fast, lambda: None, "answer", worth_s=WORTH[worth], now=0.0)
                self.assertEqual((taken, why), ((fast, "answer"), None), f"{fast} over {slow}/{kind}/{elapsed}/{worth}")

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
            self.assertEqual((taken, why), (("safety", "lava"), None), f"safety over {kind}/{elapsed}")


class NothingAboutTheWorkDefendsIt(unittest.TestCase):
    """What an intent declares about itself — how long it has run, what it would have to redo — is data for the
    log. A judgement that reads it is the arbiter pricing, which is the layer's job."""

    def test_what_has_been_spent_is_never_compared(self):
        """The arbiter's answer is the same however long the work ran."""
        # (how long the running plan has been at it): every challenger gets the answer it gets against work that has
        # just begun
        for ran in (0.0, 1.0, 100.0, 3600.0):
            with self.subTest(ran=ran):
                for layer in ("reflex", "safety", "tactic", "plan"):
                    fresh = body_with(intent("walk", layer="plan", at=0.0)).preempt(
                        layer, lambda: None, "x", worth_s=5.0, now=0.0)
                    long_run = body_with(intent("walk", layer="plan", at=-ran)).preempt(
                        layer, lambda: None, "x", worth_s=5.0, now=0.0)
                    self.assertEqual(long_run, fresh, f"{layer} against work that ran {ran}s")


class ARefusalHasExactlyOneReason(unittest.TestCase):
    REASONS = {"layer", "held", "expired", "stood_down"}     # why an answer did not get the body: a closed set

    def test_every_answer_is_taken_or_refused_for_a_named_reason(self):
        for kind, elapsed, worth, layer in itertools.product(INTENT, ELAPSED, WORTH, LAYERS):
            body = body_with(intent(kind, layer="plan", at=-ELAPSED[elapsed]))
            taken, why = body.preempt(layer, lambda: None, "answer", worth_s=WORTH[worth], now=0.0)
            if taken is None:
                self.assertIn(why, self.REASONS, f"{layer}/{kind}/{elapsed}/{worth}: {why!r}")
            else:
                self.assertIsNone(why, f"{layer}/{kind}: taken and refused at once ({why!r})")

    def test_a_held_answer_refuses_a_slower_layer_as_a_layer(self):
        for worth in WORTH.values():
            body = arbiter.Motion()
            body.preempt("tactic", lambda: None, "answer", worth_s=1e6, now=0.0, release=lambda: False)
            _taken, why = body.preempt("plan", lambda: None, "mine", worth_s=worth, now=0.1)
            self.assertEqual(why, "layer", f"worth {worth}: {why!r}")

    def test_only_the_same_layer_is_ever_refused_by_a_held_answer(self):
        seen = set()
        for challenge, layer in itertools.product(CHALLENGES, LAYERS):
            body = arbiter.Motion()
            body.preempt("tactic", lambda: None, "held", worth_s=HELD_WORTH, now=0.0,
                         release=lambda: False, seen_at=0.0)
            _taken, why = body.preempt(layer, lambda: None, "answer", worth_s=CHALLENGE(challenge),
                                       now=0.0, seen_at=0.0)
            if why == "held":
                seen.add(layer)
        self.assertEqual(seen, {"tactic"}, "only the held answer's own layer is refused as held")


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
            self.assertEqual((taken, why), ((faster, "emergency"), None), f"{faster} over a held tactic answer")

    def test_a_slower_layer_never_takes_it(self):
        for challenge in CHALLENGES:
            slower = faster_than("tactic", +1)
            body = self.held()
            taken, why = body.preempt(slower, lambda: None, "mine", worth_s=CHALLENGE(challenge), now=1.0)
            self.assertIsNone(taken, f"{slower} took the body from a held tactic answer")
            self.assertEqual(why, "layer")

    def test_the_same_layer_keeps_what_still_pays_whatever_the_challenger_claims(self):
        """Whether a held answer is still the right one is the LAYER's question, asked through `release()`. The
        arbiter comparing two worths would be a second decision rule beside `kernel.Held`'s."""
        for challenge in CHALLENGES:
            body = self.held()
            taken, why = body.preempt("tactic", lambda: None, "another answer",
                                      worth_s=CHALLENGE(challenge), now=1.0)
            self.assertIsNone(taken, f"{challenge} replaced an answer that still pays")
            self.assertEqual(why, "held")

    def test_an_answer_that_has_stopped_paying_is_handed_over_at_once(self):
        for challenge in CHALLENGES:
            body = self.held(paying=False)
            taken, why = body.preempt("tactic", lambda: None, "another answer",
                                      worth_s=CHALLENGE(challenge), now=1.0)
            self.assertEqual((taken, why), (("tactic", "another answer"), None), challenge)
            self.assertIsNone(body.lease, "the stale answer's lease was not dropped")

    def test_replacement_depends_on_whether_it_still_pays_and_nothing_else(self):
        for paying in (True, False):
            for challenge in CHALLENGES:
                body = self.held(paying=paying)
                taken, why = body.preempt("tactic", lambda: None, "another", worth_s=CHALLENGE(challenge),
                                          now=0.0, seen_at=0.0)
                self.assertEqual(taken is not None, not paying, f"paying={paying}/{challenge}: {why}")


class TheArbiterJudgesAndNeverPrices(unittest.TestCase):
    """One job: who may drive the body. Layers are ordered, and within a layer the layer's own held decision says
    whether it is still paying. The moment the arbiter compares two numbers it is both the lock and the judge, and
    the layer above it is left holding a decision nobody will run."""

    def test_the_same_layer_is_settled_by_the_held_decision_itself(self):
        for paying in (True, False):
            for challenge in CHALLENGES:
                held, body = FakeHeld(paying=paying), arbiter.Motion()
                body.preempt("tactic", lambda: None, "held answer", worth_s=HELD_WORTH, now=0.0,
                             release=held.release, held=held)
                taken, why = body.preempt("tactic", lambda: None, "another", worth_s=CHALLENGE(challenge), now=1.0)
                self.assertEqual(taken is not None, not paying,
                                 f"paying={paying}/{challenge}: {'' if taken else why}")

    def test_a_refusal_is_reported_to_whoever_was_refused(self):
        for layer, expected in (("plan", "layer"),):
            held, body = FakeHeld(), arbiter.Motion()
            body.preempt("tactic", lambda: None, "held answer", worth_s=HELD_WORTH, now=0.0,
                         release=held.release, held=held)
            asking = FakeHeld()
            _taken, why = body.preempt(layer, lambda: None, "mine", worth_s=1e6, now=1.0, held=asking)
            self.assertEqual(why, expected)
            self.assertEqual(asking.denials, [expected],
                             "a layer that was refused was never told, and will re-decide the same thing")

    # (who holds the body, who asks) → the refusal, and whether the asker's own decision is released by it
    REFUSED = [("safety", "plan", "layer"), ("safety", "tactic", "layer"), ("tactic", "plan", "layer"),
               ("tactic", "tactic", "held"), ("tactic", "safety", None)]  # must fail: a faster layer is never refused

    def test_being_refused_ends_the_assumption_it_was_made_under(self):
        for holder, asker, why_want in self.REFUSED:
            with self.subTest(holder=holder, asker=asker):
                asking, body = FakeHeld(), arbiter.Motion()
                body.preempt(holder, lambda: None, "held", worth_s=1e6, now=0.0, seen_at=0.0, release=lambda: False)
                taken, why = body.preempt(asker, lambda: None, "ask", worth_s=1e6, now=0.1, seen_at=0.1, held=asking)
                self.assertEqual(why, why_want)
                self.assertEqual((asking.denials, asking.release()), ([why], True) if why else ([], False))


class NothingIsComparedAcrossDifferentWorlds(unittest.TestCase):
    """Every reading carries when it was taken. Two layers polling the same world at different rates will hold
    readings of different ages, and a comparison between them is only honest while both are recent."""

    def test_freshness_is_a_property_of_the_reading_not_of_the_asker(self):
        for age in FRESHNESS.values():
            for within in (0.5, 5.0):
                self.assertEqual(arbiter.fresh_enough(seen_at=-age, now=0.0, within=within), age <= within,
                                 f"{age}s within {within}s")

    def test_a_decision_made_from_a_stale_reading_is_not_defended(self):
        """Over every age of the held reading: within the arbiter's window it is defended, past it it is not."""
        for age_name, age in FRESHNESS.items():
            with self.subTest(age=age_name):
                held, body = FakeHeld(), arbiter.Motion()
                body.preempt("tactic", lambda: None, "held answer", worth_s=HELD_WORTH, now=0.0,
                             release=held.release, held=held, seen_at=-age)
                taken, why = body.preempt("tactic", lambda: None, "fresh answer", worth_s=1.0, now=0.0, seen_at=0.0)
                defended = arbiter.fresh_enough(seen_at=-age, now=0.0, within=arbiter.FRESH_WITHIN_S)
                self.assertEqual((taken, why), (None, "held") if defended else (("tactic", "fresh answer"), None))


if __name__ == "__main__":
    unittest.main()
