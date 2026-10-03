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


class FreshFacts(unittest.TestCase):
    """P1 (age, no wall clock): the round decides on its own /state read or a later one; a terrain read is no older than its
    TTL (data.FACT_TTL_S) at that read; a send that may change blocks drops the terrain reads at once."""

    def test_rows(self):
        from bonobo.data import FACT_TTL_S
        from check.inv import effects
        d = rnd.Decision("plan", "task", None, None, (), None, "task t1", ())
        look = FACT_TTL_S["look"]
        # (situation, (the /state read decided on, the round's own), {terrain: (age at that read, TTL)}) → flagged
        rows = [("decided on the round's own read", (3, 3), {"look": (look / 2, look)}, False),
                ("a hazard re-read later in the round", (4, 3), {}, False),
                ("must fail: decided on an older read", (2, 3), {}, True),
                ("must fail: the look past its TTL at the body's read", (3, 3), {"look": (2 * look, look)}, True)]
        for name, reads, ages, flagged in rows:
            with self.subTest(name):
                got = effects.P1(of(), d, of(), {"body_read": reads, "fact_ages": ages})
                self.assertEqual(got is not None and not isinstance(got, oracle.Unchecked), flagged)
        self.assertIsInstance(effects.P1(of(), d, of(), {}), oracle.Unchecked)

    def test_a_dig_sent_drops_the_look(self):
        from unittest import mock
        from bonobo import api, world
        asked = []

        def get(path):
            asked.append(path)
            return {"blocks": []}
        with mock.patch.object(api, "get", get), mock.patch.dict(world._SIGHT, {"key": None, "t": 0.0, "near": {},
                                                                              "y": {}, "hits": {}, "memo": {}}), \
                mock.patch.object(world, "_per_block_ok", lambda: False):
            world.nearest(["stone"], (0, 64, 0), "minecraft:overworld")
            world.nearest(["stone"], (0, 64, 0), "minecraft:overworld")
            self.assertEqual(len(asked), 1)                      # one look a TTL
            api.STATE.world_writes += 1                          # a mine task sent
            world.nearest(["stone"], (0, 64, 0), "minecraft:overworld")
            self.assertEqual(len(asked), 2)                      # must fail: the look kept past our own dig


class AHazardMidPlan(unittest.TestCase):
    """S7: a danger written as the round plans stops the search at its next step and the round answers it."""

    def test_rows(self):
        from check.inv import safety
        d = rnd.Decision("safety", "L0", None, None, (), None, "rescue drowning", ())
        # (situation, the hazard round's reading) → flagged
        rows = [("stopped at once, the rescue", ("safety", 0), False),
                ("nothing planned: nothing to stop", None, False),
                ("must fail: the search ran on", ("safety", 57), True),
                ("must fail: the plan answered", ("plan", 0), True)]
        for name, reading, flagged in rows:
            with self.subTest(name):
                got = safety.S7(of(), d, of(), {"hazard": reading})
                self.assertEqual(got is not None and not isinstance(got, oracle.Unchecked), flagged)

    def test_the_round(self):
        f = of(hunger="starve", queued="cobblestone", place="home", bed="home")
        self.assertEqual(rnd.hazard_round(f), ("safety", 0))


class ColdIsWarm(unittest.TestCase):
    """D8: a decision is the inputs' pure function: the cold round's and the warm round's (the declared caches filled by
    another state) agree."""

    def test_rows(self):
        from check.inv import purity
        d = rnd.Decision("plan", "task", None, None, (), None, "task t1", ())
        # (situation, ctx) → flagged
        rows = [("the same decision", {"warm": "task t1"}, False),
                ("must fail: the warm round chose otherwise", {"warm": "wait for day"}, True)]
        for name, ctx, flagged in rows:
            with self.subTest(name):
                got = purity.D8(of(), d, of(), ctx)
                self.assertEqual(got is not None and not isinstance(got, oracle.Unchecked), flagged)
        self.assertIsInstance(purity.D8(of(), d, of(), {}), oracle.Unchecked)

    def test_warm_is_cold(self):
        from bonobo import lifecycle
        from check import explore
        f = of()
        self.assertEqual(rnd.warm_name(f, explore.POLLUTE), rnd.decide(f, fail_then_again=False)[0].name)
        self.assertIn(("bonobo.planner", ("_BOUNDS",)), lifecycle.CACHES)      # what the warm round keeps


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
