"""nav.OVERRUN (E4): reach_stand refuses a dug way priced past OVERRUN× a step's own estimate before digging, and
stops one running past it too — the round re-plans (NavFailed) rather than finishing an unbounded tunnel."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import api, nav  # noqa: E402
from bonobo.data import OVERRUN  # noqa: E402
from bonobo.game import TICKS_PER_S  # noqa: E402
from tests.world import FakeRegion  # noqa: E402

TARGET = (10, 64, 0)
REGION = FakeRegion((0, 60, -4), (16, 70, 4), {})


def steps_of(n):
    return [{"type": "mine", "x": i, "y": 64, "z": 0} for i in range(n)]


class ReachStandOverrun(unittest.TestCase):
    def reach(self, seconds, est_ticks, chain_times=None):
        """One reach_stand call: plan_way answers `seconds`; each chunked run_chain call advances the fake clock by
        the next of `chain_times` (none: the chain is free, as most are)."""
        clock, calls = [0.0], []

        def run_chain(batch, stop_on_failure=True):
            calls.append(len(batch))
            if chain_times:
                clock[0] += chain_times[len(calls) - 1]

        with mock.patch.object(nav, "feet", lambda: (0, 64, 0)), \
                mock.patch.object(nav, "_read_box", lambda *a, **k: REGION), \
                mock.patch.object(nav, "ways_for", lambda *a, **k: 1), \
                mock.patch.object(nav, "stands_for", lambda *a, **k: False), \
                mock.patch.object(nav, "stand_candidates", lambda *a, **k: []), \
                mock.patch.object(nav, "plan_walks", lambda *a, **k: []), \
                mock.patch.object(nav, "inventory_now", lambda: None), \
                mock.patch.object(nav, "plan_way", lambda *a, **k: (steps_of(12), None, seconds)), \
                mock.patch.object(nav.time, "time", lambda: clock[0]), \
                mock.patch.object(api, "run_chain", run_chain), mock.patch.object(api, "detail", lambda *a: None):
            nav.reach_stand({"type": "mine", "x": TARGET[0], "y": TARGET[1], "z": TARGET[2]}, nav.Policy(),
                             est=est_ticks)
        return calls

    def test_refused_before_digging(self):
        """accept7: a 145-break way (104.3s) against a 61s estimate (> 1.5×): refused, nothing dug."""
        with self.assertRaises(api.NavFailed):
            self.reach(104.3, 61 * TICKS_PER_S)

    def test_dug_within_the_estimate(self):
        est_s = 61
        self.assertLessEqual(20.0, OVERRUN * est_s, "fixture must fall inside the budget")
        calls = self.reach(20.0, est_s * TICKS_PER_S)
        self.assertTrue(calls, "a way inside the estimate: the chain runs")

    def test_stopped_mid_chain_on_overrun(self):
        """plan_way priced it fine (10s), but digging itself runs long: stopped partway, not finished."""
        with self.assertRaises(api.NavFailed):
            self.reach(10.0, 10 * TICKS_PER_S, chain_times=[20.0, 20.0])


if __name__ == "__main__":
    unittest.main()
