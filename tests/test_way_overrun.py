"""data.OVERRUN (the user's rule): a step's ways are held to OVERRUN × its as-run price (Step.est, nav.step_budget) —
a way priced past what the step has left is refused before digging, one running past it is stopped at a segment
boundary, the budget spent across every try and way of the step; an overrun is nav.Overrun (no ban: the round
prices again), never a NavFailed keyed to the target."""
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
SEGMENT = 6                       # api.run_chain's own segment size: its before_segment runs once per part


def steps_of(n):
    return [{"type": "mine", "x": i, "y": 64, "z": 0} for i in range(n)]


class StepBudget(unittest.TestCase):
    def setUp(self):
        self.clock, self.parts = [0.0], []

    def way(self, seconds, part_s=0.0):
        """One reach_stand: plan_way answers a 12-step way priced `seconds`; each run_chain segment advances the
        fake clock by `part_s` after its before_segment hook (the safety hook and the budget check) ran."""
        def run_chain(tasks, stop_on_failure=True, before_segment=None, **_kw):
            for i in range(0, len(tasks), SEGMENT):
                if before_segment:
                    before_segment(tasks[i:i + SEGMENT])
                self.parts.append(len(tasks[i:i + SEGMENT]))
                self.clock[0] += part_s
        with mock.patch.object(nav, "feet", lambda: (0, 64, 0)), \
                mock.patch.object(nav, "_read_box", lambda *a, **k: REGION), \
                mock.patch.object(nav, "ways_for", lambda *a, **k: 1), \
                mock.patch.object(nav, "stands_for", lambda *a, **k: bool(self.parts)), \
                mock.patch.object(nav, "stand_candidates", lambda *a, **k: []), \
                mock.patch.object(nav, "plan_walks", lambda *a, **k: []), \
                mock.patch.object(nav, "inventory_now", lambda: None), \
                mock.patch.object(nav, "plan_way", lambda *a, **k: (steps_of(12), None, seconds)), \
                mock.patch.object(nav.time, "time", lambda: self.clock[0]), \
                mock.patch.object(api, "run_chain", run_chain), mock.patch.object(api, "detail", lambda *a: None):
            nav.reach_stand({"type": "mine", "x": TARGET[0], "y": TARGET[1], "z": TARGET[2]}, nav.Policy())

    def test_refused_before_digging(self):
        """accept7: a way priced 104 s against a 61 s step (> 1.5×): refused, nothing dug, not a NavFailed (no ban)."""
        with nav.step_budget(61 * TICKS_PER_S), self.assertRaises(nav.Overrun) as e:
            self.way(104.3)
        self.assertNotIsInstance(e.exception, api.NavFailed)      # must fail: the target banned for a price miss
        self.assertEqual(self.parts, [])

    def test_dug_within_the_budget(self):
        self.assertLessEqual(20.0, OVERRUN * 61, "fixture must fall inside the budget")
        with nav.step_budget(61 * TICKS_PER_S):
            self.way(20.0)
        self.assertEqual(self.parts, [SEGMENT, SEGMENT])

    def test_stopped_at_a_segment_once_over(self):
        """Priced fine (10 s), the digging runs long: stopped before the next segment, not finished."""
        with nav.step_budget(10 * TICKS_PER_S), self.assertRaises(nav.Overrun):
            self.way(10.0, part_s=20.0)
        self.assertEqual(self.parts, [SEGMENT])                   # must fail: the whole way dug

    def test_one_budget_across_the_steps_ways(self):
        """Two ways of one step: the second has only what the first left (must fail: a budget per try)."""
        with nav.step_budget(10 * TICKS_PER_S) as budget:
            self.way(10.0, part_s=5.0)
            self.assertEqual(budget.spent_s, 10.0)
            self.parts.clear()                                    # the step's next way: a stand not yet held
            with self.assertRaises(nav.Overrun):
                self.way(10.0)

    def test_no_step_no_budget(self):
        """A way outside a step (a reflex, a bench walk) is never held to a price it has none of."""
        self.way(1e6, part_s=1e6)
        self.assertEqual(self.parts, [SEGMENT, SEGMENT])

    def test_the_safety_hook_runs_before_every_segment(self):
        """S1/S7: the policy's before_segment (the hazard check) still runs per segment of a budgeted way."""
        seen = []
        policy = nav.Policy(before_segment=lambda part: seen.append(len(part)))
        with nav.step_budget(61 * TICKS_PER_S), mock.patch.object(nav, "Policy", lambda: policy):
            self.way(20.0)
        self.assertEqual(seen, [SEGMENT, SEGMENT])


if __name__ == "__main__":
    unittest.main()
