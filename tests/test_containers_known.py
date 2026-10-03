"""D1: what a container holds is known once it is opened; the chance it still does decays at the rate others were
seen to change records; an unopened home container is looked into when its expected saving pays the look."""
import math
import os
import tempfile
import time
import unittest

from bonobo import brain, cost as cost_mod, decompose, goals, planner
from bonobo.memory import Memory, merge_double
from tests.world import brain_fixture, cost, inventory, round_ctx, snapshot, state

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
                self.assertAlmostEqual(cost_mod.container_p(record, {IRON}, age, rate), want)

    def test_unknown(self):
        # (opened k holding / n opened) → P (the rule of succession)
        for k, n, want in [(0, 0, 0.5), (0, 8, 0.1), (3, 4, 4 / 6)]:
            with self.subTest(k=k, n=n):
                self.assertAlmostEqual(planner.p_unknown(k, n), want)

    def test_double_chest_once(self):
        rows = [("a double chest: one container", [(0, 64, 0), (1, 64, 0)], 1),
                ("two singles apart: two", [(0, 64, 0), (3, 64, 0)], 2),
                ("must fail: a double counted twice", [(0, 64, 0), (0, 64, 1), (5, 64, 5)], 2)]
        for name, cells, n in rows:
            with self.subTest(name):
                self.assertEqual(len(merge_double(cells)), n)


class TheLookPricesTheTake(unittest.TestCase):
    """planner.look_first: look + p·take + (1 − p)·make against make — the take out of the chest is part of what the
    look buys (no chest opened: p = 1/2)."""

    def test_rows(self):
        from types import SimpleNamespace
        from unittest import mock
        from bonobo.planner import Step
        mem = SimpleNamespace(home_containers=lambda dim: [(3, 64, 0)], container_record=lambda pos: None)
        snap = SimpleNamespace(dimension="minecraft:overworld", feet=(0, 64, 0))
        # (situation, look ticks, take ticks, make ticks) → looked first?
        rows = [("a cheap take: the look pays", 100, 20, 300, True),
                ("must fail: a dear take — p·make beats the look alone, not the look and the take", 100, 300, 300, False),
                ("making is cheap: no look", 100, 20, 150, False)]
        for name, look, take, make, want in rows:
            with self.subTest(name):
                null = planner.NullCost()           # offline: no hunger clock, no site
                c = SimpleNamespace(mem=mem, snap=snap, stored=lambda token: [], hunger_rate=null.hunger_rate,
                                    site=null.site, table_back=null.table_back,
                                    estimate=lambda st, held=None, at=None, table_back=True, look=look, take=take:
                                    look if st.kind == "look" else take)
                made = [Step("craft", IRON, 24, {}, make)]
                with mock.patch.object(planner, "plan_needs", lambda *a, **k: made):
                    got = planner.look_first(snapshot(state(), inventory()).inv, [(IRON, 24)], c)
                self.assertEqual(bool(got), want)


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

    def test_the_round_looks_first(self):
        """The round's one plan (brain.replan) looks into an unopened home chest before making what it may hold,
        when the look pays (must fail: the round planned the make, the chest never looked into)."""
        from bonobo import brain
        chests = [(3, 64, 0), (6, 64, 0), (9, 64, 0)]
        rows = [("unopened chests at home, iron wanted: the round looks first", (), "look"),
                ("must fail: every chest opened, none held iron: no look", [
                    ((3, 64, 0), {"minecraft:dirt": 5}), ((6, 64, 0), {}), ((9, 64, 0), {})], None)]
        for name, records, first in rows:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                m = self.home(tmp, chests, records)
                snap = snapshot(state(), inventory())
                held, why = brain.replan([("task t1", goals.have((IRON, 24)), 0)], snap, cost(snap, mem=m))
                self.assertIsNone(why)
                kinds = [s.kind for s in held["steps"]]
                if first is None:
                    self.assertNotIn("look", kinds)
                else:
                    self.assertEqual(kinds[0], first, [str(s) for s in held["steps"]])

    def test_a_recorded_look_is_not_redone(self):
        """brain.met: a look's own effect lands only in memory (note_container), never in the "have" remainder — the
        held plan is otherwise unchanged (round_key: still short of iron, same bag), so next_step must see the look
        as already done once it is on record, even empty, instead of picking it again."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self.home(tmp, [(3, 64, 0)])          # unopened
            snap = snapshot(state(), inventory())
            c = cost(snap, mem=m)
            goal = goals.have((IRON, 24))
            task = {"id": "t1", "goal": goal["goal"], "args": goal["args"], "state": "pending", "reason": ""}
            held, why = brain.replan([("task t1", goal, 0)], snap, c)
            self.assertIsNone(why)
            self.assertEqual(held["steps"][0].kind, "look", [str(s) for s in held["steps"]])

            b = brain_fixture(mem=m)
            act, update = b.task_act(task, snap, round_ctx(b, snap), c, held)
            task.update(update)
            self.assertEqual(act.step.kind, "look")

            m.note_container((3, 64, 0), "minecraft:overworld", [])   # the look ran: the chest opened, empty

            # the same held plan, handed to task_act again (round_key unchanged: still short of iron) — must fail:
            # next_step reselects the same look, redone every round
            act2, update2 = b.task_act(task, snap, round_ctx(b, snap), c, held)
            self.assertNotEqual(act2.step.kind if act2 else None, "look",
                                "a look already on record (even empty) must not be redone")

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
