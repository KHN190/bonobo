"""p — how likely, how often.

The third of the four: success rates, encounters per second of being out there, how often a tool is reached for,
how much of a declared yield a world actually gives back, how stale a note has become. Every one of them is a
belief with observations behind it, and the rule is the same for all: unmeasured is read at its cautious end, and
only measurement moves it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import beliefs, gates  # noqa: E402
from tests.world import worlds  # noqa: E402


class Rates(unittest.TestCase):
    def test_every_rate_is_finite_and_not_negative(self):
        for w in worlds(terrain="flat", stock="none"):
            for ask in ({"event": "encounter", "dark": True}, {"event": "encounter", "dark": False},
                        {"event": "yield", "name": "loot nearby chests"},
                        {"event": "success", "key": "mine:minecraft:coal"},
                        {"event": "tool_use", "kind": "pickaxe"},
                        {"event": "tool_left", "kind": "pickaxe", "left": 60.0},
                        {"event": "stale", "age_s": 600.0}):
                event = dict(ask)
                got = gates.p(event.pop("event"), mem=w.mem, **event)
                self.assertGreaterEqual(got, 0.0, f"{w}: {ask}")
                self.assertLess(got, 1e6, f"{w}: {ask}")

    def test_the_dark_is_dearer_than_the_light(self):
        for w in worlds(terrain="flat", stock="none"):
            self.assertGreater(gates.p("encounter", mem=w.mem, dark=True),
                               gates.p("encounter", mem=w.mem, dark=False), f"{w}")

    def test_a_worn_tool_will_be_reached_for_less_often_than_a_fresh_one(self):
        w = next(worlds(terrain="flat", stock="none"))
        worn = gates.p("tool_left", mem=w.mem, kind="pickaxe", left=3.0)
        fresh = gates.p("tool_left", mem=w.mem, kind="pickaxe", left=250.0)
        self.assertLess(worn, fresh)

    def test_an_older_note_is_a_worse_guess(self):
        w = next(worlds())
        self.assertGreater(gates.p("stale", mem=w.mem, age_s=86400.0),
                           gates.p("stale", mem=w.mem, age_s=60.0))

    def test_age_grows_slowly_and_never_runs_away(self):
        """Logarithmic on purpose: a day-old note is a worse guess, not a deleted one — a cutoff cannot tell a
        stale note from a wrong one, and that cutoff sent the agent exploring past a field it had mapped."""
        w = next(worlds())
        day = gates.p("stale", mem=w.mem, age_s=86400.0)
        hour = gates.p("stale", mem=w.mem, age_s=3600.0)
        self.assertGreater(day, hour)
        self.assertLess(day, 20.0)


class FaithNeverOutbidsMeasurement(unittest.TestCase):
    def test_what_a_world_gives_back_moves_the_yield(self):
        poor = next(worlds(confidence="unmeasured")).mem
        rich = next(worlds(confidence="measured")).mem
        self.assertLess(gates.p("yield", mem=poor, name="loot nearby chests"),
                        gates.p("yield", mem=rich, name="loot nearby chests"))

    def test_an_unmeasured_belief_is_read_at_its_cautious_end(self):
        for path in ("batch.pick_s", "nav.unit_s", "memory.drift_s_per_log2"):
            value, _n = beliefs.belief(path)
            self.assertLess(beliefs.cautious(path), value, f"{path}: read at face value")
            self.assertGreater(beliefs.cautious(path, "cost"), value, f"{path}: a cost read optimistically")

    def test_observations_close_the_gap_and_nothing_else_does(self):
        from unittest import mock
        far = beliefs.cautious("batch.pick_s")
        with mock.patch.object(beliefs, "count", lambda path: 100):
            near = beliefs.cautious("batch.pick_s")
        self.assertGreater(near, far)
        self.assertLessEqual(near, beliefs.value("batch.pick_s"))

    def test_an_unknown_question_is_an_error_not_a_guess(self):
        with self.assertRaises(KeyError):
            gates.p("how lucky do you feel")


if __name__ == "__main__":
    unittest.main()
