"""fight_loop.offer never asks who holds the body under its own lock: the lease's release check bids, and bid takes
that lock -- that order deadlocks perception, the fight and the brain together, with no line and no task."""
import threading
import unittest
from unittest import mock

from bonobo import arbiter, fight_loop


def bid_like(*_a, **_k):
    with fight_loop.STATE.lock:      # what bid does (its Held keeper)
        return mock.Mock(kind="evade"), 5.0     # still pays: the lease stays, no new engagement is started


class OfferDuringALiveEngagement(unittest.TestCase):
    def test_returns_while_the_release_check_bids(self):
        st, body = fight_loop.STATE, arbiter.BODY
        hold = threading.Event()
        th = threading.Thread(target=hold.wait, daemon=True)
        th.start()
        intent = arbiter.Intent("tactic", lambda: None, "threat:evade", key="threat:evade")
        saved = (st.thread, st.intent, st.want, body.lease)
        # the engagement over and threats still seen: release reaches bid
        release = lambda: fight_loop.lease_done({"x": 0, "y": 64, "z": 0}, [{"id": 1}], None)   # noqa: E731
        st.thread, st.intent, body.lease = th, intent, (intent, release, 0.0)
        done = threading.Event()
        option = mock.Mock(kind="evade")
        try:
            with mock.patch.object(fight_loop, "engagement_over", lambda *a: (True, None)), \
                    mock.patch.object(fight_loop, "bid", bid_like):
                t = threading.Thread(target=lambda: (fight_loop.offer(option, 1.0, "threat:evade", 0.0, release,
                                                                      None, 0.0), done.set()), daemon=True)
                t.start()
                # must fail: offer blocked on STATE.lock it already holds (the old order)
                self.assertTrue(done.wait(2.0), "offer deadlocked: BODY.holder() under STATE.lock")
                self.assertIs(st.want, option)          # carried by the running engagement, none started
        finally:
            hold.set()
            st.thread, st.intent, st.want, body.lease = saved


if __name__ == "__main__":
    unittest.main()
