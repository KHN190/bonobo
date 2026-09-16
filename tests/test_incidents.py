"""The incident library. Every live failure becomes a file here and the planner must handle it before it flies again.

A fixture is the planner's own input at the moment things went wrong (end.LAST_ROUND, dumped by the bench) plus what
the fixed planner is expected to do. Replaying it costs nothing and never opens the game; a regression shows up as a
named incident, not as a dead agent.
"""
import glob
import json
import os
import unittest

from bonobo import fight_plan as fp

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = sorted(glob.glob(os.path.join(HERE, "incidents", "*.json")))


def thaw(state):
    """JSON has lists where the planner wants tuples."""
    s = json.loads(json.dumps(state))
    s["self"]["pos"] = tuple(s["self"]["pos"])
    if s["self"].get("cover") is not None:
        s["self"]["cover"] = tuple(s["self"]["cover"])
    s["threats"] = [(tuple(t[0]), t[1], tuple(t[2]), t[3]) for t in s["threats"]]
    return s


class Incidents(unittest.TestCase):
    # def test_there_are_incidents(self):
    #     self.assertTrue(FIXTURES, "an empty library means nothing learned from any live failure")

    def test_every_incident_replays(self):
        fight = fp.Fight()
        for path in FIXTURES:
            with self.subTest(os.path.basename(path)):
                inc = json.load(open(path))
                state = thaw(inc["state"])
                self.assertEqual(fp.validate_state(state), [], "a captured state must validate")
                plan = fight.plan(state)
                exp = inc.get("expect", {})
                if exp.get("no_fault"):
                    self.assertEqual(plan["fault"], [], plan["rejected"])
                if "intent_in" in exp:
                    self.assertIn(plan["intent"], exp["intent_in"], plan["rejected"])
                if "intent" in exp:
                    self.assertEqual(plan["intent"], exp["intent"], plan["rejected"])


if __name__ == "__main__":
    unittest.main()
