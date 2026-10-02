"""D1: what a container holds is known once it is opened; the chance it still does decays at the rate others were
seen to change records; an unopened home container is looked into when its expected saving pays the look."""
import math
import os
import tempfile
import time
import unittest

from bonobo import decompose, goals
from bonobo.memory import Memory, merge_double
from tests.world import cost, inventory, snapshot, state

IRON = "minecraft:iron_ingot"


class Chance(unittest.TestCase):
    def test_record(self):
        rec = {"items": {IRON: 9}}
        # (situation, record, age s, rate per s) → P
        rows = [("seen just now, nothing ever changed", rec, 0.0, 0.0, 1.0),
                ("a day old, nobody ever changed a chest: still certain", rec, 86400.0, 0.0, 1.0),
                ("must fail: a day old, one change a day observed: discounted", rec, 86400.0, 1 / 86400, math.exp(-1)),
                ("not held then, changes seen: it may have come since", {"items": {}}, 86400.0, 1 / 86400,
                 1 - math.exp(-1))]
        for name, record, age, rate, want in rows:
            with self.subTest(name):
                self.assertAlmostEqual(decompose.container_p(record, {IRON}, age, rate), want)

    def test_unknown(self):
        # (opened k holding / n opened) → P (the rule of succession)
        for k, n, want in [(0, 0, 0.5), (0, 8, 0.1), (3, 4, 4 / 6)]:
            with self.subTest(k=k, n=n):
                self.assertAlmostEqual(decompose.p_unknown(k, n), want)

    def test_double_chest_once(self):
        rows = [("a double chest: one container", [(0, 64, 0), (1, 64, 0)], 1),
                ("two singles apart: two", [(0, 64, 0), (3, 64, 0)], 2),
                ("must fail: a double counted twice", [(0, 64, 0), (0, 64, 1), (5, 64, 5)], 2)]
        for name, cells, n in rows:
            with self.subTest(name):
                self.assertEqual(len(merge_double(cells)), n)


class LookOrTake(unittest.TestCase):
    def home(self, tmp, chests, records=()):
        m = Memory(os.path.join(tmp, "notes.json"))
        boxes = [((-20, 60, -20), (20, 70, 20))]
        m.add_home("bunker", boxes, "minecraft:overworld", {tuple(c): "chest" for c in chests})
        for pos, items in records:
            m.note_container(pos, "minecraft:overworld", [{"id": i, "count": n, "owner": "chest"}
                                                           for i, n in items.items()])
        return m

    def steps(self, m, need=(IRON, 24)):
        snap = snapshot(state(), inventory())
        return decompose.decompose(snap.inv, goals.have(need), cost(snap, mem=m))

    def test_rows(self):
        chests = [(3, 64, 0), (6, 64, 0), (9, 64, 0)]
        # (situation, records) → the first step's kind
        rows = [("33 unopened chests at home (here 3), iron wanted: look first", (), "look"),
                ("a record holding the iron: taken", [((3, 64, 0), {IRON: 24})], "withdraw"),
                ("must fail: every chest opened, none held iron: no look, it is made", [
                    ((3, 64, 0), {"minecraft:dirt": 5}), ((6, 64, 0), {}), ((9, 64, 0), {})], None)]
        for name, records, first in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                got = self.steps(self.home(tmp, chests, records))
                kinds = [s.kind for s in got]
                if first is None:
                    self.assertNotIn("look", kinds)
                    self.assertNotIn("withdraw", kinds)
                else:
                    self.assertEqual(kinds[0], first, [str(s) for s in got])

    def test_change_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = self.home(tmp, [(3, 64, 0)], [((3, 64, 0), {IRON: 9})])
            m.data["containers"]["3,64,0"]["at"] = time.time() - 100
            m.saw_container((3, 64, 0), [{"id": IRON, "count": 9, "owner": "chest"}])
            self.assertEqual(m.container_change_rate(), 0.0)            # the same: no change
            m.saw_container((3, 64, 0), [{"id": IRON, "count": 2, "owner": "chest"}])
            self.assertGreater(m.container_change_rate(), 0.0)          # must fail: a change by someone unseen


if __name__ == "__main__":
    unittest.main()
