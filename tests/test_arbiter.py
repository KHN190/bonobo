"""Offline tests for the single motion exit.

What matters is not which intent wins a contest but that the ordering is by time scale and cannot be negotiated,
that a fast layer does not wait for a slow layer's tick, and that during a fight nothing drives the body except the
chosen intent. A fight loses to several commanders long before it loses to a bad plan.

Every case is a row: a sequence of calls on one fresh `Motion`, what each returned, and what actually ran.
"""
import threading
import time
import unittest

from bonobo import api, arbiter


def intent(layer, mark, out, **kw):
    return arbiter.Intent(layer, lambda: out.append(mark), mark, **kw)


# (intents as (layer, reason, at, deadline_s), now) → the reason arbitrate picks, or None
ARBITRATE = [
    ("plan vs tactic: the faster", [("plan", "p", 0.0, None), ("tactic", "t", 0.0, None)], 0.0, "t"),
    ("tactic vs safety", [("tactic", "t", 0.0, None), ("safety", "s", 0.0, None)], 0.0, "s"),
    ("safety vs reflex", [("safety", "s", 0.0, None), ("reflex", "r", 0.0, None)], 0.0, "r"),
    ("the order of submission does not matter", [("reflex", "r", 0.0, None), ("plan", "p", 5.0, None)], 5.0, "r"),
    ("within a layer the newest reading", [("tactic", "old", 100.0, None), ("tactic", "new", 101.0, None)], 101.0,
     "new"),
    ("an expired intent is dropped, not run late", [("plan", "stale", 100.0, 1.0)], 102.0, None),
    ("an expired faster intent loses to a live slower one",
     [("reflex", "stale", 100.0, 1.0), ("plan", "live", 101.5, None)], 102.0, "live"),
    ("nothing to arbitrate", [], 0.0, None),
]


class Ordering(unittest.TestCase):
    def test_arbitrate_over_the_table(self):
        for name, rows, now, want in ARBITRATE:
            with self.subTest(name):
                out = []
                got = arbiter.arbitrate([intent(layer, r, out, at=at, deadline_s=d) for layer, r, at, d in rows],
                                        now=now)
                self.assertEqual(None if got is None else got.reason, want)

    def test_an_unknown_layer_is_refused(self):
        for layer in ("urgent", "", "PLAN"):
            with self.subTest(layer=layer), self.assertRaises(ValueError):
                arbiter.Intent(layer, lambda: None)


# A sequence on one Motion: ("submit", layer, reason) / ("preempt", layer, reason, kw) / ("step",) →
# [what each call returned], and what ran, in order.
PAYING, STOPPED = (lambda: False), (lambda: True)
SEQUENCES = [
    ("a preemption drops slower pending intents",
     [("submit", "plan", "dig"), ("preempt", "safety", "breath", {}), ("step",)],
     [None, ("safety", "breath"), None], ["breath"]),
    ("a plan submitted after the preemption is fresh",
     [("preempt", "safety", "breath", {}), ("submit", "plan", "dig"), ("step",)],
     [("safety", "breath"), None, ("plan", "dig")], ["breath", "dig"]),
    ("a preemption keeps faster pending intents",
     [("submit", "reflex", "fireball"), ("preempt", "safety", "breath", {}), ("step",)],
     [None, ("safety", "breath"), ("reflex", "fireball")], ["breath", "fireball"]),
    ("only the winner of a step runs, and the queue clears",
     [("submit", "plan", "dig"), ("submit", "tactic", "reposition"), ("step",), ("step",)],
     [None, None, ("tactic", "reposition"), None], ["reposition"]),
    ("a held answer that still pays keeps the body against its own layer",
     [("preempt", "tactic", "shield up", {"release": PAYING}), ("preempt", "tactic", "step aside", {"worth_s": 1e6})],
     [("tactic", "shield up"), "held"], ["shield up"]),
    ("a held answer that stopped paying hands over",
     [("preempt", "tactic", "shield up", {"release": STOPPED}), ("preempt", "tactic", "step aside", {"worth_s": 0.1})],
     [("tactic", "shield up"), ("tactic", "step aside")], ["shield up", "step aside"]),
    ("a faster layer is never asked anything",
     [("preempt", "tactic", "shield up", {"release": PAYING}), ("preempt", "safety", "lava", {})],
     [("tactic", "shield up"), ("safety", "lava")], ["shield up", "lava"]),
    ("a slower layer is refused on the layer",
     [("preempt", "tactic", "shield up", {"release": PAYING}), ("preempt", "plan", "mine", {"worth_s": 1e6})],
     [("tactic", "shield up"), "layer"], ["shield up"]),
]


