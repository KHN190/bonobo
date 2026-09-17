"""Offline tests for the single motion exit.

What matters is not which intent wins a contest but that the ordering is by time scale and cannot be negotiated,
that a fast layer does not wait for a slow layer's tick, and that during a fight nothing drives the body except the
chosen intent. A fight loses to several commanders long before it loses to a bad plan.
"""
import threading
import unittest

from bonobo import arbiter


def intent(layer, mark, out, **kw):
    return arbiter.Intent(layer, lambda: out.append(mark), mark, **kw)


class Ordering(unittest.TestCase):
    def test_faster_layers_win_outright(self):
        out = []
        for slow, fast in (("plan", "tactic"), ("tactic", "safety"), ("safety", "reflex")):
            self.assertEqual(arbiter.arbitrate([intent(slow, slow, out), intent(fast, fast, out)]).layer, fast)

    def test_within_a_layer_the_newest_reading_wins(self):
        out = []
        self.assertEqual(arbiter.arbitrate([intent("tactic", "old", out, at=100.0),
                                            intent("tactic", "new", out, at=101.0)], now=101.0).reason, "new")

    def test_expired_intents_are_dropped_not_run_late(self):
        out = []
        self.assertIsNone(arbiter.arbitrate([intent("plan", "stale", out, deadline_s=1.0, at=100.0)], now=102.0))

    def test_an_unknown_layer_is_refused(self):
        with self.assertRaises(ValueError):
            arbiter.Intent("urgent", lambda: None)


class Preemption(unittest.TestCase):
    """A fast layer does not queue behind the fight loop; it runs now and stales what is slower."""

    def test_preempt_drops_slower_pending_intents(self):
        out, m = [], arbiter.Motion()
        m.submit("plan", lambda: out.append("dig"), "dig")
        m.preempt("safety", lambda: out.append("stop"), "breath")
        self.assertIsNone(m.step(), "a plan queued before the preemption is a plan for a situation that is gone")
        self.assertEqual(out, ["stop"])

    def test_a_plan_submitted_after_the_preemption_is_fresh(self):
        out, m = [], arbiter.Motion()
        m.preempt("safety", lambda: out.append("stop"), "breath")
        m.submit("plan", lambda: out.append("dig"), "dig")
        self.assertEqual(m.step(), ("plan", "dig"))

    def test_preempt_keeps_faster_or_equal_pending(self):
        out, m = [], arbiter.Motion()
        m.submit("reflex", lambda: out.append("dodge"), "fireball")
        m.preempt("safety", lambda: out.append("stop"), "breath")
        self.assertEqual(m.step(), ("reflex", "fireball"))


class Ownership(unittest.TestCase):
    """Inside a fight only the chosen intent may drive the body. Outside one, nothing changes."""

    def test_outside_a_fight_everything_is_allowed(self):
        m = arbiter.Motion()
        self.assertTrue(m.owns("nav.go_to"))
        self.assertEqual(m.violations, [])

    def test_inside_a_fight_a_stray_caller_is_refused_and_counted(self):
        m = arbiter.Motion()
        m.engage()
        self.assertFalse(m.owns("nav.go_to"))
        self.assertEqual([v[1] for v in m.violations], ["nav.go_to"])
        m.disengage()
        self.assertTrue(m.owns("nav.go_to"))

    def test_the_chosen_intent_owns_the_body_while_it_runs(self):
        seen, m = [], arbiter.Motion()
        m.engage()
        m.submit("plan", lambda: seen.append(m.owns("nav.go_to")), "dig")
        m.step()
        self.assertEqual(seen, [True])
        self.assertEqual(m.violations, [])

    def test_a_preempting_thread_owns_the_body_on_its_own_thread(self):
        # Perception preempts from a different thread than the fight loop. Ownership is per thread, so the
        # preemption's own body commands are allowed while the fight thread stays refused.
        seen, m = {}, arbiter.Motion()
        m.engage()
        t = threading.Thread(target=lambda: m.preempt("safety", lambda: seen.__setitem__("p", m.owns("api.run")),
                                                      "breath"))
        t.start(); t.join()
        self.assertTrue(seen["p"])
        self.assertFalse(m.owns("nav.go_to"), "the fight thread, outside any intent, is still refused")

    def test_the_body_is_one_shared_instance(self):
        self.assertIsInstance(arbiter.BODY, arbiter.Motion)


