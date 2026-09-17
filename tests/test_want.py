"""The cerebrum's door: its SHAPE, not what the agent does with what it is told (docs/api.md).

An want is the cerebrum saying "I want this state, it is worth this many seconds". What the planner then does
with it is a result of pricing, measured by the estimator suites and the benches. What belongs here is the
contract that makes those results trustworthy, stated as relations that hold for every want:

  * a want is priced in seconds or it is not accepted — no priority levels, no weights, no bare urgency;
  * every want expires, so no instruction outlives the situation that motivated it;
  * a deadline may only discount, never unlock: the same want, later, is worth no more;
  * a scope may only narrow the candidate pool;
  * a want nothing can produce is refused WITH a reason, never silently dropped;
  * an id is a replacement, not a second copy;
  * the door is those four names, and the value a want carries enters the ordinary pricing path.
"""
import inspect
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import want  # noqa: E402

DOOR = ("offer", "stop", "status", "list")

# Wants of every shape the door accepts: a token count, a tool, a terminal dimension.
WANTS = ({"iron_ingot": 3}, {"tool:pickaxe": 1}, {"end:sheltered": 1})
WORTHS = (1.0, 60.0, 600.0)


def fresh():
    """A door with nothing in it — the store is a file, so every test states its own world."""
    want.stop()
    for i in want.list(active=False):
        want.forget(i["id"] if isinstance(i, dict) else i.id)
    return want


def ask(what, worth_s=60.0, **kw):
    return fresh().offer(what, worth_s, **kw)


class ItIsPricedInSeconds(unittest.TestCase):
    def test_worth_s_is_required(self):
        """Not a keyword with a default: a caller who cannot say how many seconds must not be able to ask."""
        sig = inspect.signature(want.offer)
        worth = sig.parameters["worth_s"]
        self.assertIs(worth.default, inspect.Parameter.empty)
        self.assertEqual(worth.kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)

    def test_the_door_carries_no_other_scale(self):
        """No level, no weight, no priority, no urgency — seconds, or nothing."""
        names = set(inspect.signature(want.offer).parameters)
        for smuggled in ("priority", "weight", "level", "urgency", "importance", "rank"):
            self.assertNotIn(smuggled, names)

    def test_every_second_argument_says_so_in_its_name(self):
        for name in inspect.signature(want.offer).parameters:
            if name in ("want", "scope", "note", "id"):
                continue
            self.assertTrue(name.endswith("_s"), f"{name}: a quantity on this door is seconds")

    def test_a_want_that_is_not_seconds_is_refused(self):
        for bad in (None, "soon", -1.0, float("nan"), float("inf")):
            with self.subTest(worth_s=bad):
                with self.assertRaises((TypeError, ValueError)):
                    fresh().offer({"iron_ingot": 1}, bad)


class EverythingExpires(unittest.TestCase):
    def test_every_intent_carries_a_life(self):
        for what in WANTS:
            with self.subTest(want=what):
                got = ask(what)
                self.assertIsNotNone(got.expires_s)
                self.assertGreater(got.expires_s, 0)

    def test_no_intent_can_be_made_permanent(self):
        for forever in (None, 0, -1, float("inf")):
            with self.subTest(expires_s=forever):
                with self.assertRaises((TypeError, ValueError)):
                    fresh().offer({"iron_ingot": 1}, 60.0, expires_s=forever)

    def test_an_expired_intent_stops_being_active(self):
        got = ask({"iron_ingot": 1}, expires_s=1e-3)
        self.assertIn(got.id, [i.id for i in want.list(active=True)])
        got.expires_s = 1e-9                       # no sleeping in a test: age it by hand
        got.created -= 1.0
        self.assertNotIn(got.id, [i.id for i in want.list(active=True)])
        self.assertEqual(want.status(got.id)[0]["state"], "expired")


class ADeadlineOnlyDiscounts(unittest.TestCase):
    def test_the_same_want_later_is_worth_no_more(self):
        """Relation, not a number: whatever the pricing is, a later deadline may not price above a sooner one."""
        seen = []
        for deadline_s in (30.0, 300.0, 3000.0, None):
            with self.subTest(deadline_s=deadline_s):
                got = ask({"iron_ingot": 3}, 600.0, deadline_s=deadline_s)
                seen.append(want.status(got.id)[0]["priced_s"])
        finite = [s for s in seen[:3] if s is not None]
        self.assertEqual(finite, sorted(finite, reverse=True))

    def test_a_deadline_cannot_unlock_what_is_refused(self):
        impossible = {"dragon_egg": 1}
        fresh()
        loose = want.offer(impossible, 600.0)
        tight = want.offer(impossible, 600.0, deadline_s=1.0)
        self.assertTrue(want.status(loose.id)[0]["state"].startswith("refused"))
        self.assertTrue(want.status(tight.id)[0]["state"].startswith("refused"))


