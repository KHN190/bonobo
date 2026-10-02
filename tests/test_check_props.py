"""The checker over generated states (Hypothesis): any combination of the α facts — three and more non-core facts
together, which the explorer's pairwise rows never build — round-trips through γ and α, the production round
decides on it, and the oracle reports only invariants whose violations are already known (check/known.txt)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402

from check import oracle, round as rnd  # noqa: E402
from check.facts import DOMAINS, of  # noqa: E402

KNOWN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "check", "known.txt")
EXAMPLES = 300          # generated states per run (each one production round, ~0.02 s)

states = st.fixed_dictionaries({k: st.sampled_from(v) for k, v in DOMAINS.items()}).map(lambda kw: of(**kw))


def known():
    with open(KNOWN) as fh:
        return {ln.split()[0] for ln in fh if ln.strip() and not ln.startswith("#")}


class Generated(unittest.TestCase):
    @settings(max_examples=EXAMPLES, deadline=None, derandomize=True, database=None, suppress_health_check=[HealthCheck.too_slow])
    @given(states)
    def test_any_state(self, f):
        d, got, ctx = rnd.decide(f)
        self.assertEqual(dict(got), dict(f))                                  # γ then α: the same facts
        found = {inv for inv, _why in oracle.violations(f, d, f, ctx)}
        self.assertEqual(found - known(), set(), (dict(f), d.name))           # must fail: a new kind of violation

    def test_the_property_can_fail(self):
        """A state the oracle flags (S4: waiting in the open at night) is caught when its invariant is not known."""
        f = of(night=True)
        d, _got, ctx = rnd.decide(f)
        self.assertIn("S4", {inv for inv, _ in oracle.violations(f, d, f, ctx)} - (known() - {"S4"}))


class Fuzz(unittest.TestCase):
    def test_a_short_search(self):
        from unittest import mock
        from check import fuzz
        got = fuzz.run(3)
        self.assertGreater(got["examples"], 0)
        self.assertEqual(set(got["found"]) - fuzz.known(), set())
        for inv, f in got["found"].items():                       # each shrunk state still violates its invariant
            self.assertIn(inv, fuzz.judged(f)[1])
        with mock.patch.object(fuzz, "known", lambda: set()), mock.patch.object(fuzz, "run", lambda s, save: got):
            self.assertEqual(fuzz.main(["1"]), 1 if got["found"] else 0)   # must fail: a violation not known

    def test_corpus_round_trip(self):
        import tempfile
        from unittest import mock
        from check import fuzz
        f = of(night=True, hunger="low")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fuzz, "CORPUS", tmp):
            fuzz.keep(f)
            fuzz.keep(f)                                              # one file per state
            self.assertEqual(fuzz.corpus(), [f])


if __name__ == "__main__":
    unittest.main()
