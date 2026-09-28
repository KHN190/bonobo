"""nav's batches: many cells as one chain of single tasks — the jar's SequenceTask (mine_many, build) planned here: the
order (top of a column first / lowest layer first, the nearest next), the closing sweep, a failed step retried once
at the end (never a no-stand), two unreachable in a row give the rest back as failures."""
import unittest
from unittest import mock

from bonobo import api, nav


class MineOrder(unittest.TestCase):
    def test_rows(self):
        # (situation, cells, start) → the order
        rows = [("nearest first from the start", [(5, 64, 0), (1, 64, 0), (3, 64, 0)], (0, 64, 0),
                 [(1, 64, 0), (3, 64, 0), (5, 64, 0)]),
                ("a column top first, even from below", [(0, 64, 0), (0, 65, 0), (0, 66, 0)], (0, 63, 0),
                 [(0, 66, 0), (0, 65, 0), (0, 64, 0)]),
                ("no start: from the first given", [(2, 64, 0), (9, 64, 0), (3, 64, 0)], None,
                 [(2, 64, 0), (3, 64, 0), (9, 64, 0)]),
                ("nothing: nothing", [], (0, 64, 0), []),
                ("must fail: the cell under another of the batch is never first", [(0, 64, 0), (0, 65, 0)], (0, 64, 0),
                 [(0, 65, 0), (0, 64, 0)])]
        for name, cells, start, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.mine_order(cells, start), want)


class BuildOrder(unittest.TestCase):
    def test_rows(self):
        rows = [("the lowest layer first, whatever the list", [(0, 65, 0), (0, 64, 0)], (0, 64, 0),
                 [(0, 64, 0), (0, 65, 0)]),
                ("in a layer the nearest next", [(4, 64, 0), (1, 64, 0), (2, 64, 0)], (0, 64, 0),
                 [(1, 64, 0), (2, 64, 0), (4, 64, 0)]),
                ("two layers, each nearest-first", [(3, 65, 0), (0, 65, 0), (3, 64, 0), (0, 64, 0)], (0, 64, 0),
                 [(0, 64, 0), (3, 64, 0), (3, 65, 0), (0, 65, 0)]),
                ("must fail: a block over a gap never goes before its support", [(0, 66, 0), (0, 65, 0), (0, 64, 0)],
                 (0, 70, 0), [(0, 64, 0), (0, 65, 0), (0, 66, 0)])]
        for name, cells, start, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.build_order(cells, start), want)

    def test_build_batch_keeps_each_blocks_fields(self):
        blocks = [{"x": 0, "y": 65, "z": 0, "item": "minecraft:stone"},
                  {"x": 0, "y": 64, "z": 0, "item": "minecraft:oak_door", "facing": "north"}]
        self.assertEqual(nav.build_batch(blocks, (0, 64, 0)),
                         [dict(blocks[1], type="place"), dict(blocks[0], type="place")])


class BatchSweep(unittest.TestCase):
    def test_rows(self):
        rows = [("one cell: on it, reach 5", [(1, 2, 3)], None, {"type": "collect", "x": 1, "y": 2, "z": 3, "radius": 5, "idle": 10}),
                ("two cells 8 apart: centred, reaching both", [(0, 64, 0), (8, 64, 0)], None,
                 {"type": "collect", "x": 4, "y": 64, "z": 0, "radius": 9, "idle": 10}),
                ("a pickup filter carried", [(1, 2, 3)], ["minecraft:raw_iron"],
                 {"type": "collect", "x": 1, "y": 2, "z": 3, "radius": 5, "idle": 10, "only": ["minecraft:raw_iron"]}),
                ("must fail: no cells: round the body, no centre", [], None, {"type": "collect", "radius": 5, "idle": 10})]
        for name, cells, only, want in rows:
            with self.subTest(name):
                self.assertEqual(nav.batch_sweep(cells, only), want)


def mines(*xs):
    return [{"type": "mine", "x": x, "y": 64, "z": 0} for x in xs]


class RunCells(unittest.TestCase):
    """nav.run_cells over scripted chain answers: what is sent, and the one result it comes to."""

    def run_with(self, tasks, script, then=None):
        sent = []

        def chain(ts, **_k):
            sent.append([t["x"] for t in ts if "x" in t] if ts[0].get("type") != "collect" else ["sweep"])
            answer = script.get(tuple(t["x"] for t in ts if "x" in t), {})
            out = []
            for t in ts:
                msg = answer.get(t.get("x"))
                out.append({"status": "failed", "message": msg} if msg else {"status": "succeeded", "message": ""})
                if msg:
                    break
            return out
        with mock.patch.object(api, "run_chain", chain), mock.patch.object(api, "detail"):
            try:
                r = nav.run_cells("mine_many", tasks, then=then)
            except api.Unreachable as e:
                r = e
        return sent, r

    def test_rows(self):
        # (situation, tasks, scripted failures {the chain sent: {x: message}}) → (the sends, failures or the error)
        rows = [("all broken: one send", mines(1, 2, 3), {}, ([[1, 2, 3]], 0)),
                ("one fails: the rest sent on, it retried once at the end and broken then",
                 mines(1, 2, 3), {(1, 2, 3): {2: "block moved"}}, ([[1, 2, 3], [3], [2]], 0)),
                ("fails again on the retry: one failure", mines(1, 2), {(1, 2): {1: "block moved"}, (1,): {1: "block moved"}},
                 ([[1, 2], [2], [1]], 1)),
                ("must fail: a no-stand is not retried (the same spot is the same flip)",
                 mines(1, 2), {(1, 2): {1: "cannot hold a stand spot at 1, 64, 0"}}, ([[1, 2], [2]], "Unreachable")),
                ("must fail: two unreachable in a row give the rest back unsent",
                 mines(1, 2, 3, 4), {(1, 2, 3, 4): {1: "no path found"}, (2, 3, 4): {2: "no path found"}},
                 ([[1, 2, 3, 4], [2, 3, 4]], "Unreachable"))]
        for name, tasks, script, (sends, fails) in rows:
            with self.subTest(name):
                sent, r = self.run_with(tasks, script)
                self.assertEqual(sent, sends)
                if fails == "Unreachable":
                    self.assertIsInstance(r, api.Unreachable)
                else:
                    self.assertEqual(len(r["result"]["failures"]), fails)
                    self.assertEqual(r["status"], "failed" if fails else "succeeded")

    def test_the_sweep_goes_last(self):
        sent, r = self.run_with(mines(1, 2), {}, then={"type": "collect", "radius": 5, "idle": 10})
        self.assertEqual(sent, [[1, 2], ["sweep"]])
        self.assertEqual(r["message"], "mine_many finished")


if __name__ == "__main__":
    unittest.main()