class SingleExit(unittest.TestCase):
    def test_only_the_winner_runs(self):
        out, m = [], arbiter.Motion()
        m.submit("plan", lambda: out.append("dig"), "dig")
        m.submit("tactic", lambda: out.append("reposition"), "reposition")
        m.step()
        self.assertEqual(out, ["reposition"])

    def test_the_queue_clears_each_step(self):
        out, m = [], arbiter.Motion()
        m.submit("plan", lambda: out.append("dig"), "dig")
        m.step()
        self.assertIsNone(m.step())


class LockDiscipline(unittest.TestCase):
    def test_a_long_preempting_action_does_not_block_submit(self):
        # perception preempts with a slow action; the fight thread must still be able to submit.
        import time
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
        release.set(); t.join()
        self.assertLess(blocked, 0.5, "submit blocked behind a running preemption: action ran under the lock")

    def test_a_safety_preemption_writes_the_interrupt_message(self):
        from bonobo import api
        api.INTERRUPT = None
        arbiter.Motion().preempt("safety", lambda: None, "breath")
        self.assertEqual(api.INTERRUPT, "breath")
        api.INTERRUPT = None


class PricedInterruption(unittest.TestCase):
    """The one place the two planners meet: what taking the body costs. Emergencies are never priced."""

    def setUp(self):
        self.body = arbiter.Motion()
        self.ran = []

    def submit_work(self, price):
        self.body.submit("plan", lambda: self.ran.append("work"), "mine the vein", cost_rate=1.0, cost_s=price)
        self.body.pending[-1].at = 0.0

    def test_a_cheap_tactic_does_not_take_a_valuable_commitment(self):
        self.submit_work(300.0)
        self.assertIsNone(self.body.preempt("tactic", lambda: self.ran.append("reposition"),
                                            "shuffle aside", worth_s=5.0, now=1e6))
        self.assertEqual(self.ran, [])

    def test_a_tactic_worth_more_than_the_commitment_takes_it(self):
        self.submit_work(10.0)
        self.assertIsNotNone(self.body.preempt("tactic", lambda: self.ran.append("reposition"),
                                               "get out of the pincer", worth_s=90.0, now=1e6))
        self.assertEqual(self.ran, ["reposition"])

    def test_safety_is_never_priced(self):
        """'You are drowning' is not a bid. An emergency that has to argue its case is not an emergency."""
        self.submit_work(10_000.0)
        self.assertIsNotNone(self.body.preempt("safety", lambda: self.ran.append("stop"), "lava", worth_s=0.1,
                                               now=1e6))
        self.assertEqual(self.ran, ["stop"])

    def test_an_undefended_commitment_is_taken_freely(self):
        self.body.submit("plan", lambda: self.ran.append("work"), "wander")   # nothing invested to defend
        self.assertIsNotNone(self.body.preempt("tactic", lambda: self.ran.append("reposition"), "x", worth_s=0.5,
                                               now=1e6))
        self.assertEqual(self.ran, ["reposition"])


class APreemptionIsNotAnIntruder(unittest.TestCase):
    """When a fast layer takes the body, the slow layer's task really is replaced — by us. The waiting thread must
    read that as "go and re-plan", not as an outsider fighting over the player."""

    def setUp(self):
        from bonobo import api, arbiter as arb
        self.api, self.arb = api, arb
        arb.BODY.preempted_at, arb.BODY.preempted_by = 0.0, None

    def replaced(self):
        return [{"message": self.api.REPLACED}]

    def test_an_outsider_still_contests_the_body(self):
        with self.assertRaises(self.api.BodyContested):
            self.api._raise_if_released(self.replaced(), since=100.0)

    def test_our_own_preemption_asks_for_a_re_plan(self):
        self.arb.BODY.preempted_at = 105.0
        with self.assertRaises(self.api.CommitmentExpired):
            self.api._raise_if_released(self.replaced(), since=100.0)

    def test_an_older_preemption_is_not_this_one(self):
        self.arb.BODY.preempted_at = 90.0
        with self.assertRaises(self.api.BodyContested):
            self.api._raise_if_released(self.replaced(), since=100.0)

    def test_the_player_always_wins(self):
        self.arb.BODY.preempted_at = 105.0
        with self.assertRaises(self.api.PlayerTookControl):
            self.api._raise_if_released([{"message": "released by player"}], since=100.0)


if __name__ == "__main__":
    unittest.main()
