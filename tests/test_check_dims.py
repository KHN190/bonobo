"""check/dims: each dimension's declared effects (step) on a holding row and a must-fail row. Their alpha/gamma
round trip is test_check.GammaRoundTrip (every value of every fact, the dimensions' included)."""
import unittest

from check.dims import dusk, task
from check.facts import of
from check.round import Decision


def dec(name, token=None):
    return Decision("plan", "task", token, None, (), None, name, ())


class Steps(unittest.TestCase):
    # (module, facts, decision, ctx, the facts it changes)
    ROWS = [
        (dusk, of(dusk=True, bed="home"), dec("sleep"), {}, {"dusk": False}),
        (dusk, of(dusk=True), dec("wait for day"), {}, {"dusk": False}),
        (dusk, of(dusk=True), dec("idle: have pickaxe tier 1"), {"step_kind": "gather"}, {}),   # must fail: work is not the night
        (task, of(task="milestone"), dec("task t1", "crafting_table"), {"step_kind": "seek"},
         {"station": "crafting_table"}),
        (task, of(task="milestone"), dec("task t1", "tree"), {"step_kind": "seek"}, {"tree": True}),
        (task, of(task="milestone"), dec("task t1", "air"), {"step_kind": "reach"}, {}),
        (task, of(), dec("idle: have pickaxe tier 1", "tree"), {"step_kind": "seek"}, {}),   # must fail: no task, no effect
        (task, of(task="goto"), dec("task t1", "pos"), {"step_kind": "goto"}, {"task": "none"}),
        (task, of(task="logs"), Decision(None, None, None, None, (), None, None, ()), {}, {"task": "none"}),          # nothing proposed: the task ended
        (task, of(task="cooling"), Decision(None, None, None, None, (), None, None, ()), {}, {}),                      # must fail: a cooling task lives on
        (task, of(task="skill"), dec("task t1", "chop"), {"step_kind": "skill"}, {"task": "none"}),   # run once
        (task, of(task="tool"), dec("task t1", "log"), {"step_kind": "gather"}, {}),   # must fail: not run once
        (task, of(task="road"), dec("task t1", "pos"), {"step_kind": "goto"}, {"plan_held": "walking"}),   # must fail: a road is two legs
        (task, of(task="road", plan_held="walking"), dec("task t1", "pos"), {"step_kind": "goto"}, {"task": "none"}),
        (task, of(task="planned", pickaxe=2), dec("task t1"), {}, {"task": "none"}),
        (task, of(task="tool", pickaxe=2), Decision(None, None, None, None, (), None, None, ()), {}, {"task": "none"}),
        (task, of(task="tool", pickaxe=1), dec("task t1", "log"), {"step_kind": "gather"}, {}),   # not held yet
    ]

    def test_rows(self):
        for mod, facts, d, ctx, want in self.ROWS:
            with self.subTest(dim=mod.NAME, d=d.name, token=d.token):
                self.assertEqual(mod.step(facts, d, ctx), want)


if __name__ == "__main__":
    unittest.main()
