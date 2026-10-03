"""E4's samples and their bounded feedback: each step as priced and as run (dispatch.price_line), a run moving its
average a bounded step (memory.record_duration), a measured price kept near its prior (cost.measured)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import dispatch  # noqa: E402
from bonobo.data import MEASURED_BAND, TICKS_PER_S  # noqa: E402
from bonobo.knowledge import prior_ticks  # noqa: E402
from bonobo.memory import Memory  # noqa: E402
from bonobo.planner import Step  # noqa: E402
from tests.world import cost, snapshot  # noqa: E402


class ThePriceLine(unittest.TestCase):
    def test_rows(self):
        st = Step("withdraw", "minecraft:stick", 4, {"pos": [3, 64, 0], "p": 0.9})
        st.est = 60
        # (situation, why it ended, row) → (ok, the work's price and its source)
        rows = [("ran: a sample", None, "withdraw_chest", (True, "PRIOR_TICKS.withdraw:prior")),
                ("must fail: an interrupted step taken as a sample", "interrupted: Interrupted", None,
                 (False, "PRIOR_TICKS.withdraw:prior"))]
        for name, why, row, (ok, price) in rows:
            with self.subTest(name):
                line = dispatch.price_line(st, False, "minecraft:overworld", 4.2, why, row)
                self.assertEqual((line["ok"], line["price"]["work"]), (ok, price))
                self.assertEqual((line["est"], line["actual_s"], line["row"], line["cond"]["p"]), (60, 4.2, row, 0.9))

    def test_a_per_unit_kind_names_its_unit_price(self):
        line = dispatch.price_line(Step("mine", "minecraft:cobblestone", 3, {"breaks": 3}), True, "o", 9.0)
        self.assertEqual(line["price"]["work"], "PRIOR_TICKS.mine_each:prior")     # must fail: "skill" (the catch-all)


class TheFeedbackIsBounded(unittest.TestCase):
    def test_one_run_moves_the_average_a_bounded_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            m.record_duration("chop", 10.0)
            m.record_duration("chop", 1000.0)          # a run stuck on a path
            # must fail: 307 s a log after one stuck run (0.7 × 10 + 0.3 × 1000)
            self.assertAlmostEqual(m.data["durations"]["chop"]["per"], 0.7 * 10 + 0.3 * 10 * MEASURED_BAND)

    def test_a_measured_price_stays_near_its_prior(self):
        step = Step("gather", "log", 1, {})
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(os.path.join(tmp, "notes.json"))
            m.data["durations"]["chop"] = {"per": 900.0, "n": 5}
            got = cost(snapshot(), mem=m).measured(step)
        # must fail: 900 s a log priced as measured, far past the band around the prior
        self.assertEqual(got, prior_ticks(step) * MEASURED_BAND)
        self.assertLess(got, 900 * TICKS_PER_S)


class TheWaitIsTheNightLeft(unittest.TestCase):
    def test_rows(self):
        from bonobo.knowledge import dawn_s
        from tests.world import state
        for t in (13000, 18000, 22500):
            with self.subTest(timeOfDay=t):
                snap = snapshot(state(timeOfDay=t))
                # must fail: a wait for day priced as a search for something never seen (seek_prior_s / exists_prior)
                self.assertEqual(cost(snap).work(Step("wait", "day", 1, {})), round(dawn_s(snap.state) * TICKS_PER_S))


if __name__ == "__main__":
    unittest.main()