class AScopeOnlyNarrows(unittest.TestCase):
    def test_scoping_never_adds_a_candidate(self):
        wide = ask({"iron_ingot": 3}, 600.0)
        wide_names = set(want.status(wide.id)[0].get("considered") or ())
        for scope in ("nether", "home", "no_such_place"):
            with self.subTest(scope=scope):
                got = ask({"iron_ingot": 3}, 600.0, scope=scope)
                names = set(want.status(got.id)[0].get("considered") or ())
                self.assertLessEqual(names, wide_names)


class ARefusalCarriesAReason(unittest.TestCase):
    def test_a_want_nothing_can_produce_is_refused_not_dropped(self):
        got = ask({"dragon_egg": 1}, 600.0)
        state = want.status(got.id)[0]["state"]
        self.assertTrue(state.startswith("refused("), state)
        self.assertGreater(len(state[len("refused("):-1].strip()), 0, "a refusal names why")

    def test_a_malformed_want_is_refused_at_the_door(self):
        for bad in (None, "iron_ingot", {}, {"iron_ingot": 0}, {"iron_ingot": -1}, {1: 1}):
            with self.subTest(want=bad):
                with self.assertRaises((TypeError, ValueError)):
                    fresh().offer(bad, 60.0)

    def test_nothing_is_swallowed(self):
        """Whatever this module hides from a caller leaves a mark (`api.swallowed`), like every other door."""
        src = inspect.getsource(want)
        for i, line in enumerate(src.splitlines()):
            if line.strip().startswith("except"):
                tail = "\n".join(src.splitlines()[i:i + 6])
                self.assertTrue("raise" in tail or "swallowed" in tail or "refus" in tail,
                                f"line {i + 1}: a swallowed failure with no mark")


class AnIdIsAReplacement(unittest.TestCase):
    def test_re_offering_the_same_id_re_prices_it(self):
        for first, second in zip(WORTHS, WORTHS[1:]):
            with self.subTest(first=first, second=second):
                fresh()
                a = want.offer({"iron_ingot": 3}, first, id="same")
                b = want.offer({"iron_ingot": 3}, second, id="same")
                self.assertEqual(a.id, b.id)
                self.assertEqual(len(want.list()), 1)
                self.assertEqual(want.list()[0].worth_s, second)

    def test_a_fresh_offer_is_a_fresh_id(self):
        fresh()
        ids = {want.offer(what, 60.0).id for what in WANTS}
        self.assertEqual(len(ids), len(WANTS))


class StoppingIsTwoThings(unittest.TestCase):
    def test_soft_leaves_the_body_alone_and_hard_takes_it(self):
        self.assertIs(inspect.signature(want.stop).parameters["hard"].default, False)
        src = inspect.getsource(want.stop)
        self.assertIn("preempt", src, "a hard stop goes through the one body door (arbiter)")

    def test_a_stopped_intent_is_no_longer_active(self):
        for hard in (False, True):
            with self.subTest(hard=hard):
                got = ask({"iron_ingot": 3})
                want.stop(got.id, hard=hard)
                self.assertNotIn(got.id, [i.id for i in want.list(active=True)])

    def test_stopping_everything_is_the_default(self):
        fresh()
        for what in WANTS:
            want.offer(what, 60.0)
        stopped = want.stop()
        self.assertEqual(len(stopped), len(WANTS))
        self.assertEqual(want.list(active=True), [])


class TheDoorIsOneShape(unittest.TestCase):
    def test_those_are_the_names(self):
        for name in DOOR:
            self.assertTrue(callable(getattr(want, name)), name)

    def test_status_answers_the_same_fields_for_every_intent(self):
        fields = {"id", "want", "worth_s", "priced_s", "chosen", "blocked_by", "age_s", "expires_in_s", "state"}
        fresh()
        for what in WANTS:
            want.offer(what, 60.0)
        rows = want.status()
        self.assertEqual(len(rows), len(WANTS))
        for row in rows:
            self.assertEqual(set(row), fields)

    def test_a_want_is_an_effect_on_the_state_vector(self):
        """The same shape `value.worth_s` takes — so an want is priced by the ordinary path, not beside it."""
        src = inspect.getsource(want)
        self.assertIn("worth_of_change", src)
        self.assertIsNone(re.search(r"\bscore\b", src), "no second ranking rule lives here")

    def test_the_old_doors_became_sugar(self):
        """`priority` and `directives` may call this; this may not grow their queues back."""
        src = inspect.getsource(want)
        for gone in ("priorities.json", "directives.json"):
            self.assertNotIn(gone, src)


if __name__ == "__main__":
    unittest.main()
