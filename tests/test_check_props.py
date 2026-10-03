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


class DuskByThePrep(unittest.TestCase):
    """Dusk compares the chosen night way's preparation × LEAD with the light left, never the night itself; a night
    underground is lost only where the route has no work under cover left."""

    def test_rows(self):
        # (situation, facts) → dusk read back (production's reading on the γ world)
        rows = [("must fail: dawn, empty bag: digging in fits the day", of(), False),
                ("one tick of light left: no way fits", of(dusk=True), True)]
        for name, f, want in rows:
            with self.subTest(name):
                _d, got, _ctx = rnd.decide(f, fail_then_again=False)
                self.assertIs(got["dusk"], want)

    def test_the_night_lost_is_what_no_covered_work_fills(self):
        from bonobo import beliefs, decompose
        night_s, risk = beliefs.value("time.night_s"), beliefs.value("risk.night_sheltered") * beliefs.value(
            "time.death_cost_s")
        # (situation, covered work left on the route) → the waited night's price
        rows = [("must fail: the route's digging done: the whole night lost", 0.0, night_s + risk),
                ("work under cover for half the night", night_s / 2, night_s / 2 + risk),
                ("more work than night: nothing lost but the risk", night_s * 3, risk)]
        for name, covered, want in rows:
            with self.subTest(name):
                got = decompose.night_facts(None, night_left_s=night_s, covered_work_s=covered)["wait_s"]
                self.assertAlmostEqual(got, want)


class Generated(unittest.TestCase):
    @settings(max_examples=EXAMPLES, deadline=None, derandomize=True, database=None, suppress_health_check=[HealthCheck.too_slow])
    @given(states)
    def test_any_state(self, f):
        d, got, ctx = rnd.decide(f)
        self.assertEqual(dict(got), dict(f))                                  # γ then α: the same facts
        found = {inv for inv, _why in oracle.violations(f, d, f, ctx)}
        self.assertEqual(found - known(), set(), (dict(f), d.name))           # must fail: a new kind of violation

    def test_the_property_can_fail(self):
        """A decision the oracle flags (S4: waiting in the open at night) is caught when its invariant is not known —
        the decision built here, not left to a production fault that a fix removes."""
        f = of(night=True, place="open")
        d = rnd.Decision("plan", "wait", "day", None, (), None, "wait for day", ())
        self.assertIn("S4", {inv for inv, _ in oracle.violations(f, d, f, {})} - (known() - {"S4"}))

    def test_a_wait_in_the_open_needs_no_way_and_a_reason(self):
        """S4 with D1: waiting in the open at night is lawful only when the round's night table offers no way and the
        wait says why."""
        f = of(night=True, place="open")
        why = "night in the open, no way through it here: none can be had"
        # (situation, reason, the round's night way) → S4 flagged
        rows = [("no way here, the reason written", why, None, False),
                ("must fail: a way to take (dig in), waited instead", why, "dig in", True),
                ("must fail: no reason written", None, None, True)]
        for name, reason, way, flagged in rows:
            with self.subTest(name):
                d = rnd.Decision("plan", "idle", None, None, (), reason, "wait for day", ())
                ctx = {"night_way": way, "night_steps": []}
                self.assertEqual(oracle.S4(f, d, f, ctx) is not None, flagged)


class Fuzz(unittest.TestCase):
    def test_a_short_search(self):
        from unittest import mock
        from check import fuzz
        got = fuzz.run(3, stall=1, log=lambda line: None)
        self.assertGreater(got["examples"], 0)
        self.assertEqual(set(got["found"]) - fuzz.known(), set())
        for inv, f in got["found"].items():                       # each shrunk state still violates its invariant
            self.assertIn(inv, fuzz.judged(f)[1])
        with mock.patch.object(fuzz, "known", lambda: set()), mock.patch.object(fuzz, "run", lambda s, **kw: got):
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