class Sequences(unittest.TestCase):
    def test_sequences_over_the_table(self):
        for name, ops, want, ran_want in SEQUENCES:
            with self.subTest(name):
                ran, m, got = [], arbiter.Motion(), []
                for op in ops:
                    if op[0] == "submit":
                        m.submit(op[1], lambda r=op[2]: ran.append(r), op[2])
                        got.append(None)
                    elif op[0] == "preempt":
                        kw = {"now": 0.5, "seen_at": 0.5, "worth_s": 10.0, **op[3]}
                        taken, why = m.preempt(op[1], lambda r=op[2]: ran.append(r), op[2], **kw)
                        got.append(taken if taken is not None else why)
                    else:
                        got.append(m.step())
                self.assertEqual(got, want)
                self.assertEqual(ran, ran_want)


# (engaged?, who asks: None = outside any intent / "intent" = inside the running one / "thread" = a preemption on
# another thread) → owns?, violations recorded
OWNERSHIP = [("outside a fight, anyone", False, None, True, []),
             ("inside a fight, a stray caller is refused and counted", True, None, False, ["nav.go_to"]),
             ("inside a fight, the chosen intent while it runs", True, "intent", True, []),
             ("inside a fight, a preemption on its own thread", True, "thread", True, [])]


class Ownership(unittest.TestCase):
    def test_ownership_over_the_table(self):
        for name, engaged, asker, want, violations in OWNERSHIP:
            with self.subTest(name):
                m, seen = arbiter.Motion(), []
                if engaged:
                    m.engage()
                if asker is None:
                    seen.append(m.owns("nav.go_to"))
                elif asker == "intent":
                    m.submit("plan", lambda: seen.append(m.owns("nav.go_to")), "dig")
                    m.step()
                else:
                    t = threading.Thread(target=lambda: m.preempt("safety", lambda: seen.append(m.owns("nav.go_to")),
                                                                  "breath"))
                    t.start()
                    t.join()
                self.assertEqual(seen, [want])
                self.assertEqual([v[1] for v in m.violations], violations)
                m.disengage()
                self.assertTrue(m.owns("nav.go_to"), "after the fight everything is allowed again")

    # (a Motion) → does it watch the handover file: only the one real body; a test's Motion is its own world
    WATCH = [("the shared body", lambda: arbiter.BODY, True), ("a fresh Motion", arbiter.Motion, False),
             ("a Motion asked to watch", lambda: arbiter.Motion(watch_handover=True), True),
             ("a Motion told not to", lambda: arbiter.Motion(watch_handover=False), False)]

    def test_who_watches_the_handover(self):
        for name, make, want in self.WATCH:
            with self.subTest(name):
                body = make()
                self.assertEqual((type(body), body.watch_handover), (arbiter.Motion, want))


class LockDiscipline(unittest.TestCase):
    def test_a_long_preempting_action_does_not_block_submit(self):
        """perception preempts with a slow action; the fight thread must still be able to submit."""
        m = arbiter.Motion()
        started, release = threading.Event(), threading.Event()

        def slow():
            started.set()
            release.wait(2.0)

        t = threading.Thread(target=lambda: m.preempt("safety", slow, "slow"))
        t.start()
        started.wait(1.0)
        t0 = time.time()
        m.submit("plan", lambda: None, "dig")          # must not wait for `slow`
        blocked = time.time() - t0
        release.set()
        t.join()
        self.assertLess(blocked, 0.5, "submit blocked behind a running preemption: action ran under the lock")
        self.assertEqual([p.reason for p in m.pending], ["dig"])

    # (the layer that preempts) → the interrupt message it leaves: only safety and faster write it
    INTERRUPTS = [("reflex", "fireball", "fireball"), ("safety", "breath", "breath"), ("tactic", "fight", None),
                  ("plan", "mine", None)]

    def test_who_writes_the_interrupt_message(self):
        for layer, reason, want in self.INTERRUPTS:
            with self.subTest(layer):
                api.INTERRUPT = None
                arbiter.Motion().preempt(layer, lambda: None, reason)
                self.assertEqual(api.INTERRUPT, want)
        api.INTERRUPT = None


class APreemptionIsNotAnIntruder(unittest.TestCase):
    """When a fast layer takes the body, the slow layer's task really is replaced — by us. The waiting thread must
    read that as "go and re-plan", not as an outsider fighting over the player."""

    # (when our last preemption happened, the mod's message) → what the waiting thread raises
    ROWS = [("replaced by an outsider", 0.0, api.REPLACED, api.BodyContested),
            ("replaced by our own preemption since the task began", 105.0, api.REPLACED, api.CommitmentExpired),
            ("an older preemption is not this one", 90.0, api.REPLACED, api.BodyContested),
            ("the player always wins", 105.0, "released by player", api.PlayerTookControl)]

    def test_the_release_over_the_table(self):
        for name, preempted_at, message, raised in self.ROWS:
            with self.subTest(name):
                arbiter.BODY.preempted_at, arbiter.BODY.preempted_by = preempted_at, None
                with self.assertRaises(raised):
                    api._raise_if_released([{"message": message}], since=100.0)
        arbiter.BODY.preempted_at, arbiter.BODY.preempted_by = 0.0, None


if __name__ == "__main__":
    unittest.main()
