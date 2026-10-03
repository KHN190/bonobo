"""K10: every failure carries the ground round the body and the acts it ran — a bench row's report (runner.
failure_record: region from _report, acts) and the live log's task failure (brain.fail_task → events "task failed")."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bonobo import brain, events, goals  # noqa: E402
from tests.world import inventory, snapshot, state  # noqa: E402


class ALiveFailureCarriesRegionAndActs(unittest.TestCase):
    def test_rows(self):
        from tests.test_plan_upkeep import Queue_
        with tempfile.TemporaryDirectory() as tmp, Queue_(tmp) as q:
            task = q.task(goals.have(("log", 4)))
            snap = snapshot(state(), inventory())
            q.b.round_snap = snap
            mine = brain.act_record(brain.Act("task", f"task {task['id']}", None), 1.0, 2.0, "failed", "nav")
            other = brain.act_record(brain.Act("task", "task t9", None), 1.0, 2.0, "ok", None)
            emitted = []
            with mock.patch.object(brain, "ACTS", [mine, other]), \
                    mock.patch.object(events, "emit", lambda kind, line, *a, **f: emitted.append((kind, f))):
                q.b._collecting(lambda: q.b.fail_task(task, "unavailable: no step"))
            kind, fields = emitted[-1]
            want = sorted([[*p, n] for p, n in snap.region.blocks.items() if n != "air"])
            # must fail: a live failure with neither the ground nor the acts
            self.assertEqual((kind, fields["region_at"], fields["region"], fields["acts"]),
                             ("task failed", list(snap.feet), want, [mine]))


class ABenchFailureCarriesRegionAndActs(unittest.TestCase):
    def test_rows(self):
        from bonobo.bench import runner
        acts = [brain.act_record(brain.Act("task", "task t1", None), 1.0, 2.0, "failed", "nav")]
        rec = runner.failure_record("r", "c", "skill", "n", 1.0, [], [], [], acts)
        self.assertEqual(rec["acts"], acts)          # must fail: the report without its acts


if __name__ == "__main__":
    unittest.main()
